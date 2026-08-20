//! Per-user configuration — the single source of truth for every path, host, and token the tool
//! uses. NOTHING here is hardcoded to a developer machine: the config file lives in the OS per-user
//! config directory (resolved via `dirs`), and the working-directory default is OS-relative (a folder
//! under the user's Documents), chosen on first run and overridable via `TDMCP_WORKING_DIR`, the GUI's
//! Working-dir Apply, or arm.json. This is the guarantee that no local file paths leak into shipped code.

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// The ONE directory the tool may read from and write to. Every executor file operation is
    /// `realpath`-confined under this root. Set by the user; overridable via env / arm.json.
    pub working_dir: PathBuf,

    /// Loopback address of the in-TouchDesigner executor.
    pub executor_host: String, // default "127.0.0.1"
    pub executor_port: u16,     // default 9980

    /// Session token shared with the executor. Empty = dev/open (loopback bind is the boundary);
    /// the header is only sent when this is non-empty.
    #[serde(default)]
    pub token: String,

    /// Logging on/off.
    #[serde(default = "default_true")]
    pub logging_enabled: bool,
}

fn default_true() -> bool {
    true
}

impl Config {
    /// Per-user config file path, e.g. `%APPDATA%\touchdesigner-bridge-mcp\config.toml` on Windows —
    /// resolved at runtime, never hardcoded.
    fn config_path() -> Result<PathBuf> {
        let base = dirs::config_dir().context("no OS per-user config dir")?;
        Ok(base.join("touchdesigner-bridge-mcp").join("config.toml"))
    }

    /// Load existing config, or create defaults on first run.
    ///
    /// ENV-FIRST: when launched embedded (e.g. by an MCP client whose child `%APPDATA%` is
    /// unreliable), setting `TDMCP_WORKING_DIR` selects a fully env-derived config — no dependence on
    /// a config-file location.
    pub fn load_or_init() -> Result<Self> {
        if let Ok(working_dir) = std::env::var("TDMCP_WORKING_DIR") {
            return Ok(Self::from_env(PathBuf::from(working_dir)));
        }
        let path = Self::config_path()?;
        if path.exists() {
            let text = std::fs::read_to_string(&path)
                .with_context(|| format!("reading config at {}", path.display()))?;
            Ok(toml::from_str(&text)?)
        } else {
            let cfg = Self::first_run_wizard()?;
            cfg.save()?;
            Ok(cfg)
        }
    }

    pub fn save(&self) -> Result<()> {
        let path = Self::config_path()?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&path, toml::to_string_pretty(self)?)?;
        Ok(())
    }

    /// First run: pick an OS-relative working directory (a folder under the user's Documents),
    /// NOT a developer path — the GUI's Working-dir Apply lets the user change it before anything is
    /// read or written. The executor is loopback on 9980; the token is empty (dev/open).
    fn first_run_wizard() -> Result<Self> {
        let working_dir = dirs::document_dir()
            .context("no OS documents dir")?
            .join("touchdesigner-bridge-mcp");
        Ok(Self {
            working_dir,
            executor_host: "127.0.0.1".into(),
            executor_port: 9980,
            token: String::new(),
            logging_enabled: true,
        })
    }

    /// Build the whole config from `TDMCP_*` env vars (the embedded/packaged-launch path).
    fn from_env(working_dir: PathBuf) -> Self {
        let token = std::env::var("TDMCP_TOKEN").unwrap_or_default();
        let executor_port = std::env::var("TDMCP_PORT").ok()
            .and_then(|s| s.parse().ok()).unwrap_or(9980);
        // The executor transport is loopback-only by design (token, if any, sent in cleartext over
        // it). A non-loopback host would leak off-box, so clamp back to 127.0.0.1 with a warning.
        let executor_host = {
            let h = std::env::var("TDMCP_HOST").unwrap_or_else(|_| "127.0.0.1".into());
            if is_loopback_host(&h) {
                h
            } else {
                eprintln!("TDMCP_HOST={h:?} is not loopback; the executor transport is loopback-only \
                           - forcing 127.0.0.1");
                "127.0.0.1".into()
            }
        };
        Self {
            working_dir,
            executor_host,
            executor_port,
            token,
            logging_enabled: true,
        }
    }

    /// Environment handed to the executor when it arms, so the Python side also has NO hardcoded
    /// paths. Reserved for the auto-arm path (not yet called while arming is manual).
    #[allow(dead_code)]
    pub fn executor_env(&self) -> Vec<(String, String)> {
        vec![
            ("TDMCP_WORKING_DIR".into(), self.working_dir.display().to_string()),
            ("TDMCP_TOKEN".into(), self.token.clone()),
            ("TDMCP_PORT".into(), self.executor_port.to_string()),
        ]
    }
}

// ────────────────────────────────────────────────────────────────────────────
// arm.json — the SINGLE SOURCE OF TRUTH for the confinement root, port, and token.
//
// `~/.touchdesigner-bridge-mcp/arm.json` (written by the arm step / a future GUI) is read here by the
// gateway so the working directory, port, and token can be driven live with no restart.
// ────────────────────────────────────────────────────────────────────────────

/// The directory holding the bundled `reference/` DATA (recipes.json, catalog.json). This is a CODE /
/// resource location that ships with the binary — deliberately NOT the confinement working dir — so
/// reference lookups (`recipe_reference`, `help`) resolve identically no matter where the user points
/// the working dir. Mirrors the executor's `_REPO_DIR`-based catalog read and Houdini's resource-base
/// pattern. NO hardcoded path:
///   1. `TDMCP_REFERENCE_DIR` / `TDMCP_REPO` env (the GUI-generated arm command / packaging sets REPO),
///      when it actually contains `reference/recipes.json`;
///   2. the first ancestor of the running executable that contains `reference/recipes.json`
///      (dev layout: `<repo>/gateway/target/<profile>/exe` → `<repo>`);
///   3. the process current dir (last-ditch; matches Houdini's fallback).
/// Cached once per process (the exe location is stable); the reference FILE itself is still read fresh
/// on every call, so edits to recipes.json/catalog.json are picked up without a restart.
pub fn reference_base() -> PathBuf {
    static BASE: Mutex<Option<PathBuf>> = Mutex::new(None);
    if let Ok(guard) = BASE.lock() {
        if let Some(p) = guard.as_ref() {
            return p.clone();
        }
    }
    let resolved = resolve_reference_base();
    if let Ok(mut guard) = BASE.lock() {
        *guard = Some(resolved.clone());
    }
    resolved
}

fn resolve_reference_base() -> PathBuf {
    let has_ref = |p: &Path| p.join("reference").join("recipes.json").is_file();
    // 1) explicit env override
    for var in ["TDMCP_REFERENCE_DIR", "TDMCP_REPO"] {
        if let Ok(v) = std::env::var(var) {
            let p = PathBuf::from(v);
            if has_ref(&p) {
                return p;
            }
        }
    }
    // 2) walk the running executable's ancestors for the bundled reference/ dir
    if let Ok(exe) = std::env::current_exe() {
        for anc in exe.ancestors() {
            if has_ref(anc) {
                return anc.to_path_buf();
            }
        }
    }
    // 3) last-ditch: the process working directory
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Location of `~/.touchdesigner-bridge-mcp/arm.json` — home dir via `dirs`, or `%USERPROFILE%`
/// as a fallback.
pub fn arm_json_path() -> Option<PathBuf> {
    let home = dirs::home_dir().or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from))?;
    Some(home.join(".touchdesigner-bridge-mcp").join("arm.json"))
}

/// mtime-keyed cache of the resolved arm.json working directory, so the hot path (every tool call)
/// avoids re-reading + re-canonicalizing on every request.
static WD_CACHE: Mutex<Option<(SystemTime, PathBuf)>> = Mutex::new(None);

/// The confinement ROOT for this call: `working_dir` from arm.json, canonicalized and confirmed to be
/// a directory. On ANY failure (no file, unreadable, unparseable, missing key, not-a-dir) falls back
/// to `fallback` (the process-start `Config.working_dir`).
pub fn resolve_working_dir(fallback: &Path) -> PathBuf {
    arm_working_dir().unwrap_or_else(|| fallback.to_path_buf())
}

/// Read + parse + canonicalize the arm.json `working_dir`, using the mtime cache. `None` on any
/// failure so the caller can fall back.
fn arm_working_dir() -> Option<PathBuf> {
    let path = arm_json_path()?;
    let mtime = std::fs::metadata(&path).ok()?.modified().ok()?;

    if let Ok(guard) = WD_CACHE.lock() {
        if let Some((cached_mtime, cached_dir)) = guard.as_ref() {
            if *cached_mtime == mtime {
                return Some(cached_dir.clone());
            }
        }
    }

    let text = std::fs::read_to_string(&path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let raw = value.get("working_dir").and_then(|v| v.as_str())?;
    let canon = Path::new(raw).canonicalize().ok()?;
    if !canon.is_dir() {
        return None;
    }

    if let Ok(mut guard) = WD_CACHE.lock() {
        *guard = Some((mtime, canon.clone()));
    }
    Some(canon)
}

/// mtime-keyed cache of the parsed arm.json object, shared by the port/token resolvers.
static ARM_CACHE: Mutex<Option<(SystemTime, serde_json::Value)>> = Mutex::new(None);

/// Read + parse `~/.touchdesigner-bridge-mcp/arm.json` into a JSON value, using the mtime cache.
/// `None` on ANY failure so every caller can fall back to its Config value.
fn arm_value() -> Option<serde_json::Value> {
    let path = arm_json_path()?;
    let mtime = std::fs::metadata(&path).ok()?.modified().ok()?;

    if let Ok(guard) = ARM_CACHE.lock() {
        if let Some((cached_mtime, cached_val)) = guard.as_ref() {
            if *cached_mtime == mtime {
                return Some(cached_val.clone());
            }
        }
    }

    let text = std::fs::read_to_string(&path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    if let Ok(mut guard) = ARM_CACHE.lock() {
        *guard = Some((mtime, value.clone()));
    }
    Some(value)
}

/// The executor PORT for this connection: `port` from arm.json when present + valid, else `fallback`.
pub fn resolve_executor_port(fallback: u16) -> u16 {
    arm_value()
        .and_then(|v| v.get("port").and_then(|p| p.as_u64()))
        .and_then(|n| u16::try_from(n).ok())
        .unwrap_or(fallback)
}

/// The executor TOKEN for this connection: non-empty `token` from arm.json when present, else
/// `fallback`.
pub fn resolve_token(fallback: &str) -> String {
    arm_value()
        .and_then(|v| v.get("token").and_then(|t| t.as_str()).map(str::to_owned))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| fallback.to_string())
}

/// 128-bit hex session token from the OS CSPRNG. Reserved for the auto-arm path (the executor is
/// dev/open by default, so the gateway does not generate one at startup).
#[allow(dead_code)]
pub fn generate_token() -> Result<String> {
    let mut buf = [0u8; 16];
    getrandom::getrandom(&mut buf).map_err(|e| anyhow!("CSPRNG failed: {e}"))?;
    Ok(buf.iter().map(|b| format!("{b:02x}")).collect())
}

/// True iff `host` is a loopback address or `localhost` (the executor transport is loopback-only; a
/// non-loopback host is refused so any cleartext token can't be sent off-box).
fn is_loopback_host(host: &str) -> bool {
    let h = host.trim();
    if h.eq_ignore_ascii_case("localhost") {
        return true;
    }
    let h = h.strip_prefix('[').and_then(|s| s.strip_suffix(']')).unwrap_or(h); // [::1] -> ::1
    h.parse::<std::net::IpAddr>().map(|ip| ip.is_loopback()).unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::is_loopback_host;

    #[test]
    fn loopback_host_detection() {
        for ok in ["127.0.0.1", "127.5.6.7", "::1", "[::1]", "localhost", "LocalHost"] {
            assert!(is_loopback_host(ok), "{ok} should be treated as loopback");
        }
        for bad in ["192.168.1.5", "10.0.0.1", "0.0.0.0", "8.8.8.8", "example.com", ""] {
            assert!(!is_loopback_host(bad), "{bad} must NOT be treated as loopback");
        }
    }
}
