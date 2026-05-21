use libpglite::release::{
    BundledNativePluginResolver, LIBPGLITE_PLUGIN_PATH_ENV, NativePluginResolver,
    NativePluginSource, RELEASE_TAG, current_native_plugin_asset, expected_checksum,
    verify_file_checksum,
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
    assert_eq!(resolved.source, NativePluginSource::Environment);
}

#[test]
fn resolver_uses_standard_release_cache() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let cached_plugin = asset.cached_plugin_path(tempdir.path());
    std::fs::create_dir_all(cached_plugin.parent().expect("plugin parent")).expect("cache dir");
    std::fs::write(&cached_plugin, b"not a real dynamic library").expect("write plugin");

    let resolved = NativePluginResolver::new()
        .with_cache_root(tempdir.path())
        .resolve()
        .expect("cached plugin resolves");

    assert_eq!(resolved.path, cached_plugin);
    assert_eq!(resolved.source, NativePluginSource::Cache);
}

#[test]
fn bundled_resolver_uses_plugin_next_to_host_binary() {
    let asset = current_native_plugin_asset().expect("current target is supported");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let host_binary = tempdir.path().join("host");
    let plugin_path = tempdir.path().join(asset.plugin_filename);
    std::fs::write(&host_binary, b"host").expect("write host placeholder");
    std::fs::write(&plugin_path, b"not a real dynamic library").expect("write plugin placeholder");

    let resolved = BundledNativePluginResolver::new()
        .with_host_binary_path(&host_binary)
        .resolve()
        .expect("bundled plugin resolves");

    assert_eq!(resolved.path, plugin_path);
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
