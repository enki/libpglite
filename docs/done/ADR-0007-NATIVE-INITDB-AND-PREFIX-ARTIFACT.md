# ADR-0007: Native Initdb And Prefix Artifact

Status: Done
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

## Closure Criteria

- Keep package doctor self-tests initializing a missing data directory from the
  packaged prefix and resuming it from a later process without build-tree
  environment variables on every supported target.
- Strict package diagnostics keep rejecting build-machine absolute paths in
  prefix metadata, extension SQL/control files, loadable module paths, and
  runtime data references across both supported package layouts.
- The prefix layout remains stable across macOS and Linux packages; the bundle
  must continue to expose `postgres/`, `postgres/bin`, `postgres/share`,
  `postgres/lib`, `postgres/bin/initdb`, and `postgres/bin/postgres`.

## Closing Evidence

- `scripts/prepare-native-pglite-link.sh --build-postgres` installs the pinned
  PostgreSQL/PGlite prefix and records the prefix paths in the native link
  manifest.
- `scripts/package-native-plugin-release.sh` stages that prefix as
  `postgres/`, prunes build-only `postgres/include`, writes the canonical prefix
  paths into bundle metadata, and runs the package doctor before archive
  creation.
- `scripts/doctor-native-plugin-package.py --self-test` extracts the final
  package, opens the plugin by package directory, initializes a missing data
  directory through the bundled prefix, resumes it from a later process, and
  creates the packaged extension parity set.
- `scripts/test-doctor-native-plugin-package.py` covers canonical prefix-layout
  enforcement and build-machine absolute path rejection in packaged prefix text
  metadata.
- `scripts/preflight-native-plugin-release.sh v0.1.0` passed on macOS on
  2026-05-21 through prefix initialize/resume conformance, package smoke, and
  final package doctor self-test.
- `scripts/preflight-linux-smolvm.sh 0.1.0` previously passed the same packaged
  prefix self-test in the Ubuntu `24.04` baseline.

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
- The generated prefix now includes `pg_config` and the PGXS makefile substrate
  (`lib/pgxs/src/Makefile.global` and `lib/pgxs/src/makefiles/pgxs.mk`) so
  fetched PGlite `other_extensions` can build against the same native prefix
  rather than a host PostgreSQL installation. The package doctor treats those
  files as required prefix contents.
- The macOS preflight prefix now includes the full materialized PGlite
  `other_extensions` runtime surface, including extension control/SQL files,
  loadable modules, PostGIS companion controls, and `share/proj` projection
  data. The package doctor self-test creates those extensions from the packaged
  prefix, so the remaining prefix closure is target parity and continued strict
  relocatability rather than proving the macOS prefix contains only core
  runtime files.
- The Ubuntu `smolvm` preflight now passes the same packaged-prefix path on
  Linux: native preflight packages the generated `postgres/` prefix, strict
  dependency diagnostics accept it after Linux RUNPATH repair, and the package
  doctor self-test initializes/resumes clusters and creates the full extension
  parity set from the final package.
- The package doctor now scans packaged PostgreSQL prefix text metadata,
  including extension `.control` and `.sql` files, for build-machine absolute
  paths. In strict/preflight or production mode those leaks are package errors;
  `scripts/test-doctor-native-plugin-package.py` pins both control-file and SQL
  leak failures.
- The package doctor now enforces the canonical `postgresPrefix` bundle layout
  exactly instead of accepting any existing relative path map. That makes the
  package contract self-diagnosing when macOS and Linux packaging drift apart.
- Runtime packages now prune `postgres/include` after staging the generated
  PostgreSQL prefix. Server headers are build inputs for extension compilation,
  not runtime prefix artifacts, and pruning them keeps Emscripten-port headers
  out of native binary packages without weakening the native-only payload
  doctor check.
