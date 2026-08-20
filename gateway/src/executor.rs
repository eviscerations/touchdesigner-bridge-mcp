//! Client to the in-TouchDesigner Python executor over loopback. The gateway never talks to TD
//! directly — it goes through this typed client. There is NO raw-code path: only named endpoints
//! with validated params reach the executor.
//!
//! Wire contract (matches `td_executor/server.py`):
//!   - `GET  /health` (also `GET /`) — unauthenticated liveness; body
//!       `{"ok": true, "service": "td-bridge-mcp", "version": .., "td": <build>, "endpoints": [..]}`.
//!   - `POST /tool/{name}` — JSON body = params; auth header `X-TDMCP-Token` (sent only when a token
//!       is configured; the executor defaults to open on loopback, so an empty token sends no header).
//!       success       → 200 `{"ok": true,  "result": <value>}`
//!       handler error → 400/403/404/413/422/500 `{"ok": false, "error": "<msg>"}`
//!     TD deliberately does NOT leak tracebacks — there is no `traceback` field to surface.
//!   - 1 MB body cap on the executor side.

use crate::config::Config;
use anyhow::{anyhow, Result};
use std::time::Duration;

/// The executor runs each handler on TD's main thread; give a generous ceiling for a legitimately
/// slow call before giving up.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(90);

pub struct Executor {
    base_url: String, // http://{host}:{port}
    /// Startup fallback token (from Config/env). The LIVE token is resolved from arm.json fresh on
    /// every call (see `call`), so a re-arm that mints a new token takes effect with NO gateway
    /// restart — matching how working_dir/port are resolved live. This field is only the fallback for
    /// when arm.json has no token.
    fallback_token: String,
    http: reqwest::Client,
}

impl Executor {
    pub fn connect(cfg: &Config) -> Self {
        // `~/.touchdesigner-bridge-mcp/arm.json` wins over the Config defaults when present, so the
        // gateway hits the SAME port the executor armed on. (Token is resolved per-call in `call`.)
        let port = crate::config::resolve_executor_port(cfg.executor_port);
        // A client-wide timeout that clears the executor's main-thread ceiling. `build()` only fails
        // on TLS/config init, impossible for a plain loopback client, so fall back to the default.
        let http = reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        Self {
            base_url: format!("http://{}:{}", cfg.executor_host, port),
            fallback_token: cfg.token.clone(),
            http,
        }
    }

    /// `GET /health` — is the executor armed inside a live TD session, and which build?
    /// Returns `(reachable, td_build)`. A transport error (nothing listening yet) is reported as
    /// `(false, None)` rather than an error, so a caller shows a "disconnected" state instead of
    /// crashing while TD isn't up.
    #[allow(dead_code)]
    pub async fn health(&self) -> (bool, Option<String>) {
        let url = format!("{}/health", self.base_url);
        match self.http.get(&url).send().await {
            Ok(resp) => {
                let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::Value::Null);
                let ok = body.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                let build = body.get("td").and_then(|v| v.as_str()).map(str::to_owned);
                (ok, build)
            }
            Err(_) => (false, None),
        }
    }

    /// Invoke a typed endpoint by name with already-validated params. The gateway is responsible for
    /// schema validation, numeric clamps, and path confinement BEFORE calling this.
    ///
    /// Returns the handler's `result` value on success; maps every executor-side failure to an `Err`
    /// carrying the message.
    pub async fn call(&self, endpoint: &str, params: serde_json::Value) -> Result<serde_json::Value> {
        let url = format!("{}/tool/{}", self.base_url, endpoint);
        let mut req = self.http.post(&url).json(&params);
        // Resolve the token FRESH from arm.json on every call (mtime-cached), so a re-arm that mints a
        // new token authenticates with no gateway restart. Send the header only when a token is set;
        // the executor is open on loopback by default (empty token), so an empty header is unnecessary.
        let token = crate::config::resolve_token(&self.fallback_token);
        if !token.is_empty() {
            req = req.header("X-TDMCP-Token", &token);
        }
        let resp = req
            .send()
            .await
            .map_err(|e| anyhow!("executor unreachable at {url}: {e}"))?;

        let status = resp.status();
        let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::Value::Null);

        // Success envelope: {"ok": true, "result": <value>}.
        if body.get("ok").and_then(|v| v.as_bool()) == Some(true) {
            return Ok(body.get("result").cloned().unwrap_or(serde_json::Value::Null));
        }

        // Failure: `{"ok": false, "error": "<msg>"}` (no traceback field — TD does not leak them).
        let msg = body
            .get("error")
            .and_then(|v| v.as_str())
            .map(str::to_owned)
            .unwrap_or_else(|| format!("executor returned HTTP {status} with no error message"));
        Err(anyhow!("{endpoint} failed: {msg}"))
    }
}
