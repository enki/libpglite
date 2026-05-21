# ADR-0010: Native Backend Symbol Contract

Status: Open
Date: 2026-05-21

## Context

PostgreSQL native extensions are loadable modules. They are not ordinary
libraries with every backend dependency linked directly into each module. A
normal PostgreSQL installation loads extension modules into a process that
already contains the backend symbols those modules reference.

PGlite's WASM build has the same shape in Emscripten terms: the main module
exports backend symbols, and extension side modules resolve against that main
module. A native `libpglite` release needs the platform equivalent. The native
plugin is the process-local backend image, and bundled extension modules must be
able to resolve their backend references from it.

This is separate from the public host ABI. Rust and C hosts should only call the
`libpglite_plugin_*` ABI. PostgreSQL extension modules need a larger generated
backend-symbol surface at dynamic-load time.

## Decision

The native plugin will intentionally act as the backend main module for bundled
PostgreSQL extension modules.

The release build must generate a backend-symbol export set from the extension
modules installed in the packaged Postgres prefix. That generated set is part of
the native link manifest. The plugin build consumes the manifest and exports
only:

- the stable host-facing `libpglite_plugin_*` ABI symbols, and
- the generated backend symbols required by bundled extension modules.

The dynamic loader must open the native plugin with process-global symbol
visibility on Unix platforms so subsequently loaded PostgreSQL extension modules
can resolve backend references against the plugin image.

The generated backend-symbol set is not a public application ABI. It exists only
to satisfy PostgreSQL's extension loader contract for the exact bundled
extension inventory and may change when the pinned PGlite source or extension
set changes.

## Required Work

1. Scan installed native extension modules for undefined backend references.
2. Intersect those references with symbols defined by the linked native
   Postgres/PGlite backend inputs.
3. Emit the resulting backend-symbol export set in the native link manifest.
4. Teach the native plugin build to export those symbols on macOS and Linux.
5. Teach the Rust dynamic loader to open the plugin with global symbol
   visibility on Unix platforms.
6. Update native preflight checks so they distinguish stable host ABI exports
   from generated backend exports.
7. Add runtime tests that prove bundled extension modules can be loaded from a
   clean database through `CREATE EXTENSION`.

## Acceptance Criteria

- `CREATE EXTENSION citext` works from the dynamic plugin using the packaged
  native Postgres prefix.
- `CREATE EXTENSION pgcrypto` works from the dynamic plugin using the packaged
  native Postgres prefix.
- The plugin export list contains all generated backend symbols required by the
  bundled extension modules.
- The plugin export list does not expose unrelated implementation symbols beyond
  the stable host ABI and generated extension backend-symbol set.
- Release preflight fails if an installed extension module has an unresolved
  backend symbol that is not exported by the plugin.
- Release preflight fails if the generated backend-symbol set is stale relative
  to the packaged extension modules.

## Remaining Closure Criteria

- Linux preflight implements the equivalent export/version-script contract with
  one authoritative final link step, then proves bundled extension modules
  resolve against the globally loaded plugin.
- The package doctor keeps failing on stale backend-symbol diagnostics, missing
  exported backend symbols, and extension modules with unresolved backend
  references, with regression coverage for the full parity set.
- Linux packaged-artifact conformance creates representative extension modules
  that require backend symbol resolution, including `pgcrypto` and PostGIS.
- Linux symbol-boundary checks tolerate only the GNU version node plus the
  stable host ABI and generated backend-symbol set; they must not accept
  accidental exports from Rust, PostgreSQL, or dependency archives.

## Implementation Notes

- This ADR refines ADR-0002's native build lane and ADR-0008's extension parity
  work. ADR-0002 owns linking the backend into the plugin. ADR-0008 owns the
  extension inventory. This ADR owns the dynamic-symbol contract between those
  two pieces.
- On macOS, PostgreSQL extension modules are Mach-O bundles. They can contain
  unresolved backend references and expect those references to be available from
  the loading process. The stock PostgreSQL Darwin build uses
  `-bundle_loader postgres`, which binds those unresolved references to the main
  executable. Native `libpglite` extension bundles must instead use dynamic
  lookup semantics so backend symbols can resolve from the globally loaded
  plugin image inside arbitrary host processes.
- On Linux, the equivalent release behavior should be implemented with an
  explicit exported dynamic symbol list or version script that includes both the
  `libpglite_plugin_*` ABI and the generated backend-symbol set.
- Rust `cdylib` builds already pass a generated GNU ld version script for the
  Rust-exported ABI. A second anonymous version script cannot be combined with
  that generated script, and a focused Ubuntu linker probe showed that
  `--dynamic-list` and `--export-dynamic-symbol` do not override the Rust
  script's `local: *` rule. The Linux closure path therefore needs either a
  single final linker-owned export script, a staticlib-plus-final-link packaging
  step, or an equivalent mechanism that gives one linker boundary ownership of
  both host ABI symbols and generated backend symbols.
- Opening the plugin with local symbol visibility is insufficient for extension
  parity even if the plugin file itself exports the right symbols.
- The macOS bring-up now proves this contract for PostgreSQL `contrib` modules
  `citext` and `pgcrypto`: the Rust loader opens the plugin globally, the
  manifest emits generated backend exports, contrib bundles are linked with
  dynamic lookup instead of `-bundle_loader postgres`, and the native dynamic
  test creates both extensions in one runtime.
- The macOS release path now proves the same contract for the full PGlite
  parity sweep from the packaged artifact. The backend export scanner runs
  after `contrib` and all materialized `pglite/other_extensions` have been
  installed, including `pg_textsearch`, PostGIS, and `vector`; the package
  doctor checks the exported-symbol diagnostics against the actual plugin; and
  the packaged runtime creates the extension set through the globally loaded
  plugin.
- The scanner must include common data symbols as well as text/data/BSS symbols.
  The full parity sweep exposed `BufferBlocks` as a required common symbol for
  `pg_textsearch`; the scanner now accepts `T`, `D`, `B`, `S`, and `C` symbol
  classes so extension modules do not depend on accidental symbol omissions.
- Linux packaged runtime conformance exposed `InvalidObjectAddress` as a
  required read-only backend data symbol for `pg_ivm`. The scanner now includes
  `R` symbols as well as `T`, `D`, `B`, `S`, and `C`, because extension parity
  must be driven by actual undefined module references rather than an assumed
  subset of backend symbol classes.
- Bundled procedural language modules are part of the same dynamic-symbol
  contract. The generated `plpgsql` module is rebuilt during native prepare with
  the extension dynamic-lookup linker flags so extensions that require
  `plpgsql`, including `pg_textsearch`, resolve backend symbols from the plugin
  instead of from a nonexistent standalone `postgres` executable.
- PGlite's own `pglitec.o` must also route `exit()` through the native exit trap.
  Otherwise `pgl_longjmp()` escapes the Rust process with status 100 instead of
  returning through the C trampoline as a recoverable backend longjmp boundary.
- Multiple native runtime startups in one process remain unsafe with the current
  PostgreSQL global-state lifecycle. Runtime conformance should exercise
  multiple SQL operations in a single runtime until lifecycle reset is
  deliberately implemented.
- Packaged diagnostics now carry the generated backend export set, and the
  package doctor verifies that the diagnostic set agrees with the native link
  manifest and that every recorded backend export is actually exported by the
  packaged plugin.
- A macOS controlled-prefix prepare now generates the backend export set after
  building the full PGlite `other_extensions` set, including `vector` and
  PostGIS, and that prepare is part of normal macOS preflight. This ADR remains
  open because Linux still needs the equivalent exported-symbol/version-script
  contract.
- The Linux plugin build now uses a Rust `staticlib` plus a final native link
  script, giving GNU ld one version-script boundary that owns both the stable
  `libpglite_plugin_*` ABI and the generated `backend_export_symbol=` manifest
  entries. A focused Linux probe showed that `--exclude-libs,ALL` would hide
  archive-sourced ABI symbols even when the version script listed them, so the
  final link relies on the version script's `local: *` rule as the export
  boundary.
- Linux symbol scanners now filter the GNU version-node symbol
  `LIBPGLITE_PLUGIN_NATIVE_1` before comparing exported symbols. The version
  node is expected metadata from the single final version script, not an
  additional public export.
- The Ubuntu smolvm lane also exposed that `src/timezone/zic.o` and
  `src/timezone/zdump.o` are CLI entrypoint objects, not backend timezone
  runtime objects. The native timezone archive now excludes them so Linux does
  not collide with PostgreSQL backend `main.o` during whole-archive linking.
- Linux runtime bring-up then reached `pgl_startPGlite` and exposed a separate
  portability issue: PostgreSQL selected its epoll latch implementation for a
  dummy PGlite socket descriptor. Native prepare now forces poll/self-pipe on
  Linux. The remaining Linux runtime failure is no longer the export boundary;
  it is proving that the callback socket transport reaches
  `ProcessStartupPacket` exactly as it does in the WASM lane.
