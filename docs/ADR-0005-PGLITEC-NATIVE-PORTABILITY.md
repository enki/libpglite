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
- The release backend archive fails fast if PostgreSQL socket I/O binds to libc
  calls that PGlite is expected to emulate through `pglitec.c`.
- ADR-0002 can consume PGlite C support as a real native link input.

## Remaining Closure Criteria

- The carried PGlite native patch set is reduced to documented, fingerprinted
  source patches applied by `scripts/prepare-native-pglite-link.sh`.
- `pglitec.c` compiles as PIC without broad permissive compiler flags on macOS
  and Linux.
- The Linux compile proves the shared-memory portability gates in the same
  source path used by the release build.
- The release prepare path proves backend objects reference the PGlite callback
  transport shims (`pgl_recv`, `pgl_send`, `pgl_poll`, and related socket
  wrappers) instead of the corresponding libc socket APIs.
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
- Linux native prepare now forces PostgreSQL's latch implementation onto the
  poll/self-pipe path with `WAIT_USE_POLL` and `WAIT_USE_SELF_PIPE`. The
  Emscripten lane already routes `poll()` through `pgl_poll`; native Linux must
  avoid the epoll path because PGlite's callback transport uses a dummy socket
  descriptor rather than a kernel socket fd.
- The backend archive build now audits undefined symbols after archiving. It
  rejects raw references to `recv`, `send`, `poll`, `connect`, `fcntl`,
  `setsockopt`, `getsockopt`, or `getsockname`, and requires the corresponding
  callback read/write shims to be present. The required-present set is
  intentionally limited to shims exercised by the runtime transport path, while
  raw libc socket references are rejected exactly when present. This turns the
  callback transport binding into a build invariant without requiring optional
  setup/authentication paths to stay linked.
- Linux showed that command-line `-Drecv=pgl_recv` style overrides can be too
  early for system socket prototypes. Native prepare now generates a forced
  include header that includes `<sys/socket.h>`, `<poll.h>`, `<fcntl.h>`, and
  `<setjmp.h>` before defining the PGlite socket and jump-call macros. That
  keeps libc prototypes intact and redirects PostgreSQL call sites instead of
  system declarations.
- Linux runtime conformance then reached Postgres error recovery and exposed
  another Emscripten-specific assumption: byte-comparing `jmp_buf` storage is
  not a portable way to identify the top-level Postgres exception frame. The
  native patch now compares the jump-buffer address instead, so the PGlite
  top-level longjmp path is intercepted before glibc can attempt a real longjmp
  into the returned single-user-main stack frame.
- The backend archive audit now rejects raw `longjmp`, `siglongjmp`, and glibc
  fortified jump symbols in addition to raw socket calls, and requires the
  backend to reference `pgl_siglongjmp`. This keeps Linux from silently bypassing
  the native error-recovery trap.
