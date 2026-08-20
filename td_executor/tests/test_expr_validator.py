"""P3 parameter-expression validator: the full red-team corpus (_selftest) must report ZERO failures, plus
explicit must-pass / must-fail assertions for the headline escape classes (CPython dunder-traversal, TD-native
mod/run/ext code reaches, import/eval/exec/open, comprehension/lambda/walrus/f-string, unicode-homoglyph, and
the DoS caps). Standalone -- expr_validator imports nothing from td/op."""
import unittest

from td_executor.expr_validator import (
    validate_expr, ExprValidationError, _selftest, _MUST_PASS, _MUST_FAIL,
)


class TestExprValidator(unittest.TestCase):
    # ---- the corpus is the trust anchor -----------------------------------------------------------
    def test_selftest_zero_failures(self):
        res = _selftest()
        self.assertEqual(res["failed"], 0, "must-pass/must-fail corpus failures: %s" % res["details"])
        self.assertGreater(res["passed"], 0)

    def test_selftest_reports_no_mismatch(self):
        # Every RED case must fail for exactly the rule the corpus documents (not merely 'some' rule).
        res = _selftest()
        self.assertEqual(res["details"], [], "unexpected rule mismatches: %s" % res["details"])

    def test_green_corpus_all_pass(self):
        for src in _MUST_PASS:
            self.assertIsNone(validate_expr(src, "eval"), "GREEN case should validate: %r" % src)

    def test_red_corpus_all_reject_with_rule(self):
        for src, want in _MUST_FAIL:
            with self.assertRaises(ExprValidationError, msg="RED case should reject: %r" % src) as cm:
                validate_expr(src, "eval")
            if want:
                self.assertEqual(getattr(cm.exception, "rule", None), want,
                                 "RED case %r expected rule %r" % (src, want))

    # ---- helpers ----------------------------------------------------------------------------------
    def _reject(self, src, rule=None):
        with self.assertRaises(ExprValidationError) as cm:
            validate_expr(src, "eval")
        if rule is not None:
            self.assertEqual(getattr(cm.exception, "rule", None), rule)

    def _accept(self, src):
        self.assertIsNone(validate_expr(src, "eval"))

    # ---- headline GREEN cases ---------------------------------------------------------------------
    def test_arithmetic_passes(self):
        self._accept("me.time.seconds * 0.5 + 1")

    def test_op_subscript_passes(self):
        self._accept("op('lfo1')['chan1']")

    def test_math_call_passes(self):
        self._accept("math.sin(absTime.seconds)")

    def test_clamp_style_passes(self):
        self._accept("min(max(op('ctrl')['gain'], 0), 1)")

    def test_ternary_passes(self):
        self._accept("1 if op('sw')['on'] > 0.5 else 0")

    def test_string_concat_passes(self):
        self._accept("'sect_' + str(me.digits)")

    # ---- headline RED classes (assert the exact rule) ---------------------------------------------
    def test_dunder_subclasses_walk_rejected(self):
        self._reject("().__class__.__bases__[0].__subclasses__()", "call.not_allowed")

    def test_bare_dunder_attr_rejected(self):
        self._reject("me.__class__", "attr.dunder")

    def test_getattr_rejected(self):
        self._reject("getattr(me,'__cl'+'ass__')", "call.not_allowed")

    def test_import_rejected(self):
        self._reject("__import__('os').system('calc')", "call.not_allowed")

    def test_eval_rejected(self):
        self._reject("eval('1')", "call.not_allowed")

    def test_open_rejected(self):
        self._reject("open('C:/secret','r')", "call.not_allowed")

    def test_td_mod_reach_rejected(self):
        self._reject("mod('evildat').run()", "call.not_allowed")

    def test_td_run_reach_rejected(self):
        self._reject("run('__import__(\\'os\\')')", "call.not_allowed")

    def test_td_ext_reach_rejected(self):
        self._reject("op('x').ext.MyClass.method()", "call.not_allowed")

    def test_comprehension_rejected(self):
        self._reject("[x for x in ().__class__.__subclasses__()]", "node.disallowed")

    def test_lambda_rejected(self):
        self._reject("(lambda: ().__class__)()", "call.not_allowed")

    def test_walrus_rejected(self):
        self._reject("(x:=me).digits", "node.disallowed")

    def test_fstring_rejected(self):
        self._reject("f'{me.__class__}'", "node.disallowed")

    def test_subscript_call_escape_rejected(self):
        # method call reached through a subscripted live object
        self._reject("op('x').par.file.eval()", "call.not_allowed")

    def test_unicode_homoglyph_rejected(self):
        self._reject("m\u0435.digits", "chars.non_ascii_or_control")   # Cyrillic 'e'

    def test_deep_attr_chain_rejected(self):
        self._reject("me.par.par.par.par.par", "attr.chain_too_deep")

    def test_keyword_arg_rejected(self):
        self._reject("op('x', y=1)", "call.keywords_banned")

    # ---- DoS / bounds -----------------------------------------------------------------------------
    def test_paren_depth_bomb_rejected(self):
        # collapses to a single Constant in the AST -> must be caught by the raw bracket-nesting cap.
        self._reject("(" * 200 + "1" + ")" * 200, "bounds.nesting_too_deep")

    def test_oversized_source_rejected(self):
        self._reject("1" * 600, "bounds.too_long")

    def test_pow_hang_nested_rejected(self):
        self._reject("9**9**9", "pow.nested")           # 369M-digit int host-hang, closed by the Pow guard

    def test_pow_hang_big_exponent_rejected(self):
        self._reject("2 ** 99999999999", "pow.exponent")

    def test_pow_dynamic_exponent_rejected(self):
        self._reject("2 ** op('x')['n']", "pow.exponent")

    def test_small_power_passes(self):
        self._accept("me.digits ** 2")                  # legitimate bounded power still validates

    def test_call_count_bomb_rejected(self):
        self._reject("op('a')" + "".join("+op('a')" for _ in range(50)))

    def test_multiline_rejected(self):
        self._reject("me.digits\n+ 1", "bounds.multiline")

    # ---- mode / profile gate ----------------------------------------------------------------------
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ExprValidationError) as cm:
            validate_expr("me.digits", "exec")
        self.assertEqual(getattr(cm.exception, "rule", None), "mode.not_allowed")

    def test_empty_source_rejected(self):
        self._reject("", "source.empty")


if __name__ == "__main__":
    unittest.main()
