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
use std::fs::{self, File, OpenOptions};
#[cfg(libpglite_native_link_pglite)]
use std::io::{Read, Seek, SeekFrom};
#[cfg(libpglite_native_link_pglite)]
use std::os::fd::{AsRawFd, RawFd};
#[cfg(libpglite_native_link_pglite)]
use std::path::{Path, PathBuf};
#[cfg(libpglite_native_link_pglite)]
use std::process::Command;
#[cfg(libpglite_native_link_pglite)]
use std::ptr;
#[cfg(libpglite_native_link_pglite)]
use std::sync::atomic::{AtomicBool, AtomicPtr, AtomicU64, Ordering};
#[cfg(libpglite_native_link_pglite)]
use std::sync::{Mutex, OnceLock};

use libpglite::{
    PgliteBackendOutputLedger, PgliteConfig, PgliteError, PgliteResult, PgliteRuntime,
};
#[cfg(libpglite_native_link_pglite)]
use libpglite::{PgliteBackendOutputRecord, PgliteBackendOutputStream};

#[cfg(libpglite_native_link_pglite)]
const POSTGRES_PREFIX_ENV: &str = "LIBPGLITE_POSTGRES_PREFIX";
#[cfg(libpglite_native_link_pglite)]
const PGLITE_EXIT_ALIVE: i32 = 99;
#[cfg(libpglite_native_link_pglite)]
const POSTGRES_MAIN_LONGJMP: i32 = 100;
#[cfg(any(test, libpglite_native_link_pglite))]
const POSTMASTER_PID_FILE: &str = "postmaster.pid";

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
    #[cfg(libpglite_native_link_pglite)]
    backend_output: PgliteBackendOutputLedger,
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
#[cfg(libpglite_native_link_pglite)]
static NATIVE_BACKEND_START_ATTEMPTED: AtomicBool = AtomicBool::new(false);
#[cfg(libpglite_native_link_pglite)]
static NATIVE_STDIO_CAPTURE_SEQUENCE: AtomicU64 = AtomicU64::new(1);

impl PgliteRuntime for NativePgliteRuntime {
    fn open(config: PgliteConfig) -> PgliteResult<Self> {
        config.validate()?;
        #[cfg(libpglite_native_link_pglite)]
        {
            std::hint::black_box(ffi::native_link_probe());
            let postgres_prefix = postgres_prefix(&config)?;
            ensure_data_dir(&postgres_prefix, &config.data_dir)?;
            remove_stale_single_user_postmaster_pid(&config.data_dir)?;
            claim_native_backend_start()?;

            let mut runtime = Self {
                config,
                transport: Box::default(),
                backend_output: PgliteBackendOutputLedger::empty(),
                shutdown: false,
            };
            ACTIVE_TRANSPORT.store(&mut *runtime.transport, Ordering::SeqCst);
            unsafe {
                ffi::pgl_set_rw_cbs(Some(native_read), Some(native_write));
            }
            if let Err(err) = runtime.with_backend_stdio_capture(
                NativeBackendStdioPhase::Startup,
                NativePgliteRuntime::start_postgres,
            ) {
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

            self.with_backend_stdio_capture(NativeBackendStdioPhase::Protocol, |runtime| {
                while runtime.transport.read_offset < runtime.transport.input.len()
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
                Ok(())
            })?;

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
        self.with_backend_stdio_capture(NativeBackendStdioPhase::Shutdown, |runtime| unsafe {
            let _ = ffi::libpglite_native_pgl_set_active(0);
            let status = ffi::libpglite_native_pgl_run_atexit_funcs();
            ACTIVE_TRANSPORT.store(ptr::null_mut(), Ordering::SeqCst);
            ffi::pgl_set_rw_cbs(None, None);
            check_status(status, "pgl_run_atexit_funcs")?;
            remove_current_single_user_postmaster_pid(&runtime.config.data_dir)
        })?;
        self.shutdown = true;
        Ok(())
    }

    fn take_backend_output(&mut self) -> PgliteBackendOutputLedger {
        #[cfg(libpglite_native_link_pglite)]
        {
            return std::mem::take(&mut self.backend_output);
        }
        #[cfg(not(libpglite_native_link_pglite))]
        {
            PgliteBackendOutputLedger::empty()
        }
    }
}

impl NativePgliteRuntime {
    #[cfg(libpglite_native_link_pglite)]
    fn with_backend_stdio_capture<T>(
        &mut self,
        phase: NativeBackendStdioPhase,
        operation: impl FnOnce(&mut Self) -> PgliteResult<T>,
    ) -> PgliteResult<T> {
        let _guard = native_stdio_capture_guard().lock().map_err(|_| {
            PgliteError::initialize("native backend stdio capture guard is poisoned")
        })?;
        let lease = NativeBackendStdioLease::begin(phase)?;
        let result = operation(self);
        let records = lease.finish()?;
        self.backend_output.extend(records);
        result
    }

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
fn claim_native_backend_start() -> PgliteResult<()> {
    NATIVE_BACKEND_START_ATTEMPTED
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .map(|_| ())
        .map_err(|_| {
            PgliteError::initialize(
                "native PGlite currently supports only one backend startup per process; \
                 deterministic same-process restart requires a future PostgreSQL global-state reset contract",
            )
        })
}

#[cfg(libpglite_native_link_pglite)]
#[derive(Debug, Clone, Copy)]
enum NativeBackendStdioPhase {
    Startup,
    Protocol,
    Shutdown,
}

#[cfg(libpglite_native_link_pglite)]
impl NativeBackendStdioPhase {
    fn as_str(self) -> &'static str {
        match self {
            Self::Startup => "native_backend_startup",
            Self::Protocol => "native_backend_protocol",
            Self::Shutdown => "native_backend_shutdown",
        }
    }
}

#[cfg(libpglite_native_link_pglite)]
struct NativeBackendStdioLease {
    phase: NativeBackendStdioPhase,
    stdin: RedirectedFd,
    stdout: CapturedFd,
    stderr: CapturedFd,
    restored: bool,
}

#[cfg(libpglite_native_link_pglite)]
impl NativeBackendStdioLease {
    fn begin(phase: NativeBackendStdioPhase) -> PgliteResult<Self> {
        unsafe {
            libc::fflush(ptr::null_mut());
        }
        Ok(Self {
            phase,
            stdin: RedirectedFd::redirect_to_dev_null(libc::STDIN_FILENO, "stdin")?,
            stdout: CapturedFd::redirect(libc::STDOUT_FILENO, "stdout")?,
            stderr: CapturedFd::redirect(libc::STDERR_FILENO, "stderr")?,
            restored: false,
        })
    }

    fn finish(mut self) -> PgliteResult<Vec<PgliteBackendOutputRecord>> {
        self.restore()?;
        let mut records = Vec::new();
        if let Some(text) = self.stdout.read_text()? {
            records.push(PgliteBackendOutputRecord::new(
                PgliteBackendOutputStream::Stdout,
                self.phase.as_str(),
                text,
            ));
        }
        if let Some(text) = self.stderr.read_text()? {
            records.push(PgliteBackendOutputRecord::new(
                PgliteBackendOutputStream::Stderr,
                self.phase.as_str(),
                text,
            ));
        }
        Ok(records)
    }

    fn restore(&mut self) -> PgliteResult<()> {
        if self.restored {
            return Ok(());
        }
        unsafe {
            libc::fflush(ptr::null_mut());
        }
        self.stderr.restore()?;
        self.stdout.restore()?;
        self.stdin.restore()?;
        self.restored = true;
        Ok(())
    }
}

#[cfg(libpglite_native_link_pglite)]
impl Drop for NativeBackendStdioLease {
    fn drop(&mut self) {
        let _ = self.restore();
    }
}

#[cfg(libpglite_native_link_pglite)]
struct CapturedFd {
    label: &'static str,
    fd: RawFd,
    saved_fd: RawFd,
    file: File,
    path: PathBuf,
    restored: bool,
}

#[cfg(libpglite_native_link_pglite)]
struct RedirectedFd {
    label: &'static str,
    fd: RawFd,
    saved_fd: RawFd,
    restored: bool,
}

#[cfg(libpglite_native_link_pglite)]
impl RedirectedFd {
    fn redirect_to_dev_null(fd: RawFd, label: &'static str) -> PgliteResult<Self> {
        let file = OpenOptions::new()
            .read(true)
            .open("/dev/null")
            .map_err(|error| {
                PgliteError::initialize(format!(
                    "native backend stdio capture failed to open /dev/null for {label}: {error}"
                ))
            })?;
        let saved_fd = unsafe { libc::dup(fd) };
        if saved_fd < 0 {
            return Err(PgliteError::initialize(format!(
                "native backend stdio capture failed to duplicate {label}: {}",
                std::io::Error::last_os_error()
            )));
        }
        if unsafe { libc::dup2(file.as_raw_fd(), fd) } < 0 {
            let error = std::io::Error::last_os_error();
            unsafe {
                libc::close(saved_fd);
            }
            return Err(PgliteError::initialize(format!(
                "native backend stdio capture failed to redirect {label}: {error}"
            )));
        }
        Ok(Self {
            label,
            fd,
            saved_fd,
            restored: false,
        })
    }

    fn restore(&mut self) -> PgliteResult<()> {
        if self.restored {
            return Ok(());
        }
        if unsafe { libc::dup2(self.saved_fd, self.fd) } < 0 {
            return Err(PgliteError::initialize(format!(
                "native backend stdio capture failed to restore {}: {}",
                self.label,
                std::io::Error::last_os_error()
            )));
        }
        unsafe {
            libc::close(self.saved_fd);
        }
        self.restored = true;
        Ok(())
    }
}

#[cfg(libpglite_native_link_pglite)]
impl Drop for RedirectedFd {
    fn drop(&mut self) {
        let _ = self.restore();
    }
}

#[cfg(libpglite_native_link_pglite)]
impl CapturedFd {
    fn redirect(fd: RawFd, label: &'static str) -> PgliteResult<Self> {
        let (path, file) = create_capture_file(label)?;
        let saved_fd = unsafe { libc::dup(fd) };
        if saved_fd < 0 {
            return Err(PgliteError::initialize(format!(
                "native backend stdio capture failed to duplicate {label}: {}",
                std::io::Error::last_os_error()
            )));
        }
        if unsafe { libc::dup2(file.as_raw_fd(), fd) } < 0 {
            let error = std::io::Error::last_os_error();
            unsafe {
                libc::close(saved_fd);
            }
            return Err(PgliteError::initialize(format!(
                "native backend stdio capture failed to redirect {label}: {error}"
            )));
        }
        Ok(Self {
            label,
            fd,
            saved_fd,
            file,
            path,
            restored: false,
        })
    }

    fn restore(&mut self) -> PgliteResult<()> {
        if self.restored {
            return Ok(());
        }
        if unsafe { libc::dup2(self.saved_fd, self.fd) } < 0 {
            return Err(PgliteError::initialize(format!(
                "native backend stdio capture failed to restore {}: {}",
                self.label,
                std::io::Error::last_os_error()
            )));
        }
        unsafe {
            libc::close(self.saved_fd);
        }
        self.restored = true;
        Ok(())
    }

    fn read_text(&mut self) -> PgliteResult<Option<String>> {
        self.file.seek(SeekFrom::Start(0)).map_err(|error| {
            PgliteError::initialize(format!(
                "native backend stdio capture failed to seek {} output: {error}",
                self.label
            ))
        })?;
        let mut bytes = Vec::new();
        self.file.read_to_end(&mut bytes).map_err(|error| {
            PgliteError::initialize(format!(
                "native backend stdio capture failed to read {} output: {error}",
                self.label
            ))
        })?;
        if bytes.is_empty() {
            Ok(None)
        } else {
            Ok(Some(String::from_utf8_lossy(&bytes).into_owned()))
        }
    }
}

#[cfg(libpglite_native_link_pglite)]
impl Drop for CapturedFd {
    fn drop(&mut self) {
        let _ = self.restore();
        let _ = fs::remove_file(&self.path);
    }
}

#[cfg(libpglite_native_link_pglite)]
fn create_capture_file(label: &'static str) -> PgliteResult<(PathBuf, File)> {
    for _ in 0..100 {
        let sequence = NATIVE_STDIO_CAPTURE_SEQUENCE.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!(
            "libpglite-native-stdio-{}-{label}-{sequence}.log",
            std::process::id()
        ));
        match OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(PgliteError::initialize(format!(
                    "native backend stdio capture failed to create {}: {error}",
                    path.display()
                )));
            }
        }
    }
    Err(PgliteError::initialize(
        "native backend stdio capture could not allocate a unique capture file",
    ))
}

#[cfg(libpglite_native_link_pglite)]
fn native_stdio_capture_guard() -> &'static Mutex<()> {
    static GUARD: OnceLock<Mutex<()>> = OnceLock::new();
    GUARD.get_or_init(|| Mutex::new(()))
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

#[cfg(any(test, libpglite_native_link_pglite))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PostmasterPidLock {
    SingleUser { pid: i32 },
    Postmaster,
}

#[cfg(any(test, libpglite_native_link_pglite))]
fn remove_stale_single_user_postmaster_pid(data_dir: &std::path::Path) -> PgliteResult<()> {
    let Some(PostmasterPidLock::SingleUser { pid }) = read_postmaster_pid_lock(data_dir)? else {
        return Ok(());
    };
    if native_process_is_alive(pid) {
        return Err(PgliteError::initialize(format!(
            "native PGlite data directory {} is locked by active single-user process {pid}",
            data_dir.display()
        )));
    }
    remove_postmaster_pid(data_dir, "stale single-user")
}

#[cfg(any(test, libpglite_native_link_pglite))]
fn remove_current_single_user_postmaster_pid(data_dir: &std::path::Path) -> PgliteResult<()> {
    let Some(PostmasterPidLock::SingleUser { pid }) = read_postmaster_pid_lock(data_dir)? else {
        return Ok(());
    };
    if pid != std::process::id() as i32 {
        if native_process_is_alive(pid) {
            return Err(PgliteError::shutdown(format!(
                "native PGlite data directory {} is locked by active single-user process {pid}",
                data_dir.display()
            )));
        }
        return remove_postmaster_pid(data_dir, "stale single-user");
    }
    remove_postmaster_pid(data_dir, "current single-user")
}

#[cfg(any(test, libpglite_native_link_pglite))]
fn read_postmaster_pid_lock(data_dir: &std::path::Path) -> PgliteResult<Option<PostmasterPidLock>> {
    let path = data_dir.join(POSTMASTER_PID_FILE);
    let contents = match std::fs::read_to_string(&path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(PgliteError::initialize(format!(
                "failed to read native PGlite lockfile {}: {error}",
                path.display()
            )));
        }
    };
    let Some(first_line) = contents.lines().next() else {
        return Ok(Some(PostmasterPidLock::Postmaster));
    };
    let Ok(raw_pid) = first_line.trim().parse::<i64>() else {
        return Ok(Some(PostmasterPidLock::Postmaster));
    };
    if raw_pid >= 0 {
        return Ok(Some(PostmasterPidLock::Postmaster));
    }
    let pid = raw_pid
        .checked_abs()
        .and_then(|pid| i32::try_from(pid).ok())
        .ok_or_else(|| {
            PgliteError::initialize(format!(
                "native PGlite single-user lock pid is outside supported range in {}",
                path.display()
            ))
        })?;
    Ok(Some(PostmasterPidLock::SingleUser { pid }))
}

#[cfg(any(test, libpglite_native_link_pglite))]
fn remove_postmaster_pid(data_dir: &std::path::Path, lock_kind: &'static str) -> PgliteResult<()> {
    let path = data_dir.join(POSTMASTER_PID_FILE);
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(PgliteError::initialize(format!(
            "failed to remove {lock_kind} native PGlite lockfile {}: {error}",
            path.display()
        ))),
    }
}

#[cfg(any(test, libpglite_native_link_pglite))]
fn native_process_is_alive(pid: i32) -> bool {
    if pid <= 0 {
        return false;
    }
    let status = unsafe { libc::kill(pid, 0) };
    status == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
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

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_data_dir(label: &str) -> std::path::PathBuf {
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system clock is after Unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "libpglite-native-{label}-{}-{timestamp}",
            std::process::id(),
        ))
    }

    fn with_data_dir(label: &str, test: impl FnOnce(&std::path::Path)) {
        let data_dir = unique_data_dir(label);
        std::fs::create_dir_all(&data_dir).expect("create temp native data dir");
        test(&data_dir);
        std::fs::remove_dir_all(&data_dir).expect("remove temp native data dir");
    }

    fn write_postmaster_pid(data_dir: &std::path::Path, first_line: impl std::fmt::Display) {
        std::fs::write(
            data_dir.join(POSTMASTER_PID_FILE),
            format!("{first_line}\n{}\n0\n5432\n", data_dir.display()),
        )
        .expect("write postmaster.pid");
    }

    #[test]
    fn stale_single_user_postmaster_pid_is_removed_before_startup() {
        with_data_dir("stale-single-user", |data_dir| {
            write_postmaster_pid(data_dir, -2_147_483_647_i64);

            remove_stale_single_user_postmaster_pid(data_dir)
                .expect("remove stale single-user postmaster.pid");

            assert!(!data_dir.join(POSTMASTER_PID_FILE).exists());
        });
    }

    #[test]
    fn positive_postmaster_pid_is_not_removed_before_startup() {
        with_data_dir("positive-postmaster", |data_dir| {
            write_postmaster_pid(data_dir, 2_147_483_647_i64);

            remove_stale_single_user_postmaster_pid(data_dir)
                .expect("leave positive postmaster.pid in place");

            assert!(data_dir.join(POSTMASTER_PID_FILE).exists());
        });
    }

    #[test]
    fn current_single_user_postmaster_pid_is_removed_at_shutdown() {
        with_data_dir("current-single-user", |data_dir| {
            write_postmaster_pid(data_dir, -(std::process::id() as i64));

            remove_current_single_user_postmaster_pid(data_dir)
                .expect("remove current single-user postmaster.pid");

            assert!(!data_dir.join(POSTMASTER_PID_FILE).exists());
        });
    }

    #[test]
    fn non_pid_postmaster_pid_is_not_single_user() {
        with_data_dir("non-pid-postmaster", |data_dir| {
            write_postmaster_pid(data_dir, "not-a-pid");

            let lock = read_postmaster_pid_lock(data_dir).expect("read lock");

            assert_eq!(lock, Some(PostmasterPidLock::Postmaster));
        });
    }
}
