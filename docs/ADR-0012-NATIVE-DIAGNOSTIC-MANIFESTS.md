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

## Acceptance Criteria

- A packaged native artifact can be inspected without source checkout access.
- A failed user environment can report source version, patch set, platform,
  exported symbols, extension inventory, dependency paths, and conformance gate
  status from files in the package.
- Production packaging fails if diagnostic manifests are absent, stale, or
  contradicted by the actual artifact.
- Raw protocol and high-level client checks are recorded separately so the
  current single-start lifecycle cannot hide which runtime mode failed.
- Linux diagnostics use the same schema as macOS diagnostics, even if the
  platform-specific dependency tools differ.

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
- `dependencies.txt` is generated with `otool -L` on macOS and `ldd` on Linux.
  This currently exposes remaining relocatability weaknesses, including absolute
  build paths, instead of hiding them.
- `scripts/doctor-native-plugin-package.py` validates either an extracted
  package directory or a `.tar.zst` package without rebuilding it. It checks the
  bundle manifest, plugin checksum, ABI symbols, PostgreSQL prefix files,
  diagnostic manifests, extension control files, and dependency report shape.
- Packaging now runs the doctor against the staged native package before writing
  the binary archive, and preflight runs it again against the archive.
- In development mode the doctor warns about build-machine dependency paths. In
  production mode, or with `--strict-relocatable`, those paths are hard failures.
- This ADR remains open until diagnostics are generated as structured release
  data, production packaging rejects stale diagnostics, and conformance results
  are written as structured diagnostics.
