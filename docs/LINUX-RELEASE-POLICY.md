# Linux Native Release Policy

Status: Active
Date: 2026-05-21

Linux native release artifacts are built and validated on Ubuntu `24.04` unless
an ADR records a successor baseline.

The Linux lane must use the controlled native dependency prefix from
`deps/native-pglite-dependencies.json`. The native plugin, PostgreSQL modules,
PGlite `other_extensions`, and packaged diagnostics must not depend on
developer-machine library paths or host-provider packages.

Release preflight for Linux requires `patchelf`. Packaging must repair runtime
search paths to package-local values:

- the native plugin uses `$ORIGIN/postgres/lib`
- PostgreSQL loadable modules under `postgres/lib` use `$ORIGIN`

The package doctor must reject Linux artifacts whose dependency diagnostics
contain host-provider, build-machine, absolute-external, missing, or unknown
dependency classifications. It must also reject artifacts whose platform
baseline diagnostic does not record Ubuntu `24.04`, unless an ADR has replaced
that baseline.

Local Linux validation may run through `scripts/preflight-linux-smolvm.sh`,
which uses `../smolvm/` to execute the native release preflight inside an
Ubuntu `24.04` guest. That local route is acceptable validation evidence for
development; production release automation must preserve the same baseline,
RUNPATH repair, package diagnostics, and final-artifact self-test contract.
