"""td_executor/handlers/io.py -- confined file output (previews/exports inside the working dir).

This is the visual-feedback lane: save a TOP's current output to an image so the agent can SEE what
it built and iterate. Data-only: writes an image file (no code), and every path is realpath-confined
to the working directory via server.confined_path (the bridge config dir is always off-limits).
"""
import os
import fnmatch
from td_executor import server


# Image-write tools may ONLY write an image file, so a render save can never be aimed at a
# .py/.json/.toe trust file (even inside the working dir) to corrupt the bridge. Mirrors write_csv's
# tabular-extension gate. The path is ALSO realpath-confined (server.confined_path), which now additionally
# excludes the executor trust root -- this extension gate is the second, defense-in-depth half.
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr", ".dds", ".tga")


def _require_image_ext(rel):
    ext = os.path.splitext(str(rel))[1].lower()
    if ext not in _IMAGE_EXTS:
        raise ValueError("image tools only write image files %s (got %r) -- not a code/data file"
                         % (list(_IMAGE_EXTS), ext))
    return rel


@server.endpoint("save_top")
def save_top(params):
    """Save a TOP's current output image to `path` (relative to the working dir; default '<name>.png').
    Returns the absolute path + byte size. Use to preview generated content."""
    n = server.resolve_op(params["op"])
    if not getattr(n, "isTOP", False):
        raise ValueError("%s is not a TOP (save_top only saves image operators)" % n.path)
    rel = _require_image_ext(str(params.get("path") or ("previews/" + n.name + ".png")))
    target = server.confined_path(os.path.join(server.working_dir(), rel))
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    n.save(target)
    size = os.path.getsize(target) if os.path.exists(target) else None
    return {"op": n.path, "saved": target.replace("\\", "/"), "size_bytes": size}


# ── show: the newcomer "SEE my output" tool. TouchDesigner has NO default viewport, so a pure-MCP driver
# needs a foolproof way to look at a result. `show` renders a TOP to an inline PNG (returned in-chat) and,
# when `op` is omitted, AUTO-FINDS the final output node (an OUT_-named or terminal TOP) so the driver can
# say "show me the output" with zero knowledge of the graph. It also warns about the 256px composite crush.
def _find_final_top(parent_path=None):
    """Best-effort 'what is the final output' resolver: an OUT_-named TOP wins, else a terminal TOP (no
    downstream), else the last TOP in the container. Returns the OP or None."""
    try:
        container = server.resolve_op(parent_path) if parent_path else server.resolve_op("/project1")
    except Exception:
        container = server.ROOT
    if container is None:
        return None
    try:
        tops = [c for c in container.children if getattr(c, "isTOP", False)]
    except Exception:
        return None
    if not tops:
        return None
    named = [t for t in tops if str(getattr(t, "name", "")).upper().startswith("OUT")]
    if named:
        return named[-1]
    terminal = []
    for t in tops:
        try:
            if not list(t.outputs):
                terminal.append(t)
        except Exception:
            pass
    if terminal:
        return terminal[-1]
    return tops[-1]


def _resolution_warning(n):
    """Return a warning string if a composite/over TOP's own resolution is SMALLER than any input's --
    the silent 256x256 crush that ruins a full-res render. None if fine."""
    try:
        nw, nh = int(getattr(n, "width", 0) or 0), int(getattr(n, "height", 0) or 0)
        for i in n.inputs:
            if i is None:
                continue
            iw, ih = int(getattr(i, "width", 0) or 0), int(getattr(i, "height", 0) or 0)
            if iw and ih and nw and nh and (nw < iw or nh < ih):
                return ("%s is %dx%d but its input %s is %dx%d -- the render is being CRUSHED. Set "
                        "outputresolution=custom + resolutionw/h on %s to match."
                        % (n.name, nw, nh, i.name, iw, ih, n.name))
    except Exception:
        pass
    return None


@server.endpoint("show")
def show(params):
    """SEE the output as an inline image. Pass a TOP `op`, or omit it and `show` auto-finds the final
    output node (an OUT_-named or terminal TOP under `parent`, default /project1). Renders that TOP to a
    confined PNG and returns it INLINE so a driver with no viewport can look at its result. The newcomer
    preview keystone -- 'show me what this looks like'. For a non-TOP node's own viewer use capture_ui.

      op      : optional TOP path. Omit to auto-find the final output TOP.
      parent  : optional container to search when op is omitted (default /project1).
      path    : optional confined .png output path (default previews/show_<name>.png).

    Returns the op it resolved, how it resolved it, the resolution, and -- if the TOP is silently
    downscaling a larger input (the 256x256 composite trap) -- a resolution_warning."""
    op = params.get("op")
    if op:
        n = server.resolve_op(op)
    else:
        n = _find_final_top(params.get("parent"))
        if n is None:
            raise ValueError("no output TOP found to show -- name your final node OUT_<something> or pass "
                             "op=<TOP path> (searched %s)" % (params.get("parent") or "/project1"))
    if not getattr(n, "isTOP", False):
        raise ValueError("%s is a %s, not a TOP -- `show` renders the final IMAGE. For a CHOP/SOP/COMP/MAT "
                         "node's own viewer use capture_ui." % (n.path, getattr(n, "family", "?")))
    rel = _require_image_ext(str(params.get("path") or ("previews/show_" + n.name + ".png")))
    target = server.confined_path(os.path.join(server.working_dir(), rel))
    d = os.path.dirname(target)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    n.save(target)
    size = os.path.getsize(target) if os.path.exists(target) else None
    out = {"op": n.path, "resolved_via": ("explicit op" if op else "auto (OUT_/terminal TOP)"),
           "width": int(getattr(n, "width", 0) or 0), "height": int(getattr(n, "height", 0) or 0),
           "saved": target.replace("\\", "/"), "size_bytes": size}
    warn = _resolution_warning(n)
    if warn:
        out["resolution_warning"] = warn
    return out


# ── write_csv: DATA-ONLY tabular file writer. TouchDesigner's Table DAT cannot take literal cell values
# through any typed tool (its Fill-page cell expressions are withheld code sinks, and manual cell edits are
# Python) -- the ONLY data-only way to load a cue table / lookup table is tableDAT.file reading a delimited
# text file. This writes exactly that: rows of stringified cells, to a working-dir-confined tabular file.
# SAFETY: realpath-confined to the working dir (no traversal, config dir off-limits) AND an extension
# whitelist (.csv/.tsv/.dat/.txt only) so no code file can be written; cells are stringified DATA (never
# executed). This adds a narrow, tabular-only file-write surface -- not a general write primitive.
_TABLE_EXTS = (".csv", ".tsv", ".dat", ".txt")


_WRITE_CSV_MAX = 2_000_000   # 2 MB cap on written tabular text (a cue/lookup table is KBs)


@server.endpoint("write_csv")
def write_csv(params):
    """Write delimited tabular TEXT to a file in the working dir, for loading into a Table DAT (e.g. a cue
    table for choreography, a lookup table). This is the ONLY data-only way to get literal cell values into
    a Table DAT (its Fill-page cell expressions are withheld code sinks). Data-only + tightly scoped: the
    path is realpath-confined to the working directory AND must carry a tabular extension
    (.csv/.tsv/.dat/.txt) so no code file can ever be written; the payload is plain TEXT (never executed).

      path    : output file path (confined to the working dir; extension must be .csv/.tsv/.dat/.txt).
      content : the full file text -- rows separated by newlines, cells by commas (or tabs for .tsv).

    Returns the saved path + byte size + line count. Load it afterward with a tableDAT `file` param."""
    rel = str(params["path"])
    ext = os.path.splitext(rel)[1].lower()
    if ext not in _TABLE_EXTS:
        raise ValueError("write_csv only writes tabular text files %s (got %r) -- it cannot write a code file"
                         % (list(_TABLE_EXTS), ext))
    content = params.get("content")
    if not isinstance(content, str):
        raise ValueError("'content' must be a string (the full delimited file text)")
    if len(content.encode("utf-8", "ignore")) > _WRITE_CSV_MAX:
        raise ValueError("content exceeds the %d-byte tabular-file cap" % _WRITE_CSV_MAX)
    if not content.endswith("\n"):
        content = content + "\n"

    target = server.confined_path(os.path.join(server.working_dir(), rel))
    d = os.path.dirname(target)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return {"saved": target.replace("\\", "/"),
            "lines": content.count("\n"), "size_bytes": os.path.getsize(target)}


# ── capture_ui: SEE any operator's own node viewer, from TD's OWN GPU buffer (never a screen grab) ──
# The honest TouchDesigner analog of the Houdini bridge's capture_ui. TD exposes NO API to render a
# Network Editor / parameter PANE from its own buffer (only an OS screen grab could, which this bridge
# REFUSES -- the cardinal privacy sin from the consumer-seat reports). But the OP Viewer TOP
# (opviewerTOP) renders ANY operator's Node Viewer into a TOP on the GPU -- TD's own pixels, occlusion-
# immune: a window in front of TouchDesigner can never bleed into it.
#
# PERSISTENT-VIEWER DESIGN (fixes the blank-frame bug proven on live TD): the OP Viewer TOP renders on
# the NEXT FRAME BOUNDARY (TD's main-thread render cycle), which a single synchronous handler call
# CANNOT force -- cooking it N times in one call still yields a transparent frame. But a viewer node
# that PERSISTS across calls DOES render: a later call to save the same persistent viewer produces
# correct pixels. So instead of creating-and-destroying a temp viewer each call (blank), we create-or-
# reuse ONE deterministically-named hidden helper viewer beside the target and KEEP it alive. The first
# capture after (re)pointing it at a target can be one frame stale (warm=false in the result); calling
# capture_ui again returns the now-rendered frame. Data-only throughout (values + a node reference).
_CAPTURE_MAX_DIM = 1920      # long-edge cap so the encoded PNG always inlines (gateway MAX_IMAGE_BYTES)
_CAPTURE_DEFAULT_W = 1280
_CAPTURE_DEFAULT_H = 720
_CAPTURE_VIEWER_NAME = "_mcp_capture_view"   # persistent hidden helper viewer, reused across calls
_CAPTURE_SCRATCH_COMP = "_mcp_scratch"       # dedicated container so the helper never clutters the user's network


def _set_literal(op, name, value):
    """Set a parameter's literal VALUE if the parameter exists (probe-safe: never invents a param, and
    never sets a parameter expression -- data-only, values only). Returns True if applied."""
    p = getattr(op.par, name, None)
    if p is None:
        return False
    try:
        p.val = value
        return True
    except Exception:
        return False


def _capture_host():
    """Create-or-reuse a dedicated hidden scratch COMP to host the persistent capture viewer, so the helper
    never clutters the user's working network (driver-seat: _mcp_capture_view used to land loose in
    /project1). Home is /project1 (falls back to the root); returns the scratch COMP, or the home container
    itself as a last resort so capture still works."""
    home = None
    try:
        home = server.resolve_op("/project1")
    except Exception:
        home = None
    if home is None:
        home = getattr(server, "ROOT", None)
    if home is None:
        return None
    try:
        for c in list(getattr(home, "children", [])):
            if getattr(c, "name", None) == _CAPTURE_SCRATCH_COMP:
                return c
        return home.create(server.check_optype_allowed("baseCOMP"), _CAPTURE_SCRATCH_COMP)
    except Exception:
        return home


def release_capture_viewer_ref(op):
    """If the persistent capture viewer's `opviewer` reference points at `op`, clear it. Called just
    before an op is destroyed so deleting a previously-captured target leaves no dangling 'Operator
    Viewer' reference (which find_errors would otherwise flag as a stale-path warning). Read-only except
    for clearing the one stale ref; never creates the scratch host; best-effort (never raises)."""
    try:
        target_path = getattr(op, "path", None)
        if not target_path:
            return
        home = None
        try:
            home = server.resolve_op("/project1")
        except Exception:
            home = None
        if home is None:
            home = getattr(server, "ROOT", None)
        if home is None:
            return
        # find the scratch COMP + the viewer WITHOUT creating anything
        scratch = None
        for c in list(getattr(home, "children", [])):
            if getattr(c, "name", None) == _CAPTURE_SCRATCH_COMP:
                scratch = c
                break
        containers = [scratch, home] if scratch is not None else [home]
        for cont in containers:
            for c in list(getattr(cont, "children", [])):
                if getattr(c, "name", None) != _CAPTURE_VIEWER_NAME:
                    continue
                vp = getattr(c.par, "opviewer", None)
                if vp is None:
                    continue
                try:
                    cur = vp.eval()
                except Exception:
                    cur = None
                cur_path = getattr(cur, "path", None) or (cur if isinstance(cur, str) else None)
                if cur_path == target_path:
                    try:
                        vp.val = ""
                    except Exception:
                        pass
    except Exception:
        pass


@server.endpoint("capture_ui")
def capture_ui(params):
    """See ANY operator's OWN node viewer as an image, rendered from TouchDesigner's own GPU buffer --
    NEVER a screen grab. Uses a PERSISTENT hidden helper OP Viewer TOP (created once, then REUSED across
    calls) that renders the target operator's Node Viewer (a CHOP's channel graph, a SOP's / geometry-
    COMP's 3D view, a MAT preview, a TOP image, a panel COMP) into a TOP, and saves it to a confined PNG
    (returned INLINE so the driver SEES it). Because the pixels come from TD's own OP Viewer TOP (not the
    OS screen), another window sitting in front of TouchDesigner can never bleed into the capture -- a
    privacy + correctness guarantee. This is the closest honest TD analog to 'watch the node'.

    FRAME LATENCY (why the viewer persists): the OP Viewer TOP only renders on the NEXT frame boundary,
    which a single synchronous handler call cannot force. So the FIRST capture after the viewer is
    (re)pointed at a target can be one frame stale/blank -- the result carries warm=false. If the image
    looks blank or stale, simply call capture_ui AGAIN: the persistent viewer will have rendered by then
    (warm=true). The helper node is kept alive (not destroyed) precisely so the next call succeeds.

      op            : the operator whose node viewer to capture (any family: TOP/CHOP/SOP/COMP/MAT/...).
      width/height  : optional output pixels (each clamped 16..1920 so the PNG stays inline-embeddable;
                      defaults 1280x720).
      path          : optional confined .png output path (default previews/capture_<name>.png).

    Returns include `warm` (false = this frame may be stale; call again) and a `note`. HONEST SCOPE: it
    renders an OPERATOR's viewer, NOT the Network Editor pane or the parameter/UI chrome. TouchDesigner
    exposes no API to render those panes from its own buffer -- only an OS screen grab could, which this
    bridge refuses. For network STRUCTURE use read_network; for a TOP's finished content use save_top."""
    target = server.resolve_op(params["op"])
    optype = server.check_optype_allowed("opviewerTOP")  # data-only viewer TOP (no code marker)

    # Host the ONE persistent helper viewer inside a dedicated hidden scratch COMP so it never clutters the
    # user's working container. The opviewer param references the TARGET by path (resolves globally), so the
    # viewer needn't sit beside the target.
    parent = _capture_host()
    if parent is None:
        raise RuntimeError("no container available to host the capture viewer")

    w = int(server.clamp(int(params.get("width", _CAPTURE_DEFAULT_W)), 16, _CAPTURE_MAX_DIM))
    h = int(server.clamp(int(params.get("height", _CAPTURE_DEFAULT_H)), 16, _CAPTURE_MAX_DIM))

    # Create-or-reuse the deterministically-named persistent viewer (mirror the create-or-reuse pattern
    # used for the routing table in animation.py). A reused viewer that was ALREADY pointed at this
    # target has had frame boundaries to render -> warm; a freshly created or just-repointed viewer is
    # not yet warm (its next-frame render hasn't happened within this synchronous call).
    viewer = None
    try:
        for c in list(parent.children):
            if getattr(c, "name", None) == _CAPTURE_VIEWER_NAME:
                viewer = c
                break
    except Exception:
        viewer = None
    reused = viewer is not None
    if viewer is None:
        viewer = parent.create(optype, _CAPTURE_VIEWER_NAME)

    # 'opviewer' is an OP-REFERENCE parameter (style OP). Assign the OPERATOR itself, not a bare path
    # string: a `.val = "<path>"` string assignment does NOT reliably bind an OP param and yields a
    # BLANK capture (found on live TD). Read the previous target first (to decide warmth), then try the
    # op object, then a path string, and VERIFY the reference actually resolved -- FAIL LOUD otherwise.
    vp = getattr(viewer.par, "opviewer", None)
    if vp is None:
        raise RuntimeError("opviewerTOP exposes no 'opviewer' parameter; cannot capture node viewer")
    prev_target = None
    if reused:
        try:
            prev = vp.eval()
            prev_target = getattr(prev, "path", None) or (prev if isinstance(prev, str) else None)
        except Exception:
            prev_target = None
    bound = False
    for candidate in (target, target.path):
        try:
            vp.val = candidate
        except Exception:
            continue
        try:
            resolved = vp.eval()
        except Exception:
            resolved = None
        if getattr(resolved, "path", None) == target.path or resolved in (target, target.path):
            bound = True
            break
    if not bound:
        raise RuntimeError("capture_ui could not bind opviewerTOP.opviewer to %s "
                           "(node-viewer capture unavailable for this operator)" % target.path)
    # Warm only if the persistent viewer was already rendering THIS target (so its buffer is populated).
    warm = bool(reused and prev_target == target.path)

    _set_literal(viewer, "outputresolution", "custom")     # honor resolutionw/h below (valid token)
    _set_literal(viewer, "resolutionw", w)
    _set_literal(viewer, "resolutionh", h)
    # Resolve + confine the output path on EVERY call so a bad/traversing path is refused whether or not we
    # end up saving (a cold call saves nothing -- see below).
    rel = _require_image_ext(str(params.get("path") or ("previews/capture_" + target.name + ".png")))
    target_path = server.confined_path(os.path.join(server.working_dir(), rel))
    # Cook a few times to nudge the buffer; the true render still lands on a later frame boundary, which
    # is exactly why the viewer is PERSISTENT (a subsequent call sees the rendered frame -- warm=true).
    for _ in range(4):
        try:
            viewer.cook(force=True)
        except Exception:
            break
    try:
        viewer_path = viewer.path
    except Exception:
        viewer_path = None

    # COLD (warm=false): the OP Viewer TOP renders on the NEXT frame boundary, so saving now would write a
    # blank/stale frame. Save NOTHING and return NO image path or size -- no phantom blank file, no image
    # that looks like real output. Just report the viewer is set up and that the caller should call again.
    if not warm:
        return {"op": target.path, "captured": target.opType,
                "width": w, "height": h, "source": "opviewerTOP",
                "warm": False, "viewer": viewer_path, "image": None,
                "note": ("TouchDesigner's own OP Viewer TOP GPU buffer (not a screen grab). warm=false: it "
                         "renders on the NEXT frame boundary (a synchronous call cannot force it), so nothing "
                         "was saved this call -- call capture_ui again with the SAME op and the persistent "
                         "viewer will have rendered the real frame.")}

    # WARM: the persistent viewer has had a frame boundary to render -- save its real pixels.
    d = os.path.dirname(target_path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    viewer.save(target_path)
    size = os.path.getsize(target_path) if os.path.exists(target_path) else None
    # Echo the ACTUAL rendered resolution read back from the viewer TOP's own buffer.
    aw = int(getattr(viewer, "width", w) or w)
    ah = int(getattr(viewer, "height", h) or h)
    note = ("TouchDesigner's own OP Viewer TOP GPU buffer (not a screen grab). Rendered by a PERSISTENT "
            "hidden helper viewer (%s) reused across calls." % _CAPTURE_VIEWER_NAME)
    return {"op": target.path, "captured": target.opType,
            "saved": target_path.replace("\\", "/"), "size_bytes": size,
            "width": aw, "height": ah, "source": "opviewerTOP",
            "warm": True, "viewer": viewer_path, "note": note}
