"""P3 parameter-EXPRESSION handler (offline, via _tdmock) -- the second code lane, mirroring the GLSL
handler test part-for-part:
  (a) consent OFF  -> set_expr refuses (PermissionError) and writes NOTHING (EXPR_WRITES stays empty);
  (b) consent ON + a GREEN expression -> the validated expr is written (EXPR_WRITES contains exactly it)
      and the parameter is in expression mode; every GREEN-corpus idiom is accepted;
  (c) consent ON + each RED-corpus expression -> refused (ValueError) BEFORE any mutation, nothing written;
  (d) DELIVERY FENCE -> set_expr onto a denied code-pointer param (callbacks) is STILL refused via the
      unchanged check_par_allowed, even with consent ON + a trivially-valid expression -> nothing written;
  (e) validate_expr dry-run needs NO consent and mutates nothing;
  (f) SOURCE SCAN -> handlers/expr.py is the ONLY handler file that writes `.expr`.

Consent is forced ON by monkeypatching the handler's _read_allow_expr (default-off otherwise). The GREEN/RED
corpora are imported from the (already unit-tested) validator so the handler test tracks the same trust anchor.
"""
import glob
import os
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import expr as expr_mod
from td_executor.expr_validator import _MUST_PASS, _MUST_FAIL


_GREEN = "me.time.frame"   # a representative GREEN idiom for the exact-write assertion


def _make_target(scene, path="/project1/level1", par="opacity"):
    top = MockOp(path, pars={par: MockPar(par, 1.0)}, opType="levelTOP", family="TOP")
    scene.add(top)
    return top


class TestExprHandler(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.target = _make_target(self.scene)
        self._saved_consent = expr_mod._read_allow_expr

    def tearDown(self):
        expr_mod._read_allow_expr = self._saved_consent

    def _consent(self, on):
        expr_mod._read_allow_expr = (lambda: on)

    # (a) consent OFF -> refuse, nothing written -------------------------------------------------
    def test_consent_off_refuses_and_writes_nothing(self):
        self._consent(False)
        with self.assertRaises(PermissionError):
            expr_mod.set_expr({"op": self.target.path, "par": "opacity", "source": _GREEN})
        self.assertEqual(_tdmock.EXPR_WRITES, [], "consent off must write no expression")
        self.assertEqual(self.target.par.opacity.mode, "constant")

    # (b) consent ON + GREEN -> validated expr written, mode expression ---------------------------
    def test_consent_on_green_expression_applies(self):
        self._consent(True)
        out = expr_mod.set_expr({"op": self.target.path, "par": "opacity", "source": _GREEN})
        self.assertTrue(out["applied"])
        self.assertEqual(out["op"], self.target.path)
        self.assertEqual(out["par"], "opacity")
        self.assertEqual(out["mode"], "expression")
        # THE single sanctioned .expr write: exactly the validated source landed on the target par.
        self.assertEqual(_tdmock.EXPR_WRITES, [("opacity", _GREEN)])
        self.assertEqual(self.target.par.opacity.mode, "expression")

    def test_every_green_corpus_expression_accepted(self):
        self._consent(True)
        for src in _MUST_PASS:
            del _tdmock.EXPR_WRITES[:]
            tgt = _make_target(self.scene, path="/project1/lv_%d" % abs(hash(src)))
            out = expr_mod.set_expr({"op": tgt.path, "par": "opacity", "source": src})
            self.assertTrue(out["applied"], "GREEN must apply: %r" % src)
            self.assertEqual(_tdmock.EXPR_WRITES, [("opacity", src)], "GREEN must write exactly: %r" % src)

    # (c) consent ON + each RED expression -> refused, nothing written ----------------------------
    def test_every_red_corpus_expression_refused_and_writes_nothing(self):
        self._consent(True)
        for src, _rule in _MUST_FAIL:
            del _tdmock.EXPR_WRITES[:]
            with self.assertRaises(ValueError, msg="RED must be refused: %r" % src):
                expr_mod.set_expr({"op": self.target.path, "par": "opacity", "source": src})
            self.assertEqual(_tdmock.EXPR_WRITES, [], "RED must write nothing: %r" % src)
            self.assertEqual(self.target.par.opacity.mode, "constant",
                             "RED must not change mode: %r" % src)

    # (d) DELIVERY FENCE -- a denied code-pointer param is still refused --------------------------
    def test_delivery_fence_code_pointer_param_refused(self):
        self._consent(True)
        timer = MockOp("/project1/timer1", pars={"callbacks": MockPar("callbacks", "")},
                       opType="timerCHOP", family="CHOP")
        self.scene.add(timer)
        # even with consent ON and a trivially-valid expression, writing onto `callbacks` is refused by
        # the UNCHANGED universal code-pointer guard -- the expr lane opened no code-pointer hole.
        with self.assertRaises(PermissionError):
            expr_mod.set_expr({"op": timer.path, "par": "callbacks", "source": "1"})
        self.assertEqual(_tdmock.EXPR_WRITES, [], "code-pointer refusal must write nothing")

    def test_delivery_fence_inline_code_sink_param_refused(self):
        # an inline code-sink (evaluateDAT.expr) is likewise refused via check_par_allowed, nothing written.
        self._consent(True)
        ev = MockOp("/project1/eval1", pars={"expr": MockPar("expr", "")}, opType="evaluateDAT", family="DAT")
        self.scene.add(ev)
        with self.assertRaises(PermissionError):
            expr_mod.set_expr({"op": ev.path, "par": "expr", "source": "1"})
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_missing_parameter_refused(self):
        self._consent(True)
        with self.assertRaises(ValueError):
            expr_mod.set_expr({"op": self.target.path, "par": "nope", "source": _GREEN})
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_non_string_source_refused(self):
        self._consent(True)
        with self.assertRaises(ValueError):
            expr_mod.set_expr({"op": self.target.path, "par": "opacity", "source": 123})
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    # (e) validate_expr dry-run -- no consent, no mutation ----------------------------------------
    def test_validate_expr_dry_run_needs_no_consent(self):
        self._consent(False)   # dry-run must work with consent OFF
        ok = expr_mod.validate_expr_endpoint({"source": _GREEN})
        self.assertEqual(ok, {"ok": True})
        bad = expr_mod.validate_expr_endpoint({"source": "().__class__.__bases__[0].__subclasses__()"})
        self.assertFalse(bad["ok"])
        self.assertIn("rule", bad)
        # nothing was written by the dry-run
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_validate_expr_registered_no_auth(self):
        self.assertIn("validate_expr", self.server._REGISTRY)
        self.assertIn("set_expr", self.server._REGISTRY)
        self.assertFalse(self.server._REGISTRY["validate_expr"]["auth"],
                         "validate_expr is a read-only dry-run (auth=False)")
        self.assertTrue(self.server._REGISTRY["set_expr"]["auth"],
                        "set_expr mutates state -> auth required")

    # (f) SOURCE SCAN -- expr.py is the ONLY handler that writes .expr ----------------------------
    def test_expr_py_is_the_only_handler_writing_expr(self):
        hdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "handlers")
        wrote = []
        for path in sorted(glob.glob(os.path.join(hdir, "*.py"))):
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            if ".expr" in src:
                wrote.append(os.path.basename(path))
        self.assertEqual(wrote, ["expr.py"],
                         "expr.py must be the SOLE handler touching .expr; found %r" % wrote)
        # and the sanctioned write itself is present in expr.py.
        with open(os.path.join(hdir, "expr.py"), "r", encoding="utf-8") as f:
            self.assertIn("p.expr = source", f.read())


if __name__ == "__main__":
    unittest.main()
