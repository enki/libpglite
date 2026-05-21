# ADR-0003: PostgreSQL Client Transport

Status: Open
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

If a chosen client stack requires socket-like IO, a Unix socketpair or local
stream shim may be used as an adapter. That shim is still transport only; it
does not own provider selection, query semantics, row parsing, or runtime
lifecycle.

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

