#![cfg(feature = "dynamic-loading")]

use libpglite::PgliteConfig;
use libpglite::PgliteRuntime;
use libpglite::dynamic::DynamicPgliteRuntime;
use std::sync::Mutex;
use std::sync::OnceLock;

#[test]
fn dynamic_plugin_rejects_abi_mismatch_before_runtime_create() {
    let _guard = test_guard();
    let Some(plugin_path) = build_fake_plugin_with_abi_version(999) else {
        return;
    };
    let tempdir = tempfile::tempdir().expect("tempdir");
    let config = PgliteConfig::new(
        "dynamic-plugin-abi-mismatch-test",
        tempdir.path().join("pgdata"),
    );

    let error = DynamicPgliteRuntime::load(plugin_path, config)
        .expect_err("ABI mismatch fails before runtime creation");
    let message = error.to_string();
    assert!(
        message.contains("dynamic plugin ABI version 999 is incompatible"),
        "{message}"
    );
}

#[test]
fn dynamic_plugin_frees_plugin_owned_status_buffers() {
    let _guard = test_guard();
    let tempdir = tempfile::tempdir().expect("tempdir");
    let marker_path = tempdir.path().join("buffer-free-marker");
    let Some(plugin_path) = build_fake_plugin_with_buffer_free_marker(&marker_path) else {
        return;
    };
    let config = PgliteConfig::new(
        "dynamic-plugin-buffer-free-test",
        tempdir.path().join("pgdata"),
    );
    let mut runtime = DynamicPgliteRuntime::load(plugin_path, config).expect("fake runtime opens");
    assert_marker_count(&marker_path, 1);

    let response = runtime
        .exec_protocol_raw(&[])
        .expect("fake runtime returns protocol bytes");
    assert_eq!(response, vec![84]);
    assert_marker_count(&marker_path, 2);

    runtime.shutdown().expect("fake runtime shuts down");
    assert_marker_count(&marker_path, 3);
}

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

    let empty_query_response = runtime
        .exec_protocol_raw(&query_message(""))
        .expect("empty simple query succeeds");
    assert_no_error_message(&empty_query_response);
    assert_message_type(&empty_query_response, b'I');
    assert_message_type(&empty_query_response, b'Z');

    let transaction_response = runtime
        .exec_protocol_raw(&query_message(
            "begin; create table tx_smoke(value int); insert into tx_smoke values (7); rollback; select to_regclass('tx_smoke') is null",
        ))
        .expect("transaction rollback query succeeds");
    assert_no_error_message(&transaction_response);
    assert_message_type(&transaction_response, b'D');
    assert_message_type(&transaction_response, b'C');
    assert_message_type(&transaction_response, b'Z');

    let transaction_commit_response = runtime
        .exec_protocol_raw(&query_message(
            "begin; create table tx_commit_smoke(value int); insert into tx_commit_smoke values (9); commit; select value from tx_commit_smoke",
        ))
        .expect("transaction commit query succeeds");
    assert_no_error_message(&transaction_commit_response);
    assert_message_type(&transaction_commit_response, b'D');
    assert_message_type(&transaction_commit_response, b'C');
    assert_message_type(&transaction_commit_response, b'Z');

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

    let extended_param_response = runtime
        .exec_protocol_raw(&extended_query_message_with_text_param(
            "select $1::int4 + 1",
            "6",
        ))
        .expect("parameterized extended query succeeds");
    assert_no_error_message(&extended_param_response);
    assert_message_type(&extended_param_response, b'1');
    assert_message_type(&extended_param_response, b'2');
    assert_message_type(&extended_param_response, b'T');
    assert_message_type(&extended_param_response, b'D');
    assert_message_type(&extended_param_response, b'C');
    assert_message_type(&extended_param_response, b'Z');

    let named_statement_response = runtime
        .exec_protocol_raw(&named_prepared_statement_reuse_message())
        .expect("named prepared statement reuse succeeds");
    assert_no_error_message(&named_statement_response);
    assert_message_type(&named_statement_response, b'1');
    assert_message_type_count(&named_statement_response, b'2', 2);
    assert_message_type(&named_statement_response, b'3');
    assert_message_type(&named_statement_response, b'T');
    assert_message_type_count(&named_statement_response, b'D', 2);
    assert_message_type(&named_statement_response, b'C');
    assert_message_type(&named_statement_response, b'Z');

    assert_query_ok(
        &mut runtime,
        "citext",
        "create extension citext; select 'pglite'::citext = 'PGLITE'::citext",
    );
    assert_query_ok(
        &mut runtime,
        "pgcrypto",
        "create extension pgcrypto; select encode(digest('pglite', 'sha256'), 'hex')",
    );
    assert_pglite_other_extensions(&mut runtime);

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
fn dynamic_plugin_uses_bundled_postgres_prefix_from_plugin_dir() {
    if std::env::var_os("LIBPGLITE_RUN_BUNDLED_PREFIX_CHILD").is_none() {
        return;
    }

    let _guard = test_guard();
    let Some(plugin_dir) = std::env::var_os("LIBPGLITE_TEST_PLUGIN_DIR") else {
        panic!("LIBPGLITE_TEST_PLUGIN_DIR is required");
    };
    assert!(
        std::env::var_os("LIBPGLITE_TEST_POSTGRES_PREFIX").is_none(),
        "bundled prefix test must not receive an explicit test prefix"
    );
    let tempdir = tempfile::tempdir().expect("tempdir");
    let config = PgliteConfig::new(
        "dynamic-plugin-bundled-prefix-test",
        tempdir.path().join("pgdata"),
    );
    let mut runtime = DynamicPgliteRuntime::initialize_with_plugin_dir(config, plugin_dir)
        .expect("runtime opens through bundled plugin resolver");

    startup(&mut runtime);
    let response = runtime
        .exec_protocol_raw(&query_message("select 1"))
        .expect("simple query succeeds through bundled prefix");
    assert_no_error_message(&response);
    assert_message_type(&response, b'D');
    assert_message_type(&response, b'Z');
    runtime.shutdown().expect("runtime shuts down");
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

fn build_fake_plugin_with_abi_version(version: u32) -> Option<std::path::PathBuf> {
    build_fake_plugin(&format!(
        "#include <stdint.h>\nuint32_t libpglite_plugin_abi_version(void) {{ return {version}; }}\n"
    ))
}

fn build_fake_plugin_with_buffer_free_marker(
    marker_path: &std::path::Path,
) -> Option<std::path::PathBuf> {
    let marker = c_string_literal(marker_path.to_string_lossy().as_ref());
    build_fake_plugin(&format!(
        r#"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {{
    uint8_t *data;
    uintptr_t len;
}} LibpglitePluginBuffer;

typedef struct {{
    uint32_t code;
    LibpglitePluginBuffer payload;
}} LibpglitePluginStatus;

static LibpglitePluginStatus json_status(const char *json) {{
    uintptr_t len = strlen(json);
    uint8_t *data = (uint8_t *)malloc(len);
    memcpy(data, json, len);
    LibpglitePluginStatus status = {{0, {{data, len}}}};
    return status;
}}

uint32_t libpglite_plugin_abi_version(void) {{
    return 1;
}}

void libpglite_plugin_buffer_free(LibpglitePluginBuffer buffer) {{
    FILE *marker = fopen({marker}, "a");
    if (marker != NULL) {{
        fputs("free\n", marker);
        fclose(marker);
    }}
    free(buffer.data);
}}

LibpglitePluginStatus libpglite_plugin_runtime_create(const uint8_t *config, uintptr_t config_len, void **runtime) {{
    (void)config;
    (void)config_len;
    *runtime = malloc(1);
    return json_status("{{}}");
}}

void libpglite_plugin_runtime_destroy(void *runtime) {{
    free(runtime);
}}

LibpglitePluginStatus libpglite_plugin_runtime_exec_protocol_raw(void *runtime, const uint8_t *input, uintptr_t input_len) {{
    (void)runtime;
    (void)input;
    (void)input_len;
    return json_status("[84]");
}}

LibpglitePluginStatus libpglite_plugin_runtime_shutdown(void *runtime) {{
    (void)runtime;
    return json_status("{{}}");
}}
"#
    ))
}

fn build_fake_plugin(source: &str) -> Option<std::path::PathBuf> {
    let compiler = std::env::var_os("CC").unwrap_or_else(|| "cc".into());
    let tempdir = tempfile::tempdir().expect("tempdir");
    let source_path = tempdir.path().join("fake_plugin.c");
    let extension = if cfg!(target_os = "macos") {
        "dylib"
    } else {
        "so"
    };
    let output = tempdir
        .path()
        .join(format!("libfake_pglite_plugin.{extension}"));
    std::fs::write(&source_path, source).expect("write fake plugin source");

    let mut command = std::process::Command::new(compiler);
    if cfg!(target_os = "macos") {
        command.arg("-dynamiclib");
    } else {
        command.arg("-shared").arg("-fPIC");
    }
    let status = command
        .arg(&source_path)
        .arg("-o")
        .arg(&output)
        .status()
        .expect("run C compiler for fake plugin");
    if !status.success() {
        return None;
    }

    Some(
        tempdir
            .keep()
            .join(output.file_name().expect("fake plugin filename")),
    )
}

fn c_string_literal(value: &str) -> String {
    let mut escaped = String::from("\"");
    for ch in value.chars() {
        match ch {
            '\\' => escaped.push_str("\\\\"),
            '"' => escaped.push_str("\\\""),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            other => escaped.push(other),
        }
    }
    escaped.push('"');
    escaped
}

fn assert_marker_count(marker_path: &std::path::Path, expected: usize) {
    let marker = std::fs::read_to_string(marker_path).unwrap_or_default();
    let actual = marker.lines().filter(|line| *line == "free").count();
    assert_eq!(actual, expected, "{marker}");
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

fn extended_query_message_with_text_param(sql: &str, value: &str) -> Vec<u8> {
    let mut message = Vec::new();

    let mut parse = Vec::new();
    push_cstr(&mut parse, "");
    push_cstr(&mut parse, sql);
    parse.extend_from_slice(&0u16.to_be_bytes());
    push_tagged_message(&mut message, b'P', &parse);

    let mut bind = Vec::new();
    push_cstr(&mut bind, "");
    push_cstr(&mut bind, "");
    bind.extend_from_slice(&1u16.to_be_bytes());
    bind.extend_from_slice(&0u16.to_be_bytes());
    bind.extend_from_slice(&1u16.to_be_bytes());
    bind.extend_from_slice(&(value.len() as u32).to_be_bytes());
    bind.extend_from_slice(value.as_bytes());
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

fn named_prepared_statement_reuse_message() -> Vec<u8> {
    let mut message = Vec::new();

    let mut parse = Vec::new();
    push_cstr(&mut parse, "pglite_stmt");
    push_cstr(&mut parse, "select $1::int4 * 2");
    parse.extend_from_slice(&0u16.to_be_bytes());
    push_tagged_message(&mut message, b'P', &parse);

    push_text_param_bind_describe_execute(&mut message, "pglite_stmt", "11");
    push_text_param_bind_describe_execute(&mut message, "pglite_stmt", "13");

    let mut close = Vec::new();
    close.push(b'S');
    push_cstr(&mut close, "pglite_stmt");
    push_tagged_message(&mut message, b'C', &close);

    push_tagged_message(&mut message, b'S', &[]);
    message
}

fn push_text_param_bind_describe_execute(message: &mut Vec<u8>, statement: &str, value: &str) {
    let mut bind = Vec::new();
    push_cstr(&mut bind, "");
    push_cstr(&mut bind, statement);
    bind.extend_from_slice(&1u16.to_be_bytes());
    bind.extend_from_slice(&0u16.to_be_bytes());
    bind.extend_from_slice(&1u16.to_be_bytes());
    bind.extend_from_slice(&(value.len() as u32).to_be_bytes());
    bind.extend_from_slice(value.as_bytes());
    bind.extend_from_slice(&0u16.to_be_bytes());
    push_tagged_message(message, b'B', &bind);

    let mut describe = Vec::new();
    describe.push(b'P');
    push_cstr(&mut describe, "");
    push_tagged_message(message, b'D', &describe);

    let mut execute = Vec::new();
    push_cstr(&mut execute, "");
    execute.extend_from_slice(&0u32.to_be_bytes());
    push_tagged_message(message, b'E', &execute);
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

fn assert_message_type_count(response: &[u8], expected: u8, expected_count: usize) {
    let mut offset = 0;
    let mut actual_count = 0;
    while offset + 5 <= response.len() {
        let tag = response[offset];
        let len = u32::from_be_bytes(
            response[offset + 1..offset + 5]
                .try_into()
                .expect("message length"),
        ) as usize;
        assert!(len >= 4, "invalid protocol message length {len}");
        if tag == expected {
            actual_count += 1;
        }
        offset += 1 + len;
    }
    assert_eq!(
        actual_count, expected_count,
        "response contained message type {} {actual_count} times in {:?}",
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

fn assert_query_ok(runtime: &mut DynamicPgliteRuntime, label: &str, sql: &str) {
    let response = runtime
        .exec_protocol_raw(&query_message(sql))
        .unwrap_or_else(|err| panic!("{label} query failed to execute: {err}"));
    assert_no_error_message_for(label, &response);
    assert_message_type(&response, b'C');
    assert_message_type(&response, b'Z');
}

fn assert_pglite_other_extensions(runtime: &mut DynamicPgliteRuntime) {
    for (label, sql) in [
        ("age", "create extension age"),
        ("pg_hashids", "create extension pg_hashids"),
        ("pg_ivm", "create extension pg_ivm"),
        ("pg_textsearch", "create extension pg_textsearch"),
        ("pg_uuidv7", "create extension pg_uuidv7"),
        ("pgtap", "create extension pgtap"),
        (
            "postgis",
            "create extension postgis; select postgis_full_version()",
        ),
        (
            "vector",
            "create extension vector; select '[1,2,3]'::vector(3)",
        ),
    ] {
        assert_query_ok(runtime, label, sql);
    }
}

fn assert_no_error_message_for(label: &str, response: &[u8]) {
    if let Some(error) = protocol_error_message(response) {
        panic!("{label} response contained PostgreSQL error: {error}");
    }
}

fn protocol_error_message(response: &[u8]) -> Option<String> {
    let mut offset = 0;
    while offset + 5 <= response.len() {
        let tag = response[offset];
        let len = u32::from_be_bytes(
            response[offset + 1..offset + 5]
                .try_into()
                .expect("message length"),
        ) as usize;
        assert!(len >= 4, "invalid protocol message length {len}");
        if tag == b'E' {
            let end = offset + 1 + len;
            let fields = &response[offset + 5..end];
            let mut message = None;
            let mut cursor = 0;
            while cursor < fields.len() {
                let field = fields[cursor];
                cursor += 1;
                if field == 0 {
                    break;
                }
                let Some(relative_end) = fields[cursor..].iter().position(|byte| *byte == 0) else {
                    break;
                };
                let value = String::from_utf8_lossy(&fields[cursor..cursor + relative_end]);
                if field == b'M' {
                    message = Some(value.into_owned());
                }
                cursor += relative_end + 1;
            }
            return Some(message.unwrap_or_else(|| "unknown PostgreSQL error".to_string()));
        }
        offset += 1 + len;
    }
    None
}

fn test_guard() -> std::sync::MutexGuard<'static, ()> {
    static TEST_GUARD: OnceLock<Mutex<()>> = OnceLock::new();
    TEST_GUARD.get_or_init(|| Mutex::new(())).lock().unwrap()
}
