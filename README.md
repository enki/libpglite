# libpglite

Rust facade for hosting PGlite through a replaceable native plugin.

This repository owns the stable Rust boundary, the dynamic plugin ABI, and the
architecture record for turning PGlite into an in-process native Postgres
runtime. It does not expose Postgres internals, generated wasm2c symbols, or
PGlite frontend transport details as the public API.

## Status

Initial scaffold. The facade, dynamic loader, plugin ABI, plugin crate, and
internal native crate are present. The native Postgres/PGlite implementation is
intentionally not wired yet; the required work is documented under `docs/`.

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

- `docs/ADR-0001-RUST-FACADE-AND-DYNAMIC-PLUGIN.md`
- `docs/ADR-0002-NATIVE-PGLITE-BUILD-LANE.md`
- `docs/ADR-0003-POSTGRES-CLIENT-TRANSPORT.md`

