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

- ADR-0002: still needs Linux native link/conformance and broader protocol
  coverage before the native build lane is complete.
- ADR-0004: still needs every root ADR closed before production packages can
  claim runtime-ready status.
- ADR-0005: still needs Linux `pglitec.c` PIC proof and an upstream/carry
  decision for the native portability patches.
- ADR-0006: still needs Linux baseline automation through the Ubuntu
  environment in `../smolvm/` or an equivalent release container.
- ADR-0007: still needs final relocatable prefix closure after extension parity
  expands the prefix beyond current contrib coverage.
- ADR-0008: still needs packaged-artifact `CREATE EXTENSION` conformance for
  the full parity set and promotion of the full `other_extensions` build into
  release preflight. The dependency-prefixed macOS probe now materializes all
  pinned PGlite `other_extensions` and builds the full set, including
  `postgis`, into the generated prefix.
- ADR-0009: still needs packaged `pgcrypto` and PostGIS runtime proof, strict
  dependency diagnostics across the final extension surface, and the Linux
  prefix contract. The macOS preflight path now builds the clean controlled
  dependency prefix by default; the full-extension probe also builds PostGIS
  against that prefix and installs its PROJ data into the generated Postgres
  prefix.
- ADR-0010: still needs stale-symbol checks against the packaged full extension
  parity set and Linux export/version-script coverage. The macOS symbol scanner
  has now seen the full PGlite `other_extensions` build, including PostGIS, but
  the packaged parity set still needs to drive the release gate.
- ADR-0012: still needs production package enforcement for every
  release-critical diagnostic and Linux schema parity before it can close. The
  normal macOS preflight package path now carries the controlled prefix
  diagnostic and checksum into the final-artifact doctor, and the full
  `other_extensions` build probe now gives the diagnostic substrate concrete
  PostGIS build artifacts and projection data to validate.
