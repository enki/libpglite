use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("linux") {
        emit_linux_plugin_export_boundary();
    }

    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_LINK_MANIFEST");
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_BUILD_DIR");
}

fn emit_linux_plugin_export_boundary() {
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR is set by Cargo"));
    let version_script = out_dir.join("libpglite-plugin-native.exports");
    fs::write(
        &version_script,
        r#"{
    global:
        libpglite_plugin_abi_version;
        libpglite_plugin_buffer_free;
        libpglite_plugin_runtime_create;
        libpglite_plugin_runtime_destroy;
        libpglite_plugin_runtime_exec_protocol_raw;
        libpglite_plugin_runtime_shutdown;
    local:
        *;
};
"#,
    )
    .unwrap_or_else(|err| {
        panic!(
            "failed to write Linux plugin export version script at {}: {err}",
            version_script.display()
        )
    });

    println!(
        "cargo:rustc-cdylib-link-arg=-Wl,--version-script={}",
        version_script.display()
    );
    println!("cargo:rustc-cdylib-link-arg=-Wl,--exclude-libs,ALL");
}
