//! Native PGlite adapter for the stable `libpglite` facade.
//!
//! This crate is intentionally internal. Product hosts should load it through
//! the dynamic plugin instead of statically linking it.

#[cfg(not(feature = "internal-adapter"))]
compile_error!(
    "`libpglite-native` is an internal implementation crate. Build the dynamic \
     plugin instead of statically linking this crate into a downstream host."
);

use libpglite::{PgliteConfig, PgliteError, PgliteResult, PgliteRuntime};

#[cfg(libpglite_native_link_pglite)]
mod ffi {
    use std::ffi::c_char;
    use std::ffi::c_void;

    unsafe extern "C" {
        pub fn libpglite_native_pgl_run_atexit_funcs() -> i32;
        pub fn libpglite_native_pgl_set_active(new_value: i32) -> i32;
        pub fn libpglite_native_pgl_pq_flush() -> i32;
        pub fn libpglite_native_postgres_main_longjmp() -> i32;
        pub fn libpglite_native_postgres_main_loop_once() -> i32;
        pub fn libpglite_native_postgres_send_ready_for_query_if_necessary() -> i32;
        pub fn libpglite_native_postgres_single_user_main(
            argc: i32,
            argv: *mut *mut c_char,
            username: *const c_char,
        ) -> i32;
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
            ^ libpglite_native_pgl_pq_flush as usize
            ^ libpglite_native_pgl_run_atexit_funcs as usize
            ^ libpglite_native_pgl_set_active as usize
    }
}

#[derive(Debug)]
pub struct NativePgliteRuntime {
    _config: PgliteConfig,
    shutdown: bool,
}

impl PgliteRuntime for NativePgliteRuntime {
    fn open(config: PgliteConfig) -> PgliteResult<Self> {
        config.validate()?;
        #[cfg(libpglite_native_link_pglite)]
        std::hint::black_box(ffi::native_link_probe());

        Err(PgliteError::initialize(
            "native PGlite runtime is not linked yet; see docs/ADR-0002-NATIVE-PGLITE-BUILD-LANE.md",
        ))
    }

    fn exec_protocol_raw(&mut self, _message: &[u8]) -> PgliteResult<Vec<u8>> {
        if self.shutdown {
            return Err(PgliteError::RuntimeShutdown);
        }
        Err(PgliteError::protocol(
            "native PGlite protocol execution is not implemented yet",
        ))
    }

    fn shutdown(&mut self) -> PgliteResult<()> {
        self.shutdown = true;
        Ok(())
    }
}
