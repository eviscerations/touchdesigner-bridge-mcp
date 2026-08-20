"""td_executor/handlers/glsl.py -- the VALIDATED GLSL fragment-shader lane (the first code lane).

The validated code-lane architecture:
executor-authoritative, validate-BEFORE-write, default-off consent read fresh from arm.json, audit log,
one-validated-path invariant. `set_glsl` is THE single place in the entire MCP that writes a DAT's `.text`
-- and it only ever writes to a Text DAT it creates and OWNS under the target glslTOP, wired only to that
op's `pixeldat`. The text is GLSL, gated to the GPU sandbox; it can never become a host-Python code
pointer because `_DENY_PARAM_NAMES_UNIVERSAL` (server.py) still refuses callbacks/*script refs on EVERY op.
"""
import os
import json
import time

from td_executor import server
from td_executor.glsl_validator import validate_glsl, GlslValidationError

# Derived (never caller-supplied) name of the Text DAT this lane owns under a glslTOP.
_OWNED_DAT_NAME = "__mcp_pixel"

# Low build-resolution / pass ceilings (the compensating control for a TOP auto-cooking: a 4K/8K glslTOP
# is the strongest TDR vector regardless of how clean the source is). The human raises resolution + drives
# the output lane themselves for 4K delivery.
_RES_W_CEIL = 1280
_RES_H_CEIL = 720
_NPASSES_CEIL = 4


def _config_dir():
    return os.path.join(os.path.expanduser("~"), ".touchdesigner-bridge-mcp")


def _read_allow_glsl():
    """Read `allow_glsl` from ~/.touchdesigner-bridge-mcp/arm.json, default False if file/key absent.
    Lives HERE (not server.py) to keep the lanes decoupled; read FRESH on every call (mirror of
    Houdini's _allow_attrib_expr reading arm.json)."""
    try:
        with open(os.path.join(_config_dir(), "arm.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return bool(data.get("allow_glsl", False))
    except Exception:
        return False


def _audit(record):
    """Best-effort append one line to glsl_audit.log in the working dir. Never blocks / raises."""
    try:
        path = os.path.join(server.working_dir(), "glsl_audit.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _clamp_par(op, name, ceiling):
    """Clamp a numeric build-parameter to `ceiling` if it exceeds it (or is unreadable). Returns the
    applied value, or None if the param is absent. Goes through check_par_allowed for parity (these are
    plain data params, never denied)."""
    p = getattr(op.par, name, None)
    if p is None:
        return None
    try:
        server.check_par_allowed(op.opType, name)
        cur = p.eval()
        if not isinstance(cur, (int, float)) or isinstance(cur, bool) or cur > ceiling:
            p.val = ceiling
        return p.eval()
    except Exception:
        return None


@server.endpoint("set_glsl")
def set_glsl(params):
    """Deliver VALIDATED GLSL fragment source into a glslTOP's pixel shader -- the ONE sanctioned DAT-.text
    write. Params: {op, stage, source}. FLOW (strict order): consent -> validate BEFORE any mutation ->
    resolve/assert glslTOP+pixel -> create-or-reuse owned child Text DAT -> write .text -> wire pixeldat via
    the guarded set_par path -> clamp resolution/npasses -> audit. Fail-closed at every step; on refusal
    NOTHING is written. Returns {applied, dat, warnings}."""
    op_path = params.get("op")
    stage = params.get("stage")
    source = params.get("source")

    # (1) CONSENT -- default-off, read fresh from arm.json. Refuse before touching anything.
    if not _read_allow_glsl():
        _audit({"ts": time.time(), "event": "refused_consent", "op": op_path, "stage": stage})
        raise PermissionError(
            "GLSL lane not consented (set allow_glsl:true in "
            "~/.touchdesigner-bridge-mcp/arm.json and re-arm)")

    if not isinstance(source, str):
        raise ValueError("'source' (the GLSL fragment source string) is required")

    # (2) VALIDATE BEFORE ANY MUTATION -- executor is authoritative. On failure: refuse, nothing written.
    try:
        validate_glsl(source, stage)
    except GlslValidationError as e:
        _audit({"ts": time.time(), "event": "rejected", "op": op_path, "stage": stage,
                "rule": getattr(e, "rule", None)})
        raise ValueError("GLSL validation failed [%s]: %s" % (getattr(e, "rule", "?"), e))

    # (3) resolve op; assert it is a glslTOP and stage=='pixel' (no compute, no POP/MAT).
    n = server.assert_writable(server.resolve_op(op_path))
    if n.opType != "glslTOP":
        raise PermissionError("set_glsl targets a glslTOP only (got %r)" % n.opType)
    if stage != "pixel":
        raise PermissionError("set_glsl v1 accepts stage 'pixel' only (got %r)" % (stage,))

    # (4) create-or-reuse the DERIVED, glslTOP-owned child Text DAT (path is never caller-supplied).
    dat = None
    for c in list(n.children):
        if getattr(c, "name", None) == _OWNED_DAT_NAME:
            dat = c
            break
    if dat is None:
        server.check_optype_allowed("textDAT")   # re-run the optype guard on create
        dat = n.create("textDAT", _OWNED_DAT_NAME)

    # (5) THE SINGLE DAT-.text WRITE IN THE ENTIRE MCP. Isolated + validated + consent-gated + audited.
    #     `source` has passed validate_glsl above; it is verbatim GLSL fragment text bound to the GPU.
    dat.text = source

    # (6) wire the glslTOP's pixeldat to the owned DAT via the GUARDED set_par path (do NOT bypass).
    #     pixeldat is a NodePath data ref, not a denied code-pointer -- check_par_allowed lets it through.
    server.check_par_allowed(n.opType, "pixeldat")
    pd = getattr(n.par, "pixeldat", None)
    if pd is None:
        raise ValueError("glslTOP %s has no 'pixeldat' parameter" % n.path)
    try:
        pd.val = dat
    except Exception:
        pd.val = dat.path

    # (7) clamp to a low build ceiling (auto-cook TDR governor).
    warnings = []
    applied_res = {}
    for pname, ceil in (("resolutionw", _RES_W_CEIL), ("resolutionh", _RES_H_CEIL),
                        ("npasses", _NPASSES_CEIL)):
        v = _clamp_par(n, pname, ceil)
        if v is not None:
            applied_res[pname] = v
    # prefer 'custom' output resolution so the clamped w/h take effect (best-effort; skip if absent).
    orp = getattr(n.par, "outputresolution", None)
    if orp is not None:
        try:
            server.check_par_allowed(n.opType, "outputresolution")
            orp.val = "custom"
        except Exception:
            pass
    warnings.append("resolution clamped to <=%dx%d and npasses<=%d for a safe build; raise resolution and "
                    "drive the output lane yourself for 4K delivery" % (_RES_W_CEIL, _RES_H_CEIL, _NPASSES_CEIL))

    # (8) audit the accepted delivery.
    _audit({"ts": time.time(), "event": "applied", "op": n.path, "stage": stage,
            "dat": dat.path, "bytes": len(source)})

    return {"applied": True, "dat": dat.path, "op": n.path, "stage": stage,
            "resolution": applied_res, "warnings": warnings}


@server.endpoint("validate_glsl", auth=False)
def validate_glsl_endpoint(params):
    """Dry-run: validate a GLSL fragment source WITHOUT any mutation and WITHOUT requiring consent.
    Params: {source, stage}. Returns {ok: True} or {ok: False, rule: ...}."""
    source = params.get("source")
    stage = params.get("stage")
    if not isinstance(source, str):
        raise ValueError("'source' (the GLSL fragment source string) is required")
    try:
        validate_glsl(source, stage)
        return {"ok": True}
    except GlslValidationError as e:
        return {"ok": False, "rule": getattr(e, "rule", None), "error": str(e)}
