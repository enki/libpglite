use std::ffi::c_void;
use std::mem::ManuallyDrop;
use std::path::Path;
use std::sync::Mutex;
use std::sync::MutexGuard;
use std::sync::OnceLock;
use std::sync::TryLockError;

use libloading::Library;
use serde::de::DeserializeOwned;

use crate::plugin_abi::{
    LIBPGLITE_PLUGIN_ABI_VERSION, LIBPGLITE_PLUGIN_STATUS_ERROR, LIBPGLITE_PLUGIN_STATUS_OK,
    LibpglitePluginBuffer, LibpglitePluginStatus,
};
use crate::release;
use crate::release::ResolvedNativePlugin;
use crate::{PgliteConfig, PgliteError, PgliteResult, PgliteRuntime};

type PluginAbiVersionFn = unsafe extern "C" fn() -> u32;
type PluginBufferFreeFn = unsafe extern "C" fn(LibpglitePluginBuffer);
type RuntimeCreateFn =
    unsafe extern "C" fn(*const u8, usize, *mut *mut c_void) -> LibpglitePluginStatus;
type RuntimeDestroyFn = unsafe extern "C" fn(*mut c_void);
type RuntimeExecProtocolRawFn =
    unsafe extern "C" fn(*mut c_void, *const u8, usize) -> LibpglitePluginStatus;
type RuntimeShutdownFn = unsafe extern "C" fn(*mut c_void) -> LibpglitePluginStatus;

#[derive(Debug)]
pub struct DynamicPgliteRuntime {
    plugin: DynamicPlugin,
    runtime: *mut c_void,
    _runtime_guard: MutexGuard<'static, ()>,
    shutdown: bool,
}

#[derive(Debug)]
struct DynamicPlugin {
    _library: ManuallyDrop<Library>,
    buffer_free: PluginBufferFreeFn,
    runtime_create: RuntimeCreateFn,
    runtime_destroy: RuntimeDestroyFn,
    runtime_exec_protocol_raw: RuntimeExecProtocolRawFn,
    runtime_shutdown: RuntimeShutdownFn,
}

impl DynamicPgliteRuntime {
    pub fn load(plugin_path: impl AsRef<Path>, config: PgliteConfig) -> PgliteResult<Self> {
        config.validate()?;
        let runtime_guard = dynamic_runtime_guard()
            .try_lock()
            .map_err(|err| match err {
                TryLockError::WouldBlock => PgliteError::initialize(
                    "another dynamic PGlite runtime is already active in this process",
                ),
                TryLockError::Poisoned(_) => {
                    PgliteError::initialize("dynamic PGlite runtime guard is poisoned")
                }
            })?;
        let plugin = DynamicPlugin::load(plugin_path.as_ref())?;
        let config = serde_json::to_vec(&config).map_err(|err| {
            PgliteError::initialize(format!("dynamic plugin config encode failed: {err}"))
        })?;
        let mut runtime = std::ptr::null_mut();
        let status =
            unsafe { (plugin.runtime_create)(config.as_ptr(), config.len(), &mut runtime) };
        plugin.status_unit(status, PgliteError::initialize)?;
        if runtime.is_null() {
            return Err(PgliteError::initialize(
                "dynamic plugin returned a null runtime handle",
            ));
        }

        Ok(Self {
            plugin,
            runtime,
            _runtime_guard: runtime_guard,
            shutdown: false,
        })
    }

    pub fn initialize_with_bundled_plugin(
        config: PgliteConfig,
        host_binary_path: impl AsRef<Path>,
    ) -> PgliteResult<Self> {
        let plugin = release::BundledNativePluginResolver::from_env()
            .with_host_binary_path(host_binary_path.as_ref())
            .resolve()?;
        Self::load_resolved(plugin, config)
    }

    pub fn initialize_with_plugin_dir(
        config: PgliteConfig,
        plugin_dir: impl AsRef<Path>,
    ) -> PgliteResult<Self> {
        let plugin = release::BundledNativePluginResolver::from_env()
            .with_plugin_dir(plugin_dir.as_ref())
            .resolve()?;
        Self::load_resolved(plugin, config)
    }

    pub fn load_resolved(plugin: ResolvedNativePlugin, config: PgliteConfig) -> PgliteResult<Self> {
        let config = config_with_resolved_plugin_environment(config, &plugin);
        Self::load(plugin.path, config)
    }
}

impl PgliteRuntime for DynamicPgliteRuntime {
    fn open(config: PgliteConfig) -> PgliteResult<Self> {
        let plugin = release::resolve_native_plugin()?;
        Self::load_resolved(plugin, config)
    }

    fn exec_protocol_raw(&mut self, message: &[u8]) -> PgliteResult<Vec<u8>> {
        if self.shutdown {
            return Err(PgliteError::RuntimeShutdown);
        }
        let status = unsafe {
            (self.plugin.runtime_exec_protocol_raw)(self.runtime, message.as_ptr(), message.len())
        };
        self.plugin.status_json(status, PgliteError::protocol)
    }

    fn shutdown(&mut self) -> PgliteResult<()> {
        if self.shutdown {
            return Ok(());
        }
        let status = unsafe { (self.plugin.runtime_shutdown)(self.runtime) };
        self.plugin.status_unit(status, PgliteError::shutdown)?;
        self.shutdown = true;
        Ok(())
    }
}

fn config_with_resolved_plugin_environment(
    mut config: PgliteConfig,
    plugin: &ResolvedNativePlugin,
) -> PgliteConfig {
    let Some(prefix) = &plugin.postgres_prefix else {
        return config;
    };
    if config
        .environment
        .iter()
        .any(|(key, _)| key == release::LIBPGLITE_POSTGRES_PREFIX_ENV)
    {
        return config;
    }
    config.environment.push((
        release::LIBPGLITE_POSTGRES_PREFIX_ENV.to_string(),
        prefix.display().to_string(),
    ));
    config
}

impl Drop for DynamicPgliteRuntime {
    fn drop(&mut self) {
        if !self.runtime.is_null() {
            if !self.shutdown {
                let _ = unsafe { (self.plugin.runtime_shutdown)(self.runtime) };
            }
            unsafe { (self.plugin.runtime_destroy)(self.runtime) };
            self.runtime = std::ptr::null_mut();
        }
    }
}

fn dynamic_runtime_guard() -> &'static Mutex<()> {
    static DYNAMIC_RUNTIME_GUARD: OnceLock<Mutex<()>> = OnceLock::new();
    DYNAMIC_RUNTIME_GUARD.get_or_init(|| Mutex::new(()))
}

impl DynamicPlugin {
    fn load(path: &Path) -> PgliteResult<Self> {
        let library = load_plugin_library(path).map_err(|err| {
            PgliteError::initialize(format!(
                "dynamic plugin load failed at {}: {err}",
                path.display()
            ))
        })?;

        let abi_version: PluginAbiVersionFn =
            unsafe { library.get(b"libpglite_plugin_abi_version") }
                .map(|symbol| *symbol)
                .map_err(|err| {
                    PgliteError::initialize(format!("plugin ABI symbol missing: {err}"))
                })?;
        let reported = unsafe { abi_version() };
        if reported != LIBPGLITE_PLUGIN_ABI_VERSION {
            return Err(PgliteError::initialize(format!(
                "dynamic plugin ABI version {reported} is incompatible with host ABI {LIBPGLITE_PLUGIN_ABI_VERSION}"
            )));
        }

        Ok(Self {
            buffer_free: unsafe { symbol(&library, b"libpglite_plugin_buffer_free")? },
            runtime_create: unsafe { symbol(&library, b"libpglite_plugin_runtime_create")? },
            runtime_destroy: unsafe { symbol(&library, b"libpglite_plugin_runtime_destroy")? },
            runtime_exec_protocol_raw: unsafe {
                symbol(&library, b"libpglite_plugin_runtime_exec_protocol_raw")?
            },
            runtime_shutdown: unsafe { symbol(&library, b"libpglite_plugin_runtime_shutdown")? },
            _library: ManuallyDrop::new(library),
        })
    }

    fn status_unit(
        &self,
        status: LibpglitePluginStatus,
        err: fn(String) -> PgliteError,
    ) -> PgliteResult<()> {
        let _: serde_json::Value = self.status_json(status, err)?;
        Ok(())
    }

    fn status_json<T: DeserializeOwned>(
        &self,
        status: LibpglitePluginStatus,
        err: fn(String) -> PgliteError,
    ) -> PgliteResult<T> {
        let payload = self.take_payload(status.payload);
        match status.code {
            LIBPGLITE_PLUGIN_STATUS_OK => serde_json::from_slice(&payload).map_err(|decode| {
                err(format!(
                    "dynamic plugin response JSON decode failed: {decode}"
                ))
            }),
            LIBPGLITE_PLUGIN_STATUS_ERROR => {
                let message = String::from_utf8_lossy(&payload).into_owned();
                Err(err(message))
            }
            other => Err(err(format!(
                "dynamic plugin returned unknown status {other}"
            ))),
        }
    }

    fn take_payload(&self, buffer: LibpglitePluginBuffer) -> Vec<u8> {
        if buffer.data.is_null() || buffer.len == 0 {
            return Vec::new();
        }
        let bytes = unsafe { std::slice::from_raw_parts(buffer.data, buffer.len) }.to_vec();
        unsafe { (self.buffer_free)(buffer) };
        bytes
    }
}

#[cfg(unix)]
fn load_plugin_library(path: &Path) -> Result<Library, libloading::Error> {
    use libloading::os::unix::{Library as UnixLibrary, RTLD_GLOBAL, RTLD_NOW};

    unsafe { UnixLibrary::open(Some(path), RTLD_NOW | RTLD_GLOBAL).map(Into::into) }
}

#[cfg(not(unix))]
fn load_plugin_library(path: &Path) -> Result<Library, libloading::Error> {
    unsafe { Library::new(path) }
}

unsafe fn symbol<T: Copy>(library: &Library, name: &[u8]) -> PgliteResult<T> {
    unsafe { library.get(name) }
        .map(|symbol| *symbol)
        .map_err(|err| {
            PgliteError::initialize(format!(
                "plugin ABI symbol `{}` missing: {err}",
                String::from_utf8_lossy(name)
            ))
        })
}
