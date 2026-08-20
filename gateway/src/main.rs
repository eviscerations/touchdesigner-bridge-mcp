//! touchdesigner-bridge-mcp — the single binary a user runs alongside TouchDesigner and their AI
//! client. It is the whole client-side install. Responsibilities:
//!   1. Resolve the local environment (working directory, executor host/port/token) — see `config`.
//!      NOTHING is hardcoded to a developer machine except the greenfield working-dir default; every
//!      path/host/token is resolved at runtime from per-user config / env / arm.json.
//!   2. Connect to the in-TouchDesigner executor over loopback — see `executor`.
//!   3. Serve the typed MCP tool surface to the AI client over stdio — see `gateway`.
//!
//!   4. Show a TD-themed GUI (working-dir field, log toggle, live audit log) — see `gui`.
//!
//! Default launch = GUI. Set `TDMCP_GW_HEADLESS` (e.g. an embedded MCP-client launch like Claude
//! Desktop) to serve headless instead.

mod config;
mod executor;
mod gateway;
mod gui;
mod native;
mod tool_schema;
mod tools;

use anyhow::Result;
use std::path::Path;
use std::sync::{Arc, RwLock};

fn main() -> Result<()> {
    // Load per-user config FIRST — we need the working directory to place the log file.
    let cfg = config::Config::load_or_init()?;

    // STDOUT IS SACRED: it carries the MCP JSON-RPC stream to the AI client. A single stray line on
    // stdout would corrupt the protocol. Logs go to STDERR (live console) AND a sequential file in the
    // working directory. Hold the guard for the process lifetime so the file writer flushes.
    let _log_guard = init_logging(&cfg.working_dir);

    // Embedded launch (e.g. Claude Desktop, a packaged app): serve headless — no GUI window; the
    // audit stream goes to the log file. Otherwise the GUI owns the lifecycle, spawning the gateway +
    // executor client on a background runtime and rendering the working-dir field, log toggle, and
    // live audit log.
    if std::env::var("TDMCP_GW_HEADLESS").is_ok() {
        return run_headless(cfg);
    }
    gui::run(cfg)
}

/// Serve the MCP gateway with no GUI. Audit events are logged to the log file.
///
/// The typed catalog is large (500+ tools / 14k+ params). Constructing `mvp_catalog()` and serializing
/// `tools/list` needs far more stack than Windows' 1 MB main-thread default (a stack overflow otherwise),
/// so the whole serve path runs on an explicit 64 MB-stack thread, with 16 MB tokio worker stacks.
fn run_headless(cfg: config::Config) -> Result<()> {
    let child = std::thread::Builder::new()
        .name("mcp-serve".into())
        .stack_size(64 * 1024 * 1024)
        .spawn(move || -> Result<()> {
            let rt = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .thread_stack_size(16 * 1024 * 1024)
                .build()?;
            let working_dir = Arc::new(RwLock::new(cfg.working_dir.clone()));
            let exec = executor::Executor::connect(&cfg);
            let (audit_tx, mut audit_rx) = tokio::sync::mpsc::unbounded_channel::<gateway::AuditEvent>();
            rt.spawn(async move {
                while let Some(ev) = audit_rx.recv().await {
                    tracing::info!(target: "audit", "{} {} — {}", if ev.ok { "OK " } else { "ERR" }, ev.endpoint, ev.summary);
                }
            });
            tracing::info!("touchdesigner-bridge-mcp serving headless (working_dir = {})", cfg.working_dir.display());
            rt.block_on(gateway::serve(working_dir, exec, audit_tx))
        })?;
    child.join().map_err(|_| anyhow::anyhow!("serve thread panicked"))?
}

/// Set up logging: a live stderr layer + a sequential per-run log file in the working directory
/// (`touchdesigner-bridge-mcp_0001.log`, `_0002.log`, …). Returns the non-blocking writer guard.
fn init_logging(working_dir: &Path) -> Option<tracing_appender::non_blocking::WorkerGuard> {
    use tracing_subscriber::prelude::*;

    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    let stderr_layer = tracing_subscriber::fmt::layer().with_writer(std::io::stderr);

    let (file_layer, guard) = match open_log_file(working_dir) {
        Some(file) => {
            let (nb, guard) = tracing_appender::non_blocking(file);
            let layer = tracing_subscriber::fmt::layer().with_ansi(false).with_writer(nb);
            (Some(layer), Some(guard))
        }
        None => (None, None),
    };

    tracing_subscriber::registry()
        .with(filter)
        .with(stderr_layer)
        .with(file_layer)
        .init();
    guard
}

/// Create (if needed) the working directory and open the next sequential log file in it.
fn open_log_file(working_dir: &Path) -> Option<std::fs::File> {
    std::fs::create_dir_all(working_dir).ok()?;
    let mut max = 0u32;
    if let Ok(entries) = std::fs::read_dir(working_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if let Some(mid) = name.strip_prefix("touchdesigner-bridge-mcp_").and_then(|s| s.strip_suffix(".log")) {
                if let Ok(n) = mid.parse::<u32>() {
                    max = max.max(n);
                }
            }
        }
    }
    let path = working_dir.join(format!("touchdesigner-bridge-mcp_{:04}.log", max + 1));
    std::fs::File::create(path).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn log_files_grow_sequentially_on_reuse() {
        let mut dir = std::env::temp_dir();
        dir.push(format!("tdmcp_log_test_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);

        assert!(open_log_file(&dir).is_some());
        assert!(dir.join("touchdesigner-bridge-mcp_0001.log").exists());

        assert!(open_log_file(&dir).is_some());
        assert!(dir.join("touchdesigner-bridge-mcp_0002.log").exists());

        assert!(open_log_file(&dir).is_some());
        assert!(dir.join("touchdesigner-bridge-mcp_0003.log").exists());

        let _ = std::fs::remove_dir_all(&dir);
    }
}
