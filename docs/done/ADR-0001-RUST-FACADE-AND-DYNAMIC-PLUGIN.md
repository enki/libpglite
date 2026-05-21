# ADR-0001: Rust Facade and Dynamic Plugin Boundary

Status: Done
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

## Implementation Notes

- The root crate remains the publishable facade. The native adapter and plugin
  crates are workspace members with `publish = false`.
- The dynamic loader checks `libpglite_plugin_abi_version` before resolving the
  rest of the plugin ABI, and the test suite builds a tiny fake plugin to prove
  ABI mismatch fails before runtime creation.
- The dynamic loader test suite also builds a fake correct-ABI plugin that
  returns plugin-owned status buffers from create, protocol execution, and
  shutdown. The test proves each payload is released through
  `libpglite_plugin_buffer_free`.
- Release packaging and the package doctor validate plugin checksums and the
  required `libpglite_plugin_*` export set. Symbol diagnostics must match the
  packaged plugin.
- Native preflight now checks the facade dependency boundary with
  `cargo tree -p libpglite --edges normal --no-default-features` and fails if
  the facade pulls in the internal native implementation or plugin crates.
- Full native preflight now enforces the facade/plugin boundary, including ABI
  mismatch rejection, plugin-owned buffer release, resolver path behavior,
  package checksum/symbol validation, and the no-default-feature facade
  dependency boundary.
