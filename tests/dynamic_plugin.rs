#![cfg(feature = "dynamic-loading")]

use libpglite::PgliteConfig;
use libpglite::dynamic::DynamicPgliteRuntime;

#[test]
fn dynamic_plugin_loads_and_reports_native_runtime_status() {
    let Some(plugin_path) = std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH") else {
        return;
    };

    let tempdir = tempfile::tempdir().expect("tempdir");
    let config = PgliteConfig::new("dynamic-plugin-test", tempdir.path().join("pgdata"));
    let error = DynamicPgliteRuntime::load(plugin_path, config)
        .expect_err("native runtime is intentionally not linked yet");
    let message = error.to_string();

    assert!(
        message.contains("native PGlite runtime is not linked yet"),
        "{message}"
    );
    assert!(
        message.contains("ADR-0002-NATIVE-PGLITE-BUILD-LANE"),
        "{message}"
    );
}
