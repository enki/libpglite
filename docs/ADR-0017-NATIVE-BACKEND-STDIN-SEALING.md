# ADR-0017: Native Backend Stdin Sealing

Status: Open

## Context

ADR-0014 captured native backend stdout/stderr, but embedded PostgreSQL startup
still inherited the host process stdin. When a product host runs under an
interactive terminal, PostgreSQL single-user startup can block in `read(0)`. The
same path may appear green under non-interactive execution only because stdin
happens to reach EOF.

That is not acceptable substrate. Native backend execution must not depend on
the caller's terminal state, shell job control, or accidental stdin EOF.

## Decision

`NativeBackendStdioLease` owns the complete native backend stdio frontier for
each backend call:

```text
NativeBackendCall
  -> stdin sealed to EOF
  -> stdout/stderr captured
  -> PgliteBackendOutputLedger
  -> fds restored
```

Stdin is not an input authority for the embedded backend. PostgreSQL protocol
input already flows through the PGlite transport callbacks. Any attempt by the
backend to read fd 0 during startup/protocol/shutdown must observe immediate EOF
from a libpglite-owned `/dev/null` lease, never the host terminal or a parent
pipe.

## Hard Rules

- Native backend startup, protocol, and shutdown must not inherit host stdin.
- The stdio lease covers stdin, stdout, and stderr as one sealed phase boundary.
- Stdout/stderr remain captured as backend-output records.
- Stdin sealing is not a data path; protocol input remains only the PGlite
  transport callback.
- If any fd cannot be redirected or restored, the native runtime operation fails.
- Native release preflight and package doctor must prove startup completes while
  the child process has a live inherited stdin pipe with no data.

## Acceptance Criteria

- A focused native dynamic-plugin test spawns a child with a live piped stdin and
  proves runtime startup completes without consuming or waiting on that pipe.
- Native preflight runs the stdin-sealing test against the release plugin.
- Package doctor runs the stdin-sealing test against an extracted package.
- Downstream product-host execution no longer changes behavior when run under an
  interactive TTY, non-interactive shell, symlinked launcher, or after shell
  suspend/resume.

## Implementation Progress

2026-05-22:

- `NativeBackendStdioLease` now redirects stdin to `/dev/null` while capturing
  stdout/stderr and restores all three fds when the lease finishes.
- Added `dynamic_plugin_native_startup_seals_inherited_stdin`, which keeps a
  child stdin pipe open with no data and fails if native startup blocks on fd 0.
- Wired the focused stdin-sealing test into native preflight and package doctor.
- Verified the focused stdin-sealing test against the locally rebuilt native
  release plugin.

## Remaining Closure Criteria

- Run full libpglite formatting/tests after downstream integration.
- Rebuild the packaged native plugin consumed by a product host and prove
  interactive execution no longer stalls or changes behavior after
  suspend/resume.
- Run package doctor or the native preflight path that exercises this test from
  the packaged archive.
