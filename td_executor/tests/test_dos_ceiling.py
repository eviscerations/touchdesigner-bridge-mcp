"""F-DOS-1: the ENFORCED magnitude ceiling. The advisory governor only FLAGS heavy work; this test covers
the HARD refuse that stops a driver-killing resolution/instance/pass value from reaching TD via the generic
set_par -- overridable only by the human-gated arm.json `allow_highres` consent."""
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor import governor
from td_executor.handlers import control


class TestGovernorCeilingUnit(unittest.TestCase):
    def test_ceiling_lookup_scoped_to_real_magnitude_params(self):
        self.assertEqual(governor.magnitude_ceiling_for("resolutionw")[0], governor.CEIL_RES_DIM)
        self.assertEqual(governor.magnitude_ceiling_for("resolutionh")[0], governor.CEIL_RES_DIM)
        self.assertEqual(governor.magnitude_ceiling_for("npasses")[0], governor.CEIL_PASSES)
        self.assertEqual(governor.magnitude_ceiling_for("instancecount")[0], governor.CEIL_INSTANCES)
        # NOT a magnitude param -> never enforced (no false refusal on a look-alike like a COMP's w/h).
        self.assertEqual(governor.magnitude_ceiling_for("tx"), (None, None))
        self.assertEqual(governor.magnitude_ceiling_for("w"), (None, None))

    def test_enforce_refuses_catastrophic(self):
        with self.assertRaises(ValueError):
            governor.enforce_magnitude_ceiling("resolutionw", 100000)
        with self.assertRaises(ValueError):
            governor.enforce_magnitude_ceiling("npasses", 1000)
        with self.assertRaises(ValueError):
            governor.enforce_magnitude_ceiling("instancecount", 50_000_000)

    def test_enforce_allows_legit_and_nonmagnitude(self):
        governor.enforce_magnitude_ceiling("resolutionw", 3840)          # 4K delivery
        governor.enforce_magnitude_ceiling("resolutionh", governor.CEIL_RES_DIM)  # exactly at the cap = ok
        governor.enforce_magnitude_ceiling("tx", 1e9)                    # not a magnitude param -> pass
        governor.enforce_magnitude_ceiling("resolutionw", "notanumber")  # non-numeric -> pass

    def test_override_bypasses(self):
        governor.enforce_magnitude_ceiling("resolutionw", 100000, allow_override=True)


class TestSetParCeiling(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.top = MockOp("/project1/blur1",
                          pars={"resolutionw": MockPar("resolutionw", 256, style="Int"),
                                "resolutionh": MockPar("resolutionh", 256, style="Int")},
                          opType="blurTOP", family="TOP")
        self.scene.add(self.top)
        # Pin the consent OFF deterministically (don't depend on the real arm.json).
        self._orig = control._read_allow_highres
        control._read_allow_highres = lambda: False

    def tearDown(self):
        control._read_allow_highres = self._orig

    def test_catastrophic_resolution_refused_not_applied(self):
        out = control.set_par({"op": "/project1/blur1", "pars": {"resolutionw": 100000}})
        self.assertIn("resolutionw", out.get("failed", {}))
        self.assertIn("ceiling", out["failed"]["resolutionw"])
        self.assertNotIn("resolutionw", out.get("applied", {}))

    def test_legit_4k_resolution_applies(self):
        out = control.set_par({"op": "/project1/blur1", "pars": {"resolutionw": 3840}})
        self.assertEqual(out.get("applied", {}).get("resolutionw"), 3840)

    def test_override_allows_catastrophic(self):
        control._read_allow_highres = lambda: True
        out = control.set_par({"op": "/project1/blur1", "pars": {"resolutionw": 100000}})
        self.assertEqual(out.get("applied", {}).get("resolutionw"), 100000)


if __name__ == "__main__":
    unittest.main()
