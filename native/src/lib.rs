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

#[derive(Debug)]
pub struct NativePgliteRuntime {
    _config: PgliteConfig,
    shutdown: bool,
}

impl PgliteRuntime for NativePgliteRuntime {
    fn open(config: PgliteConfig) -> PgliteResult<Self> {
        config.validate()?;
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
