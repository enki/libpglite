# ADR-0013: Retained Tokio Postgres Session

Status: Done
Date: 2026-05-22

## Context

ADR-0003 proved that `libpglite` can expose a normal `tokio-postgres`
client over the in-process PGlite protocol stream. ADR-0011 then made the
native runtime lifecycle explicit: production native hosts must use one
long-lived runtime per process, not close and reopen the backend.

The current `postgres_client::connect(...)` API returns a loose
`(tokio_postgres::Client, TokioPostgresConnection<R>)` pair. That is a valid
low-level transport primitive, but embedders that need a retained production
session must assemble the lifecycle shape themselves:

- spawn or otherwise drive the connection future,
- retain the driver guard for as long as the client is usable,
- ensure migration/admin work and application work use the same opened backend,
- avoid treating a raw client as independent of runtime ownership.

That lets every embedding crate rediscover the same lifecycle law and makes
bad states easier to express at integration boundaries.

## Decision

`libpglite` will add a retained `tokio-postgres` session primitive.

The primitive owns:

- the `tokio_postgres::Client`,
- the embedder-provided connection-driver guard,
- the fact that the guard was created from the matching
  `TokioPostgresConnection<R>`.

The primitive does not own application or product authority. It is only the
libpglite lifecycle carrier:

```text
PgliteRuntime
  -> PgliteProtocolStream
  -> tokio_postgres::Client + TokioPostgresConnection
  -> embedder drives connection and returns guard
  -> PgliteTokioPostgresSession
```

Embedders may wrap this session with their own authority records. They must not
hand-author an equivalent raw client/driver pair as production lifecycle
evidence when the retained session aperture is available.

## Required Work

1. Add `PgliteTokioPostgresSession<DriverGuard>`.
2. Add `connect_with_driver(...)`, which connects through the existing
   protocol stream, consumes the connection with an embedder-provided driver
   spawner, and returns the retained session.
3. Keep `connect(...)` as the low-level transport primitive from ADR-0003.
4. Update downstream durable-runtime integrations to use the retained session
   when proving same-process native runtime operation.

## Acceptance Criteria

- A high-level embedding can receive one value that carries both the
  `tokio_postgres::Client` and the retained connection-driver guard.
- The retained-session constructor is the only libpglite API that claims a
  client is backed by a live driver guard.
- Driver-spawn failures are reported distinctly from tokio-postgres connection
  failures.
- The API preserves ADR-0011's single-start lifecycle. It does not add restart,
  multi-open, or backend replacement semantics.
- Existing `postgres_client::connect(...)` users continue to compile as
  low-level transport users.

## Implementation Notes

- `src/postgres_client.rs` adds
  `PgliteTokioPostgresSession<DriverGuard>`,
  `PgliteTokioPostgresSessionError<DriverError>`, and
  `connect_with_driver(...)`.
- `connect(...)` remains the low-level ADR-0003 transport primitive for callers
  that intentionally want to own connection driving themselves.
- The dynamic-plugin tokio-postgres smoke path now exercises
  `connect_with_driver(...)` when its native-plugin environment gate is enabled.
- Downstream durable execution can consume the retained-session API in an
  optional native-libpglite durable Postgres provider, then wrap the returned
  client and driver guard in product-specific store/provider authority.

## Closing Evidence

- `src/postgres_client.rs` exposes
  `PgliteTokioPostgresSession<DriverGuard>` and
  `connect_with_driver(...)`, so embedders can receive one value carrying the
  `tokio_postgres::Client` and the retained connection-driver guard.
- Driver-spawn failure and tokio-postgres connection failure are represented as
  distinct `PgliteTokioPostgresSessionError` variants.
- The low-level ADR-0003 `connect(...)` API remains available for intentional
  transport-level callers.
- The retained-session API preserves ADR-0011's `single-start-per-process`
  lifecycle and does not add restart, multi-open, or backend replacement
  semantics.
- `tests/dynamic_plugin.rs` uses `connect_with_driver(...)` for the
  tokio-postgres native-plugin smoke route when its native-plugin environment
  gate is enabled.
- Downstream durable execution can consume the retained-session API in an
  optional native-libpglite durable Postgres provider, proving the downstream
  authority shape compiles through a real embedding boundary.
