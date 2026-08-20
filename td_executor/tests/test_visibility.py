"""W3 in-the-loop visibility layer: set_pos (network layout) + capture_ui (see any operator's own
node viewer from TouchDesigner's OWN GPU buffer, never a screen grab).

These prove the two shipped W3 utility endpoints exist, are reachable, and behave data-only:
  * set_pos moves an operator's nodeX/nodeY (layout only; no code, no .expr).
  * capture_ui renders the target via a TEMPORARY OP Viewer TOP (TD's own buffer), saves a CONFINED
    PNG, and DESTROYS the temp node -- and it NEVER performs a screen grab (static source scan) and
    NEVER sets a parameter expression (runtime tripwire)."""
import os
import tempfile
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control, io


class TestSetPos(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.op = MockOp("/project1/blur1", opType="blurTOP")
        self.scene.add(self.op)

    def test_registered_endpoint(self):
        self.assertIn("set_pos", self.server._REGISTRY)

    def test_sets_position(self):
        out = control.set_pos({"op": "/project1/blur1", "x": 120.0, "y": -40.5})
        self.assertEqual(out["path"], "/project1/blur1")
        self.assertEqual(out["pos"], {"x": 120.0, "y": -40.5})
        self.assertEqual(self.op.nodeX, 120.0)
        self.assertEqual(self.op.nodeY, -40.5)
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_partial_axis(self):
        out = control.set_pos({"op": "/project1/blur1", "y": 7.0})
        self.assertEqual(out["pos"], {"y": 7.0})
        self.assertNotIn("x", out["pos"])

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError):
            control.set_pos({"op": "/nope", "x": 1.0})


class TestCaptureUi(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        # a CHOP (non-TOP) -- the case save_top cannot show, so capture_ui earns its keep
        self.target = MockOp("/project1/lfo1", opType="lfoCHOP", family="CHOP")
        self.target._parent = self.scene.ops["/project1"]
        self.scene.add(self.target)
        # confine writes to a temp working dir for the duration
        self._saved_wd = self.server.WORKING_DIR
        self._tmp = tempfile.mkdtemp(prefix="tdmcp_cap_")
        self.server.WORKING_DIR = os.path.realpath(self._tmp)
        self.server._WD_OVERRIDE = os.path.realpath(self._tmp)   # working_dir() honors this over live arm.json

    def tearDown(self):
        self.server.WORKING_DIR = self._saved_wd
        self.server._WD_OVERRIDE = None

    def test_registered_endpoint(self):
        self.assertIn("capture_ui", self.server._REGISTRY)

    def _viewers(self):
        # the persistent viewer now lives inside a dedicated hidden scratch COMP, not loose in /project1
        p1 = self.scene.ops["/project1"]
        scratch = next((c for c in p1.children if getattr(c, "name", None) == "_mcp_scratch"), None)
        host = scratch if scratch is not None else p1
        return [c for c in host.children if c.opType == "opviewerTOP"]

    def test_captures_via_persistent_opviewer_top_not_destroyed(self):
        out = io.capture_ui({"op": "/project1/lfo1", "path": "cap.png", "width": 640, "height": 360})
        # honest return: what was captured + that it is TD's own OP Viewer TOP buffer
        self.assertEqual(out["op"], "/project1/lfo1")
        self.assertEqual(out["captured"], "lfoCHOP")
        self.assertEqual(out["source"], "opviewerTOP")
        self.assertIn("not a screen grab", out["note"])
        # first capture of a target is NOT warm (next-frame render latency); the note says to call again
        self.assertIn("warm", out)
        self.assertFalse(out["warm"])
        self.assertIn("call capture_ui again", out["note"])
        # COLD first call: nothing is saved (no phantom blank file) and no image path is returned
        self.assertNotIn("saved", out)
        self.assertIsNone(out.get("size_bytes"))
        self.assertIsNone(out.get("image"))
        # exactly ONE persistent OP Viewer TOP was created-or-reused, deterministically named, pointed at
        # the target, and NOT destroyed (persistence is the fix -- it renders on a later frame boundary)
        viewers = self._viewers()
        self.assertEqual(len(viewers), 1)
        viewer = viewers[0]
        self.assertEqual(viewer.name, "_mcp_capture_view")
        self.assertEqual(out["viewer"], viewer.path)
        # opviewer is an OP-reference param: the handler binds the OPERATOR (not a bare path string) and
        # verifies it resolved to the target (fails loud otherwise). eval() yields the referenced op.
        resolved = getattr(viewer.par, "opviewer").eval()
        self.assertEqual(getattr(resolved, "path", resolved), "/project1/lfo1")
        self.assertEqual(getattr(viewer.par, "outputresolution").eval(), "custom")
        self.assertEqual(getattr(viewer.par, "resolutionw").eval(), 640)
        self.assertFalse(viewer._destroyed, "capture_ui must NOT destroy its persistent viewer node")
        # never set a parameter expression (data-only)
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_second_call_reuses_same_persistent_viewer_and_is_warm(self):
        io.capture_ui({"op": "/project1/lfo1", "path": "cap.png"})
        viewers1 = self._viewers()
        self.assertEqual(len(viewers1), 1)
        # a second capture of the SAME target reuses the SAME node (no new viewer) and reports warm=true
        out2 = io.capture_ui({"op": "/project1/lfo1", "path": "cap.png"})
        viewers2 = self._viewers()
        self.assertEqual(len(viewers2), 1)
        self.assertIs(viewers2[0], viewers1[0])
        self.assertFalse(viewers2[0]._destroyed)
        self.assertTrue(out2["warm"])
        # the WARM call saves the real frame: a confined PNG under the working dir
        self.assertTrue(out2["saved"].endswith("cap.png"))
        self.assertTrue(os.path.isfile(out2["saved"]))
        self.assertTrue(out2["size_bytes"] and out2["size_bytes"] > 0)

    def test_resolution_clamped(self):
        io.capture_ui({"op": "/project1/lfo1", "path": "cap2.png", "width": 99999})
        viewer = self._viewers()[0]
        self.assertEqual(getattr(viewer.par, "resolutionw").eval(), 1920)  # clamped to _CAPTURE_MAX_DIM

    def test_confinement_enforced(self):
        with self.assertRaises(PermissionError):
            io.capture_ui({"op": "/project1/lfo1", "path": "../../escape.png"})

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError):
            io.capture_ui({"op": "/nope"})

    def test_no_screen_grab_sink_in_source(self):
        # STATIC guard: the visual-feedback lane must never reach for an OS screen grab or TD's Screen
        # Grab TOP -- capture must come from TD's OWN buffer (the cardinal rule of this wave).
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "handlers", "io.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read().lower()
        for sink in ("screengrab", "screencapture", "grabwindow", "pyautogui", "mss",
                     "getpixel", "screenshot", ".expr", "setexpression"):
            self.assertNotIn(sink, src, "io.py must contain no screen-grab / expression sink (found %r)" % sink)


if __name__ == "__main__":
    unittest.main()
