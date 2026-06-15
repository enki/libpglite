use libpglite::release::{
    BundledNativePluginResolver, LIBPGLITE_HOME_ENV, LIBPGLITE_PLUGIN_PATH_ENV,
    NativePluginResolver, NativePluginSource, POSTGRES_PREFIX_DIR, RELEASE_TAG,
    current_native_plugin_asset, expected_checksum, verify_file_checksum,
};

#[test]
fn current_asset_names_match_release_contract() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    assert_eq!(asset.release_tag, RELEASE_TAG);
    assert_eq!(asset.repository, "enki/libpglite");
    assert_eq!(
        asset.asset_name,
        format!(
            "libpglite-plugin-native-{}-{}.tar.zst",
            RELEASE_TAG, asset.target
        )
    );
    assert!(asset.archive_url().ends_with(&asset.asset_name));
    assert_eq!(
        asset.checksums_asset_name(),
        format!("libpglite-plugin-native-{}-checksums.txt", RELEASE_TAG)
    );
    assert_eq!(
        asset.notice_asset_name(),
        format!("libpglite-plugin-native-{}-NOTICE.txt", RELEASE_TAG)
    );
    assert_eq!(
        asset.source_asset_name(),
        format!("libpglite-plugin-native-{}-SOURCE.txt", RELEASE_TAG)
    );
    assert_eq!(
        asset.licenses_asset_name(),
        format!("libpglite-plugin-native-{}-licenses.json", RELEASE_TAG)
    );
    if asset.target.ends_with("apple-darwin") {
        assert_eq!(asset.plugin_filename, "liblibpglite_plugin_native.dylib");
    } else {
        assert_eq!(asset.plugin_filename, "liblibpglite_plugin_native.so");
    }
}

#[test]
fn resolver_prefers_explicit_plugin_path() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let plugin_path = tempdir.path().join(asset.plugin_filename);
    std::fs::write(&plugin_path, b"not a real dynamic library").expect("write plugin placeholder");
    let cached_dir = asset.cache_dir(tempdir.path().join("cache"));
    std::fs::create_dir_all(&cached_dir).expect("create cache dir");
    std::fs::write(cached_dir.join(asset.plugin_filename), b"cached").expect("write cache");

    let resolved = NativePluginResolver::new()
        .with_plugin_path(&plugin_path)
        .with_cache_root(tempdir.path().join("cache"))
        .resolve()
        .expect("explicit plugin path resolves");

    assert_eq!(resolved.path, plugin_path);
    assert_eq!(resolved.postgres_prefix, None);
    assert_eq!(resolved.source, NativePluginSource::Environment);
}

#[test]
fn resolver_uses_standard_release_cache() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let cached_plugin = asset.cached_plugin_path(tempdir.path());
    std::fs::create_dir_all(cached_plugin.parent().expect("plugin parent")).expect("cache dir");
    std::fs::write(&cached_plugin, b"not a real dynamic library").expect("write plugin");
    let cached_prefix = cached_plugin
        .parent()
        .expect("plugin parent")
        .join(POSTGRES_PREFIX_DIR);
    std::fs::create_dir_all(&cached_prefix).expect("create cached prefix");

    let resolved = NativePluginResolver::new()
        .with_cache_root(tempdir.path())
        .resolve()
        .expect("cached plugin resolves");

    assert_eq!(resolved.path, cached_plugin);
    assert_eq!(resolved.postgres_prefix, Some(cached_prefix));
    assert_eq!(resolved.source, NativePluginSource::Cache);
}

#[test]
fn resolver_does_not_use_cache_without_explicit_root() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let cached_plugin = asset.cached_plugin_path(tempdir.path());
    std::fs::create_dir_all(cached_plugin.parent().expect("plugin parent")).expect("cache dir");
    std::fs::write(&cached_plugin, b"not a real dynamic library").expect("write plugin");

    let error = NativePluginResolver::new()
        .resolve()
        .expect_err("ambient cache should not resolve without explicit cache root");
    let message = error.to_string();

    assert!(
        !message.contains(&cached_plugin.display().to_string()),
        "{message}"
    );
    assert!(message.contains("No cache root was admitted"), "{message}");
}

#[test]
fn bundled_resolver_uses_plugin_next_to_host_binary() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let host_binary = tempdir.path().join("host");
    let plugin_path = tempdir.path().join(asset.plugin_filename);
    let postgres_prefix = tempdir.path().join(POSTGRES_PREFIX_DIR);
    std::fs::write(&host_binary, b"host").expect("write host placeholder");
    std::fs::write(&plugin_path, b"not a real dynamic library").expect("write plugin placeholder");
    std::fs::create_dir_all(&postgres_prefix).expect("create bundled prefix");

    let resolved = BundledNativePluginResolver::new()
        .with_host_binary_path(&host_binary)
        .resolve()
        .expect("bundled plugin resolves");

    assert_eq!(resolved.path, plugin_path);
    assert_eq!(resolved.postgres_prefix, Some(postgres_prefix));
    assert_eq!(resolved.source, NativePluginSource::Bundled);
}

#[test]
fn bundled_resolver_uses_canonical_host_binary_for_symlinked_launchers() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let real_dir = tempdir.path().join("real");
    let shim_dir = tempdir.path().join("shim");
    std::fs::create_dir_all(&real_dir).expect("create real dir");
    std::fs::create_dir_all(&shim_dir).expect("create shim dir");
    let real_host_binary = real_dir.join("product-host");
    let shim_host_binary = shim_dir.join("product-host");
    let plugin_path = real_dir.join(asset.plugin_filename);
    let postgres_prefix = real_dir.join(POSTGRES_PREFIX_DIR);
    std::fs::write(&real_host_binary, b"host").expect("write host placeholder");
    std::fs::write(&plugin_path, b"not a real dynamic library").expect("write plugin placeholder");
    std::fs::create_dir_all(&postgres_prefix).expect("create bundled prefix");

    #[cfg(unix)]
    std::os::unix::fs::symlink(&real_host_binary, &shim_host_binary)
        .expect("create host binary symlink");
    #[cfg(windows)]
    std::os::windows::fs::symlink_file(&real_host_binary, &shim_host_binary)
        .expect("create host binary symlink");

    let resolved = BundledNativePluginResolver::new()
        .with_host_binary_path(&shim_host_binary)
        .resolve()
        .expect("bundled plugin resolves through canonical host binary");

    assert_eq!(
        resolved.path,
        std::fs::canonicalize(plugin_path).expect("canonical plugin path")
    );
    assert_eq!(
        resolved.postgres_prefix,
        Some(std::fs::canonicalize(postgres_prefix).expect("canonical postgres prefix"))
    );
    assert_eq!(resolved.source, NativePluginSource::Bundled);
}

#[test]
fn bundled_resolver_accepts_plugin_in_parent_of_cargo_deps_test_binary() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let deps_dir = tempdir.path().join("deps");
    std::fs::create_dir_all(&deps_dir).expect("create deps dir");
    let host_binary = deps_dir.join("product-host-test-binary");
    let plugin_path = tempdir.path().join(asset.plugin_filename);
    let postgres_prefix = tempdir.path().join(POSTGRES_PREFIX_DIR);
    std::fs::write(&host_binary, b"host").expect("write host placeholder");
    std::fs::write(&plugin_path, b"not a real dynamic library").expect("write plugin placeholder");
    std::fs::create_dir_all(&postgres_prefix).expect("create bundled prefix");

    let resolved = BundledNativePluginResolver::new()
        .with_host_binary_path(&host_binary)
        .resolve()
        .expect("bundled plugin resolves from parent of Cargo deps dir");

    assert_eq!(resolved.path, plugin_path);
    assert_eq!(resolved.postgres_prefix, Some(postgres_prefix));
    assert_eq!(resolved.source, NativePluginSource::Bundled);
}

#[test]
fn resolver_missing_plugin_error_is_actionable() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let error = NativePluginResolver::new()
        .with_cache_root(tempdir.path())
        .resolve()
        .expect_err("missing plugin should fail");
    let message = error.to_string();

    assert!(message.contains(LIBPGLITE_PLUGIN_PATH_ENV), "{message}");
    assert!(message.contains(LIBPGLITE_HOME_ENV), "{message}");
    assert!(message.contains(&asset.asset_name), "{message}");
    assert!(message.contains(&asset.archive_url()), "{message}");
    assert!(message.contains(&asset.target), "{message}");
}

#[test]
fn checksum_helpers_parse_and_verify_release_format() {
    let tempdir = tempfile::tempdir().expect("tempdir");
    let asset_path = tempdir.path().join("asset.txt");
    std::fs::write(&asset_path, b"hello").expect("write asset");
    let checksums = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824  asset.txt\n";

    assert_eq!(
        expected_checksum(checksums, "asset.txt").expect("checksum entry"),
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    );
    verify_file_checksum(checksums, "asset.txt", &asset_path).expect("checksum verifies");
}
