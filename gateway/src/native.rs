//! Gateway-native tools: computed INSIDE the gateway, offline, never forwarded to the in-TouchDesigner
//! executor. These are the orientation + local-help lane, the parity port of the Houdini bridge's
//! `native.rs` `capabilities` / reference tools (Houdini routes a fixed `GATEWAY_NATIVE` set here instead
//! of `exec.call`; TD does the same — see `gateway.rs::GATEWAY_NATIVE`).
//!
//! DATA-ONLY: both tools only READ. `capabilities` introspects the SHIPPED typed catalog
//! (`crate::tools::mvp_catalog`) — the authoritative embedded surface, so its counts can never drift
//! from what the server actually exposes and it naturally yields the "utility" bucket that
//! `reference/catalog.json` (operators only) cannot. `help` returns only structured FACTS for one
//! operator (family, input count, parameter names) read from the shippable `reference/catalog.json`,
//! plus a deep link to Derivative's official documentation page — it never bundles or serves Derivative's
//! documentation prose. The catalog read is CONFINED to the working directory and the optype
//! is STRICTLY sanitized (alphanumeric/underscore only) so no caller string can traverse out or reach a
//! URL/path. There is no executor call and no code path here.
//!
//! Args reaching here are already schema-validated by `ToolDef::validate` (both are UTILITY tools in
//! `tools.rs`, `optype = None`).

use anyhow::{anyhow, Result};
use serde_json::{json, Map, Value};
use std::path::Path;

/// Dispatch a gateway-native tool by name. Mirrors how Houdini's `gateway.rs` routes its
/// `GATEWAY_NATIVE` set to `native.rs`. `wd` (the resolved confinement root) is passed through for
/// signature stability but is no longer used by any native tool: the reference reads (`help`,
/// `recipe_reference`) resolve their BUNDLED data from the code-relative `config::reference_base()`, not
/// the working dir, and `capabilities` reads the in-memory catalog. So reference lookups work no matter
/// where the user points the working dir.
pub fn dispatch(name: &str, args: &Value, wd: &Path) -> Result<Value> {
    match name {
        "td_capabilities" => capabilities(args, wd),
        "help" => help(args, wd),
        "recipe_reference" => recipe_reference(args, wd),
        "glsl_reference" => glsl_reference(args, wd),
        "expr_reference" => expr_reference(args, wd),
        "code_reference" => code_reference(args, wd),
        other => Err(anyhow!("no gateway-native tool named '{other}'")),
    }
}

/// Orientation / discoverability entry point — a fresh agent should call this FIRST. Returns a summary
/// of the whole surface (tool count + counts by TD family, incl. a "utility" bucket), the data-only
/// boundary, how to route a question to the right lookup tool, and the reference tools — computed from
/// the SHIPPED catalog. READ-ONLY, OFFLINE, in-gateway: no executor call, no TouchDesigner session, no
/// network, no filesystem access.
pub fn capabilities(_args: &Value, _wd: &Path) -> Result<Value> {
    // Introspect the SHIPPED typed surface — the authoritative source of truth (operator tools carry
    // their TD family in `category`; utility tools have `optype = None`). Counts can never drift from
    // what the server actually exposes, and the "utility" bucket falls out for free.
    let catalog = crate::tools::mvp_catalog();
    let tool_count = catalog.len();

    let mut cats: Vec<(String, usize)> = Vec::new();
    let mut utility = 0usize;
    for t in &catalog {
        match t.optype {
            Some(_) => {
                let fam = t.category;
                match cats.iter_mut().find(|(name, _)| name == fam) {
                    Some(entry) => entry.1 += 1,
                    None => cats.push((fam.to_string(), 1)),
                }
            }
            None => utility += 1,
        }
    }
    // Deterministic operator-family order (matches the ROADMAP inventory), then the utility bucket.
    cats.sort_by_key(|(name, _)| family_rank(name));
    cats.push(("utility".to_string(), utility));
    let categories: Map<String, Value> = cats.into_iter().map(|(k, v)| (k, json!(v))).collect();

    Ok(json!({
        "server": "touchdesigner-bridge-mcp",
        "summary": format!(
            "{tool_count} typed, validated, data-only tools for driving TouchDesigner to build \
             projection-mapping content. Every capability is a fixed, schema-checked operation: one \
             create-and-configure tool per operator (TOP/CHOP/SOP/COMP/MAT/DAT/POP) plus utility/ \
             introspection tools. There is NO arbitrary-code, generic-node, or param-code tool — you \
             build operator networks, the USER fires the cooks/renders. Consult the references below to \
             discover exactly which operators and parameters exist before you build."
        ),
        "tool_count": tool_count,
        "categories": categories,
        "boundary": "Data-only by construction: a FIXED typed registry of create-and-configure + utility \
             tools. There is no arbitrary-code / generic-node-parameter / raw-script tool, so the boundary \
             cannot be talked past. The 18 audited code-eval parameter sinks (a settable string VALUE that \
             TouchDesigner itself evaluates as Python/Tscript/GLSL — e.g. expressionCHOP.expr0expr, \
             groupSOP.filter, replicatorCOMP.tscript, phongMAT.multitexexpr) are DENIED at both the \
             generated surface and the executor. set_par writes literal VALUES only, never expressions. \
             Renders and exports are WIRE-ONLY: this bridge builds the network (e.g. a moviefileoutTOP for \
             a 4K sequence); you run the cook/export.",
        "how_to_discover": "Look up an operator's typed parameters -> operator_reference (optype=<name>, \
             or search=<substring>). SEE an operator's node viewer (a CHOP graph, a SOP/geometryCOMP 3D \
             view, a MAT preview) -> capture_ui. Read a network's structure + wiring -> read_network. \
             Diagnose why something is broken / not showing -> find_errors. Cheap numeric TOP state \
             (resolution, GPU memory, cook stats) -> top_info; SEE a TOP's pixels -> save_top. Check the \
             resource budget / pre-check a heavy op's magnitude -> mem. ANIMATE a parameter data-only \
             (bind it to a CHOP channel via code-free export) -> bind_chop. Fuller \
             prose help for one operator -> help (optype=<name> or search=<substring>). Task-level \
             façade-content workflows (how to actually DO X) -> recipe_reference (classify=<what you \
             have / want to do> for the routing table, then recipe=<id> for the ordered steps). Write a GPU \
             SHADER (generative field / custom FX / warp / feedback) -> glsl_reference; a parameter \
             EXPRESSION that computes itself -> expr_reference; where code fits in TD + the human-gated \
             consent handshake -> code_reference. This MCP is also a LEARNING tool: surface a code \
             opportunity, teach what it does, then hand it off to paste or run it via the consented \
             validated lane (set_glsl / set_expr, default OFF).",
        "verbs": {
            "note": "The exact tool NAMES for building and driving (call these directly). Each operator has \
                 its own create-and-configure tool named for its type (e.g. blurTOP, noiseTOP, mergeCHOP); \
                 the names below are the shared verbs that act on what you build. You do NOT need to load an \
                 operator's own tool (via tool search) to build with it: call operator_reference(optype=X) \
                 for its params (add compact=true for a slim view), then invoke X by NAME inside batch \
                 (ops:[{name:'X', arguments:{...}}]). To FIND an operator by name use \
                 operator_reference(search=<substring>) -- it returns matching optypes directly (the \
                 reliable operator finder).",
            "build": ["connect", "set_par", "set_par_many", "set_flags", "set_pos", "delete_op", "pulse",
                      "bind_chop", "write_csv", "import_segmented_model"],
            "see": ["show", "save_top", "capture_ui", "top_info", "read_network", "find_errors", "inspect", "mem"],
            "batch": "batch runs several of these in one call: {ops:[{name, arguments}]}."
        },
        "references": {
            "operator_reference": "The full typed parameter schema for any operator type from the catalog \
                 (509 operators / 17,370 parameters): every parameter's name, kind, range/tokens, and \
                 default. optype=<name> for one operator (add compact=true for a slimmer view); \
                 search=<substring> is the cheap operator FINDER -- returns matching optype names, so use \
                 THIS (not tool search) to locate an operator by name; no args for a family summary.",
            "capture_ui": "SEE any operator's own node viewer as an image, rendered from TouchDesigner's \
                 OWN GPU buffer (a temporary OP Viewer TOP) and returned inline — NEVER a screen grab. The \
                 'watch the node' tool for non-TOP operators (CHOP graphs, SOP/geometryCOMP 3D views, MAT \
                 previews).",
            "read_network": "Read a network's STRUCTURE as text — each child operator's name, type, input \
                 wiring, position, and child count; pars=true also dumps each node's non-default \
                 parameters. The token-cheap map for rebuilding your picture of a graph. READ-ONLY.",
            "find_errors": "READ-ONLY diagnostic: scan a network subtree for operators reporting errors \
                 (and warnings) and return their paths + messages, so you can self-correct.",
            "glsl_reference": "READ-ONLY GLSL teaching guide for the glslTOP surface — the TD shader \
                 environment, core functions, projection-mapping patterns, gotchas, and the GLSL handoff. \
                 topic=<key> for a section, search=<substring> to find lines. Proposes shader TEXT for the \
                 validated set_glsl lane (allow_glsl) or paste-by-hand; never runs code.",
            "expr_reference": "READ-ONLY guide to TD's Python parameter-EXPRESSION surface — the exact \
                 allowlist the set_expr validator accepts (me/op/parent/math/tdu/absTime), common \
                 expressions, the DAT/callback paste-handoff, and gotchas. topic=<key> / search=<substring>. \
                 Proposes expression TEXT for the validated set_expr lane (allow_expr) or type-by-hand.",
            "code_reference": "READ-ONLY orientation for TD's whole code surface — which lane carries what \
                 (GLSL shaders, parameter expressions, DAT/callback Python), the shared human-gated consent \
                 handshake, and the *_opportunity recipe cues. Routes to glsl_reference / expr_reference. \
                 The learning front door for code.",
            "help": "This tool: structured FACTS for one operator (family, input count, parameter \
                 names via optype=<name>) plus a deep link to the official Derivative documentation page, \
                 or a list of matching operator types (search=<substring>). Facts + official link to \
                 complement operator_reference's exact typed schema.",
            "recipe_reference": "Canonical, tool-mapped façade-content workflow recipes — how to \
                 actually DO X (3D render / generative texture-FX / point cloud / camera framing / \
                 output hand-off) with these tools, each an ordered sequence of real tools + the params \
                 that matter + per-step OUT_ landmark and verify. Call with classify=<what you have / \
                 want to do> for the routing table (start here), recipe=<id> for the full ordered steps, \
                 domain=<name> for a lane's recipes, or search=<substring>; no args for the index."
        },
        "governor": "Build to the DELIVERABLE's budget, not the tool's ceiling. ADVISORY-first, one \
             hard-refuse. magnitude: set_par (and every operator create-and-configure, which lowers to \
             set_par) attaches a {level: ok|caution|heavy, note} flag when the requested params are \
             sizeable for a realtime GPU — output resolution (the dominant cost; honors TD \
             non-commercial's 1280 output cap), instance/particle counts, and render passes. envelope: \
             call `mem` for the live resource band (system RAM total/avail/load + guidance), optionally \
             per-TOP gpu_memory_bytes (op=<TOP>) and a pre-build magnitude pre-check (optype+pars). These \
             GUIDE, they do not limit — on caution/heavy, down-scale to the target. HONEST VRAM LIMIT: \
             TouchDesigner's Python API exposes GPU memory only PER-TOP (top_info gpu_memory_bytes), NOT a \
             whole-card total/used/avail, so the envelope classifies on system RAM and the realtime-GPU \
             scale signal is carried by the magnitude advisory (unlike the Houdini bridge's whole-card \
             VRAM query). Only a catastrophic system-RAM band refuses; a telemetry-unknown never refuses."
    }))
}

/// Ordering key so the family buckets read in the conventional TD order.
fn family_rank(fam: &str) -> usize {
    match fam {
        "TOP" => 0,
        "CHOP" => 1,
        "SOP" => 2,
        "COMP" => 3,
        "MAT" => 4,
        "DAT" => 5,
        "POP" => 6,
        _ => 7,
    }
}

/// Local operator-help lookup, served OFFLINE. It returns only structured
/// FACTS about an operator — its optype, TD family, input count, and parameter-name list, read from the
/// shippable `reference/catalog.json` (facts, not authored prose) — plus a deep link to Derivative's
/// official documentation page for the readable article. It never bundles or serves Derivative's
/// documentation text — it serves only original facts plus the official docs link:
///   * `optype=<name>` -> that operator's facts (family, input count, parameter names) + the official
///     docs URL `https://docs.derivative.ca/<optype>`;
///   * `search=<substring>` -> the matching operator types (from the shipped surface);
///   * no args -> a short usage note.
///
/// SECURITY: the optype is STRICTLY sanitized (ASCII alphanumeric/underscore only — every real TD optype,
/// e.g. `blurTOP`, matches) so no `.`/`/`/`..` can appear or reach a path or URL. The catalog is a bundled
/// code-resource asset read from the code-relative `reference_base()`; there is no caller-supplied
/// filesystem path param and no working-dir dependence (`_wd` is unused, kept for the dispatch signature).
pub fn help(args: &Value, _wd: &Path) -> Result<Value> {
    // search=<substring> -> matching operator types (case-insensitive), from the shipped surface.
    if let Some(q) = args.get("search").and_then(Value::as_str) {
        let ql = q.to_lowercase();
        let mut matches: Vec<String> = operator_optypes()
            .into_iter()
            .filter(|ot| ot.to_lowercase().contains(&ql))
            .collect();
        matches.sort();
        matches.dedup();
        return Ok(json!({
            "search": q,
            "count": matches.len(),
            "matches": matches,
            "note": "call help optype=<name> for that operator's facts + official docs link, or operator_reference for its exact typed parameter schema."
        }));
    }

    // optype=<name> -> facts (family/inputs/param names) + the official Derivative docs deep link.
    if let Some(ot) = args.get("optype").and_then(Value::as_str) {
        if ot.is_empty() || !ot.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
            return Err(anyhow!(
                "invalid optype '{ot}': help serves operator facts only — pass an operator type \
                 name (ASCII alphanumeric/underscore, e.g. 'blurTOP')"
            ));
        }
        // The official docs deep link is always safe to construct (the optype is already sanitized to
        // alphanumeric/underscore). We construct it; we never fetch it.
        let docs_url = format!("https://docs.derivative.ca/{ot}");

        match operator_facts(ot) {
            Some((family, maxinputs, params)) => Ok(json!({
                "optype": ot,
                "found": true,
                "family": family,
                "max_inputs": maxinputs,
                "param_count": params.len(),
                "params": params,
                "docs_url": docs_url,
                "note": "Facts (family, inputs, parameter names) from the local catalog; the full prose \
                         documentation is the official Derivative page at docs_url. For the exact typed \
                         parameter schema (kinds, ranges, tokens, defaults) call operator_reference."
            })),
            None => {
                // Unknown optype -> graceful note + near matches + the docs link (never an error).
                let otl = ot.to_lowercase();
                let mut near: Vec<String> = operator_optypes()
                    .into_iter()
                    .filter(|o| o.to_lowercase().contains(&otl))
                    .collect();
                near.sort();
                near.dedup();
                Ok(json!({
                    "optype": ot,
                    "found": false,
                    "docs_url": docs_url,
                    "note": "no operator of that exact type in the local catalog; try help search=<substring>, operator_reference for the typed parameter schema, or the official docs at docs_url.",
                    "matches": near
                }))
            }
        }
    } else {
        // No args -> usage.
        Ok(json!({
            "usage": "help optype=<operatorType> returns that operator's facts (family, input count, parameter names) plus a deep link to the official Derivative documentation page (e.g. 'blurTOP'); help search=<substring> lists matching operator types. For the exact typed parameter schema use operator_reference; for the whole tool catalog + boundary use td_capabilities.",
            "example": "help optype=blurTOP"
        }))
    }
}

/// Read the structured FACTS for one operator from the shippable `reference/catalog.json`: its TD
/// family, declared input count, and the list of parameter names. Returns `None` when the optype is not
/// in the catalog or the catalog cannot be read/parsed. The catalog is a BUNDLED code-resource asset
/// read from the code-relative `reference_base()` — NOT the confinement working dir — so it resolves the
/// same regardless of where the user points the working dir. `optype` was already strictly sanitized by
/// the caller and the path is a fixed constant, so there is no user-controlled path component to confine.
fn operator_facts(optype: &str) -> Option<(String, String, Vec<String>)> {
    let path = crate::config::reference_base().join("reference").join("catalog.json");
    let text = std::fs::read_to_string(&path).ok()?;
    let data: Value = serde_json::from_str(&text).ok()?;
    let entry = data.get(optype)?;
    let family = entry
        .get("family")
        .and_then(Value::as_str)
        .unwrap_or("?")
        .to_string();
    // maxinputs is stored as a string fact in the catalog; carry it through as-is.
    let maxinputs = match entry.get("maxinputs") {
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
        None => "?".to_string(),
    };
    let params: Vec<String> = entry
        .get("params")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|p| p.get("name").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    Some((family, maxinputs, params))
}

/// The optypes of every shipped operator tool (the operators that have bundled help), from the catalog.
fn operator_optypes() -> Vec<String> {
    crate::tools::mvp_catalog()
        .iter()
        .filter_map(|t| t.optype.map(str::to_string))
        .collect()
}

// ---- recipe reference (offline; bundled tool-mapped façade-content workflow recipes) ----
// The parity port of the Houdini bridge's `recipe_reference` (native.rs) — the DRIVE LAYER that turns
// the typed tool SURFACE into DRIVABLE capability. Read-only + offline (a local JSON read of
// `reference/recipes.json`); no TouchDesigner session, no executor call, no network.

/// A one-line brief of a recipe for the index / search / domain listings.
fn recipe_brief(r: &Value) -> Value {
    json!({ "id": r.get("id"), "domain": r.get("domain"),
            "title": r.get("title"), "summary": r.get("summary") })
}

/// The lowercased search haystack for one recipe: id/title/summary/classify + the STEP TOOL NAMES +
/// tool_manifest, so `search` finds a recipe by the tool it uses (a driving agent searches "how do I
/// use feedbackTOP" and lands the recipe, never guessing). Also indexes the presence of any
/// `*_opportunity` gate on a step, so `search=output` (or `render`/`opportunity`/`wire-only`) surfaces
/// the recipes where the AI should proactively offer the gated (WIRE-ONLY) capability.
fn recipe_hay(r: &Value) -> String {
    let mut hay = format!("{} {} {} {}",
        r.get("id").and_then(Value::as_str).unwrap_or(""),
        r.get("title").and_then(Value::as_str).unwrap_or(""),
        r.get("summary").and_then(Value::as_str).unwrap_or(""),
        r.get("classify").and_then(Value::as_str).unwrap_or(""),
    );
    if let Some(steps) = r.get("steps").and_then(Value::as_array) {
        for s in steps {
            if let Some(t) = s.get("tool").and_then(Value::as_str) { hay.push(' '); hay.push_str(t); }
        }
    }
    if let Some(man) = r.get("tool_manifest").and_then(Value::as_array) {
        for t in man { if let Some(t) = t.as_str() { hay.push(' '); hay.push_str(t); } }
    }
    if let Some(steps) = r.get("steps").and_then(Value::as_array) {
        for s in steps {
            if let Some(obj) = s.as_object() {
                for k in obj.keys() {
                    if let Some(gate) = k.strip_suffix("_opportunity") {
                        hay.push_str(" opportunity ");
                        hay.push_str(k);        // e.g. "output_opportunity"
                        hay.push(' ');
                        hay.push_str(gate);     // the bare gate word: output/render
                    }
                }
                // Keep a wire-only alias so `search=wire-only`/`wire` lands the WIRE-ONLY output steps.
                if obj.contains_key("output_opportunity") {
                    hay.push_str(" wire-only wire handoff output");
                }
            }
        }
    }
    hay.to_lowercase()
}

/// Query the bundled façade-content workflow-recipe reference (`reference/recipes.json`) — canonical,
/// tool-mapped "how to actually do X" recipes, each an ordered sequence of THIS server's real tools +
/// the params that matter + per-step landmark/verify/geometry_out. Read-only + offline (a local JSON
/// read); no TouchDesigner session. Modes: `classify`=<what you have / want to do> -> the ROUTING table
/// (input element -> lane -> entry_recipe; START HERE); `recipe`=<id> -> the full ordered steps;
/// `domain`=<lane name> -> that lane's recipes (brief); `search`=<substring> over id/title/summary +
/// step tools + tool_manifest; none -> the recipe index + routing table. Serves data; never actuates.
pub fn recipe_reference(args: &Value, _wd: &Path) -> Result<Value> {
    // Bundled code-resource asset: read from the code-relative reference_base(), NOT the confinement
    // working dir, so recipes resolve no matter where the user points the working dir. Read fresh each
    // call so recipes.json edits are picked up without a restart.
    let path = crate::config::reference_base().join("reference").join("recipes.json");
    let text = std::fs::read_to_string(&path)
        .map_err(|e| anyhow!("recipe reference not found at {}: {e}", path.display()))?;
    let data: Value = serde_json::from_str(&text)?;
    let recipes = data.get("recipes").and_then(Value::as_array).cloned().unwrap_or_default();

    // recipe=<id> -> the full recipe (exact id, case-insensitive; else closest matches).
    if let Some(id) = args.get("recipe").and_then(Value::as_str) {
        if let Some(r) = recipes.iter().find(|r| {
            r.get("id").and_then(Value::as_str).map(|s| s.eq_ignore_ascii_case(id)).unwrap_or(false)
        }) {
            return Ok(r.clone());
        }
        let idl = id.to_lowercase();
        let matches: Vec<Value> =
            recipes.iter().filter(|r| recipe_hay(r).contains(&idl)).map(recipe_brief).collect();
        return Ok(json!({ "recipe": id, "found": false,
                          "note": "no recipe with that id; closest matches:", "matches": matches }));
    }
    // domain=<x> -> that domain's recipes (brief).
    if let Some(dom) = args.get("domain").and_then(Value::as_str) {
        let list: Vec<Value> = recipes.iter().filter(|r| {
            r.get("domain").and_then(Value::as_str).map(|s| s.eq_ignore_ascii_case(dom)).unwrap_or(false)
        }).map(recipe_brief).collect();
        return Ok(json!({ "domain": dom, "count": list.len(), "recipes": list }));
    }
    // search=<substr> over id/title/summary + step tools + tool_manifest + classify.
    if let Some(q) = args.get("search").and_then(Value::as_str) {
        let ql = q.to_lowercase();
        let list: Vec<Value> =
            recipes.iter().filter(|r| recipe_hay(r).contains(&ql)).map(recipe_brief).collect();
        return Ok(json!({ "search": q, "count": list.len(), "matches": list }));
    }
    // classify=<element> -> the ROUTING table: which lane + entry_recipe fits an input element / task
    // ("a building", "a point cloud", "generative content", "output to the media server"). The router
    // is the front door: what do I have / want -> which recipe. `classify` with no value (empty string)
    // returns the whole routing table so an agent can see every lane.
    let routing = data.get("routing").and_then(Value::as_array).cloned().unwrap_or_default();
    if let Some(el) = args.get("classify").and_then(Value::as_str) {
        let ell = el.to_lowercase();
        // Match a route when the WHOLE phrase OR any significant word (>=3 chars) of the query appears in a
        // row field. Word-level matching is the fix for fair multi-word queries like "generative content"
        // that never appear verbatim in a row but whose keywords do (driver-seat: the front door returned 0).
        let tokens: Vec<&str> = ell.split_whitespace().filter(|t| t.len() >= 3).collect();
        let mut matches: Vec<Value> = if ell.is_empty() {
            routing.clone()
        } else {
            routing.iter().filter(|row| {
                ["input_element", "geometry_class", "lane", "entry_recipe", "notes", "axis"].iter().any(|k| {
                    row.get(*k).and_then(Value::as_str).map(|s| {
                        let sl = s.to_lowercase();
                        sl.contains(&ell) || tokens.iter().any(|t| sl.contains(t))
                    }).unwrap_or(false)
                })
            }).cloned().collect()
        };
        // NEVER dead-end the driver: if a non-empty query matched nothing, return the WHOLE routing table so
        // the agent can pick a lane instead of falling back to blind operator search (driver-seat P2).
        let fell_back = !ell.is_empty() && matches.is_empty();
        if fell_back {
            matches = routing.clone();
        }
        let note = if fell_back {
            "no lane matched that phrase — showing ALL lanes so you can choose; call recipe=<entry_recipe> for a lane's ordered steps."
        } else {
            "each route names an entry_recipe — call recipe=<entry_recipe> for its ordered, verifiable steps. axis=content-source rows are reached by classifying the input you were handed (a model, a scan, a brief); axis=task-intent rows (camera framing, output hand-off) are invoked as a task on content you already have."
        };
        return Ok(json!({ "classify": el, "count": matches.len(), "routes": matches,
            "routing_note": data.get("routing_note"),
            "fell_back_to_all": fell_back,
            "note": note }));
    }
    // No args -> the recipe index (+ the routing table front door).
    Ok(json!({
        "note": data.get("note"),
        "routing_note": data.get("routing_note"),
        "domains": data.get("domains"),
        "routing": routing,
        "count": recipes.len(),
        "recipes": recipes.iter().map(recipe_brief).collect::<Vec<_>>(),
        "usage": "classify=<what you have / want to do> -> routing table (start here); recipe=<id> -> full ordered steps; domain=<name> -> a lane's recipes; search=<substring> (indexes step tools too). Every recipe plants OUT_ null taps + a per-step verify; output steps are WIRE-ONLY (the operator fires record/stream)."
    }))
}

// ---- code-teaching references (glsl_reference / expr_reference / code_reference) --------------------
// READ-ONLY teaching tools — the parity port of the Houdini bridge's `vex_reference`. Each serves a
// curated markdown guide (reference/*.md) by `topic=<key>`, or `search=<substring>` over its lines. They
// never run code: they propose code TEXT for a validated lane (set_glsl/set_expr, consent-gated) or for
// the user to paste by hand. This MCP is also a learning tool; the guides teach the workflow + the
// consent handshake. Topic keys map to the `## <heading>` sections of each file.
pub(crate) const GLSL_TOPICS: &[(&str, &str)] = &[
    ("why", "Why this doc exists"),
    ("handoff", "The GLSL handoff"),
    ("surface", "Surfacing a GLSL shader — when to offer, and the consent handshake"),
    ("environment", "The TouchDesigner GLSL TOP environment"),
    ("functions", "Core functions"),
    ("patterns", "Patterns"),
    ("gotchas", "Gotchas"),
    ("index", "Quick index"),
];
pub(crate) const EXPR_TOPICS: &[(&str, &str)] = &[
    ("why", "Why this doc exists"),
    ("handoff", "The expression handoff"),
    ("surface", "Surfacing an expression — when to offer, and the consent handshake"),
    ("allowed", "The allowed expression surface"),
    ("common", "Common expressions"),
    ("beyond", "Beyond the lane — DAT / script Python (paste-handoff only)"),
    ("gotchas", "Gotchas"),
    ("index", "Quick index"),
];
pub(crate) const CODE_TOPICS: &[(&str, &str)] = &[
    ("surfaces", "TouchDesigner's code surface — three kinds, three postures"),
    ("which", "Which lane for what"),
    ("handshake", "The consent handshake (never skip it)"),
    ("opportunity", "`*_opportunity` cues in recipes"),
    ("siblings", "Sibling references"),
];

/// Return the body of a `## <heading>` section of a bundled markdown guide, up to the next `## `.
/// Reads from the code-relative `reference_base()` (not the working dir), fresh each call.
fn md_section(file: &str, heading: &str) -> Option<String> {
    let path = crate::config::reference_base().join("reference").join(file);
    let text = std::fs::read_to_string(path).ok()?;
    let target = format!("## {heading}");
    let mut lines = text.lines();
    lines.by_ref().find(|l| l.trim() == target)?;
    let mut body = Vec::new();
    for line in lines {
        if line.starts_with("## ") {
            break;
        }
        body.push(line);
    }
    while matches!(body.first(), Some(l) if l.trim().is_empty() || l.trim() == "---") {
        body.remove(0);
    }
    while matches!(body.last(), Some(l) if l.trim().is_empty() || l.trim() == "---") {
        body.pop();
    }
    Some(body.join("\n"))
}

/// Case-insensitive substring search over a guide's content lines (skips blanks / rules), capped.
fn md_search(file: &str, needle: &str, max: usize) -> Vec<String> {
    let path = crate::config::reference_base().join("reference").join(file);
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return Vec::new(),
    };
    let n = needle.to_lowercase();
    let mut out = Vec::new();
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() || t == "---" {
            continue;
        }
        if t.to_lowercase().contains(&n) {
            out.push(t.to_string());
            if out.len() >= max {
                break;
            }
        }
    }
    out
}

/// Shared server for the three read-only code-teaching references: `topic=<key>` -> a guide section,
/// `search=<substring>` -> matching lines, no args -> the topic list + the reference-only boundary note.
fn reference_tool(args: &Value, file: &str, topics: &[(&str, &str)], name: &str, boundary: &str) -> Result<Value> {
    if let Some(topic) = args.get("topic").and_then(Value::as_str) {
        let key = topic.to_lowercase();
        if let Some((_, heading)) = topics.iter().find(|(k, _)| *k == key) {
            return match md_section(file, heading) {
                Some(body) => Ok(json!({ "topic": topic, "heading": heading, "content": body })),
                None => Ok(json!({ "topic": topic, "note": format!("guide section '{heading}' not found") })),
            };
        }
        let keys: Vec<&str> = topics.iter().map(|(k, _)| *k).collect();
        return Ok(json!({ "topic": topic, "note": "unknown topic", "valid_topics": keys }));
    }
    if let Some(search) = args.get("search").and_then(Value::as_str) {
        let hits = md_search(file, search, 40);
        return Ok(json!({ "search": search, "count": hits.len(), "matches": hits }));
    }
    let keys: Vec<&str> = topics.iter().map(|(k, _)| *k).collect();
    Ok(json!({
        "tool": name,
        "boundary": boundary,
        "topics": keys,
        "usage": "pass topic=<key> for a guide section, or search=<substring> to find lines. REFERENCE ONLY."
    }))
}

/// GLSL shader teaching reference (the glslTOP surface). Read-only.
pub fn glsl_reference(args: &Value, _wd: &Path) -> Result<Value> {
    reference_tool(args, "GLSL_REFERENCE.md", GLSL_TOPICS, "glsl_reference",
        "REFERENCE ONLY — never runs code. Proposes GLSL shader text for the validated set_glsl lane (consent: allow_glsl, default OFF) or for the user to paste into a Text DAT wired to the glslTOP's pixeldat.")
}

/// Python parameter-expression teaching reference. Read-only.
pub fn expr_reference(args: &Value, _wd: &Path) -> Result<Value> {
    reference_tool(args, "EXPR_REFERENCE.md", EXPR_TOPICS, "expr_reference",
        "REFERENCE ONLY — never runs code. Proposes single-line parameter-expression text for the validated set_expr lane (consent: allow_expr, default OFF) or for the user to type by hand. Prefer bind_chop for CHOP-driven animation.")
}

/// Code-surface orientation + the shared consent handshake. Routes to glsl_reference / expr_reference. Read-only.
pub fn code_reference(args: &Value, _wd: &Path) -> Result<Value> {
    reference_tool(args, "CODE_REFERENCE.md", CODE_TOPICS, "code_reference",
        "REFERENCE ONLY — never runs code. Orients TD's code surface (GLSL, parameter expressions, DAT/callback Python) and the human-gated consent handshake; routes to glsl_reference / expr_reference.")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// `capabilities` runs OFFLINE, in-gateway (no executor), and returns an object carrying the full
    /// required key set — the parity port of Houdini's `capabilities`. Called directly here (no
    /// `Executor` in scope), which IS the proof it never touches the executor.
    #[test]
    fn capabilities_returns_required_keys_offline() {
        let v = capabilities(&json!({}), std::env::temp_dir().as_path()).unwrap();
        for k in [
            "server", "summary", "tool_count", "categories", "boundary", "how_to_discover",
            "references", "governor", "verbs",
        ] {
            assert!(v.get(k).is_some(), "capabilities result must carry key '{k}'");
        }
        assert_eq!(v["server"], json!("touchdesigner-bridge-mcp"));
        assert!(
            v["tool_count"].as_u64().unwrap() >= 508,
            "tool_count should reflect the full surface (508+), got {}",
            v["tool_count"]
        );

        // Categories: every TD operator family + the utility bucket, counts > 0.
        let cats = v["categories"].as_object().expect("categories must be an object");
        for fam in ["TOP", "CHOP", "SOP", "COMP", "MAT", "DAT", "POP", "utility"] {
            let n = cats.get(fam).and_then(Value::as_u64)
                .unwrap_or_else(|| panic!("categories must include '{fam}'"));
            assert!(n > 0, "family '{fam}' count must be > 0");
        }

        // References: the routing tools the how_to_discover text names.
        let refs = v["references"].as_object().expect("references must be an object");
        for r in ["operator_reference", "capture_ui", "read_network", "find_errors", "help"] {
            assert!(refs.contains_key(r), "references must name '{r}'");
        }
        // recipe_reference is a shipped reference tool (the W5 drive layer).
        assert!(refs.contains_key("recipe_reference"), "references must name recipe_reference");
    }

    /// The repo root (crate manifest's parent) — where `reference/recipes.json` is bundled.
    fn repo_root() -> std::path::PathBuf {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
    }

    /// `recipe_reference` runs OFFLINE, in-gateway (no executor), and with no args returns the index +
    /// routing-table front door. Called directly here (no `Executor` in scope), which IS the proof it
    /// never touches the executor.
    #[test]
    fn recipe_reference_index_offline() {
        let v = recipe_reference(&json!({}), &repo_root()).unwrap();
        assert!(v.get("routing").and_then(Value::as_array).map(|a| !a.is_empty()).unwrap_or(false),
            "no-args recipe_reference must return the routing table");
        let recipes = v["recipes"].as_array().expect("index must list recipes");
        assert!(recipes.len() >= 5, "expected at least the 5 façade lanes, got {}", recipes.len());
        assert!(v.get("domains").is_some(), "index must carry the domains list");
    }

    /// The three code-teaching references dispatch OFFLINE (no executor), list their topics with no args,
    /// and return real guide content for a topic — proof they read their bundled reference/*.md.
    #[test]
    fn code_references_serve_topics_offline() {
        for (name, topic) in [("glsl_reference", "handoff"), ("expr_reference", "handoff"), ("code_reference", "which")] {
            let idx = dispatch(name, &json!({}), &repo_root()).unwrap();
            assert!(idx.get("topics").and_then(Value::as_array).map(|a| !a.is_empty()).unwrap_or(false),
                "{name} (no args) must list topics");
            assert!(idx.get("boundary").is_some(), "{name} must state the reference-only boundary");
            let sec = dispatch(name, &json!({ "topic": topic }), &repo_root()).unwrap();
            let body = sec.get("content").and_then(Value::as_str).unwrap_or("");
            assert!(body.len() > 40, "{name} topic={topic} must return real guide content, got {} chars", body.len());
        }
    }

    /// Drift guard: each tool's catalog `topic` enum EXACTLY matches its TOPICS const, and every mapped
    /// heading actually resolves in the bundled .md — so a renamed heading can never silently break a topic.
    #[test]
    fn code_reference_topics_match_catalog_and_files() {
        let cat = crate::tools::mvp_catalog();
        for (name, topics, file) in [
            ("glsl_reference", GLSL_TOPICS, "GLSL_REFERENCE.md"),
            ("expr_reference", EXPR_TOPICS, "EXPR_REFERENCE.md"),
            ("code_reference", CODE_TOPICS, "CODE_REFERENCE.md"),
        ] {
            let t = cat.iter().find(|d| d.name == name).unwrap_or_else(|| panic!("{name} must be in the catalog"));
            let tp = t.params.iter().find(|p| p.name == "topic").unwrap_or_else(|| panic!("{name} needs a topic param"));
            let choices = match &tp.kind {
                crate::tool_schema::Kind::Enum(c) => *c,
                _ => panic!("{name} topic must be an Enum"),
            };
            let mut a: Vec<&str> = choices.to_vec();
            a.sort();
            let mut b: Vec<&str> = topics.iter().map(|(k, _)| *k).collect();
            b.sort();
            assert_eq!(a, b, "{name} catalog topic enum must match its TOPICS const");
            for (key, heading) in topics {
                assert!(md_section(file, heading).is_some(),
                    "{name}: topic '{key}' heading '{heading}' not found in reference/{file}");
            }
        }
    }

    /// `classify=<element>` returns matching routing rows, each naming a real `entry_recipe`.
    #[test]
    fn recipe_reference_classify_routes_to_entry_recipe() {
        let v = recipe_reference(&json!({ "classify": "building" }), &repo_root()).unwrap();
        let routes = v["routes"].as_array().expect("classify must return routes");
        assert!(!routes.is_empty(), "classify=building should match at least one route");
        assert!(routes.iter().any(|r| r.get("entry_recipe") == Some(&json!("facade_3d_render"))),
            "classify=building must route to facade_3d_render");
    }

    /// `classify` matches on WORD tokens, not just the whole phrase, so a fair multi-word query lands a
    /// lane instead of dead-ending (driver-seat: "generative content" used to return 0 routes), and an
    /// unmatched query falls back to ALL lanes rather than an empty front door.
    #[test]
    fn recipe_reference_classify_word_tokens_and_never_dead_ends() {
        let v = recipe_reference(&json!({ "classify": "generative content" }), &repo_root()).unwrap();
        let routes = v["routes"].as_array().expect("classify must return routes");
        assert!(!routes.is_empty(), "'generative content' must not return 0 routes");
        assert!(routes.iter().any(|r| r.get("entry_recipe") == Some(&json!("generative_texture_fx"))),
            "'generative content' must reach the generative_texture_fx lane");
        // A nonsense query still returns ALL lanes (never a dead end) + the fell_back flag.
        let v = recipe_reference(&json!({ "classify": "zzqx nonsense gibberish" }), &repo_root()).unwrap();
        assert_eq!(v["fell_back_to_all"], json!(true), "an unmatched query must fall back to all lanes");
        assert!(!v["routes"].as_array().unwrap().is_empty(), "fallback must list every lane, never empty");
    }

    /// `recipe=<id>` returns the full recipe with ordered steps.
    #[test]
    fn recipe_reference_recipe_returns_full_steps() {
        let v = recipe_reference(&json!({ "recipe": "facade_3d_render" }), &repo_root()).unwrap();
        assert_eq!(v["id"], json!("facade_3d_render"));
        let steps = v["steps"].as_array().expect("a recipe must carry ordered steps");
        assert!(!steps.is_empty(), "facade_3d_render must have steps");
        assert!(v.get("tool_manifest").is_some(), "a recipe must carry a tool_manifest");
    }

    /// `search=<substring>` indexes step tool names — searching a tool finds the recipe using it.
    #[test]
    fn recipe_reference_search_indexes_step_tools() {
        let v = recipe_reference(&json!({ "search": "feedbackTOP" }), &repo_root()).unwrap();
        let matches = v["matches"].as_array().expect("search must return matches");
        assert!(matches.iter().any(|m| m.get("id") == Some(&json!("generative_texture_fx"))),
            "search=feedbackTOP should surface generative_texture_fx (it uses feedbackTOP)");
    }

    /// `dispatch` routes `recipe_reference` to the native reader (parity with capabilities/help).
    #[test]
    fn dispatch_routes_recipe_reference() {
        let v = dispatch("recipe_reference", &json!({}), &repo_root()).unwrap();
        assert!(v.get("recipes").is_some(), "dispatch(recipe_reference) must return the index");
    }

    #[test]
    fn help_no_args_returns_usage() {
        let v = help(&json!({}), std::env::temp_dir().as_path()).unwrap();
        assert!(v.get("usage").is_some(), "help with no args must return a usage note");
    }

    /// The strict optype sanitizer refuses any path-traversal-shaped argument BEFORE any read.
    #[test]
    fn help_rejects_traversal_optype() {
        let wd = std::env::temp_dir();
        assert!(help(&json!({ "optype": "../secret" }), wd.as_path()).is_err());
        assert!(help(&json!({ "optype": "a/b" }), wd.as_path()).is_err());
        assert!(help(&json!({ "optype": "blur.TOP" }), wd.as_path()).is_err());
        assert!(help(&json!({ "optype": "" }), wd.as_path()).is_err());
    }

    #[test]
    fn help_search_lists_matching_optypes() {
        let v = help(&json!({ "search": "blur" }), std::env::temp_dir().as_path()).unwrap();
        let matches = v["matches"].as_array().expect("search must return a matches array");
        assert!(
            matches.iter().any(|m| m == "blurTOP"),
            "search=blur should include blurTOP"
        );
    }

    /// `help optype=blurTOP` returns FACTS from the confined catalog (family, inputs, param names) plus
    /// the official Derivative docs deep link — NO bundled documentation prose (repo root = crate manifest's parent).
    #[test]
    fn help_returns_facts_and_docs_link() {
        let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf();
        let v = help(&json!({ "optype": "blurTOP" }), &repo).unwrap();
        assert_eq!(v["found"], json!(true), "blurTOP facts must be found in the catalog under the repo root");
        assert_eq!(v["family"], json!("TOP"), "blurTOP is a TOP");
        assert_eq!(
            v["docs_url"], json!("https://docs.derivative.ca/blurTOP"),
            "help must construct the official Derivative deep link"
        );
        let params = v["params"].as_array().expect("facts must include a param-name list");
        assert!(!params.is_empty(), "blurTOP must expose parameter names as facts");
        // Licensing guarantee: no bundled documentation prose is served.
        assert!(v.get("help").is_none(), "help must NOT serve any bundled prose 'help' field");
    }
}
