# ADR-0016: Symlinked Host Binary Bundled Plugin Resolution

Status: Open

## Context

Product hosts are allowed to launch through stable symlinks while the real
runtime binary and bundled native plugin live in a build or release directory.
For example, a user-level command shim can point at a build or release binary:

```text
~/.local/bin/product-host -> /repo/product/target/debug/product-host
```

Libpglite's bundled native plugin resolver previously derived the plugin
directory from the raw host-binary path. That made symlinked launchers search
`~/.local/bin` for `liblibpglite_plugin_native.dylib`, even though the verified
plugin was correctly bundled beside the real executable in `target/debug`.

This is the same class of runtime-artifact topology bug that libbun already
avoids by considering canonical executable paths.

## Decision

Bundled plugin resolution from a host binary must use a bounded, explicit
candidate frontier:

1. the raw host executable directory;
2. the parent of the raw directory when the raw directory is Cargo `deps`;
3. the canonical host executable directory;
4. the parent of the canonical directory when the canonical directory is Cargo
   `deps`.

The resolver must not probe source checkouts, package roots, working
directories, or user-level shim directories beyond those executable-derived
frontiers. Explicit `LIBPGLITE_PLUGIN_PATH` and explicit plugin-directory
resolution remain exact and higher-priority.

## Acceptance Criteria

- A symlinked host binary resolves the bundled plugin beside the canonical real
  executable.
- A Cargo test binary under `target/<profile>/deps` can resolve the bundled
  plugin from `target/<profile>`.
- Missing bundled plugin diagnostics list the executable-derived expected paths.
- A product host launched through a symlink succeeds when the plugin is bundled
  beside the canonical real executable.
- Native package preflight or package doctor covers this resolver behavior
  before this ADR moves to `docs/done/`.

## Implementation Progress

2026-05-22: `BundledNativePluginResolver` now routes host-binary resolution
through a candidate frontier that includes raw and canonical executable
directories plus Cargo `deps` parents. Focused tests cover symlinked launcher
resolution and Cargo `deps` parent resolution.

Verification so far:

- `cargo fmt --manifest-path Cargo.toml --check`
- `cargo test --test release --quiet`

The ADR remains open until the native package preflight/package doctor includes
or directly exercises this resolver behavior.

## Remaining Closure Criteria

- Add native package preflight or package-doctor coverage that exercises
  symlinked host-binary bundled-plugin resolution from an extracted package.
- Add native package preflight or package-doctor coverage that exercises Cargo
  `deps`-parent bundled-plugin resolution, or document the equivalent release
  command that runs the focused release test before packaging.
