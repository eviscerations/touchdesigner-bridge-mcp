"""inspect: READ-ONLY deep-state introspection of ONE operator (the 'why isn't my binding working /
what state is this node in' tool). Proves the endpoint is registered, reports node flags (incl. the
CHOP Export Flag), a CHOP's live channel values, a DAT's cell grid, and a parameter's mode + export
source -- and that it mutates NOTHING (no .expr write, no flag flip, no value change)."""
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar, MockChan
from td_executor.handlers import diagnostics


class TestInspect(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_registered_endpoint(self):
        self.assertIn("inspect", self.server._REGISTRY)

    def test_chop_flags_and_channels(self):
        chop = MockOp("/project1/lfo1", opType="lfoCHOP", family="CHOP",
                      channels=[MockChan("chan1", 0.5), MockChan("tx", -1.25)])
        self.scene.add(chop)
        out = diagnostics.inspect({"op": "/project1/lfo1"})
        self.assertEqual(out["path"], "/project1/lfo1")
        self.assertEqual(out["type"], "lfoCHOP")
        self.assertEqual(out["family"], "CHOP")
        # the CHOP Export Flag is reported among the node flags (the animation-debugging signal)
        self.assertIn("export", out["flags"])
        self.assertIs(out["flags"]["export"], False)
        # channels carry their current (eval'd) values, JSON-safe
        self.assertEqual(out["channels"],
                         [{"name": "chan1", "val": 0.5}, {"name": "tx", "val": -1.25}])
        # a CHOP is not a DAT -> no cell grid
        self.assertNotIn("rows", out)

    def test_export_flag_true_reported(self):
        chop = MockOp("/project1/null1", opType="nullCHOP", family="CHOP",
                      channels=[MockChan("c", 1.0)])
        chop.export = True   # the Export Flag is ON (e.g. after bind_chop)
        self.scene.add(chop)
        out = diagnostics.inspect({"op": "/project1/null1"})
        self.assertTrue(out["flags"]["export"])

    def test_par_mode_and_export_source(self):
        target = MockOp("/project1/level1", opType="levelTOP", family="TOP",
                        pars={"opacity": MockPar("opacity", 0.8, mode="export",
                                                 exportSource="null1:chan1")})
        self.scene.add(target)
        out = diagnostics.inspect({"op": "/project1/level1", "par": "opacity"})
        self.assertEqual(out["par"]["name"], "opacity")
        self.assertEqual(out["par"]["val"], 0.8)
        # mode reveals the parameter is in Export mode; exportSource reveals what drives it
        self.assertEqual(out["par"]["mode"], "export")
        self.assertEqual(out["par"]["exportSource"], "null1:chan1")

    def test_par_export_source_op_reports_path(self):
        # when the export source is an OP object, its .path is reported (not a python repr)
        driver = MockOp("/project1/null1", opType="nullCHOP", family="CHOP")
        self.scene.add(driver)
        target = MockOp("/project1/level1", opType="levelTOP", family="TOP",
                        pars={"opacity": MockPar("opacity", 1.0, exportOP=driver)})
        self.scene.add(target)
        out = diagnostics.inspect({"op": "/project1/level1", "par": "opacity"})
        self.assertEqual(out["par"]["exportOP"], "/project1/null1")

    def test_dat_rows(self):
        dat = MockOp("/project1/table1", opType="tableDAT", family="DAT")
        dat.rows = [["channel", "path", "parameter"], ["chan1", "/project1/level1", "opacity"]]
        self.scene.add(dat)
        out = diagnostics.inspect({"op": "/project1/table1"})
        self.assertEqual(out["rows"],
                         [["channel", "path", "parameter"], ["chan1", "/project1/level1", "opacity"]])
        self.assertNotIn("channels", out)

    def test_missing_par_reported_not_raised(self):
        op = MockOp("/project1/level1", opType="levelTOP", family="TOP",
                    pars={"opacity": MockPar("opacity", 1.0)})
        self.scene.add(op)
        out = diagnostics.inspect({"op": "/project1/level1", "par": "nope"})
        self.assertIn("error", out["par"])   # defensive: reports, never raises on a missing attr

    def test_read_only_no_mutation(self):
        chop = MockOp("/project1/lfo1", opType="lfoCHOP", family="CHOP", channels=[MockChan("c", 0.3)])
        chop.export = True
        self.scene.add(chop)
        target = MockOp("/project1/level1", opType="levelTOP", family="TOP",
                        pars={"opacity": MockPar("opacity", 0.5, mode="constant")})
        self.scene.add(target)
        diagnostics.inspect({"op": "/project1/lfo1"})
        diagnostics.inspect({"op": "/project1/level1", "par": "opacity"})
        # THE invariant: no parameter expression ever written (the data-only tripwire), and no observed
        # state changed by the read (flag not flipped, value + mode unchanged).
        self.assertEqual(_tdmock.EXPR_WRITES, [])
        self.assertTrue(chop.export)
        self.assertEqual(target.par.opacity.eval(), 0.5)
        self.assertEqual(target.par.opacity.mode, "constant")

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError):
            diagnostics.inspect({"op": "/nope"})


if __name__ == "__main__":
    unittest.main()
