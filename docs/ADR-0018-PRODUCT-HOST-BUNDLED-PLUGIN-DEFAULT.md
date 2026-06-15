# ADR-0018: Product Host Bundled Plugin Default

Status: Open

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

## Remaining Closure Criteria

- Package preflight or package doctor should exercise the product-facing default
  from an extracted product-host binary.
- Missing-plugin diagnostics from the dynamic runtime should be captured in a
  focused failure test or package-doctor check.
