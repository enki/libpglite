//! Hostable PGlite embedding facade.
//!
//! This crate owns the stable Rust boundary for hosting PGlite through a
//! replaceable native plugin. It deliberately does not expose raw Postgres,
//! Emscripten, wasm2c, or generated PGlite symbols across its public API.

use std::path::PathBuf;

use serde::Deserialize;
use serde::Serialize;

#[cfg(feature = "dynamic-loading")]
pub mod dynamic;
pub mod release;

pub type PgliteResult<T> = Result<T, PgliteError>;

pub mod plugin_abi {
    pub const LIBPGLITE_PLUGIN_ABI_VERSION: u32 = 1;

    pub const LIBPGLITE_PLUGIN_STATUS_OK: u32 = 0;
    pub const LIBPGLITE_PLUGIN_STATUS_ERROR: u32 = 1;

    #[repr(C)]
    #[derive(Debug, Clone, Copy)]
    pub struct LibpglitePluginBuffer {
        pub data: *mut u8,
        pub len: usize,
    }

    impl LibpglitePluginBuffer {
        pub const fn empty() -> Self {
            Self {
                data: std::ptr::null_mut(),
                len: 0,
            }
        }
    }

    #[repr(C)]
    #[derive(Debug, Clone, Copy)]
    pub struct LibpglitePluginStatus {
        pub code: u32,
        pub payload: LibpglitePluginBuffer,
    }

    impl LibpglitePluginStatus {
        pub const fn ok(payload: LibpglitePluginBuffer) -> Self {
            Self {
                code: LIBPGLITE_PLUGIN_STATUS_OK,
                payload,
            }
        }

        pub const fn error(payload: LibpglitePluginBuffer) -> Self {
            Self {
                code: LIBPGLITE_PLUGIN_STATUS_ERROR,
                payload,
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PgliteConfig {
    pub host_id: String,
    pub data_dir: PathBuf,
    pub user: String,
    pub database: String,
    pub environment: Vec<(String, String)>,
}

impl PgliteConfig {
    pub fn new(host_id: impl Into<String>, data_dir: impl Into<PathBuf>) -> Self {
        Self {
            host_id: host_id.into(),
            data_dir: data_dir.into(),
            user: "postgres".to_string(),
            database: "postgres".to_string(),
            environment: Vec::new(),
        }
    }

    pub fn with_user(mut self, user: impl Into<String>) -> Self {
        self.user = user.into();
        self
    }

    pub fn with_database(mut self, database: impl Into<String>) -> Self {
        self.database = database.into();
        self
    }

    pub fn with_environment(
        mut self,
        environment: impl IntoIterator<Item = (impl Into<String>, impl Into<String>)>,
    ) -> Self {
        self.environment = environment
            .into_iter()
            .map(|(key, value)| (key.into(), value.into()))
            .collect();
        self
    }

    pub fn validate(&self) -> PgliteResult<()> {
        if self.host_id.trim().is_empty() {
            return Err(PgliteError::config("host id is empty"));
        }
        if self.user.trim().is_empty() {
            return Err(PgliteError::config("database user is empty"));
        }
        if self.database.trim().is_empty() {
            return Err(PgliteError::config("database name is empty"));
        }
        if self.data_dir.as_os_str().is_empty() {
            return Err(PgliteError::config("data directory is empty"));
        }
        Ok(())
    }
}

pub trait PgliteRuntime {
    fn open(config: PgliteConfig) -> PgliteResult<Self>
    where
        Self: Sized;

    fn exec_protocol_raw(&mut self, message: &[u8]) -> PgliteResult<Vec<u8>>;

    fn shutdown(&mut self) -> PgliteResult<()>;
}

#[derive(Debug, thiserror::Error)]
pub enum PgliteError {
    #[error("PGlite configuration error: {message}")]
    Config { message: String },
    #[error("PGlite initialization failed: {message}")]
    Initialize { message: String },
    #[error("PGlite protocol execution failed: {message}")]
    Protocol { message: String },
    #[error("PGlite shutdown failed: {message}")]
    Shutdown { message: String },
    #[error("PGlite runtime is already shut down")]
    RuntimeShutdown,
}

impl PgliteError {
    pub fn config(message: impl Into<String>) -> Self {
        Self::Config {
            message: message.into(),
        }
    }

    pub fn initialize(message: impl Into<String>) -> Self {
        Self::Initialize {
            message: message.into(),
        }
    }

    pub fn protocol(message: impl Into<String>) -> Self {
        Self::Protocol {
            message: message.into(),
        }
    }

    pub fn shutdown(message: impl Into<String>) -> Self {
        Self::Shutdown {
            message: message.into(),
        }
    }
}
