"""probe_optype: the generic self-probe endpoint that ends the "operator not in the offline catalog"
lockout class. It must, for an ALLOWED optype, CREATE a throwaway instance -> INTROSPECT its typed
parameter schema -> DESTROY the scratch subtree (leaving NO node behind); REFUSE a code-carrying optype
via check_optype_allowed BEFORE creating anything; and, when the create itself fails, return ok:False
rather than raising. Mock-based (license-free); uses the _tdmock fake scene.
"""
import unittest

from td_executor.tests import _tdmock


def _alive_children(parent):
    """Children the mock still considers present (destroy() sets _destroyed=True)."""
    return [c for c in parent.children if not getattr(c, "_destroyed", False)]


class ProbeOptypeTest(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = _tdmock.install()
        self.probe = self.server._REGISTRY["probe_optype"]["fn"]
        self.project1 = self.scene.ops["/project1"]

    # ---- happy path: create -> introspect -> destroy, nothing left behind --------------------------
    def test_allowed_optype_creates_introspects_and_destroys(self):
        before = len(_alive_children(self.project1))

        out = self.probe({"optype": "noiseTOP"})

        self.assertTrue(out["ok"])
        self.assertEqual(out["optype"], "noiseTOP")
        self.assertEqual(out["family"], "TOP")
        self.assertEqual(out["maxinputs"], 1)
        # a real param list came back, catalog-shaped
        self.assertGreater(out["param_count"], 0)
        self.assertEqual(out["param_count"], len(out["params"]))
        names = {p["name"] for p in out["params"]}
        self.assertIn("period", names)
        self.assertIn("type", names)
        # every param carries the catalog fields (name/style/default/norm/hard/tokens/tuplet)
        for p in out["params"]:
            for key in ("label", "name", "style", "default", "norm", "hard", "tokens", "tuplet"):
                self.assertIn(key, p)
        # the menu param exposes its vocabulary as tokens; a plain numeric has tokens=None
        by_name = {p["name"]: p for p in out["params"]}
        self.assertEqual(by_name["type"]["style"], "Menu")
        self.assertEqual(by_name["type"]["tokens"], ["sparse", "hermite", "harmon"])
        self.assertIsNone(by_name["period"]["tokens"])
        self.assertEqual(by_name["period"]["norm"], [0.0, 10.0])
        self.assertEqual(by_name["period"]["hard"], [0.0, 1.0, False, False])

        # NO node left behind: the alive-child count is exactly what it was before the probe.
        self.assertEqual(len(_alive_children(self.project1)), before)

    def test_scratch_subtree_is_destroyed(self):
        # After the probe, any scratch COMP it created must be destroyed (not merely orphaned).
        self.probe({"optype": "noiseTOP"})
        scratch = [c for c in self.project1.children if c.name.startswith("__mcp_probe_")]
        self.assertTrue(scratch, "probe should have created a scratch COMP")
        for c in scratch:
            self.assertTrue(c._destroyed, "scratch COMP %s was not destroyed" % c.name)
            # and the temp op created inside it is destroyed too
            for gc in c.children:
                self.assertTrue(gc._destroyed, "temp op %s left behind" % gc.name)

    # ---- boundary: code-carrying optype refused BEFORE any create ----------------------------------
    def test_banned_optype_refused_before_create(self):
        before = len(_alive_children(self.project1))
        for banned in ("executeDAT", "scriptDAT", "scriptCHOP", "cplusplusTOP"):
            with self.assertRaises(PermissionError):
                self.probe({"optype": banned})
        # nothing was created while refusing
        self.assertEqual(len(_alive_children(self.project1)), before)

    def test_evaluatedat_exact_denylist_refused(self):
        # evaluateDAT carries no script/execute/cplusplus marker; it is on the EXACT evaluator denylist.
        with self.assertRaises(PermissionError):
            self.probe({"optype": "evaluateDAT"})

    # ---- create failure returns ok:False rather than raising ---------------------------------------
    def test_create_failure_returns_ok_false(self):
        before = len(_alive_children(self.project1))

        out = self.probe({"optype": "probeFailTOP"})

        self.assertFalse(out["ok"])
        self.assertEqual(out["optype"], "probeFailTOP")
        self.assertIn("error", out)
        # even on a failed create, the scratch container is cleaned up (nothing left behind)
        self.assertEqual(len(_alive_children(self.project1)), before)

    # ---- missing arg is a clear error --------------------------------------------------------------
    def test_missing_optype_raises_valueerror(self):
        with self.assertRaises(ValueError):
            self.probe({})

    # ---- no parameter EXPRESSION is ever written (data-only invariant) -----------------------------
    def test_probe_writes_no_expression(self):
        self.probe({"optype": "noiseTOP"})
        self.assertEqual(_tdmock.EXPR_WRITES, [], "probe_optype must never write a parameter expression")


if __name__ == "__main__":
    unittest.main()
