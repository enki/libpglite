# ADR-0003: PostgreSQL Client Transport

Status: Done
Date: 2026-05-21

## Context

The runtime boundary should not reimplement PostgreSQL client semantics. Rust
already has mature PostgreSQL client crates for SQL parameters, prepared
statements, row decoding, transactions, and type handling.

The native PGlite runtime naturally speaks PostgreSQL frontend/backend protocol
bytes, not TCP sockets.

## Decision

`libpglite` will keep its first stable primitive at the protocol-byte boundary.
Higher-level SQL APIs should be layered above that boundary by adapting a normal
Rust PostgreSQL client stack to an in-process transport.

Preferred layering:

```text
Rust PostgreSQL client semantics
  -> custom in-memory transport over libpglite protocol calls
  -> dynamic plugin ABI
  -> native PGlite/Postgres loop
```

`tokio-postgres` exposes `Config::connect_raw`, so no socketpair is needed for
the first client layer.

## Required Work

1. Evaluate whether `tokio-postgres` can be driven by a custom in-memory stream.
2. If async custom stream integration is clean, build a `tokio-postgres`
   transport adapter.
3. If not, build a minimal socketpair adapter and point standard clients at it.
4. Keep raw protocol execution available for conformance and debugging.
5. Add tests comparing high-level query results with raw protocol messages.

## Acceptance Criteria

- Application code can use normal Rust PostgreSQL query APIs.
- SQL parameter encoding and row decoding are not hand-rolled in `libpglite`.
- Raw protocol execution remains available as the substrate contract.
- The high-level client layer cannot bypass runtime lifecycle ownership.

## Implementation Notes

- `src/postgres_client.rs` provides `PgliteProtocolStream<R>`, an
  `AsyncRead`/`AsyncWrite` adapter over `PgliteRuntime::exec_protocol_raw`.
- `postgres_client::connect` hands that stream to `tokio-postgres` through
  `Config::connect_raw`, so prepared statement messages, parameter encoding,
  row decoding, transactions, and type handling remain owned by the standard
  Rust PostgreSQL client stack.
- The stream owns the runtime. Dropping or shutting down the stream shuts down
  the underlying PGlite runtime, so the high-level client cannot outlive or
  bypass lifecycle ownership.
- `tests/dynamic_plugin.rs` keeps the raw protocol conformance path and adds a
  `tokio-postgres` child-process check that runs against the real native plugin.
  That check covers connection startup, parameterized query encoding, row
  decoding by name, `citext` extension loading, transaction rollback, and
  deterministic shutdown.
- `scripts/preflight-native-plugin-release.sh` runs raw protocol conformance and
  the `tokio-postgres` client transport as separate native checks. Separate
  processes keep failures attributable under the current single-start lifecycle
  contract from ADR-0011.
