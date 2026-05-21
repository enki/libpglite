use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::path::Path;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_LINK_PGLITE");
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_LINK_MANIFEST");
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_BUILD_DIR");

    let native_manifest = if env::var("LIBPGLITE_NATIVE_LINK_PGLITE").as_deref() == Ok("1") {
        Some(read_native_manifest())
    } else {
        None
    };

    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("linux") {
        let backend_exports = native_manifest
            .as_ref()
            .map(|(_, contents)| backend_export_symbols_from_manifest(contents))
            .unwrap_or_default();
        emit_linux_plugin_export_boundary(&backend_exports);
    }

    if let Some((manifest, contents)) = native_manifest {
        emit_native_pglite_link_inputs(&manifest, &contents);
    }
}

fn emit_linux_plugin_export_boundary(backend_exports: &BTreeSet<String>) {
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR is set by Cargo"));
    let version_script = out_dir.join("libpglite-plugin-native.exports");
    let mut script = String::from(
        r#"{
    global:
        libpglite_plugin_abi_version;
        libpglite_plugin_buffer_free;
        libpglite_plugin_runtime_create;
        libpglite_plugin_runtime_destroy;
        libpglite_plugin_runtime_exec_protocol_raw;
        libpglite_plugin_runtime_shutdown;
"#,
    );
    for symbol in backend_exports {
        script.push_str("        ");
        script.push_str(symbol);
        script.push_str(";\n");
    }
    script.push_str(
        r#"    local:
        *;
};
"#,
    );

    fs::write(&version_script, script).unwrap_or_else(|err| {
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

fn read_native_manifest() -> (PathBuf, String) {
    let manifest = env::var_os("LIBPGLITE_NATIVE_LINK_MANIFEST")
        .map(PathBuf::from)
        .unwrap_or_else(default_manifest_path);
    println!("cargo:rerun-if-changed={}", manifest.display());
    let contents = fs::read_to_string(&manifest).unwrap_or_else(|err| {
        panic!(
            "failed to read native PGlite link manifest at {}: {err}. Run scripts/prepare-native-pglite-link.sh first.",
            manifest.display()
        )
    });
    (manifest, contents)
}

fn backend_export_symbols_from_manifest(contents: &str) -> BTreeSet<String> {
    contents
        .lines()
        .filter_map(|line| line.strip_prefix("backend_export_symbol="))
        .map(ToOwned::to_owned)
        .collect()
}

fn emit_native_pglite_link_inputs(manifest: &Path, contents: &str) {
    let link_inputs = native_link_inputs_from_manifest(manifest, contents);

    for input in link_inputs {
        match input {
            NativeLinkInput::Object(path) => {
                println!("cargo:rustc-cdylib-link-arg={}", path.display());
            }
            NativeLinkInput::Archive(path) => {
                if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
                    println!(
                        "cargo:rustc-cdylib-link-arg=-Wl,-force_load,{}",
                        path.display()
                    );
                } else {
                    println!("cargo:rustc-cdylib-link-arg=-Wl,--whole-archive");
                    println!("cargo:rustc-cdylib-link-arg={}", path.display());
                    println!("cargo:rustc-cdylib-link-arg=-Wl,--no-whole-archive");
                }
            }
            NativeLinkInput::LinkArg(arg) => {
                println!("cargo:rustc-cdylib-link-arg={arg}");
            }
            NativeLinkInput::BackendExportSymbol(symbol) => {
                if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
                    println!(
                        "cargo:rustc-cdylib-link-arg=-Wl,-exported_symbol,_{}",
                        symbol
                    );
                }
            }
        }
    }
}

#[derive(Debug)]
enum NativeLinkInput {
    Object(PathBuf),
    Archive(PathBuf),
    LinkArg(String),
    BackendExportSymbol(String),
}

fn default_manifest_path() -> PathBuf {
    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let repo_root = manifest_dir.parent().expect("plugin crate has repo parent");
    env::var_os("LIBPGLITE_NATIVE_BUILD_DIR")
        .map(PathBuf::from)
        .map(|path| {
            if path.is_absolute() {
                path
            } else {
                repo_root.join(path)
            }
        })
        .unwrap_or_else(|| {
            let target = env::var("TARGET").unwrap_or_else(|_| "unknown-target".to_string());
            repo_root.join("target").join("native-pglite").join(target)
        })
        .join("libpglite_native_link_manifest.txt")
}

fn native_link_inputs_from_manifest(manifest: &Path, contents: &str) -> Vec<NativeLinkInput> {
    let mut link_inputs = Vec::new();
    let mut has_format = false;
    for line in contents.lines() {
        let Some((kind, raw_path)) = line.split_once('=') else {
            continue;
        };
        if kind == "format" && raw_path == "libpglite-native-link-manifest-v1" {
            has_format = true;
            continue;
        }
        if kind == "link_arg" {
            link_inputs.push(NativeLinkInput::LinkArg(raw_path.to_string()));
            continue;
        }
        if kind == "backend_export_symbol" {
            link_inputs.push(NativeLinkInput::BackendExportSymbol(raw_path.to_string()));
            continue;
        }
        if kind != "archive" && kind != "static" && kind != "object" {
            continue;
        }
        reject_debug_native_link_input(manifest, raw_path);
        let path = PathBuf::from(raw_path);
        let path = if path.is_absolute() {
            path
        } else {
            manifest
                .parent()
                .expect("native link manifest has parent")
                .join(path)
        };
        if !path.exists() {
            panic!(
                "native PGlite link manifest {} references missing input {}. Regenerate it with scripts/prepare-native-pglite-link.sh.",
                manifest.display(),
                path.display()
            );
        }
        let input = match kind {
            "object" => NativeLinkInput::Object(path),
            "archive" | "static" => NativeLinkInput::Archive(path),
            _ => unreachable!("manifest link input kind is already filtered"),
        };
        link_inputs.push(input);
    }

    if !has_format {
        panic!(
            "native PGlite link manifest {} is missing format=libpglite-native-link-manifest-v1",
            manifest.display()
        );
    }
    if link_inputs.is_empty() {
        panic!(
            "native PGlite link manifest {} contains no archive/static/object inputs. ADR-0002 native PIC object generation is still incomplete.",
            manifest.display()
        );
    }
    link_inputs
}

fn reject_debug_native_link_input(manifest: &Path, path: &str) {
    if path.contains("/debug/")
        || path.contains("\\debug\\")
        || path.contains("-debug/")
        || path.contains("-debug\\")
    {
        panic!(
            "native PGlite link manifest {} contains debug build input {}. Regenerate it from release/PIC inputs.",
            manifest.display(),
            path
        );
    }
}
