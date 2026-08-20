"""td_executor/handlers/control.py -- the control surface (ported from the Houdini bridge's
control_surface). Data-only: create/connect/parameterize/inspect/delete operators by typed inputs.
No arbitrary code. This is the minimal set that lets the agent BUILD and READ networks over the wire.
"""
from td_executor import server
from td_executor import governor


def _resolve(path):
    return server.resolve_op(path)


def _read_allow_highres():
    """High-res override: read `allow_highres` from ~/.touchdesigner-bridge-mcp/arm.json (default False).
    True bypasses the enforced magnitude ceiling -- a human-gated consent, exactly like allow_expr/
    allow_glsl. Read fresh (only when a magnitude param is actually being set) so a GUI/manual flip takes
    effect without a re-arm."""
    import json as _json
    import os as _os
    try:
        with open(_os.path.join(server._CONFIG_DIR, "arm.json"), "r", encoding="utf-8") as fh:
            return bool(_json.load(fh).get("allow_highres", False))
    except Exception:
        return False


def _clamp_par_value(par, value):
    """BOUNDARY re-clamp for every parameter WRITE (set_par / set_par_many / internal _set_lit): the
    executor enforces numeric bounds ITSELF instead of trusting the gateway's typed-tool clamp. The generic
    set_par lowers a ParMap the gateway only checks for FINITENESS, not range -- and TouchDesigner does NOT
    clamp a programmatic `p.val =` write even when the parameter declares a hard clamp (a UI-slider soft
    bound only). So without this the boundary would store out-of-range values the typed operator tools
    reject. Mirrors the build-time generator's `num_bounds`: clamp to the parameter's HARD
    range ONLY where TD declares a clamp (Par.clampMin / Par.clampMax -- the same hard[2]/hard[3] signal the
    shipped tools use), so un-clamped params (e.g. resolution) pass through exactly as the tools' generous
    bounds allow -- no legitimate value is newly rejected. Non-finite (NaN / +-inf) numerics are REFUSED
    (parity with the gateway ParMap finiteness gate, enforced at the boundary too). Non-numeric values
    (strings, menu tokens, booleans) pass through untouched. Lives in this hot-reloadable handler module so
    `dev_reload` applies it live (server.py is not reloaded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if value != value or value in (float("inf"), float("-inf")):   # NaN or +-inf
        raise ValueError("non-finite numeric value refused")

    def _attr(name, default):
        # TD Par attributes may be exposed as callables in some contexts; call-if-callable, exactly like
        # the offline parameter probe's own reader, so clampMin/clampMax/min/max resolve.
        try:
            a = getattr(par, name)
            return a() if callable(a) else a
        except Exception:
            return default

    v = value
    try:
        if _attr("clampMin", False):
            v = max(float(_attr("min", v)), v)
        if _attr("clampMax", False):
            v = min(float(_attr("max", v)), v)
    except Exception:
        return value   # a param without usable min/max bounds -> leave the (finite) value as-is
    return v


# TD Par.style values that denote a FILESYSTEM PATH (the values the typed tools mark FsPath).
_FILE_PATH_STYLES = frozenset({"File", "Folder", "Files", "Filesave"})


def _guard_par_value(par, value):
    """THE single boundary guard every parameter WRITE must pass (set_par / set_par_many / _set_lit):
      (1) NUMERIC bound + non-finite refusal (via _clamp_par_value), and
      (2) FILE-PATH confinement -- a value set on a filesystem-path parameter must resolve UNDER the
          working dir, exactly like the typed tools' FsPath confinement. The generic set_par
          ParMap validates values-only with NO path check, so `set_par {file:'C:/Users/.../id_rsa'}` +
          inspect could exfiltrate any file and `moviefileoutTOP.file` + addframe could write anywhere.
          confined_path() realpath-confines to WORKING_DIR (and blocks the config dir), raising on escape;
          the per-par try/except reports it as a failed par (non-fatal). Empty values (clearing a path)
          pass through. This closes the read/write-outside-working-dir hole at the executor boundary."""
    v = _clamp_par_value(par, value)   # numeric clamp + non-finite refusal; non-numerics pass through
    # ENFORCED magnitude ceiling. Cheap pure name check first; only read the allow_highres consent
    # when a genuine catastrophic-magnitude param (resolution/instances/passes) is being set, so this adds
    # no per-write cost to ordinary params. Refuses a driver-killing value unless the human set allow_highres.
    try:
        pname = getattr(par, "name")
        pname = pname() if callable(pname) else pname
    except Exception:
        pname = None
    if pname is not None and governor.magnitude_ceiling_for(pname)[0] is not None:
        governor.enforce_magnitude_ceiling(pname, v, allow_override=_read_allow_highres())
    if isinstance(v, str) and v:
        try:
            style = getattr(par, "style")
            style = style() if callable(style) else style
        except Exception:
            style = None
        if style in _FILE_PATH_STYLES:
            server.confined_path(v)    # raises PermissionError if the path escapes WORKING_DIR
    return v


@server.endpoint("scene_info")
def scene_info(params):
    """Proof/orientation endpoint: what's at root, plus build info."""
    root = server.ROOT
    kids = list(root.children) if root is not None else []
    return {
        "root": root.path if root is not None else "?",
        "td_build": server._app_build(),
        "children": [{"name": c.name, "type": c.opType} for c in kids],
    }


def _default_parent():
    """Where new content lands when no parent is given. Prefer the conventional user work container
    /project1 (what the network editor shows and what the driver/artist expects) over the system root
    '/' (which holds ui/sys/local/perform). Falls back to '/' if /project1 doesn't exist."""
    try:
        if server.OP("/project1") is not None:
            return "/project1"
    except Exception:
        pass
    return "/"


@server.endpoint("create_op")
def create_op(params):
    """Create an operator of `type` (optype string, e.g. 'compositeTOP') inside `parent` (defaults to
    /project1, the conventional work container, else '/'). Optional `name`, and optional `x`/`y` network
    position. Returns the new op's path/type/name."""
    parent = server.assert_writable(_resolve(params.get("parent") or _default_parent()))
    optype = server.check_optype_allowed(params["type"])
    name = params.get("name")
    n = parent.create(optype, name) if name else parent.create(optype)
    if "x" in params:
        try: n.nodeX = float(params["x"])
        except Exception: pass
    if "y" in params:
        try: n.nodeY = float(params["y"])
        except Exception: pass
    return {"path": n.path, "type": n.opType, "name": n.name, "family": n.family}


def _menu_token_error(par, value):
    """If `par` is a MENU parameter and `value` is a string that is NOT one of its valid tokens, return an
    error string so set_par REFUSES it (into `failed`) instead of letting TouchDesigner silently snap the
    garbage token to a real one and report success (driver-seat P1). Returns None when the value is fine,
    the param is not a menu, the value is not a string (an int index is left to TD), or the vocabulary
    can't be read (fail-open, like the numeric clamp). Data-only: reads the live menu vocabulary, never writes."""
    try:
        if not bool(getattr(par, "isMenu", False)):
            return None
        # StrMenu (an EDITABLE string field with menu SUGGESTIONS -- renamefrom/renameto/scope, sendername,
        # and ~1228 others) accepts ANY typed string; only a CLOSED 'Menu' restricts to its tokens. TD's
        # isMenu is True for BOTH, so gate on style: enforce the token check for 'Menu' only. check_par_allowed
        # already ran and still refuses code-sink params (callbacks/*script/datexpr) regardless of style, so
        # relaxing StrMenu only frees harmless data string fields -- the data-only boundary is unaffected.
        if str(getattr(par, "style", "")) != "Menu":
            return None
    except Exception:
        return None
    if not isinstance(value, str):
        return None  # a menu can also be set by integer index -- leave numeric values to TD
    try:
        names = list(getattr(par, "menuNames", None) or [])
    except Exception:
        names = []
    if not names or value in names:
        return None  # unknown vocabulary -> don't block; a valid token -> fine
    shown = ", ".join(names[:24]) + (" ..." if len(names) > 24 else "")
    return "invalid menu token %r for %r -- valid tokens: %s" % (value, getattr(par, "name", "?"), shown)


@server.endpoint("set_par")
def set_par(params):
    """Set parameters on `op`. `pars` is a dict {parName: value}. Menu params take their token string,
    numerics take numbers. Returns the evaluated values actually applied. Per-par failures are reported,
    not fatal."""
    n = server.assert_writable(_resolve(params["op"]))  # bridge infra is off-limits to mutation
    sets = params.get("pars", {})
    if not isinstance(sets, dict):
        raise ValueError("'pars' must be an object of {parName: value}")
    applied, failed = {}, {}
    for k, v in sets.items():
        try:
            p = getattr(n.par, k, None)
            if p is None:
                failed[k] = "no such parameter"
                continue
            server.check_par_allowed(n.opType, k, p)  # refuse code-eval sinks + params not in the data allowlist
            server.check_driver_shader_ref(k)  # pixeldat is delivered only via set_glsl (validated)
            menu_err = _menu_token_error(p, v)  # refuse a garbage menu token (TD would silently snap it)
            if menu_err:
                failed[k] = menu_err
                continue
            p.val = _guard_par_value(p, v)  # BOUNDARY guard: numeric clamp + file-path confinement
            applied[k] = server._jsonable(p.eval())  # OP-path params eval to an OP; keep result JSON-safe
        except Exception as e:
            failed[k] = str(e)
    out = {"path": n.path, "applied": applied, "all_applied": not failed}
    if failed:
        out["failed"] = failed
    # ADVISORY governor: attach a magnitude flag when the requested params are sizeable for a realtime
    # GPU (resolution / instance / particle / pass counts, incl. the non-commercial 1280 cap). Pure,
    # fail-soft, NEVER blocks and NEVER raises -- purely informational telemetry in the result.
    try:
        mag = governor.magnitude_advice(n.opType, sets)
        if mag.get("level") != "ok":
            out["magnitude"] = mag
    except Exception:
        pass
    return out


@server.endpoint("connect")
def connect(params):
    """Wire `from` op's output into `to` op's input. `input` = destination input index (default 0),
    `output` = source output index (default 0)."""
    src = _resolve(params["from"])
    dst = server.assert_writable(_resolve(params["to"]))  # can't wire INTO bridge infra
    in_idx = int(params.get("input", 0))
    out_idx = int(params.get("output", 0))
    if in_idx < 0 or out_idx < 0:   # a negative index would wrap to the LAST connector, not error
        raise ValueError("connector indices must be >= 0 (got input=%d, output=%d)" % (in_idx, out_idx))
    conns = dst.inputConnectors
    if in_idx >= len(conns):
        raise ValueError("input index %d out of range (%s has %d inputs)" % (in_idx, dst.path, len(conns)))
    # Connector.connect accepts the source OP (uses its output `out_idx`) or a specific out connector.
    if out_idx and out_idx < len(src.outputConnectors):
        conns[in_idx].connect(src.outputConnectors[out_idx])
    else:
        conns[in_idx].connect(src)
    return {"from": src.path, "to": dst.path, "input": in_idx,
            "inputs_now": [i.name if i else None for i in dst.inputs]}


@server.endpoint("set_flags")
def set_flags(params):
    """Set node flags on `op`: render / display / bypass / viewer / export (booleans). These are NODE
    FLAGS, not params -- e.g. inside a geometryCOMP the SOP with the render flag is what the Render TOP
    renders (the TD analog of Houdini's display-flag chain); `export` is a CHOP's Export Flag (toggling it
    re-resolves CHOP-channel -> parameter exports). Data-only: flags carry no code. Returns the flag states."""
    n = server.assert_writable(_resolve(params["op"]))
    out = {}
    for flag in ("display", "render", "bypass", "viewer", "export"):
        if flag in params:
            try:
                setattr(n, flag, bool(params[flag]))
                out[flag] = getattr(n, flag)
            except Exception as e:
                out[flag] = "ERR: " + str(e)
    return {"path": n.path, "flags": out}


# ---- ALLOWLISTED ACTION PULSES (the ONLY parameterless actions the bridge may fire) ----------------
# A "pulse" fires a TD parameter's ACTION (par.pulse()) -- a parameterless EVENT, not a VALUE and not
# CODE (contrast the code-sink VALUE params guarded in server.py). It is the same risk class as the CHOP
# export flag: it actuates already-built, data-only behaviour. But because a pulse still TRIGGERS
# something, arbitrary pulses are NOT allowed -- we ship an EXPLICIT REVIEWED ALLOWLIST of
# (optype -> {pulse params}), every entry a benign show-control / media / file action:
#   * timerCHOP        -- transport + cue navigation (start / initialize / cue, go-to {done, end-of-cycle,
#                         prev seg, next seg}, exit-at-end-of-cycle): parameterless timeline control.
#   * moviefileinTOP / audiofileinCHOP -- cue (jump to start) + reload (re-read the media file from disk).
#   * moviefileoutTOP  -- addframe (append ONE frame to a recording).
#   * fileinSOP / fileinDAT -- refresh (re-read geometry / table from disk).
#   * tableDAT         -- loadonstartpulse (Load File: re-read the external .csv/.tsv/.dat into the table;
#                         the file value is working-dir-confined, same class as the filein refresh pulses).
# DELIBERATELY EXCLUDED (never addable here without re-review): anything *execute* / *reinit* / re-cook-
# the-world, windowCOMP winopen/winclose (a human opens output windows), delete/destroy. This dict is the
# single auditable place; the marker guard below is defense-in-depth so a future edit cannot slip a
# code / exec / window pulse in by name. Mirrors server._DENY_CODE_SINK_PARS in spirit (reviewed set).
_PULSE_FORBIDDEN_MARKERS = ("execute", "reinit", "winopen", "winclose", "python", "script")
_ALLOW_PULSE = {
    "timerCHOP":       {"start", "initialize", "cuepulse", "gotodone", "gotoendcycle",
                        "gotoprevseg", "gotonextseg", "exitendcycle"},
    "moviefileinTOP":  {"cuepulse", "reloadpulse"},
    "audiofileinCHOP": {"cuepulse", "reloadpulse"},
    "moviefileoutTOP": {"addframe"},
    "fileinSOP":       {"refreshpulse"},
    "fileinDAT":       {"refreshpulse"},
    "tableDAT":        {"loadonstartpulse"},
}
# build-time self-check: no allowlisted pulse name may carry a forbidden marker (mirrors the RCE asserts).
for _ot, _ps in _ALLOW_PULSE.items():
    for _p in _ps:
        assert not any(m in _p.lower() for m in _PULSE_FORBIDDEN_MARKERS), \
            "allowlisted pulse %r on %r carries a forbidden marker" % (_p, _ot)


@server.endpoint("pulse")
def pulse(params):
    """Fire a parameterless ACTION pulse on `op`'s pulse parameter `par` -- the show-control actuator:
    cue/reload a movie, add a frame to a recording, refresh a File In from disk, or drive a timerCHOP's
    transport (start / cue / go-to segment). A pulse triggers an existing, data-only behaviour: it sets
    no value and carries no code. RESTRICTED to a fixed reviewed allowlist (_ALLOW_PULSE) -- any other
    (optype, param) is refused -- and the parameter must be Pulse/Momentary style.

      op  : the operator to pulse (required).
      par : the pulse parameter name (required); must be allowlisted for this op's type."""
    n = server.assert_writable(_resolve(params["op"]))
    name = params.get("par")
    if not name or not isinstance(name, str):
        raise ValueError("'par' (the pulse parameter name) is required")
    optype = n.opType
    allowed = _ALLOW_PULSE.get(optype)
    if not allowed or name not in allowed:
        raise PermissionError(
            "pulse %r on %r is not in the reviewed action-pulse allowlist (allowed for this type: %s)"
            % (name, optype, sorted(allowed) if allowed else "none"))
    if any(m in name.lower() for m in _PULSE_FORBIDDEN_MARKERS):
        raise PermissionError("pulse %r carries a forbidden marker" % name)  # defense-in-depth
    p = getattr(n.par, name, None)
    if p is None:
        raise ValueError("no such parameter %r on %s" % (name, n.path))
    style = getattr(p, "style", "?")
    if style not in ("Pulse", "Momentary"):
        raise PermissionError("parameter %r is style %r, not a Pulse action" % (name, style))
    p.pulse()
    return {"path": n.path, "pulsed": name, "optype": optype}


@server.endpoint("set_pos")
def set_pos(params):
    """Move the operator at `op` to network position (`x`, `y`) so networks can be laid out for
    legibility (the driver's layout tool -- unclutter a graph the AI built, align a chain). Either or
    both of x/y may be given. Data-only: node coordinates carry no code (they set n.nodeX / n.nodeY,
    the same attributes create_op's x/y set). Returns the resulting position."""
    n = server.assert_writable(_resolve(params["op"]))
    out = {}
    if "x" in params:
        try:
            n.nodeX = float(params["x"]); out["x"] = round(n.nodeX, 3)
        except Exception as e:
            out["x"] = "ERR: " + str(e)
    if "y" in params:
        try:
            n.nodeY = float(params["y"]); out["y"] = round(n.nodeY, 3)
        except Exception as e:
            out["y"] = "ERR: " + str(e)
    return {"path": n.path, "pos": out}


@server.endpoint("delete_op")
def delete_op(params):
    """Destroy the operator at `op`."""
    n = server.assert_writable(_resolve(params["op"]))
    path = n.path
    # If the persistent capture-viewer helper is pointed at this node, clear that reference first so the
    # delete doesn't leave a dangling 'Operator Viewer' path that find_errors would flag. Best-effort.
    try:
        from td_executor.handlers import io as _io
        _io.release_capture_viewer_ref(n)
    except Exception:
        pass
    n.destroy()
    return {"destroyed": path}


@server.endpoint("read_network")
def read_network(params):
    """Read the STRUCTURE of the network at `path` (default '/') -- each child's name, type, input
    wiring, and flags. Token-cheap map for rebuilding the picture of a graph. `pars=true` also dumps
    each node's NON-default parameters (the artist's actual choices) with evaluated values."""
    net = _resolve(params.get("path", "/"))
    want_pars = bool(params.get("pars", False))
    nodes = []
    for c in net.children:
        entry = {
            "name": c.name,
            "type": c.opType,
            "inputs": [i.name if i else None for i in c.inputs],
            "children": (c.numChildren if c.isCOMP else 0),
        }
        try:
            entry["pos"] = [round(c.nodeX, 1), round(c.nodeY, 1)]
        except Exception:
            pass
        if want_pars:
            pd = {}
            try:
                for p in c.pars():
                    try:
                        if p.isDefault:
                            continue
                        pd[p.name] = server._jsonable(p.eval())
                    except Exception:
                        continue
            except Exception:
                pass
            if pd:
                entry["pars"] = pd
        nodes.append(entry)
    return {"path": net.path, "type": net.opType, "child_count": len(net.children), "nodes": nodes}


def _set_lit(op, name, value):
    """Set a literal parameter VALUE if the parameter exists (never an expression). Returns True if applied.

    Forces the parameter to CONSTANT mode first, so the literal takes effect even when the parameter ships a
    default EXPRESSION. (fileinSOP.file defaults to an expression evaluating to app.samplesFolder+'/Geo/
    defgeo.tog'; setting only .val leaves the param in Expression mode and TD keeps loading the default
    sample geometry -- the cause of import_segmented_model silently loading defgeo.tog instead of the OBJ.)
    Forcing CONSTANT can only REMOVE an expression (data-only / fail-closed), never introduce one."""
    p = getattr(op.par, name, None)
    if p is None:
        return False
    try:
        # Clear any default expression/bind so the literal below is what evaluates. `.mode.__class__.CONSTANT`
        # avoids importing ParMode and is defensive against the offline mock (whose mode is a plain str).
        try:
            p.mode = p.mode.__class__.CONSTANT
        except Exception:
            pass
        p.val = _guard_par_value(p, value)  # BOUNDARY guard: numeric clamp + file-path confinement
        return True
    except Exception:
        return False


@server.endpoint("set_par_many")
def set_par_many(params):
    """Set the SAME parameters on MANY operators in one call (the projection-mapping rig routinely needs
    'assign this emit map to all 15 materials' or 'blackout every window mat'). Targets are either an
    explicit `ops` list of paths, or a name `pattern` (glob, e.g. 'mat*') under `parent` (default
    /project1). `pars` is the SAME {parName: value} map applied to each. Uses the exact data-only set_par
    discipline per op (literal values only, code-sink params refused); per-op/per-par failures are
    reported, not fatal. Returns a per-op result list.

      ops     : list of operator paths (use this OR pattern).
      pattern : glob over child NAMES under `parent` (e.g. 'mat*', 'sec0*').
      parent  : container for `pattern` (default /project1).
      pars    : {parName: value} applied to every matched op (literal values, no expressions)."""
    pars = params.get("pars", {})
    if not isinstance(pars, dict) or not pars:
        raise ValueError("'pars' must be a non-empty object {parName: value}")
    targets = []
    ops = params.get("ops")
    if ops:
        if not isinstance(ops, list):
            raise ValueError("'ops' must be a list of operator paths")
        for o in ops:
            targets.append(server.assert_writable(server.resolve_op(o)))  # bridge infra off-limits
    else:
        pattern = params.get("pattern")
        if not pattern:
            raise ValueError("provide 'ops' (a list of paths) or 'pattern' (a name glob) + 'parent'")
        parent = server.resolve_op(params.get("parent") or "/project1")
        import fnmatch
        for c in parent.children:
            if fnmatch.fnmatch(str(getattr(c, "name", "")), str(pattern)):
                try:
                    targets.append(server.assert_writable(c))  # silently skip bridge infra
                except PermissionError:
                    pass
        if not targets:
            raise ValueError("no operators under %s matched name pattern %r" % (parent.path, pattern))
    results = []
    for n in targets:
        applied, failed = {}, {}
        for k, v in pars.items():
            try:
                p = getattr(n.par, k, None)
                if p is None:
                    failed[k] = "no such parameter"
                    continue
                server.check_par_allowed(n.opType, k, p)  # code-eval sinks + non-allowlist params refused
                server.check_driver_shader_ref(k)  # pixeldat only via set_glsl (same as set_par)
                menu_err = _menu_token_error(p, v)  # refuse a garbage menu token (same as set_par)
                if menu_err:
                    failed[k] = menu_err
                    continue
                p.val = _guard_par_value(p, v)  # F3/F2: same clamp + path guard as set_par
                applied[k] = server._jsonable(p.eval())
            except Exception as e:
                failed[k] = str(e)
        entry = {"op": n.path, "applied": applied, "all_applied": not failed}
        if failed:
            entry["failed"] = failed
        results.append(entry)
    return {"count": len(results), "results": results}


@server.endpoint("import_segmented_model")
def import_segmented_model(params):
    """Build the per-section projection-mapping RIG in ONE call: scan a directory for section OBJ parts
    (section_00.obj, section_01.obj, …) and, for each, create a geometryCOMP (sec00, sec01, …) with a
    File In SOP loading that part (render+display flags on, default child deleted) and a dedicated phongMAT
    (mat00, mat01, …) with diffuse/spec/emit zeroed (dark, emission-driven) assigned to it. Collapses the
    ~6-calls-per-section rig (~90 calls for 15 parts) into one. Then animate with a choreography recipe
    (drive matNN emit) or assign per-section content (matNN.emitmap) and render with a renderTOP
    geometry='<prefix>*'. Data-only: only creates data operators + sets literal values/flags.

      dir       : directory holding the section OBJ parts (read-only; the Houdini export dir is fine).
      pattern   : glob for the parts (default 'section_*.obj'); sorted -> section index order.
      parent    : COMP to build under (default /project1).
      prefix    : section COMP name prefix (default 'sec' -> sec00..secNN; renderTOP geometry='sec*').
      matprefix : material name prefix (default 'mat' -> mat00..matNN).

    Returns the built sections (COMP path, material path, source file). Idempotent by name (re-running
    rebuilds the same sec/mat names)."""
    import glob as _glob
    import os as _os
    # Confine the scan directory to the working dir (models must live under it anyway). Turns the
    # old unconfined-dir existence/content oracle into a clear refusal, and stops silent empty-section builds
    # from OBJs the per-file confinement would reject.
    directory = server.confined_path(str(params["dir"]))
    if not _os.path.isdir(directory):
        raise ValueError("not a directory (or outside the working dir): %s" % directory)
    pattern = str(params.get("pattern", "section_*.obj"))
    files = sorted(_glob.glob(_os.path.join(directory, pattern)))
    if not files:
        raise ValueError("no files matching %r in %s" % (pattern, directory))
    if len(files) > 512:
        raise ValueError("refusing to build %d sections (>512) -- narrow the pattern" % len(files))
    parent = server.assert_writable(server.resolve_op(params.get("parent") or "/project1"))
    if parent is None:
        raise ValueError("no such parent COMP: %s" % (params.get("parent") or "/project1"))
    prefix = str(params.get("prefix", "sec"))
    matprefix = str(params.get("matprefix", "mat"))
    server.check_optype_allowed("geometryCOMP")
    server.check_optype_allowed("fileinSOP")
    server.check_optype_allowed("phongMAT")

    def _reuse_or_create(container, name, optype):
        for c in list(container.children):
            if getattr(c, "name", None) == name:
                return c
        return container.create(optype, name)

    def _geo_attribs(sop):
        """Report (has_uv, has_normal, npoints) for a cooked File In SOP, fully defensively.
        UVs (attrib 'uv') are load-bearing for the projection method (emitmapcoord=uv0); normals ('N')
        matter for any lit/PBR path. Never raises -- unknown/mock API -> (False, False, None)."""
        try:
            sop.cook(force=True)
        except Exception:
            pass
        has_uv = has_normal = False
        npts = None
        try:
            npts = int(sop.numPoints)
        except Exception:
            pass
        for coll_name in ("pointAttribs", "vertexAttribs", "primAttribs"):
            try:
                coll = getattr(sop, coll_name, None)
                if coll is None:
                    continue
                names = {getattr(a, "name", None) for a in coll}
                if names & {"uv", "uv0"}:
                    has_uv = True
                if "N" in names:
                    has_normal = True
            except Exception:
                pass
        return has_uv, has_normal, npts

    built = []
    warnings = []
    for i, f in enumerate(files):
        secname = "%s%02d" % (prefix, i)
        matname = "%s%02d" % (matprefix, i)
        geo = _reuse_or_create(parent, secname, "geometryCOMP")
        # Clear the geometryCOMP's default child (the torus that carries render/display) + any prior build.
        for ch in list(geo.children):
            try:
                ch.destroy()
            except Exception:
                pass
        fin = geo.create("fileinSOP", "file1")
        _set_lit(fin, "file", f)
        for flag in ("render", "display"):
            try:
                setattr(fin, flag, True)
            except Exception:
                pass
        has_uv, has_normal, npts = _geo_attribs(fin)
        if not npts:
            warnings.append("%s: File In loaded 0 points from %s -- the OBJ did not load (verify the file "
                            "exists and is under the working dir)" % (secname, f.replace("\\", "/")))
        if not has_uv:
            warnings.append("%s: no 'uv' attribute -- emitmapcoord=uv0 content will NOT map correctly "
                            "(re-export the section with UVs)" % secname)
        if not has_normal:
            warnings.append("%s: no 'N' (normal) attribute -- lit/PBR shading may be flat" % secname)
        mat = _reuse_or_create(parent, matname, "phongMAT")
        for p in ("diffr", "diffg", "diffb", "specr", "specg", "specb", "emitr", "emitg", "emitb"):
            _set_lit(mat, p, 0)
        # Assign the material to the COMP (OP-reference param; try the OP object then the path string).
        mp = getattr(geo.par, "material", None)
        if mp is not None:
            try:
                mp.val = mat
            except Exception:
                try:
                    mp.val = mat.path
                except Exception:
                    pass
        _set_lit(geo, "render", True)
        built.append({"section": geo.path, "material": mat.path, "file": f.replace("\\", "/"),
                      "uv": has_uv, "normals": has_normal, "points": npts})
    n_uv = sum(1 for b in built if b["uv"])
    n_norm = sum(1 for b in built if b["normals"])
    return {"count": len(built), "parent": parent.path, "prefix": prefix, "sections": built,
            "uv_ok": n_uv, "normals_ok": n_norm, "warnings": warnings,
            "note": ("Built %d per-section geometryCOMPs + phongMATs (dark, emission-driven); %d/%d have UVs, "
                     "%d/%d have normals. Render with a renderTOP geometry='%s*' + a camera; animate via a "
                     "choreography (drive matNN emit) or assign per-section content (matNN.emitmap, "
                     "emitmapcoord=uv0)." % (len(built), n_uv, len(built), n_norm, len(built), prefix))}
