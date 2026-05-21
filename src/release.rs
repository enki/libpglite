use std::path::Path;
use std::path::PathBuf;

use sha2::Digest;

use crate::PgliteError;
use crate::PgliteResult;

pub const LIBPGLITE_PLUGIN_PATH_ENV: &str = "LIBPGLITE_PLUGIN_PATH";
pub const LIBPGLITE_HOME_ENV: &str = "LIBPGLITE_HOME";
pub const RELEASE_REPOSITORY: &str = "enki/libpglite";
pub const RELEASE_TAG: &str = concat!("v", env!("CARGO_PKG_VERSION"));
pub const CHECKSUMS_ASSET_SUFFIX: &str = "checksums.txt";
pub const NOTICE_ASSET_SUFFIX: &str = "NOTICE.txt";
pub const SOURCE_ASSET_SUFFIX: &str = "SOURCE.txt";
pub const LICENSES_ASSET_SUFFIX: &str = "licenses.json";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NativePluginAsset {
    pub repository: &'static str,
    pub release_tag: &'static str,
    pub target: &'static str,
    pub asset_name: String,
    pub plugin_filename: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NativePluginSource {
    Environment,
    Bundled,
    Cache,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedNativePlugin {
    pub path: PathBuf,
    pub source: NativePluginSource,
    pub asset: NativePluginAsset,
}

#[derive(Debug, Clone)]
pub struct NativePluginResolver {
    plugin_path: Option<PathBuf>,
    cache_root: Option<PathBuf>,
}

#[derive(Debug, Clone, Default)]
pub struct BundledNativePluginResolver {
    plugin_path: Option<PathBuf>,
    plugin_dir: Option<PathBuf>,
    host_binary_path: Option<PathBuf>,
}

impl Default for NativePluginResolver {
    fn default() -> Self {
        Self {
            plugin_path: None,
            cache_root: default_cache_root(),
        }
    }
}

impl NativePluginAsset {
    pub fn current() -> PgliteResult<Self> {
        let target = current_target_triple().ok_or_else(|| {
            PgliteError::initialize(format!(
                "libpglite native plugin release assets are not available for host target `{}`",
                compile_time_target()
            ))
        })?;
        Ok(Self::for_target(target))
    }

    pub fn for_target(target: &'static str) -> Self {
        Self {
            repository: RELEASE_REPOSITORY,
            release_tag: RELEASE_TAG,
            target,
            asset_name: format!("libpglite-plugin-native-{RELEASE_TAG}-{target}.tar.zst"),
            plugin_filename: plugin_filename_for_target(target),
        }
    }

    pub fn archive_url(&self) -> String {
        self.release_asset_url(&self.asset_name)
    }

    pub fn checksums_asset_name(&self) -> String {
        release_asset_name(CHECKSUMS_ASSET_SUFFIX)
    }

    pub fn notice_asset_name(&self) -> String {
        release_asset_name(NOTICE_ASSET_SUFFIX)
    }

    pub fn source_asset_name(&self) -> String {
        release_asset_name(SOURCE_ASSET_SUFFIX)
    }

    pub fn licenses_asset_name(&self) -> String {
        release_asset_name(LICENSES_ASSET_SUFFIX)
    }

    pub fn checksums_url(&self) -> String {
        self.release_asset_url(&self.checksums_asset_name())
    }

    pub fn notice_url(&self) -> String {
        self.release_asset_url(&self.notice_asset_name())
    }

    pub fn source_url(&self) -> String {
        self.release_asset_url(&self.source_asset_name())
    }

    pub fn licenses_url(&self) -> String {
        self.release_asset_url(&self.licenses_asset_name())
    }

    pub fn cache_dir(&self, cache_root: impl AsRef<Path>) -> PathBuf {
        cache_root.as_ref().join(self.release_tag).join(self.target)
    }

    pub fn cached_plugin_path(&self, cache_root: impl AsRef<Path>) -> PathBuf {
        self.cache_dir(cache_root).join(self.plugin_filename)
    }

    pub fn plugin_path_in_dir(&self, dir: impl AsRef<Path>) -> PathBuf {
        dir.as_ref().join(self.plugin_filename)
    }

    fn release_asset_url(&self, asset_name: &str) -> String {
        format!(
            "https://github.com/{}/releases/download/{}/{}",
            self.repository, self.release_tag, asset_name
        )
    }
}

impl NativePluginResolver {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn from_env() -> Self {
        Self {
            plugin_path: std::env::var_os(LIBPGLITE_PLUGIN_PATH_ENV).map(PathBuf::from),
            cache_root: std::env::var_os(LIBPGLITE_HOME_ENV)
                .map(PathBuf::from)
                .or_else(default_cache_root),
        }
    }

    pub fn with_plugin_path(mut self, plugin_path: impl Into<PathBuf>) -> Self {
        self.plugin_path = Some(plugin_path.into());
        self
    }

    pub fn with_cache_root(mut self, cache_root: impl Into<PathBuf>) -> Self {
        self.cache_root = Some(cache_root.into());
        self
    }

    pub fn resolve(&self) -> PgliteResult<ResolvedNativePlugin> {
        let asset = NativePluginAsset::current()?;
        if let Some(plugin_path) = &self.plugin_path {
            if plugin_path.is_file() {
                return Ok(ResolvedNativePlugin {
                    path: plugin_path.clone(),
                    source: NativePluginSource::Environment,
                    asset,
                });
            }
            return Err(PgliteError::initialize(format!(
                "{LIBPGLITE_PLUGIN_PATH_ENV} points to `{}`, but no plugin file exists there",
                plugin_path.display()
            )));
        }

        if let Some(cache_root) = &self.cache_root {
            let plugin_path = asset.cached_plugin_path(cache_root);
            if plugin_path.is_file() {
                return Ok(ResolvedNativePlugin {
                    path: plugin_path,
                    source: NativePluginSource::Cache,
                    asset,
                });
            }
        }

        Err(PgliteError::initialize(missing_plugin_message(
            &asset,
            self.cache_root.as_deref(),
        )))
    }
}

impl BundledNativePluginResolver {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn from_env() -> Self {
        Self {
            plugin_path: std::env::var_os(LIBPGLITE_PLUGIN_PATH_ENV).map(PathBuf::from),
            plugin_dir: None,
            host_binary_path: None,
        }
    }

    pub fn with_plugin_path(mut self, plugin_path: impl Into<PathBuf>) -> Self {
        self.plugin_path = Some(plugin_path.into());
        self
    }

    pub fn with_plugin_dir(mut self, plugin_dir: impl Into<PathBuf>) -> Self {
        self.plugin_dir = Some(plugin_dir.into());
        self
    }

    pub fn with_host_binary_path(mut self, host_binary_path: impl Into<PathBuf>) -> Self {
        self.host_binary_path = Some(host_binary_path.into());
        self
    }

    pub fn resolve(&self) -> PgliteResult<ResolvedNativePlugin> {
        let asset = NativePluginAsset::current()?;
        if let Some(plugin_path) = &self.plugin_path {
            if plugin_path.is_file() {
                return Ok(ResolvedNativePlugin {
                    path: plugin_path.clone(),
                    source: NativePluginSource::Environment,
                    asset,
                });
            }
            return Err(PgliteError::initialize(format!(
                "{LIBPGLITE_PLUGIN_PATH_ENV} points to `{}`, but no plugin file exists there",
                plugin_path.display()
            )));
        }

        if let Some(plugin_dir) = &self.plugin_dir {
            return resolve_bundled_plugin_in_dir(asset, plugin_dir);
        }

        if let Some(host_binary_path) = &self.host_binary_path {
            let plugin_dir = host_binary_path.parent().ok_or_else(|| {
                PgliteError::initialize(format!(
                    "host binary path `{}` has no parent directory for bundled libpglite plugin resolution",
                    host_binary_path.display()
                ))
            })?;
            return resolve_bundled_plugin_in_dir(asset, plugin_dir);
        }

        Err(PgliteError::initialize(format!(
            "bundled libpglite plugin resolution requires {LIBPGLITE_PLUGIN_PATH_ENV}, a plugin directory, or a host binary path"
        )))
    }
}

pub fn current_native_plugin_asset() -> PgliteResult<NativePluginAsset> {
    NativePluginAsset::current()
}

pub fn resolve_native_plugin() -> PgliteResult<ResolvedNativePlugin> {
    NativePluginResolver::from_env().resolve()
}

pub fn expected_checksum(checksums: &str, asset_name: &str) -> PgliteResult<String> {
    checksums
        .lines()
        .filter_map(|line| {
            let mut parts = line.split_whitespace();
            let checksum = parts.next()?;
            let name = parts.next()?;
            (name == asset_name).then(|| checksum.to_string())
        })
        .next()
        .ok_or_else(|| {
            PgliteError::initialize(format!("checksum entry for `{asset_name}` was not found"))
        })
}

pub fn verify_file_checksum(checksums: &str, asset_name: &str, path: &Path) -> PgliteResult<()> {
    let expected = expected_checksum(checksums, asset_name)?;
    let bytes = std::fs::read(path).map_err(|err| {
        PgliteError::initialize(format!(
            "failed to read `{}` for checksum verification: {err}",
            path.display()
        ))
    })?;
    let actual = hex_lower(&sha2::Sha256::digest(bytes));
    if actual != expected {
        return Err(PgliteError::initialize(format!(
            "checksum mismatch for `{asset_name}`: expected {expected}, got {actual}"
        )));
    }
    Ok(())
}

fn resolve_bundled_plugin_in_dir(
    asset: NativePluginAsset,
    plugin_dir: &Path,
) -> PgliteResult<ResolvedNativePlugin> {
    let plugin_path = asset.plugin_path_in_dir(plugin_dir);
    if plugin_path.is_file() {
        return Ok(ResolvedNativePlugin {
            path: plugin_path,
            source: NativePluginSource::Bundled,
            asset,
        });
    }
    Err(PgliteError::initialize(format!(
        "bundled libpglite plugin `{}` was not found in `{}`",
        asset.plugin_filename,
        plugin_dir.display()
    )))
}

fn missing_plugin_message(asset: &NativePluginAsset, cache_root: Option<&Path>) -> String {
    let cache_hint = cache_root
        .map(|root| {
            format!(
                ", or install the plugin at `{}`",
                asset.cached_plugin_path(root).display()
            )
        })
        .unwrap_or_default();
    format!(
        "native libpglite plugin for target `{}` was not found. Set {LIBPGLITE_PLUGIN_PATH_ENV} to an exact plugin path{cache_hint}. Expected release asset: {}",
        asset.target,
        asset.archive_url()
    )
}

fn current_target_triple() -> Option<&'static str> {
    match compile_time_target().as_str() {
        "aarch64-apple-darwin" => Some("aarch64-apple-darwin"),
        "x86_64-apple-darwin" => Some("x86_64-apple-darwin"),
        "x86_64-unknown-linux-gnu" => Some("x86_64-unknown-linux-gnu"),
        "aarch64-unknown-linux-gnu" => Some("aarch64-unknown-linux-gnu"),
        _ => None,
    }
}

fn compile_time_target() -> String {
    format!(
        "{}-{}-{}",
        std::env::consts::ARCH,
        std::env::consts::OS,
        std::env::consts::FAMILY
    )
    .replace("macos-unix", "apple-darwin")
    .replace("linux-unix", "unknown-linux-gnu")
}

fn plugin_filename_for_target(target: &str) -> &'static str {
    if target.ends_with("apple-darwin") {
        "liblibpglite_plugin_native.dylib"
    } else {
        "liblibpglite_plugin_native.so"
    }
}

fn default_cache_root() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .map(|home| home.join(".cache").join("libpglite"))
}

fn release_asset_name(suffix: &str) -> String {
    format!("libpglite-plugin-native-{RELEASE_TAG}-{suffix}")
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}
