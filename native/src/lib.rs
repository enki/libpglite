//! Native PGlite adapter for the stable `libpglite` facade.
//!
//! This crate is intentionally internal. Product hosts should load it through
//! the dynamic plugin instead of statically linking it.

#[cfg(not(feature = "internal-adapter"))]
compile_error!(
    "`libpglite-native` is an internal implementation crate. Build the dynamic \
     plugin instead of statically linking this crate into a downstream host."
);

#[cfg(libpglite_native_link_pglite)]
use std::ffi::CString;
#[cfg(libpglite_native_link_pglite)]
use std::ffi::c_void;
#[cfg(libpglite_native_link_pglite)]
use std::path::{Path, PathBuf};
#[cfg(libpglite_native_link_pglite)]
use std::process::Command;
#[cfg(libpglite_native_link_pglite)]
use std::ptr;
#[cfg(libpglite_native_link_pglite)]
use std::sync::atomic::{AtomicPtr, Ordering};

use libpglite::{PgliteConfig, PgliteError, PgliteResult, PgliteRuntime};

#[cfg(libpglite_native_link_pglite)]
const POSTGRES_PREFIX_ENV: &str = "LIBPGLITE_POSTGRES_PREFIX";
#[cfg(libpglite_native_link_pglite)]
const PGLITE_EXIT_ALIVE: i32 = 99;
#[cfg(libpglite_native_link_pglite)]
const POSTGRES_MAIN_LONGJMP: i32 = 100;

#[cfg(libpglite_native_link_pglite)]
mod ffi {
    use std::ffi::c_char;
    use std::ffi::c_void;

    unsafe extern "C" {
        pub fn libpglite_native_pgl_start_pglite() -> i32;
        pub fn libpglite_native_pgl_run_atexit_funcs() -> i32;
        pub fn libpglite_native_pgl_set_active(new_value: i32) -> i32;
        pub fn libpglite_native_pgl_pq_flush() -> i32;
        pub fn libpglite_native_pgl_send_conn_data() -> i32;
        pub fn libpglite_native_process_startup_packet() -> i32;
        pub fn libpglite_native_postgres_main_longjmp() -> i32;
        pub fn libpglite_native_postgres_main_loop_once() -> i32;
        pub fn libpglite_native_postgres_send_ready_for_query_if_necessary() -> i32;
        pub fn libpglite_native_postgres_single_user_main(
            argc: i32,
            argv: *mut *mut c_char,
            username: *const c_char,
        ) -> i32;
        pub fn libpglite_native_pq_buffer_remaining_data() -> isize;
        pub fn PostgresMainLoopOnce();
        pub fn PostgresSingleUserMain(argc: i32, argv: *mut *mut c_char, username: *const c_char);
        pub fn pgl_set_rw_cbs(
            read_cb: Option<unsafe extern "C" fn(*mut c_void, usize) -> isize>,
            write_cb: Option<unsafe extern "C" fn(*const c_void, usize) -> isize>,
        );
        pub fn pgl_startPGlite();
    }

    #[inline(never)]
    pub fn native_link_probe() -> usize {
        PostgresMainLoopOnce as usize
            ^ PostgresSingleUserMain as usize
            ^ pgl_set_rw_cbs as usize
            ^ pgl_startPGlite as usize
            ^ libpglite_native_postgres_main_loop_once as usize
            ^ libpglite_native_postgres_main_longjmp as usize
            ^ libpglite_native_postgres_send_ready_for_query_if_necessary as usize
            ^ libpglite_native_postgres_single_user_main as usize
            ^ libpglite_native_process_startup_packet as usize
            ^ libpglite_native_pgl_send_conn_data as usize
            ^ libpglite_native_pgl_pq_flush as usize
            ^ libpglite_native_pgl_run_atexit_funcs as usize
            ^ libpglite_native_pgl_set_active as usize
            ^ libpglite_native_pgl_start_pglite as usize
            ^ libpglite_native_pq_buffer_remaining_data as usize
    }
}

#[derive(Debug)]
pub struct NativePgliteRuntime {
    #[cfg(libpglite_native_link_pglite)]
    config: PgliteConfig,
    #[cfg(libpglite_native_link_pglite)]
    transport: Box<NativeTransport>,
    shutdown: bool,
}

#[cfg(libpglite_native_link_pglite)]
#[derive(Debug, Default)]
struct NativeTransport {
    input: Vec<u8>,
    read_offset: usize,
    output: Vec<u8>,
}

#[cfg(libpglite_native_link_pglite)]
impl NativeTransport {
    fn begin(&mut self, message: &[u8]) {
        self.input.clear();
        self.input.extend_from_slice(message);
        self.read_offset = 0;
        self.output.clear();
    }

    fn finish(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.output)
    }
}

#[cfg(libpglite_native_link_pglite)]
static ACTIVE_TRANSPORT: AtomicPtr<NativeTransport> = AtomicPtr::new(ptr::null_mut());

impl PgliteRuntime for NativePgliteRuntime {
    fn open(config: PgliteConfig) -> PgliteResult<Self> {
        config.validate()?;
        #[cfg(libpglite_native_link_pglite)]
        {
            std::hint::black_box(ffi::native_link_probe());
            let postgres_prefix = postgres_prefix(&config)?;
            ensure_data_dir(&postgres_prefix, &config.data_dir)?;

            let mut runtime = Self {
                config,
                transport: Box::default(),
                shutdown: false,
            };
            ACTIVE_TRANSPORT.store(&mut *runtime.transport, Ordering::SeqCst);
            unsafe {
                ffi::pgl_set_rw_cbs(Some(native_read), Some(native_write));
            }
            if let Err(err) = runtime.start_postgres() {
                ACTIVE_TRANSPORT.store(ptr::null_mut(), Ordering::SeqCst);
                unsafe {
                    ffi::pgl_set_rw_cbs(None, None);
                    let _ = ffi::libpglite_native_pgl_set_active(0);
                }
                return Err(err);
            }
            Ok(runtime)
        }
        #[cfg(not(libpglite_native_link_pglite))]
        {
            let _ = config;
            Err(PgliteError::initialize(
                "native PGlite runtime is not linked yet; see docs/ADR-0002-NATIVE-PGLITE-BUILD-LANE.md",
            ))
        }
    }

    fn exec_protocol_raw(&mut self, message: &[u8]) -> PgliteResult<Vec<u8>> {
        if self.shutdown {
            return Err(PgliteError::RuntimeShutdown);
        }
        #[cfg(libpglite_native_link_pglite)]
        {
            if message.first() == Some(&b'X') {
                return Ok(Vec::new());
            }
            self.transport.begin(message);
            if message.first() == Some(&0) {
                check_status(
                    unsafe { ffi::libpglite_native_process_startup_packet() },
                    "ProcessStartupPacket",
                )?;
                check_status(
                    unsafe { ffi::libpglite_native_pgl_send_conn_data() },
                    "pgl_sendConnData",
                )?;
                check_status(
                    unsafe { ffi::libpglite_native_pgl_pq_flush() },
                    "pgl_pq_flush",
                )?;
                return Ok(self.transport.finish());
            }

            while self.transport.read_offset < self.transport.input.len()
                || unsafe { ffi::libpglite_native_pq_buffer_remaining_data() } > 0
            {
                let status = unsafe { ffi::libpglite_native_postgres_main_loop_once() };
                match status {
                    0 => {}
                    POSTGRES_MAIN_LONGJMP => {
                        check_status(
                            unsafe { ffi::libpglite_native_postgres_main_longjmp() },
                            "PostgresMainLongJmp",
                        )?;
                    }
                    PGLITE_EXIT_ALIVE => break,
                    other => {
                        return Err(PgliteError::protocol(format!(
                            "PostgresMainLoopOnce exited with status {other}"
                        )));
                    }
                }
            }

            check_status(
                unsafe { ffi::libpglite_native_postgres_send_ready_for_query_if_necessary() },
                "PostgresSendReadyForQueryIfNecessary",
            )?;
            check_status(
                unsafe { ffi::libpglite_native_pgl_pq_flush() },
                "pgl_pq_flush",
            )?;
            Ok(self.transport.finish())
        }
        #[cfg(not(libpglite_native_link_pglite))]
        {
            let _ = message;
            Err(PgliteError::protocol(
                "native PGlite protocol execution is not implemented yet",
            ))
        }
    }

    fn shutdown(&mut self) -> PgliteResult<()> {
        if self.shutdown {
            return Ok(());
        }
        #[cfg(libpglite_native_link_pglite)]
        unsafe {
            let _ = ffi::libpglite_native_pgl_set_active(0);
            let status = ffi::libpglite_native_pgl_run_atexit_funcs();
            ACTIVE_TRANSPORT.store(ptr::null_mut(), Ordering::SeqCst);
            ffi::pgl_set_rw_cbs(None, None);
            check_status(status, "pgl_run_atexit_funcs")?;
        }
        self.shutdown = true;
        Ok(())
    }
}

impl NativePgliteRuntime {
    #[cfg(libpglite_native_link_pglite)]
    fn start_postgres(&mut self) -> PgliteResult<()> {
        let user = CString::new(self.config.user.as_str())
            .map_err(|_| PgliteError::initialize("database user contains a NUL byte"))?;
        let data_dir = self.config.data_dir.to_string_lossy().into_owned();
        let postgres = postgres_prefix(&self.config)?
            .join("bin")
            .join("postgres")
            .to_string_lossy()
            .into_owned();
        let args = [
            postgres.as_str(),
            "--single",
            "-F",
            "-O",
            "-j",
            "-c",
            "search_path=public",
            "-c",
            "exit_on_error=false",
            "-c",
            "log_checkpoints=false",
            "-c",
            "max_worker_processes=0",
            "-c",
            "max_parallel_workers=0",
            "-c",
            "max_parallel_workers_per_gather=0",
            "-D",
            data_dir.as_str(),
            self.config.database.as_str(),
        ];
        let c_args = args
            .iter()
            .map(|arg| {
                CString::new(*arg).map_err(|_| {
                    PgliteError::initialize("native Postgres argument contains a NUL byte")
                })
            })
            .collect::<PgliteResult<Vec<_>>>()?;
        let mut argv = c_args
            .iter()
            .map(|arg| arg.as_ptr().cast_mut())
            .collect::<Vec<_>>();

        check_status(
            unsafe { ffi::libpglite_native_pgl_set_active(1) },
            "pgl_setPGliteActive",
        )?;
        let status = unsafe {
            ffi::libpglite_native_postgres_single_user_main(
                argv.len() as i32,
                argv.as_mut_ptr(),
                user.as_ptr(),
            )
        };
        if status != PGLITE_EXIT_ALIVE {
            return Err(PgliteError::initialize(format!(
                "PostgresSingleUserMain exited with status {status}, expected {PGLITE_EXIT_ALIVE}"
            )));
        }
        check_status(
            unsafe { ffi::libpglite_native_pgl_start_pglite() },
            "pgl_startPGlite",
        )
    }
}

#[cfg(libpglite_native_link_pglite)]
fn postgres_prefix(config: &PgliteConfig) -> PgliteResult<PathBuf> {
    config
        .environment
        .iter()
        .find_map(|(key, value)| (key == POSTGRES_PREFIX_ENV).then(|| PathBuf::from(value)))
        .ok_or_else(|| {
            PgliteError::initialize(format!(
                "native PGlite requires {POSTGRES_PREFIX_ENV} in the runtime environment"
            ))
        })
}

#[cfg(libpglite_native_link_pglite)]
fn ensure_data_dir(postgres_prefix: &Path, data_dir: &Path) -> PgliteResult<()> {
    if data_dir.join("PG_VERSION").is_file() {
        return Ok(());
    }
    if data_dir.exists()
        && data_dir
            .read_dir()
            .map(|mut it| it.next().is_some())
            .unwrap_or(true)
    {
        return Err(PgliteError::initialize(format!(
            "data directory {} exists but is not an initialized PostgreSQL cluster",
            data_dir.display()
        )));
    }
    let initdb = postgres_prefix.join("bin").join("initdb");
    let lib_dir = postgres_prefix.join("lib");
    let output = Command::new(&initdb)
        .arg("-D")
        .arg(data_dir)
        .arg("--encoding")
        .arg("UTF8")
        .arg("--locale")
        .arg("C")
        .arg("--locale-provider")
        .arg("libc")
        .arg("--auth")
        .arg("trust")
        .env("DYLD_LIBRARY_PATH", &lib_dir)
        .env("LD_LIBRARY_PATH", &lib_dir)
        .output()
        .map_err(|err| {
            PgliteError::initialize(format!(
                "failed to run initdb at {}: {err}",
                initdb.display()
            ))
        })?;
    if !output.status.success() {
        return Err(PgliteError::initialize(format!(
            "initdb failed with status {}: {}{}",
            output.status,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    Ok(())
}

#[cfg(libpglite_native_link_pglite)]
fn check_status(status: i32, operation: &str) -> PgliteResult<()> {
    if status == 0 {
        Ok(())
    } else {
        Err(PgliteError::initialize(format!(
            "{operation} exited with status {status}"
        )))
    }
}

#[cfg(libpglite_native_link_pglite)]
unsafe extern "C" fn native_read(buffer: *mut c_void, max_length: usize) -> isize {
    if buffer.is_null() || max_length == 0 {
        return 0;
    }
    let transport = ACTIVE_TRANSPORT.load(Ordering::SeqCst);
    if transport.is_null() {
        return 0;
    }
    let transport = unsafe { &mut *transport };
    let available = transport.input.len().saturating_sub(transport.read_offset);
    let length = available.min(max_length);
    if length == 0 {
        return 0;
    }
    unsafe {
        ptr::copy_nonoverlapping(
            transport.input.as_ptr().add(transport.read_offset),
            buffer.cast::<u8>(),
            length,
        );
    }
    transport.read_offset += length;
    length as isize
}

#[cfg(libpglite_native_link_pglite)]
unsafe extern "C" fn native_write(buffer: *const c_void, length: usize) -> isize {
    if buffer.is_null() || length == 0 {
        return 0;
    }
    let transport = ACTIVE_TRANSPORT.load(Ordering::SeqCst);
    if transport.is_null() {
        return 0;
    }
    let bytes = unsafe { std::slice::from_raw_parts(buffer.cast::<u8>(), length) };
    unsafe { &mut *transport }.output.extend_from_slice(bytes);
    length as isize
}
