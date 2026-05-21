# ADR-0011: Native Runtime Lifecycle

Status: Done
Date: 2026-05-21

## Context

The native macOS dynamic smoke path can start a PGlite/Postgres backend, process
startup and query protocol messages, create bundled contrib extensions, and shut
down. However, PostgreSQL still owns extensive process-global state. A second
native backend startup in the same Rust test process can enter inconsistent
shutdown/startup state, including panics while closing inherited global file
state.

The current dynamic runtime guard prevents concurrent native runtimes, but it
does not prove that sequential runtime teardown and restart is safe. PGlite's
WASM environment effectively owns the full module instance. A native dynamic
library loaded into an arbitrary host process has a stricter lifecycle contract:
it must either support clean restart or explicitly model a one-backend-per-process
runtime boundary.

## Decision

`libpglite` will make native runtime lifecycle an explicit release contract.

The production runtime must not rely on accidental PostgreSQL global-state reuse
between starts.

The first production lifecycle contract is intentionally
single-start-per-process. All expected application work must happen inside one
long-lived runtime. A second startup attempt in the same process must fail
before entering PostgreSQL with an actionable Rust initialization error.

Deterministic same-process restart remains a desirable future widening of the
contract, but it is not required for the first runtime-ready release. Supporting
restart later requires a new ADR or a replacement lifecycle contract with its own
PostgreSQL global-state reset evidence.

## Required Work

1. Make the single-start process contract explicit in the runtime error path and
   release metadata.
2. Ensure a second startup attempt fails before entering PostgreSQL.
3. Prove startup, multiple SQL operations, extension loading, error recovery,
   and shutdown inside one runtime.
4. Ensure dropping a successful runtime runs PostgreSQL/PGlite shutdown hooks
   once.
5. Add preflight coverage so lifecycle regressions fail before release.

## Acceptance Criteria

- The documented lifecycle contract matches observed runtime behavior.
- The package declares the lifecycle contract as `single-start-per-process`.
- A second open fails with an actionable Rust error
  instead of entering PostgreSQL or aborting the process.
- Dropping a runtime after successful startup runs required PostgreSQL/PGlite
  shutdown hooks exactly once.
- A failed startup does not poison the process for the documented lifecycle
  contract.

## Implementation Notes

- The current test suite keeps macOS extension smoke coverage inside one native
  runtime because same-process sequential startup is outside the first release
  contract.
- The existing mutex guard prevents concurrency only. It is not sufficient
  evidence for sequential lifecycle safety.
- The native implementation now records whether a backend startup has been
  attempted in the process. After the first startup, later opens fail before
  calling into PostgreSQL.
- The dynamic plugin test now verifies that startup, simple query, `citext`,
  `pgcrypto`, transaction rollback, protocol error recovery, a basic
  extended-query flow, and shutdown all work in one runtime, then verifies that
  a second open returns an actionable Rust error instead of aborting the process.
- Native preflight runs raw protocol conformance and the `tokio-postgres`
  high-level client transport as separate process-level checks. This is
  intentional while the documented lifecycle remains single-start per process:
  each check gets a fresh backend lifetime and failures point at one runtime
  mode instead of a mixed global-state sequence.
- Native packages now carry `diagnostics/runtime-lifecycle.json`, which records
  the current single-start-per-process contract, lack of restart/concurrency
  support, second-start failure behavior, shutdown behavior, and the conformance
  result that proves it. The package doctor validates this manifest so release
  artifacts cannot silently drift from the implemented lifecycle contract.

## Closing Evidence

- `native/src/lib.rs` rejects a second backend startup in the process before
  entering PostgreSQL.
- `tests/dynamic_plugin.rs` verifies startup, query execution, contrib extension
  loading, transaction rollback, protocol error recovery, a basic extended-query
  flow, shutdown, and actionable second-start failure in one native runtime.
- `scripts/package-native-plugin-release.sh` writes
  `diagnostics/runtime-lifecycle.json` with the single-start contract.
- `scripts/doctor-native-plugin-package.py` validates that lifecycle diagnostic
  and requires raw-protocol conformance evidence.
- `scripts/preflight-native-plugin-release.sh 0.1.0` runs the lifecycle-bearing
  dynamic plugin tests and package doctor against the final native archive.
