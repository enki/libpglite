# libpglite

Rust facade for hosting PGlite through a replaceable native plugin.

This repository owns the stable Rust boundary, the dynamic plugin ABI, and the
architecture record for turning PGlite into an in-process native Postgres
runtime. It does not expose Postgres internals, generated wasm2c symbols, or
PGlite frontend transport details as the public API.

## Status

Runtime-ready native plugin. The facade, dynamic loader, plugin ABI, plugin
crate, internal native crate, native Postgres/PGlite runtime, packaged Postgres
prefix, extension parity set, dependency prefix, diagnostics, and package doctor
are implemented.

The current release gate has passed on macOS and on Ubuntu 24.04. All design
records for the first native release scope are closed under `docs/done/`.

## Intended Use

Downstream Rust applications depend on the facade crate and load the native
implementation through a replaceable plugin:

```toml
libpglite = { version = "0.1", features = ["dynamic-loading"] }
```

Product hosts should bundle the verified plugin beside the host binary, or in a
deterministic directory relative to it:

```text
bin/
  host
  liblibpglite_plugin_native.dylib      # macOS
  liblibpglite_plugin_native.so         # Linux
```

Minimal dynamic-loading shape:

```rust
use libpglite::dynamic::DynamicPgliteRuntime;
use libpglite::{PgliteConfig, PgliteRuntime};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = PgliteConfig::new("example-host", "./pgdata");
    let mut runtime = DynamicPgliteRuntime::initialize_with_bundled_plugin(
        config,
        std::env::current_exe()?,
    )?;

    let response = runtime.exec_protocol_raw(&[])?;
    assert!(response.is_empty());
    runtime.shutdown()?;
    Ok(())
}
```

The first stable runtime contract is PostgreSQL frontend protocol bytes in and
backend protocol bytes out. Higher-level SQL clients should be layered above
that transport instead of reimplementing PostgreSQL type and row semantics in
this crate.

## Design Records

- `docs/done/ADR-0001-RUST-FACADE-AND-DYNAMIC-PLUGIN.md`
- `docs/done/ADR-0002-NATIVE-PGLITE-BUILD-LANE.md`
- `docs/done/ADR-0003-POSTGRES-CLIENT-TRANSPORT.md`
- `docs/done/ADR-0004-RUNTIME-READY-RELEASE-GATE.md`
- `docs/done/ADR-0005-PGLITEC-NATIVE-PORTABILITY.md`
- `docs/done/ADR-0006-NATIVE-BUILD-PLATFORM-FLOOR.md`
- `docs/done/ADR-0007-NATIVE-INITDB-AND-PREFIX-ARTIFACT.md`
- `docs/done/ADR-0008-NATIVE-EXTENSION-PARITY.md`
- `docs/done/ADR-0009-NATIVE-DEPENDENCY-PREFIX.md`
- `docs/done/ADR-0010-NATIVE-BACKEND-SYMBOL-CONTRACT.md`
- `docs/done/ADR-0011-NATIVE-RUNTIME-LIFECYCLE.md`
- `docs/done/ADR-0012-NATIVE-DIAGNOSTIC-MANIFESTS.md`
