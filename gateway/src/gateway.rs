//! The MCP stdio server — the SOLE entry point for the AI client. This is where the security
//! boundary is enforced on the way IN:
//!   - exposes only the fixed, typed endpoint set as MCP tools (schemas from `tools.rs` +
//!     `tool_schema.rs`);
//!   - validates every param (numeric clamps, enums, path allowlists) before it can reach the
//!     executor — via `ToolDef::validate`;
//!   - confines every filesystem path to the configured working directory (`realpath`);
//!   - LOWERS every operator tool onto the generic `create_op` + `set_par` engine (see `lower_operator`);
//!   - emits every call to the audit sink.
//!
//! It never forwards raw code — there is no exec / node_op / raw-scripting tool in the catalog.
//!
//! Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout (the MCP stdio transport). STDOUT
//! carries ONLY protocol frames — all logging goes to stderr (see `main.rs`). Requests are handled
//! one at a time: the executor runs on TD's single main thread, so serializing here keeps the audit
//! log ordered and avoids pointless pile-up at the executor.

use crate::executor::Executor;
use crate::tool_schema::{confine_path, Kind, ToolDef};
use anyhow::{anyhow, Result};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

/// The MCP protocol revision this server implements. If the client asks for a different one we echo
/// theirs back (best-effort negotiation) rather than fail the handshake.
const PROTOCOL_VERSION: &str = "2025-06-18";

/// A single line in the live audit log.
#[derive(Debug, Clone)]
pub struct AuditEvent {
    pub endpoint: String,
    pub summary: String,
    pub ok: bool,
}

pub type AuditSink = tokio::sync::mpsc::UnboundedSender<AuditEvent>;

/// A shared handle to the confinement root; `serve` reads it fresh on every call so a change (via
/// arm.json) takes effect for all future calls.
pub type WorkingDir = Arc<RwLock<PathBuf>>;

/// Max bytes for one inbound `\n`-delimited JSON-RPC frame — the gateway's memory-DoS guard so a
/// single unterminated multi-GB line can never buffer unbounded into memory here. The executor
/// separately enforces a 1 MB request-body cap, so 8 MB is ample headroom for any real inbound frame.
const MAX_INBOUND_LINE_BYTES: usize = 8 * 1024 * 1024;

/// The outcome of reading one inbound frame.
enum Frame {
    /// A complete `\n`-delimited line (newline stripped).
    Line(String),
    /// The line exceeded the cap; it has been drained to the next newline and must be skipped.
    TooLong,
    /// End of input (client disconnected).
    Eof,
}

/// Read one `\n`-delimited line from `reader`, capped at `MAX_INBOUND_LINE_BYTES`. On overflow it
/// drains the remainder of the over-long line and returns `TooLong`, so an oversized frame neither
/// buffers into memory nor kills the session. `buf` is a reusable scratch buffer.
async fn read_capped_line<R: AsyncBufReadExt + Unpin>(reader: &mut R, buf: &mut Vec<u8>) -> Result<Frame> {
    buf.clear();
    let mut overflow = false;
    loop {
        let chunk = reader.fill_buf().await?;
        if chunk.is_empty() {
            return Ok(if overflow {
                Frame::TooLong
            } else if buf.is_empty() {
                Frame::Eof
            } else {
                Frame::Line(String::from_utf8_lossy(buf).into_owned())
            });
        }
        if let Some(pos) = chunk.iter().position(|&b| b == b'\n') {
            if !overflow {
                buf.extend_from_slice(&chunk[..pos]);
                if buf.len() > MAX_INBOUND_LINE_BYTES {
                    overflow = true;
                }
            }
            reader.consume(pos + 1);
            return Ok(if overflow {
                Frame::TooLong
            } else {
                Frame::Line(String::from_utf8_lossy(buf).into_owned())
            });
        }
        let n = chunk.len();
        if !overflow {
            buf.extend_from_slice(chunk);
            if buf.len() > MAX_INBOUND_LINE_BYTES {
                overflow = true;
                buf.clear();
                buf.shrink_to_fit();
            }
        }
        reader.consume(n);
    }
}

/// Serve the MCP tool surface over stdio until stdin closes (the client disconnects).
pub async fn serve(working_dir: WorkingDir, exec: Executor, audit: AuditSink) -> Result<()> {
    let catalog = crate::tools::mvp_catalog();
    let index: HashMap<&str, &ToolDef> = catalog.iter().map(|t| (t.name, t)).collect();

    // Optional action-throttle interval (ms). Off by default (0). PACES destructive tools only.
    let min_action_interval_ms: u64 = std::env::var("TDMCP_MIN_ACTION_INTERVAL_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);

    let mut reader = BufReader::new(tokio::io::stdin());
    let mut linebuf: Vec<u8> = Vec::with_capacity(8192);
    let mut stdout = tokio::io::stdout();

    // Timestamp of the last DESTRUCTIVE tool call, for the optional action throttle. The serve loop
    // handles one request at a time, so a plain `&mut` threaded through the call chain is correct.
    let mut last_action: Option<Instant> = None;

    tracing::info!("MCP stdio server ready — {} tools", catalog.len());

    loop {
        match read_capped_line(&mut reader, &mut linebuf).await? {
            Frame::Eof => break,
            Frame::TooLong => {
                tracing::warn!(
                    "dropped an inbound frame exceeding {} bytes (memory-DoS guard)",
                    MAX_INBOUND_LINE_BYTES
                );
                continue;
            }
            Frame::Line(line) => {
                if line.trim().is_empty() {
                    continue;
                }
                if let Some(response) = handle_line(
                    &line,
                    &working_dir,
                    &exec,
                    min_action_interval_ms,
                    &index,
                    &audit,
                    &mut last_action,
                )
                .await
                {
                    let mut text = serde_json::to_string(&response)?;
                    text.push('\n'); // newline-delimited framing
                    stdout.write_all(text.as_bytes()).await?;
                    stdout.flush().await?;
                }
            }
        }
    }
    Ok(())
}

/// Parse and dispatch one JSON-RPC line. Returns `Some(response)` for requests, `None` for
/// notifications (which carry no `id` and must not be answered).
#[allow(clippy::too_many_arguments)]
async fn handle_line(
    line: &str,
    working_dir: &RwLock<PathBuf>,
    exec: &Executor,
    min_action_interval_ms: u64,
    index: &HashMap<&str, &ToolDef>,
    audit: &AuditSink,
    last_action: &mut Option<Instant>,
) -> Option<Value> {
    let msg: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(_) => return Some(rpc_error(Value::Null, -32700, "parse error")),
    };

    let id = msg.get("id").cloned(); // absent ⇒ notification
    let method = msg.get("method").and_then(Value::as_str).unwrap_or_default();
    let params = msg.get("params").cloned().unwrap_or(Value::Null);

    match method {
        "initialize" => Some(rpc_ok(id?, initialize_result(&params))),
        // Notifications — no response, ever.
        m if m.starts_with("notifications/") => None,
        "ping" => Some(rpc_ok(id?, json!({}))),
        "tools/list" => Some(rpc_ok(id?, tools_list(index))),
        "tools/call" => {
            let id = id?;
            match tools_call(&params, working_dir, exec, min_action_interval_ms, index, audit, last_action).await {
                Ok(result) => Some(rpc_ok(id, result)),
                Err(code_msg) => Some(rpc_error(id, code_msg.0, &code_msg.1)),
            }
        }
        other => {
            // Unknown method: error a request, ignore a notification.
            id.map(|id| rpc_error(id, -32601, &format!("method not found: {other}")))
        }
    }
}

fn initialize_result(params: &Value) -> Value {
    // Echo the client's protocol version when they name one, else advertise ours.
    let version = params
        .get("protocolVersion")
        .and_then(Value::as_str)
        .unwrap_or(PROTOCOL_VERSION);
    json!({
        "protocolVersion": version,
        "capabilities": { "tools": {} },
        "serverInfo": { "name": "touchdesigner-bridge-mcp", "version": env!("CARGO_PKG_VERSION") },
        // Bootstrap handed to the agent ON CONNECT so a fresh agent finds the guided path.
        "instructions": "touchdesigner-bridge-mcp is a DATA-ONLY TouchDesigner control surface for \
building projection-mapping content: you build operator networks (TOPs/CHOPs/SOPs/COMPs/...) from \
typed, schema-validated tools; the USER fires the cooks/renders. There is NO arbitrary-code / script \
path — every capability is a fixed, typed tool. Each operator has its own create-and-configure tool \
(e.g. `blurTOP`) that creates the node and sets its parameters in one call; read live scene state with \
`scene_info`, map a network with `read_network`, adjust an existing node with `set_par`, wire nodes with \
`connect`, look up any operator's full parameter schema with `operator_reference`, and save a TOP's image \
to the working directory with `save_top`."
    })
}

fn tools_list(index: &HashMap<&str, &ToolDef>) -> Value {
    // Stable ordering so the client sees a consistent list.
    let mut tools: Vec<Value> = index.values().map(|t| t.listing()).collect();
    tools.sort_by(|a, b| a["name"].as_str().unwrap_or("").cmp(b["name"].as_str().unwrap_or("")));
    json!({ "tools": tools })
}

/// Destructive tools whose rapid-fire would let a runaway loop / prompt-injection tear down a scene
/// before a human can react. When the action throttle is enabled (`TDMCP_MIN_ACTION_INTERVAL_MS > 0`),
/// each is PACED — a brief sleep so successive destructive calls can't fire back-to-back. This is a
/// safety pacer, NOT a security gate: it never rejects.
const THROTTLED_TOOLS: &[&str] = &["delete_op"];

/// Utility tools answered INSIDE the gateway (native), computed offline — never forwarded to the
/// executor. Mirrors how the Houdini bridge routes its `GATEWAY_NATIVE` set to `native.rs`. `batch` is
/// ALSO gateway-native but has its own special path in `tools_call` (it orchestrates other tools), so it
/// is not listed here; `capabilities` (orientation), `help` (local operator-help lookup), and
/// `recipe_reference` (façade-content workflow recipes) are pure offline reads dispatched by `call_one`.
/// Kept in sync with `scripts/audit_registry_consistency.py`'s `GATEWAY_NATIVE` (exempt from the
/// executor-endpoint invariant).
const GATEWAY_NATIVE: &[&str] = &["td_capabilities", "help", "recipe_reference",
    "glsl_reference", "expr_reference", "code_reference"];

/// Pure helper: how long (if at all) a dispatch must sleep before running `name`. Returns `None` when
/// the throttle is off, the tool isn't destructive, there was no prior action, or enough time elapsed.
fn throttle_delay(name: &str, min_interval_ms: u64, last: Option<Instant>, now: Instant) -> Option<Duration> {
    if min_interval_ms == 0 || !THROTTLED_TOOLS.contains(&name) {
        return None;
    }
    let interval = Duration::from_millis(min_interval_ms);
    let prev = last?; // the first destructive action is never delayed
    let elapsed = now.saturating_duration_since(prev);
    if elapsed < interval {
        Some(interval - elapsed)
    } else {
        None
    }
}

/// Handle `tools/call`. A tool-level failure (unknown tool, validation rejection, executor error) is
/// surfaced as an `isError: true` result the model can react to — NOT a protocol error. Only a
/// genuinely malformed request (`name` not a string) is a JSON-RPC `-32602`.
///
/// `batch` (reserved for a future meta-tool) is the ONE tool handled specially here; every other tool
/// (and every batch sub-op) goes through `call_one`, the single security choke point.
#[allow(clippy::too_many_arguments)]
async fn tools_call(
    params: &Value,
    working_dir: &RwLock<PathBuf>,
    exec: &Executor,
    min_action_interval_ms: u64,
    index: &HashMap<&str, &ToolDef>,
    audit: &AuditSink,
    last_action: &mut Option<Instant>,
) -> std::result::Result<Value, (i64, String)> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or((-32602, "tools/call requires a string 'name'".to_string()))?;
    let arguments = params.get("arguments").cloned().unwrap_or(Value::Null);

    if name == "batch" {
        return run_batch(&arguments, working_dir, exec, min_action_interval_ms, index, audit, last_action).await;
    }
    Ok(call_one(name, &arguments, working_dir, exec, min_action_interval_ms, index, audit, last_action).await)
}

/// Run the `batch` meta-tool: validate the ops list via the batch `ToolDef` (structural gate), then
/// dispatch each op through `call_one` (full per-op validation + confinement + audit + throttle + the
/// operator lowering). Ops run in order; a failing op stops the batch iff `stop_on_error`. Batching is
/// a latency envelope only — it grants no capability a direct call lacks. `batch` ships as a utility
/// tool in `tools.rs`; if it is somehow missing from the catalog this errors cleanly.
#[allow(clippy::too_many_arguments)]
async fn run_batch(
    arguments: &Value,
    working_dir: &RwLock<PathBuf>,
    exec: &Executor,
    min_action_interval_ms: u64,
    index: &HashMap<&str, &ToolDef>,
    audit: &AuditSink,
    last_action: &mut Option<Instant>,
) -> std::result::Result<Value, (i64, String)> {
    let Some(batch_def) = index.get("batch").copied() else {
        return Ok(tool_error("batch", audit, "internal error: 'batch' is not in the catalog".to_string()));
    };
    let fallback = match working_dir.read() {
        Ok(g) => g.clone(),
        Err(_) => return Ok(tool_error("batch", audit, "internal error: working-dir lock poisoned".to_string())),
    };
    let wd = crate::config::resolve_working_dir(&fallback);
    let clean = match batch_def.validate(arguments, &wd) {
        Ok(v) => v,
        Err(e) => return Ok(tool_error("batch", audit, format!("invalid arguments: {e}"))),
    };

    let ops = clean.get("ops").and_then(Value::as_array).cloned().unwrap_or_default();
    let stop_on_error = clean.get("stop_on_error").and_then(Value::as_bool).unwrap_or(false);

    let mut results: Vec<Value> = Vec::with_capacity(ops.len());
    for op in &ops {
        let op_name = op.get("name").and_then(Value::as_str).unwrap_or_default();
        let op_args = op.get("arguments").cloned().unwrap_or(Value::Null);
        let res = call_one(op_name, &op_args, working_dir, exec, min_action_interval_ms, index, audit, last_action).await;
        let ok = !res.get("isError").and_then(Value::as_bool).unwrap_or(false);
        results.push(json!({ "name": op_name, "ok": ok, "result": res }));
        if stop_on_error && !ok {
            break;
        }
    }

    let fail_count = results.iter().filter(|r| !r["ok"].as_bool().unwrap_or(false)).count();
    let ok_count = results.len() - fail_count;
    let stopped_early = results.len() < ops.len();   // stop_on_error broke before running every op
    // Reserve the top-level error wrapper for a BATCH-LEVEL failure (the batch ran but NOTHING succeeded).
    // A PARTIAL failure is normal data the driver reads from ok_count/fail_count/results — not a top-level
    // error (driver-seat P2): a partial success must not read as a total error. Batch-machinery failures
    // (bad args / missing catalog) already errored above via tool_error.
    let batch_level_error = !results.is_empty() && ok_count == 0;
    let payload = json!({ "results": results, "requested": ops.len(),
        "ok_count": ok_count, "fail_count": fail_count, "stopped_early": stopped_early });
    let text = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string());
    emit(audit, "batch", !batch_level_error, format!("batch · {} ok · {} failed{}",
        ok_count, fail_count, if stopped_early { " · stopped early" } else { "" }));
    Ok(json!({ "content": [{ "type": "text", "text": text }], "isError": batch_level_error }))
}

/// Run ONE tool op end-to-end: lookup → `ToolDef::validate` (schema + clamp + path-confine) → optional
/// action-throttle for destructive tools → dispatch → wrap as an MCP result → emit audit → embed any
/// inline image. This is the single security choke point; both a direct `tools/call` and every batch
/// sub-op funnel through here.
///
/// DISPATCH is the TD-specific part:
///   - UTILITY tool (`optype = None`): `exec.call(tool.name, clean)` — straight passthrough.
///   - OPERATOR tool (`optype = Some(..)`): LOWERED to `create_op` + `set_par` (see `lower_operator`).
#[allow(clippy::too_many_arguments)]
async fn call_one(
    name: &str,
    arguments: &Value,
    working_dir: &RwLock<PathBuf>,
    exec: &Executor,
    min_action_interval_ms: u64,
    index: &HashMap<&str, &ToolDef>,
    audit: &AuditSink,
    last_action: &mut Option<Instant>,
) -> Value {
    // Defense in depth: `batch` is dispatched only by the special path in `tools_call`; if it ever
    // reaches here (e.g. as a sub-op) refuse it, so nesting is structurally impossible.
    if name == "batch" {
        return tool_error(name, audit, "'batch' cannot be invoked as a sub-op (no nesting)".to_string());
    }

    // Unknown tool → model-visible error (it can pick a real one).
    let Some(tool) = index.get(name).copied() else {
        return tool_error(name, audit, format!("unknown tool '{name}'"));
    };

    // Read the confinement root fresh, then validate + clamp + confine before anything reaches TD.
    let fallback = match working_dir.read() {
        Ok(g) => g.clone(),
        Err(_) => return tool_error(name, audit, "internal error: working-dir lock poisoned".to_string()),
    };
    let wd = crate::config::resolve_working_dir(&fallback);
    let clean = match tool.validate(arguments, &wd) {
        Ok(v) => v,
        Err(e) => return tool_error(name, audit, format!("invalid arguments: {e}")),
    };

    // Action throttle: PACE (never reject) destructive tools. Off by default. Applied after validation
    // so a rejected call costs nothing.
    if let Some(remaining) = throttle_delay(name, min_action_interval_ms, *last_action, Instant::now()) {
        tokio::time::sleep(remaining).await;
    }
    if min_action_interval_ms > 0 && THROTTLED_TOOLS.contains(&name) {
        *last_action = Some(Instant::now());
    }

    // Dispatch: gateway-native tools compute offline in-process (no executor); other utility tools
    // passthrough to the executor; operator tools lower to the generic engine.
    let outcome = if GATEWAY_NATIVE.contains(&name) {
        crate::native::dispatch(name, &clean, &wd)
    } else {
        match tool.optype {
            None => exec.call(name, clean).await,
            Some(optype) => lower_operator(tool, optype, clean, exec).await,
        }
    };
    match outcome {
        Ok(result) => {
            let text = serde_json::to_string_pretty(&result).unwrap_or_else(|_| result.to_string());
            emit(audit, name, true, summarize(&result));
            let mut content = vec![json!({ "type": "text", "text": text })];
            // Inline-images: `save_top` / `capture_ui` return a confined PNG path — embed it so the caller SEES it.
            // EXCEPT a cold capture_ui (warm=false): its OP Viewer TOP hasn't rendered yet, so the saved PNG is a
            // blank/stale frame — inlining it would look like real output (driver-seat P0). The text result already
            // carries warm=false + a call-again note, so the driver knows to retry.
            if IMAGE_TOOLS.contains(&name) && !is_cold_capture(&name, &result) {
                if let Some(img) = try_embed_image(&result, &wd) {
                    content.push(img);
                }
            }
            json!({ "content": content, "isError": false })
        }
        Err(e) => tool_error(name, audit, e.to_string()),
    }
}

/// LOWER an operator tool onto the generic engine. `clean` is the validated args (declared params +
/// the reserved placement params when present). Steps:
///   1. Pull reserved `op_name`/`parent_path`(default "/")/`pos_x`/`pos_y`.
///   2. `create_op {type, name?, parent, x?, y?}` → take `path` from the result.
///   3. Build a `pars` map from the REMAINING params: scalars pass through; a `NumVec` EXPANDS into one
///      `pars` entry per component name (from the ToolDef's `Kind::NumVec { parts }`).
///   4. If `pars` non-empty: `set_par {op: path, pars}` (per-par failures surface in its result).
///   5. Return `{path, created, applied?}`.
async fn lower_operator(tool: &ToolDef, optype: &str, clean: Value, exec: &Executor) -> Result<Value> {
    let obj = match clean {
        Value::Object(m) => m,
        _ => Map::new(),
    };

    let op_name = obj.get("op_name").and_then(Value::as_str);
    // Only forward `parent` when the caller actually specified one; otherwise let the executor apply
    // its scene-aware default (the /project1 work container, else '/'). Forcing '/' here would clutter
    // the system root and hide new content from the artist's /project1 view.
    let parent_path = obj.get("parent_path").and_then(Value::as_str);
    let pos_x = obj.get("pos_x").and_then(Value::as_f64);
    let pos_y = obj.get("pos_y").and_then(Value::as_f64);

    // 2. create_op.
    let mut create_args = Map::new();
    create_args.insert("type".into(), json!(optype));
    if let Some(n) = op_name {
        create_args.insert("name".into(), json!(n));
    }
    if let Some(pp) = parent_path {
        create_args.insert("parent".into(), json!(pp));
    }
    if let Some(x) = pos_x {
        create_args.insert("x".into(), json!(x));
    }
    if let Some(y) = pos_y {
        create_args.insert("y".into(), json!(y));
    }
    let created = exec.call("create_op", Value::Object(create_args)).await?;
    let path = created
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("create_op did not return a path for optype '{optype}'"))?
        .to_string();

    // 3. Build pars from the remaining params, expanding NumVec tuples into per-component names.
    let mut pars = Map::new();
    for (k, v) in &obj {
        if crate::tool_schema::RESERVED_OP_PARAMS.contains(&k.as_str()) {
            continue;
        }
        match tool.params.iter().find(|p| p.name == k).map(|p| &p.kind) {
            Some(Kind::NumVec { parts }) => {
                if let Some(arr) = v.as_array() {
                    for (i, part) in parts.iter().enumerate() {
                        if let Some(item) = arr.get(i) {
                            pars.insert((*part).to_string(), item.clone());
                        }
                    }
                }
            }
            _ => {
                pars.insert(k.clone(), v.clone());
            }
        }
    }

    // 4 + 5. Apply pars (if any) and build the combined result.
    let mut result = Map::new();
    result.insert("path".into(), json!(path));
    result.insert("created".into(), json!(optype));
    if !pars.is_empty() {
        let set_args = json!({ "op": path, "pars": Value::Object(pars) });
        let set_res = exec.call("set_par", set_args).await?;
        // set_par returns {path, applied, all_applied, failed?, magnitude?}. Lift its fields to the TOP of
        // the create result -> {path, created, applied:{...}, all_applied, failed?} instead of nesting the
        // whole set_par result under "applied" (the doubled applied.applied — driver-seat cosmetic fix).
        if let Some(obj) = set_res.as_object() {
            for key in ["applied", "all_applied", "failed", "magnitude"] {
                if let Some(v) = obj.get(key) {
                    result.insert(key.to_string(), v.clone());
                }
            }
        } else {
            result.insert("applied".into(), set_res);
        }
    }
    Ok(Value::Object(result))
}

/// Tools whose result carries a PNG the caller should SEE inline. `save_top` returns a TOP's own
/// image; `capture_ui` returns an operator's node-viewer rendered from TD's own OP Viewer TOP buffer.
const IMAGE_TOOLS: &[&str] = &["save_top", "capture_ui", "show"];
/// Cap embedded images so an inline PNG never overflows the MCP client's tool-result limit (commonly
/// ~1 MB). base64 expands raw bytes by 4/3, so a ~700 KB PNG → ~933 KB embedded, leaving headroom for
/// the JSON text alongside it. A render over the cap is DOWNSCALED to fit (so the driver still SEES it at
/// any resolution — 1080p/4K on a commercial license included); only if it can't be decoded/shrunk do we
/// fall back to a text note. Either way the call never hard-errors on size (driver-seat #1).
const MAX_IMAGE_BYTES: u64 = 700_000;

/// A cold `capture_ui` (warm=false): its OP Viewer TOP renders on the NEXT frame boundary, so the PNG it
/// just saved is a blank/stale frame. Inlining it would look like real output — so we skip the embed and
/// let the driver retry off the warm=false + call-again note (driver-seat P0). Only `capture_ui` is
/// frame-latent this way; `save_top`/`show` render synchronously and always inline.
fn is_cold_capture(name: &str, result: &Value) -> bool {
    name == "capture_ui" && result.get("warm").and_then(Value::as_bool) == Some(false)
}

/// If `result` carries an image path that confines under `wd`: return an MCP `image` content block
/// (base64) when it fits the inline cap, or a `text` NOTE block when it's OVER the cap (so an oversized
/// render never hard-errors the call). Returns None only when there is no usable image file.
fn try_embed_image(result: &Value, wd: &std::path::Path) -> Option<Value> {
    let raw = find_image_path(result)?;
    let confined = confine_path(wd, &raw, false).ok()?;
    let meta = std::fs::metadata(&confined).ok()?;
    if !meta.is_file() || meta.len() == 0 {
        return None;
    }
    // Oversized: an inline image over the client's ~1 MB tool-result limit hard-errors the whole call.
    // First try to DOWNSCALE it so the driver still SEES the render; only if that fails (undecodable /
    // can't shrink enough) fall back to a text note — never a dead turn (driver-seat #1).
    if meta.len() > MAX_IMAGE_BYTES {
        if let Some(block) = downscale_to_fit(&confined, MAX_IMAGE_BYTES) {
            return Some(block);
        }
        return Some(json!({ "type": "text", "text": format!(
            "[preview not inlined: {} is {} KB and could not be downscaled to fit the ~{} KB inline cap — \
             use top_info for numeric state; the full-resolution image is saved at that path.]",
            raw, meta.len() / 1000, MAX_IMAGE_BYTES / 1000) }));
    }
    let bytes = std::fs::read(&confined).ok()?;
    let mime = if raw.to_lowercase().ends_with(".png") { "image/png" } else { "image/jpeg" };
    Some(json!({ "type": "image", "data": base64_encode(&bytes), "mimeType": mime }))
}

/// Decode `path` and shrink it (halving the long edge each pass) until the re-encoded PNG fits under
/// `cap`, so a big render still inlines as a VISIBLE (downscaled) image instead of a text note — the real
/// "downscale-to-fit" (driver-seat #1: `show`/`save_top` must show *something* at any resolution, 4K
/// included). Returns an MCP `image` content block, or None if the file can't be decoded (non-PNG /
/// corrupt) or can't be shrunk enough — the caller then falls back to a note.
fn downscale_to_fit(path: impl AsRef<std::path::Path>, cap: u64) -> Option<Value> {
    let img = image::open(path.as_ref()).ok()?;
    let (nw, nh) = (img.width().max(1), img.height().max(1));
    let mut divisor: u32 = 1;
    loop {
        let w = (nw / divisor).max(32);
        let h = (nh / divisor).max(32);
        let scaled = img.resize(w, h, image::imageops::FilterType::Triangle);
        let mut buf: Vec<u8> = Vec::new();
        if scaled
            .write_to(&mut std::io::Cursor::new(&mut buf), image::ImageFormat::Png)
            .is_err()
        {
            return None;
        }
        if (buf.len() as u64) <= cap {
            return Some(json!({ "type": "image", "data": base64_encode(&buf), "mimeType": "image/png" }));
        }
        if w <= 32 && h <= 32 {
            return None; // shrank as far as sensible and still over the cap
        }
        divisor = divisor.saturating_mul(2);
    }
}

/// First string value anywhere in the result that looks like an image path.
fn find_image_path(v: &Value) -> Option<String> {
    match v {
        Value::String(s) => {
            let l = s.to_lowercase();
            if l.ends_with(".png") || l.ends_with(".jpg") || l.ends_with(".jpeg") {
                Some(s.clone())
            } else {
                None
            }
        }
        Value::Object(m) => m.values().find_map(find_image_path),
        Value::Array(a) => a.iter().find_map(find_image_path),
        _ => None,
    }
}

/// Standard base64 (RFC 4648), inlined to avoid an extra crate dependency on the offline build.
fn base64_encode(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(T[(n >> 18 & 63) as usize] as char);
        out.push(T[(n >> 12 & 63) as usize] as char);
        out.push(if chunk.len() > 1 { T[(n >> 6 & 63) as usize] as char } else { '=' });
        out.push(if chunk.len() > 2 { T[(n & 63) as usize] as char } else { '=' });
    }
    out
}

/// Build an `isError` tool result and record it in the audit log.
fn tool_error(name: &str, audit: &AuditSink, message: String) -> Value {
    emit(audit, name, false, message.clone());
    json!({ "content": [{ "type": "text", "text": message }], "isError": true })
}

fn emit(audit: &AuditSink, endpoint: &str, ok: bool, summary: String) {
    let _ = audit.send(AuditEvent { endpoint: endpoint.to_string(), ok, summary: truncate(&summary, 200) });
}

/// A short one-line summary of a success payload for the audit log.
fn summarize(result: &Value) -> String {
    match result {
        Value::Object(m) => {
            let keys: Vec<&str> = m.keys().map(String::as_str).collect();
            format!("ok · {{{}}}", keys.join(", "))
        }
        Value::Null => "ok".to_string(),
        other => truncate(&other.to_string(), 120),
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let cut: String = s.chars().take(max).collect();
        format!("{cut}…")
    }
}

// ---- JSON-RPC envelope helpers ----

fn rpc_ok(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn capped_reader_drops_oversized_frame_and_recovers() {
        let big = "x".repeat(MAX_INBOUND_LINE_BYTES + 10);
        let input = format!("{{\"a\":1}}\n{{\"b\":2}}\n{big}\n{{\"c\":3}}\n");
        let mut reader = BufReader::new(input.as_bytes());
        let mut buf = Vec::new();

        assert!(matches!(read_capped_line(&mut reader, &mut buf).await.unwrap(), Frame::Line(l) if l == r#"{"a":1}"#));
        assert!(matches!(read_capped_line(&mut reader, &mut buf).await.unwrap(), Frame::Line(l) if l == r#"{"b":2}"#));
        assert!(matches!(read_capped_line(&mut reader, &mut buf).await.unwrap(), Frame::TooLong));
        assert!(matches!(read_capped_line(&mut reader, &mut buf).await.unwrap(), Frame::Line(l) if l == r#"{"c":3}"#));
        assert!(matches!(read_capped_line(&mut reader, &mut buf).await.unwrap(), Frame::Eof));
    }

    #[tokio::test]
    async fn capped_reader_accepts_frame_at_limit() {
        let input = format!("{}\n", "y".repeat(MAX_INBOUND_LINE_BYTES));
        let mut reader = BufReader::new(input.as_bytes());
        let mut buf = Vec::new();
        assert!(matches!(read_capped_line(&mut reader, &mut buf).await.unwrap(),
                         Frame::Line(l) if l.len() == MAX_INBOUND_LINE_BYTES));
    }

    #[test]
    fn throttle_paces_destructive_but_not_normal() {
        let now = Instant::now();
        assert_eq!(throttle_delay("delete_op", 0, Some(now), now), None);
        assert_eq!(throttle_delay("set_par", 1000, Some(now), now), None);

        let d = throttle_delay("delete_op", 1000, Some(now), now).expect("must delay");
        assert!(d > Duration::from_millis(500) && d <= Duration::from_millis(1000), "got {d:?}");

        assert_eq!(throttle_delay("delete_op", 1000, None, now), None);

        let long_ago = now.checked_sub(Duration::from_secs(5)).unwrap();
        assert_eq!(throttle_delay("delete_op", 1000, Some(long_ago), now), None);

        for t in THROTTLED_TOOLS {
            assert!(throttle_delay(t, 1000, Some(now), now).is_some(), "{t} should be throttled");
        }
        assert_eq!(throttle_delay("delete_op_now", 1000, Some(now), now), None);
    }
}

/// Boundary tests over the SHIPPED, GENERATED catalog (`crate::tools::mvp_catalog`). `tools.rs` is
/// generated and never hand-edited, so these assertions — run at `cargo test` time — are the build-time
/// enforcement SECURITY.md mandates: the data-only surface can never grow an arbitrary-code primitive,
/// and every operator tool is a typed create-and-configure tool with the reserved placement args.
#[cfg(test)]
mod catalog_tests {
    use crate::tool_schema::{Kind, RESERVED_OP_PARAMS, ToolDef};
    use crate::tools::mvp_catalog;
    use serde_json::json;

    /// THE load-bearing invariant (mirrors Houdini's `catalog_never_exposes_rce_tools`): the public
    /// catalog must contain no arbitrary-code primitive.
    ///
    /// Two shapes, matched precisely so the guarantee holds without false positives:
    ///   * GENERIC RCE-DRIVER names — a tool literally named `exec`/`eval`/`run_code`/`node_op`/
    ///     `wrangle`/`run`/`shell`/`python` would be a general-purpose code door. Checked by EXACT
    ///     name match (a typed per-operator tool like `evaluateDAT` — an expression-table op, not a
    ///     Python-exec — legitimately contains the substring "eval" and must NOT trip this).
    ///   * CODE-CARRYING OPERATOR families — `script`/`execute`/`cplusplus` optypes run user code or
    ///     load native plugins. Checked as a SUBSTRING over every tool name AND optype, the build-time
    ///     mirror of the executor's runtime `check_optype_allowed`. The generated surface must not ship
    ///     one (they were classified BLOCKED at catalog time).
    #[test]
    fn catalog_never_exposes_rce_tools() {
        let catalog = mvp_catalog();

        const BANNED_EXACT: [&str; 8] =
            ["exec", "eval", "run_code", "node_op", "wrangle", "run", "shell", "python"];
        for t in &catalog {
            assert!(
                !BANNED_EXACT.contains(&t.name),
                "catalog must never expose a generic RCE-driver tool named '{}'",
                t.name
            );
        }

        const CODE_MARKERS: [&str; 3] = ["script", "execute", "cplusplus"];
        for t in &catalog {
            for marker in CODE_MARKERS {
                assert!(
                    !t.name.to_lowercase().contains(marker),
                    "tool name '{}' contains code-carrying marker '{marker}'",
                    t.name
                );
                if let Some(optype) = t.optype {
                    assert!(
                        !optype.to_lowercase().contains(marker),
                        "operator tool '{}' has code-carrying optype '{optype}' (marker '{marker}')",
                        t.name
                    );
                }
            }
        }
    }

    /// F3 FENCE — GLSL operators expose shader SOURCE only as a DAT NodePath, never a settable Str VALUE.
    /// The ~7 GLSL create tools (glslTOP/glslmultiTOP/glslMAT/glslPOP/glslcopyPOP/glsladvancedPOP/
    /// glslselectPOP) take their shader program from a REFERENCED DAT (style "DAT" -> Kind::NodePath); no
    /// tool can put text INTO a DAT, so a DAT ref is data, not a code sink (why they need no _DENY entry).
    /// This property was previously believed but unfenced. This test turns RED if a
    /// regen or new GLSL op ever exposes shader source as an inline Str VALUE. `cargo test` only (no bridge).
    #[test]
    fn glsl_ops_expose_shader_source_only_as_nodepath_never_str() {
        // Reviewed shader-SOURCE params per GLSL op (reference/catalog.json); every one is style "DAT"
        // -> must lower to Kind::NodePath. Keep in sync: a regen adding an inline-source param must be
        // reviewed here (and MUST be a DAT ref, or the boundary is broken).
        const GLSL_SHADER_SOURCE_PARAMS: &[(&str, &str)] = &[
            ("glslmultiTOP", "predat"), ("glslmultiTOP", "vertexdat"),
            ("glslmultiTOP", "pixeldat"), ("glslmultiTOP", "computedat"),
            ("glslTOP", "predat"), ("glslTOP", "vertexdat"),
            ("glslTOP", "pixeldat"), ("glslTOP", "computedat"),
            ("glslMAT", "predat"), ("glslMAT", "vdat"), ("glslMAT", "pdat"), ("glslMAT", "gdat"),
            ("glsladvancedPOP", "computedat"), ("glslPOP", "computedat"),
            ("glslcopyPOP", "ptcomputedat"), ("glslcopyPOP", "vertcomputedat"),
            ("glslcopyPOP", "primcomputedat"),
            // glslselectPOP has NO shader-source param (only selects an output) -- nothing to guard.
        ];

        let catalog = mvp_catalog();
        let is_glsl = |t: &ToolDef| -> bool {
            t.optype.map_or(false, |o| o.to_lowercase().contains("glsl")) || t.name.contains("glsl")
        };
        // `Kind` has no Debug (tool_schema.rs); name it by hand for messages (same as the sibling fence).
        let kind_name = |k: &Kind| -> &'static str {
            match k {
                Kind::Str => "Str",
                Kind::NodePath => "NodePath",
                Kind::Bool => "Bool",
                Kind::Num { .. } => "Num",
                Kind::Int { .. } => "Int",
                Kind::Enum(_) => "Enum",
                _ => "Other",
            }
        };
        // A shader PROGRAM input name (vs a uniform/attribute identifier or a mode enum): on GLSL ops
        // every "*dat" param is a shader/preprocessor DAT ref; the shader*/glslsrc clauses catch a future
        // inline-source param. Narrow on purpose: `shaderdispatchmode` (Menu) ends "mode",
        // `deformdata`/`pcaptdata` end "data" -- none end "dat".
        let looks_like_shader_source = |name: &str| -> bool {
            let n = name.to_lowercase();
            n.ends_with("dat") || n.ends_with("shader") || n.contains("shadersource")
                || n.contains("shadertext") || n.contains("shadercode") || n.contains("glslsrc")
        };

        // (1) Every known shader-source param, where its op is shipped, is present AND a NodePath.
        for (ot, par) in GLSL_SHADER_SOURCE_PARAMS {
            let op_shipped = catalog.iter().any(|t| t.optype == Some(*ot) || t.name == *ot);
            if !op_shipped {
                continue; // op dropped from the surface -> nothing to set -> safe
            }
            let mut checked = false;
            for t in &catalog {
                if t.optype != Some(*ot) && t.name != *ot {
                    continue;
                }
                if let Some(p) = t.params.iter().find(|p| p.name == *par) {
                    checked = true;
                    assert!(
                        matches!(p.kind, Kind::NodePath),
                        "GLSL shader-source param '{ot}.{par}' must be Kind::NodePath (a DAT reference) \
                         but is {} -- a settable shader-source VALUE would be an F3 code-sink hole",
                        kind_name(&p.kind)
                    );
                }
            }
            assert!(
                checked,
                "GLSL op '{ot}' is shipped but its reviewed shader-source param '{par}' is missing -- \
                 renamed? The replacement must be a DAT NodePath; then update this fence."
            );
        }

        // (2) Forward-looking: NO glsl* op may expose ANY shader-source-looking param as a Str.
        for t in &catalog {
            if !is_glsl(t) {
                continue;
            }
            for p in &t.params {
                if looks_like_shader_source(p.name) {
                    assert!(
                        matches!(p.kind, Kind::NodePath),
                        "GLSL op '{}' exposes shader-source-looking param '{}' as {}, not a NodePath -- \
                         an inline shader-source VALUE is an F3 hole; make it a DAT NodePath or DENY it",
                        t.name, p.name, kind_name(&p.kind)
                    );
                }
            }
        }

        // (3) Sanity: the GLSL op set is actually present, so (1)/(2) really ran.
        const EXPECTED_GLSL_OPS: &[&str] = &[
            "glslmultiTOP", "glslTOP", "glslMAT",
            "glsladvancedPOP", "glslcopyPOP", "glslPOP", "glslselectPOP",
        ];
        assert!(
            catalog.iter().filter(|t| is_glsl(t)).count() >= 1,
            "no GLSL operator tools found -- this fence would be vacuous"
        );
        for ot in EXPECTED_GLSL_OPS {
            assert!(
                catalog.iter().any(|t| t.optype == Some(*ot) || t.name == *ot),
                "expected GLSL op '{ot}' is no longer on the surface -- if intentional, remove it from \
                 EXPECTED_GLSL_OPS; if accidental, the create tool regressed"
            );
        }
    }

    /// Regression FENCE over the code-eval boundary gap.
    /// A comprehensive audit of all 509 operators / ~17k params found
    /// 18 parameters whose settable string VALUE TouchDesigner itself evaluates as CODE (Python
    /// expression / Tscript / script / GLSL) — NOT a parameter `.expr` (which `set_par` never touches).
    /// `check_optype_allowed` (markers `script`/`execute`/`cplusplus`) cannot reach them.
    ///
    /// The gap is CLOSED in three coordinated places, and this fence guards the SHIPPED surface:
    ///   1. the build-time catalog generator (denylist `DENY_CODE_SINK_PARS`) DROPS every sink from the
    ///      generated catalog — so the MCP never exposes a code-eval param. This test asserts each sink
    ///      is ABSENT from `mvp_catalog()`.
    ///   2. `td_executor/server.py::_DENY_CODE_SINK_PARS` + `check_par_allowed` REFUSE each sink at the
    ///      executor (defense in depth: a direct loopback `set_par` is blocked). `test_code_sink_guard.py`
    ///      cross-checks that Python list against `DROPPED_CODE_SINKS` below, so the two artifacts and
    ///      this fence cannot drift apart silently.
    ///   3. The residual CODEY-name scan (below) still turns RED the moment a catalog regen introduces a
    ///      NEW code-named param, forcing a human to audit + classify it.
    ///
    /// Classifications of the residual (kept) code-NAMED params:
    ///   * `SAFE-KIND` — the param is `Bool`/`Num`/`Int`, not a code-text value at all (wrong Kind to
    ///                   carry code). e.g. `parameterDAT.expression` is a Toggle.
    ///   * `CLOSED-ENUM` — the param is an `Enum` over a FIXED reviewed token list (not free code text); the
    ///                   executor maps each token to fixed reviewed bytes. e.g. `device_send.command` (the
    ///                   PJLink Class-1 command allowlist) — no caller string can reach the wire.
    #[test]
    fn code_named_params_are_the_known_reviewed_set() {
        // The 18 code-eval sinks: DROPPED from the generated surface AND denied at the executor. The
        // classification token `EVAL-SINK-DROPPED` is parsed by test_code_sink_guard.py to cross-check
        // the executor's _DENY_CODE_SINK_PARS. Keep in sync with server.py + gen_tools_rs.py.
        const DROPPED_CODE_SINKS: &[(&str, &str, &str)] = &[
            // DAT family — Python expression evaluated from the string value
            ("evaluateDAT", "expr", "EVAL-SINK-DROPPED"),
            ("evaluateDAT", "rowexpr", "EVAL-SINK-DROPPED"),
            ("evaluateDAT", "colexpr", "EVAL-SINK-DROPPED"),
            ("examineDAT", "expression", "EVAL-SINK-DROPPED"),
            ("jsonDAT", "expression", "EVAL-SINK-DROPPED"),
            ("tableDAT", "cellexpr", "EVAL-SINK-DROPPED"),
            ("tableDAT", "fills0expr", "EVAL-SINK-DROPPED"),
            ("insertDAT", "replace0expr", "EVAL-SINK-DROPPED"),
            // CHOP family
            ("dattoCHOP", "rowexpr", "EVAL-SINK-DROPPED"),
            ("dattoCHOP", "colexpr", "EVAL-SINK-DROPPED"),
            ("pipeoutCHOP", "script", "EVAL-SINK-DROPPED"),
            ("expressionCHOP", "expr0expr", "EVAL-SINK-DROPPED"),
            ("waveCHOP", "exprs", "EVAL-SINK-DROPPED"),
            ("clipblenderCHOP", "aend", "EVAL-SINK-DROPPED"),
            // SOP family — "Filter Expression" evaluated per point/primitive
            ("groupSOP", "filter", "EVAL-SINK-DROPPED"),
            ("deleteSOP", "filter", "EVAL-SINK-DROPPED"),
            // COMP family
            ("replicatorCOMP", "tscript", "EVAL-SINK-DROPPED"),
            // MAT family — GLSL expression compiled into the shader
            ("phongMAT", "multitexexpr", "EVAL-SINK-DROPPED"),
        ];

        // Residual code-NAMED params that legitimately remain (reviewed non-sinks). After dropping the
        // sinks, the ONLY exact-CODEY-named survivor is parameterDAT.expression (a Toggle => Bool).
        const REVIEWED_KEPT: &[(&str, &str, &str)] = &[
            ("parameterDAT", "expression", "SAFE-KIND"),
            // device_send.command is a CLOSED Enum of reviewed PJLink Class-1 tokens (never free code text);
            // the executor maps each token to fixed reviewed wire bytes -- no caller string reaches the wire.
            ("device_send", "command", "CLOSED-ENUM"),
        ];
        const CODEY: [&str; 14] = [
            "code", "snippet", "vex", "vexpression", "expr", "exprs", "expression", "rowexpr",
            "colexpr", "script", "python", "glsl", "command", "commands",
        ];

        let catalog = mvp_catalog();

        // 1) Every code-eval sink must be ABSENT from the shipped catalog (dropped by the generator).
        for (ot, par, _) in DROPPED_CODE_SINKS {
            for t in &catalog {
                let is_op = t.optype == Some(*ot) || t.name == *ot;
                if is_op {
                    assert!(
                        !t.params.iter().any(|p| p.name == *par),
                        "code-eval sink '{ot}.{par}' must be DROPPED from the generated surface \
                         (add it to gen_tools_rs.py DENY_CODE_SINK_PARS)"
                    );
                }
            }
        }

        // 2) The residual CODEY-named param surface must be EXACTLY the reviewed-kept set — a NEW
        //    code-named param (or a sink that slipped back in) turns this RED for human review.
        let mut found: Vec<(String, String, &'static str)> = Vec::new();
        for t in &catalog {
            for p in &t.params {
                if CODEY.contains(&p.name) {
                    let kind = match &p.kind {
                        Kind::Str => "Str",
                        Kind::NodePath => "NodePath",
                        Kind::Bool => "Bool",
                        Kind::Num { .. } => "Num",
                        Kind::Int { .. } => "Int",
                        Kind::Enum(_) => "Enum",
                        _ => "Other",
                    };
                    found.push((t.name.to_string(), p.name.to_string(), kind));
                }
            }
        }
        let mut found_keys: Vec<(String, String)> =
            found.iter().map(|(t, p, _)| (t.clone(), p.clone())).collect();
        found_keys.sort();
        let mut want_keys: Vec<(String, String)> =
            REVIEWED_KEPT.iter().map(|(t, p, _)| (t.to_string(), p.to_string())).collect();
        want_keys.sort();
        assert_eq!(
            found_keys, want_keys,
            "residual code-named param surface drifted — a NEW code-named param must be audited + \
             classified (kept as a reviewed non-sink, or dropped as a code sink)."
        );

        // 3) Every reviewed-kept param classified SAFE-KIND must actually be a non-code Kind
        //    (Bool/Num/Int); a regen that turned it into a Str (a real code-text value) turns this RED.
        for (t, p, cls) in REVIEWED_KEPT {
            if *cls == "SAFE-KIND" {
                let kind = found.iter().find(|(ft, fp, _)| ft == t && fp == p).map(|(_, _, k)| *k);
                assert!(
                    matches!(kind, Some("Bool") | Some("Num") | Some("Int")),
                    "'{t}.{p}' is classified SAFE-KIND but is Kind {kind:?} — reclassify: a Str value \
                     here would be a code-text sink"
                );
            }
        }
    }

    #[test]
    fn catalog_names_are_unique() {
        let mut seen = std::collections::HashSet::new();
        for t in mvp_catalog() {
            assert!(seen.insert(t.name), "duplicate tool name in catalog: '{}'", t.name);
        }
    }

    /// Every OPERATOR tool (`optype = Some`) exposes the four reserved placement args in its schema
    /// (so the gateway can LOWER it onto `create_op` + `set_par`); every UTILITY tool (`optype = None`)
    /// does NOT. Verified over the real, generated surface.
    #[test]
    fn operator_tools_carry_reserved_placement_args() {
        let catalog = mvp_catalog();
        let mut op_tools = 0usize;
        for t in &catalog {
            let props = t.input_schema();
            let props = props["properties"].as_object().unwrap();
            let has_reserved = RESERVED_OP_PARAMS.iter().all(|r| props.contains_key(*r));
            let any_reserved = RESERVED_OP_PARAMS.iter().any(|r| props.contains_key(*r));
            match t.optype {
                Some(_) => {
                    assert!(has_reserved, "operator tool '{}' is missing a reserved placement arg", t.name);
                    op_tools += 1;
                }
                None => assert!(!any_reserved, "utility tool '{}' must NOT inject reserved args", t.name),
            }
        }
        assert!(op_tools > 100, "expected the full operator surface, found only {op_tools}");
    }

    /// Exercise the REAL generated surface through `ToolDef::validate` for a NumVec-carrying operator
    /// tool: the tuple validates to a numeric array and the reserved placement args pass through — the
    /// input half of the operator-lowering contract (`gateway.rs::lower_operator` then expands the
    /// NumVec into per-component `set_par` entries).
    #[test]
    fn real_operator_numvec_tool_validates_with_reserved_args() {
        let root = std::env::temp_dir().canonicalize().unwrap();
        let catalog = mvp_catalog();
        // Find the first operator tool that has a NumVec param.
        let found = catalog.iter().find_map(|t| {
            t.optype?;
            let p = t.params.iter().find(|p| matches!(p.kind, Kind::NumVec { .. }))?;
            let parts = match &p.kind {
                Kind::NumVec { parts } => *parts,
                _ => unreachable!(),
            };
            Some((t, p.name, parts))
        });
        let (tool, pname, parts) = found.expect("catalog should have an operator tool with a NumVec param");

        let vec_arg: Vec<f64> = (0..parts.len()).map(|i| i as f64).collect();
        let args = json!({
            pname: vec_arg,
            "op_name": "n1",
            "parent_path": "/project1",
            "pos_x": 1.0,
            "pos_y": 2.0,
        });
        let out = tool.validate(&args, &root).expect("real operator tool must validate clean args");
        assert!(out[pname].is_array(), "NumVec param must validate to a JSON array");
        assert_eq!(out[pname].as_array().unwrap().len(), parts.len());
        assert_eq!(out["op_name"], json!("n1"));
        assert_eq!(out["parent_path"], json!("/project1"));
    }

    /// The `batch` meta-tool ships in the TD catalog exactly ONCE, as a UTILITY tool (`optype = None`)
    /// carrying an `OpList` param `ops` (the structural no-nesting/bounded-size gate) and a `Bool`
    /// `stop_on_error`. Its gateway wiring — `run_batch` dispatching every sub-op through the same
    /// `call_one` gate, and `call_one` defensively refusing a `batch` sub-op — is above; the OpList
    /// structural gate is tested in `tool_schema::tests`. This pins the shipped surface of `batch`.
    #[test]
    fn batch_is_in_catalog_and_unique() {
        let catalog = mvp_catalog();
        let batches: Vec<&crate::tool_schema::ToolDef> =
            catalog.iter().filter(|t| t.name == "batch").collect();
        assert_eq!(batches.len(), 1, "`batch` must appear exactly once in the catalog");
        let b = batches[0];
        assert!(b.optype.is_none(), "`batch` must be a utility tool (optype = None), not an operator tool");
        // It declares an OpList `ops` (required) and a Bool `stop_on_error` (optional).
        let ops = b.params.iter().find(|p| p.name == "ops").expect("`batch` must declare an `ops` param");
        assert!(matches!(ops.kind, Kind::OpList), "`batch.ops` must be an OpList");
        assert!(ops.required, "`batch.ops` must be required");
        let soe = b.params.iter().find(|p| p.name == "stop_on_error")
            .expect("`batch` must declare a `stop_on_error` param");
        assert!(matches!(soe.kind, Kind::Bool), "`batch.stop_on_error` must be a Bool");
        assert!(!soe.required, "`batch.stop_on_error` must be optional");
    }

    /// W3 in-the-loop visibility utilities ship as UTILITY tools with the expected params, and
    /// `capture_ui`'s PNG is wired for inline embed (parity with `save_top`). `set_pos` relocates an
    /// existing node for legible layout; `capture_ui` renders an operator's node viewer from TD's own
    /// OP Viewer TOP buffer (never a screen grab) and returns it inline so the driver SEES it.
    #[test]
    fn visibility_utilities_are_shipped() {
        let catalog = mvp_catalog();

        let sp = catalog.iter().find(|t| t.name == "set_pos").expect("set_pos must be in the catalog");
        assert!(sp.optype.is_none(), "set_pos must be a utility tool (optype = None)");
        let op = sp.params.iter().find(|p| p.name == "op").expect("set_pos must declare `op`");
        assert!(op.required && matches!(op.kind, Kind::NodePath), "set_pos.op must be a required NodePath");
        assert!(sp.params.iter().any(|p| p.name == "x" && matches!(p.kind, Kind::Num { .. })));
        assert!(sp.params.iter().any(|p| p.name == "y" && matches!(p.kind, Kind::Num { .. })));

        let cap = catalog.iter().find(|t| t.name == "capture_ui").expect("capture_ui must be in the catalog");
        assert!(cap.optype.is_none(), "capture_ui must be a utility tool (optype = None)");
        let cop = cap.params.iter().find(|p| p.name == "op").expect("capture_ui must declare `op`");
        assert!(cop.required && matches!(cop.kind, Kind::NodePath), "capture_ui.op must be a required NodePath");
        assert!(
            cap.params.iter().any(|p| p.name == "path" && matches!(p.kind, Kind::FsPath { write: true })),
            "capture_ui must expose a write-confined FsPath `path`"
        );

        // Both image tools must embed their PNG inline so the driver actually SEES the result.
        assert!(super::IMAGE_TOOLS.contains(&"capture_ui"), "capture_ui must be an inline-image tool");
        assert!(super::IMAGE_TOOLS.contains(&"save_top"), "save_top must remain an inline-image tool");
    }

    /// A too-large render must NOT hard-error the call: over the inline cap `try_embed_image` returns a
    /// text NOTE (never an image block that overflows the client's ~1 MB result limit). Driver-seat P0.
    #[test]
    fn oversized_render_returns_note_never_breaks() {
        let wd = std::env::temp_dir().join(format!("tdmcp_imgcap_{}", std::process::id()));
        std::fs::create_dir_all(&wd).unwrap();
        // Under the cap -> inline image block.
        std::fs::write(wd.join("small.png"), vec![0u8; 1000]).unwrap();
        let small = super::try_embed_image(&serde_json::json!({ "saved": "small.png" }), wd.as_path())
            .expect("a small PNG must embed");
        assert_eq!(small["type"], serde_json::json!("image"), "small PNG inlines as an image block");
        // Over the cap -> text NOTE (not None, not an image, not a hard error).
        std::fs::write(wd.join("big.png"), vec![0u8; (super::MAX_IMAGE_BYTES + 1) as usize]).unwrap();
        let big = super::try_embed_image(&serde_json::json!({ "saved": "big.png" }), wd.as_path())
            .expect("an oversized PNG must return a note, not None");
        assert_eq!(big["type"], serde_json::json!("text"), "oversized render returns a text note");
        assert!(big["text"].as_str().unwrap().contains("not inlined"), "note says why");
        // No image path -> None.
        assert!(super::try_embed_image(&serde_json::json!({ "foo": "bar.txt" }), wd.as_path()).is_none());
        let _ = std::fs::remove_dir_all(&wd);
    }

    /// A cold capture_ui (warm=false) must NOT be inlined — a blank frame looks like real output. Warm and
    /// the other image tools inline normally. Driver-seat P0 #2.
    #[test]
    fn cold_capture_ui_is_not_inlined() {
        assert!(super::is_cold_capture("capture_ui", &serde_json::json!({ "warm": false, "saved": "x.png" })),
            "a cold capture_ui must be held back from inlining");
        assert!(!super::is_cold_capture("capture_ui", &serde_json::json!({ "warm": true, "saved": "x.png" })),
            "a warm capture_ui inlines its rendered frame");
        assert!(!super::is_cold_capture("save_top", &serde_json::json!({ "warm": false, "saved": "x.png" })),
            "save_top renders synchronously — never treated as cold");
        assert!(!super::is_cold_capture("show", &serde_json::json!({ "saved": "x.png" })),
            "show renders synchronously — never treated as cold");
    }

    /// A large, VALID render over the inline cap must be DOWNSCALED to an inline image (the driver still
    /// SEES it at any resolution), not dropped to a note. The real downscale-to-fit — driver-seat #1.
    #[test]
    fn oversized_valid_render_downscales_and_inlines() {
        use image::{ImageBuffer, Rgb};
        let wd = std::env::temp_dir().join(format!("tdmcp_dscale_{}", std::process::id()));
        std::fs::create_dir_all(&wd).unwrap();
        // High-entropy (hash-noise) image that encodes to a PNG well over the cap.
        let (w, h) = (1500u32, 1500u32);
        let img = ImageBuffer::from_fn(w, h, |x, y| {
            let n = x.wrapping_mul(2_654_435_761).wrapping_add(y.wrapping_mul(2_246_822_519));
            Rgb([(n >> 3) as u8, (n >> 11) as u8, (n >> 19) as u8])
        });
        img.save(wd.join("big.png")).unwrap();
        assert!(std::fs::metadata(wd.join("big.png")).unwrap().len() > super::MAX_IMAGE_BYTES,
            "the test image must exceed the inline cap to exercise downscaling");
        let block = super::try_embed_image(&serde_json::json!({ "saved": "big.png" }), wd.as_path())
            .expect("an oversized valid render must still inline (downscaled)");
        assert_eq!(block["type"], serde_json::json!("image"),
            "an oversized VALID render must downscale-to-inline, not drop to a note");
        let _ = std::fs::remove_dir_all(&wd);
    }

    /// W4 orientation + local-help utilities ship as GATEWAY-NATIVE utility tools (`optype = None`,
    /// computed in-gateway, no executor endpoint): `capabilities` (no args) and `help` (optype/search).
    /// Both are listed in `GATEWAY_NATIVE` so `call_one` routes them to `native.rs` instead of the
    /// executor, and both are exempt from the audit's executor-endpoint invariant.
    #[test]
    fn native_orientation_utilities_are_shipped() {
        let catalog = mvp_catalog();

        let cap = catalog.iter().find(|t| t.name == "td_capabilities")
            .expect("td_capabilities must be in the catalog");
        assert!(cap.optype.is_none(), "capabilities must be a utility tool (optype = None)");
        assert!(cap.params.is_empty(), "capabilities takes no arguments");

        let help = catalog.iter().find(|t| t.name == "help").expect("help must be in the catalog");
        assert!(help.optype.is_none(), "help must be a utility tool (optype = None)");
        let optype = help.params.iter().find(|p| p.name == "optype").expect("help must declare `optype`");
        assert!(matches!(optype.kind, Kind::Str), "help.optype must be a Str");
        let search = help.params.iter().find(|p| p.name == "search").expect("help must declare `search`");
        assert!(matches!(search.kind, Kind::Str), "help.search must be a Str");

        // Both are routed gateway-native (no executor endpoint), like `batch`.
        assert!(super::GATEWAY_NATIVE.contains(&"td_capabilities"), "td_capabilities must be gateway-native");
        assert!(super::GATEWAY_NATIVE.contains(&"help"), "help must be gateway-native");
    }

    /// W6 governor + animation: `mem` (resource envelope / magnitude pre-check) and `bind_chop`
    /// (data-only CHOP-export param binding) ship as UTILITY tools (`optype = None`) that map to a real
    /// executor endpoint (NOT gateway-native — both need the live TD session), with the expected params.
    #[test]
    fn w6_governor_and_animation_utilities_are_shipped() {
        let catalog = mvp_catalog();

        let mem = catalog.iter().find(|t| t.name == "mem").expect("mem must be in the catalog");
        assert!(mem.optype.is_none(), "mem must be a utility tool (optype = None)");
        // mem is an executor endpoint (reads live RAM in TD's process), NOT gateway-native.
        assert!(!super::GATEWAY_NATIVE.contains(&"mem"), "mem must route to the executor, not native");
        for p in ["op", "optype", "pars"] {
            assert!(mem.params.iter().any(|q| q.name == p), "mem must declare optional `{p}`");
        }
        assert!(mem.params.iter().all(|p| !p.required), "all mem params are optional");

        let bc = catalog.iter().find(|t| t.name == "bind_chop").expect("bind_chop must be in the catalog");
        assert!(bc.optype.is_none(), "bind_chop must be a utility tool (optype = None)");
        assert!(!super::GATEWAY_NATIVE.contains(&"bind_chop"), "bind_chop must route to the executor");
        for p in ["chop", "op", "par"] {
            let q = bc.params.iter().find(|q| q.name == p)
                .unwrap_or_else(|| panic!("bind_chop must declare `{p}`"));
            assert!(q.required, "bind_chop.{p} must be required");
        }
        // `chop`/`op` are node paths, `par` is a plain string (a parameter name, never a code sink).
        assert!(bc.params.iter().any(|p| p.name == "chop" && matches!(p.kind, Kind::NodePath)));
        assert!(bc.params.iter().any(|p| p.name == "par" && matches!(p.kind, Kind::Str)));
        assert!(bc.params.iter().any(|p| p.name == "channel" && !p.required));
    }

    /// W5 drive layer: `recipe_reference` ships as a GATEWAY-NATIVE utility tool (`optype = None`,
    /// computed in-gateway from `reference/recipes.json`, no executor endpoint) exactly once, carrying
    /// the classify/recipe/domain/search modes (all optional Str params). Listed in `GATEWAY_NATIVE` so
    /// `call_one` routes it to `native.rs`, and exempt from the audit's executor-endpoint invariant.
    #[test]
    fn native_recipe_reference_is_shipped() {
        let catalog = mvp_catalog();

        let rrs: Vec<&crate::tool_schema::ToolDef> =
            catalog.iter().filter(|t| t.name == "recipe_reference").collect();
        assert_eq!(rrs.len(), 1, "`recipe_reference` must appear exactly once in the catalog");
        let rr = rrs[0];
        assert!(rr.optype.is_none(), "recipe_reference must be a utility tool (optype = None)");
        for mode in ["classify", "recipe", "domain", "search"] {
            let p = rr.params.iter().find(|p| p.name == mode)
                .unwrap_or_else(|| panic!("recipe_reference must declare a `{mode}` param"));
            assert!(matches!(p.kind, Kind::Str), "recipe_reference.{mode} must be a Str");
            assert!(!p.required, "recipe_reference.{mode} must be optional (all modes are)");
        }

        assert!(super::GATEWAY_NATIVE.contains(&"recipe_reference"),
            "recipe_reference must be gateway-native");
    }

    /// ONE-VALIDATED-PATH FENCE for the GLSL code lane (mirrors Houdini's `safe_vex_is_the_only_code_path`).
    /// The validated GLSL lane introduces the surface's ONLY code-carrying Kind, `Kind::GlslSnippet`. This
    /// fence pins the invariant that keeps it safe:
    ///   (a) every `GlslSnippet` on the surface carries profile "glsl_v1" (the pinned validated ruleset);
    ///   (b) `GlslSnippet` appears ONLY on the two GLSL-lane utilities — `set_glsl` (which DELIVERS it) and
    ///       `validate_glsl` (a dry-run of the SAME validator that writes nothing) — and `set_glsl` is the
    ///       one tool that delivers it into the graph;
    ///   (c) the restated F3 invariant: no DAT `.text` is writable from the tool surface EXCEPT `set_glsl`
    ///       delivering validated+consented GLSL into a derived glslTOP-owned DAT — so no GLSL *operator*
    ///       tool carries an inline `GlslSnippet` (shader source reaches a glslTOP only via a referenced
    ///       DAT NodePath; the per-op NodePath detail is enforced by the sibling F3 fence).
    /// Turns RED if a regen exposes a GlslSnippet on any other tool, drifts its profile, or an inline
    /// shader-source snippet appears on a GLSL create tool. `cargo test` only (no bridge).
    #[test]
    fn glsl_snippet_is_the_only_shader_code_path() {
        let catalog = mvp_catalog();

        // (a)+(b): every GlslSnippet carries profile "glsl_v1", and the ONLY tools bearing one are the
        // two GLSL-lane utilities.
        let mut snippet_tools: Vec<&str> = Vec::new();
        for t in &catalog {
            for p in &t.params {
                if let Kind::GlslSnippet { stage, profile } = &p.kind {
                    assert_eq!(
                        *profile, "glsl_v1",
                        "GlslSnippet param '{}.{}' must carry profile 'glsl_v1' (got '{profile}')",
                        t.name, p.name
                    );
                    assert_eq!(
                        *stage, "pixel",
                        "GlslSnippet param '{}.{}' must carry stage 'pixel' in v1 (got '{stage}')",
                        t.name, p.name
                    );
                    snippet_tools.push(t.name);
                }
            }
        }
        snippet_tools.sort();
        snippet_tools.dedup();
        assert_eq!(
            snippet_tools,
            vec!["set_glsl", "validate_glsl"],
            "GlslSnippet may appear ONLY on the GLSL-lane utilities set_glsl (delivers) + validate_glsl \
             (dry-run); found {snippet_tools:?}"
        );

        // set_glsl is the ONE tool that DELIVERS a shader (a GlslSnippet `source` into a glslTOP-owned DAT).
        let set_glsl = catalog.iter().find(|t| t.name == "set_glsl").expect("set_glsl must ship");
        assert!(set_glsl.optype.is_none(), "set_glsl must be a utility tool (optype = None)");
        assert!(
            set_glsl
                .params
                .iter()
                .any(|p| p.name == "source" && matches!(p.kind, Kind::GlslSnippet { .. })),
            "set_glsl must carry the GlslSnippet `source` param"
        );
        // Its op target is a NodePath and its stage an Enum locked to pixel — source is never a bare Str.
        assert!(
            set_glsl.params.iter().any(|p| p.name == "op" && matches!(p.kind, Kind::NodePath)),
            "set_glsl.op must be a NodePath (the target glslTOP)"
        );
        assert!(
            set_glsl.params.iter().any(|p| p.name == "stage" && matches!(p.kind, Kind::Enum(_))),
            "set_glsl.stage must be an Enum (pixel-only in v1)"
        );

        // validate_glsl carries a GlslSnippet but is a READ-ONLY dry-run — it must NOT deliver anything
        // (utility tool, no op target to write into).
        let validate_glsl =
            catalog.iter().find(|t| t.name == "validate_glsl").expect("validate_glsl must ship");
        assert!(validate_glsl.optype.is_none(), "validate_glsl must be a utility tool (optype = None)");
        assert!(
            !validate_glsl.params.iter().any(|p| p.name == "op"),
            "validate_glsl must be write-free: no `op` target (it only validates, never delivers)"
        );

        // (c) Restated F3 invariant: no GLSL *operator* tool carries an inline GlslSnippet. Shader source
        // reaches a glslTOP only via a referenced DAT NodePath (the per-op NodePath guarantee is enforced
        // in detail by `glsl_ops_expose_shader_source_only_as_nodepath_never_str`); here we echo the
        // one-path conclusion — the sole inline shader-source carrier is set_glsl, into a DAT it owns.
        let is_glsl_operator =
            |t: &ToolDef| t.optype.map_or(false, |o| o.to_lowercase().contains("glsl"));
        for t in &catalog {
            if !is_glsl_operator(t) {
                continue;
            }
            for p in &t.params {
                assert!(
                    !matches!(p.kind, Kind::GlslSnippet { .. }),
                    "GLSL operator '{}' must not carry an inline GlslSnippet '{}' — shader source reaches \
                     a glslTOP only via a referenced DAT, and set_glsl owns that DAT",
                    t.name, p.name
                );
            }
        }
    }

    /// ONE-VALIDATED-PATH FENCE for the parameter-EXPRESSION lane (mirrors the GLSL fence above and
    /// Houdini's `safe_vex_is_the_only_code_path`). The expr lane introduces the surface's second
    /// code-carrying Kind, `Kind::ExprSnippet`. This fence pins the invariant that keeps it safe:
    ///   (a) every `ExprSnippet` on the surface carries profile "expr_v1" (the pinned validated ruleset);
    ///   (b) `ExprSnippet` appears ONLY on the two expr-lane utilities — `set_expr` (which DELIVERS it onto
    ///       a parameter) and `validate_expr` (a dry-run of the SAME validator that writes nothing);
    ///   (c) `set_expr` exposes the expression ONLY as a validated `ExprSnippet` (never a bare `Str`), its
    ///       `op` target is a `NodePath` and its `par` a `Str` — no code-sink Kind — and `validate_expr` is
    ///       write-free (no `op` target). This is the surface half of the boundary; the executor's
    ///       `set_expr` re-runs consent + `validate_expr` + `check_par_allowed` and is authoritative.
    /// Turns RED if a regen exposes an ExprSnippet on any other tool, drifts its profile, or set_expr's
    /// `source` degrades to a bare Str. `cargo test` only (no bridge).
    #[test]
    fn expr_snippet_is_the_only_expr_code_path() {
        let catalog = mvp_catalog();

        // (a)+(b): every ExprSnippet carries profile "expr_v1", and the ONLY tools bearing one are the
        // two expr-lane utilities.
        let mut snippet_tools: Vec<&str> = Vec::new();
        for t in &catalog {
            for p in &t.params {
                if let Kind::ExprSnippet { profile } = &p.kind {
                    assert_eq!(
                        *profile, "expr_v1",
                        "ExprSnippet param '{}.{}' must carry profile 'expr_v1' (got '{profile}')",
                        t.name, p.name
                    );
                    snippet_tools.push(t.name);
                }
            }
        }
        snippet_tools.sort();
        snippet_tools.dedup();
        assert_eq!(
            snippet_tools,
            vec!["set_expr", "validate_expr"],
            "ExprSnippet may appear ONLY on the expr-lane utilities set_expr (delivers) + validate_expr \
             (dry-run); found {snippet_tools:?}"
        );

        // (c) set_expr is the ONE tool that DELIVERS an expression (an ExprSnippet `source` onto a param).
        let set_expr = catalog.iter().find(|t| t.name == "set_expr").expect("set_expr must ship");
        assert!(set_expr.optype.is_none(), "set_expr must be a utility tool (optype = None)");
        assert!(
            set_expr
                .params
                .iter()
                .any(|p| p.name == "source" && matches!(p.kind, Kind::ExprSnippet { .. })),
            "set_expr must carry the ExprSnippet `source` param (never a bare Str)"
        );
        // Its op target is a NodePath and its par name a plain Str — source is the only code-carrying arg.
        assert!(
            set_expr.params.iter().any(|p| p.name == "op" && matches!(p.kind, Kind::NodePath)),
            "set_expr.op must be a NodePath (the target operator)"
        );
        assert!(
            set_expr.params.iter().any(|p| p.name == "par" && matches!(p.kind, Kind::Str)),
            "set_expr.par must be a Str (the target parameter name)"
        );
        // No parameter on set_expr may be a GlslSnippet (the two code lanes stay distinct).
        for p in &set_expr.params {
            assert!(
                !matches!(p.kind, Kind::GlslSnippet { .. }),
                "set_expr must not carry a GlslSnippet param '{}'",
                p.name
            );
        }

        // validate_expr carries an ExprSnippet but is a READ-ONLY dry-run — it must NOT deliver anything
        // (utility tool, no op target to write into).
        let validate_expr =
            catalog.iter().find(|t| t.name == "validate_expr").expect("validate_expr must ship");
        assert!(validate_expr.optype.is_none(), "validate_expr must be a utility tool (optype = None)");
        assert!(
            !validate_expr.params.iter().any(|p| p.name == "op" || p.name == "par"),
            "validate_expr must be write-free: no `op`/`par` target (it only validates, never delivers)"
        );

        // No OPERATOR tool (optype = Some) may carry an inline ExprSnippet — expressions reach a parameter
        // ONLY through the sanctioned set_expr utility, never as a create-tool param.
        for t in &catalog {
            if t.optype.is_none() {
                continue;
            }
            for p in &t.params {
                assert!(
                    !matches!(p.kind, Kind::ExprSnippet { .. }),
                    "operator tool '{}' must not carry an inline ExprSnippet '{}' — expressions reach a \
                     parameter only via the sanctioned set_expr utility",
                    t.name, p.name
                );
            }
        }
    }
}
