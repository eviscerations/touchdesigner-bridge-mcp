"""Security + behaviour tests for the `pulse` action endpoint (allowlisted parameterless actions).

`pulse` is a new ACTUATION surface: it fires par.pulse() (a parameterless event -- cue/reload media,
refresh a File In, drive a timer transport). The whole safety argument is the EXPLICIT REVIEWED ALLOWLIST
(_ALLOW_PULSE) + a Pulse-style guard + a forbidden-marker guard. These tests pin all three: only
allowlisted (optype, param) pairs fire, everything else is refused, and no code/exec/window pulse can be
reached -- so `pulse` stays the same risk class as the export flag (actuates data-only behaviour, no code).
"""
import inspect
import re
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control as control_mod


def _timer(scene, path="/project1/tmr", pars=None, opType="timerCHOP"):
    op = MockOp(path, pars=pars or {}, opType=opType, family="CHOP")
    scene.add(op)
    return op


class TestPulseRegistered(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_pulse_endpoint_registered(self):
        self.assertIn("pulse", self.server._REGISTRY)

    def test_pulse_not_rce_shaped(self):
        self.assertFalse(self.server._name_is_rce_shaped("pulse"))


class TestAllowlistInvariants(unittest.TestCase):
    """The allowlist is the safety boundary -- assert its shape can't drift into danger."""

    def test_no_allowlisted_pulse_carries_a_forbidden_marker(self):
        for ot, ps in control_mod._ALLOW_PULSE.items():
            for p in ps:
                for m in control_mod._PULSE_FORBIDDEN_MARKERS:
                    self.assertNotIn(m, p.lower(), "%s.%s carries forbidden marker %r" % (ot, p, m))

    def test_window_and_execute_ops_are_not_allowlisted(self):
        # A human opens output windows; no window/execute op may be pulsed through the bridge.
        for ot in ("windowCOMP", "executeDAT", "perform"):
            self.assertNotIn(ot, control_mod._ALLOW_PULSE)

    def test_handler_routes_through_the_allowlist(self):
        src = inspect.getsource(control_mod.pulse)
        self.assertIn("_ALLOW_PULSE", src, "pulse must consult the allowlist")
        self.assertIn("style", src, "pulse must enforce Pulse style")

    def test_no_code_or_shell_sink_in_pulse_source(self):
        src = inspect.getsource(control_mod.pulse)
        for pat in (r"(?<![.\w])eval\s*\(", r"(?<![.\w])exec\s*\(", r"\bos\.system\b", r"\bsubprocess\b"):
            self.assertIsNone(re.search(pat, src))


class TestPulseBehaviour(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    # ---- allowed ----
    def test_allowed_timer_start_fires_once(self):
        op = _timer(self.scene, pars={"start": MockPar("start", style="Pulse")})
        r = control_mod.pulse({"op": op.path, "par": "start"})
        self.assertEqual(r["pulsed"], "start")
        self.assertEqual(r["optype"], "timerCHOP")
        self.assertEqual(op.par.start.pulsed, 1)

    def test_allowed_timer_goto_and_movie_cue(self):
        t = _timer(self.scene, "/project1/t2", pars={"gotodone": MockPar("gotodone", style="Pulse")})
        control_mod.pulse({"op": t.path, "par": "gotodone"})
        self.assertEqual(t.par.gotodone.pulsed, 1)
        mv = MockOp("/project1/mv", pars={"cuepulse": MockPar("cuepulse", style="Pulse")},
                    opType="moviefileinTOP", family="TOP")
        self.scene.add(mv)
        control_mod.pulse({"op": mv.path, "par": "cuepulse"})
        self.assertEqual(mv.par.cuepulse.pulsed, 1)

    def test_allowed_tabledat_loadfile(self):
        # Load File re-reads the working-dir-confined .csv into the table (same class as filein refresh).
        dat = MockOp("/project1/tbl", pars={"loadonstartpulse": MockPar("loadonstartpulse", style="Pulse")},
                     opType="tableDAT", family="DAT")
        self.scene.add(dat)
        r = control_mod.pulse({"op": dat.path, "par": "loadonstartpulse"})
        self.assertEqual(r["pulsed"], "loadonstartpulse")
        self.assertEqual(dat.par.loadonstartpulse.pulsed, 1)

    # ---- refused ----
    def test_refuses_non_allowlisted_optype(self):
        win = MockOp("/project1/win", pars={"winopen": MockPar("winopen", style="Pulse")},
                     opType="windowCOMP", family="COMP")
        self.scene.add(win)
        with self.assertRaises(PermissionError):
            control_mod.pulse({"op": win.path, "par": "winopen"})
        self.assertEqual(win.par.winopen.pulsed, 0)  # never fired

    def test_refuses_non_allowlisted_param_on_allowed_optype(self):
        op = _timer(self.scene, pars={"reset": MockPar("reset", style="Pulse")})
        with self.assertRaises(PermissionError):
            control_mod.pulse({"op": op.path, "par": "reset"})
        self.assertEqual(op.par.reset.pulsed, 0)

    def test_refuses_allowlisted_name_that_is_not_pulse_style(self):
        # Even an allowlisted name must be a real Pulse action -- a value param of that name is refused.
        op = _timer(self.scene, pars={"start": MockPar("start", style="Float")})
        with self.assertRaises(PermissionError):
            control_mod.pulse({"op": op.path, "par": "start"})
        self.assertEqual(op.par.start.pulsed, 0)

    def test_missing_param_raises_value_error(self):
        op = _timer(self.scene, pars={"start": MockPar("start", style="Pulse")})
        with self.assertRaises(ValueError):
            control_mod.pulse({"op": op.path, "par": "cuepulse"})  # allowlisted but not on this op

    def test_missing_or_nonstring_par_raises(self):
        op = _timer(self.scene, pars={"start": MockPar("start", style="Pulse")})
        for bad in (None, "", 5, ["start"]):
            with self.assertRaises(ValueError):
                control_mod.pulse({"op": op.path, "par": bad})


if __name__ == "__main__":
    unittest.main()
