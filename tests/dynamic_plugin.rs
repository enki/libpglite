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

    let transaction_response = runtime
        .exec_protocol_raw(&query_message(
            "begin; create table tx_smoke(value int); insert into tx_smoke values (7); rollback; select to_regclass('tx_smoke') is null",
        ))
        .expect("transaction query succeeds");
    assert_no_error_message(&transaction_response);
    assert_message_type(&transaction_response, b'D');
    assert_message_type(&transaction_response, b'C');
    assert_message_type(&transaction_response, b'Z');

    let error_response = runtime
        .exec_protocol_raw(&query_message("select missing_column from missing_table"))
        .expect("protocol error is returned as backend response");
    assert_message_type(&error_response, b'E');
    assert_message_type(&error_response, b'Z');

    let recovery_response = runtime
        .exec_protocol_raw(&query_message("select 42"))
        .expect("query after protocol error succeeds");
    assert_no_error_message(&recovery_response);
    assert_message_type(&recovery_response, b'D');
    assert_message_type(&recovery_response, b'Z');

    let extended_response = runtime
        .exec_protocol_raw(&extended_query_message("select 5::int4"))
        .expect("extended query succeeds");
    assert_no_error_message(&extended_response);
    assert_message_type(&extended_response, b'1');
    assert_message_type(&extended_response, b'2');
    assert_message_type(&extended_response, b'T');
    assert_message_type(&extended_response, b'D');
    assert_message_type(&extended_response, b'C');
    assert_message_type(&extended_response, b'Z');

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
    drop(runtime);

    let Some(restart_error) = load_native_runtime_result("dynamic-plugin-restart-test")
        .map(|result| result.expect_err("second native runtime open fails actionably"))
    else {
        return;
    };
    let restart_message = restart_error.to_string();
    assert!(
        restart_message.contains("only one backend startup per process"),
        "{restart_message}"
    );
}

#[test]
fn dynamic_plugin_rejects_nonempty_uninitialized_data_dir_before_backend_start() {
    let _guard = test_guard();
    let Some(plugin_path) = std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH") else {
        return;
    };
    let Some(postgres_prefix) = std::env::var_os("LIBPGLITE_TEST_POSTGRES_PREFIX") else {
        return;
    };
    let plugin_path = std::fs::canonicalize(plugin_path).expect("plugin path is absolute");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let data_dir = tempdir.path().join("pgdata");
    std::fs::create_dir(&data_dir).expect("create invalid data dir");
    std::fs::write(data_dir.join("not-a-cluster"), b"not postgres").expect("write marker");

    let error = load_native_runtime_result_with_data_dir(
        "dynamic-plugin-invalid-data-dir-test",
        data_dir,
        plugin_path,
        postgres_prefix,
    )
    .expect_err("nonempty uninitialized data dir is rejected");
    let message = error.to_string();
    assert!(
        message.contains("exists but is not an initialized PostgreSQL cluster"),
        "{message}"
    );
}

#[cfg(feature = "client-tokio-postgres")]
#[test]
fn dynamic_plugin_tokio_postgres_client_child() {
    if std::env::var_os("LIBPGLITE_RUN_TOKIO_POSTGRES_CHILD").is_none() {
        return;
    }

    let _guard = test_guard();
    let Some(runtime) = load_native_runtime("dynamic-plugin-tokio-postgres-test") else {
        return;
    };

    let tokio = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    let local = tokio::task::LocalSet::new();
    local.block_on(&tokio, async move {
        let mut config = tokio_postgres::Config::new();
        config.user("postgres");
        config.dbname("postgres");

        let (mut client, connection) = libpglite::postgres_client::connect(runtime, &config)
            .await
            .expect("tokio-postgres connects through libpglite transport");
        let connection = tokio::task::spawn_local(async move {
            connection.await.expect("tokio-postgres connection runs");
        });

        let row = client
            .query_one(
                "select $1::int4 + 1 as answer, upper($2::text) as label",
                &[&41i32, &"pglite"],
            )
            .await
            .expect("parameterized query succeeds");
        let answer: i32 = row.get("answer");
        let label: String = row.get("label");
        assert_eq!(answer, 42);
        assert_eq!(label, "PGLITE");

        client
            .batch_execute("create extension citext")
            .await
            .expect("citext extension loads through tokio-postgres");

        let transaction = client.transaction().await.expect("transaction starts");
        transaction
            .execute("create table tokio_pg_smoke(value int)", &[])
            .await
            .expect("table is created in transaction");
        transaction
            .execute("insert into tokio_pg_smoke values ($1)", &[&7i32])
            .await
            .expect("parameterized insert succeeds");
        transaction
            .rollback()
            .await
            .expect("transaction rolls back");

        let row = client
            .query_one("select to_regclass('tokio_pg_smoke') is null", &[])
            .await
            .expect("query after rollback succeeds");
        let rolled_back: bool = row.get(0);
        assert!(rolled_back);

        drop(client);
        connection.await.expect("connection task joins");
    });
}

#[test]
fn dynamic_plugin_prefix_initialize_child() {
    if std::env::var_os("LIBPGLITE_RUN_PREFIX_INITIALIZE_CHILD").is_none() {
        return;
    }

    let _guard = test_guard();
    let Some(data_dir) = std::env::var_os("LIBPGLITE_TEST_DATA_DIR") else {
        panic!("LIBPGLITE_TEST_DATA_DIR is required");
    };
    let data_dir = std::path::PathBuf::from(data_dir);
    assert!(
        !data_dir.join("PG_VERSION").exists(),
        "prefix initialize check must start with a missing cluster"
    );
    let Some(mut runtime) = load_native_runtime_with_data_dir(
        "dynamic-plugin-prefix-initialize-test",
        data_dir.clone(),
    ) else {
        return;
    };

    startup(&mut runtime);
    let response = runtime
        .exec_protocol_raw(&query_message(
            "create table prefix_resume_marker(value int); insert into prefix_resume_marker values (17)",
        ))
        .expect("prefix initialize write succeeds");
    assert_no_error_message(&response);
    assert_message_type(&response, b'C');
    runtime.shutdown().expect("runtime shuts down");
    assert!(
        data_dir.join("PG_VERSION").is_file(),
        "runtime should initialize missing data directory"
    );
}

#[test]
fn dynamic_plugin_prefix_resume_child() {
    if std::env::var_os("LIBPGLITE_RUN_PREFIX_RESUME_CHILD").is_none() {
        return;
    }

    let _guard = test_guard();
    let Some(data_dir) = std::env::var_os("LIBPGLITE_TEST_DATA_DIR") else {
        panic!("LIBPGLITE_TEST_DATA_DIR is required");
    };
    let data_dir = std::path::PathBuf::from(data_dir);
    assert!(
        data_dir.join("PG_VERSION").is_file(),
        "prefix resume check requires an initialized cluster"
    );
    let Some(mut runtime) =
        load_native_runtime_with_data_dir("dynamic-plugin-prefix-resume-test", data_dir)
    else {
        return;
    };

    startup(&mut runtime);
    let response = runtime
        .exec_protocol_raw(&query_message("select value from prefix_resume_marker"))
        .expect("prefix resume read succeeds");
    assert_no_error_message(&response);
    assert_message_type(&response, b'D');
    assert_message_type(&response, b'Z');
    runtime.shutdown().expect("runtime shuts down");
}

fn load_native_runtime(name: &str) -> Option<DynamicPgliteRuntime> {
    Some(load_native_runtime_result(name)?.expect("runtime opens"))
}

fn load_native_runtime_result(
    name: &str,
) -> Option<Result<DynamicPgliteRuntime, libpglite::PgliteError>> {
    let plugin_path = std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH")?;
    let plugin_path = std::fs::canonicalize(plugin_path).expect("plugin path is absolute");
    let postgres_prefix = std::env::var_os("LIBPGLITE_TEST_POSTGRES_PREFIX")?;

    let tempdir = tempfile::tempdir().expect("tempdir");
    Some(load_native_runtime_result_with_data_dir(
        name,
        tempdir.keep().join("pgdata"),
        plugin_path,
        postgres_prefix,
    ))
}

fn load_native_runtime_with_data_dir(
    name: &str,
    data_dir: std::path::PathBuf,
) -> Option<DynamicPgliteRuntime> {
    Some(
        load_native_runtime_result_with_data_dir(
            name,
            data_dir,
            std::fs::canonicalize(std::env::var_os("LIBPGLITE_TEST_PLUGIN_PATH")?)
                .expect("plugin path is absolute"),
            std::env::var_os("LIBPGLITE_TEST_POSTGRES_PREFIX")?,
        )
        .expect("runtime opens"),
    )
}

fn load_native_runtime_result_with_data_dir(
    name: &str,
    data_dir: std::path::PathBuf,
    plugin_path: std::path::PathBuf,
    postgres_prefix: std::ffi::OsString,
) -> Result<DynamicPgliteRuntime, libpglite::PgliteError> {
    let config = PgliteConfig::new(name, data_dir).with_environment([(
        "LIBPGLITE_POSTGRES_PREFIX",
        postgres_prefix.to_string_lossy().as_ref(),
    )]);
    DynamicPgliteRuntime::load(plugin_path, config)
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

fn extended_query_message(sql: &str) -> Vec<u8> {
    let mut message = Vec::new();

    let mut parse = Vec::new();
    push_cstr(&mut parse, "");
    push_cstr(&mut parse, sql);
    parse.extend_from_slice(&0u16.to_be_bytes());
    push_tagged_message(&mut message, b'P', &parse);

    let mut bind = Vec::new();
    push_cstr(&mut bind, "");
    push_cstr(&mut bind, "");
    bind.extend_from_slice(&0u16.to_be_bytes());
    bind.extend_from_slice(&0u16.to_be_bytes());
    bind.extend_from_slice(&0u16.to_be_bytes());
    push_tagged_message(&mut message, b'B', &bind);

    let mut describe = Vec::new();
    describe.push(b'P');
    push_cstr(&mut describe, "");
    push_tagged_message(&mut message, b'D', &describe);

    let mut execute = Vec::new();
    push_cstr(&mut execute, "");
    execute.extend_from_slice(&0u32.to_be_bytes());
    push_tagged_message(&mut message, b'E', &execute);

    push_tagged_message(&mut message, b'S', &[]);
    message
}

fn push_tagged_message(buffer: &mut Vec<u8>, tag: u8, body: &[u8]) {
    buffer.push(tag);
    buffer.extend_from_slice(&((body.len() + 4) as u32).to_be_bytes());
    buffer.extend_from_slice(body);
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
