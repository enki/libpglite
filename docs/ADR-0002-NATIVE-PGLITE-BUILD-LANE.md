# ADR-0002: Native PGlite Build Lane

Status: Open
Date: 2026-05-21

## Context

PGlite's Postgres fork already contains the key runtime changes needed for
in-process execution: loop unrolling, fake socket callbacks, startup packet
handling, controlled longjmp recovery, and a single-user backend shape that can
act like a protocol backend.

The existing build path is Emscripten-first. There is also evidence of a
wasm2c/static-library experiment, but that shape is not a clean long-term native
library boundary.

## Decision

The native plugin will be built from a pinned PGlite Postgres source snapshot,
not from the JavaScript package.

The first proof may wrap a wasm2c/static archive to burn down lifecycle and
protocol unknowns, but the production lane is a native PIC build that links into
the `libpglite-plugin-native` cdylib.

The native lane must preserve the PGlite runtime model:

- initialize or resume a data directory
- start the single-user backend with PGlite active
- feed PostgreSQL frontend protocol bytes through a callback transport
- collect backend protocol bytes without parsing them in the native layer
- recover from expected Postgres longjmp boundaries
- shut down deterministically

## Required Work

1. Vendor or otherwise pin the PGlite Postgres source snapshot.
2. Write `scripts/prepare-native-pglite-link.sh` to produce a native link
   manifest.
3. Compile PGlite-specific C support as PIC.
4. Compile the required Postgres backend objects as PIC.
5. Link only release-grade native inputs into the plugin.
6. Hide all native implementation symbols behind the plugin C ABI.
7. Add preflight checks for missing objects, debug objects, and unexpected
   dynamic exports.
8. Add protocol conformance fixtures that prove startup, simple query, extended
   query, transaction, error recovery, and shutdown.

## Acceptance Criteria

- The plugin links on supported macOS and Linux targets.
- The plugin exports only `libpglite_plugin_*` ABI symbols on Linux.
- A Rust test can open a temporary data directory, run `select 1`, and shut down.
- A protocol error does not poison the runtime if PostgreSQL can recover.
- No JavaScript, Emscripten module object, or wasm runtime is required by the
  production native plugin.

