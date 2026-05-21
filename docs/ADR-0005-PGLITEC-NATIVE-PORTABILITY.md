# ADR-0005: PGlite C Support Native Portability

Status: Open
Date: 2026-05-21

## Context

The PGlite C support file is currently written for the Emscripten build lane.
A direct native PIC compile exposes portability gaps such as missing standard C
headers and platform-specific shared-memory struct fields.

These are real native substrate issues. They must not be hidden behind Rust
wrappers or treated as warnings, because the native plugin depends on this code
for lifecycle, callback transport, longjmp handling, and Postgres process
emulation.

## Decision

Native PGlite support code must have an explicit portability patch lane.

The Rust plugin must not link a locally hacked copy of `pglitec.c` with ad hoc
compiler flags. Required changes belong either upstream in the pinned
postgres-pglite source or in a small, documented patch set applied by the native
link preparation script.

The patch lane must keep Emscripten behavior and native behavior separated by
clear preprocessor gates.

## Required Work

1. Add a native compile fixture for PGlite C support.
2. Fix missing standard C declarations through source includes, not permissive
   compiler modes.
3. Replace platform-specific shared-memory field access with portable helpers.
4. Prove `pglitec.c` compiles as PIC on supported macOS and Linux targets.
5. Decide whether patches are carried in this repository or upstreamed to the
   PGlite Postgres fork.

## Acceptance Criteria

- `pglitec.c` compiles as release PIC on supported targets.
- Native and Emscripten behavior are both intentional and documented.
- No native build script suppresses C portability errors with broad
  compatibility flags.
- ADR-0002 can consume PGlite C support as a real native link input.

## Remaining Closure Criteria

- The carried PGlite native patch set is reduced to documented, fingerprinted
  source patches applied by `scripts/prepare-native-pglite-link.sh`.
- `pglitec.c` compiles as PIC without broad permissive compiler flags on macOS
  and Linux.
- The Linux compile proves the shared-memory portability gates in the same
  source path used by the release build.
- The ADR records the final carry/upstream decision for each portability patch
  before moving to `docs/done/`.

## Implementation Notes

- The carried patch keeps Emscripten behavior unchanged while routing native
  `pgl_exit()` through `libpglite_native_exit()`. This gives the Rust adapter a
  C-level recovery boundary without unwinding through Rust frames.
- Native shared-memory metadata access is gated for macOS and Linux because the
  exposed `struct shmid_ds` field names differ across platforms.
- The native build compiles the patched `pglitec.c` as PIC and fingerprints the
  patch in the generated link manifest so source-substrate changes force a
  rebuild.
- A second native patch exports PGlite runtime symbols needed outside the
  Emscripten build, keeps native embedded mode from probing a nonexistent
  postmaster-death pipe, and preserves Emscripten behavior behind explicit
  preprocessor gates.
