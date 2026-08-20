"""Recording mock of the TouchDesigner runtime globals (op / root / app), for LICENSE-FREE offline
tests of the data-only executor. TD does NOT ship a `pip`-importable module -- the executor reaches
the scene ONLY through `server.OP(...)`, `server.ROOT`, `server.APP`, which the in-TD callbacks DAT
binds via `server.bind()`. So, unlike the Houdini executor (which imports `hou`), we do NOT need to
inject a fake module into sys.modules: we just build a small fake scene and hand it to `server.bind()`.

DELIBERATE DESIGN CHOICES (so a reviewer can trust what green means)
-------------------------------------------------------------------
* A real fake scene. `OP('/path')` resolves to a `MockOp` we created, or None for an unknown path --
  so `resolve_op` takes its real "no such operator" branch on a miss.
* `MockPar` is a values-only parameter handle: `.val` is settable and `.eval()` returns it. Setting
  `.expr` (parameter-expression mode) is RECORDED to the module-level `EXPR_WRITES` list and MUST stay
  empty -- that is the runtime proof that `set_par` sets VALUES only, never expressions. A par flagged
  `raises=True` throws on `.val` set, to exercise the "per-par failure is reported, not fatal" path.
* `MockParColl` (`op.par`) returns a real `MockPar` for a known name and raises AttributeError for an
  unknown one, so `getattr(n.par, k, None)` yields None on a miss -- the handler's "no such parameter".
* No catch-all `__getattr__` on the op itself, so a genuine wrong-attribute bug in a handler surfaces.
"""
import os
import sys

# Ensure the repo root (which contains the `td_executor` package) is importable.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# Any attempt by handler code to set a parameter's EXPRESSION (code) mode is recorded here. The
# data-only boundary requires this to stay EMPTY: set_par writes `.val` (literal values) only.
EXPR_WRITES = []


class MockPar(object):
    """A single parameter handle: values-only, with an expression-mode tripwire.

    `mode`/`exportOP`/`exportSource` are READ-ONLY introspection surfaces (what `inspect` reports to
    reveal whether a parameter is in Export mode and which CHOP channel drives it). They default to a
    constant, unbound parameter; a test can pass exportOP/exportSource to simulate an active export."""

    def __init__(self, name, val=0, raises=False, mode="constant", exportOP=None, exportSource=None,
                 style="Float", clampMin=False, clampMax=False, min=0.0, max=1.0,
                 isMenu=False, menuNames=None, label=None, default=None, normMin=0.0, normMax=1.0,
                 tupletName=None):
        self.name = name
        self._val = val
        self._raises = raises
        # Introspection surface read by probe_optype (mirrors TD's Par.label/default/normMin/normMax/
        # tupletName). Defaulted so every MockPar is fully introspectable; the probe reads them defensively.
        self.label = label if label is not None else name.capitalize()
        self.default = default
        self.normMin = normMin
        self.normMax = normMax
        self.tupletName = tupletName if tupletName is not None else name
        # Numeric clamp declaration (TD's Par.clampMin/clampMax + min/max); the executor's boundary
        # re-clamp (server.clamp_par_value) reads these to bound a value exactly where TD would.
        self.clampMin = clampMin
        self.clampMax = clampMax
        self.min = min
        self.max = max
        # ParMode-like handle (str()'d by inspect); a real TD par exposes .mode as an enum.
        self.mode = mode
        # Par.style (TD's parameter style string, e.g. 'Float' / 'Pulse'); the `pulse` endpoint requires
        # a Pulse/Momentary style to fire. Defaults to a plain value param.
        self.style = style
        # Menu vocabulary (TD's Par.isMenu / Par.menuNames): set_par validates a string token against these
        # and REFUSES a garbage token instead of letting TD silently snap it to a real one.
        self.isMenu = bool(isMenu)
        self.menuNames = list(menuNames) if menuNames else []
        # Export source introspection (read-only): the OP exporting to this par + the channel spec.
        self.exportOP = exportOP
        self.exportSource = exportSource
        # par.pulse() fires a parameterless action; recorded so a test can assert exactly one fire.
        self.pulsed = 0

    def pulse(self, *a, **k):
        self.pulsed += 1

    @property
    def val(self):
        return self._val

    @val.setter
    def val(self, v):
        if self._raises:
            raise RuntimeError("simulated read-only / invalid value for %r" % self.name)
        self._val = v

    def eval(self):
        return self._val

    # Expression mode. Every data-only handler (set_par, bind_chop, ...) must NEVER touch this; if one
    # ever does, we record it and the test asserting EXPR_WRITES == [] fails. The SOLE sanctioned writer
    # is handlers/expr.py::set_expr (validated + consent-gated), whose test asserts EXPR_WRITES contains
    # exactly the validated expression. Setting `.expr` also flips the parameter to expression mode --
    # modeling real TD (assigning an expression activates expression mode) so the set_expr test can
    # verify the end state without importing ParMode.
    @property
    def expr(self):
        return ""

    @expr.setter
    def expr(self, v):
        EXPR_WRITES.append((self.name, v))
        self.mode = "expression"

    @property
    def isDefault(self):
        return False


class MockChan(object):
    """A CHOP channel handle: bind_chop's channel resolution reads `.name` off `chop.chan(0)`, and
    `inspect` reads its current value via `.eval()` (falling back to `[0]` / `float()`)."""

    def __init__(self, name, val=0.0):
        self.name = name
        self._val = val

    def eval(self):
        return self._val

    def __getitem__(self, i):
        return self._val

    def __float__(self):
        return float(self._val)


class MockConnector(object):
    """An input connector (op.inputConnectors[i]). `.connect(src)` wires `src` (an OP, or an output
    connector) into this input -- the same call control.py's `connect` uses. For a renameCHOP it copies
    the source's channel names as the rename BASE, so chan()/chans() then reflect the renamed output."""

    def __init__(self, owner, index):
        self._owner = owner
        self.index = index

    def connect(self, src):
        op = getattr(src, "_owner", src)  # accept an OP or an output connector
        self._owner._connect_input(self.index, op)


class MockParColl(object):
    """`op.par`: attribute access returns the MockPar for a known name, else AttributeError (so the
    handler's `getattr(n.par, k, None)` gets None on a miss)."""

    def __init__(self, pars):
        object.__setattr__(self, "_pars", dict(pars))

    def __getattr__(self, k):
        pars = object.__getattribute__(self, "_pars")
        if k in pars:
            return pars[k]
        raise AttributeError(k)

    def __call__(self):
        # op.pars() -> iterable of pars (used by read_network)
        return list(object.__getattribute__(self, "_pars").values())


class _MockCell(object):
    """A DAT cell handle: TD exposes `n[r, c].val` (the cell text). inspect reads str(cell.val)."""

    def __init__(self, val):
        self.val = val


class MockOp(object):
    """A scene operator. Explicit named surface only (no catch-all)."""

    def __init__(self, path, pars=None, opType="testTOP", family="TOP", is_comp=False, children=None,
                 channels=None):
        self.path = path
        self.name = path.rstrip("/").rsplit("/", 1)[-1] or path
        self.opType = opType
        self.family = family
        # TD's OP.maxInputs (read by probe_optype alongside family). A plain default; harmless elsewhere.
        self.maxInputs = 1
        self.isCOMP = is_comp
        # Family predicates TD exposes on every OP (used by save_top/capture_ui/bind_chop guards).
        self.isTOP = (family == "TOP")
        self.isCHOP = (family == "CHOP")
        self.isDAT = (family == "DAT")
        # CHOP channels (inspect reads channel names + values; a renameCHOP renames them, see below).
        self._channels = [MockChan(c) if isinstance(c, str) else c for c in (channels or [])]
        # renameCHOP modeling: a rename node inherits its source's channels as a base, then renamefrom/
        # renameto rename them -- so chan()/chans() reflect the renamed output (set up in create()).
        self._is_rename = False
        self._base_channels = None
        # Table DAT cells: bind_chop writes literal routing rows here via clear()/appendRow().
        self.rows = []
        # The Export Flag (a node flag, like render/display/bypass); bind_chop sets it True. Any write is
        # observable to the test; it carries no code.
        self.export = False
        self.par = MockParColl(pars or {})
        self.children = list(children or [])
        self.nodeX = 0.0
        self.nodeY = 0.0
        self.inputs = []
        self.inputConnectors = []
        self.outputConnectors = []
        self.numChildren = len(self.children)
        self._destroyed = False
        self._parent = None
        self.saved_to = None
        self.cooked = 0

    def pars(self):
        return self.par()

    def parent(self, n=1):
        # TD's OP.parent() returns the parent COMP (n=1 default). Used by capture_ui to host its
        # temporary OP Viewer TOP beside the target.
        return self._parent

    def chan(self, i):
        # TD's CHOP.chan(index) -> a Channel; inspect reads chan(0).name.
        return self._effective_channels()[i]

    def chans(self, *a, **k):
        # TD's CHOP.chans() -> the list of channels; inspect reads each channel's name + eval() value.
        return list(self._effective_channels())

    def _effective_channels(self):
        # A plain CHOP exposes its own channels; a renameCHOP renames them. A renameCHOP with renamefrom
        # and a non-empty renameto emits its (collapsed) channel under the renameto name -- the proven
        # renamefrom='*' behavior; otherwise it passes its base channels through unchanged.
        if not self._is_rename:
            return list(self._channels)
        base = self._base_channels or ["chan1"]
        rfrom = rto = ""
        try:
            rfrom = str(self.par.renamefrom.eval() or "")
        except Exception:
            rfrom = ""
        try:
            rto = str(self.par.renameto.eval() or "")
        except Exception:
            rto = ""
        if rfrom and rto:
            return [MockChan(rto)]
        return [MockChan(n) for n in base]

    def _connect_input(self, index, src):
        # Wire src into input `index` (records it in .inputs). A renameCHOP inherits the source's channel
        # names as its rename base, so the renamed output reflects the real source channels.
        while len(self.inputs) <= index:
            self.inputs.append(None)
        self.inputs[index] = src
        if self._is_rename:
            srcchans = getattr(src, "_channels", None)
            if srcchans:
                self._base_channels = [c.name for c in srcchans]

    def clear(self, keepFirstRow=False, keepFirstCol=False):
        # tableDAT.clear(): bind_chop clears the routing table before writing fresh rows.
        if keepFirstRow and self.rows:
            self.rows = self.rows[:1]
        else:
            self.rows = []

    def appendRow(self, cells):
        # tableDAT.appendRow(list): records a literal routing row (cells are pure data).
        self.rows.append([str(c) for c in cells])

    @property
    def numRows(self):
        return len(self.rows)

    @property
    def numCols(self):
        # TD DAT.numCols -> width of the widest row (inspect caps the grid it reads).
        return max((len(r) for r in self.rows), default=0)

    def __getitem__(self, key):
        # TD DAT cell access n[r, c] -> a Cell whose .val is the cell text. inspect reads str(n[r,c].val).
        r, c = key
        return _MockCell(self.rows[r][c])

    def create(self, optype, name=None):
        # Populate real params/family per optype so handler calls are observable.
        #  * opviewerTOP: capture_ui's _set_literal calls (opviewer + custom output resolution).
        #  * renameCHOP : bind_chop's export node -- rename + autoname export params; an input connector so
        #                 the handler can wire the source CHOP in; rename modeling on chan()/chans().
        # probe_optype create-failure path: a type whose create() raises (models an op that needs a
        # special parent context). The handler must return ok:False rather than propagate the exception.
        if optype == "probeFailTOP":
            raise RuntimeError("simulated: this operator needs a special context")
        pars = {}
        family = "TOP"
        if optype == "noiseTOP":
            # A rich, catalog-shaped parameter set so probe_optype has real params to introspect:
            # a numeric (with a menu among them) exercising style/default/norm/hard/tokens/tuplet.
            family = "TOP"
            pars = {
                "period": MockPar("period", 1.0, style="Float", default="1", normMin=0.0, normMax=10.0,
                                  min=0.0, max=1.0, tupletName="period"),
                "type": MockPar("type", "sparse", style="Menu", default="sparse", isMenu=True,
                                menuNames=["sparse", "hermite", "harmon"], tupletName="type"),
            }
        elif optype == "opviewerTOP":
            pars = {"opviewer": MockPar("opviewer", ""),
                    "outputresolution": MockPar("outputresolution", "useinput"),
                    "resolutionw": MockPar("resolutionw", 0),
                    "resolutionh": MockPar("resolutionh", 0)}
        elif optype == "renameCHOP":
            family = "CHOP"
            pars = {"renamefrom": MockPar("renamefrom", "*"),
                    "renameto": MockPar("renameto", ""),
                    "exportmethod": MockPar("exportmethod", "off"),
                    # autoexportroot is an OP-reference param: .eval() returns whatever was assigned, so
                    # bind_chop's fallback+verify passes; the '..' relative value is set as a plain string.
                    "autoexportroot": MockPar("autoexportroot", "..")}
        elif optype == "tableDAT":
            family = "DAT"
        elif optype == "textDAT":
            # The GLSL lane creates a derived child Text DAT under a glslTOP and writes its `.text` (the
            # single sanctioned DAT-.text write). Give the mock a settable `.text` so the handler test can
            # assert the exact source landed (or was never written on a rejected/refused call).
            family = "DAT"
        child = MockOp((self.path.rstrip("/") + "/" + (name or (optype + "1"))),
                       pars=pars, opType=optype, family=family)
        child._parent = self
        if optype == "textDAT":
            child.text = None   # settable; starts unwritten so a test can prove nothing was written
        if optype == "renameCHOP":
            child._is_rename = True
            child.inputConnectors = [MockConnector(child, 0)]
        self.children.append(child)
        self.numChildren = len(self.children)
        return child

    def cook(self, force=False, recurse=False, includeUtility=False):
        self.cooked += 1

    def save(self, filepath, *a, **k):
        # TOP.save() writes the current image. Write a minimal real file so the handler's getsize and
        # the gateway's inline-embed find a byte stream (a valid PNG signature keeps it honest).
        with open(filepath, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")
        self.saved_to = filepath

    def destroy(self):
        self._destroyed = True


class Scene(object):
    """Path -> MockOp registry with an `op(path)` resolver matching TD's `op()` shortcut."""

    def __init__(self):
        self.ops = {}

    def op(self, path):
        return self.ops.get(str(path))

    def add(self, mockop):
        self.ops[mockop.path] = mockop
        return mockop


class MockApp(object):
    build = "2023.11760"


def install():
    """Import the executor + all handler modules (registers @endpoint decorators), build a fresh fake
    scene, bind it into `server`, and return (server, scene). Call at the top of every test module."""
    from td_executor import server
    import td_executor.handlers  # noqa: F401  -- registers endpoints via decorators

    del EXPR_WRITES[:]
    scene = Scene()
    root = MockOp("/", opType="root", family="COMP", is_comp=True)
    scene.ops["/"] = root
    # The conventional work container the executor's _default_parent() prefers.
    project1 = MockOp("/project1", opType="containerCOMP", family="COMP", is_comp=True)
    scene.add(project1)
    root.children.append(project1)
    root.numChildren = len(root.children)

    server.bind(op=scene.op, root=root, app=MockApp())
    return server, scene
