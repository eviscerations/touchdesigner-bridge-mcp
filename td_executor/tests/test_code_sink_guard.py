"""check_par_allowed / set_par: the executor must REFUSE to set operator parameters whose VALUE
TouchDesigner itself evaluates as a Python expression (evaluateDAT.expr, dattoCHOP.rowexpr, ...).

This closes the tracked boundary gap: those params are plain `.val`s (not parameter EXPRESSIONS, which
we already never touch), but the operator reads the string and eval()s it -- so "set_par sets values
only" is necessary but not sufficient, and their optype names carry no script/execute/cplusplus marker
for check_optype_allowed to catch. The denial keeps the operators fully usable for every other param.

Three proofs:
  * UNIT   : check_par_allowed raises for each reviewed sink and allows other params on the same optype.
  * RUNTIME: set_par (control.py) drops the sink into `failed`, never applies it, and never writes .expr.
  * SYNC   : the Python denylist equals the gateway's EVAL-SINK-DENIED fence entries (gateway.rs), so the
             two boundary artifacts cannot drift apart silently.
"""
import os
import re
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control


# The reviewed (optype, param) code sinks, flattened -- the full audited set.
SINKS = [
    # DAT family
    ("evaluateDAT", "expr"),
    ("evaluateDAT", "rowexpr"),
    ("evaluateDAT", "colexpr"),
    ("examineDAT", "expression"),
    ("jsonDAT", "expression"),
    ("tableDAT", "cellexpr"),
    ("tableDAT", "fills0expr"),
    ("insertDAT", "replace0expr"),
    # CHOP family
    ("dattoCHOP", "rowexpr"),
    ("dattoCHOP", "colexpr"),
    ("pipeoutCHOP", "script"),
    ("expressionCHOP", "expr0expr"),
    ("waveCHOP", "exprs"),
    ("clipblenderCHOP", "aend"),
    # SOP family
    ("groupSOP", "filter"),
    ("deleteSOP", "filter"),
    # COMP family
    ("replicatorCOMP", "tscript"),
    # MAT family
    ("phongMAT", "multitexexpr"),
]


class TestCodeSinkGuard(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_check_par_allowed_blocks_each_sink(self):
        for ot, par in SINKS:
            with self.assertRaises(PermissionError, msg="%s.%s must be blocked" % (ot, par)):
                self.server.check_par_allowed(ot, par)

    def test_check_par_allowed_permits_other_params_on_same_optype(self):
        # A code-sink optype is still usable for its data-only params.
        for ot, par in (("evaluateDAT", "language"), ("dattoCHOP", "firstrow"),
                        ("examineDAT", "op"), ("pipeoutCHOP", "active"),
                        ("jsonDAT", "filter"),        # the JSONPath query param is SAFE (not Python)
                        ("tableDAT", "rows"), ("groupSOP", "crname"),
                        ("replicatorCOMP", "numreplicants"), ("phongMAT", "shininess"),
                        ("levelTOP", "opacity"), ("blurTOP", "size")):
            self.assertEqual(self.server.check_par_allowed(ot, par), par)

    def test_check_par_allowed_is_pair_specific(self):
        # 'script' is a code sink on pipeoutCHOP but a SAFE real param on serialCHOP (device output);
        # 'filter' is a sink on groupSOP/deleteSOP but SAFE (JSONPath query) on jsonDAT; 'expression' is a
        # sink on jsonDAT but a Toggle (safe) elsewhere. (serialCHOP.script/jsonDAT.filter/parameterDAT.
        # expression are all real catalog params, so the F-EXEC-1 allowlist permits them.)
        self.assertEqual(self.server.check_par_allowed("serialCHOP", "script"), "script")
        self.assertEqual(self.server.check_par_allowed("jsonDAT", "filter"), "filter")
        self.assertEqual(self.server.check_par_allowed("parameterDAT", "expression"), "expression")
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("groupSOP", "filter")

    def test_set_par_refuses_sink_but_applies_safe_par(self):
        op = MockOp(
            "/project1/eval1",
            pars={
                "expr": MockPar("expr", val=""),
                "rowexpr": MockPar("rowexpr", val=""),
                "language": MockPar("language", val="python"),
            },
            opType="evaluateDAT",
        )
        self.scene.add(op)
        out = control.set_par({
            "op": "/project1/eval1",
            "pars": {"expr": "__import__('os').system('calc')", "rowexpr": "1+1", "language": "python"},
        })
        # the safe param applied ...
        self.assertEqual(out["applied"], {"language": "python"})
        # ... the code sinks were refused, not applied
        self.assertIn("expr", out["failed"])
        self.assertIn("rowexpr", out["failed"])
        self.assertIn("data-only boundary", out["failed"]["expr"])
        # the malicious value never reached the parameter
        self.assertEqual(op.par.expr.eval(), "")
        # and no expression-mode write ever happened
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_sequence_indexed_sinks_denied_at_every_index(self):
        """Red-team F1: TD Sequence code params are index-suffixed (fills0expr, fills1expr, ...).
        The exact-name list only holds block 0 (all the probe captured), but set_par's ParMap is
        open-keyed -- so check_par_allowed must deny EVERY index, not just 0."""
        for ot, par in (("tableDAT", "fills1expr"), ("tableDAT", "fills7expr"),
                        ("tableDAT", "fills12expr"),
                        ("insertDAT", "replace1expr"), ("insertDAT", "replace9expr"),
                        ("expressionCHOP", "expr1expr"), ("expressionCHOP", "expr3expr")):
            with self.assertRaises(PermissionError, msg="%s.%s must be blocked (indexed sink)" % (ot, par)):
                self.server.check_par_allowed(ot, par)

    def test_sequence_pattern_does_not_overreach(self):
        # The block-count param and unrelated indexed-looking params stay usable (data-only).
        for ot, par in (("tableDAT", "fills"),          # the sequence COUNT param -- not code
                        ("tableDAT", "rows"),
                        ("expressionCHOP", "chanperexpr"),  # a data param, not a per-block expr
                        ("insertDAT", "replace")):        # menu, not the per-block expr
            self.assertEqual(self.server.check_par_allowed(ot, par), par)

    def test_set_par_refuses_indexed_sink_after_count_raised(self):
        """End-to-end: even if the block count is raised, set_par drops fills<N>expr into failed and
        never applies the malicious value."""
        op = MockOp(
            "/project1/tab1",
            pars={
                "fills": MockPar("fills", val=1),
                "fills1expr": MockPar("fills1expr", val=""),
            },
            opType="tableDAT",
        )
        self.scene.add(op)
        out = control.set_par({
            "op": "/project1/tab1",
            "pars": {"fills": 3, "fills1expr": "__import__('os').system('calc')"},
        })
        self.assertEqual(out["applied"], {"fills": 3})           # count applied (harmless)
        self.assertIn("fills1expr", out["failed"])               # indexed sink refused
        self.assertIn("data-only boundary", out["failed"]["fills1expr"])
        self.assertEqual(op.par.fills1expr.eval(), "")           # value never reached the param
        self.assertEqual(_tdmock.EXPR_WRITES, [])

    def test_python_denylist_matches_gateway_eval_sink_fence(self):
        """The executor's _DENY_CODE_SINK_PARS must equal the gateway.rs DROPPED_CODE_SINKS entries
        classified EVAL-SINK-DROPPED. If a regen re-reviews the set, both artifacts move together or
        this goes RED."""
        py_pairs = {
            (ot, par)
            for ot, pars in self.server._DENY_CODE_SINK_PARS.items()
            for par in pars
        }
        gw_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))),
            "gateway", "src", "gateway.rs",
        )
        with open(gw_path, "r", encoding="utf-8") as f:
            src = f.read()
        rust_pairs = set(
            re.findall(r'\(\s*"(\w+)"\s*,\s*"(\w+)"\s*,\s*"EVAL-SINK-DROPPED"\s*\)', src)
        )
        self.assertEqual(
            py_pairs, rust_pairs,
            "executor _DENY_CODE_SINK_PARS drifted from the gateway EVAL-SINK-DENIED fence",
        )
        self.assertEqual(py_pairs, set(SINKS))


if __name__ == "__main__":
    unittest.main()
