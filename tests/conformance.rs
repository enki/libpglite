use libpglite::PgliteConfig;
use libpglite::plugin_abi::{
    LIBPGLITE_PLUGIN_ABI_VERSION, LIBPGLITE_PLUGIN_STATUS_ERROR, LIBPGLITE_PLUGIN_STATUS_OK,
    LibpglitePluginBuffer, LibpglitePluginStatus,
};

#[test]
fn config_defaults_match_postgres_convention() {
    let config = PgliteConfig::new("test-host", "./pgdata");

    assert_eq!(config.host_id, "test-host");
    assert_eq!(config.user, "postgres");
    assert_eq!(config.database, "postgres");
    assert_eq!(config.data_dir.to_string_lossy(), "./pgdata");
    config.validate().expect("default config is valid");
}

#[test]
fn config_rejects_empty_authority_fields() {
    let mut config = PgliteConfig::new(" ", "./pgdata");
    assert!(config.validate().is_err());

    config.host_id = "test-host".to_string();
    config.user.clear();
    assert!(config.validate().is_err());

    config.user = "postgres".to_string();
    config.database.clear();
    assert!(config.validate().is_err());
}

#[test]
fn plugin_abi_status_layout_is_stable() {
    assert_eq!(LIBPGLITE_PLUGIN_ABI_VERSION, 1);
    assert_eq!(LIBPGLITE_PLUGIN_STATUS_OK, 0);
    assert_eq!(LIBPGLITE_PLUGIN_STATUS_ERROR, 1);
    assert_eq!(
        std::mem::align_of::<LibpglitePluginStatus>(),
        std::mem::align_of::<usize>()
    );
    assert!(
        std::mem::size_of::<LibpglitePluginStatus>()
            >= std::mem::size_of::<u32>() + std::mem::size_of::<LibpglitePluginBuffer>()
    );

    let ok = LibpglitePluginStatus::ok(LibpglitePluginBuffer::empty());
    assert_eq!(ok.code, LIBPGLITE_PLUGIN_STATUS_OK);
    assert!(ok.payload.data.is_null());
    assert_eq!(ok.payload.len, 0);
}
