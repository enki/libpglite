use std::env;
use std::fs;
use std::path::Path;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_LINK_PGLITE");
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_LINK_MANIFEST");
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_BUILD_DIR");

    if env::var("LIBPGLITE_NATIVE_LINK_PGLITE").as_deref() != Ok("1") {
        return;
    }

    let manifest = env::var_os("LIBPGLITE_NATIVE_LINK_MANIFEST")
        .map(PathBuf::from)
        .unwrap_or_else(default_manifest_path);
    let contents = fs::read_to_string(&manifest).unwrap_or_else(|err| {
        panic!(
            "failed to read native PGlite link manifest at {}: {err}. Run scripts/prepare-native-pglite-link.sh first.",
            manifest.display()
        )
    });
    let link_inputs = native_link_inputs_from_manifest(&manifest, &contents);

    for path in link_inputs {
        if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
            println!("cargo:rustc-link-arg=-Wl,-force_load,{}", path.display());
        } else {
            println!("cargo:rustc-link-arg=-Wl,--whole-archive");
            println!("cargo:rustc-link-arg={}", path.display());
            println!("cargo:rustc-link-arg=-Wl,--no-whole-archive");
        }
    }
}

fn default_manifest_path() -> PathBuf {
    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let repo_root = manifest_dir.parent().expect("native crate has repo parent");
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

fn native_link_inputs_from_manifest(manifest: &Path, contents: &str) -> Vec<PathBuf> {
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
        link_inputs.push(path);
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
