# ADR-0001: Rust Facade and Dynamic Plugin Boundary

Status: Open
Date: 2026-05-21

## Context

PGlite has useful in-process Postgres hooks, but the current public packaging is
JavaScript and WebAssembly oriented. A Rust host needs a stable boundary that is
independent of Emscripten module objects, generated wasm2c symbols, and raw
Postgres internals.

The native implementation is expected to be complex, platform-specific, and
replaceable. It should not be statically linked into product hosts.

## Decision

`libpglite` will expose a stable Rust facade crate and load native PGlite through
a versioned dynamic plugin.

The public facade owns:

- runtime configuration
- runtime lifecycle
- PostgreSQL frontend protocol bytes in
- PostgreSQL backend protocol bytes out
- diagnostics and release/plugin resolution

The public facade must not expose:

- Postgres backend structs
- PGlite C callback internals
- generated wasm2c `WASI2C_*` symbols
- Emscripten module APIs
- native build-system details

The dynamic plugin ABI is a small C ABI:

- `libpglite_plugin_abi_version`
- `libpglite_plugin_buffer_free`
- `libpglite_plugin_runtime_create`
- `libpglite_plugin_runtime_destroy`
- `libpglite_plugin_runtime_exec_protocol_raw`
- `libpglite_plugin_runtime_shutdown`

## Required Work

1. Keep the root crate publishable as the facade.
2. Keep the native implementation in an internal crate.
3. Keep the plugin crate as the only native implementation loaded by product
   hosts.
4. Add ABI conformance tests before any native implementation ships.
5. Add release packaging that verifies plugin checksums and symbol exports.

## Acceptance Criteria

- A host can depend on the facade without linking native Postgres.
- A host can load a plugin by exact path or binary-relative path.
- Plugin ABI version mismatch fails before a runtime is created.
- Plugin-owned buffers are freed only through the plugin ABI.
- No native Postgres symbol becomes part of the public Rust API.

