"""Boundary re-clamp tests for set_par / _set_lit (control_mod._clamp_par_value).

The executor is the boundary: it must bound numeric parameter values ITSELF, not trust the gateway's
typed-tool clamp (the generic set_par lowers a ParMap the gateway only checks for finiteness, not range).
`clamp_par_value` mirrors the generator's `num_bounds`: clamp to the HARD range only where TD declares a
clamp (Par.clampMin/clampMax), pass un-clamped params through (no legit value newly rejected), and refuse
non-finite numerics. These tests pin exactly that, plus that strings/menus/bools are never touched.
"""
import math
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control as control_mod


class TestClampParValueUnit(unittest.TestCase):
    def test_clamps_to_hard_max_when_td_clamps(self):
        p = MockPar("res", style="Int", clampMax=True, max=100.0)
        self.assertEqual(control_mod._clamp_par_value(p, 1e9), 100.0)

    def test_clamps_to_hard_min_when_td_clamps(self):
        p = MockPar("g", style="Float", clampMin=True, min=0.0)
        self.assertEqual(control_mod._clamp_par_value(p, -50), 0.0)

    def test_unclamped_param_passes_through(self):
        # resolution-style param: TD does NOT clamp -> a large value is legitimate, must pass unchanged.
        p = MockPar("resolutionw", style="Int", clampMin=False, clampMax=False, min=0.0, max=1920.0)
        self.assertEqual(control_mod._clamp_par_value(p, 7680), 7680)

    def test_in_range_value_unchanged(self):
        p = MockPar("x", style="Float", clampMin=True, clampMax=True, min=0.0, max=1.0)
        self.assertEqual(control_mod._clamp_par_value(p, 0.5), 0.5)

    def test_refuses_nan_and_inf(self):
        p = MockPar("x", style="Float")
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                control_mod._clamp_par_value(p, bad)

    def test_non_numeric_passthrough(self):
        p = MockPar("op", style="Str", clampMax=True, max=1.0)
        for v in ("uv0", "some/path", True, False):
            self.assertEqual(control_mod._clamp_par_value(p, v), v)  # strings/bools never clamped


class TestSetParAppliesClamp(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def _op(self, pars):
        # non-catalogued optype => allowlist deny-only fallback, so these synthetic clamp params exercise
        # pure _guard_par_value/clamp behavior (unrelated to the F-EXEC-1 allowlist, which is tested apart).
        op = MockOp("/project1/n", pars=pars, opType="testTOP", family="TOP")
        self.scene.add(op)
        return op

    def test_set_par_clamps_out_of_range(self):
        op = self._op({"lim": MockPar("lim", style="Float", clampMax=True, max=10.0)})
        r = control_mod.set_par({"op": op.path, "pars": {"lim": 999}})
        self.assertEqual(r["applied"]["lim"], 10.0)

    def test_set_par_reports_nonfinite_as_failed_not_fatal(self):
        op = self._op({"lim": MockPar("lim", style="Float")})
        r = control_mod.set_par({"op": op.path, "pars": {"lim": float("inf")}})
        self.assertIn("lim", r.get("failed", {}))          # reported, not raised
        self.assertNotIn("lim", r.get("applied", {}))

    def test_set_par_leaves_unclamped_large_value(self):
        op = self._op({"resolutionw": MockPar("resolutionw", style="Int")})  # clampMax False by default
        r = control_mod.set_par({"op": op.path, "pars": {"resolutionw": 4096}})
        self.assertEqual(r["applied"]["resolutionw"], 4096)


if __name__ == "__main__":
    unittest.main()
