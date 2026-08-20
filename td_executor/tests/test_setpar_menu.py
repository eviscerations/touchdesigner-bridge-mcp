"""set_par menu-token validation (driver-seat P1): a garbage menu token must be REFUSED into `failed`,
not silently snapped to a real token with ok:true. Plus the all_applied honesty flag."""
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control


class TestSetParMenuToken(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.op = MockOp("/project1/noise1", opType="noiseTOP", family="TOP",
                         pars={"type": MockPar("type", val="sparse", style="Menu", isMenu=True,
                                               menuNames=["sparse", "hermite", "harmon", "perlin2d"]),
                               "period": MockPar("period", val=1.0, style="Float")})
        self.scene.add(self.op)

    def test_garbage_menu_token_refused_not_coerced(self):
        r = control.set_par({"op": "/project1/noise1", "pars": {"type": "notarealtype"}})
        self.assertIn("type", r.get("failed", {}), "a garbage menu token must be refused into failed")
        self.assertIn("valid tokens", r["failed"]["type"], "the refusal must list the valid tokens")
        self.assertNotIn("type", r["applied"], "a refused token must NOT be applied")
        self.assertEqual(self.op.par.type.eval(), "sparse", "the param must be unchanged")
        self.assertFalse(r["all_applied"], "all_applied is false when a par was refused")

    def test_valid_menu_token_applied(self):
        r = control.set_par({"op": "/project1/noise1", "pars": {"type": "perlin2d"}})
        self.assertEqual(r["applied"].get("type"), "perlin2d")
        self.assertNotIn("failed", r)
        self.assertTrue(r["all_applied"], "all_applied is true when every par stuck")

    def test_non_menu_param_unaffected(self):
        r = control.set_par({"op": "/project1/noise1", "pars": {"period": 2.5}})
        self.assertEqual(r["applied"].get("period"), 2.5)
        self.assertTrue(r["all_applied"])

    def test_menu_index_int_left_to_td(self):
        # a menu can also be set by integer index -- numeric values are left to TD, never blocked
        r = control.set_par({"op": "/project1/noise1", "pars": {"type": 2}})
        self.assertEqual(r["applied"].get("type"), 2)
        self.assertTrue(r["all_applied"])

    def test_strmenu_custom_value_accepted(self):
        # StrMenu is an EDITABLE string field with menu SUGGESTIONS -- any typed value is valid, NOT
        # restricted to the suggestion tokens. Regression: renameto='mat0:colorr' was wrongly refused as an
        # "invalid menu token", which blocked ~1228 StrMenu params (renamefrom/renameto/scope/sendername/...).
        # renameCHOP.renameto is a real allowlisted StrMenu param (the choreography export chain uses it).
        rn = MockOp("/project1/rename1", opType="renameCHOP", family="CHOP",
                    pars={"renameto": MockPar("renameto", val="", style="StrMenu", isMenu=True,
                                              menuNames=["*"]),
                          "renamefrom": MockPar("renamefrom", val="*", style="StrMenu", isMenu=True,
                                                menuNames=["*"])})
        self.scene.add(rn)
        r = control.set_par({"op": "/project1/rename1", "pars": {"renameto": "mat0:colorr mat1:colorr"}})
        self.assertEqual(r["applied"].get("renameto"), "mat0:colorr mat1:colorr",
                         "a StrMenu accepts an arbitrary string, not just its suggestion tokens")
        self.assertNotIn("renameto", r.get("failed", {}), "a StrMenu custom value must NOT be refused")
        self.assertTrue(r["all_applied"])

    def test_mixed_partial_sets_all_applied_false(self):
        r = control.set_par({"op": "/project1/noise1",
                             "pars": {"period": 3.0, "type": "notarealtype"}})
        self.assertEqual(r["applied"].get("period"), 3.0)   # the good one still lands
        self.assertIn("type", r["failed"])                  # the bad token is refused
        self.assertFalse(r["all_applied"])                  # ok != all-applied


if __name__ == "__main__":
    unittest.main()
