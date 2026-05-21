# libpglite Design Records

This directory records the architecture needed to make PGlite consumable as a
Rust-hosted native dynamic library.

Done records:

- `done/ADR-0001-RUST-FACADE-AND-DYNAMIC-PLUGIN.md`
- `done/ADR-0002-NATIVE-PGLITE-BUILD-LANE.md`
- `done/ADR-0003-POSTGRES-CLIENT-TRANSPORT.md`
- `done/ADR-0004-RUNTIME-READY-RELEASE-GATE.md`
- `done/ADR-0005-PGLITEC-NATIVE-PORTABILITY.md`
- `done/ADR-0006-NATIVE-BUILD-PLATFORM-FLOOR.md`
- `done/ADR-0007-NATIVE-INITDB-AND-PREFIX-ARTIFACT.md`
- `done/ADR-0008-NATIVE-EXTENSION-PARITY.md`
- `done/ADR-0009-NATIVE-DEPENDENCY-PREFIX.md`
- `done/ADR-0010-NATIVE-BACKEND-SYMBOL-CONTRACT.md`
- `done/ADR-0011-NATIVE-RUNTIME-LIFECYCLE.md`
- `done/ADR-0012-NATIVE-DIAGNOSTIC-MANIFESTS.md`

Policy records:

- `LINUX-RELEASE-POLICY.md`

## Closing Rules

An ADR moves to `docs/done/` only when its acceptance criteria are enforced by
repo commands, not just described in text. The minimum evidence is:

- implementation exists in the release path, not only in a local fixture
- a focused regression test covers the behavior or failure mode
- native preflight runs that test or an equivalent package-level doctor check
- packaged diagnostics carry enough data to debug stale or partial artifacts
- production packaging fails when the ADR's release contract is not satisfied

`scripts/audit-adr-closure.py` keeps the bookkeeping honest: root ADR files must
be `Status: Open`, done ADR files must be `Status: Done` with a `Closing
Evidence` section, open ADRs must carry non-empty acceptance and remaining
closure bullets, and this README must list every open and done record. Native
preflight runs that audit before package work starts.

Current closure frontier:

- No root ADRs are open. The current runtime-ready substrate is closed for the
  first native release scope.

What is now closed:

- ADR-0002, ADR-0005, ADR-0006, ADR-0007, ADR-0008, ADR-0009, ADR-0010, and
  ADR-0012 have been moved to `docs/done/` with `Closing Evidence` sections
  because their contracts are enforced by release-path scripts, focused
  regressions, package doctor checks, and macOS plus Ubuntu final-package
  evidence.
- ADR-0004 has also moved to `docs/done/` after a production-mode macOS package
  command wrote a `runtime-ready` archive and the generated archive passed
  strict package doctor/self-test.
- `scripts/preflight-linux-smolvm.sh 0.1.0` passed again on 2026-05-21 after
  the final package doctor self-test was extended to run the high-level
  `tokio-postgres` client from the extracted package.
- Adding new process documents is not required unless a new substrate weakness
  is found or the runtime contract is intentionally widened.
