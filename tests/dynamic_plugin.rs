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
fn dynamic_plugin_executes_queries_and_contrib_extensions_when_native_prefix_is_available() {
    let _guard = test_guard();
    let Some(mut runtime) = load_native_runtime("dynamic-plugin-runtime-test") else {
        return;
    };

    startup(&mut runtime);

    let query = query_message("select 1");
    let query_response = runtime
        .exec_protocol_raw(&query)
        .expect("simple query succeeds");
    assert_message_type(&query_response, b'T');
    assert_message_type(&query_response, b'D');
    assert_message_type(&query_response, b'C');
    assert_message_type(&query_response, b'Z');

    let citext_response = runtime
        .exec_protocol_raw(&query_message(
            "create extension citext; select 'pglite'::citext = 'PGLITE'::citext",
        ))
        .expect("citext extension query succeeds");
    assert_no_error_message(&citext_response);
    assert_message_type(&citext_response, b'D');
    assert_message_type(&citext_response, b'C');
    assert_message_type(&citext_response, b'Z');

    let pgcrypto_response = runtime
        .exec_protocol_raw(&query_message(
            "create extension pgcrypto; select encode(digest('pglite', 'sha256'), 'hex')",
        ))
        .expect("pgcrypto extension query succeeds");
    assert_no_error_message(&pgcrypto_response);
    assert_message_type(&pgcrypto_response, b'D');
    assert_message_type(&pgcrypto_response, b'C');
    assert_message_type(&pgcrypto_response, b'Z');

    runtime.shutdown().expect("runtime shuts down");
}

fn load_native_runtime(name: &str) -> Option<DynamicPgliteRuntime> {
    let plugin_path = std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH")?;
    let plugin_path = std::fs::canonicalize(plugin_path).expect("plugin path is absolute");
    let postgres_prefix = std::env::var_os("LIBPGLITE_TEST_POSTGRES_PREFIX")?;

    let tempdir = tempfile::tempdir().expect("tempdir");
    let config = PgliteConfig::new(name, tempdir.keep().join("pgdata")).with_environment([(
        "LIBPGLITE_POSTGRES_PREFIX",
        postgres_prefix.to_string_lossy().as_ref(),
    )]);
    Some(DynamicPgliteRuntime::load(plugin_path, config).expect("runtime opens"))
}

fn startup(runtime: &mut DynamicPgliteRuntime) {
    let startup = startup_message("postgres", "postgres");
    let startup_response = runtime
        .exec_protocol_raw(&startup)
        .expect("startup packet succeeds");
    assert_no_error_message(&startup_response);
    assert_message_type(&startup_response, b'Z');
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

fn assert_no_error_message(response: &[u8]) {
    let mut offset = 0;
    while offset + 5 <= response.len() {
        let tag = response[offset];
        let len = u32::from_be_bytes(
            response[offset + 1..offset + 5]
                .try_into()
                .expect("message length"),
        ) as usize;
        assert!(len >= 4, "invalid protocol message length {len}");
        assert_ne!(tag, b'E', "response contained error in {:?}", response);
        offset += 1 + len;
    }
}

fn test_guard() -> std::sync::MutexGuard<'static, ()> {
    static TEST_GUARD: OnceLock<Mutex<()>> = OnceLock::new();
    TEST_GUARD.get_or_init(|| Mutex::new(())).lock().unwrap()
}
