# libpglite Design Records

This directory records the architecture needed to make PGlite consumable as a
Rust-hosted native dynamic library.

- `ADR-0002-NATIVE-PGLITE-BUILD-LANE.md`
- `ADR-0004-RUNTIME-READY-RELEASE-GATE.md`
- `ADR-0005-PGLITEC-NATIVE-PORTABILITY.md`
- `ADR-0006-NATIVE-BUILD-PLATFORM-FLOOR.md`
- `ADR-0007-NATIVE-INITDB-AND-PREFIX-ARTIFACT.md`
- `ADR-0008-NATIVE-EXTENSION-PARITY.md`
- `ADR-0009-NATIVE-DEPENDENCY-PREFIX.md`
- `ADR-0010-NATIVE-BACKEND-SYMBOL-CONTRACT.md`
- `ADR-0012-NATIVE-DIAGNOSTIC-MANIFESTS.md`

Done records:

- `done/ADR-0001-RUST-FACADE-AND-DYNAMIC-PLUGIN.md`
- `done/ADR-0003-POSTGRES-CLIENT-TRANSPORT.md`
- `done/ADR-0011-NATIVE-RUNTIME-LIFECYCLE.md`

## Closing Rules

An ADR moves to `docs/done/` only when its acceptance criteria are enforced by
repo commands, not just described in text. The minimum evidence is:

- implementation exists in the release path, not only in a local fixture
- a focused regression test covers the behavior or failure mode
- native preflight runs that test or an equivalent package-level doctor check
- packaged diagnostics carry enough data to debug stale or partial artifacts
- production packaging fails when the ADR's release contract is not satisfied

`scripts/audit-adr-closure.py` keeps the bookkeeping honest: root ADR files must
be `Status: Open`, done ADR files must be `Status: Done`, and this README must
list every open and done record. Native preflight runs that audit before package
work starts.

Current closure frontier:

- ADR-0002: macOS release preflight now passes from the final package artifact
  with native Postgres/PGlite linked and full extension parity exercised. It
  still needs Linux native link/conformance and broader protocol coverage before
  the native build lane is complete.
- ADR-0004: still needs every root ADR closed before production packages can
  claim runtime-ready status.
- ADR-0005: still needs the final upstream/carry decision for the native
  portability patches and a passing Linux release preflight. The backend
  archive now audits that PostgreSQL socket I/O binds to PGlite callback shims
  instead of libc socket APIs, and Linux prepare forces the poll/self-pipe latch
  path for the dummy PGlite socket descriptor.
- ADR-0006: still needs Linux baseline automation through the Ubuntu
  environment in `../smolvm/` or an equivalent release container. A local
  `scripts/preflight-linux-smolvm.sh` entrypoint now exists for the
  `ubuntu:24.04` guest path and isolates Linux build outputs under `/tmp` in the
  guest. The full Ubuntu preflight now passes; remaining closure is to record
  the Linux distro/toolchain/libc baseline in package diagnostics and reject
  mismatched baselines.
- ADR-0007: macOS package doctor self-tests the packaged `postgres/` prefix,
  including the full extension/runtime data surface. The Ubuntu lane now passes
  the same package doctor self-test with Linux RUNPATH repair. Remaining
  closure is keeping the prefix layout stable across supported packages and
  ensuring strict diagnostics stay release-gating.
- ADR-0008: macOS release preflight now materializes all pinned PGlite
  `other_extensions`, builds the full set including `postgis`, packages them,
  and runs packaged-artifact `CREATE EXTENSION` conformance for the parity set.
  The Ubuntu lane now passes the same parity path on Linux. Remaining closure is
  production enforcement and regression coverage that keeps missing extension
  sources or files from degrading to warnings.
- ADR-0009: macOS packaged `pgcrypto` and PostGIS now work from the controlled
  dependency prefix under strict package diagnostics. It still needs the Linux
  prefix contract and continued strict dependency-regression coverage. The
  Ubuntu lane now applies package-local RUNPATH repair with `patchelf` and
  passes strict package diagnostics. Remaining closure is documenting the Linux
  prefix contract as release policy and keeping strict dependency-regression
  coverage in place.
- ADR-0010: macOS release preflight now generates backend exports from the full
  packaged parity set, including common data symbols, and proves the modules
  load through the globally loaded plugin. Linux now uses a Rust staticlib plus
  one final GNU ld version-script boundary and filters the expected version node
  from symbol diagnostics. The Ubuntu lane now reaches the package doctor after
  raw-protocol extension conformance; `pg_ivm` exposed the need to export
  read-only backend data symbols such as `InvalidObjectAddress`, so the scanner
  now includes `R` symbols. The Ubuntu packaged-runtime proof now passes; this
  ADR still needs full-set stale-symbol regression coverage before it can close.
- ADR-0012: still needs production package enforcement for every
  release-critical diagnostic and Linux schema parity before it can close. The
  normal macOS preflight package path now carries controlled-prefix diagnostics,
  source/patch provenance, symbol manifests, conformance logs, and full
  extension package claims into the final-artifact doctor.
