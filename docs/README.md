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
- `done/ADR-0013-RETAINED-TOKIO-POSTGRES-SESSION.md`
- `done/ADR-0014-NATIVE-BACKEND-STDIO-LEDGER.md`

Open records:

- `ADR-0015-TOKIO-POSTGRES-SESSION-BACKEND-OUTPUT-DRAIN.md`
- `ADR-0016-SYMLINKED-HOST-BINARY-BUNDLED-PLUGIN-RESOLUTION.md`
- `ADR-0017-NATIVE-BACKEND-STDIN-SEALING.md`
- `ADR-0018-PRODUCT-HOST-BUNDLED-PLUGIN-DEFAULT.md`

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

- ADR-0015 is open for carrying captured backend output through retained
  `tokio-postgres` sessions. ADR-0014 stops terminal leakage and exposes runtime
  drains; ADR-0015 is the narrower follow-on needed so downstream durable
  providers can attach startup/protocol/shutdown backend output to their owning
  provider/test diagnostic ledgers after `connect_with_driver(...)`.
- ADR-0016 is open for symlinked host-binary bundled-plugin resolution. Runtime
  code now resolves raw and canonical executable-adjacent plugin directories, and
  focused tests pass; closure still needs native preflight/package-doctor
  coverage of the resolver behavior.
- ADR-0017 is open for sealing native backend stdin. Runtime code now redirects
  embedded PostgreSQL stdin to `/dev/null` under the stdio lease and focused
  tests prove startup does not block on a live inherited stdin pipe; closure still
  needs package/preflight proof from the rebuilt archive.
- ADR-0018 is open for product-host bundled-plugin defaults. Runtime code now
  routes `DynamicPgliteRuntime::open(...)` and `release::resolve_native_plugin()`
  through current-executable bundled resolution; closure still needs
  package/preflight proof of the product-facing default and missing-plugin
  diagnostic.

What is now closed:

- ADR-0002, ADR-0005, ADR-0006, ADR-0007, ADR-0008, ADR-0009, ADR-0010, and
  ADR-0012 have been moved to `docs/done/` with `Closing Evidence` sections
  because their contracts are enforced by release-path scripts, focused
  regressions, package doctor checks, and macOS plus Ubuntu final-package
  evidence.
- ADR-0004 has also moved to `docs/done/` after a production-mode macOS package
  command wrote a `runtime-ready` archive and the generated archive passed
  strict package doctor/self-test.
- ADR-0014 has moved to `docs/done/` after the ABI-2 dynamic plugin exposed a
  backend-output drain, focused native tests proved startup/shutdown output
  drains through the ledger, and native preflight rebuilt and packaged the
  plugin.
- `scripts/preflight-linux-smolvm.sh 0.1.0` passed again on 2026-05-21 after
  the final package doctor self-test was extended to run the high-level
  `tokio-postgres` client from the extracted package.
- Adding new process documents is not required unless a new substrate weakness
  is found or the runtime contract is intentionally widened.
