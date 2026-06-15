# ADR-0015: Tokio Postgres Session Backend Output Drain

Status: Done

## Context

ADR-0014 captures native backend stdout/stderr into `PgliteBackendOutputLedger`
and exposes `PgliteRuntime::take_backend_output`. That closes terminal leakage at
the native runtime boundary.

The retained `tokio-postgres` integration hides the runtime inside
`PgliteProtocolStream` after connection setup. Once a host calls
`connect_with_driver(...)`, downstream code receives a `tokio_postgres::Client`
and a driver guard. Without this ADR, it did not receive authority to drain
backend output produced during startup, protocol execution, or shutdown without
retaining a separate runtime handle.

## Decision

The tokio-postgres session boundary carries a session-owned backend output
drain.

Positive shape:

```text
PgliteRuntime
  -> PgliteProtocolStream
  -> PgliteTokioPostgresSession
  -> PgliteSessionBackendOutputDrain
```

`PgliteProtocolStream` is the only code that may drain runtime backend output
after protocol execution begins. It appends records to a session-owned drain.
`PgliteTokioPostgresSession` carries that drain as first-class authority and
returns it when the session is split into client/driver parts.

Product hosts own the next projection step. Libpglite provides the affine
session-output authority; downstream systems decide how drained records become
provider, invocation, test, or reporter diagnostics.

## Hard Rules

- Hosts must not recover backend output by holding the runtime separately after
  `tokio-postgres` connection setup.
- `PgliteProtocolStream` drains runtime output after startup admission, every
  protocol flush, shutdown, and drop-shutdown.
- The session output drain is affine: draining moves records out and a second
  drain observes only later records.
- `connect_with_driver(...)` must return the backend output drain with the owned
  session parts.
- The drain is diagnostic/output authority only; it is not SQL execution,
  connection-driver, or runtime-lifetime authority.
- Downstream provider/test projection is outside this libpglite ADR. Swarm owns
  that product concern under
  `../swarm/docs/ADR-2090-NATIVE-PROVIDER-STDIO-CAPTURE-AND-NO-HOST-LOG-LEAKAGE.md`.

## Required Work

1. Add `PgliteSessionBackendOutputDrain` or equivalent session-owned carrier.
2. Change `PgliteProtocolStream::new(...)` to receive the drain writer and drain
   startup output immediately after taking the runtime.
3. Drain records after `exec_protocol_raw(...)`, `shutdown(...)`, and drop
   shutdown.
4. Change `PgliteTokioPostgresSession::into_parts(...)` to return the drain.
5. Make downstream handoff explicit: libpglite owns the retained-session drain
   authority, and product hosts own projection into provider/test diagnostics.

## Acceptance Criteria

- A focused libpglite test proves startup, protocol, and shutdown records are
  observable through the session drain after `connect_with_driver(...)`.
- Existing direct runtime `take_backend_output` behavior from ADR-0014 remains
  affine and unchanged for non-tokio users.
- Downstream hosts can carry native-libpglite backend output past
  `tokio-postgres` connection setup without scraping process stdout/stderr or
  retaining a separate runtime handle.

## Closing Evidence

- `PgliteSessionBackendOutputDrain` and the internal
  `PgliteSessionBackendOutputWriter` are implemented in the libpglite
  `tokio-postgres` transport.
- `connect(...)` returns `(Client, Connection, PgliteSessionBackendOutputDrain)`,
  and `connect_with_driver(...)` stores that drain in
  `PgliteTokioPostgresSession`.
- `PgliteTokioPostgresSession::into_parts(...)` returns `(client,
  driver_guard, backend_output)`, so downstream retained-session wrappers cannot
  accidentally lose diagnostic authority at admission time.
- `PgliteProtocolStream` drains runtime backend output at stream birth, after
  every protocol flush, after protocol errors, after shutdown, and during
  drop-shutdown.
- `postgres_client::tests::protocol_stream_moves_startup_protocol_and_shutdown_output_into_session_drain`
  proves startup, protocol, shutdown, and affine second-drain behavior
  deterministically.
- `dynamic_plugin_tokio_postgres_client_child` proves real native startup and
  shutdown backend output is available through the session drain after
  `connect_with_driver(...)`.
- Existing direct runtime `take_backend_output` behavior from ADR-0014 remains
  affine and unchanged for non-tokio users.
- Swarm already carries the libpglite session drain into its native-libpglite
  connection-driver wrapper; its remaining record-level diagnostic projection is
  downstream product architecture, not unfinished libpglite substrate.

## Implementation Notes

2026-05-22:

- Added `PgliteSessionBackendOutputDrain` and an internal writer owned by
  `PgliteProtocolStream`.
- Hard-cut the raw `connect(...)` API to return the drain beside the
  `tokio_postgres::Client` and connection future, so direct tokio users cannot
  lose backend-output authority silently.
- `connect_with_driver(...)` now stores the same drain in
  `PgliteTokioPostgresSession`; `into_parts(...)` returns `(client,
  driver_guard, backend_output)`.
- Verification:
  - `cargo fmt --check`;
  - `cargo test --features client-tokio-postgres
    postgres_client::tests::protocol_stream_moves_startup_protocol_and_shutdown_output_into_session_drain --quiet`;
  - `LIBPGLITE_RUN_TOKIO_POSTGRES_CHILD=1 ... cargo test --features
    dynamic-loading,client-tokio-postgres --test dynamic_plugin
    dynamic_plugin_tokio_postgres_client_child -- --nocapture`.
