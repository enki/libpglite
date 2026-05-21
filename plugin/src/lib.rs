use std::ffi::c_void;
use std::panic::AssertUnwindSafe;

use libpglite::plugin_abi::{
    LIBPGLITE_PLUGIN_ABI_VERSION, LibpglitePluginBuffer, LibpglitePluginStatus,
};
use libpglite::{PgliteConfig, PgliteError, PgliteRuntime};
use libpglite_native::NativePgliteRuntime;

struct PluginRuntime {
    runtime: NativePgliteRuntime,
}

#[unsafe(no_mangle)]
pub extern "C" fn libpglite_plugin_abi_version() -> u32 {
    LIBPGLITE_PLUGIN_ABI_VERSION
}

#[unsafe(no_mangle)]
pub extern "C" fn libpglite_plugin_buffer_free(buffer: LibpglitePluginBuffer) {
    if buffer.data.is_null() || buffer.len == 0 {
        return;
    }
    unsafe {
        let slice = std::ptr::slice_from_raw_parts_mut(buffer.data, buffer.len);
        drop(Box::from_raw(slice));
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn libpglite_plugin_runtime_create(
    config_data: *const u8,
    config_len: usize,
    runtime_out: *mut *mut c_void,
) -> LibpglitePluginStatus {
    if runtime_out.is_null() {
        return error("runtime output pointer is null");
    }
    unsafe {
        *runtime_out = std::ptr::null_mut();
    }

    ffi_status(|| {
        let config: PgliteConfig = read_json(config_data, config_len)?;
        let runtime = NativePgliteRuntime::open(config)?;
        let runtime = Box::new(PluginRuntime { runtime });
        unsafe {
            *runtime_out = Box::into_raw(runtime).cast::<c_void>();
        }
        Ok(serde_json::Value::Null)
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn libpglite_plugin_runtime_destroy(runtime: *mut c_void) {
    if runtime.is_null() {
        return;
    }
    unsafe {
        drop(Box::from_raw(runtime.cast::<PluginRuntime>()));
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn libpglite_plugin_runtime_exec_protocol_raw(
    runtime: *mut c_void,
    data: *const u8,
    len: usize,
) -> LibpglitePluginStatus {
    ffi_status(|| {
        let message = read_bytes(data, len)?;
        runtime_mut(runtime)?.runtime.exec_protocol_raw(message)
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn libpglite_plugin_runtime_shutdown(
    runtime: *mut c_void,
) -> LibpglitePluginStatus {
    ffi_status(|| {
        runtime_mut(runtime)?.runtime.shutdown()?;
        Ok(serde_json::Value::Null)
    })
}

fn ffi_status<T>(operation: impl FnOnce() -> libpglite::PgliteResult<T>) -> LibpglitePluginStatus
where
    T: serde::Serialize,
{
    match std::panic::catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(value)) => match serde_json::to_vec(&value) {
            Ok(bytes) => LibpglitePluginStatus::ok(buffer(bytes)),
            Err(err) => error(format!("plugin response encode failed: {err}")),
        },
        Ok(Err(err)) => error(err.to_string()),
        Err(_) => error("plugin operation panicked"),
    }
}

fn error(message: impl AsRef<str>) -> LibpglitePluginStatus {
    LibpglitePluginStatus::error(buffer(message.as_ref().as_bytes().to_vec()))
}

fn buffer(bytes: Vec<u8>) -> LibpglitePluginBuffer {
    let mut bytes = bytes.into_boxed_slice();
    let out = LibpglitePluginBuffer {
        data: bytes.as_mut_ptr(),
        len: bytes.len(),
    };
    std::mem::forget(bytes);
    out
}

fn runtime_mut<'a>(runtime: *mut c_void) -> libpglite::PgliteResult<&'a mut PluginRuntime> {
    if runtime.is_null() {
        return Err(PgliteError::protocol("runtime handle is null"));
    }
    Ok(unsafe { &mut *runtime.cast::<PluginRuntime>() })
}

fn read_json<T: serde::de::DeserializeOwned>(
    data: *const u8,
    len: usize,
) -> libpglite::PgliteResult<T> {
    serde_json::from_slice(read_bytes(data, len)?)
        .map_err(|err| PgliteError::initialize(format!("plugin request JSON decode failed: {err}")))
}

fn read_bytes<'a>(data: *const u8, len: usize) -> libpglite::PgliteResult<&'a [u8]> {
    if data.is_null() && len != 0 {
        return Err(PgliteError::protocol("plugin request pointer is null"));
    }
    Ok(if len == 0 {
        &[]
    } else {
        unsafe { std::slice::from_raw_parts(data, len) }
    })
}
