# ADR-0014: Native Backend Stdio Ledger

Status: Done

## Context

The native runtime embeds PostgreSQL in-process. PostgreSQL protocol I/O is
already routed through the PGlite transport callbacks, but backend startup,
shutdown, and diagnostic paths can still write directly to process stdout or
stderr. Downstream hosts such as test runners cannot classify that output after
it has already reached the terminal.

## Decision

Native backend stdio is not process terminal authority. It is backend-owned
diagnostic output and must be captured by libpglite at the native backend
boundary.

The positive shape is:

```text
NativeBackendCall
  -> NativeBackendStdioLease
  -> PgliteBackendOutputLedger
  -> host diagnostic projection
```

The dynamic plugin ABI must expose a backend-output drain operation. Hosts may
decide how to project the ledger, but they must not have to intercept process
stdio to recover backend diagnostics.

## Hard Rules

- Embedded PostgreSQL may not write directly to the caller's stdout/stderr in
  normal libpglite runtime operations.
- The stdio lease is scoped to the exact native backend call phase.
- Captured records carry stream and phase.
- Captured output is drained from libpglite through an explicit runtime method.
- Dynamic plugin hosts get the same ledger through plugin ABI, not by scraping
  process output.
- If stdio capture cannot be installed or restored, the runtime operation fails
  with a structured libpglite error.

## Required Work

1. Add `PgliteBackendOutputLedger` and records to the public Rust facade.
2. Add a `PgliteRuntime::take_backend_output` boundary.
3. Capture native stdout/stderr around backend startup, protocol execution, and
   shutdown.
4. Add a dynamic plugin ABI symbol that drains the ledger.
5. Verify native plugin users no longer leak backend stdio to the terminal while
   still preserving diagnostics.

## Acceptance Criteria

- Native runtime startup/shutdown backend messages are captured into the ledger.
- Dynamic runtime users can drain the same records through the plugin.
- No normal native runtime operation leaks backend stdout/stderr to process
  stdout/stderr.
- Old plugins without the ledger ABI fail fast as incompatible.

## Closing Evidence

- `PgliteBackendOutputLedger`, `PgliteRuntime::take_backend_output`, and the
  dynamic plugin `libpglite_plugin_runtime_take_backend_output` ABI are in the
  release path.
- The native runtime captures stdout/stderr around backend startup, protocol
  execution, and shutdown through `NativeBackendStdioLease`; records carry stream
  and phase.
- `dynamic_plugin_rejects_abi_mismatch_before_runtime_create` proves old plugins
  fail fast at ABI admission.
- `dynamic_plugin_executes_queries_and_contrib_extensions_when_native_prefix_is_available`
  now drains startup output, proves the drain is affine, and drains shutdown
  output through the dynamic runtime.
- `PATH=/opt/homebrew/bin:$PATH cargo test --all --quiet` passes.
- Focused native proof passed with the rebuilt release plugin:
  `LIBPGLITE_TEST_PLUGIN_PATH=/Users/paul/Documents/GitHub/libpglite/target/release/liblibpglite_plugin_native.dylib
  LIBPGLITE_TEST_POSTGRES_PREFIX=/Users/paul/Documents/GitHub/libpglite/target/native-pglite/aarch64-apple-darwin/postgres-build/install
  cargo test --features dynamic-loading --test dynamic_plugin
  dynamic_plugin_executes_queries_and_contrib_extensions_when_native_prefix_is_available
  -- --nocapture`.
- `scripts/preflight-native-plugin-release.sh v0.1.0` completed after rebuilding
  the ABI-2 package and running package doctor.
- Downstream product-host execution passes without raw PostgreSQL backend lines
  reaching the terminal.
