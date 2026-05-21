fn main() {
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_LINK_MANIFEST");
    println!("cargo:rerun-if-env-changed=LIBPGLITE_NATIVE_BUILD_DIR");
}
