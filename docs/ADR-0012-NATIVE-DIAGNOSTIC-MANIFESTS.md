# ADR-0012: Native Diagnostic Manifests

Status: Open
Date: 2026-05-21

## Context

The native build crosses several fragile boundaries: pinned PGlite/Postgres
source, downstream patches, backend exported symbols, dynamically loaded
extension modules, third-party native dependencies, runtime lifecycle, and
PostgreSQL protocol conformance.

A green build is not enough evidence. When a native package fails on a user's
machine, the package should be able to explain what it contains, what it was
built from, what it expects to load, and which conformance gates proved it.

## Decision

`libpglite` native releases must be self-diagnosing. Every package should carry
machine-readable diagnostic manifests that describe the native artifact and the
checks that admitted it.

The diagnostic surface should include:

- pinned `postgres-pglite` source identity
- applied patch list and patch checksums
- native build platform and toolchain identity
- backend export symbol manifest
- public plugin ABI symbol manifest
- extension inventory with required control files, libraries, and data files
- third-party dependency closure and install names or rpaths
- runtime lifecycle contract
- conformance gate results, including raw protocol and high-level client checks

The diagnostic files are release metadata, not optional debug notes. Production
packaging should fail if required manifests are missing or inconsistent with the
actual plugin and prefix contents.

## Required Work

1. Define a stable `diagnostics/` layout inside packaged native artifacts.
2. Generate a build provenance manifest from the pinned source, patch directory,
   Rust toolchain, C compiler, platform, and configure flags.
3. Generate symbol manifests from the built plugin and compare them with the
   expected ABI/backend export sets during preflight.
4. Generate dependency manifests from platform-native tools (`otool -L` on
   macOS, `ldd`/`readelf` on Linux) and fail if build-machine paths remain in
   production packages.
5. Promote the existing extension inventory into packaged diagnostics and check
   that every listed extension has its expected control/SQL/module files.
6. Write conformance results as structured files, with one process-level result
   per runtime mode so crashes or lifecycle failures are attributable.
7. Add a `libpglite doctor`-style command or script that validates a packaged
   plugin directory without rebuilding it.
8. Treat unknown extension provenance as a diagnostic failure: every PGlite
   `other_extensions` entry must carry a source path, pinned submodule commit,
   source URL, and present/missing status.

## Acceptance Criteria

- A packaged native artifact can be inspected without source checkout access.
- A failed user environment can report source version, patch set, platform,
  exported symbols, extension inventory, dependency paths, and conformance gate
  status from files in the package.
- Production packaging fails if diagnostic manifests are absent, stale, or
  contradicted by the actual artifact.
- Production packaging fails if the extension inventory cannot identify the
  exact PGlite extension source commits it is supposed to build.
- Raw protocol and high-level client checks are recorded separately so the
  current single-start lifecycle cannot hide which runtime mode failed.
- Linux diagnostics use the same schema as macOS diagnostics, even if the
  platform-specific dependency tools differ.
- This ADR moves to `docs/done/` only after the package doctor owns all
  release-critical manifest checks that preflight depends on.

## Implementation Notes

- Development packaging now includes a top-level `diagnostics/` directory inside
  the native binary archive.
- The bundle manifest points at:
  - `diagnostics/build-provenance.txt`
  - `diagnostics/native-link-manifest.txt`
  - `diagnostics/extension-inventory.txt`
  - `diagnostics/plugin-defined-symbols.txt`
  - `diagnostics/backend-export-symbols.txt`
  - `diagnostics/dependencies.txt`
  - `diagnostics/dependencies.json`
  - `diagnostics/source-provenance.json`
  - `diagnostics/runtime-lifecycle.json`
- `dependencies.txt` is generated with `otool -L` on macOS and `ldd` on Linux.
  This currently exposes remaining relocatability weaknesses, including absolute
  build paths, instead of hiding them.
- `dependencies.json` records the same dependency scan as structured release
  data. Each scanned plugin or extension module records the platform tool, exit
  code, object path, and dependency classifications. The package doctor rejects
  missing, unknown, build-machine, local-provider, or external dependencies in
  strict/production mode.
- `scripts/doctor-native-plugin-package.py` validates either an extracted
  package directory or a `.tar.zst` package without rebuilding it. It checks the
  bundle manifest, plugin checksum, ABI symbols, PostgreSQL prefix files,
  diagnostic manifests, extension control files, and dependency report shape.
- Packaging now runs the doctor against the staged native package before writing
  the binary archive, and preflight runs it again against the archive.
- Native preflight now writes structured conformance diagnostics under
  `diagnostics/conformance/`. The initial result set records `raw-protocol` and
  `tokio-postgres-client` as separate JSON files with logs, preserving the
  process-level attribution required by the current single-start lifecycle
  contract. It also records `prefix-initialize` and `prefix-resume` results to
  prove the packaged Postgres prefix can initialize a missing data directory and
  later reopen the initialized cluster from a fresh process.
- Conformance result JSON records a SHA-256 checksum of its log, and the package
  doctor verifies the checksum. This prevents stale or mismatched logs from
  satisfying a release diagnostic gate.
- Packaging requires `LIBPGLITE_CONFORMANCE_DIR` so native artifacts are tied to
  explicit runtime evidence instead of only console output. The doctor validates
  that both required conformance results passed.
- In development mode the doctor warns about build-machine dependency paths. In
  production mode, or with `--strict-relocatable`, those paths are hard failures.
- Native preflight now runs the doctor in strict relocatability mode and then
  uses the doctor's `--self-test` mode to extract the final package and run the
  native raw protocol/contrib smoke against the packaged plugin and packaged
  Postgres prefix. This keeps final-artifact runtime validation in the artifact
  doctor instead of duplicating one-off extraction logic in preflight.
- The doctor now cross-checks inventoried `contrib` extensions against packaged
  control files, default-version SQL, and referenced native modules. It also
  makes missing PGlite `other_extensions` production-fatal while keeping them
  visible as development warnings. The inventory now records each
  `other_extensions` gitlink commit and submodule URL, and the doctor fails any
  package whose PGlite extension provenance is unknown.
- The materialization step for PGlite `other_extensions` consumes the same
  inventory that is packaged as diagnostics. This keeps fetch/build inputs and
  package claims tied to one generated source of truth instead of separate
  handwritten extension lists.
- The doctor now distinguishes missing source from present source for PGlite
  `other_extensions`: missing materialized sources remain development warnings
  and production failures, while present sources must also have their packaged
  control files, install SQL, and referenced native modules.
- The package now includes a structured runtime lifecycle diagnostic. The doctor
  validates that it matches the current single-start-per-process contract and
  cites raw protocol conformance as evidence.
- The native link manifest and packaged diagnostics now include structured
  source provenance for the pinned `postgres-pglite` repository/ref/commit and
  SHA-256 checksums for every downstream patch. The doctor validates that the
  packaged source provenance and native link manifest agree on the patch set.
- The doctor now treats symbol diagnostics as package claims rather than
  presence checks: `plugin-defined-symbols.txt` must match the actual plugin
  exports, `backend-export-symbols.txt` must match the native link manifest, and
  every recorded backend export must be present in the packaged plugin. Preflight
  runs regression tests for these stale-diagnostic failures.
- The doctor now parses `build-provenance.txt` and compares it with the bundle
  manifest. Target, release version, release mode, runtime status, libpglite git
  commit, plugin filename/checksum, packaged diagnostic filenames, timestamp
  shape, Rust toolchain block, C compiler block, and `uname` must be present and
  current.
- The doctor now cross-checks `source-provenance.json` against the native link
  manifest for pinned `postgres-pglite` repository/ref/commit, patch
  fingerprint, patch list, and per-patch SHA-256 values. A stale source
  provenance file can no longer satisfy the package diagnostic gate by carrying
  only plausible-looking checksums.
- This ADR remains open until diagnostics are generated as structured release
  data across all required gates and production packaging rejects stale
  diagnostics.
