"""W6 animation: bind_chop establishes a CODE-FREE CHOP-export param binding via a dedicated renameCHOP +
autoname (the data-only animation mechanism). Two independent proofs it writes NO code:
  * RUNTIME: the mock parameter records any `.expr` write to EXPR_WRITES; it MUST stay empty (same
    tripwire that guards set_par). The binding lives entirely on the renameCHOP (literal rename/export
    values + the Export node flag); the target parameter is never written at all.
  * STATIC : animation.py's source contains no `.expr`/setExpression/exportOP/bindExpr/`.mode`/ParMode
             sink (belt-and-suspenders, mirroring the set_par source scan)."""
import os
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import animation


def _make_chop(scene, path="/project1/null1", optype="nullCHOP"):
    """A source CHOP with a first channel ('chan1') and a parent COMP. The export/rename params live on
    the dedicated renameCHOP bind_chop creates -- NOT on this source node."""
    chop = MockOp(path, opType=optype, family="CHOP", channels=["chan1"])
    chop._parent = scene.ops["/project1"]
    scene.add(chop)
    return chop


class TestBindChop(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.chop = _make_chop(self.scene)
        self.target = MockOp("/project1/level1", opType="levelTOP", family="TOP",
                             pars={"opacity": MockPar("opacity", 1.0)})
        # SAME parent as the source CHOP -> the same-parent autoname case (root '..').
        self.target._parent = self.scene.ops["/project1"]
        self.scene.add(self.target)

    def _bind(self, **extra):
        p = {"chop": "/project1/null1", "op": "/project1/level1", "par": "opacity"}
        p.update(extra)
        return animation.bind_chop(p)

    def _rc(self):
        """The renameCHOP bind_chop created in the source's parent (named '<chop>_export')."""
        for c in self.scene.ops["/project1"].children:
            if getattr(c, "name", None) == "null1_export":
                return c
        return None

    def test_registered_endpoint(self):
        self.assertIn("bind_chop", self.server._REGISTRY)

    def test_creates_rename_chop_in_source_parent(self):
        out = self._bind()
        self.assertTrue(out["bound"])
        self.assertEqual(out["source_chop"], "/project1/null1")
        self.assertEqual(out["op"], "/project1/level1")
        self.assertEqual(out["par"], "opacity")
        # a renameCHOP was created in the SOURCE chop's parent, deterministically named
        rc = self._rc()
        self.assertIsNotNone(rc)
        self.assertEqual(rc.opType, "renameCHOP")
        self.assertEqual(rc.path, "/project1/null1_export")
        self.assertEqual(out["rename_chop"], "/project1/null1_export")
        # fed by the source CHOP (input 0 wired to it)
        self.assertIs(rc.inputs[0], self.chop)

    def test_channel_renamed_to_path_par(self):
        # same-parent case: the channel is renamed to "<target.name>:<par>" and autoname resolves it
        out = self._bind()
        self.assertEqual(out["channel_name"], "level1:opacity")
        rc = self._rc()
        self.assertEqual(rc.chan(0).name, "level1:opacity")
        self.assertEqual(rc.par.renameto.eval(), "level1:opacity")

    def test_export_settings_autoname_root_and_flag(self):
        out = self._bind()
        rc = self._rc()
        # exportmethod is 'autoname', set as a literal VALUE
        self.assertEqual(rc.par.exportmethod.eval(), "autoname")
        # autoexportroot is '..' (the exporter's parent) for the same-parent case
        self.assertEqual(out["autoexportroot"], "..")
        self.assertEqual(rc.par.autoexportroot.eval(), "..")
        # the Export node flag is ON (a flag, not code)
        self.assertTrue(out["export_flag"])
        self.assertTrue(rc.export)

    def test_target_untouched_and_no_expression(self):
        before = self.target.par.opacity.eval()
        self._bind()
        # the target parameter is never written (the export overrides it live, without a write)
        self.assertEqual(self.target.par.opacity.eval(), before)
        # THE core invariant: NO parameter expression was ever written (data-only binding)
        self.assertEqual(_tdmock.EXPR_WRITES, [], "bind_chop must NEVER set a parameter expression")

    def test_rebind_reuses_same_rename_chop(self):
        self._bind()
        rc1 = self._rc()
        n_children = len(self.scene.ops["/project1"].children)
        # a second bind of the same CHOP UPDATES the same renameCHOP rather than creating a new one
        self._bind()
        self.assertEqual(len(self.scene.ops["/project1"].children), n_children)
        rc2 = self._rc()
        self.assertIs(rc2, rc1)
        self.assertEqual(rc2.par.renameto.eval(), "level1:opacity")

    def test_explicit_channel_sets_renamefrom(self):
        out = self._bind(channel="tx")
        rc = self._rc()
        # the explicit channel becomes the rename-from pattern (which source channel routes)
        self.assertEqual(rc.par.renamefrom.eval(), "tx")
        # the export name (renameto) is still "<target.name>:<par>"
        self.assertEqual(out["channel_name"], "level1:opacity")
        self.assertEqual(rc.par.renameto.eval(), "level1:opacity")

    def test_default_channel_is_wildcard(self):
        out = self._bind()
        self.assertEqual(self._rc().par.renamefrom.eval(), "*")
        self.assertEqual(out["channel_name"], "level1:opacity")

    def test_general_case_absolute_root(self):
        # target in a DIFFERENT parent -> the general autoname rule: root '/', absolute channel path.
        other = MockOp("/other", opType="containerCOMP", family="COMP", is_comp=True)
        self.scene.add(other)
        tgt2 = MockOp("/other/box1", opType="levelTOP", family="TOP",
                      pars={"opacity": MockPar("opacity", 1.0)})
        tgt2._parent = other
        self.scene.add(tgt2)
        out = animation.bind_chop({"chop": "/project1/null1", "op": "/other/box1", "par": "opacity"})
        self.assertEqual(out["autoexportroot"], "/")
        self.assertEqual(out["channel_name"], "other/box1:opacity")
        rc = self._rc()
        self.assertEqual(rc.chan(0).name, "other/box1:opacity")
        self.assertTrue(rc.export)

    def test_non_chop_source_rejected(self):
        with self.assertRaises(ValueError):
            animation.bind_chop({"chop": "/project1/level1", "op": "/project1/level1", "par": "opacity"})

    def test_missing_target_parameter_rejected(self):
        with self.assertRaises(ValueError):
            animation.bind_chop({"chop": "/project1/null1", "op": "/project1/level1", "par": "nope"})

    def test_code_sink_parameter_refused(self):
        # binding onto an audited code-eval sink param is refused (defense in depth, like set_par)
        sink = MockOp("/project1/grp1", opType="groupSOP", family="SOP",
                      pars={"filter": MockPar("filter", "")})
        sink._parent = self.scene.ops["/project1"]
        self.scene.add(sink)
        with self.assertRaises(PermissionError):
            animation.bind_chop({"chop": "/project1/null1", "op": "/project1/grp1", "par": "filter"})

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError):
            animation.bind_chop({"chop": "/nope", "op": "/project1/level1", "par": "opacity"})

    def test_no_expression_or_export_mode_sink_in_source(self):
        # STATIC guard: the animation lane must reach TD's CODE-FREE export ONLY -- never a parameter
        # expression, bindExpr, export-mode write, or the read-only exportOP setter.
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "handlers", "animation.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        for sink in (".expr", "setExpression", "exportOP", "bindExpr", ".mode", "ParMode"):
            self.assertNotIn(sink, src,
                             "animation.py must contain no expression/export-mode/code sink (found %r)" % sink)


if __name__ == "__main__":
    unittest.main()
