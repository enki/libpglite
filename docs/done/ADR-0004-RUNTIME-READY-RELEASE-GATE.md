# ADR-0004: Runtime-Ready Release Gate

Status: Done
Date: 2026-05-21

## Context

The facade, dynamic plugin ABI, and native plugin package layout can be built
before the native PGlite/Postgres runtime is complete. That is useful for
testing ABI and release mechanics, but it is dangerous if the resulting package
looks like a production-ready database runtime.

A plugin that loads, checks its ABI, and returns an honest "native runtime not
linked" initialization error is an ABI artifact. It is not a runtime-ready
PGlite release.

## Decision

Release metadata must distinguish ABI/package artifacts from runtime-ready
artifacts until the native runtime, extension, dependency, platform, and
lifecycle gates are complete.

The package manifest must carry an explicit runtime status. Development package
tooling may produce smoke-test artifacts, but those artifacts must not be
documented or published as a usable PGlite database runtime.

Runtime-ready release status requires:

- native PGlite/Postgres runtime linked into the plugin
- temporary data directory open/resume tested
- PostgreSQL startup packet tested
- simple query and extended query tested through the runtime boundary
- transaction success and rollback tested
- protocol error recovery tested
- deterministic shutdown tested
- high-level Rust PostgreSQL client transport tested

The macOS native preflight now covers this list through raw protocol tests,
including startup, simple query, empty query, transaction success and rollback,
recoverable protocol error, basic extended query, parameter-bound extended
query, named prepared-statement reuse, extension loading, and deterministic
shutdown. ADR-0003 adds a `tokio-postgres` transport check against the real
native plugin, and the package doctor now repeats that high-level client test
from the extracted final package. ADR-0011 closes the first lifecycle contract
as single-start-per-process; deterministic same-process restart is a future
widening of that contract, not a production release prerequisite.

The package doctor now owns the packaged-artifact runtime smoke through
`--self-test`, and native preflight runs that mode against the final archive.
That is still a development/preflight gate, but it moves the release boundary in
the intended direction: runtime readiness must be proven from the artifact that
would ship, not only from build-tree outputs.

Production packaging now fails while any root `docs/ADR-*.md` except this final
gate remains open. This makes the ADR process itself part of the release gate:
an artifact cannot claim `runtime-ready` status until every other
release-gating ADR has been honestly moved to `docs/done/` and the remaining
package diagnostics pass.
`scripts/test-package-native-plugin-release.py` runs the production packaging
command with a placeholder plugin and asserts that it fails before package
assembly while naming the still-open root ADRs.
The package doctor validates packaged conformance diagnostics directly, and
`scripts/test-doctor-native-plugin-package.py` now pins missing results, failing
status/exit codes, and stale log checksums as package errors.
The release packaging regression suite also pins the final boundary ordering:
`scripts/package-native-plugin-release.sh` must run the package doctor against
the staged binary package before writing the distributable `.tar.zst` archive.
The package doctor now rejects contradictory bundle metadata: production
packages must claim `runtimeStatus=runtime-ready`, and development packages must
not claim that status.

## Required Work

1. Keep package metadata explicit about `runtimeStatus`.
2. Add runtime conformance tests from ADR-0002.
3. Keep high-level client transport tests from ADR-0003 in native preflight.
4. Make release packaging fail for production release mode unless runtime-ready
   conformance has passed.
5. Document any ABI-only artifacts as development/preflight artifacts only.

## Acceptance Criteria

- A package manifest cannot be mistaken for a runtime-ready release while the
  native runtime is pending.
- Production release packaging fails unless runtime-ready conformance is proven.
- Development/preflight packaging remains available for ABI and packaging
  iteration.
- ADR-0004 moves to `docs/done/` only after runtime-ready release gating is
  enforced by commands, not by convention.

## Closure Criteria

- All root `docs/ADR-*.md` records have moved to `docs/done/` with their own
  acceptance evidence intact, except this final ADR while it is collecting its
  own production-package evidence.
- Production packaging sets `runtimeStatus=runtime-ready` only after native
  preflight has produced passing packaged-artifact conformance diagnostics and
  the staged artifact has passed the package doctor before archive creation.
- ADR-0004 closes last. Its final evidence must include a production-mode
  package command that is no longer blocked by open ADRs, writes a
  `runtime-ready` bundle, runs the staged package doctor before archive
  creation, and then passes the archive doctor/self-test from the generated
  `.tar.zst`.

## Closing Evidence

- All other release-gating ADRs have moved to `docs/done/` with their own
  `Closing Evidence` sections.
- `scripts/package-native-plugin-release.sh` blocks production packaging while
  any root ADR other than this final gate remains open, and
  `scripts/test-package-native-plugin-release.py` covers that failure mode.
- Production packaging writes `runtimeStatus=runtime-ready`; development
  packaging writes `runtimeStatus=native-runtime-development`; and
  `scripts/doctor-native-plugin-package.py` rejects contradictory
  `releaseMode`/`runtimeStatus` pairs.
- `scripts/package-native-plugin-release.sh` runs the package doctor against the
  staged binary package before writing the distributable archive, and the
  regression suite pins that ordering.
- The production command
  `LIBPGLITE_RELEASE_MODE=production LIBPGLITE_CONFORMANCE_DIR=<macOS preflight conformance> scripts/package-native-plugin-release.sh v0.1.0 target/release/liblibpglite_plugin_native.dylib dist/production-native-plugin`
  passed on macOS on 2026-05-21 and wrote the runtime-ready archive.
- `scripts/doctor-native-plugin-package.py --strict-relocatable --self-test
  dist/production-native-plugin/libpglite-plugin-native-v0.1.0-aarch64-apple-darwin.tar.zst`
  passed on 2026-05-21, including raw protocol/extension self-test,
  high-level `tokio-postgres` self-test, and bundled-prefix self-test from the
  extracted final package.
