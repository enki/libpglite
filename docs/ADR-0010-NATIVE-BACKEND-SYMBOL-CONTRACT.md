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

- The backend export set is generated from every packaged extension module in
  the final parity set, not only the current contrib subset.
- macOS preflight proves the plugin exports the generated backend symbols while
  keeping unrelated implementation symbols hidden from the public host ABI.
- Linux preflight implements the equivalent export/version-script contract and
  proves bundled extension modules resolve against the globally loaded plugin.
- The package doctor fails on stale backend-symbol diagnostics, missing exported
  backend symbols, and extension modules with unresolved backend references.
- Packaged-artifact conformance creates representative extension modules that
  require backend symbol resolution, including `pgcrypto` and PostGIS.

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
- Opening the plugin with local symbol visibility is insufficient for extension
  parity even if the plugin file itself exports the right symbols.
- The macOS bring-up now proves this contract for PostgreSQL `contrib` modules
  `citext` and `pgcrypto`: the Rust loader opens the plugin globally, the
  manifest emits generated backend exports, contrib bundles are linked with
  dynamic lookup instead of `-bundle_loader postgres`, and the native dynamic
  test creates both extensions in one runtime.
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
- A macOS controlled-prefix opt-in prepare has generated the backend export set
  after building the full PGlite `other_extensions` set, including `vector` and
  PostGIS. That is useful evidence that the scanner can see beyond `contrib`,
  but this ADR stays open until the final packaged parity set drives the export
  set and packaged conformance proves those modules load through the plugin.
