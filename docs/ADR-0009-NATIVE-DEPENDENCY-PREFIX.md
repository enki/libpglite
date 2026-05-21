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

- Linux packaged-artifact conformance continues to prove `pgcrypto` and PostGIS
  work with controlled OpenSSL, GEOS, PROJ, json-c, SQLite, related dependency
  inputs, and projection data from the final package.
- Strict package diagnostics keep rejecting host-provider, build-machine,
  external, or unresolved dependency paths in plugin and extension modules, with
  regression coverage for both macOS and Linux package layouts.

## Closed Evidence

- A clean macOS command builds the full pinned dependency prefix from
  `deps/native-pglite-dependencies.json` without relying on Homebrew libraries
  as link inputs. Homebrew autotools are still build tools for regenerated
  autotools projects.
- The macOS full-prefix descriptor is complete and static-only:
  `complete=true`, `staticOnly=true`, `missing=[]`, and `dynamicObjects=[]`.
- On macOS, `scripts/prepare-native-pglite-link.sh --build-postgres
  --dependency-prefix <prefix>` completes against that prefix and writes a
  native link manifest naming `native_dependency_provider=libpglite-prefix`,
  the prefix path, the prefix diagnostic, and its SHA-256.
- That dependency-prefixed prepare builds and installs PostgreSQL `contrib`
  modules including `pgcrypto.dylib`, `uuid-ossp.dylib`, and `pgxml.dylib`
  without Homebrew dylib dependencies. `otool -L` reports only
  `/usr/lib/libSystem.B.dylib` for those modules.
- Native preflight now builds the pinned controlled dependency prefix and passes
  it to `scripts/prepare-native-pglite-link.sh --build-postgres` by default.
  Packaging already copies the resulting complete prefix diagnostic into the
  package and the doctor requires it to be complete when present.
- `scripts/preflight-native-plugin-release.sh v0.1.0` passed on macOS through
  that default path. The generated native link manifest records
  `native_dependency_provider=libpglite-prefix`, a complete static-only
  dependency-prefix diagnostic, and `macos_deployment_target=11.0`; the packaged
  artifact includes `diagnostics/native-dependency-prefix.json` and passed the
  strict package doctor/self-test.
- A macOS release preflight now materializes PGlite `other_extensions` and
  builds the full PGXS set against the same prefix, including `vector` and
  `postgis`. The PostGIS path uses prefix-local
  GEOS/PROJ/json-c/SQLite/libtiff/libdeflate/zlib/libxml2 inputs, forces static
  dependency closure through wrapper `geos-config` and `pkg-config` scripts,
  disables the PostGIS loader/raster scope for the native extension build, and
  copies `share/proj/proj.db` into the generated Postgres prefix.
- The macOS packaged artifact now proves `pgcrypto` and PostGIS from the final
  archive under strict package diagnostics. The package doctor extracts the
  `.tar.zst`, loads the packaged plugin and bundled Postgres prefix, runs the
  dynamic-plugin extension sweep, and accepts only relocatable dependency
  classifications. `otool -L` for the PostGIS modules reports platform
  libraries only after static closure through the controlled prefix.
- Strict package diagnostics now reject loader-relative dependency paths that
  contain parent-directory traversal, so `@loader_path` or `$ORIGIN` entries
  cannot satisfy relocatability by escaping the packaged layout.

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
- OSSP uuid is installed with both `include/uuid.h` and a prefix-local
  `include/ossp/uuid.h` wrapper. The wrapper keeps PostgreSQL's expected
  `<ossp/uuid.h>` include shape while avoiding Darwin's system `uuid_t`
  typedef collision by renaming the OSSP abstract type to `ossp_uuid_t`.
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
- The dependency-prefix builder accepts both the Homebrew/macOS `glibtoolize`
  command name and the GNU/Linux `libtoolize` command name. The Ubuntu baseline
  exposed this as an actual portability requirement during the first Linux
  prefix attempt, before any libpglite-specific C code was compiled.
- OSSP uuid carries stale autotools platform scripts that do not recognize the
  Ubuntu/aarch64 baseline used by `smolvm`. The dependency-prefix builder
  refreshes `config.guess` and `config.sub` from the host autotools install
  before configuring that source tree, and the builder regression test keeps
  that portability repair in place.
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
  classifications instead of relying only on text matching. The doctor also
  rejects dependency manifests whose recorded platform contradicts the package
  target, so a macOS scan cannot satisfy a Linux package or vice versa. It also
  rejects platform/tool mismatches (`Darwin` must be `otool -L`, `Linux` must be
  `ldd`), so a stale raw dependency scan cannot satisfy a structured manifest.
- If a native link manifest was built with a dependency prefix, packaging carries
  `diagnostics/native-dependency-prefix.json`, build provenance names it, and
  the package doctor requires that prefix diagnostic to be complete. This keeps
  future release artifacts from losing their dependency-prefix evidence while
  still allowing today's host-pkg-config development build to remain explicit.
- Production package diagnostics now require a controlled dependency-prefix
  diagnostic. Development packages may still exercise the explicit host-provider
  path, but a production package without `diagnostics.dependencyPrefix` is a
  package-doctor error.
- Preflight extracts the final `.tar.zst` package and runs the native raw
  protocol/contrib smoke against the packaged plugin and packaged Postgres
  prefix. This verifies that the repaired install names work behaviorally, not
  just textually.
- Linux packaging now applies the matching package-local repair with `patchelf`:
  the plugin gets an `$ORIGIN/postgres/lib` RUNPATH, and packaged PostgreSQL
  modules get an `$ORIGIN` RUNPATH so sibling libraries such as `libpq.so.5`
  resolve from the final package. The Ubuntu baseline installs `patchelf` as a
  release preflight prerequisite, and the strict package doctor now passes from
  the final Ubuntu package artifact.
- This is still not the final dependency-prefix implementation for all
  supported targets: the checked-in inventory, source fetcher, compile-stage
  entrypoint, and prefix descriptor define the contract, and the normal macOS
  and Ubuntu preflights now prove the full extension surface from the packaged
  artifact. The remaining closure work is keeping that strictness under
  regression.
- `docs/LINUX-RELEASE-POLICY.md` records the Linux controlled-prefix release
  contract: Ubuntu `24.04` is the current baseline, `patchelf` is required for
  release preflight, the plugin and PostgreSQL modules must use package-local
  RUNPATHs, and strict diagnostics reject host-provider, build-machine,
  absolute-external, missing, or unknown dependency classifications.
  `scripts/test-preflight-linux-smolvm.py` pins the policy to the local Ubuntu
  preflight lane so the policy cannot drift away from the executable path.
- PGlite's WASM build extracts export-symbol lists from dependency archives for
  Emscripten. Native builds do not need the same files verbatim, but they do
  need equivalent link/export discipline for extension module loading.
- PostGIS projection data is part of the runtime dependency payload, not a
  documentation or optional data add-on.
