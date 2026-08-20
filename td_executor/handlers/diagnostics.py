"""td_executor/handlers/diagnostics.py -- read-only diagnostics for the driver's self-correction loop.

find_errors : scan a subtree for operators in an error/warning state (the AI's "why is this broken"
              tool; competitor parity -- 6/7 other TD MCPs expose this, and it's how a blind driver
              recovers). Uses OP.errors()/OP.warnings() (verified against OP_Class.htm).
top_info    : cheap numeric state of a TOP (resolution/format/GPU mem/cook) -- the budget-friendly
              default check so the driver reserves save_top's inline PNG for milestones (the TD analog
              of Houdini's read_geo_stats vs snapshot verification budget).
inspect     : deep read-only state of ONE operator -- node flags (incl. the CHOP Export Flag), a CHOP's
              live channel values, a DAT's cell grid, and a parameter's mode + export/bind source. The
              "why isn't my binding working / what state is this node in" tool.
probe_optype: SELF-PROBE the live typed parameter schema of ANY operator type by briefly creating a
              throwaway instance, introspecting it exactly like operator_reference (name/style/default/
              range/tokens/tuplet + family + maxinputs), and destroying it -- so an optype that is NOT in
              the shipped catalog.json (probed offline) can still be described from the running TD build.
              Ends the "operator not in the 509-op catalog" lockout class. Refuses code-carrying optypes
              (check_optype_allowed) and leaves NOTHING behind (scratch subtree destroyed in a finally).
Read-only w.r.t. the user's network (probe_optype's temp op is created + destroyed within the call);
no persistent scene mutation.
"""
import secrets

from td_executor import server
from td_executor import governor


@server.endpoint("mem")
def mem(params):
    """Report the live resource ENVELOPE for scale-governed operation sizing: system RAM (total/avail/
    load) + the honest VRAM telemetry limit (TouchDesigner's Python API exposes GPU memory only PER-TOP,
    not a whole-card total/avail — so the band classifies on system RAM; pass `op`=<a TOP path> to also
    read that TOP's gpu_memory_bytes). Optionally pass `optype`+`pars` to PRE-CHECK a planned op's
    requested magnitude (resolution / instance counts / render passes) before you build it. ADVISORY:
    this never blocks. READ-ONLY."""
    out = governor.envelope_status()
    # Optional per-TOP GPU memory (the only GPU-memory signal TD exposes) when an op is named.
    op = params.get("op")
    if op:
        try:
            n = server.resolve_op(op)
            gm = getattr(n, "gpuMemory", None)
            if gm is not None:
                out["op"] = n.path
                out["op_gpu_memory_bytes"] = gm
        except Exception as e:
            out["op_note"] = "could not read op gpu memory: " + str(e)[:80]
    # Optional pre-check of a planned op's magnitude (pure; from the caller's would-be params).
    optype = params.get("optype")
    if optype:
        out["magnitude"] = governor.magnitude_advice(optype, params.get("pars", {}))
    return out


@server.endpoint("find_errors")
def find_errors(params):
    """Scan the network subtree at `path` (default '/') for operators reporting errors (and, unless
    include_warnings=false, warnings). Returns a list of {path,type,errors,warnings}. READ-ONLY --
    the diagnose/troubleshoot tool: 'why isn't this working', 'what's broken', 'find the cook error'."""
    root = server.resolve_op(params.get("path", "/"))
    include_warnings = params.get("include_warnings", True)
    max_nodes = int(server.clamp(int(params.get("max_nodes", 5000)), 1, 100000))  # bounded ceiling

    issues = []
    scanned = 0
    stack = [root]
    seen = set()
    while stack and scanned < max_nodes:
        n = stack.pop()
        try:
            key = n.path
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        scanned += 1
        try:
            errs = n.errors(recurse=False) or ""
        except Exception:
            errs = ""
        try:
            warns = (n.warnings(recurse=False) or "") if include_warnings else ""
        except Exception:
            warns = ""
        if errs or warns:
            entry = {"path": key, "type": getattr(n, "opType", "?")}
            if errs:
                entry["errors"] = str(errs)[:1000]
            if warns:
                entry["warnings"] = str(warns)[:1000]
            issues.append(entry)
        # descend into COMP children
        try:
            if getattr(n, "isCOMP", False):
                stack.extend(list(n.children))
        except Exception:
            pass
    return {"path": root.path, "scanned": scanned, "issue_count": len(issues), "issues": issues}


_INSPECT_FLAGS = ("display", "render", "bypass", "viewer", "export", "clone", "lock")


@server.endpoint("inspect")
def inspect(params):
    """READ-ONLY deep-state introspection of ONE operator -- the 'why isn't my binding working / what
    state is this node actually in' tool. Reports the node's identity, its NODE FLAGS (the CHOP Export
    Flag among them -- the animation-debugging signal), a CHOP's live channel values, a DAT's cell grid,
    and (when `par` is given) that parameter's evaluation MODE and its export/bind SOURCE -- so you can
    see whether a parameter is in Export mode and which CHOP channel drives it.

      op   : the operator to inspect (required).
      par  : optional parameter name to also report its mode + export/bind source (the CHOP-export
             debugging tool: is this parameter actually in Export mode, and what drives it?).

    READ-ONLY: every attribute read is wrapped defensively (live TD objects expose different attrs per
    op type) and NOTHING is ever written -- no parameter, flag, or value is set."""
    n = server.resolve_op(params["op"])
    out = {}
    try:
        out["path"] = n.path
    except Exception:
        out["path"] = str(params.get("op"))
    try:
        out["type"] = getattr(n, "opType", "?")
    except Exception:
        out["type"] = "?"
    try:
        out["family"] = getattr(n, "family", "?")
    except Exception:
        out["family"] = "?"

    # NODE FLAGS: report only the booleans that genuinely exist as real bools on this op (getattr;
    # families expose different subsets). `export` (the CHOP Export Flag) is the load-bearing one.
    flags = {}
    for fname in _INSPECT_FLAGS:
        try:
            fv = getattr(n, fname, None)
        except Exception:
            fv = None
        if isinstance(fv, bool):
            flags[fname] = fv
    out["flags"] = flags

    # CHOP: up to 32 channels with their current values (eval() -> [0] -> float()).
    try:
        is_chop = bool(getattr(n, "isCHOP", False))
    except Exception:
        is_chop = False
    if is_chop:
        chans = []
        try:
            source = list(n.chans())
        except Exception:
            source = []
        for c in source[:32]:
            entry = {}
            try:
                entry["name"] = c.name
            except Exception:
                entry["name"] = "?"
            val = None
            try:
                val = c.eval()
            except Exception:
                try:
                    val = c[0]
                except Exception:
                    try:
                        val = float(c)
                    except Exception:
                        val = None
            entry["val"] = server._jsonable(val)
            chans.append(entry)
        out["channels"] = chans

    # DAT: the cell grid (cap ~50 rows x ~12 cols) as strings.
    try:
        is_dat = bool(getattr(n, "isDAT", False))
    except Exception:
        is_dat = False
    if is_dat:
        rows = []
        try:
            nr = int(getattr(n, "numRows", 0))
        except Exception:
            nr = 0
        try:
            nc = int(getattr(n, "numCols", 0))
        except Exception:
            nc = 0
        for r in range(min(nr, 50)):
            row = []
            for c in range(min(nc, 12)):
                try:
                    row.append(str(n[r, c].val))
                except Exception:
                    row.append("")
            rows.append(row)
        out["rows"] = rows

    # Optional parameter report: mode + export/bind source (reveals Export mode + the driving channel).
    par = params.get("par")
    if par:
        par = str(par)
        pinfo = {"name": par}
        try:
            p = getattr(n.par, par, None)
        except Exception:
            p = None
        if p is None:
            pinfo["error"] = "no such parameter %r on %s" % (par, out.get("path"))
        else:
            try:
                pinfo["val"] = server._jsonable(p.eval())
            except Exception:
                pass
            try:
                pinfo["mode"] = str(p.mode)
            except Exception:
                pass
            # export/bind SOURCE: report .path when the source is an OP object, else its str value.
            for attr in ("exportOP", "exportSource", "bindMaster"):
                try:
                    v = getattr(p, attr, None)
                except Exception:
                    v = None
                if v in (None, ""):
                    continue
                pv = getattr(v, "path", None)
                pinfo[attr] = server._jsonable(pv if pv is not None else v)
            # a parameter EXPRESSION string, only if one is set (read-only -- never written here).
            try:
                ex = getattr(p, "expr", None)
            except Exception:
                ex = None
            if ex:
                pinfo["expr"] = server._jsonable(ex)
        out["par"] = pinfo

    return out


@server.endpoint("top_info")
def top_info(params):
    """Cheap numeric state of a TOP at `op`: resolution, aspect, GPU memory, and cook stats. The
    budget-friendly inspection default -- use this for routine checks and reserve save_top (inline
    image) for milestones. Errors if `op` is not a TOP."""
    n = server.resolve_op(params["op"])
    if not hasattr(n, "width"):
        raise ValueError("%s is not a TOP (no resolution); top_info is TOP-only" % n.path)
    out = {"path": n.path, "type": getattr(n, "opType", "?")}
    for attr, key in (("width", "width"), ("height", "height"), ("aspect", "aspect"),
                      ("gpuMemory", "gpu_memory_bytes"), ("cpuCookTime", "cook_ms"),
                      ("totalCooks", "total_cooks")):
        try:
            out[key] = getattr(n, attr)
        except Exception:
            pass
    return out


# ---- probe_optype: live self-probe of an operator type's typed parameter schema -------------------
# WHY: reference/catalog.json is built OFFLINE from a one-time live probe over
# reference/include_optypes.json. Any optype NOT in that include-list is a hard
# lockout for the driver -- operator_reference/help can't describe it and there is no typed create tool.
# This endpoint closes that class GENERICALLY: it briefly creates a throwaway instance of `optype`,
# introspects every parameter with the SAME field set the offline probe captures (so the result is
# catalog-shaped and could seed a catalog rebuild), then destroys the scratch subtree. It is data-only:
# check_optype_allowed refuses code-carrying optypes FIRST (a probe must never instantiate a code op),
# it writes nothing persistent, and it leaves no node behind.

def _probe_parent():
    """Where the throwaway scratch COMP lands: the conventional work container /project1 if present, else
    the root. Mirrors control._default_parent()'s preference without importing across handlers."""
    for path in ("/project1", "/"):
        try:
            n = server.OP(path) if server.OP is not None else None
            if n is not None:
                return n
        except Exception:
            pass
    return server.resolve_op("/")   # last resort (raises if the executor isn't bound)


def _probe_param(p):
    """Introspect ONE live Par into the SAME shape build_catalog.py emits into catalog.json
    (label/name/style/default/norm/hard/tokens/tuplet). Every attribute read is wrapped defensively --
    live TD Par objects expose these as properties, but we never assume a given one is present."""
    d = {}
    for key, attr in (("label", "label"), ("name", "name"), ("default", "default"),
                      ("tuplet", "tupletName")):
        try:
            d[key] = server._jsonable(getattr(p, attr))
        except Exception:
            d[key] = None
    try:
        d["style"] = str(getattr(p, "style"))
    except Exception:
        d["style"] = None
    # norm = [normMin, normMax]  (TD's UI slider range)
    norm = []
    for attr in ("normMin", "normMax"):
        try:
            norm.append(server._jsonable(getattr(p, attr)))
        except Exception:
            norm.append(None)
    d["norm"] = norm
    # hard = [min, max, clampMin, clampMax]  (the real clamp declaration)
    hard = []
    for attr in ("min", "max", "clampMin", "clampMax"):
        try:
            hard.append(server._jsonable(getattr(p, attr)))
        except Exception:
            hard.append(None)
    d["hard"] = hard
    # tokens = menu vocabulary when this is a menu param, else None (matches catalog.json)
    try:
        mn = getattr(p, "menuNames", None)
        d["tokens"] = [str(x) for x in mn] if mn else None
    except Exception:
        d["tokens"] = None
    return d


@server.endpoint("probe_optype")
def probe_optype(params):
    """SELF-PROBE the live typed parameter schema of operator type `optype` from the RUNNING TD build --
    the "operator not in the offline catalog" escape hatch. Briefly creates a throwaway instance of the
    type inside a scratch COMP, introspects it exactly like operator_reference (each parameter's name /
    style / default / norm+hard range / menu tokens / tuplet, plus the op's family + maxinputs), then
    DESTROYS the scratch subtree in a finally so nothing persists and no node is left behind.

    DATA-ONLY: `check_optype_allowed` runs FIRST and refuses any code-carrying optype (script/execute/
    cplusplus markers + the exact evaluator denylist) -- a probe must never instantiate a code op. The
    whole introspection is defensive: if the CREATE itself fails (some operators need a special parent
    context), the call returns {optype, ok:False, error:...} instead of raising, and every attribute read
    is wrapped. Returns {optype, ok, family, maxinputs, param_count, params:[...]}.
      optype : the operator type to probe (e.g. 'syncinCHOP'). REQUIRED."""
    optype = params.get("optype")
    if not optype:
        raise ValueError("probe_optype requires 'optype'")
    optype = server.check_optype_allowed(optype)   # (a) guard FIRST -- refuse code-carrying optypes

    # (b) throwaway scratch COMP under the work container; a collision-proof unique name.
    parent = _probe_parent()
    scratch_name = "__mcp_probe_%s" % secrets.token_hex(4)
    scratch = None
    temp = None
    try:
        try:
            server.check_optype_allowed("baseCOMP")   # data-only container (defense in depth)
            scratch = parent.create("baseCOMP", scratch_name)
        except Exception as e:
            return {"optype": optype, "ok": False, "error": "scratch container create failed: %s" % (str(e)[:200])}

        # (b cont.) create the temp op of the requested type inside the scratch COMP.
        try:
            temp = scratch.create(optype)
        except Exception as e:
            # some ops need a special context (e.g. a specific parent family) -- report, don't raise.
            return {"optype": optype, "ok": False, "error": "create failed: %s" % (str(e)[:200])}

        # (c) introspect family / maxinputs / every parameter (all defensive).
        out = {"optype": optype, "ok": True}
        try:
            out["family"] = str(getattr(temp, "family"))
        except Exception:
            out["family"] = "?"
        try:
            out["maxinputs"] = server._jsonable(getattr(temp, "maxInputs"))
        except Exception:
            out["maxinputs"] = "?"
        plist = []
        try:
            live = list(temp.pars())
        except Exception:
            live = []
        for p in live:
            try:
                plist.append(_probe_param(p))
            except Exception:
                pass
        out["param_count"] = len(plist)
        out["params"] = plist
        return out
    finally:
        # (d) ALWAYS destroy the scratch subtree (the temp op + anything it spawned) -- even on error.
        for node in (temp, scratch):
            if node is not None:
                try:
                    node.destroy()
                except Exception:
                    pass
