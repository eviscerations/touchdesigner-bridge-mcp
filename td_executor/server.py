"""td_executor/server.py -- the in-TouchDesigner, data-only executor core.

Loaded (as a normal Python package on sys.path) by a thin Web Server DAT callbacks DAT; see
arm.py. Exposes ONLY a fixed registry of typed, validated handlers. There is NO
arbitrary-code path (no exec / eval / generic node driver / raw scripting) -- that is the security
boundary, re-asserted by assert_no_rce_endpoints().

The Web Server DAT callback runs on TD's MAIN THREAD (verified via the substrate spike), so there is
NO main-thread marshalling pump -- handlers touch op()/root directly. This is the key simplification
over the Houdini executor.

TD globals (op, root, app) are NOT auto-injected into imported modules, so the callbacks DAT -- which
DOES have them -- binds them in via bind() each request. Handlers reach the scene through
server.OP(...) / server.ROOT / server.APP.
"""
import os
import sys
import json
import re
import traceback
import secrets
import hashlib

# ---- bound TD globals (set by the callbacks DAT via bind(); see arm.py) ----
OP = None      # the op() shortcut (callable: OP('/path') -> OP or None)
ROOT = None    # the root COMP
APP = None     # the app global

def bind(op=None, root=None, app=None):
    global OP, ROOT, APP
    if op is not None: OP = op
    if root is not None: ROOT = root
    if app is not None: APP = app

# ---- config ----
# The confinement root. NO hardcoded developer path: the fallback default is the process cwd
# (overridable via TDMCP_WORKING_DIR). The LIVE root every file op confines to is arm.json's
# `working_dir`, read fresh per call by working_dir() below -- the SAME single source of truth the
# Rust GUI writes (on Apply) and the gateway reads, so both layers always confine to the directory the
# user chose. WORKING_DIR is only the fallback when arm.json is absent/unreadable/invalid.
WORKING_DIR = os.path.realpath(os.environ.get("TDMCP_WORKING_DIR", os.getcwd()))
_CONFIG_DIR = os.path.realpath(os.path.join(os.path.expanduser("~"), ".touchdesigner-bridge-mcp"))
TOKEN = os.environ.get("TDMCP_TOKEN", "")   # "" = dev/open (loopback bind is the boundary). Set to require auth.
MAX_BODY_BYTES = 1_048_576                   # 1 MB request-body cap
VERSION = "0.1.0"

# ---- integrity: hash-pin the on-disk executor against td_executor/INTEGRITY.json ----------------
# Detect a TAMPERED-ON-DISK executor file before its code is trusted, and fail closed at both
# trust-establishing moments (arm + dev_reload). "Tampered" = on-disk bytes differ from the committed,
# reviewed manifest of expected SHA-256 digests, OR the set of on-disk handler modules differs from the
# pinned set (an ADDED handler that is not in the manifest is itself a violation -- closes the
# "smuggle in a new handler" gap). This is defense-in-depth / tamper-evidence -- the honest
# threat-model ceiling: it detects an outside tampering of the files at rest, not an attacker who
# already owns the install dir (and can rewrite the manifest too). Bypassable ONLY via an explicit,
# loudly-logged TDMCP_INTEGRITY=0 dev flag.
_PKG_DIR = os.path.dirname(os.path.realpath(__file__))          # …/td_executor
_MANIFEST = os.path.join(_PKG_DIR, "INTEGRITY.json")
_REPO_DIR = os.path.dirname(_PKG_DIR)                            # repo root (…/touchdesigner-bridge-mcp)
_ARM_PY = os.path.realpath(os.path.join(_REPO_DIR, "arm.py"))   # the arming bootstrap (trust root)
INTEGRITY_ENFORCE = os.environ.get("TDMCP_INTEGRITY", "1") != "0"  # default ON; "0" = dev bypass


class IntegrityError(RuntimeError):
    pass


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_integrity(pkg_dir=None, manifest_path=None, enforce=None):
    """Fail closed unless every pinned file's on-disk SHA-256 matches the manifest, AND the set of on-disk
    handler files exactly equals the pinned set (no added/removed handler modules). Hashes files directly
    off disk (imports NO handler code -- chicken/egg safe) and compares to the manifest. Raises
    IntegrityError on any mismatch when enforcing. Bypass ONLY via TDMCP_INTEGRITY=0 (logged loudly).

    Args (all optional; production call sites use the defaults) let a self-contained test point at its own
    fixture package + manifest:
      pkg_dir       -- package root to hash under (default this package, …/td_executor).
      manifest_path -- INTEGRITY.json to compare against (default …/td_executor/INTEGRITY.json).
      enforce       -- override the INTEGRITY_ENFORCE default (True/False)."""
    pkg_dir = pkg_dir or _PKG_DIR
    manifest_path = manifest_path or _MANIFEST
    enforce = INTEGRITY_ENFORCE if enforce is None else enforce
    if not enforce:
        sys.stderr.write("[td-bridge] WARNING: integrity check BYPASSED (TDMCP_INTEGRITY=0)\n")
        return {"enforced": False}
    try:
        manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
    except Exception as e:
        raise IntegrityError("integrity manifest missing/unreadable: %s" % e)
    expected = manifest.get("files", {})
    if not expected:
        raise IntegrityError("integrity manifest has no pinned files")
    # 1) set equality over the handlers dir: what's on disk must be exactly what's pinned (an unpinned
    #    handler .py on disk = tamper / smuggled module).
    import glob as _glob
    on_disk = set(expected)
    for p in _glob.glob(os.path.join(pkg_dir, "handlers", "*.py")):
        on_disk.add(os.path.relpath(p, pkg_dir).replace(os.sep, "/"))
    extra = sorted(on_disk - set(expected))     # unpinned handler on disk
    if extra:
        raise IntegrityError("handler set mismatch -- unpinned file(s) on disk: %s" % extra)
    # 2) per-file digest match (constant-time compare per file; a missing pinned file is unreadable = bad).
    bad = []
    for rel, want in sorted(expected.items()):
        ap = os.path.join(pkg_dir, rel.replace("/", os.sep))
        try:
            got = _sha256_file(ap)
        except OSError as e:
            bad.append((rel, "unreadable: %s" % e))
            continue
        if not secrets.compare_digest(got, str(want)):
            bad.append((rel, "digest mismatch"))
    if bad:
        raise IntegrityError("integrity FAILURE (refusing to serve): %s" % bad)
    return {"enforced": True, "files": len(expected)}


# ---- handler registry ----
_REGISTRY = {}   # name -> {"fn": callable, "auth": bool}

def endpoint(name, auth=True):
    def deco(fn):
        _REGISTRY[name] = {"fn": fn, "auth": auth}
        return fn
    return deco

# ---- data-only boundary canary (mirrors the Houdini executor) ----
_BANNED = ("exec", "eval", "wrangle", "hscript", "node_op", "run_code", "os_system", "python_", "script_")

def _name_is_rce_shaped(name):
    hay = "_" + name.lower() + "_"
    return any(("_" + t.strip("_") + "_") in hay for t in _BANNED)

def assert_no_rce_endpoints():
    bad = sorted(n for n in _REGISTRY if _name_is_rce_shaped(n))
    if bad:
        raise RuntimeError("data-only boundary violation: RCE-shaped endpoint(s): " + ", ".join(bad))

# ---- code-carrying operator guard (hybrid boundary: guarded generic engine) ----
# The generic create path must never instantiate an operator whose purpose is running code / loading
# native plugins. Two denial layers:
#  1. NAME MARKERS -- optypes whose name carries script/execute/cplusplus run user code / native plugins.
#  2. EXACT NAMES  -- operators that EVALUATE data/DAT-cell/input TEXT as code but carry no such marker.
# The naive assumption "no tool sets DAT .text" is FALSE -- `write_csv` writes a
# `.dat` (or .txt) file and `fileinDAT` loads it into DAT cells, so DAT/input text is ATTACKER-CONTROLLABLE.
# Any operator that compiles/evaluates that text is an RCE primitive EVEN WHEN check_par_allowed blocks its
# INLINE expression params -- because the expression can instead arrive via a DAT-REFERENCE param (a
# NodePath) or an INPUT CONNECTION. A param denylist alone cannot close that; the robust fix is to forbid
# CREATING the evaluator. (Sibling evaluators are being re-audited; add confirmed ones here.)
_DENY_OPTYPE_MARKERS = ("script", "execute", "cplusplus")
_DENY_OPTYPE_EXACT = frozenset({
    "evaluatedat",   # evaluateDAT: output=evaluate (default) + language incl. python evaluates its input /
                     # `datexpr`-referenced DAT cells as Python. Inline expr/rowexpr/colexpr are denied, but
                     # datexpr + the dat input are not. Confirmed chain: write_csv .dat -> fileinDAT ->
                     # evaluateDAT -> inspect/cook = arbitrary code with the full td API.
})

def check_optype_allowed(optype):
    ot = str(optype).lower()
    if ot in _DENY_OPTYPE_EXACT:
        raise PermissionError(
            "operator type %r is blocked by the data-only boundary (evaluates data/DAT text as code)" % optype)
    for m in _DENY_OPTYPE_MARKERS:
        if m in ot:
            raise PermissionError(
                "operator type %r is blocked by the data-only boundary (code-carrying operator)" % optype)
    return str(optype)

# ---- code-sink PARAMETER guard (closes the tracked boundary gap) ----
# A handful of otherwise-legitimate native TD operators expose a *string parameter whose VALUE
# TouchDesigner itself evaluates as a Python expression*. Setting such a value via `set_par` is a
# data-shaped arbitrary-code path: the value is not an expression on the PARAMETER (`.expr` mode, which
# we already never touch) -- it is a plain `.val`, but the OPERATOR reads that string and eval()s it.
# So "set_par sets values only" is necessary but NOT sufficient for these specific (optype, param)
# pairs, and `check_optype_allowed` does not catch them (their optype names carry no script/execute/
# cplusplus marker). We DENY the exact sinks here, which keeps the operators fully usable for every
# other (data-only) parameter -- unlike blocking the whole optype.
#
# This is THE authoritative REVIEWED list (the single auditable place).
# It is mirrored by:
#   * the build-time generator denylist (drops these params from the SHIPPED surface), and
#   * the gateway's regression fence `code_named_params_are_the_known_reviewed_set` (gateway/src/gateway.rs),
#     which asserts these exact (optype, param) pairs are ABSENT from the generated catalog.
# The Python test test_code_sink_guard.py cross-checks this dict against the gateway fence so the two
# boundary artifacts cannot drift apart silently.
#
# Derivation: a comprehensive audit of all 509 operators / ~17k params (name + label scan cross-checked
# against the offline help). Every entry is a *value-settable* parameter
# whose string VALUE the OPERATOR itself evaluates as code (Python expression, Tscript, script, or GLSL),
# NOT a parameter EXPRESSION (`.expr` mode, which set_par already never touches). Reference params that
# merely POINT at a DAT holding code (callbacks/dragscript/... -> NodePath) are SAFE and NOT listed (no
# tool sets DAT text). Non-code selectors (jsonDAT.filter JSONPath, opfindDAT.parexpressionfilter pattern),
# device output (serialCHOP.script AND its per-block `script<N>callback` fields -- the "Callback"-labelled
# Script-sequence strings that TD SENDS OUT the serial port on a channel change, per docs.derivative.ca/
# Serial_CHOP; they are transmitted data, NOT host code TD evaluates), and toggles (parameterDAT.expression)
# are SAFE and NOT listed.
_DENY_CODE_SINK_PARS = {
    # ---- DAT family: string VALUE evaluated as a Python expression ----
    "evaluateDAT":     ("expr", "rowexpr", "colexpr"),  # per-cell / per-row / per-col Python expressions
    "examineDAT":      ("expression",),                 # Python expression
    "jsonDAT":         ("expression",),                 # Output=Expression: Python expr (me.result[...]) eval'd
    "tableDAT":        ("cellexpr", "fills0expr"),      # Python cell-fill expressions (me.subRow/me.subCol/op(...))
    "insertDAT":       ("replace0expr",),               # Python cell-fill expression
    # ---- CHOP family ----
    "dattoCHOP":       ("rowexpr", "colexpr"),          # Python select conditions
    "pipeoutCHOP":     ("script",),                     # script string executed by the receiving pipein CHOP
    "expressionCHOP":  ("expr0expr",),                  # per-sample expression (me.inputVal ...) evaluated
    "waveCHOP":        ("exprs",),                       # math expression evaluated when wavetype=Expression
    "clipblenderCHOP": ("aend",),                        # "A End Script" -- per-clip-end script field
    # ---- SOP family: "Filter Expression" evaluated for every point/primitive ----
    "groupSOP":        ("filter",),
    "deleteSOP":       ("filter",),
    # ---- COMP family ----
    "replicatorCOMP":  ("tscript",),                    # Tscript run per replicant to customize it
    # ---- MAT family: GLSL expression compiled into the shader ----
    "phongMAT":        ("multitexexpr",),
}

# ---- SEQUENCE-INDEX closure ----
# Three of the sinks above are Sequence BLOCK-0 instances: TouchDesigner names the per-block code
# parameter `<seq>0expr`, `<seq>1expr`, ... one per sequence block. The exact-name list only holds
# the block-0 name because that is all the probe (default block count) ever captured -- so it is the
# only index present in catalog.json, and the generator/fence correctly drop just that. BUT `set_par`'s
# ParMap is OPEN-KEYED (it validates values, not key names): an attacker can raise the block count and
# then set `fills1expr` / `replace1expr` / `expr1expr`, which TD evaluates as code exactly like block 0,
# and an exact-name check would wave it through. The executor's check_par_allowed is the universal choke
# point (every operator tool lowers through set_par -> check_par_allowed), so we close the gap HERE by
# denying EVERY index per-optype via anchored patterns. The block count param itself (e.g. tableDAT
# `fills`) stays allowed -- harmless once every `<seq>\d+expr` slot is denied regardless of count.
_DENY_CODE_SINK_PATTERNS = {
    "tableDAT":       (re.compile(r"^fills\d+expr$"),),    # generalizes fills0expr -> fillsNexpr
    "insertDAT":      (re.compile(r"^replace\d+expr$"),),  # generalizes replace0expr -> replaceNexpr
    "expressionCHOP": (re.compile(r"^expr\d+expr$"),),     # generalizes expr0expr -> exprNexpr
}

# ---- UNIVERSAL inline code-sink patterns (ANY optype) -- Layer 1 of the un-catalogued-op closure ----
# The per-optype tables above only protect the CATALOGUED ops. `create_op` can also instantiate a niche/Pro
# op that is NOT in the catalog (the probe_optype escape hatch stays fully usable -- we do NOT restrict what
# can be created), and for such an op check_par_allowed has no allowlist and previously reverted to deny-only,
# leaving an inline code-sink param reachable. These param-NAME shapes denote an inline expression/script
# field on ANY operator -- TouchDesigner's parameter naming is consistent (`<sel>expr` / `exprNexpr` /
# `fillsNexpr` / `replaceNexpr` / `exprs` / `tscript` / `multitexexpr` / `aend` are ALWAYS code) -- so they
# are denied UNIVERSALLY. VERIFIED against all catalogued ops / 17,370 params: ZERO collisions with a benign
# parameter. The two shapes that DID collide -- bare `expr` (expressionCHOP Sequence header) and `expression`
# (parameterDAT Toggle) -- are deliberately EXCLUDED here and remain per-optype in _DENY_CODE_SINK_PARS.
_DENY_CODE_SINK_PATTERNS_UNIVERSAL = (
    re.compile(r"^rowexpr$"), re.compile(r"^colexpr$"), re.compile(r"^cellexpr$"),
    re.compile(r"^fills\d+expr$"), re.compile(r"^replace\d+expr$"), re.compile(r"^expr\d+expr$"),
    re.compile(r"^exprs$"), re.compile(r"^tscript$"), re.compile(r"^multitexexpr$"), re.compile(r"^aend$"),
)

# ---- UN-CATALOGUED code-token heuristic (ANY optype) -- Layer 2 of the closure ----
# Layer 1 catches the KNOWN code-sink name shapes. For an UN-CATALOGUED op there is no reviewed allowlist,
# so a *novel* inline-code param name would still slip. check_par_allowed already requires the param to be a
# REAL live parameter (set_par does getattr(op.par, name)), so an attacker cannot invent a name -- the only
# residual is a real, code-evaluating param whose name Layer 1 does not enumerate. TouchDesigner names every
# such field with a code-indicator token as the trailing component of the name (`cellexpr`, `rowexpr`,
# `dragscript`, `script0callback`, `expr0expr`, ...). So on un-catalogued ops ONLY (the 509 catalogued ops
# keep their exact fail-closed allowlist untouched) we additionally refuse any param whose name ENDS WITH a
# code-indicator token (optionally followed by a block index). SUFFIX-anchoring is deliberate: it catches the
# real code-field naming convention while NOT false-positiving on ordinary words that merely contain a token
# in the middle (`description`, `descriptor`, `resolution`). This keeps the op fully creatable and
# data-configurable while making the code boundary fail-closed for the whole creatable surface, not just the
# catalogued slice. (Catalogued ops never reach this branch, so no recipe or reviewed data param is affected.)
# The complete zero-heuristic closure -- probing every creatable op into the catalog so the exact allowlist is
# universal -- is the follow-up for the next catalog/rebuild cycle.
_UNCATALOGUED_CODE_TOKEN_RE = re.compile(r"(?:expr|script|callback|tscript|pyexpr)\d*$", re.I)

# ---- UNIVERSAL code-POINTER parameter deny ----
# The LARGEST RCE class is not inline-expression params -- it is REFERENCE params (NodePaths) that point at
# a DAT whose text TouchDesigner EXECUTES as host Python on an event/cook. DAT text is attacker-controllable
# (write_csv writes a .py/.txt, fileinDAT loads it), so setting one of these to an attacker DAT is direct
# host RCE (e.g. set_par(timerCHOP, callbacks=<evil DAT>) -> timer fires -> the DAT's Python runs). These
# param NAMES denote a code-callback/expression DAT on EVERY op that exposes them (callbacks alone is on 46+
# ops), so they are denied UNIVERSALLY (any optype). This is a PARAM deny -- the operators stay fully usable
# for their DATA params; we do NOT deny the op.
# STOPGAP: the strategic direction is a VALIDATED code lane that safely re-exposes
# callbacks/scripts/expressions -- validation + consent will
# REPLACE these blanket denies, so capability is restored safely, not cut.
_DENY_PARAM_NAMES_UNIVERSAL = frozenset({
    "callbacks",          # a DAT of Python callback functions TD calls on events/cook (timerCHOP, moviefilein,
                          # webserverDAT, replicatorCOMP, ... 46+ ops)
    "dragscript", "dropscript", "dropdestscript", "droptypescript",  # panel drag/drop callback DATs
    "dragdropcallbacks",  # panel COMP drag/drop callback DAT
    "datexpr",            # evaluateDAT expression-table DAT ref (op also optype-denied; defense-in-depth)
})


# ---- ALLOWLIST: known DATA parameters per optype --------------------------------------------------
# The denies above are necessary but a denylist over a closed-source ~17k-param surface cannot be proven
# complete. This adds the FAIL-CLOSED half: a DRIVER-supplied (optype, param) is accepted only when it is a
# KNOWN parameter -- present in the shipped, reviewed catalog (reference/catalog.json, the same probe the
# typed tool surface is generated from). A parameter ABSENT from the catalog (a TD build newer than the
# probe, an un-probed value-eval param) is now REFUSED instead of waved through -- exactly the audit's ask
# ("fail closed on any TD-version-new parameter instead of open").
#
# HONEST SCOPE (documented, not oversold): the catalog contains ALL params incl. the code sinks, so
# membership alone cannot distinguish a data param from a sink -- the DENY half above (run FIRST) is what
# separates them. So the allowlist's security delta is precisely: unknown/newer/un-probed params fail
# closed. It is NOT a claim of sink-completeness over the CURRENT catalog (that remains the denylist's job).
#
# SEQUENCE BLOCKS: TD names per-block params `<base>0<suffix>`, `<base>1<suffix>`, ... but only block 0 was
# probed into the catalog. An unknown INDEXED name is generalized (first digit-run -> 0) and allowed ONLY if
# that block-0 form is a catalog param AND is not itself a denied sink -- so a legit block-N DATA param
# (constantCHOP.const1name) is allowed while a block-N SINK (tableDAT.fills1expr) stays denied.
# CUSTOM params (live Par.isCustom): the user's own parameters; their `.val` is data (never auto-evaluated
# as host code), so they are allowed when the live Par is supplied.
# UNCATALOGUED OPTYPES: for an optype not in the 509-op catalog we have no reviewed allowlist, but the op
# stays fully creatable (probe_optype escape hatch) and its data params settable. The code boundary is now
# fail-closed for this surface too: Layer 1 (_DENY_CODE_SINK_PATTERNS_UNIVERSAL) denies the known code-sink
# NAME shapes on ANY optype, and Layer 2 (_UNCATALOGUED_CODE_TOKEN_RE, applied ONLY when known is None)
# refuses any remaining param whose name carries a code-indicator token, while live CUSTOM params (data) pass.
# The complete zero-heuristic closure -- probing every creatable op into the catalog so the allowlist is
# universal -- is the follow-up for the next catalog/rebuild cycle.
# LOAD FAILURE: if the catalog can't be read at all, the allowlist can't be built and the boundary reverts
# to deny-only globally, with a loud warning -- bricking every write on a missing asset would be worse. No
# tool can corrupt the catalog (write_csv/save_top enforce extension whitelists; .json is neither), so this
# is a genuine disk failure, not an attack surface.
_CATALOG_PATH = os.path.join(_REPO_DIR, "reference", "catalog.json")
_ALLOW_PARAMS = None          # {optype: frozenset(param names)} or None => deny-only fallback
_SEQ_INDEX_RE = re.compile(r"\d+")


def _load_allow_params(path=None):
    """Build {optype: frozenset(param names)} from the reviewed catalog (immutable repo path, NOT WORKING_DIR
    which tools/tests repoint). Sets _ALLOW_PARAMS to None on any read failure => deny-only fallback."""
    global _ALLOW_PARAMS
    path = path or _CATALOG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            cat = json.load(f)
        idx = {}
        for ot, info in cat.items():
            try:
                idx[str(ot)] = frozenset(str(p["name"]) for p in info.get("params", []))
            except Exception:
                idx[str(ot)] = frozenset()
        _ALLOW_PARAMS = idx
    except Exception as e:
        sys.stderr.write("[td-bridge] WARNING: parameter allowlist unavailable (%s); "
                         "falling back to denylist-only param guard\n" % e)
        _ALLOW_PARAMS = None
    return _ALLOW_PARAMS


_load_allow_params()


def _is_denied_param(optype, parname):
    """Pure DENY predicate (universal code-pointers + exact inline sinks + sequence-index sink patterns).
    Reused by check_par_allowed and by the sequence-generalization safety re-check. Returns bool."""
    ot, pn = str(optype), str(parname)
    if pn in _DENY_PARAM_NAMES_UNIVERSAL:
        return True
    banned = _DENY_CODE_SINK_PARS.get(ot)
    if banned and pn in banned:
        return True
    for pat in _DENY_CODE_SINK_PATTERNS.get(ot, ()):
        if pat.match(pn):
            return True
    for pat in _DENY_CODE_SINK_PATTERNS_UNIVERSAL:   # any-optype known code-sink shapes (S2 Layer 1)
        if pat.match(pn):
            return True
    return False


# ---- shader-source DAT-ref delivery guard (driver path only) ----------------------------------------
# A glslTOP shader-source DAT-ref param that carries a VALIDATED delivery lane (set_glsl) must never be wired
# on the raw DRIVER path (set_par / set_par_many / set_expr). Wiring it to an arbitrary DAT bypasses
# validate_glsl + the resolution clamp: write_csv writes attacker GLSL to a file, fileinDAT loads it as DAT
# text, and set_par(glslTOP, pixeldat=<that DAT>) compiles an UNVALIDATED shader (GPU-DoS / driver TDR, not
# host RCE). A name-prefix "owned DAT" check is SPOOFABLE (a fileinDAT can be named `__mcp_pixel`), so the gate
# is absolute: `pixeldat` is delivered ONLY through set_glsl, which wires its own validated child DAT
# INTERNALLY (never through these driver handlers) -- so refusing the driver path costs zero legitimate
# capability. The other shader refs (vertexdat/predat/gdat/computedat/glslMAT/POP) have no validated lane yet;
# gating them would amputate, so they stay usable and are a documented GPU-DoS residual.
# Extending the validated lane to those stages is a future capability-add.
_DRIVER_REFUSED_SHADER_REFS = frozenset({"pixeldat"})


def check_driver_shader_ref(parname):
    """Refuse a shader-source DAT ref that has a validated lane, on the driver path only. Raises
    PermissionError; else returns str(parname). NOT called by set_glsl (its owned wiring is trusted)."""
    if str(parname) in _DRIVER_REFUSED_SHADER_REFS:
        raise PermissionError(
            "parameter %r is delivered only via set_glsl (which validates the shader and owns the DAT); a raw "
            "driver wire bypasses validate_glsl and is refused by the data-only boundary" % parname)
    return str(parname)


# ---- bridge self-protection: the AI must never mutate the bridge it runs through --------------------
# /mcp_bridge (the Web Server DAT + its callbacks + the GUI CONSENT TOGGLES) is the bridge's own
# infrastructure. A mutation there could disable auth, change the loopback bind/port, DELETE the bridge, or --
# most importantly -- FLIP the allow_expr/allow_glsl consent toggles to SELF-ENABLE a code lane. arm.json (the
# consent source of truth) already sits in the config dir that file-writing tools can't reach; this closes the
# PARAMETER-write path to the GUI toggles that mirror it. Every MUTATING handler calls this; read-only tools
# (inspect / read_network / find_errors) may still observe the bridge.
_BRIDGE_COMP_PATH = "/mcp_bridge"


def assert_writable(op):
    """Raise PermissionError if `op` is the bridge COMP or anything inside it (mutation off-limits); else
    return `op`. Accepts an OP object or a path string."""
    try:
        p = getattr(op, "path", None) or str(op)
    except Exception:
        p = str(op)
    p = str(p)
    if p == _BRIDGE_COMP_PATH or p.startswith(_BRIDGE_COMP_PATH + "/"):
        raise PermissionError(
            "refused: %s is bridge infrastructure -- the MCP may not mutate the bridge it runs through "
            "(consent toggles, auth, and transport are protected)" % p)
    return op


def check_par_allowed(optype, parname, par=None):
    """FAIL-CLOSED data-only gate for every DRIVER-supplied parameter write (set_par / set_par_many /
    set_expr / bind_chop). Two halves:
      DENY  -- refuse universal code-POINTER params (callbacks/*script/datexpr), the exact reviewed INLINE
               code sinks, and every Sequence-block index of the indexed sink families (F1).
      ALLOW -- accept ONLY a KNOWN parameter of a CATALOGUED optype: (a) an exact catalog member, (b) a
               Sequence-block index whose block-0 form is a catalog param and not a sink, or (c) a live
               CUSTOM parameter (Par.isCustom). Anything else on a catalogued op fails CLOSED.
    Reverts to deny-only when the catalog couldn't load OR the optype isn't catalogued (no allowlist to
    apply => no capability regression). Internal fixed-name writes (control._set_lit etc.) do NOT route
    through here, so this gates exactly the open-keyed, driver-controlled surface. `par` (the live Par) is
    optional; when supplied it enables the custom-parameter allowance. Raises PermissionError; else str(pn)."""
    ot, pn = str(optype), str(parname)
    # 1) DENY half (unchanged semantics + messages).
    if pn in _DENY_PARAM_NAMES_UNIVERSAL:
        raise PermissionError(
            "parameter %r is blocked by the data-only boundary on every operator "
            "(it references a DAT whose text TouchDesigner executes as host code)" % parname)
    banned = _DENY_CODE_SINK_PARS.get(ot)
    denied = bool(banned) and pn in banned
    if not denied:
        for pat in _DENY_CODE_SINK_PATTERNS.get(ot, ()):
            if pat.match(pn):
                denied = True
                break
    if not denied:
        for pat in _DENY_CODE_SINK_PATTERNS_UNIVERSAL:   # S2 Layer 1: known code-sink shapes on ANY optype
            if pat.match(pn):
                denied = True
                break
    if denied:
        raise PermissionError(
            "parameter %r on operator type %r is blocked by the data-only boundary "
            "(TouchDesigner evaluates this string parameter's value as a Python expression)"
            % (parname, optype))
    # 2) ALLOW half (fail-closed). Deny-only fallback when the catalog is unavailable globally.
    if _ALLOW_PARAMS is None:
        return pn
    known = _ALLOW_PARAMS.get(ot)
    if known is None:
        # UN-CATALOGUED optype (S2 Layer 2): no reviewed allowlist exists. The op stays fully creatable and
        # its data params settable, but we cannot prove an unknown param is data -- so we fail closed on any
        # param whose name carries a code-indicator token (Layer 1 above already denied the known code-sink
        # shapes universally). A live CUSTOM param is the user's own -- its .val is data, never auto-evaluated
        # as host code -- so it is allowed. Catalogued ops never reach this branch, so the 509-op fail-closed
        # allowlist and every recipe are unaffected.
        if par is not None:
            try:
                if bool(getattr(par, "isCustom", False)):
                    return pn
            except Exception:
                pass
        if _UNCATALOGUED_CODE_TOKEN_RE.search(pn):
            raise PermissionError(
                "parameter %r on un-catalogued operator type %r is refused fail-closed by the data-only "
                "boundary (its name carries a code-indicator token; only reviewed catalogued operators expose "
                "such params for setting). Data parameters on this operator are unaffected." % (parname, optype))
        return pn
    if pn in known:
        return pn
    # Sequence-block index: generalize the first digit-run to 0; allow only if that block-0 form is a
    # catalog param AND is not itself a denied sink (a block-N SINK can never slip through here).
    if any(c.isdigit() for c in pn):
        g = _SEQ_INDEX_RE.sub("0", pn, count=1)
        if g in known and not _is_denied_param(ot, g):
            return pn
    # Custom parameter (the user's own; its .val is data, never auto-evaluated as host code).
    if par is not None:
        try:
            if bool(getattr(par, "isCustom", False)):
                return pn
        except Exception:
            pass
    raise PermissionError(
        "parameter %r is not a known data parameter of operator type %r "
        "(refused by the data-only allowlist; unknown/unshipped params fail closed)" % (parname, optype))

# ---- helpers ----
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def _jsonable(o):
    try:
        json.dumps(o)
        return o
    except TypeError:
        return str(o)

# Live confinement root: the single source of truth is ~/.touchdesigner-bridge-mcp/arm.json's
# `working_dir` (the Rust GUI writes it on Apply). mtime-cached so the hot path (every tool call) does
# not re-read + re-canonicalize; falls back to the WORKING_DIR global if arm.json is absent/unreadable/
# invalid so confinement never silently opens up. This mirrors the gateway's resolve_working_dir(), so
# the executor and gateway confine to the SAME directory the user chose -- no restart, no re-arm, and NO
# hardcoded path. A non-None _WD_OVERRIDE wins (test / embedding pin).
_WD_CACHE = None      # (mtime, resolved_dir) fast path for the arm.json read
_WD_OVERRIDE = None   # explicit pin; when set, wins over arm.json (tests set this to a temp dir)

def working_dir():
    global _WD_CACHE
    if _WD_OVERRIDE is not None:
        return _WD_OVERRIDE
    cfg = os.path.join(_CONFIG_DIR, "arm.json")
    try:
        mtime = os.path.getmtime(cfg)
    except OSError:
        return WORKING_DIR
    if _WD_CACHE is not None and _WD_CACHE[0] == mtime:
        return _WD_CACHE[1]
    wd = WORKING_DIR
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        cand = os.path.realpath(str(data["working_dir"]))
        if os.path.isdir(cand):
            wd = cand
    except Exception:   # any parse/type/OS error -> keep the safe fallback
        wd = WORKING_DIR
    _WD_CACHE = (mtime, wd)
    return wd

def confined_path(path):
    """Resolve `path` and require it under the live working dir (working_dir()); the bridge config dir
    and the executor trust root are always off-limits."""
    p = str(path)
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\"):
        p = p[4:]
    rp = os.path.realpath(p)
    cfg = _CONFIG_DIR + os.sep
    if rp == _CONFIG_DIR or rp.startswith(cfg):
        raise PermissionError("path inside bridge config dir (off-limits to tools): %s" % path)
    # TRUST ROOT is off-limits even INSIDE the working dir: the executor package (its
    # .py + INTEGRITY.json) and the arm.py bootstrap. Without this a render/CSV write could land on a trusted
    # file (the default working dir IS the repo root), corrupting the bridge on the next arm/dev_reload.
    pkg = _PKG_DIR + os.sep
    if rp == _PKG_DIR or rp.startswith(pkg) or rp == _ARM_PY:
        raise PermissionError("path inside the executor trust root (off-limits to tools): %s" % path)
    base = working_dir()
    root = base + os.sep
    if rp != base and not rp.startswith(root):
        raise PermissionError("path outside working directory: %s" % path)
    return rp

def resolve_op(path):
    if OP is None:
        raise RuntimeError("executor not bound to TD globals (bind() not called)")
    n = OP(str(path))
    if n is None:
        raise ValueError("no such operator: %s" % path)
    return n

def _app_build():
    try:
        return APP.build if APP is not None else "?"
    except Exception:
        return "?"

def _hdr(request, name):
    want = name.lower()
    for k, v in request.items():
        if str(k).lower() == want:
            return v
    return None

def _authed(request):
    if not TOKEN:
        return True  # dev/open: the loopback bind (localaddress 127.0.0.1) is the boundary
    supplied = _hdr(request, "X-TDMCP-Token") or ""
    return secrets.compare_digest(str(supplied), str(TOKEN))

def _host_is_loopback(hostval):
    """True if a Host/Origin header denotes loopback (127.0.0.1 / ::1 / localhost). MISSING -> True: the
    Rust gateway sends Host=127.0.0.1:<port> and NO Origin, so fail-open keeps the trusted client working;
    a browser's cross-origin POST (incl. DNS-rebind, where Host is the attacker's domain resolving to
    loopback) carries a FOREIGN Origin/Host and is refused. Fail-open on unparseable."""
    if not hostval:
        return True
    s = str(hostval).strip().lower()
    if "://" in s:                       # Origin is scheme://host[:port]
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]               # drop any path
    host = s
    if host.startswith("["):             # [::1]:port  (bracketed IPv6)
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:           # host:port
        host = host.split(":", 1)[0]
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")

# ---- dispatch (Web Server DAT onHTTPRequest entry) ----
def handle(web_dat, request, response):
    """Runs on TD's main thread. Parse -> auth -> registry lookup -> call -> JSON envelope."""
    def _resp(payload, code=200):
        response["statusCode"] = code
        response["statusReason"] = "OK" if code == 200 else "Error"
        response["content-type"] = "application/json"
        response["data"] = json.dumps(payload)
        return response

    # Loopback-only: refuse any request carrying a non-loopback Origin/Host (a cross-origin browser page /
    # DNS-rebind target). Applies to EVERY endpoint incl. the auth=False health/validate ones.
    if not _host_is_loopback(_hdr(request, "Origin")) or not _host_is_loopback(_hdr(request, "Host")):
        return _resp({"ok": False, "error": "cross-origin request refused (loopback only)"}, 403)

    uri = str(request.get("uri", ""))
    name = uri.strip("/")
    if name.startswith("tool/"):
        name = name[len("tool/"):]

    if name in ("", "health"):
        # Unauthenticated health leaks no endpoint inventory (host/software recon); the full
        # endpoint list is returned only to an authenticated caller.
        h = {"ok": True, "service": "td-bridge-mcp", "version": VERSION, "td": _app_build()}
        if _authed(request):
            h["endpoints"] = sorted(_REGISTRY)
        return _resp(h)

    spec = _REGISTRY.get(name)
    if spec is None:
        return _resp({"ok": False, "error": "unknown endpoint: %s" % name}, 404)
    if spec["auth"] and not _authed(request):
        return _resp({"ok": False, "error": "unauthorized"}, 403)

    body = request.get("data") or ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if len(body) > MAX_BODY_BYTES:
        return _resp({"ok": False, "error": "request too large"}, 413)
    try:
        params = json.loads(body) if body.strip() else {}
    except ValueError:
        return _resp({"ok": False, "error": "invalid json body"}, 422)
    if not isinstance(params, dict):
        return _resp({"ok": False, "error": "body must be a JSON object"}, 422)

    try:
        result = spec["fn"](params)
        return _resp({"ok": True, "result": _jsonable(result)})
    except (PermissionError, ValueError, KeyError) as e:
        return _resp({"ok": False, "error": str(e)}, 400)
    except Exception as e:  # don't leak tracebacks (host recon) -- log locally to textport
        sys.stderr.write("td-bridge handler error in %r:\n%s\n" % (name, traceback.format_exc()))
        return _resp({"ok": False, "error": str(e)}, 500)


# ---- DEV: hot-reload on-disk handler modules without re-running arm.py ----
def _dev_reload(params):
    """Hot-reload on-disk handler edits, ROBUST against stale bytecode.

    `importlib.reload` alone can return a cached code object when the timestamp `.pyc` merely LOOKS
    current (finder/loader caches seeded at arm time), so an edit to an EXISTING handler silently kept
    running old code. This version forces a from-source recompile immune to cache mode/location:
      (1) invalidate the import caches,
      (2) delete each target module's ACTUAL cached bytecode via module.__cached__ (the real .pyc
          wherever it lives, incl. any sys.pycache_prefix redirect),
      (3) drop td_executor.handlers[.*] from sys.modules and RE-IMPORT the package from source -- a
          guaranteed compile that re-runs every @endpoint decorator and rebuilds _REGISTRY.
    Only the handlers package is purged, so `server`/`governor` and the bound TD globals / _REGISTRY
    itself survive. NOT a request-code path -- it only reimports the developer's on-disk files, never
    anything from the request. This `server` module is not reloaded (edits to it still need one re-arm)."""
    verify_integrity()   # refuse to reload tampered / unpinned files BEFORE any module body re-runs
    import importlib
    import os

    targets = sorted(
        n for n in list(sys.modules)
        if (n == "td_executor.handlers" or n.startswith("td_executor.handlers."))
        and sys.modules.get(n) is not None
    )
    # (2) remove each module's REAL cached bytecode so a stale .pyc cannot be reused.
    purged_pyc = []
    for n in targets:
        cached = getattr(sys.modules.get(n), "__cached__", None)
        if cached and os.path.isfile(cached):
            try:
                os.remove(cached); purged_pyc.append(cached)
            except OSError:
                pass
    # (1) drop stale finder/loader caches seeded when the session was armed.
    importlib.invalidate_caches()
    # (3) purge submodules then the package, so the next import is a fresh from-source compile.
    for n in sorted(targets, reverse=True):
        try:
            del sys.modules[n]
        except KeyError:
            pass
    reloaded, failed = [], None
    try:
        importlib.import_module("td_executor.handlers")
        reloaded = sorted(n for n in sys.modules
                          if n == "td_executor.handlers" or n.startswith("td_executor.handlers."))
    except Exception as e:
        failed = {"module": "td_executor.handlers", "error": str(e)}
    assert_no_rce_endpoints()  # re-assert the data-only boundary after reload
    out = {"reloaded": reloaded, "endpoints": sorted(_REGISTRY), "purged_pyc": purged_pyc}
    if failed:
        out["failed"] = failed
    return out

_REGISTRY["dev_reload"] = {"fn": _dev_reload, "auth": True}
