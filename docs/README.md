# libpglite Design Records

This directory records the architecture needed to make PGlite consumable as a
Rust-hosted native dynamic library.

- `ADR-0002-NATIVE-PGLITE-BUILD-LANE.md`
- `ADR-0004-RUNTIME-READY-RELEASE-GATE.md`

Done records:

- `done/ADR-0001-RUST-FACADE-AND-DYNAMIC-PLUGIN.md`
- `done/ADR-0003-POSTGRES-CLIENT-TRANSPORT.md`
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

- ADR-0002: macOS release preflight now passes from the final package artifact
  with native Postgres/PGlite linked and full extension parity exercised. It
  now covers raw startup, simple query, empty query, transaction rollback,
  transaction commit, recoverable protocol error, basic extended query, and
  parameter-bound extended query, plus named prepared-statement reuse, and the
  raw-protocol conformance diagnostic must name those cases. It still needs
  broader protocol coverage before the native build lane is complete. The
  missing closure is not more packaging substrate; it is a final protocol and
  client conformance decision, followed by macOS and Ubuntu final-package
  preflights that record that decision in packaged diagnostics. The Ubuntu
  package path already proves the Linux final artifact for the current
  conformance set, and the package doctor rejects WASM, JavaScript,
  Emscripten-named, wasm2c-named, and bitcode payloads in native packages.
- ADR-0004: production packaging is now regression-tested to fail while root
  ADRs remain open, and the doctor has focused conformance-diagnostic failure
  regressions. The package script is also regression-pinned to run the package
  doctor before writing the distributable binary archive, and the doctor rejects
  contradictory `releaseMode`/`runtimeStatus` bundle claims. It still needs
  ADR-0002 closed first. After that, ADR-0004 is the final flip: production
  packaging must assemble a `runtime-ready` artifact only after packaged
  conformance diagnostics pass and the staged artifact passes the doctor before
  archive creation.

What is now closed:

- ADR-0005, ADR-0006, ADR-0007, ADR-0008, ADR-0009, ADR-0010, and ADR-0012 have
  been moved to `docs/done/` with `Closing Evidence` sections because their
  contracts are enforced by release-path scripts, focused regressions, package
  doctor checks, and macOS plus Ubuntu final-package evidence.
- The remaining work is intentionally narrow: finish the runtime conformance
  scope for ADR-0002, then perform the ADR-0004 production-ready packaging
  transition. Adding new process documents is not required for closure unless a
  new substrate weakness is found.
