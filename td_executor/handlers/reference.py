"""td_executor/handlers/reference.py -- the reference backbone.

operator_reference: query the probed TD catalog (reference/catalog.json) for any operator's real
parameters (name / style / default / range / menu tokens / tuplet). This is what lets the driving
agent build correctly WITHOUT guessing param names -- the "node reference the MCP can call on".
Read-only, data-only.
"""
import json
import os
from td_executor import server

_CACHE = {"data": None, "mtime": None}


def _catalog():
    path = os.path.join(server._REPO_DIR, "reference", "catalog.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise ValueError("catalog.json not found (reference data missing): %s" % path)
    if _CACHE["data"] is None or _CACHE["mtime"] != mtime:
        with open(path, "r", encoding="utf-8") as f:
            _CACHE["data"] = json.load(f)
        _CACHE["mtime"] = mtime
    return _CACHE["data"]


# Compact mode drops the two bulky UI-range arrays so a build touching many operator types doesn't blow
# the driver's context (driver-seat P2): the middle tier between `help` (names only) and the full typed
# dump. Everything identifying/behavioural stays -- name/style/default/label/tokens/tuplet.
_COMPACT_DROP = ("norm", "hard")


def _compact_param(p):
    """A slimmed parameter: keep name/style/default/label/tokens/tuplet; drop the norm/hard range arrays
    and a null `tokens` field."""
    return {k: v for k, v in p.items() if k not in _COMPACT_DROP and not (k == "tokens" and v is None)}


# The two param-naming conventions a driver MUST NOT mix (mismatched names are silently dropped):
#   * an operator CREATE tool takes a multi-component parameter by its TUPLET name as a VECTOR
#     (color=[r,g,b], resolution=[w,h], t=[x,y,z]); single-component params by name.
#   * set_par (adjust an existing node) takes the RAW COMPONENT names shown in each param's `name`
#     (colorr, resolutionw, tx) inside its `pars` object.
# A create tool does NOT accept the raw component name (colorr) -- the MCP schema silently strips it, so
# the node keeps the default. This note + create_vector_params below make that explicit at the point the
# driver reads the parameter list.
_PARAM_NAMING_NOTE = (
    "PARAM NAMING: to CREATE via an operator tool, pass a multi-component parameter by its TUPLET name "
    "as a vector (see create_vector_params, e.g. color=[r,g,b], resolution=[w,h], t=[x,y,z]); pass "
    "single-component params by name. To adjust an EXISTING node via set_par, use the raw component "
    "names in each param's 'name' (colorr, resolutionw, tx) inside pars={}. A create tool does NOT accept "
    "the raw component name -- it is silently ignored (the value stays default). Never wrap create-tool "
    "params in a pars={} object."
)


def _create_vector_params(plist):
    """Derive the tuplet-vector param names an operator CREATE tool accepts: {tuplet: [component names]}
    for every multi-component tuplet (color -> [colorr,colorg,colorb], t -> [tx,ty,tz], ...). Single-
    component params (name == tuplet) are passed by name and are omitted here."""
    groups = {}
    for p in plist:
        t, n = p.get("tuplet"), p.get("name")
        if t and n and t != n:
            groups.setdefault(t, []).append(n)
    return {t: comps for t, comps in groups.items() if len(comps) > 1}


@server.endpoint("operator_reference")
def operator_reference(params):
    """Look up TD operator parameters. `optype`='compositeTOP' -> that op's full param list (add
    compact=true for a slimmer name/style/default/tokens view); `search`='blur' -> matching optypes;
    no args -> a per-family summary."""
    cat = _catalog()
    optype = params.get("optype")
    search = params.get("search")

    if optype:
        info = cat.get(optype)
        if info is None:
            near = sorted(k for k in cat if optype.lower() in k.lower())[:10]
            hint = (" (near: %s)" % ", ".join(near)) if near else ""
            raise ValueError("unknown optype %r%s" % (optype, hint))
        compact = bool(params.get("compact"))
        plist = [_compact_param(p) for p in info["params"]] if compact else info["params"]
        return {"optype": optype, "family": info["family"],
                "maxinputs": info["maxinputs"], "param_count": len(info["params"]),
                "compact": compact, "param_naming": _PARAM_NAMING_NOTE,
                "create_vector_params": _create_vector_params(info["params"]),
                "params": plist}

    if search:
        s = search.lower()
        matches = sorted(k for k in cat if s in k.lower())
        return {"search": search, "count": len(matches), "matches": matches}

    by_family = {}
    for v in cat.values():
        by_family[v["family"]] = by_family.get(v["family"], 0) + 1
    return {"operators": len(cat), "by_family": by_family}
