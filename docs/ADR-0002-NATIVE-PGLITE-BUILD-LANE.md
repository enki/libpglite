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
6. Keep the public host-facing ABI limited to `libpglite_plugin_*` symbols while
   allowing the native backend symbol exports required by PostgreSQL extension
   modules.
7. Add preflight checks for missing objects, debug objects, and unexpected
   dynamic exports.
8. Add protocol conformance fixtures that prove startup, simple query, extended
   query, transaction, error recovery, and shutdown.

## Acceptance Criteria

- The plugin links on supported macOS and Linux targets.
- The plugin's public host-facing ABI is limited to `libpglite_plugin_*`
  symbols; any additional dynamic exports are generated backend symbols required
  for PostgreSQL extension module loading.
- A Rust test can open a temporary data directory, run `select 1`, and shut down.
- A protocol error does not poison the runtime if PostgreSQL can recover.
- No JavaScript, Emscripten module object, or wasm runtime is required by the
  production native plugin.

## Implementation Notes

- The source snapshot is pinned in `PGLITE_POSTGRES_SOURCE`.
- `scripts/prepare-native-pglite-link.sh --build-postgres` configures a native
  VPATH build of the pinned fork, compiles `pglitec.c` as PIC, builds the
  backend with the PGlite syscall and longjmp overrides, and emits a manifest
  with concrete object/archive inputs.
- The plugin build reads the manifest when `LIBPGLITE_NATIVE_LINK_PGLITE=1` and
  links the Postgres/PGlite symbols into the cdylib while keeping the public
  host-facing ABI limited to the `libpglite_plugin_*` symbols. ADR-0010 owns the
  generated backend-symbol export set needed by PostgreSQL extension modules.
- The native build lane also emits an install prefix for `initdb` and PostgreSQL
  runtime support files; ADR-0007 owns making that prefix relocatable and
  package-ready.
- Non-Emscripten `pgl_exit()` is diverted to a small native C trap helper so
  expected Postgres/PGlite exits can be recovered inside C before control
  returns to Rust. The same helper source is compiled in two modes: a tiny
  exit-only object used when broad Postgres `LDFLAGS_EX` reaches helper
  programs, and a backend trampoline object linked into the plugin for wrapped
  calls such as `PostgresSingleUserMain()`, `PostgresMainLoopOnce()`, and
  `pgl_run_atexit_funcs()`.
- Cargo build scripts now track the generated native link manifest itself, so
  object/archive changes from the preparation script invalidate stale plugin
  link arguments.
- On macOS the release plugin links the native Postgres/PGlite objects while
  exporting the `libpglite_plugin_*` ABI symbols and the generated backend
  symbol set needed by bundled PostgreSQL extension modules. Other Postgres and
  `libpglite_native_*` symbols remain local implementation details.
- The native runtime now has a macOS conformance path through the dynamic
  plugin: initialize a clean data directory with the generated Postgres prefix,
  install PGlite read/write callbacks, start the single-user backend, process a
  PostgreSQL startup packet, execute raw simple query messages, exercise
  transaction rollback, recover after a backend protocol error, execute a basic
  extended-query flow, create contrib extensions, and shut down. The same
  native plugin is also exercised through the `tokio-postgres` client transport
  in an isolated process.
- Broader extended-query coverage, richer transaction/error cases, full
  extension parity, packaging hardening, restart lifecycle, and Linux
  conformance remain open.
