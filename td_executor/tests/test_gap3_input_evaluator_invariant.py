"""GAP-3 invariant: encode, as an enforced test, WHY the
generic `connect` handler needs no per-edge "evaluator" guard.

The reasoning chain the boundary depends on:
  * `write_csv` can write attacker-chosen text to a .dat/.txt, and `fileinDAT` loads it into DAT cells,
    so DAT/input CONTENT is attacker-controllable.
  * An operator that EVALUATES a wired INPUT CONNECTION as code (not just an inline `.val` param) turns
    that attacker-controlled content into arbitrary code the moment the graph cooks -- even though
    `check_par_allowed` blocks the operator's inline expression params, the code can instead arrive over
    an input edge.
  * `connect` writes wiring with NO per-edge inspection of what flows through it. A param denylist cannot
    close this: the danger is the EDGE, not a parameter.

INVARIANT: every operator type that evaluates an input connection as code MUST be refused by
`check_optype_allowed` (optype-deny), so it can never be CREATED -- which is the only thing that makes
`connect` safe without a bespoke evaluator guard. Optype-deny (can't exist) strictly dominates
param-deny (exists but one door shut) for this class, because `connect` opens a different door.

If a future audit finds another input-connection code-evaluator, add it to
KNOWN_INPUT_CONNECTION_CODE_EVALUATORS below AND ensure `check_optype_allowed` refuses it (via
server._DENY_OPTYPE_EXACT or a name marker) -- this test will fail until both are true.
"""
import unittest

from td_executor.tests._tdmock import install
from td_executor import server
from td_executor.handlers import control as control_mod


# Operator types whose PURPOSE is to evaluate a wired INPUT connection's content as code (an expression /
# Python), independent of any inline code-sink parameter. This is the narrow class `connect` must never be
# able to feed. evaluateDAT: output=evaluate (default) treats its input DAT's cells as expressions in the
# selected language (incl. Python); its inline expr/rowexpr/colexpr are separately param-denied, but the
# INPUT path is the one `connect` could otherwise wire.
KNOWN_INPUT_CONNECTION_CODE_EVALUATORS = frozenset({"evaluatedat"})


class TestGap3InputEvaluatorInvariant(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_known_set_is_nonempty(self):
        # A canary: if this ever empties, the invariant has silently lost its subject -- re-audit rather
        # than assume `connect` is safe for free.
        self.assertTrue(KNOWN_INPUT_CONNECTION_CODE_EVALUATORS,
                        "the input-connection code-evaluator set must name its members explicitly")

    def test_every_input_evaluator_is_optype_denied(self):
        # The invariant proper: each is refused by check_optype_allowed in every case-fold, so it can
        # never be instantiated -- optype-deny, not merely param-deny.
        for ot in KNOWN_INPUT_CONNECTION_CODE_EVALUATORS:
            for variant in (ot, ot.upper(), ot.capitalize()):
                with self.assertRaises(PermissionError,
                                       msg="%s evaluates an input connection as code -> must be optype-denied" % variant):
                    server.check_optype_allowed(variant)

    def test_optype_deny_dominates_param_deny_for_this_class(self):
        # Explicit statement that these live in the OPTYPE deny set (or are marker-caught), NOT relying on
        # the code-sink PARAM denylist alone -- param-deny would leave the input edge open.
        for ot in KNOWN_INPUT_CONNECTION_CODE_EVALUATORS:
            marker_caught = any(m in ot for m in server._DENY_OPTYPE_MARKERS)
            self.assertTrue(ot in server._DENY_OPTYPE_EXACT or marker_caught,
                            "%s must be caught by _DENY_OPTYPE_EXACT or a name marker, not only param-deny" % ot)

    def test_create_op_cannot_instantiate_an_input_evaluator(self):
        # End-to-end: the create path refuses them, so no such node can exist for `connect` to wire into.
        for ot in KNOWN_INPUT_CONNECTION_CODE_EVALUATORS:
            with self.assertRaises(PermissionError):
                control_mod.create_op({"type": ot, "parent": "/project1", "name": "ie_probe"})


if __name__ == "__main__":
    unittest.main()
