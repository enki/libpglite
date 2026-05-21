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

## Remaining Closure Criteria

- Every release-critical gate emits structured diagnostics copied into the
  package, including source provenance, patches, platform floor, dependency
  prefix, extension inventory, backend exports, public ABI, lifecycle, and
  conformance results.
- The package doctor validates those diagnostics against the actual packaged
  plugin and prefix without source checkout access.
- The doctor has regression tests for stale, missing, malformed, and
  contradicted diagnostics across source provenance, symbols, dependencies,
  extension inventory, lifecycle, and conformance.
- Linux dependency diagnostics use the same JSON schema as macOS with
  platform-specific tool details captured as data.
- Production packaging and strict preflight fail when any release-critical
  diagnostic is absent or contradicted by the artifact.
- Preflight fails before build work starts if the documented downstream patch
  set no longer applies cleanly to the pinned PGlite source; patch checksums in
  package diagnostics are useful only if the patch substrate itself is
  reproducible.

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
  - `diagnostics/platform-baseline.json`
  - `diagnostics/native-dependency-prefix.json` when the native link manifest
    was built from a controlled dependency prefix
  - `diagnostics/source-provenance.json`
  - `diagnostics/runtime-lifecycle.json`
- `dependencies.txt` is generated with `otool -L` on macOS and `ldd` on Linux.
  This currently exposes remaining relocatability weaknesses, including absolute
  build paths, instead of hiding them.
- `dependencies.json` records the same dependency scan as structured release
  data. Each scanned plugin or extension module records the platform tool, exit
  code, object path, and dependency classifications. The package doctor rejects
  missing, unknown, build-machine, local-provider, or external dependencies in
  strict/production mode, and it rejects dependency diagnostics whose recorded
  platform does not match the package target or whose tool is not the native
  scanner for that platform (`otool -L` for `Darwin`, `ldd` for `Linux`).
- Dependency-prefix diagnostics are optional only for the current host-pkg-config
  development lane. Production packages must carry `diagnostics.dependencyPrefix`
  sourced from `native_dependency_prefix_manifest` in the native link manifest;
  packaging fails before bundle writing if that manifest claim is absent. When
  present, build provenance must name the prefix diagnostic and the package
  doctor requires it to be a complete
  `libpglite-native-dependency-prefix-v1` manifest.
- The dependency-prefixed macOS prepare path now writes a native link manifest
  that names the controlled prefix, the copied prefix diagnostic, and the
  diagnostic SHA-256. That proves the prepare stage can make a falsifiable
  dependency claim, but this ADR stays open until production packages require
  and doctor-check the same claim from the final artifact.
- Native preflight now enters packaging through that dependency-prefixed
  manifest path by default, so the final package doctor sees the controlled
  prefix diagnostic during the normal macOS release gate instead of only in a
  manual smoke command.
- `scripts/test-doctor-native-plugin-package.py` now pins the production rule:
  a package with `releaseMode=production` and no `diagnostics.dependencyPrefix`
  is rejected by the doctor.
- `scripts/test-package-native-plugin-release.py` also pins the packaging-side
  production rule: once root ADRs are closed, production packaging cannot
  assemble a bundle unless the native link manifest names the controlled
  dependency-prefix diagnostic.
- The macOS `v0.1.0` preflight passed with that default path. The produced
  package contains `diagnostics/native-dependency-prefix.json`, the bundle
  manifest references it, and the strict package doctor/self-test accepted the
  final `.tar.zst` artifact.
- Native dependency sources now have a structured
  `libpglite-native-dependency-sources-v1` fetch manifest with archive hashes
  and exact git commits. That manifest is build-stage evidence today; it should
  become package diagnostic input when the controlled dependency prefix becomes
  the release path.
- Native dependency-prefix diagnostics now record whether the prefix is purely
  static and list any dynamic objects found under the prefix. Release-style
  prefix checks use `--require-static`, so a complete prefix descriptor cannot
  hide accidental `.dylib`, `.bundle`, `.so`, or `.so.*` outputs.
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
- The `raw-protocol` conformance JSON now includes a required `cases` inventory
  for the protocol behaviors it covers. The package doctor rejects stale or
  partial raw-protocol diagnostics that omit any required case, so a passing
  conformance file cannot hide a narrowed test command. The required case list
  now includes the empty-query path so PostgreSQL `EmptyQueryResponse` behavior
  is part of the named raw protocol evidence.
- Conformance result JSON records a SHA-256 checksum of its log, and the package
  doctor verifies the checksum. This prevents stale or mismatched logs from
  satisfying a release diagnostic gate.
- Conformance result JSON must also record the invoked command and well-formed
  UTC `startedAt`/`endedAt` timestamps, and the package doctor rejects results
  whose end time predates the start time. This keeps release evidence
  self-diagnosing when a packaged-artifact check is copied, truncated, or stale.
- The package doctor now checks the recorded command against the expected
  release-gate fragments for `raw-protocol`, `tokio-postgres-client`,
  `prefix-initialize`, and `prefix-resume`. A passing conformance JSON from an
  unrelated or narrowed command cannot satisfy the final package evidence.
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
- Linux native preflight now reaches that strict doctor gate. The current
  failure is diagnostic, not opaque: dependency manifests classify
  build-prefix `libpq.so.5` references from modules such as `dblink` and
  `postgres_fdw`, plus unresolved/static-unknown module dependencies, and the
  strict doctor rejects the package before it can be treated as releasable.
- Linux dependency diagnostics now ignore `ldd`'s `statically linked` marker
  for modules that have no dynamic dependencies, because that line is an absence
  of dependencies rather than an unresolved dependency. Real missing,
  build-machine, or absolute-external dependencies remain strict failures.
- After Linux RUNPATH repair, `scripts/preflight-linux-smolvm.sh 0.1.0` passes
  the strict package doctor and final-artifact self-test in the Ubuntu baseline.
  That gives this ADR Linux schema evidence for the current diagnostics, while
  production enforcement still depends on closing the remaining release-gating
  ADRs.
- The doctor now cross-checks inventoried `contrib` extensions against packaged
  control files, default-version SQL, and referenced native modules. It also
  makes missing PGlite `other_extensions` production-fatal while keeping them
  visible as development warnings. The inventory now records each
  `other_extensions` gitlink commit and submodule URL, and the doctor fails any
  package whose PGlite extension provenance is unknown.
- Doctor regression tests now cover present PGlite `other_extensions` with
  missing control files, missing default-version install SQL, missing referenced
  native modules, missing PostGIS projection data, and unreadable packaged
  PostGIS projection databases. That gives the final-artifact diagnostics
  concrete failure modes instead of relying only on a green package smoke.
- Dependency diagnostics now treat loader-relative parent traversal as a
  package error. That keeps `@loader_path` and `$ORIGIN` entries from being
  accepted as relocatable when they can escape the final package layout.
- The materialization step for PGlite `other_extensions` consumes the same
  inventory that is packaged as diagnostics. This keeps fetch/build inputs and
  package claims tied to one generated source of truth instead of separate
  handwritten extension lists.
- The doctor now distinguishes missing source from present source for PGlite
  `other_extensions`: missing materialized sources remain development warnings
  and production failures, while present sources must also have their packaged
  control files, install SQL, and referenced native modules.
- The macOS controlled-prefix prepare has now exercised that distinction in the
  normal preflight path: all eight pinned `other_extensions` are materialized
  and the full set, including PostGIS, is installed into the generated prefix, so
  the inventory moves from `status=missing` to `status=present` with concrete
  files behind the claim. `scripts/check-native-other-extension-build.sh` still
  provides a focused build-stage proof, and normal macOS preflight now carries
  the same claims into the package doctor.
- The packaged-artifact doctor self-test now validates full-extension runtime
  evidence from the final archive. It extracts the `.tar.zst`, loads the
  packaged plugin and bundled prefix, and runs the dynamic-plugin sweep that
  creates `age`, `pg_hashids`, `pg_ivm`, `pg_textsearch`, `pg_uuidv7`, `pgtap`,
  `postgis`, and `vector` from the packaged files.
- Native prepare and package diagnostics now record the failure mode that the
  full parity sweep exposed: backend export manifests must include common data
  symbols, and packaged `plpgsql` must be linked with dynamic lookup semantics.
  Static regression tests assert both rules so future generated artifacts fail
  early instead of producing a package that only fails when an extension loads.
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
- The doctor now has focused conformance-diagnostic regression coverage for
  missing result files, failed status/exit-code claims, and stale log checksums
  in a production `runtime-ready` package shape.
- The doctor now parses `build-provenance.txt` and compares it with the bundle
  manifest. Target, release version, release mode, runtime status, libpglite git
  commit, plugin filename/checksum, packaged diagnostic filenames for every
  release-critical diagnostic, timestamp shape, Rust toolchain block, C
  compiler block, and `uname` must be present and current.
- Packaging now records every release-critical diagnostic path in
  `build-provenance.txt`, including public ABI symbols, backend exports,
  dependency text/json, source provenance, lifecycle, and conformance results.
  The doctor rejects provenance that points at stale or partial diagnostic
  filenames rather than the bundle's current package claims.
- The package doctor validates the `releaseMode`/`runtimeStatus` pair directly:
  development artifacts cannot claim `runtime-ready`, and production artifacts
  cannot carry the pending runtime status.
- The doctor now cross-checks `source-provenance.json` against the native link
  manifest for pinned `postgres-pglite` repository/ref/commit, patch
  fingerprint, patch list, and per-patch SHA-256 values. The patch fingerprint
  must also be a full SHA-1-shaped value. A stale source provenance file can no
  longer satisfy the package diagnostic gate by carrying only plausible-looking
  checksums.
- Doctor regression tests now corrupt each structured JSON diagnostic family
  and assert explicit failures: dependency manifest, platform baseline, source
  provenance, dependency prefix, runtime lifecycle, and conformance result
  files. This keeps malformed release diagnostics from being treated as missing
  optional data or plausible text.
- The package doctor now also treats malformed extension-inventory lines,
  unnamed inventoried extensions, and unknown inventory entry types as
  diagnostic errors instead of allowing malformed text to crash or be silently
  ignored.
- Packaging now writes `diagnostics/platform-baseline.json` and the doctor
  validates it as a package claim. The diagnostic must match the bundle target.
  It must also carry nonempty observed `system` and `machine` fields. Linux
  packages must record the Ubuntu `24.04` baseline, matching `/etc/os-release`,
  plus a nonempty `ldd --version` line; packaging rejects a mismatched Linux
  distro/version before the package is written. Build provenance records the
  selected Linux baseline too. macOS packages record the deployment target from
  the native link manifest, and the doctor rejects mismatches between the
  manifest, build provenance, and platform baseline diagnostic.
- Native prepare now runs `git apply --check` before applying each downstream
  source patch to the archived pinned source. That makes patch reproducibility
  an explicit preflight gate, so source provenance, patch fingerprints, and
  build inputs cannot drift apart silently. The patched-source cache fingerprint
  also records the patch application method, so tightening patch validation
  forces a fresh patched tree instead of trusting an older cache. Because the
  patched source can live under this repository's `target/`, prepare sets a Git
  ceiling directory while applying patches so `git apply` operates on the
  archived source tree rather than silently walking up to this repository.
- The ADR closure audit now also verifies that every focused
  `scripts/test-*.py` regression is wired into native release preflight. Python
  unittest discovery does not load these hyphenated filenames by default, so the
  audit makes the explicit preflight list a checked contract rather than a
  fragile convention.
- This ADR remains open until diagnostics are generated as structured release
  data across all required gates and production packaging rejects stale
  diagnostics.
