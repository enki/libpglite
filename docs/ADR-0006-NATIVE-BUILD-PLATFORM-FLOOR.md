# ADR-0006: Native Build Platform Floor

Status: Open
Date: 2026-05-21

## Context

The first macOS native archive link surfaced linker warnings because the C
objects were built for the host SDK default deployment version while Rust linked
the cdylib for an older macOS deployment target.

That is not a runtime bug by itself, but it is a release substrate weakness:
the native Postgres objects, PGlite support object, and Rust plugin must agree
on the platform floor promised by the released binary.

## Decision

The native build lane must set, record, and preflight the platform floor for
native C inputs. On Darwin, `MACOSX_DEPLOYMENT_TARGET` defaults to `11.0` unless
the release environment overrides it intentionally.

Linux needs the same treatment before production release, either through a
documented build container/toolchain or an explicit libc baseline.

For local Linux validation, this workspace can use the Ubuntu environment in
`../smolvm/`, matching the local Linux testing route used for comparable native
library work. That VM path is a development and preflight aid; release artifacts
still need an explicitly documented Linux baseline.

## Required Work

1. Set a deterministic macOS deployment target for native C compilation.
2. Record the selected deployment target in the native link manifest.
3. Rebuild native Postgres objects when the platform floor changes.
4. Add preflight checks that reject mixed deployment-target native inputs.
5. Define and test the Linux libc/toolchain baseline.
6. Document and automate the local Ubuntu validation flow through `../smolvm/`.

## Acceptance Criteria

- macOS native preflight produces no deployment-target mismatch warnings.
- The manifest records the macOS deployment target used for C inputs.
- Changing the deployment target forces a native Postgres rebuild.
- Linux release preflight runs in a documented baseline environment.

## Remaining Closure Criteria

- The package doctor rejects missing, empty, stale, or contradictory
  platform-floor diagnostics for both supported targets. On macOS that means the
  native link manifest, `platform-baseline.json`, and build provenance agree on
  the deployment target. On Linux that means the package records and validates
  the selected distro/libc baseline.
- A focused regression proves changing `MACOSX_DEPLOYMENT_TARGET` invalidates
  the native Postgres/PGlite build fingerprint instead of reusing stale objects.
- The Linux baseline is treated as release policy, not only a local preflight
  habit: production packages must reject artifacts that were not built on the
  documented Ubuntu baseline or an explicitly documented successor baseline.
- After platform-floor diagnostics change, the full macOS preflight and the
  Ubuntu `../smolvm/` preflight both pass from the final package artifact.

## Implementation Notes

- `scripts/prepare-native-pglite-link.sh` now defaults
  `MACOSX_DEPLOYMENT_TARGET=11.0` on Darwin.
- The native build fingerprint includes the deployment target and forces a
  Postgres rebuild when it changes.
- The native link manifest records `macos_deployment_target`.
- Packaged build provenance records the release target, and the package doctor
  verifies that this target agrees with the bundle manifest so stale or
  cross-target provenance cannot satisfy the diagnostic gate.
- The native dependency-prefix builder now applies the same macOS floor
  discipline to third-party C/C++ dependencies. On Darwin it adds
  `-Werror=unguarded-availability-new`, so SDK availability mistakes fail the
  prefix build. This caught SQLite detecting `strchrnul` from the macOS 15 SDK
  despite the macOS 11.0 deployment target; the builder now forces
  `HAVE_STRCHRNUL=0` after SQLite configure.
- Linux baseline selection is now exercised locally through the Ubuntu
  environment in `../smolvm/`.
- `scripts/preflight-linux-smolvm.sh <version>` is now the local Ubuntu
  baseline entrypoint. It runs `scripts/preflight-native-plugin-release.sh` in
  an `ubuntu:24.04` guest through `../smolvm/target/release/smolvm`, installs
  the native build prerequisites, mounts this checkout at `/mnt/libpglite`,
  mounts the pinned `postgres-pglite` checkout at `/mnt/postgres-pglite` when
  available from `LIBPGLITE_POSTGRES_SOURCE_DIR`, `vendor/postgres-pglite`, or
  `../postgres-pglite`, and marks those mounts as Git-safe inside the guest.
  Linux Cargo/native/package output is isolated under `/tmp` inside the guest.
  On macOS it also points `DYLD_LIBRARY_PATH` at the local smolvm `lib/`
  directory so the checked out smolvm binary can use its bundled VMM libraries
  instead of depending on Homebrew install names. The first Ubuntu run reached
  the native dependency-prefix stage and exposed mixed-ownership reuse of
  host-side `target/native-pglite` git checkouts; native preflight now accepts a
  `LIBPGLITE_NATIVE_BUILD_ROOT` so Linux can keep its dependency sources,
  dependency build directory, dependency prefix, and native link manifest out of
  the mounted macOS worktree. The next Ubuntu run built the full controlled
  dependency prefix and then exposed the missing `postgres-pglite` source mount;
  the wrapper now supplies that mount explicitly. The following run reached
  native `initdb` and exposed a root-execution violation; the wrapper now keeps
  package installation as root but runs the libpglite preflight as an
  unprivileged `libpglite` user.
- `scripts/preflight-linux-smolvm.sh 0.1.0` passed in the `ubuntu:24.04`
  guest on 2026-05-21.
- Native packaging now writes `diagnostics/platform-baseline.json` and names it
  from both the bundle manifest and `build-provenance.txt`. On Linux, packaging
  defaults the expected baseline to Ubuntu `24.04`, records the `/etc/os-release`
  identity and `ldd --version` first line, and fails immediately if the actual
  distro/version differs. The package doctor validates the diagnostic target,
  Linux baseline identity, recorded OS release, and Linux libc version line. On
  macOS, the same diagnostic records the deployment target from the native link
  manifest so the package has one place to explain its platform floor.
- After adding the package baseline diagnostic, `scripts/preflight-linux-smolvm.sh`
  with version `0.1.0` passed again in the `ubuntu:24.04` guest on 2026-05-21.
