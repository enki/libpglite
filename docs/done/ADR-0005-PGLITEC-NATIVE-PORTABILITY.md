# ADR-0005: PGlite C Support Native Portability

Status: Done
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

## Closure Criteria

- The carried PGlite native patch set is reduced to documented, fingerprinted
  source patches applied by `scripts/prepare-native-pglite-link.sh`.
- The release prepare path proves backend objects reference the PGlite callback
  transport shims (`pgl_recv`, `pgl_send`, `pgl_poll`, and related socket
  wrappers) instead of the corresponding libc socket APIs.
- A focused regression keeps the Linux forced-include ordering and jump-buffer
  address comparison in place, because those are the portability fixes most
  likely to be broken by future patch refreshes.
- A focused regression keeps the forced-include shim header honest on every
  target: it must include system socket/jump headers first, declare the PGlite
  replacement functions with compatible prototypes, and only then macro-map
  PostgreSQL call sites to those replacements.
- The native portability patch itself must apply cleanly to the pinned source
  as part of preflight; a stale or malformed patch is not acceptable closure
  evidence. The patched-source cache must include the patch application method
  in its fingerprint so stricter patch validation cannot reuse an older tree.

## Closing Evidence

- `scripts/prepare-native-pglite-link.sh` applies the downstream patches with
  `git apply --check` and fingerprints the patch application method before any
  native build output can be reused.
- `scripts/test-native-patch-decisions.py` keeps every
  `patches/postgres-pglite/*.patch` file tied to the ADR's carry/upstream
  decision table, and `scripts/audit-adr-closure.py` verifies that regression is
  wired into native preflight.
- `scripts/test-prepare-native-pglite-link.py` pins the forced-include shim
  ordering, patch application path, patch-cache fingerprinting, and native
  rebuild behavior.
- Native preflight runs the patch-decision and prepare regressions before build
  work, then builds the patched PGlite support code as release PIC.
- `scripts/preflight-native-plugin-release.sh v0.1.0` passed on macOS on
  2026-05-21 after building the native backend, running raw protocol
  conformance, and validating the final package.
- `scripts/preflight-linux-smolvm.sh 0.1.0` passed in the documented Ubuntu
  `24.04` baseline on 2026-05-21, including the Linux poll/self-pipe and
  jump-buffer portability path.

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
- Native embedded mode must cover both public and internal postmaster-aliveness
  probes. macOS conformance exposed a direct `PostmasterIsAliveInternal()` path
  through latch handling; the runtime patch now returns alive immediately under
  `__PGLITE__` so PostgreSQL does not read the nonexistent postmaster-death
  pipe.
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
- macOS then exposed the next layer of the same issue: modern C compilation
  rejects call sites that macro-expand to undeclared replacement functions.
  The forced-include header now declares the PGlite socket, poll, fcntl, and
  jump shims before macro replacement. The carried native patch also gives
  native `pgl_poll` the host `nfds_t` width while keeping the Emscripten dummy
  `struct pollfd` path isolated behind an Emscripten gate.
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
- The final patch policy for this repository is to carry the native
  portability patches downstream while the native library target is developed.
  They stay small, fingerprinted, `git apply --check` verified, and isolated
  behind explicit native preprocessor gates. Upstreaming remains desirable once
  the native target contract is stable, but production readiness no longer
  depends on an upstream merge.
- Per-patch decisions:

  | Patch | Decision | Rationale |
  | --- | --- | --- |
  | `0001-pglitec-native-portability.patch` | carry downstream | Native exit trapping, socket/poll/jump shim routing, host `poll()` signature compatibility, and portable jump-buffer identity are required for the native dynamic-library target while preserving the Emscripten lane behind explicit gates. |
  | `0002-native-pglite-runtime-symbols.patch` | carry downstream | Native embedded mode needs runtime symbols and postmaster-aliveness behavior that the Emscripten build does not expose, including avoiding the nonexistent postmaster-death pipe in a single-process library runtime. |

- `scripts/test-native-patch-decisions.py` keeps that decision table in sync
  with the actual `patches/postgres-pglite/*.patch` files, and native preflight
  runs the test before build work.
- The prepare script no longer depends on the platform `patch` utility for the
  downstream patch lane. `git apply --check` and `git apply` are the only
  accepted patch application path, and the prepare regression suite pins both
  the positive Git path and the absence of the old `patch` command.
