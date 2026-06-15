# ADR-0016: Symlinked Host Binary Bundled Plugin Resolution

Status: Done

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

2026-06-15: downstream Swarm confirms this topology matters for real product
hosts: development and installed commands can launch through stable symlinks
while the actual `ss` binary and bundled native-libpglite package live in a
build or release directory. The libpglite resolver behavior is correct, but the
release/package gate still needs to prove the behavior from an extracted package
instead of relying only on path-level unit tests.

## Closing Evidence

- `BundledNativePluginResolver` uses a bounded executable-derived frontier:
  raw executable directory, raw Cargo `deps` parent, canonical executable
  directory, and canonical Cargo `deps` parent.
- `bundled_resolver_follows_symlinked_host_binary_to_real_bundle` proves a
  symlinked host resolves the bundled plugin beside the canonical executable.
- `bundled_resolver_finds_plugin_from_cargo_deps_parent` keeps Cargo
  `target/<profile>/deps` parent resolution covered in the release test suite.
- `bundled_resolver_missing_plugin_error_lists_canonical_symlink_expected_path`
  proves missing-plugin diagnostics list both raw and canonical
  executable-derived expected paths for a symlinked product host.
- `dynamic_plugin_resolves_bundled_plugin_for_symlinked_host_binary_from_package`
  stages an extracted native package beside a real host executable, launches
  through an external symlink, and proves the plugin and bundled Postgres prefix
  are found from the canonical executable location.
- `scripts/doctor-native-plugin-package.py --self-test
  dist/preflight-native-plugin/libpglite-plugin-native-v0.1.0-aarch64-apple-darwin.tar.zst`
  passed with the symlinked-host package self-test included.
