"""td_executor/handlers/animation.py -- data-only animation via CHOP-EXPORT param binding.

THE animation mechanism for "4K animated content" that stays inside the data-only boundary. The boundary
bans parameter EXPRESSIONS (a TD expression string can run Python = an RCE path), so animation must come
from a DATA BINDING, not a code string.

MECHANISM (autoname export via a dedicated renameCHOP, ESTABLISHED at cook time):
  Two things must both be true, and the SECOND was the long-missing piece:
    1. NAME: the export channel must be a literal "<path-relative-to-export-root>:<parameter>". A DEDICATED
       renameCHOP produces it (renamefrom='*' / renameto='<name>:<parameter>'); the Null-CHOP Common-page
       rename is a no-op through the Python API, and a rename PATTERN is not a literal setter, so the name
       must come from a renameCHOP whose single input carries exactly one channel.
    2. ESTABLISHMENT: the Export node flag ALONE does NOT drive the target. TD derives the
       channel->parameter mapping only as a COOK-TIME side effect, and the offline docs require the
       exporter's Viewer to be ACTIVE. So after setting exportmethod='autoname' + autoexportroot + the
       Export flag, this handler turns the renameCHOP's Viewer Active flag ON and FORCE-COOKS it -- which is
       what makes the target auto-switch to export evaluation and actually be driven. Earlier attempts set
       the flag but never cooked/activated the viewer, so the target stayed Constant.
  There is NO Python API that directly CREATES an export (the target's export-source accessors are
  READ-ONLY); the cook-time establishment above is the only code-free path. The handler READS the target's
  resulting state and reports `driven` honestly. An LFO/noise/pattern CHOP -> a Null -> this renameCHOP ->
  bound param = clean motion; then wire a moviefileoutTOP (WIRE-ONLY) for the 4K sequence.

This handler sets ONLY literal VALUES on the renameCHOP's export/rename params + the Export node flag. It
NEVER writes a parameter expression, bind-expression, or evaluation-mode on the target -- the same
values-only discipline as set_par, and a static source scan in the tests enforces it. The TARGET parameter
is never written at all.
"""
from td_executor import server


def _same_parent(a, b):
    """True iff operators `a` and `b` share the same parent COMP (the autoname export case)."""
    try:
        pa = a.parent()
    except Exception:
        pa = None
    try:
        pb = b.parent()
    except Exception:
        pb = None
    if pa is None or pb is None:
        return False
    if pa is pb:
        return True
    return getattr(pa, "path", None) == getattr(pb, "path", object())


def _set_val(op, name, value):
    """Set a required literal parameter VALUE on `op`, fail-loud if the parameter is absent (data-only --
    a literal value, never a parameter expression)."""
    p = getattr(op.par, name, None)
    if p is None:
        raise ValueError("%s lacks the %r export parameter (not a renameCHOP?)" % (op.path, name))
    p.val = value
    return p


def _bind_op_ref(par, op_obj, path_str):
    """Assign an OP-REFERENCE parameter by the OP OBJECT first, then a path string, and VERIFY the
    reference resolved -- the fail-loud pattern proven in io.py's opviewer binding (a bare `.val="<path>"`
    string does not reliably bind an OP-reference param). Returns True on a resolved binding."""
    for candidate in (op_obj, path_str):
        if candidate is None:
            continue
        try:
            par.val = candidate
        except Exception:
            continue
        try:
            resolved = par.eval()
        except Exception:
            resolved = None
        if getattr(resolved, "path", None) == path_str or resolved in (op_obj, path_str):
            return True
    return False


@server.endpoint("bind_chop")
def bind_chop(params):
    """Bind an operator PARAMETER to a CHOP CHANNEL via TouchDesigner's code-free autoname export -- the
    data-only animation mechanism. The channel's live values drive the parameter with NO
    expression/code (a DATA binding, not an expression string).

      chop     : path of the CHOP to export FROM (its channel drives the parameter). A Null CHOP appended
                 to the end of your CHOP chain is the recommended source (stable export placeholder).
      op       : path of the operator whose parameter to drive.
      par      : the parameter name on `op` to bind (e.g. 'opacity', 'tx', 'level').
      channel  : which source channel(s) to rename onto the export name (a rename PATTERN; default '*'
                 matches the CHOP's channel -- the proven single-channel case). For a multi-channel source,
                 pass the exact channel name so only that one routes to the parameter.

    Mechanism (a dedicated renameCHOP carries the binding as literal data + a NODE FLAG only; the target
    parameter is never written at all):
      1. create-or-reuse a renameCHOP named '<chop>_export' in the SOURCE chop's parent, fed by the CHOP,
      2. compute the autoname channel name '<path-relative-to-export-root>:<par>' + the export root,
      3. on the renameCHOP set literal values renamefrom / renameto / exportmethod='autoname' and bind
         autoexportroot (an OP-reference param), then turn ON the renameCHOP's Export flag.

    Returns the established binding. Sets NO parameter expression, bind-expression, or evaluation-mode on
    the target -- the whole binding lives on the renameCHOP as literal data + a node flag.
    """
    chop = server.resolve_op(params["chop"])
    target = server.assert_writable(server.resolve_op(params["op"]))
    par = str(params["par"])

    if not getattr(chop, "isCHOP", False):
        raise ValueError("%s is not a CHOP; bind_chop exports animation from a CHOP channel" % chop.path)

    # The target parameter must exist (data check; we never WRITE it -- the export overrides it live).
    tp = getattr(target.par, par, None)
    if tp is None:
        raise ValueError("no such parameter %r on %s" % (par, target.path))
    # Defense in depth: never wire a binding onto a code-eval sink parameter (consistent with set_par); the
    # target must also be a known data parameter (or a custom par) per the parameter allowlist.
    server.check_par_allowed(target.opType, par, tp)

    # Which source channel(s) to rename onto the export name. '*' (default) matches the CHOP's channel --
    # the proven single-channel case; an explicit name routes only that channel.
    channel = str(params.get("channel", "*"))

    # Host the renameCHOP in the SOURCE chop's parent (so autoexportroot='..' resolves to that parent).
    parent = None
    try:
        parent = chop.parent()
    except Exception:
        parent = None
    if parent is None:
        parent = server.ROOT
    if parent is None:
        raise RuntimeError("no parent COMP available to host the export renameCHOP for %s" % chop.path)

    # Compute the autoname channel name + export root. autoname resolves the channel name
    # "<path-relative-to-export-root>:<parameter>" to the target parameter.
    if _same_parent(chop, target):
        # Same-parent case: the exporter and the target share a parent, so the root is '..' (the
        # exporter's parent) and the channel name is just "<target.name>:<par>".
        autoexportroot = ".."
        channel_name = "%s:%s" % (target.name, par)
    else:
        # General case (the documented autoname rule; the same-parent case above is the common
        # one): root at '/' and the target's absolute path (minus the leading slash), e.g. "project1/geo1:tx".
        autoexportroot = "/"
        channel_name = "%s:%s" % (target.path.lstrip("/"), par)

    # Create-or-reuse the renameCHOP, deterministically named so a re-bind of the same CHOP UPDATES the
    # same node rather than duplicating it (mirror the create-or-reuse pattern in io.py's capture_ui).
    rc_name = "%s_export" % chop.name
    rc = None
    try:
        for c in list(parent.children):
            if getattr(c, "name", None) == rc_name:
                rc = c
                break
    except Exception:
        rc = None
    if rc is None:
        server.check_optype_allowed("renameCHOP")  # data-only CHOP (no code marker); defense in depth
        rc = parent.create("renameCHOP", rc_name)

    # Wire the source CHOP into the renameCHOP's first input (idempotent on re-bind).
    try:
        rc.inputConnectors[0].connect(chop)
    except Exception as e:
        raise RuntimeError("could not wire %s into the export renameCHOP %s (%s)" % (chop.path, rc.path, e))

    # Set the rename + export settings on the renameCHOP -- literal VALUES only.
    _set_val(rc, "renamefrom", channel)        # which source channel(s) to rename (default '*')
    _set_val(rc, "renameto", channel_name)     # rename to the autoname export name "<path>:<par>"
    _set_val(rc, "exportmethod", "autoname")   # "Channel Name is Path:Parameter"

    # autoexportroot is an OP-REFERENCE parameter. For the relative "root at my parent" value ('..') set
    # the literal string; for an absolute root ('/') assign the OP object with the io.py fallback+verify
    # pattern so the reference actually resolves (an unresolved root = no export link).
    aer = getattr(rc.par, "autoexportroot", None)
    if aer is None:
        raise ValueError("%s lacks the 'autoexportroot' export parameter (not a renameCHOP?)" % rc.path)
    if autoexportroot == "..":
        aer.val = ".."
    else:
        root_op = None
        try:
            root_op = server.resolve_op(autoexportroot)
        except Exception:
            root_op = None
        if not _bind_op_ref(aer, root_op, autoexportroot):
            raise RuntimeError("bind_chop could not bind %s.autoexportroot to %s "
                               "(export root unresolved -- the parameter would not be driven)"
                               % (rc.path, autoexportroot))

    # Turn on the Export flag -- a NODE FLAG (like render/display/bypass); carries no code.
    rc.export = True

    # ESTABLISH the export. Per the offline TD help, there is NO Python API that CREATES an export link (the
    # target's export-source accessors are READ-ONLY, and forcing the target into export evaluation is
    # refused unless an export already exists). TD derives the channel->parameter mapping as a COOK-TIME
    # side effect of the exporter's export-method machinery, and the docs state exports are "active only
    # while the CHOP Viewer is on". So setting the flag alone does NOT drive the parameter -- we must
    # turn the exporter's Viewer Active flag ON and FORCE A COOK so the mapping resolves.
    # Both are NODE-level, data-only (no code). Best-effort so a mock/older API never breaks the bind.
    try:
        rc.activeViewer = True
    except Exception:
        pass
    for _op in (chop, rc):
        try:
            _op.cook(force=True)
        except Exception:
            pass

    # Verify the export actually resolved -- the target auto-switches to export evaluation the instant a
    # valid export establishes (that state is read-only from our side; we only READ it here). Report the
    # honest driven-state so a caller is never told the parameter is driven while it is still Constant.
    driven = None
    try:
        driven = "EXPORT" in str(getattr(tp, "mode", "")).upper()
    except Exception:
        driven = None

    return {
        "bound": True,
        "driven": driven,   # True = the target flipped to Export mode (the export resolved); None = unknown
        "source_chop": chop.path,
        "rename_chop": server._jsonable(rc.path),
        "op": target.path,
        "par": par,
        "channel_name": channel_name,
        "autoexportroot": autoexportroot,
        "export_flag": bool(getattr(rc, "export", True)),
        "active_viewer": bool(getattr(rc, "activeViewer", False)),
        "note": ("code-free CHOP-export binding via a dedicated renameCHOP (exportmethod='autoname'): the "
                 "channel is renamed to '%s' and autoname resolves it to %s.%s. The export mapping is a "
                 "COOK-TIME side effect -- this handler turns the renameCHOP's Viewer Active flag on and "
                 "force-cooks it so the mapping resolves (setting the Export flag alone does NOT drive the "
                 "target). No expression/bind/evaluation-mode is written on the target -- a DATA binding, "
                 "not code. driven=%s reports whether the target actually flipped to Export mode; if False/"
                 "None, keep the renameCHOP's viewer active and ensure the source CHOP is cooking (an "
                 "animated source cooks each frame)." % (channel_name, target.path, par, driven)),
    }
