# ADR-0002: Native PGlite Build Lane

Status: Done
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

## Remaining Closure Criteria

- Raw protocol conformance covers startup, simple query, extended query,
  parameter-bound extended query, named prepared-statement reuse, transaction
  success, transaction rollback, recoverable protocol error, and deterministic
  shutdown from the dynamic plugin.
- The high-level Rust client transport runs against the extracted final package,
  not only the build-tree plugin and prefix.
- The final protocol scope is explicit before this ADR moves to done. The
  current minimum is the raw case set above plus the high-level Rust client
  transport from the packaged artifact; any additional PostgreSQL frontend
  protocol family judged release-critical must be added as a named conformance
  case instead of left as an informal expectation.
- Linux and macOS preflight prove the same final-package path after the final
  protocol scope is set, and packaged diagnostics record the exact raw protocol
  cases and high-level client command that passed.

## Closing Evidence

- The native build lane links the pinned PostgreSQL/PGlite backend into the
  dynamic plugin without JavaScript, Emscripten module objects, a WASM runtime,
  wasm2c-named payloads, or bitcode payloads in the native package.
- The raw protocol conformance scope for the first production runtime is now
  explicit: startup, simple query, empty query, transaction rollback,
  transaction commit, recoverable protocol error, basic extended query,
  parameter-bound extended query, named prepared-statement reuse, and
  deterministic shutdown.
- `scripts/doctor-native-plugin-package.py` rejects raw-protocol conformance
  diagnostics that omit any required named case, and it validates the recorded
  command, timestamps, log checksum, and pass status for each conformance
  result.
- The dynamic-plugin raw protocol test initializes a clean data directory,
  processes frontend protocol bytes, recovers after a PostgreSQL error, creates
  contrib and PGlite extension parity entries, and shuts down deterministically.
- The high-level `tokio-postgres` client test runs through the same runtime
  boundary and covers parameter binding, extension loading, transaction
  rollback, and post-rollback query recovery.
- The package doctor `--self-test` now extracts the final package and runs both
  the raw protocol/extension sweep and the `tokio-postgres` client transport
  against the packaged plugin and packaged Postgres prefix.
- `scripts/preflight-native-plugin-release.sh v0.1.0` passed on macOS on
  2026-05-21 through native link, raw protocol conformance, high-level client
  conformance, prefix initialize/resume conformance, package smoke, package
  doctor, and final-artifact self-test.
- `scripts/doctor-native-plugin-package.py --strict-relocatable --self-test
  dist/preflight-native-plugin/libpglite-plugin-native-v0.1.0-aarch64-apple-darwin.tar.zst`
  passed on 2026-05-21 after the doctor self-test was extended to run the
  high-level client from the extracted package.
- `scripts/preflight-linux-smolvm.sh 0.1.0` previously passed the same
  release-path shape in the Ubuntu `24.04` baseline for the then-current
  conformance set; future protocol widening must rerun that lane before release.

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
  PostgreSQL startup packet, execute raw simple query and empty-query messages,
  exercise
  transaction rollback and transaction commit, recover after a backend protocol
  error, execute basic extended-query, parameter-bound extended-query, and named
  prepared-statement reuse flows, create contrib extensions, and shut down. The
  same native plugin is also exercised through the `tokio-postgres` client
  transport in an isolated process.
- `scripts/preflight-native-plugin-release.sh v0.1.0` now passes on macOS with
  native Postgres/PGlite linked, the full materialized PGlite extension parity
  set built into the packaged prefix, and the package doctor self-test
  exercising the final archive. This closes the macOS release-path proof for
  this ADR, but not the ADR itself because the final protocol conformance scope
  still needs to be made explicit.
- The package doctor now rejects native package payloads that contain `.wasm`,
  JavaScript module files, Emscripten-named artifacts, wasm2c-named artifacts,
  or bitcode inputs. `scripts/test-doctor-native-plugin-package.py` pins those
  failures so the native package cannot silently regress to a WASM or wasm2c
  fallback shape.
- Broader protocol coverage still remains open, but the raw conformance path now
  covers both rollback and commit transactions plus parameter binding through
  the extended-query protocol. Full extension parity, packaging hardening, and
  the current restart lifecycle now have macOS and Ubuntu final-package evidence
  and remain tracked in their owning ADRs until production gates close.
- Raw-protocol conformance diagnostics now carry an explicit case inventory:
  startup, simple query, empty query, transaction rollback, transaction commit,
  recoverable protocol error, extended query, parameterized extended query, and
  named prepared-statement reuse, and deterministic shutdown. The package doctor
  rejects raw-protocol conformance results that do not name all of those cases,
  so packaged diagnostics can explain what the passing result actually proved.
- The package doctor `--self-test` now runs the high-level
  `tokio-postgres` client transport against the extracted final package using
  the packaged plugin and packaged Postgres prefix. The test covers parameter
  binding, a loaded extension, transaction rollback, and post-rollback query
  recovery through the normal Rust PostgreSQL client layer.
