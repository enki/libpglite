#![cfg(feature = "dynamic-loading")]

use libpglite::PgliteConfig;
use libpglite::PgliteRuntime;
use libpglite::dynamic::DynamicPgliteRuntime;
use std::sync::Mutex;
use std::sync::OnceLock;

#[test]
fn dynamic_plugin_loads_and_reports_native_runtime_status() {
    let _guard = test_guard();
    let Some(plugin_path) = std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH") else {
        return;
    };
    let plugin_path = std::fs::canonicalize(plugin_path).expect("plugin path is absolute");

    let tempdir = tempfile::tempdir().expect("tempdir");
    let config = PgliteConfig::new("dynamic-plugin-test", tempdir.path().join("pgdata"));
    let error = DynamicPgliteRuntime::load(plugin_path, config)
        .expect_err("native runtime is intentionally not linked yet");
    let message = error.to_string();

    assert!(
        message.contains("native PGlite runtime is not linked yet")
            || message.contains("native PGlite requires LIBPGLITE_POSTGRES_PREFIX"),
        "{message}"
    );
}

#[test]
fn dynamic_plugin_executes_startup_and_simple_query_when_native_prefix_is_available() {
    let _guard = test_guard();
    let Some(plugin_path) = std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH") else {
        return;
    };
    let plugin_path = std::fs::canonicalize(plugin_path).expect("plugin path is absolute");
    let Some(postgres_prefix) = std::env::var_os("LIBPGLITE_TEST_POSTGRES_PREFIX") else {
        return;
    };

    let tempdir = tempfile::tempdir().expect("tempdir");
    let config = PgliteConfig::new("dynamic-plugin-runtime-test", tempdir.path().join("pgdata"))
        .with_environment([(
            "LIBPGLITE_POSTGRES_PREFIX",
            postgres_prefix.to_string_lossy().as_ref(),
        )]);
    let mut runtime = DynamicPgliteRuntime::load(plugin_path, config).expect("runtime opens");

    let startup = startup_message("postgres", "postgres");
    let startup_response = runtime
        .exec_protocol_raw(&startup)
        .expect("startup packet succeeds");
    assert_message_type(&startup_response, b'R');
    assert_message_type(&startup_response, b'Z');

    let query = query_message("select 1");
    let query_response = runtime
        .exec_protocol_raw(&query)
        .expect("simple query succeeds");
    assert_message_type(&query_response, b'T');
    assert_message_type(&query_response, b'D');
    assert_message_type(&query_response, b'C');
    assert_message_type(&query_response, b'Z');

    runtime.shutdown().expect("runtime shuts down");
}

fn startup_message(user: &str, database: &str) -> Vec<u8> {
    let mut body = Vec::new();
    body.extend_from_slice(&3u16.to_be_bytes());
    body.extend_from_slice(&0u16.to_be_bytes());
    push_cstr(&mut body, "user");
    push_cstr(&mut body, user);
    push_cstr(&mut body, "database");
    push_cstr(&mut body, database);
    push_cstr(&mut body, "client_encoding");
    push_cstr(&mut body, "UTF8");
    body.push(0);

    let mut message = Vec::new();
    message.extend_from_slice(&((body.len() + 4) as u32).to_be_bytes());
    message.extend_from_slice(&body);
    message
}

fn query_message(sql: &str) -> Vec<u8> {
    let mut message = Vec::new();
    message.push(b'Q');
    message.extend_from_slice(&((sql.len() + 1 + 4) as u32).to_be_bytes());
    message.extend_from_slice(sql.as_bytes());
    message.push(0);
    message
}

fn push_cstr(buffer: &mut Vec<u8>, value: &str) {
    buffer.extend_from_slice(value.as_bytes());
    buffer.push(0);
}

fn assert_message_type(response: &[u8], expected: u8) {
    let mut offset = 0;
    while offset + 5 <= response.len() {
        let tag = response[offset];
        let len = u32::from_be_bytes(
            response[offset + 1..offset + 5]
                .try_into()
                .expect("message length"),
        ) as usize;
        assert!(len >= 4, "invalid protocol message length {len}");
        if tag == expected {
            return;
        }
        offset += 1 + len;
    }
    panic!(
        "response did not contain message type {} in {:?}",
        expected as char, response
    );
}

fn test_guard() -> std::sync::MutexGuard<'static, ()> {
    static TEST_GUARD: OnceLock<Mutex<()>> = OnceLock::new();
    TEST_GUARD.get_or_init(|| Mutex::new(())).lock().unwrap()
}
