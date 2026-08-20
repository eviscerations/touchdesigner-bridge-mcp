"""td_executor/handlers/expr.py -- the VALIDATED parameter-EXPRESSION lane (the second code lane).

Mirrors the shipped GLSL lane (`td_executor/handlers/glsl.py`) part-for-part: executor-authoritative,
validate-BEFORE-write, default-off consent read fresh from arm.json, audit log, one-validated-path
invariant, fail-closed at every step. `set_expr` is THE single place in the entire MCP that writes a
parameter's `.expr` (expression mode) -- and it only ever does so AFTER (a) fresh consent, (b) the
authoritative `validate_expr` allowlist pass, and (c) `server.check_par_allowed` (so an expression can
NEVER be written onto a denied code-pointer / code-sink parameter). Everywhere else `.expr` writes stay
forbidden and tripwired (EXPR_WRITES in the offline mock; the per-handler static source scans).

DIVERGENCE FROM THE GLSL LANE: the write target is a `Par`, not a
DAT, and the worst-case validator gap is HOST RCE, not a recoverable GPU reset. So this lane ships
EXPERIMENTAL / default-off and stays off until the live-API audit of the validator's positive
allowlists is signed off. Enabling `allow_expr` is the OWNER's separate decision; nothing here enables it.
"""
import os
import json
import time

from td_executor import server
from td_executor.expr_validator import validate_expr, ExprValidationError


def _config_dir():
    return os.path.join(os.path.expanduser("~"), ".touchdesigner-bridge-mcp")


def _read_allow_expr():
    """Read `allow_expr` from ~/.touchdesigner-bridge-mcp/arm.json, default False if file/key absent.
    Lives HERE (not server.py) to keep the lanes decoupled; read FRESH on every call (mirror of
    glsl.py's _read_allow_glsl). Orthogonal to `allow_glsl` -- consenting to one does not consent the
    other. Any read/parse error fails closed to False (lane inert)."""
    try:
        with open(os.path.join(_config_dir(), "arm.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return bool(data.get("allow_expr", False))
    except Exception:
        return False


def _audit(record):
    """Best-effort append one line to expr_audit.log in the working dir. Never blocks / raises."""
    try:
        path = os.path.join(server.working_dir(), "expr_audit.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


@server.endpoint("set_expr")
def set_expr(params):
    """Deliver a VALIDATED parameter expression onto a target parameter -- the ONE sanctioned `.expr`
    write in the entire MCP. Params: {op, par, source}. FLOW (strict order): consent -> validate BEFORE
    any mutation -> resolve op + check_par_allowed (defense in depth) + assert the param exists -> set
    expression mode + write the validated `.expr` -> audit. Fail-closed at every step; on refusal NOTHING
    is written. Returns {applied, op, par, mode}."""
    op_path = params.get("op")
    par_name = params.get("par")
    source = params.get("source")

    # (1) CONSENT -- default-off, read fresh from arm.json. Refuse before touching anything.
    if not _read_allow_expr():
        _audit({"ts": time.time(), "event": "refused_consent", "op": op_path, "par": par_name})
        raise PermissionError(
            "expr lane not consented (set allow_expr:true in "
            "~/.touchdesigner-bridge-mcp/arm.json and re-arm)")

    if not isinstance(source, str):
        raise ValueError("'source' (the parameter-expression string) is required")
    if not isinstance(par_name, str) or not par_name:
        raise ValueError("'par' (the target parameter name) is required")

    # (2) VALIDATE BEFORE ANY MUTATION -- executor is authoritative. On failure: refuse, nothing written.
    try:
        validate_expr(source, "eval")
    except ExprValidationError as e:
        _audit({"ts": time.time(), "event": "rejected", "op": op_path, "par": par_name,
                "rule": getattr(e, "rule", None)})
        raise ValueError("expr validation failed [%s]: %s" % (getattr(e, "rule", "?"), e))

    # (3) resolve op; DEFENSE IN DEPTH -- check_par_allowed refuses code-pointer (callbacks/*script) and
    #     code-sink params UNIVERSALLY, so an expression can NEVER be written onto one of them regardless of
    #     how "safe" its arithmetic is (the param's VALUE is interpreted as code by TD). Then assert the
    #     target parameter exists.
    n = server.assert_writable(server.resolve_op(op_path))
    p = getattr(n.par, par_name, None)
    if p is None:
        raise ValueError("op %s has no parameter %r" % (n.path, par_name))
    server.check_par_allowed(n.opType, par_name, p)
    server.check_driver_shader_ref(par_name)  # a validated shader ref (pixeldat) is delivered only via set_glsl

    # (4) THE SINGLE Par.expr WRITE IN THE ENTIRE MCP. Isolated + validated + consent-gated + audited.
    #     `source` has passed validate_expr above; it is a verbatim, single-line, AST-allowlisted CPython
    #     expression. Clear any prior bind, switch to EXPRESSION mode, then write the expression.
    #     The `.mode.__class__.EXPRESSION` idiom mirrors control.py's CONSTANT idiom -- it avoids importing
    #     ParMode and is defensive against the offline mock (whose mode is a plain str; setting .expr there
    #     puts it in expression mode). In real TD setting .expr also activates expression mode.
    try:
        p.bindExpr = ''
    except Exception:
        pass
    try:
        p.mode = p.mode.__class__.EXPRESSION
    except Exception:
        pass
    p.expr = source

    # (5) audit the accepted delivery (verbatim expression + op/par/timestamp).
    _audit({"ts": time.time(), "event": "applied", "op": n.path, "par": par_name, "expr": source})
    return {"applied": True, "op": n.path, "par": par_name, "mode": "expression"}


@server.endpoint("validate_expr", auth=False)
def validate_expr_endpoint(params):
    """Dry-run: validate a parameter expression WITHOUT any mutation and WITHOUT requiring consent.
    Params: {source}. Returns {ok: True} or {ok: False, rule: ..., error: ...}. Mirrors
    validate_glsl_endpoint (read-only, needs no consent flag, creates/writes nothing)."""
    source = params.get("source")
    if not isinstance(source, str):
        raise ValueError("'source' (the parameter-expression string) is required")
    try:
        validate_expr(source, "eval")
        return {"ok": True}
    except ExprValidationError as e:
        return {"ok": False, "rule": getattr(e, "rule", None), "error": str(e)}
