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

## Required Work

1. Set a deterministic macOS deployment target for native C compilation.
2. Record the selected deployment target in the native link manifest.
3. Rebuild native Postgres objects when the platform floor changes.
4. Add preflight checks that reject mixed deployment-target native inputs.
5. Define and test the Linux libc/toolchain baseline.

## Acceptance Criteria

- macOS native preflight produces no deployment-target mismatch warnings.
- The manifest records the macOS deployment target used for C inputs.
- Changing the deployment target forces a native Postgres rebuild.
- Linux release preflight runs in a documented baseline environment.

## Implementation Notes

- `scripts/prepare-native-pglite-link.sh` now defaults
  `MACOSX_DEPLOYMENT_TARGET=11.0` on Darwin.
- The native build fingerprint includes the deployment target and forces a
  Postgres rebuild when it changes.
- The native link manifest records `macos_deployment_target`.
- Linux baseline selection remains open.
