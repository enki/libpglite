# ADR-0004: Runtime-Ready Release Gate

Status: Open
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

The package manifest must carry an explicit runtime status. While ADR-0002 is
open, package tooling may produce smoke-test artifacts, but those artifacts must
not be documented or published as a usable PGlite database runtime.

Runtime-ready release status requires:

- native PGlite/Postgres runtime linked into the plugin
- temporary data directory open/resume tested
- PostgreSQL startup packet tested
- simple query and extended query tested through the runtime boundary
- transaction success and rollback tested
- protocol error recovery tested
- deterministic shutdown tested
- high-level Rust PostgreSQL client transport tested

The macOS native preflight now covers much of this list through raw protocol
tests,
including startup, simple query, a basic extended-query flow, transaction
rollback, protocol error recovery, contrib extension loading, and deterministic
shutdown. ADR-0003 also adds a `tokio-postgres` transport check against the real
native plugin. The macOS package doctor now self-tests the final archive and
creates the materialized PGlite `other_extensions` set from the packaged prefix.
Runtime-ready status remains blocked on broader conformance, Linux coverage, and
the fact that root ADRs are still open. ADR-0011 now closes the first lifecycle
contract as single-start-per-process; deterministic same-process restart is a
future widening of that contract, not a production release prerequisite.

The package doctor now owns the packaged-artifact runtime smoke through
`--self-test`, and native preflight runs that mode against the final archive.
That is still a development/preflight gate, but it moves the release boundary in
the intended direction: runtime readiness must be proven from the artifact that
would ship, not only from build-tree outputs.

Production packaging now fails while any root `docs/ADR-*.md` remains open.
This makes the ADR process itself part of the release gate: an artifact cannot
claim `runtime-ready` status until every release-gating ADR has been honestly
moved to `docs/done/` and the remaining package diagnostics pass.

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

## Remaining Closure Criteria

- All root `docs/ADR-*.md` records have moved to `docs/done/` with their own
  acceptance evidence intact.
- Production packaging fails while any release-gating ADR remains open, for
  example:
  `LIBPGLITE_RELEASE_MODE=production scripts/package-native-plugin-release.sh <version> <target>`.
- Once the other release-gating ADRs are done, production packaging sets
  `runtimeStatus=runtime-ready` only after native preflight has produced passing
  packaged-artifact conformance diagnostics.
- The package doctor rejects missing, stale, or failing conformance diagnostics
  for a production runtime-ready package.
