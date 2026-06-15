# ADR-0018: Product Host Bundled Plugin Default

Status: Done

## Context

Libpglite has two native-plugin resolution shapes:

- product-host resolution, where the verified plugin and packaged Postgres
  prefix are bundled beside the host binary or selected by an exact
  `LIBPGLITE_PLUGIN_PATH`;
- release-tooling resolution, where a release package can be inspected from an
  explicitly admitted cache root.

Those shapes must not collapse into one default. A product host that calls
`DynamicPgliteRuntime::open(...)` or `release::resolve_native_plugin()` is
declaring runtime intent, not asking libpglite to search a user-local release
cache. A cache fallback hides packaging defects and gives downstream consumers
the wrong remediation: install into a local cache instead of bundling the
verified plugin with the product.

## Decision

The product-facing default is bundled-plugin resolution from the current host
executable:

```text
DynamicPgliteRuntime::open(config)
  -> release::resolve_native_plugin()
  -> current_exe()
  -> BundledNativePluginResolver
  -> LIBPGLITE_PLUGIN_PATH exact override or executable-derived bundle path
```

`NativePluginResolver` remains available only as an explicit release-tooling
resolver. It must not derive `~/.cache/libpglite` or any other user-local cache
root by default. A cache root is authority only when the caller supplies it
directly or through `LIBPGLITE_HOME`.

The missing-plugin diagnostic on the product path must name bundled
binary-relative resolution and exact `LIBPGLITE_PLUGIN_PATH` override. It must
not tell product hosts to install into a user-local cache.

## Acceptance Criteria

- `DynamicPgliteRuntime::open(...)` reaches bundled current-executable
  resolution.
- `release::resolve_native_plugin()` reaches bundled current-executable
  resolution.
- `NativePluginResolver::new()` does not resolve an ambient user-local cache.
- `NativePluginResolver::from_env()` admits only `LIBPGLITE_PLUGIN_PATH` and an
  explicit `LIBPGLITE_HOME`; it does not synthesize `~/.cache/libpglite`.
- Product-path missing-plugin diagnostics mention bundled/executable-derived
  paths or exact plugin overrides, not cache installation.
- Release tests cover both the explicit-cache resolver and the no-ambient-cache
  default.

## Implementation Progress

2026-05-24: `release::resolve_native_plugin()` now delegates to
`resolve_bundled_native_plugin_for_current_exe()`. `NativePluginResolver`
defaults to no cache root, and `from_env()` admits only explicit
`LIBPGLITE_HOME`. Focused release tests prove explicit cache resolution still
works while an ambient cache is not used without an admitted root.

2026-06-15: downstream Swarm ADR-2088 confirms this is the right owner-boundary
law for real product hosts. Swarm's `ss` runtime path must rely on
`DynamicPgliteRuntime::open(...)` reaching libpglite's bundled current-exe
resolver; Swarm must not compensate with a local cache resolver or
`LIBPGLITE_HOME` fallback. Libpglite still needs package-level proof that the
product-facing default works from a current executable, not only through
explicit plugin-directory test helpers.

## Closing Evidence

- `DynamicPgliteRuntime::open(...)` reaches
  `release::resolve_native_plugin()`, which resolves through the bundled
  current-executable product path.
- `NativePluginResolver::new()` has no ambient cache root, and
  `NativePluginResolver::from_env()` admits a cache only through explicit
  `LIBPGLITE_HOME`; exact `LIBPGLITE_PLUGIN_PATH` override remains higher
  priority.
- `native_plugin_resolver_does_not_use_ambient_cache_without_explicit_root`
  proves product runtime resolution does not silently fall back to a user-local
  release cache.
- `bundled_resolver_missing_plugin_error_names_product_paths_not_cache` proves
  product-path missing-plugin diagnostics name bundled executable-derived
  resolution and the exact plugin override, without directing hosts to a cache.
- `dynamic_plugin_open_uses_current_exe_bundled_plugin_default` runs from an
  extracted native package with `LIBPGLITE_PLUGIN_PATH`, `LIBPGLITE_HOME`,
  `LIBPGLITE_TEST_PLUGIN_PATH`, and `LIBPGLITE_TEST_POSTGRES_PREFIX` removed,
  then proves `DynamicPgliteRuntime::open(...)` finds the plugin and Postgres
  prefix through current-executable bundled resolution.
- `scripts/doctor-native-plugin-package.py --self-test
  dist/preflight-native-plugin/libpglite-plugin-native-v0.1.0-aarch64-apple-darwin.tar.zst`
  passed with the product current-exe bundled default self-test included.
