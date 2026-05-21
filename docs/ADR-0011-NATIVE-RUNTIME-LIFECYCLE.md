# ADR-0011: Native Runtime Lifecycle

Status: Open
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
between starts. We will either:

- implement a verified reset path that allows shutdown followed by another
  runtime startup in the same process, or
- make the host-facing contract intentionally single-start per process and prove
  that all expected application work can happen inside one long-lived runtime.

The preferred target is deterministic restart, because Rust library consumers
will naturally expect a runtime object to be droppable and creatable again in
tests, workers, and long-running hosts.

Until that reset path is proven, the implemented native contract is single
backend startup per process. A second startup attempt must fail before entering
PostgreSQL with an actionable Rust initialization error.

## Required Work

1. Inventory PostgreSQL and PGlite process-global state touched by native
   startup, protocol execution, extension loading, and shutdown.
2. Identify which state is safely reset by `pgl_run_atexit_funcs()` and which
   state survives across runtime drops.
3. Add a conformance test that opens a runtime, executes a query, shuts down,
   opens a second runtime in the same process, executes another query, and shuts
   down.
4. Either implement the reset path required by that test or make the
   single-start process contract explicit in the public API and release
   metadata.
5. Ensure extension loading does not leave process-global state that prevents
   later clean shutdown or restart.
6. Add preflight coverage so lifecycle regressions fail before release.

## Acceptance Criteria

- The documented lifecycle contract matches observed runtime behavior.
- If restart is supported, a same-process open/shutdown/open/shutdown test passes
  with a clean data directory and after loading at least one native extension.
- If restart is not supported, a second open fails with an actionable Rust error
  instead of entering PostgreSQL or aborting the process.
- Dropping a runtime after successful startup runs required PostgreSQL/PGlite
  shutdown hooks exactly once.
- A failed startup does not poison the process for the documented lifecycle
  contract.

## Implementation Notes

- The current test suite keeps macOS extension smoke coverage inside one native
  runtime because same-process sequential startup is not yet proven safe.
- The existing mutex guard prevents concurrency only. It is not sufficient
  evidence for sequential lifecycle safety.
- This ADR blocks moving the runtime-ready release gate to done even though the
  macOS single-runtime extension smoke now passes.
- The native implementation now records whether a backend startup has been
  attempted in the process. After the first startup, later opens fail before
  calling into PostgreSQL. This is an explicit temporary lifecycle contract, not
  the desired final restart behavior.
- The dynamic plugin test now verifies that startup, simple query, `citext`,
  `pgcrypto`, and shutdown all work in one runtime, then verifies that a second
  open returns an actionable Rust error instead of aborting the process.
