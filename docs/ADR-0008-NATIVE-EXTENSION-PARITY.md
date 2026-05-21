# ADR-0008: Native Extension Parity

Status: Open
Date: 2026-05-21

## Context

The native build lane currently focuses on the core PGlite/Postgres runtime and
its relocatable prefix. It installs the backend support files and `plpgsql`, but
it does not build or package the extension set that ships with the PGlite WASM
distribution.

That is not the product shape we want. `libpglite` should feel like native
PGlite, not like a smaller PostgreSQL build with a different extension surface.
Applications should not have to choose between the WASM package for extension
coverage and the native plugin for embedding.

The pinned `postgres-pglite` source already defines the extension surface in two
places:

- PostgreSQL `contrib`, packaged by the PGlite WASM build.
- `pglite/other_extensions`, currently including `pg_ivm`, `vector`, `pgtap`,
  `pg_uuidv7`, `age`, `pg_hashids`, `pg_textsearch`, and `postgis`.

PostGIS also carries native dependency and data requirements such as GEOS, PROJ,
and projection data. Those requirements are part of parity, not a reason to make
GIS support optional.

## Decision

The native plugin release scope is extended to mandatory extension parity with
the pinned PGlite WASM distribution.

Every extension that the pinned `postgres-pglite` PGlite build includes must be
built, linked, installed, and packaged for native `libpglite` releases. This is
an always-on runtime contract, not a Cargo feature, build profile, or downstream
packaging choice.

Native extension support should mirror the WASM shape as closely as practical:

- The native build should follow the pinned PGlite build pipeline structure:
  build a controlled dependency prefix, configure the pinned Postgres fork
  against that prefix, build PostgreSQL `contrib`, special-case extensions that
  PGlite special-cases, build `pglite/other_extensions`, then package the
  installed extension files and runtime data.
- Extension C objects and required third-party native libraries are linked into
  the native plugin or otherwise made inseparable from the released native
  bundle.
- Extension modules may remain loadable PostgreSQL `.dylib` or `.so` files when
  that matches PostgreSQL's native extension model, but their dependencies must
  be bundled and relocatable so they are still inseparable from the release.
- Extension SQL, control files, dictionaries, metadata, and support data are
  installed into the packaged Postgres prefix.
- PostgreSQL must be able to `CREATE EXTENSION` for the parity set without
  reaching outside the `libpglite` native bundle.
- The extension inventory is derived from the pinned `postgres-pglite` source so
  extension additions or removals in PGlite are visible during native preflight.

The native build may still produce intermediate archives or staged extension
directories, but release artifacts must behave as a single complete PGlite
runtime. Consumers should not need to opt in to `vector`, PostGIS, or any other
PGlite-shipped extension.

## Required Work

1. Define a generated extension inventory from the pinned `postgres-pglite`
   build scripts, covering both `contrib` and `pglite/other_extensions`.
2. Extend `scripts/prepare-native-pglite-link.sh` to build the parity extension
   set as native PIC inputs.
3. Build the extension set through a native equivalent of the PGlite WASM build:
   core `contrib`, PGlite's `pgcrypto` special case, `pglite/other_extensions`,
   and PGlite's PostGIS special case.
4. Link extension objects and required third-party native libraries into the
   native plugin or into bundled loadable modules with the same always-available
   semantics as the WASM build.
5. Install every extension's `.control`, SQL upgrade files, support files,
   dictionaries, headers, and data files into the generated Postgres prefix.
6. Package third-party runtime data required by extensions, including PostGIS
   projection data.
7. Record the extension inventory, versions, linked libraries, and data paths in
   the native link manifest and release bundle metadata.
8. Add preflight checks that fail when the native extension inventory diverges
   from the pinned PGlite WASM inventory.
9. Add runtime conformance tests that initialize a clean data directory and run
   `CREATE EXTENSION` smoke tests for the full parity set, including `vector`
   and PostGIS.
10. Fetch or vendor the exact `pglite/other_extensions` gitlink commits from
    the pinned `postgres-pglite` tree before building parity artifacts.

## Acceptance Criteria

- A production native release contains the same PGlite extension surface as the
  pinned WASM distribution.
- `CREATE EXTENSION vector` works in a clean native `libpglite` database without
  additional files or user configuration.
- `CREATE EXTENSION postgis` works in a clean native `libpglite` database without
  additional files or user configuration.
- Release preflight fails if `postgres-pglite` adds an extension and the native
  build does not include it.
- Release preflight fails if a PGlite `other_extensions` entry lacks a pinned
  submodule commit and source URL in the generated inventory.
- Release preflight fails if extension control files are present without the
  corresponding linked code or required support data.
- The native bundle remains relocatable; no extension depends on build-machine
  absolute paths.
- This ADR moves to `docs/done/` only after the full inventoried parity set is
  built, packaged, and exercised from the packaged artifact.

## Implementation Notes

- ADR-0002 still owns the core native backend link model. This ADR expands the
  set of mandatory native link inputs once the base runtime can execute queries.
- ADR-0007 owns the prefix artifact. This ADR expands that artifact from core
  runtime support files to the full PGlite extension prefix.
- PostgreSQL `contrib` should not be treated as a manually curated subset unless
  the pinned PGlite WASM build itself excludes an extension.
- `pglite/other_extensions/vector` is the pgvector-compatible vector extension
  used by PGlite and is part of the required parity set.
- PostGIS should be handled as a first-class parity requirement even though it
  requires third-party library and data packaging.
- `scripts/inventory-native-pglite-extensions.py` derives a native extension
  inventory from the pinned PGlite source and records it in the native link
  manifest. It now records each PGlite `other_extensions` entry's source path,
  present/missing status, gitlink commit, submodule URL, and branch metadata
  where the pinned source declares it.
- The current local pinned source has unpopulated `pglite/other_extensions`
  submodules, but the pinned gitlinks are now visible in the inventory:
  `age`, `pg_hashids`, `pg_ivm`, `pg_textsearch`, `pg_uuidv7`, `pgtap`,
  `postgis`, and `vector` each carry exact commits and URLs. Full parity
  requires fetching or vendoring those exact commits before native extension
  builds can be made release-gating.
- The package doctor now validates inventoried PostgreSQL `contrib` extensions
  beyond control-file presence: it checks each extension's `default_version`
  SQL and verifies native `$libdir`/`MODULE_PATHNAME` modules are present in the
  packaged Postgres prefix.
- Missing PGlite `other_extensions` submodules are warnings for development
  packages and production-package failures. Missing submodule commits or URLs
  are package errors in all modes because they mean the native inventory cannot
  identify the exact parity target. This keeps the current macOS bring-up usable
  while preventing a production artifact from silently claiming full PGlite
  extension parity without `vector`, PostGIS, and the rest of the pinned PGlite
  extension set.
- The native prepare step now builds extension-bearing PostgreSQL `contrib`
  source directories individually and validates installed control files.
  Standalone contrib modules and utility programs remain inventoried, but are
  not part of the first `CREATE EXTENSION` parity gate.
- PGlite's WASM build keeps global OpenSSL disabled for the Postgres configure
  step and special-cases `pgcrypto` with explicit OpenSSL side-module link
  flags. The native build should copy that shape: do not enable `sslinfo`
  implicitly just to satisfy `pgcrypto`; instead give `pgcrypto` its explicit
  native OpenSSL module link inputs.
- PGlite packages extension artifacts separately from core backend build output.
  Native releases should do the same conceptually: build/install extension
  modules and their SQL/control/data into a staged prefix, then package that
  prefix beside the plugin.
- PGlite's WASM main module exports the backend symbols required by side-module
  extensions. Native releases need the platform equivalent: enough backend
  symbols visible to extension modules at load time while the public plugin ABI
  remains limited to `libpglite_plugin_*`.
