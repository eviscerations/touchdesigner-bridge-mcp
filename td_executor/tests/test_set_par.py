"""set_par sets VALUES only, never expressions (the core data-only invariant), and reports per-par
failures instead of aborting. Parity with the Houdini executor's "values-only param setting" guard.

Two independent proofs that no code path sets a parameter's EXPRESSION:
  * RUNTIME: the mock parameter records any `.expr` write to EXPR_WRITES; it must stay empty.
  * STATIC : control.py's source contains no `.expr =` / `setExpression` sink (belt-and-suspenders,
             mirroring the Houdini executor's no-raw-sink source scan)."""
import os
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control


class TestSetParValuesOnly(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.op = MockOp(
            "/project1/level1",
            pars={
                "opacity": MockPar("opacity", val=1.0),
                "gamma": MockPar("gamma", val=1.0),
                "readonly": MockPar("readonly", val=0.0, raises=True),
            },
            # a non-catalogued optype => the F-EXEC-1 allowlist reverts to deny-only, so these synthetic
            # params exercise pure set_par value/failure mechanics (the allowlist has its own dedicated tests).
            opType="testTOP",
        )
        self.scene.add(self.op)

    def test_sets_values_and_returns_applied(self):
        out = control.set_par({"op": "/project1/level1", "pars": {"opacity": 0.5, "gamma": 2.2}})
        self.assertEqual(out["path"], "/project1/level1")
        self.assertEqual(out["applied"], {"opacity": 0.5, "gamma": 2.2})
        self.assertNotIn("failed", out)
        # never touched expression mode
        self.assertEqual(_tdmock.EXPR_WRITES, [], "set_par must NEVER set a parameter expression")

    def test_per_par_failure_is_reported_not_fatal(self):
        out = control.set_par({
            "op": "/project1/level1",
            "pars": {"opacity": 0.25, "readonly": 9.0, "nonexistent": 1},
        })
        # the good par still applied
        self.assertEqual(out["applied"], {"opacity": 0.25})
        # the failures are surfaced, not raised
        self.assertIn("readonly", out["failed"])
        self.assertEqual(out["failed"]["nonexistent"], "no such parameter")
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_pars_must_be_an_object(self):
        with self.assertRaises(ValueError):
            control.set_par({"op": "/project1/level1", "pars": [1, 2, 3]})

    def test_no_expression_sink_in_source(self):
        # STATIC guard: the control surface must contain no expression-writing sink.
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "handlers", "control.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        for sink in (".expr", "setExpression", "exportOP", "bindExpr"):
            self.assertNotIn(sink, src,
                             "control.py must contain no expression/code sink (found %r)" % sink)


if __name__ == "__main__":
    unittest.main()
