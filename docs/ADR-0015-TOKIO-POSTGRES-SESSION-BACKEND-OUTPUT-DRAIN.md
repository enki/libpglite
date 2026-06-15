# ADR-0015: Tokio Postgres Session Backend Output Drain

Status: Open

## Context

ADR-0014 captures native backend stdout/stderr into `PgliteBackendOutputLedger`
and exposes `PgliteRuntime::take_backend_output`. That closes terminal leakage at
the native runtime boundary.

The retained `tokio-postgres` integration still hides the runtime inside
`PgliteProtocolStream` after connection setup. Once a host calls
`connect_with_driver(...)`, downstream code receives a `tokio_postgres::Client`
and a driver guard, but it does not receive an authority that can drain backend
output produced during startup, protocol execution, or shutdown. Downstream
hosts can prove that raw terminal output no longer leaks, but they cannot yet
attach backend output records to the owning invocation/test case without reaching
around libpglite internals.

## Decision

The tokio-postgres session boundary must carry a session-owned backend output
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

## Required Work

1. Add `PgliteSessionBackendOutputDrain` or equivalent session-owned carrier.
2. Change `PgliteProtocolStream::new(...)` to receive the drain writer and drain
   startup output immediately after taking the runtime.
3. Drain records after `exec_protocol_raw(...)`, `shutdown(...)`, and drop
   shutdown.
4. Change `PgliteTokioPostgresSession::into_parts(...)` to return the drain.
5. Update downstream users to carry the drain into their provider/test diagnostic
   ledgers instead of treating backend output as terminal text.

## Acceptance Criteria

- A focused libpglite test proves startup, protocol, and shutdown records are
  observable through the session drain after `connect_with_driver(...)`.
- Existing direct runtime `take_backend_output` behavior from ADR-0014 remains
  affine and unchanged for non-tokio users.
- Downstream hosts can attach native-libpglite backend output records to the
  owning invocation/test case without scraping process stdout/stderr or retaining
  a separate runtime handle.

## Remaining Closure Criteria

- Done: implement the session drain in `PgliteProtocolStream` and
  `PgliteTokioPostgresSession`. `PgliteSessionBackendOutputDrain` is returned
  from `connect(...)` and `PgliteTokioPostgresSession::into_parts(...)`.
  `PgliteProtocolStream` drains runtime backend output at stream birth, after
  every protocol flush, after protocol errors, after shutdown, and during
  drop-shutdown.
- Done: update dynamic/native tokio-postgres tests. The fake-runtime protocol
  stream test proves startup, protocol, shutdown, and affine second-drain
  behavior deterministically. The dynamic native plugin tokio-postgres test
  proves real native startup/shutdown backend output is available through the
  session drain after `connect_with_driver(...)`; the real PostgreSQL backend
  does not always emit protocol text, so protocol-drain coverage stays in the
  deterministic runtime test.
- Downstream carry-forward started: durable native-libpglite providers can carry
  the session drain in their native connection-driver wrapper instead of dropping
  it at admission time. Projection into provider/test diagnostics remains a
  downstream owner concern.

## Implementation Progress

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

This ADR remains open only for downstream projection. The libpglite session drain
authority now exists; product hosts must still consume their own diagnostic
aperture and attach the records to the appropriate invocation/test diagnostics.
