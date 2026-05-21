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

- Extension C objects and required third-party native libraries are linked into
  the native plugin or otherwise made inseparable from the released native
  bundle.
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
3. Link extension objects and required third-party native libraries into the
   native plugin with the same always-available semantics as the WASM build.
4. Install every extension's `.control`, SQL upgrade files, support files,
   dictionaries, headers, and data files into the generated Postgres prefix.
5. Package third-party runtime data required by extensions, including PostGIS
   projection data.
6. Record the extension inventory, versions, linked libraries, and data paths in
   the native link manifest and release bundle metadata.
7. Add preflight checks that fail when the native extension inventory diverges
   from the pinned PGlite WASM inventory.
8. Add runtime conformance tests that initialize a clean data directory and run
   `CREATE EXTENSION` smoke tests for the full parity set, including `vector`
   and PostGIS.

## Acceptance Criteria

- A production native release contains the same PGlite extension surface as the
  pinned WASM distribution.
- `CREATE EXTENSION vector` works in a clean native `libpglite` database without
  additional files or user configuration.
- `CREATE EXTENSION postgis` works in a clean native `libpglite` database without
  additional files or user configuration.
- Release preflight fails if `postgres-pglite` adds an extension and the native
  build does not include it.
- Release preflight fails if extension control files are present without the
  corresponding linked code or required support data.
- The native bundle remains relocatable; no extension depends on build-machine
  absolute paths.

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
  manifest. It currently shows the PGlite `other_extensions` as required
  submodules with explicit present/missing status.
- The current local pinned source has unpopulated `pglite/other_extensions`
  submodules. Full parity requires fetching or vendoring those exact submodule
  commits before native extension builds can be made release-gating.
