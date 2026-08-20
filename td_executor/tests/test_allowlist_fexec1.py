"""F-EXEC-1: the executor's param gate is an ALLOWLIST, not just a denylist.

check_par_allowed now fails CLOSED: a driver-supplied (optype, param) on a CATALOGUED operator is accepted
only when it is a known parameter -- an exact catalog member, a sequence-block index whose block-0 form is a
catalog data param (and not a sink), or a live custom parameter. An unknown / newer-TD / un-probed param is
REFUSED. Uncatalogued optypes and a failed catalog load revert to deny-only (no capability regression).

These tests pin every branch, and the end-to-end set_par integration (unknown param -> failed, not fatal;
known param still applies). The code lanes' reliance on check_par_allowed (glslTOP lane params, set_expr on a
data param) is covered by test_glsl_handler / test_expr_handler; here we assert the specific lane params the
allowlist must permit are catalog members so those lanes cannot be silently broken.
"""
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control


class _Custom:
    """Minimal live-Par stand-in exposing isCustom (the only attribute check_par_allowed reads for the
    custom-parameter allowance)."""
    def __init__(self, is_custom):
        self.isCustom = is_custom


class TestAllowlistBranches(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_known_catalog_param_allowed(self):
        self.assertEqual(self.server.check_par_allowed("blurTOP", "size"), "size")
        self.assertEqual(self.server.check_par_allowed("moviefileinTOP", "file"), "file")

    def test_unknown_param_on_catalogued_op_fails_closed(self):
        for ot, par in (("blurTOP", "totallybogusparam"), ("moviefileinTOP", "zzznope"),
                        ("levelTOP", "gamma")):   # real levelTOP has gamma1/2/3, not 'gamma'
            with self.assertRaises(PermissionError, msg="%s.%s must fail closed" % (ot, par)):
                self.server.check_par_allowed(ot, par)

    def test_sequence_block_data_param_allowed_by_generalization(self):
        # constantCHOP.const0name is a catalog data param; block 1 (const1name) was never probed but
        # generalizes to const0name -> allowed.
        self.assertEqual(self.server.check_par_allowed("constantCHOP", "const1name"), "const1name")
        self.assertEqual(self.server.check_par_allowed("constantCHOP", "const7name"), "const7name")

    def test_sequence_block_sink_still_denied(self):
        # A block-N SINK must never slip through the generalization (block-0 form is a denied sink).
        for ot, par in (("tableDAT", "fills1expr"), ("insertDAT", "replace4expr"),
                        ("expressionCHOP", "expr2expr")):
            with self.assertRaises(PermissionError):
                self.server.check_par_allowed(ot, par)

    def test_bogus_indexed_param_not_allowed(self):
        # An indexed name whose block-0 form is NOT a catalog param stays refused (no false generalization).
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("blurTOP", "bogus1param")

    def test_uncatalogued_optype_allows_data_denies_code(self):
        # S2 closure: an un-catalogued op has no allowlist, so a plain DATA param is still accepted (the op
        # stays fully creatable + configurable), but the code boundary is now FAIL-CLOSED there too:
        #   - a benign data param passes,
        #   - a universal code-POINTER (callbacks) is denied,
        #   - a known code-sink SHAPE is denied by Layer 1 (universal patterns), and
        #   - any other code-indicator-token param is denied by Layer 2.
        self.assertEqual(self.server.check_par_allowed("someFutureXYZOP", "anyparam"), "anyparam")
        self.assertEqual(self.server.check_par_allowed("someFutureXYZOP", "resolution"), "resolution")
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("someFutureXYZOP", "callbacks")
        for par in ("cellexpr", "rowexpr", "tscript", "aend", "fills3expr"):  # Layer 1 universal shapes
            with self.assertRaises(PermissionError, msg="Layer1 must deny %r on any optype" % par):
                self.server.check_par_allowed("someFutureXYZOP", par)
        for par in ("myscript", "onCallback", "customexpr", "pyexpr0"):       # Layer 2 code-token names
            with self.assertRaises(PermissionError, msg="Layer2 must deny %r on un-catalogued op" % par):
                self.server.check_par_allowed("someFutureXYZOP", par)

    def test_uncatalogued_custom_code_token_param_allowed(self):
        # A live CUSTOM param on an un-catalogued op is the user's own data (its .val is never auto-evaluated),
        # so it is allowed even if its name carries a code-indicator token -- capability preserved.
        self.assertEqual(
            self.server.check_par_allowed("someFutureXYZOP", "myCallback", _Custom(True)), "myCallback")
        # ... but a NON-custom code-token param on the same op stays fail-closed.
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("someFutureXYZOP", "myCallback", _Custom(False))

    def test_layer1_universal_patterns_do_not_touch_catalogued_data(self):
        # The universal Layer-1 patterns must NOT block a benign catalogued param, and the two deliberately
        # EXCLUDED shapes (bare 'expr' Sequence header, 'expression' Toggle) must still work on their ops.
        self.assertEqual(self.server.check_par_allowed("expressionCHOP", "expr"), "expr")
        self.assertEqual(self.server.check_par_allowed("parameterDAT", "expression"), "expression")

    def test_custom_param_allowed_only_with_live_custom_par(self):
        # An unknown param name is refused without a live par ...
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("blurTOP", "Mycustomknob")
        # ... allowed when the live Par reports isCustom=True ...
        self.assertEqual(
            self.server.check_par_allowed("blurTOP", "Mycustomknob", _Custom(True)), "Mycustomknob")
        # ... and still refused when the live Par is not custom.
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("blurTOP", "Mycustomknob", _Custom(False))

    def test_custom_par_cannot_override_a_deny(self):
        # deny-first: a universal code-pointer stays denied even if presented as a custom par.
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("timerCHOP", "callbacks", _Custom(True))

    def test_catalog_load_failure_reverts_to_denylist(self):
        saved = self.server._ALLOW_PARAMS
        self.server._ALLOW_PARAMS = None
        try:
            # deny-only: an unknown param is now allowed (fail-soft), but sinks/pointers stay denied.
            self.assertEqual(self.server.check_par_allowed("blurTOP", "totallybogusparam"),
                             "totallybogusparam")
            with self.assertRaises(PermissionError):
                self.server.check_par_allowed("evaluateDAT", "expr")
            with self.assertRaises(PermissionError):
                self.server.check_par_allowed("timerCHOP", "callbacks")
        finally:
            self.server._ALLOW_PARAMS = saved


class TestAllowlistSetParIntegration(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_unknown_param_lands_in_failed_known_applies(self):
        op = MockOp("/project1/blur1",
                    pars={"size": MockPar("size", val=1.0, style="Float"),
                          "bogusxyz": MockPar("bogusxyz", val=0.0, style="Float")},
                    opType="blurTOP", family="TOP")
        self.scene.add(op)
        out = control.set_par({"op": "/project1/blur1", "pars": {"size": 5.0, "bogusxyz": 9.0}})
        self.assertEqual(out["applied"], {"size": 5.0})
        self.assertIn("bogusxyz", out["failed"])
        self.assertIn("not a known data parameter", out["failed"]["bogusxyz"])
        self.assertFalse(out["all_applied"])
        self.assertEqual(_tdmock.EXPR_WRITES, [])


class TestLaneParamsRemainAllowlisted(unittest.TestCase):
    """The two code lanes route their own writes through check_par_allowed; assert the exact params they set
    are catalog members so an allowlist flip can never silently break a lane."""
    def setUp(self):
        self.server, _ = install()

    def test_glsl_lane_params_allowed(self):
        for p in ("pixeldat", "resolutionw", "resolutionh", "npasses", "outputresolution"):
            self.assertEqual(self.server.check_par_allowed("glslTOP", p), p)


if __name__ == "__main__":
    unittest.main()
