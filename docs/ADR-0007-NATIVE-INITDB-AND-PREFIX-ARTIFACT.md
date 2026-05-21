# ADR-0007: Native Initdb And Prefix Artifact

Status: Open
Date: 2026-05-21

## Context

The native plugin can link the PGlite backend, but a usable runtime also needs
the files that PostgreSQL normally installs beside `postgres`: generated catalog
data, timezone data, text search dictionaries, extension control files, and
`initdb`.

`pglite-bindings` treats this prefix as an explicit artifact. That is the right
shape for `libpglite` too; runtime startup should not depend on whatever happens
to be present in a developer checkout.

## Decision

The native build lane will produce a relocatable Postgres install prefix
artifact from the same pinned source snapshot as the linked backend.

The runtime may use that prefix to initialize a new data directory and to resolve
PostgreSQL runtime support files. Release packaging must carry the prefix beside
the plugin or provide an equivalent generated artifact.

## Required Work

1. Build and install the pinned `postgres`, `initdb`, generated catalog files,
   timezone data, text search files, and bundled procedural language metadata.
2. Record the prefix paths in the native link manifest.
3. Add a smoke test that runs `initdb` from the produced prefix against a
   temporary data directory.
4. Make package layout carry the prefix or an equivalent compact archive beside
   the native plugin.
5. Teach runtime startup how to find the packaged prefix without relying on
   build-machine absolute paths.
6. Verify initialized clusters can be resumed by the in-process backend.

## Acceptance Criteria

- A clean checkout can produce a native prefix from the pinned source snapshot.
- `initdb` can create a temporary cluster using only the produced prefix.
- Packaged releases do not contain build-machine absolute paths as the only way
  to find PostgreSQL support files.
- Runtime open can initialize a missing data directory and resume an existing
  one.

## Implementation Notes

- `scripts/prepare-native-pglite-link.sh --build-postgres` now installs a
  prefix under the native build directory and records `postgres_install_prefix`,
  `initdb_binary`, `postgres_binary`, `postgres_share_dir`, and
  `postgres_lib_dir` in the manifest.
- `scripts/package-native-plugin-release.sh` requires that manifest and packages
  the prefix under `postgres/` beside the native plugin. The bundle metadata
  records the relative prefix paths.
- Native preflight now records separate `prefix-initialize` and `prefix-resume`
  conformance results. The checks run in separate processes against the same
  temporary data directory: the first proves the runtime can initialize a
  missing cluster using the produced prefix, and the second proves a later
  process can open the initialized cluster and read persisted data.
- The dynamic runtime test suite also verifies that a nonempty directory without
  `PG_VERSION` is rejected before native backend startup. That keeps invalid
  prefix/data-dir inputs from poisoning the current single-start process.
- Package doctor `--self-test` now runs a bundled-prefix runtime smoke that
  opens the plugin by package directory without `LIBPGLITE_TEST_POSTGRES_PREFIX`.
  This proves the release artifact can find the packaged `postgres/` prefix
  beside the plugin instead of relying on build-machine prefix paths.
