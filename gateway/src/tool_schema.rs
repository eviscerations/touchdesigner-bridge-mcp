//! The hand-written security-boundary machinery for the typed tool surface.
//!
//! `tools.rs` is GENERATED (one create-and-configure tool per TouchDesigner operator + the utility
//! tools) and depends on the types and logic in THIS file:
//!   `use crate::tool_schema::{Kind::*, Param as P, ToolDef};`
//!
//! From each `ToolDef`'s typed `Param`s we derive BOTH the MCP `inputSchema` a client sees AND the
//! server-side validation that every `tools/call` must pass before anything reaches the executor.
//! Nothing is free-form: a numeric param is clamped to a range, an enum to a fixed set, a filesystem
//! path is `realpath`-confined to the working directory, and `ParMap` (the generic `set_par` values
//! map) accepts VALUES ONLY — no nested objects, so it can never smuggle a code-shaped structure.
//! There is deliberately NO tool for arbitrary code / raw scripting — those simply do not exist in
//! the catalog, so the boundary cannot be talked past.
//!
//! OPERATOR TOOLS (`optype = Some(..)`) additionally carry four RESERVED optional params that are NOT
//! declared in `params` — `op_name`, `parent_path`, `pos_x`, `pos_y` — injected here into both the
//! schema and the validator. `gateway.rs` reads them back to LOWER the operator tool onto the generic
//! `create_op` + `set_par` engine.

use anyhow::{bail, Result};
use serde_json::{json, Map, Value};
use std::path::Path;

/// One parameter's type and validation rule. The kind drives schema generation and clamping.
pub enum Kind {
    /// Free-ish string (node name, keyword). Non-empty when required.
    Str,
    /// A TouchDesigner operator path (e.g. `/geo1/blur1`) — a string, not a filesystem path.
    NodePath,
    /// A filesystem path — `realpath`-confined to the working dir. `write` allows a not-yet-existing
    /// leaf (the parent chain is still confined); read requires the file to resolve under the root.
    FsPath { write: bool },
    Int { min: i64, max: i64 },
    Num { min: f64, max: f64 },
    Bool,
    /// Fixed choice set (menu tokens, modes).
    Enum(&'static [&'static str]),
    /// A fixed-length numeric tuple. `parts` are the per-component TD parameter names (e.g.
    /// `["tx","ty","tz"]`); the schema length is `parts.len()`, and operator-tool lowering EXPANDS the
    /// tuple into one `pars` entry per component name.
    NumVec { parts: &'static [&'static str] },
    /// A free `{parName: value}` map for the generic `set_par` — VALUES ONLY. Each value must be a
    /// string, number, bool, or array-of-number. Nested objects (and anything else) are REJECTED, so
    /// no code-shaped structure can pass. This is the one open-keyed param; it still carries no code.
    ParMap,
    /// A list of tool ops for the `batch` meta-tool: an array (1..=64) of `{name, arguments?}`
    /// objects. Validated STRUCTURALLY ONLY here — each sub-op is re-looked-up and re-validated against
    /// its REAL target tool at dispatch time, exactly as a direct call would be. Nesting is refused
    /// (an op named "batch" is rejected). Declared by the shipped `batch` utility tool (`tools.rs`).
    OpList,
    /// A GLSL shader-source snippet (the validated code lane). Carried by `set_glsl`/`validate_glsl`
    /// ONLY. The schema is a plain string; the gateway does a CHEAP structural pre-check here (ASCII,
    /// non-empty, length ceiling) purely for a fast client error — the EXECUTOR's `validate_glsl` is
    /// the AUTHORITATIVE fail-closed DoS/hygiene constrainer (static loop bounds, texture-fetch caps,
    /// `#include`/`while` banned, stage gate). This is the ONLY code-carrying Kind on the surface; the
    /// tools exposing it are greppable + fenced (`glsl_snippet_is_the_only_shader_code_path`). `profile`
    /// pins the validated ruleset version (`"glsl_v1"`); `stage` names the shader stage (`"pixel"`).
    GlslSnippet { stage: &'static str, profile: &'static str },
    /// A TouchDesigner parameter-EXPRESSION snippet (the second validated code lane). Carried by
    /// `set_expr`/`validate_expr` ONLY. The schema is a plain string; the gateway does a CHEAP structural
    /// pre-check here (non-empty, single line, ASCII, length ceiling) purely for a fast client error — the
    /// EXECUTOR's `validate_expr` (an `ast.parse(mode="eval")` + strict allowlist NodeVisitor) is the
    /// AUTHORITATIVE fail-closed validator; the loopback port is reachable by any authed caller, so we
    /// NEVER assume "only the gateway calls us". Fenced by `expr_snippet_is_the_only_expr_code_path`.
    /// `profile` pins the validated ruleset version (`"expr_v1"`).
    ExprSnippet { profile: &'static str },
}

pub struct Param {
    pub name: &'static str,
    pub required: bool,
    pub kind: Kind,
    pub desc: &'static str,
}

pub struct ToolDef {
    pub name: &'static str,
    /// Display category (source-of-truth for a doc generator's grouping; not sent over MCP).
    #[allow(dead_code)]
    pub category: &'static str,
    pub description: &'static str,
    /// `Some(optype)` => an OPERATOR tool: `gateway.rs` LOWERS it to `create_op` + `set_par`.
    /// `None` => a UTILITY tool: dispatched straight to the executor endpoint of the same name.
    pub optype: Option<&'static str>,
    pub params: Vec<Param>,
}

/// The four reserved optional params injected into every OPERATOR tool (`optype = Some`). They are
/// NOT in `params`; the generic engine reads them to place the new operator.
pub(crate) const RESERVED_OP_PARAMS: [&str; 4] = ["op_name", "parent_path", "pos_x", "pos_y"];

impl Param {
    pub const fn req(name: &'static str, kind: Kind, desc: &'static str) -> Self {
        Param { name, required: true, kind, desc }
    }
    pub const fn opt(name: &'static str, kind: Kind, desc: &'static str) -> Self {
        Param { name, required: false, kind, desc }
    }

    /// The JSON-Schema fragment for this one parameter.
    fn schema(&self) -> Value {
        let mut s = match &self.kind {
            Kind::Str | Kind::NodePath | Kind::FsPath { .. } => json!({ "type": "string" }),
            Kind::Int { min, max } => json!({ "type": "integer", "minimum": min, "maximum": max }),
            Kind::Num { min, max } => json!({ "type": "number", "minimum": min, "maximum": max }),
            Kind::Bool => json!({ "type": "boolean" }),
            Kind::Enum(choices) => json!({ "type": "string", "enum": choices }),
            Kind::NumVec { parts } => json!({
                "type": "array", "items": { "type": "number" },
                "minItems": parts.len(), "maxItems": parts.len()
            }),
            Kind::ParMap => json!({ "type": "object" }),
            Kind::GlslSnippet { stage, profile } => json!({
                "type": "string",
                "description": format!(
                    "{} (GLSL {stage} shader source, profile {profile}; the source is validated \
                     executor-side — the authoritative check — before it is compiled).",
                    self.desc
                ),
            }),
            Kind::ExprSnippet { profile } => json!({
                "type": "string",
                "maxLength": 512,
                "description": format!(
                    "{} (a single-line TouchDesigner parameter expression, profile {profile}; the source \
                     is validated executor-side — the authoritative AST-allowlist check — before it is \
                     written onto the parameter).",
                    self.desc
                ),
            }),
            Kind::OpList => json!({
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": { "type": "string" },
                        "arguments": { "type": "object" }
                    },
                    "required": ["name"],
                    "additionalProperties": false
                }
            }),
        };
        if let Some(obj) = s.as_object_mut() {
            // Most kinds get the param's `desc` verbatim; a kind that already composed its own
            // description (e.g. GlslSnippet, folding in stage/profile) keeps it.
            obj.entry("description").or_insert_with(|| json!(self.desc));
        }
        s
    }
}

/// JSON-Schema fragments for the four reserved operator params (schema + validation share this).
fn reserved_schema(name: &str) -> Value {
    match name {
        "op_name" => json!({ "type": "string", "description": "Optional name for the new operator (auto-named when omitted)." }),
        "parent_path" => json!({ "type": "string", "description": "Path of the parent network to create the operator inside. Omit to use the default work container /project1 (falls back to '/')." }),
        "pos_x" => json!({ "type": "number", "description": "Optional network editor X position for the new operator." }),
        "pos_y" => json!({ "type": "number", "description": "Optional network editor Y position for the new operator." }),
        _ => json!({}),
    }
}

impl ToolDef {
    /// The full MCP `inputSchema` (JSON Schema object) generated from the params. For operator tools
    /// the four reserved placement params are INJECTED (optional; never `required`).
    pub fn input_schema(&self) -> Value {
        let mut props = Map::new();
        let mut required = Vec::new();
        for p in &self.params {
            props.insert(p.name.to_string(), p.schema());
            if p.required {
                required.push(json!(p.name));
            }
        }
        if self.optype.is_some() {
            for name in RESERVED_OP_PARAMS {
                props.insert(name.to_string(), reserved_schema(name));
            }
        }
        json!({
            "type": "object",
            "properties": Value::Object(props),
            "required": required,
            "additionalProperties": false
        })
    }

    /// The `tools/list` entry a client sees.
    pub fn listing(&self) -> Value {
        json!({
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema()
        })
    }

    /// Validate caller `arguments` against this tool and return a cleaned param object to send to the
    /// executor: unknown keys rejected, required keys enforced, numerics clamped to range, enums
    /// checked, filesystem paths `realpath`-confined to `working_dir`, and (for operator tools) the
    /// reserved placement params accepted + normalized. This is the gate; if it returns `Ok`, the
    /// payload is safe to forward.
    pub fn validate(&self, args: &Value, working_dir: &Path) -> Result<Value> {
        let obj = match args {
            Value::Object(m) => m.clone(),
            Value::Null => Map::new(),
            _ => bail!("arguments must be a JSON object"),
        };

        let has_reserved = self.optype.is_some();

        // Reject any parameter we didn't declare — no smuggling unknown keys past the boundary.
        // Self-teaching rejection: name the rule AND list the valid params (+ a did-you-mean).
        for key in obj.keys() {
            let known = self.params.iter().any(|p| p.name == key)
                || (has_reserved && RESERVED_OP_PARAMS.contains(&key.as_str()));
            if !known {
                let mut valid: Vec<&str> = self.params.iter().map(|p| p.name).collect();
                if has_reserved {
                    valid.extend_from_slice(&RESERVED_OP_PARAMS);
                }
                let hint = if key.len() >= 2 {
                    valid
                        .iter()
                        .find(|n| n.contains(key.as_str()) || key.contains(*n))
                        .map(|n| format!(" (did you mean '{n}'?)"))
                        .unwrap_or_default()
                } else {
                    String::new()
                };
                let list = if valid.is_empty() {
                    "(this tool takes no parameters)".to_string()
                } else {
                    valid.join(", ")
                };
                bail!(
                    "unknown parameter '{key}' for tool '{}'{hint}. Valid parameters: {list}",
                    self.name
                );
            }
        }

        let mut out = Map::new();
        for p in &self.params {
            let Some(v) = obj.get(p.name) else {
                if p.required {
                    bail!("missing required parameter '{}' for tool '{}'", p.name, self.name);
                }
                continue;
            };
            out.insert(p.name.to_string(), validate_value(p, v, working_dir)?);
        }

        // Operator tools: validate + carry the reserved placement params when present.
        if has_reserved {
            if let Some(v) = obj.get("op_name") {
                let s = v.as_str().ok_or_else(|| anyhow::anyhow!("parameter 'op_name' must be a string"))?;
                if s.is_empty() {
                    bail!("parameter 'op_name' must not be empty");
                }
                out.insert("op_name".into(), json!(s));
            }
            if let Some(v) = obj.get("parent_path") {
                let s = v.as_str().ok_or_else(|| anyhow::anyhow!("parameter 'parent_path' must be a string"))?;
                if s.is_empty() {
                    bail!("parameter 'parent_path' must not be empty");
                }
                out.insert("parent_path".into(), json!(s));
            }
            for axis in ["pos_x", "pos_y"] {
                if let Some(v) = obj.get(axis) {
                    let n = v.as_f64().ok_or_else(|| anyhow::anyhow!("parameter '{axis}' must be a number"))?;
                    if !n.is_finite() {
                        bail!("parameter '{axis}' must be a finite number");
                    }
                    out.insert(axis.into(), json!(n));
                }
            }
        }

        Ok(Value::Object(out))
    }
}

/// Validate + normalize a single value against its declared kind. Numerics are CLAMPED (not
/// rejected) into range — a request for 10^9 becomes the ceiling, never an OOM.
fn validate_value(p: &Param, v: &Value, working_dir: &Path) -> Result<Value> {
    let err = |what: &str| format!("parameter '{}' {}", p.name, what);
    Ok(match &p.kind {
        Kind::Str => {
            let s = v.as_str().ok_or_else(|| anyhow::anyhow!(err("must be a string")))?;
            if s.is_empty() {
                bail!(err("must not be empty"));
            }
            json!(s)
        }
        Kind::NodePath => {
            let s = v.as_str().ok_or_else(|| anyhow::anyhow!(err("must be an operator path string")))?;
            if s.is_empty() {
                bail!(err("must not be empty"));
            }
            json!(s)
        }
        Kind::FsPath { write } => {
            let s = v.as_str().ok_or_else(|| anyhow::anyhow!(err("must be a path string")))?;
            // Confine-CHECK ONLY, then pass the caller's ORIGINAL clean value through unchanged.
            // We mirror the executor's `_guard_par_value`, which calls `confined_path(v)` purely to
            // REJECT escapes and then writes the untouched `v`. Rewriting the value to the return of
            // `confine_path` (a `canonicalize`d path) is wrong on Windows: `canonicalize` emits a
            // `\\?\`-prefixed extended-length (verbatim) path, which TouchDesigner then MISRESOLVES as
            // RELATIVE, so the file fails to load. Confinement is preserved (an escaping path still
            // raises below AND is re-confined executor-side); only the value forwarded to TD changes.
            confine_path(working_dir, s, *write)?;
            json!(s)
        }
        Kind::Int { min, max } => {
            let n = v.as_i64().ok_or_else(|| anyhow::anyhow!(err("must be an integer")))?;
            json!(n.clamp(*min, *max))
        }
        Kind::Num { min, max } => {
            let n = v.as_f64().ok_or_else(|| anyhow::anyhow!(err("must be a number")))?;
            if !n.is_finite() {
                bail!(err("must be a finite number"));
            }
            json!(n.clamp(*min, *max))
        }
        Kind::Bool => {
            let b = v.as_bool().ok_or_else(|| anyhow::anyhow!(err("must be a boolean")))?;
            json!(b)
        }
        Kind::Enum(choices) => {
            let s = v.as_str().ok_or_else(|| anyhow::anyhow!(err("must be a string")))?;
            if !choices.contains(&s) {
                bail!(err(&format!("must be one of {choices:?}")));
            }
            json!(s)
        }
        Kind::NumVec { parts } => {
            let len = parts.len();
            let arr = v.as_array().ok_or_else(|| anyhow::anyhow!(err("must be an array")))?;
            if arr.len() != len {
                bail!(err(&format!("must have exactly {len} numbers")));
            }
            let mut nums = Vec::with_capacity(len);
            for item in arr {
                let n = item.as_f64().ok_or_else(|| anyhow::anyhow!(err("must be numbers")))?;
                if !n.is_finite() {
                    bail!(err("must be finite numbers"));
                }
                nums.push(n);
            }
            json!(nums)
        }
        Kind::ParMap => {
            // VALUES ONLY: string | number | bool | array-of-number. Nested objects and nulls are
            // rejected so the generic set_par can never carry a code-shaped structure.
            let map = v
                .as_object()
                .ok_or_else(|| anyhow::anyhow!(err("must be an object of {parName: value}")))?;
            let mut out = Map::new();
            for (k, val) in map {
                let ok = match val {
                    Value::String(_) | Value::Bool(_) => true,
                    Value::Number(n) => n.as_f64().map(|f| f.is_finite()).unwrap_or(false),
                    Value::Array(a) => a
                        .iter()
                        .all(|e| e.as_f64().map(|f| f.is_finite()).unwrap_or(false)),
                    _ => false, // null / nested object → rejected
                };
                if !ok {
                    bail!(err(&format!(
                        "value for '{k}' must be a string, number, bool, or array of numbers (no nested objects)"
                    )));
                }
                out.insert(k.clone(), val.clone());
            }
            Value::Object(out)
        }
        Kind::GlslSnippet { stage, profile } => {
            // CHEAP structural pre-check ONLY — a fast client error, NOT the security boundary. The
            // executor's `validate_glsl` is the AUTHORITATIVE fail-closed validator (static loop bounds,
            // texture-fetch caps, `#include`/`while` banned, stage gate); we do NOT port it to Rust.
            // Here: non-empty, ASCII (GLSL source is ASCII; kills homoglyph/unicode tricks), length
            // ceiling (compile-DoS + reviewability cap). Newlines/tabs are legal ASCII and preserved.
            const GLSL_MAX_BYTES: usize = 16 * 1024;
            let s = v.as_str().ok_or_else(|| anyhow::anyhow!(err("must be a GLSL source string")))?;
            if s.is_empty() {
                bail!(err("must not be empty"));
            }
            if s.len() > GLSL_MAX_BYTES {
                bail!(err(&format!(
                    "GLSL {stage} source exceeds the {GLSL_MAX_BYTES}-byte pre-check ceiling (profile {profile})"
                )));
            }
            if !s.is_ascii() {
                bail!(err("GLSL source must be ASCII (non-ASCII characters are rejected)"));
            }
            json!(s)
        }
        Kind::ExprSnippet { profile } => {
            // CHEAP structural pre-check ONLY — a fast client error, NOT the security boundary. The
            // executor's `validate_expr` is the AUTHORITATIVE fail-closed validator (ast.parse mode="eval"
            // + strict node/name/call/attribute allowlist). Here: non-empty, single line, ASCII (kills the
            // unicode-homoglyph/NFKC identifier vector), length ceiling (matches the validator's 512 cap).
            const EXPR_MAX_BYTES: usize = 512;
            let s = v
                .as_str()
                .ok_or_else(|| anyhow::anyhow!(err("must be a parameter-expression string")))?;
            if s.is_empty() {
                bail!(err("must not be empty"));
            }
            if s.len() > EXPR_MAX_BYTES {
                bail!(err(&format!(
                    "expression exceeds the {EXPR_MAX_BYTES}-byte pre-check ceiling (profile {profile})"
                )));
            }
            if s.contains('\n') || s.contains('\r') {
                bail!(err("expression must be a single line (no newlines)"));
            }
            if !s.is_ascii() {
                bail!(err("expression must be ASCII (non-ASCII characters are rejected)"));
            }
            json!(s)
        }
        Kind::OpList => {
            // STRUCTURAL validation ONLY. Each op must be `{name: <non-empty string>, arguments?: object}`.
            // Per-op tool validation is deferred to dispatch, which re-looks-up + re-validates each sub-op
            // against its REAL target tool. Nesting is refused (an op named "batch" is rejected).
            let arr = v.as_array().ok_or_else(|| anyhow::anyhow!(err("must be an array of ops")))?;
            if arr.is_empty() {
                bail!(err("must contain at least 1 op"));
            }
            if arr.len() > 64 {
                bail!(err("must contain at most 64 ops"));
            }
            for (i, item) in arr.iter().enumerate() {
                let op = item
                    .as_object()
                    .ok_or_else(|| anyhow::anyhow!(err(&format!("op #{i} must be an object"))))?;
                for k in op.keys() {
                    if k != "name" && k != "arguments" {
                        bail!(err(&format!("op #{i} has unexpected key '{k}' (only name, arguments)")));
                    }
                }
                let name = op
                    .get("name")
                    .and_then(Value::as_str)
                    .ok_or_else(|| anyhow::anyhow!(err(&format!("op #{i} must have a string 'name'"))))?;
                if name.is_empty() {
                    bail!(err(&format!("op #{i} 'name' must not be empty")));
                }
                if name == "batch" {
                    bail!(err(&format!("op #{i} 'name' must not be 'batch' (batch cannot nest)")));
                }
                if let Some(a) = op.get("arguments") {
                    if !a.is_object() {
                        bail!(err(&format!("op #{i} 'arguments' must be an object")));
                    }
                }
            }
            Value::Array(arr.clone())
        }
    })
}

/// `realpath`-confine a caller-supplied path to the working directory (defense-in-depth over the
/// executor's own `confined_path`). Resolves the longest existing ancestor with `canonicalize`
/// (following symlinks, collapsing `..`) then re-checks it is under the canonical root — closing the
/// junction/symlink-inside-root escape. For writes, a not-yet-existing leaf is allowed as long as its
/// resolved parent chain stays confined. Returns the confined absolute path as a string.
pub(crate) fn confine_path(working_dir: &Path, raw: &str, write: bool) -> Result<String> {
    let root = working_dir
        .canonicalize()
        .map_err(|e| anyhow::anyhow!("working directory {} is not accessible: {e}", working_dir.display()))?;

    let candidate = {
        let p = Path::new(raw);
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            root.join(p)
        }
    };

    // Canonicalize the deepest existing prefix; keep the trailing (not-yet-existing) remainder.
    let mut existing = candidate.as_path();
    let mut remainder: Vec<std::ffi::OsString> = Vec::new();
    let resolved_existing = loop {
        match existing.canonicalize() {
            Ok(c) => break c,
            Err(_) => match existing.parent() {
                Some(parent) => {
                    match existing.file_name() {
                        Some(name) => remainder.push(name.to_os_string()),
                        // A trailing `..` (ParentDir) has no file_name; fail closed rather than
                        // silently drop it (which would redirect a write to an unnamed path).
                        None => {
                            if existing.components().next_back()
                                == Some(std::path::Component::ParentDir)
                            {
                                bail!("path '{raw}' contains a '..' segment that does not resolve under the working directory");
                            }
                        }
                    }
                    existing = parent;
                }
                None => bail!("path '{raw}' cannot be resolved"),
            },
        }
    };

    // Reassemble: resolved existing prefix + the non-existent tail (which contained no symlinks
    // because it doesn't exist), then confirm confinement.
    let mut resolved = resolved_existing;
    for name in remainder.iter().rev() {
        resolved.push(name);
    }

    if !resolved.starts_with(&root) {
        bail!("path '{raw}' escapes the working directory");
    }
    if !write && resolved.metadata().is_err() {
        bail!("path '{raw}' does not exist under the working directory");
    }
    Ok(resolved.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    //! Boundary-machinery tests (parity with the Houdini gateway's `tool_schema`/`tools.rs` tests,
    //! adapted to the TD types): path confinement, typed validation/clamp/enum/required/unknown-key,
    //! NumVec length, the values-only `ParMap`, the reserved operator-placement args, and the OpList
    //! structural gate (the batch envelope). `tools.rs` is GENERATED and never edited — the boundary
    //! logic under test all lives HERE.
    use super::*;
    use serde_json::json;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};

    static CTR: AtomicU64 = AtomicU64::new(0);

    /// A fresh, canonicalized temp directory as a confinement root (unique per call).
    fn tmp_root() -> std::path::PathBuf {
        let n = CTR.fetch_add(1, Ordering::Relaxed);
        let mut p = std::env::temp_dir();
        p.push(format!("tdmcp_ts_{}_{}", std::process::id(), n));
        fs::create_dir_all(&p).unwrap();
        p.canonicalize().unwrap()
    }

    // ────────────────────────────── path confinement (SECURITY.md: twice-confined) ──────────────

    #[test]
    fn confine_allows_relative_write_leaf() {
        // A not-yet-existing write target whose parent chain stays confined is allowed.
        let root = tmp_root();
        let got = confine_path(&root, "previews/out.png", true).unwrap();
        assert!(got.starts_with(&root.to_string_lossy().to_string()), "got {got}");
    }

    #[test]
    fn confine_allows_existing_path_under_root() {
        let root = tmp_root();
        fs::write(root.join("in.png"), b"x").unwrap();
        assert!(confine_path(&root, "in.png", false).is_ok());
    }

    #[test]
    fn confine_rejects_parent_escape() {
        let root = tmp_root();
        assert!(confine_path(&root, "../evil.txt", true).is_err());
    }

    #[test]
    fn confine_rejects_absolute_outside() {
        let root = tmp_root();
        let outside = if cfg!(windows) {
            "C:\\Windows\\System32\\drivers\\etc\\hosts"
        } else {
            "/etc/passwd"
        };
        assert!(confine_path(&root, outside, false).is_err());
    }

    #[test]
    fn confine_read_requires_existing() {
        let root = tmp_root();
        assert!(confine_path(&root, "does_not_exist.png", false).is_err());
    }

    #[test]
    fn confine_rejects_parent_dir_traversal_in_tail() {
        // A `..` reaching the not-yet-existing tail must be REJECTED, never silently rewritten to a
        // path above the root. write=true is the vulnerable mode (read already fails the exists check).
        let root = tmp_root();
        assert!(confine_path(&root, "out.png", true).is_ok(), "clean leaf must resolve");
        for bad in [
            "newdir/../../etc/passwd",
            "a/b/../../../../tmp/pwned.png",
            "../escape.png",
            "../../etc/passwd",
        ] {
            assert!(confine_path(&root, bad, true).is_err(), "must reject traversal '{bad}'");
        }
    }

    // The Windows tunneling vector: a directory junction INSIDE the working dir that points OUTSIDE
    // it must not let a read/write tunnel out — `canonicalize` resolves the junction to its target,
    // so `starts_with(root)` fails. Junctions (mklink /J) need no admin, unlike symlinks.
    #[cfg(windows)]
    #[test]
    fn confine_rejects_junction_escaping_root() {
        let root = tmp_root();
        let outside = tmp_root();
        fs::write(outside.join("secret.txt"), b"top secret").unwrap();
        let link = root.join("escape");
        let made = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J", link.to_str().unwrap(), outside.to_str().unwrap()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        assert!(made, "mklink /J failed — cannot exercise the junction-escape gate");

        assert!(confine_path(&root, "escape/secret.txt", false).is_err(),
            "read through an escaping junction was NOT rejected");
        assert!(confine_path(&root, "escape/planted.txt", true).is_err(),
            "write through an escaping junction was NOT rejected");
        fs::write(root.join("ok.txt"), b"fine").unwrap();
        assert!(confine_path(&root, "ok.txt", false).is_ok(), "a genuine in-root path must still pass");

        let _ = std::process::Command::new("cmd").args(["/C", "rmdir", link.to_str().unwrap()]).status();
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
    }

    // ── FsPath lowering passes the caller's CLEAN value through (no `\\?\` / backslash mangling) ──
    // A confined absolute path must reach the executor VERBATIM (forward slashes, no extended-length
    // prefix). Regression guard for the create-tool bug: `confine_path` returns a `canonicalize`d
    // path, which on Windows carries a `\\?\` verbatim prefix that TD then misresolves as RELATIVE,
    // so the file fails to load. The FsPath lowering must confine-CHECK yet forward the original `s`.
    #[test]
    fn fspath_confined_absolute_passes_through_unchanged() {
        let root = tmp_root();
        // Use a clean, forward-slashed absolute path UNDER the root (write leaf need not exist).
        let clean = format!("{}/assets/sections/section_00.obj", root.to_string_lossy().replace('\\', "/"));
        let t = tool(vec![Param::opt("file", Kind::FsPath { write: false }, "")]);
        // Read confinement requires the file to exist; create it so we exercise the existing-path lane.
        fs::create_dir_all(root.join("assets").join("sections")).unwrap();
        fs::write(root.join("assets").join("sections").join("section_00.obj"), b"o").unwrap();
        let out = t.validate(&json!({ "file": clean.clone() }), &root).unwrap();
        let got = out["file"].as_str().unwrap();
        assert_eq!(got, clean, "confined path must pass through byte-for-byte");
        assert!(!got.contains("\\\\?\\"), "must not carry a Windows `\\\\?\\` verbatim prefix: {got}");
        assert!(!got.contains('\\'), "must not mangle forward slashes into backslashes: {got}");
    }

    #[test]
    fn fspath_write_leaf_absolute_passes_through_unchanged() {
        // A not-yet-existing WRITE target (the typical create-and-configure case) also passes verbatim.
        let root = tmp_root();
        let clean = format!("{}/previews/out.png", root.to_string_lossy().replace('\\', "/"));
        let t = tool(vec![Param::opt("path", Kind::FsPath { write: true }, "")]);
        let out = t.validate(&json!({ "path": clean.clone() }), &root).unwrap();
        assert_eq!(out["path"].as_str().unwrap(), clean, "confined write leaf must pass through verbatim");
    }

    #[test]
    fn fspath_escaping_path_is_still_rejected() {
        // Confinement is intact: an out-of-working-dir absolute path AND a `..` escape both reject,
        // so the security boundary the pass-through fix must not weaken still holds.
        let root = tmp_root();
        let t = tool(vec![Param::opt("file", Kind::FsPath { write: true }, "")]);
        let outside = if cfg!(windows) {
            "C:/Windows/System32/drivers/etc/hosts"
        } else {
            "/etc/passwd"
        };
        assert!(t.validate(&json!({ "file": outside }), &root).is_err(), "outside-root path must reject");
        assert!(t.validate(&json!({ "file": "../evil.obj" }), &root).is_err(), "`..` escape must reject");
    }

    // ────────────────────────────── typed validation (clamp / enum / required / unknown) ─────────

    fn tool(params: Vec<Param>) -> ToolDef {
        ToolDef { name: "t", category: "_test", description: "", optype: None, params }
    }

    #[test]
    fn validate_clamps_int_to_range_not_reject() {
        let root = tmp_root();
        let t = tool(vec![Param::opt("n", Kind::Int { min: 1, max: 10 }, "")]);
        assert_eq!(t.validate(&json!({ "n": 10_000 }), &root).unwrap()["n"], json!(10));
        assert_eq!(t.validate(&json!({ "n": -5 }), &root).unwrap()["n"], json!(1));
        assert_eq!(t.validate(&json!({ "n": 7 }), &root).unwrap()["n"], json!(7));
    }

    #[test]
    fn validate_clamps_num_to_range_not_reject() {
        let root = tmp_root();
        let t = tool(vec![Param::opt("x", Kind::Num { min: 0.0, max: 1.0 }, "")]);
        assert_eq!(t.validate(&json!({ "x": 5.0 }), &root).unwrap()["x"], json!(1.0));
        assert_eq!(t.validate(&json!({ "x": -2.0 }), &root).unwrap()["x"], json!(0.0));
    }

    #[test]
    fn validate_rejects_unknown_param() {
        let root = tmp_root();
        let t = tool(vec![]);
        assert!(t.validate(&json!({ "smuggled": 1 }), &root).is_err());
    }

    #[test]
    fn validate_enforces_required() {
        let root = tmp_root();
        let t = tool(vec![Param::req("x", Kind::Str, "")]);
        assert!(t.validate(&json!({}), &root).is_err());
        assert!(t.validate(&json!({ "x": "v" }), &root).is_ok());
    }

    #[test]
    fn validate_enum_rejects_out_of_set() {
        let root = tmp_root();
        let t = tool(vec![Param::opt("m", Kind::Enum(&["a", "b"]), "")]);
        assert!(t.validate(&json!({ "m": "c" }), &root).is_err());
        assert!(t.validate(&json!({ "m": "a" }), &root).is_ok());
    }

    #[test]
    fn validate_str_rejects_empty() {
        let root = tmp_root();
        let t = tool(vec![Param::req("s", Kind::Str, "")]);
        assert!(t.validate(&json!({ "s": "" }), &root).is_err());
    }

    #[test]
    fn validate_numvec_wrong_length_rejected() {
        let root = tmp_root();
        let t = tool(vec![Param::opt("v", Kind::NumVec { parts: &["x", "y", "z"] }, "")]);
        assert!(t.validate(&json!({ "v": [1.0, 2.0] }), &root).is_err(), "too short must reject");
        assert!(t.validate(&json!({ "v": [1.0, 2.0, 3.0, 4.0] }), &root).is_err(), "too long must reject");
        assert_eq!(
            t.validate(&json!({ "v": [1.0, 2.0, 3.0] }), &root).unwrap()["v"],
            json!([1.0, 2.0, 3.0])
        );
    }

    #[test]
    fn parmap_is_values_only_rejects_nested_object() {
        // The one open-keyed param must never carry a code-shaped (nested) structure.
        let root = tmp_root();
        let t = tool(vec![Param::req("pars", Kind::ParMap, "")]);
        assert!(t.validate(&json!({ "pars": { "ok": 1, "s": "v", "b": true, "arr": [1, 2] } }), &root).is_ok());
        assert!(t.validate(&json!({ "pars": { "bad": { "nested": 1 } } }), &root).is_err(), "nested object must reject");
        assert!(t.validate(&json!({ "pars": { "bad": null } }), &root).is_err(), "null value must reject");
    }

    // ────────────────────────────── GlslSnippet (validated code lane) cheap pre-check ────────────

    #[test]
    fn glsl_snippet_accepts_source_rejects_empty_oversized_nonascii() {
        let root = tmp_root();
        let t = tool(vec![Param::req(
            "source",
            Kind::GlslSnippet { stage: "pixel", profile: "glsl_v1" },
            "",
        )]);
        // A normal multi-line ASCII shader (newlines/tabs are legal) validates clean.
        let src = "#version 330\nout vec4 fragColor;\nvoid main(){\n\tfragColor = vec4(1.0);\n}";
        let out = t.validate(&json!({ "source": src }), &root).unwrap();
        assert_eq!(out["source"], json!(src), "clean source must pass through verbatim");
        // Empty is rejected.
        assert!(t.validate(&json!({ "source": "" }), &root).is_err(), "empty source must reject");
        // Over the 16 KB ceiling is rejected.
        let big = "a".repeat(16 * 1024 + 1);
        assert!(t.validate(&json!({ "source": big }), &root).is_err(), "oversized source must reject");
        // Exactly at the ceiling is accepted.
        let at_cap = "a".repeat(16 * 1024);
        assert!(t.validate(&json!({ "source": at_cap }), &root).is_ok(), "16 KB source must pass");
        // Non-ASCII is rejected (homoglyph/unicode hygiene).
        assert!(
            t.validate(&json!({ "source": "void main(){/* café */}" }), &root).is_err(),
            "non-ASCII source must reject"
        );
        // A non-string value is rejected.
        assert!(t.validate(&json!({ "source": 42 }), &root).is_err(), "non-string source must reject");
    }

    #[test]
    fn glsl_snippet_schema_is_string_naming_stage_and_profile() {
        let p = Param::req(
            "source",
            Kind::GlslSnippet { stage: "pixel", profile: "glsl_v1" },
            "GLSL fragment source.",
        );
        let s = p.schema();
        assert_eq!(s["type"], json!("string"), "GlslSnippet schema type must be string");
        let desc = s["description"].as_str().unwrap();
        assert!(desc.contains("pixel"), "schema description must name the stage");
        assert!(desc.contains("glsl_v1"), "schema description must name the profile");
    }

    // ────────────────────────────── ExprSnippet (validated expr lane) cheap pre-check ────────────

    #[test]
    fn expr_snippet_accepts_source_rejects_empty_oversized_multiline_nonascii() {
        let root = tmp_root();
        let t = tool(vec![Param::req(
            "source",
            Kind::ExprSnippet { profile: "expr_v1" },
            "",
        )]);
        // A normal single-line ASCII expression validates clean and passes through verbatim.
        let src = "math.sin(absTime.seconds) * 0.5 + 0.5";
        let out = t.validate(&json!({ "source": src }), &root).unwrap();
        assert_eq!(out["source"], json!(src), "clean expression must pass through verbatim");
        // Empty is rejected.
        assert!(t.validate(&json!({ "source": "" }), &root).is_err(), "empty expression must reject");
        // Over the 512-byte ceiling is rejected; exactly at the ceiling is accepted.
        let big = "a".repeat(513);
        assert!(t.validate(&json!({ "source": big }), &root).is_err(), "oversized expression must reject");
        let at_cap = "1".repeat(512);
        assert!(t.validate(&json!({ "source": at_cap }), &root).is_ok(), "512-byte expression must pass");
        // Multi-line is rejected (a parameter expression is a single logical expression).
        assert!(
            t.validate(&json!({ "source": "1 +\n2" }), &root).is_err(),
            "multi-line expression must reject"
        );
        // Non-ASCII is rejected (homoglyph/NFKC identifier hygiene).
        assert!(
            t.validate(&json!({ "source": "me.digits + café" }), &root).is_err(),
            "non-ASCII expression must reject"
        );
        // A non-string value is rejected.
        assert!(t.validate(&json!({ "source": 42 }), &root).is_err(), "non-string expression must reject");
    }

    #[test]
    fn expr_snippet_schema_is_string_naming_profile() {
        let p = Param::req(
            "source",
            Kind::ExprSnippet { profile: "expr_v1" },
            "Parameter expression.",
        );
        let s = p.schema();
        assert_eq!(s["type"], json!("string"), "ExprSnippet schema type must be string");
        assert_eq!(s["maxLength"], json!(512), "ExprSnippet schema must cap length at 512");
        let desc = s["description"].as_str().unwrap();
        assert!(desc.contains("expr_v1"), "schema description must name the profile");
    }

    // ────────────────────────────── reserved operator-placement args (lowering contract) ─────────

    fn op_tool(params: Vec<Param>) -> ToolDef {
        ToolDef { name: "blurTOP", category: "TOP", description: "", optype: Some("blurTOP"), params }
    }

    #[test]
    fn operator_tool_schema_injects_reserved_params() {
        let t = op_tool(vec![Param::opt("size", Kind::Num { min: 0.0, max: 1e6 }, "")]);
        let schema = t.input_schema();
        let props = schema["properties"].as_object().unwrap();
        for r in RESERVED_OP_PARAMS {
            assert!(props.contains_key(r), "operator schema must inject reserved '{r}'");
        }
        // reserved params are NEVER required
        let required = schema["required"].as_array().unwrap();
        for r in RESERVED_OP_PARAMS {
            assert!(!required.iter().any(|v| v == r), "reserved '{r}' must not be required");
        }
    }

    #[test]
    fn utility_tool_rejects_reserved_param() {
        // A utility tool (optype=None) does NOT accept the reserved placement params.
        let root = tmp_root();
        let t = tool(vec![]);
        assert!(t.validate(&json!({ "op_name": "x" }), &root).is_err(),
            "utility tool must reject op_name as an unknown param");
    }

    #[test]
    fn operator_tool_validate_accepts_and_normalizes_reserved() {
        let root = tmp_root();
        let t = op_tool(vec![Param::opt("size", Kind::Num { min: 0.0, max: 1e6 }, "")]);
        let out = t
            .validate(
                &json!({ "size": 3.0, "op_name": "blur1", "parent_path": "/project1", "pos_x": 10.0, "pos_y": -5.0 }),
                &root,
            )
            .unwrap();
        assert_eq!(out["op_name"], json!("blur1"));
        assert_eq!(out["parent_path"], json!("/project1"));
        assert_eq!(out["pos_x"], json!(10.0));
        assert_eq!(out["pos_y"], json!(-5.0));
        assert_eq!(out["size"], json!(3.0));
        // omitting them is fine too
        assert!(t.validate(&json!({ "size": 1.0 }), &root).is_ok());
    }

    #[test]
    fn operator_tool_rejects_bad_reserved_values() {
        let root = tmp_root();
        let t = op_tool(vec![]);
        assert!(t.validate(&json!({ "op_name": "" }), &root).is_err(), "empty op_name must reject");
        assert!(t.validate(&json!({ "parent_path": "" }), &root).is_err(), "empty parent_path must reject");
        assert!(t.validate(&json!({ "pos_x": "nope" }), &root).is_err(), "non-number pos_x must reject");
    }

    // ────────────────────────────── OpList structural gate (the batch envelope's `ops`) ──────────
    // The `batch` utility tool declares an OpList param `ops`; the OpList Kind is its typed gate. These
    // tests lock in the no-nesting / bounded-size / well-formed guarantees. (The gateway-side "same
    // gate, no bypass" wiring is `run_batch`→`call_one`, tested in `gateway::catalog_tests`.)

    #[test]
    fn oplist_accepts_wellformed_ops() {
        let root = tmp_root();
        let p = Param::req("ops", Kind::OpList, "");
        let good = json!([{ "name": "scene_info" }, { "name": "blurTOP", "arguments": { "size": 3 } }]);
        assert_eq!(validate_value(&p, &good, &root).unwrap(), good);
    }

    #[test]
    fn oplist_rejects_maxitems_overflow() {
        let root = tmp_root();
        let p = Param::req("ops", Kind::OpList, "");
        let too_many: Vec<Value> = (0..65).map(|_| json!({ "name": "scene_info" })).collect();
        assert!(validate_value(&p, &json!(too_many), &root).is_err(), "65 ops must reject (max 64)");
        let ok64: Vec<Value> = (0..64).map(|_| json!({ "name": "scene_info" })).collect();
        assert!(validate_value(&p, &json!(ok64), &root).is_ok(), "exactly 64 ops must be accepted");
    }

    #[test]
    fn oplist_rejects_empty() {
        let root = tmp_root();
        let p = Param::req("ops", Kind::OpList, "");
        assert!(validate_value(&p, &json!([]), &root).is_err(), "empty ops must reject (min 1)");
    }

    #[test]
    fn oplist_rejects_nested_batch() {
        let root = tmp_root();
        let p = Param::req("ops", Kind::OpList, "");
        let nested = json!([{ "name": "scene_info" }, { "name": "batch", "arguments": {} }]);
        assert!(validate_value(&p, &nested, &root).is_err(), "an op named 'batch' must reject (no nesting)");
    }

    #[test]
    fn oplist_rejects_malformed_ops() {
        let root = tmp_root();
        let p = Param::req("ops", Kind::OpList, "");
        assert!(validate_value(&p, &json!([{ "name": 123 }]), &root).is_err(), "non-string name");
        assert!(validate_value(&p, &json!([{ "name": "" }]), &root).is_err(), "empty name");
        assert!(validate_value(&p, &json!([{ "arguments": {} }]), &root).is_err(), "missing name");
        assert!(validate_value(&p, &json!([{ "name": "x", "extra": 1 }]), &root).is_err(), "unexpected key");
        assert!(validate_value(&p, &json!([{ "name": "x", "arguments": 5 }]), &root).is_err(), "non-object args");
    }
}
