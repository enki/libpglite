# ADR-0009: Native Dependency Prefix

Status: Open
Date: 2026-05-21

## Context

The pinned PGlite WASM build does not rely on arbitrary host libraries. Its
builder creates a controlled dependency prefix under `/install/libs`, containing
static builds of zlib, libxml2, libxslt, OpenSSL, OSSP uuid, json-c, SQLite,
PROJ, GEOS, and associated runtime data. PostgreSQL and PGlite extensions are
then configured and linked against that prefix.

The native build should copy that architecture, except that outputs are native
Mach-O or ELF artifacts instead of Emscripten modules. Using Homebrew or system
libraries is acceptable for early macOS bring-up, but it is not a release
contract because it produces non-reproducible and non-relocatable bundles.

## Decision

`libpglite` will grow a reproducible native dependency prefix for each supported
target. The prefix is the native equivalent of PGlite's `/install/libs`.

The dependency prefix is an input to the native Postgres/PGlite build, the
extension build, the native link manifest, and the release package. Release
artifacts must not depend on developer-machine absolute paths for extension
libraries or runtime data.

## Required Work

1. Define the native dependency inventory and versions from the pinned PGlite
   builder inputs.
2. Build or vendor the dependency prefix reproducibly for macOS first, then
   Linux.
3. Configure the pinned Postgres fork with include and library paths from that
   prefix.
4. Feed extension-specific module link flags from that prefix, including
   `pgcrypto` OpenSSL inputs and PostGIS GEOS/PROJ/json-c/SQLite inputs.
5. Package runtime libraries and data files from the dependency prefix into the
   native release bundle.
6. Rewrite or constrain install names, rpaths, and data paths so the packaged
   bundle is relocatable.
7. Record dependency versions, build inputs, linked libraries, and packaged data
   roots in the native link manifest and release metadata.
8. Add preflight checks that fail when a built plugin or extension resolves to a
   dependency outside the release bundle, except for platform system libraries.

## Acceptance Criteria

- A clean macOS machine can build the native dependency prefix from pinned
  inputs.
- The native plugin and extension modules link against the controlled prefix, not
  against arbitrary Homebrew paths, for release builds.
- `CREATE EXTENSION pgcrypto` works without requiring OpenSSL to be installed
  separately.
- `CREATE EXTENSION postgis` works with bundled GEOS, PROJ, json-c, SQLite, and
  projection data.
- Release preflight rejects absolute build-machine dependency paths in plugin,
  extension, and metadata artifacts.

## Remaining Closure Criteria

- `scripts/prepare-native-pglite-link.sh --dependency-prefix <prefix>
  --build-postgres` is the release path, and the resulting manifest records a
  complete `libpglite-native-dependency-prefix-v1` diagnostic.
- `pgcrypto` links against the controlled OpenSSL inputs and works from the
  packaged artifact without an external OpenSSL install.
- PostGIS links against controlled GEOS, PROJ, json-c, SQLite, and related
  dependency/data inputs and works from the packaged artifact.
- Strict package diagnostics reject host-provider, build-machine, external, or
  unresolved dependency paths in plugin and extension modules.
- The same prefix contract is implemented for the Linux baseline after macOS is
  closed.

## Closed Evidence

- A clean macOS command builds the full pinned dependency prefix from
  `deps/native-pglite-dependencies.json` without relying on Homebrew libraries
  as link inputs. Homebrew autotools are still build tools for regenerated
  autotools projects.
- The macOS full-prefix descriptor is complete and static-only:
  `complete=true`, `staticOnly=true`, `missing=[]`, and `dynamicObjects=[]`.

## Implementation Notes

- This ADR owns the dependency prefix. ADR-0007 owns the Postgres runtime prefix.
  ADR-0008 owns extension parity and consumes both prefixes.
- Early macOS bring-up may continue to use `pkg-config` against Homebrew to
  validate build mechanics, but that mode must remain marked as non-release.
- `deps/native-pglite-dependencies.json` records the native dependency inventory
  copied from the pinned PGlite WASM builder: zlib, libxml2, libxslt, OpenSSL,
  OSSP uuid, json-c, libdeflate, libtiff, SQLite, PROJ, and GEOS, with versions,
  source URLs, archive SHA-256 values or exact git commits, expected
  headers/libraries, pkg-config names, and consuming roles.
- `scripts/fetch-native-dependency-sources.py` is the first stage of the native
  prefix build: it fetches archives with checksum verification, checks out git
  dependencies at exact commits, and writes
  `libpglite-native-dependency-sources-v1`. The PGlite upstream zlib and OSSP
  uuid URLs currently require fallback mirrors because their historical primary
  URLs are not reliably fetchable, but the content hashes keep those fallbacks
  pinned.
- `scripts/build-native-dependency-prefix.sh` is the compile-stage entrypoint
  for the native equivalent of PGlite's `/install/libs`. It consumes the fetched
  source manifest, builds into an isolated prefix, and finishes by running the
  prefix descriptor. The script mirrors the PGlite build order and can run
  focused smoke builds with `--only <name>` while the full macOS prefix is being
  brought up.
- macOS focused smoke builds have proven every locked dependency slice:
  `zlib`, `libxml2`, `libxslt`, OpenSSL, OSSP uuid, json-c, libdeflate,
  libtiff, SQLite, PROJ, and GEOS. The OpenSSL native path uses `no-module` in
  addition to `no-shared` so the prefix does not silently acquire a loadable
  `legacy.dylib`.
- A clean macOS full-prefix smoke run now builds the entire pinned inventory
  from `deps/native-pglite-dependencies.json` into an isolated prefix and emits
  a complete `libpglite-native-dependency-prefix-v1` descriptor. The descriptor
  for that run reports `complete=true`, `staticOnly=true`, `missing=[]`, and
  `dynamicObjects=[]`.
- The compile-stage descriptor now records dynamic objects under the prefix and
  `--require-static` rejects `.dylib`, `.bundle`, `.so`, and `.so.*` outputs.
  Full prefix builds and dependency-prefix release prepares use that stricter
  gate so accidental dynamic dependency leakage cannot satisfy this ADR's prefix
  evidence.
- The macOS dependency-prefix path currently requires GNU autotools from
  Homebrew for the PGlite-aligned libxml2/libxslt/libtiff `autogen.sh` path.
  That is an acceptable source-build prerequisite, not a release link provider.
  Release artifacts must still link against the controlled prefix outputs.
- The Darwin dependency compile flags include
  `-Werror=unguarded-availability-new`, and SQLite's generated
  `HAVE_STRCHRNUL` setting is forced off after configure because the macOS 15
  SDK exposes `strchrnul` even when `MACOSX_DEPLOYMENT_TARGET=11.0`. This turns
  deployment-floor leaks into prefix build failures instead of warnings.
- `scripts/describe-native-dependency-prefix.py` validates a native dependency
  prefix against that inventory and writes
  `libpglite-native-dependency-prefix-v1`. The native prepare step accepts
  `--dependency-prefix` / `LIBPGLITE_NATIVE_DEPENDENCY_PREFIX`; when provided it
  requires a complete prefix, uses that prefix's pkg-config directory, switches
  UUID handling to the PGlite-aligned OSSP provider, fingerprints the prefix
  manifest, and records it in the native link manifest.
- macOS development packaging now repairs the staged package rather than the
  build output: plugin and extension install names are rewritten to package-local
  `@rpath`/`@loader_path` references, `libpq` references are made package-local,
  and `pgcrypto` carries a bundled `libcrypto.3.dylib` copied into
  `postgres/lib`.
- The package doctor now runs with `--strict-relocatable` in preflight, so
  dependency diagnostics containing build-machine paths fail the package gate.
- Native packages now carry both the raw platform dependency report
  (`dependencies.txt`) and a structured dependency manifest (`dependencies.json`)
  that classifies each object dependency as package-local, platform,
  loader-relative, local-provider, build-machine, missing, unknown, or external.
  The doctor fails strict/preflight packages on non-relocatable or unresolved
  classifications instead of relying only on text matching.
- If a native link manifest was built with a dependency prefix, packaging carries
  `diagnostics/native-dependency-prefix.json`, build provenance names it, and
  the package doctor requires that prefix diagnostic to be complete. This keeps
  future release artifacts from losing their dependency-prefix evidence while
  still allowing today's host-pkg-config development build to remain explicit.
- Preflight extracts the final `.tar.zst` package and runs the native raw
  protocol/contrib smoke against the packaged plugin and packaged Postgres
  prefix. This verifies that the repaired install names work behaviorally, not
  just textually.
- This is still not the final dependency-prefix implementation: the checked-in
  inventory, source fetcher, compile-stage entrypoint, and prefix descriptor
  define the contract, and the clean macOS full-prefix smoke now passes. The
  remaining closure work is making this prefix the default release link path,
  proving `pgcrypto` and PostGIS from the packaged artifact, and then repeating
  the prefix contract on Linux. OpenSSL is still copied from the local provider
  for default macOS development packaging.
- PGlite's WASM build extracts export-symbol lists from dependency archives for
  Emscripten. Native builds do not need the same files verbatim, but they do
  need equivalent link/export discipline for extension module loading.
- PostGIS projection data is part of the runtime dependency payload, not a
  documentation or optional data add-on.
