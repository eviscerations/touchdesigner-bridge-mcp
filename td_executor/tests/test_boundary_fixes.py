"""Boundary-fix regression tests: F1 evaluateDAT optype deny, F2 file-path
confinement in every parameter WRITE, F3 set_par_many routed through the same guard.

F1 (CRITICAL): `write_csv` can write a .dat/.txt file and `fileinDAT` loads it into DAT cells, so DAT/input
text is attacker-controllable -> an operator that EVALUATES that text as code (evaluateDAT via its datexpr
DAT-ref or its input, not just the denied inline expr) is an RCE primitive. Closure = forbid CREATING it.
F2 (HIGH): the generic set_par has no path confinement -> a file-path param value could read/write outside
the working dir. Closure = confine file-style param values at the boundary (_guard_par_value).
F3 (MED): set_par_many did a raw `p.val = v`, bypassing the numeric clamp + path guard. Closure = route it
through _guard_par_value too.
"""
import os
import shutil
import tempfile
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor import server
from td_executor.handlers import control as control_mod


class TestEvaluateDatOptypeDenied(unittest.TestCase):
    def test_check_optype_denies_evaluatedat_both_cases(self):
        for name in ("evaluateDAT", "evaluatedat", "EVALUATEDAT"):
            with self.assertRaises(PermissionError, msg="%s must be blocked (F1 RCE evaluator)" % name):
                server.check_optype_allowed(name)

    def test_normal_ops_still_allowed(self):
        for name in ("constantTOP", "tableDAT", "fileinDAT", "moviefileoutTOP"):
            self.assertEqual(server.check_optype_allowed(name), name)

    def test_create_op_refuses_evaluatedat(self):
        install()
        with self.assertRaises(PermissionError):
            control_mod.create_op({"type": "evaluateDAT", "parent": "/project1", "name": "ev"})

    def test_marker_denies_still_active(self):
        for name in ("textDAT", ):  # sanity: a normal DAT is fine
            self.assertEqual(server.check_optype_allowed(name), name)
        for name in ("scriptDAT", "executeDAT", "chopexecuteDAT", "cplusplusTOP"):
            with self.assertRaises(PermissionError):
                server.check_optype_allowed(name)


class TestFilePathConfinement(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self._wd = self.server.WORKING_DIR
        self.wd = os.path.realpath(tempfile.mkdtemp(prefix="tdmcp_fpc_"))
        self.server.WORKING_DIR = self.wd
        self.server._WD_OVERRIDE = self.wd   # working_dir() honors this over the live arm.json

    def tearDown(self):
        self.server.WORKING_DIR = self._wd
        self.server._WD_OVERRIDE = None
        shutil.rmtree(self.wd, ignore_errors=True)

    def test_file_param_outside_workdir_rejected(self):
        for style in ("File", "Folder", "Filesave"):
            p = MockPar("file", style=style)
            with self.assertRaises(PermissionError, msg="%s path escape must be refused" % style):
                control_mod._guard_par_value(p, os.path.join(tempfile.gettempdir(), "..", "secret.key"))
            with self.assertRaises(PermissionError):
                control_mod._guard_par_value(p, "C:/Windows/System32/drivers/etc/hosts")

    def test_file_param_inside_workdir_ok(self):
        p = MockPar("file", style="File")
        inside = os.path.join(self.wd, "media", "clip.mp4")
        self.assertEqual(control_mod._guard_par_value(p, inside), inside)

    def test_non_file_string_param_not_confined(self):
        # a non-path string param (OP path, token, menu) must pass untouched -- confinement is file-only.
        p = MockPar("someref", style="Str")
        self.assertEqual(control_mod._guard_par_value(p, "C:/anywhere/outside/x"), "C:/anywhere/outside/x")

    def test_empty_file_value_passes(self):
        p = MockPar("file", style="File")
        self.assertEqual(control_mod._guard_par_value(p, ""), "")  # clearing a path is safe

    def test_set_par_refuses_file_escape_as_failed(self):
        op = MockOp("/project1/mv", pars={"file": MockPar("file", style="File")},
                    opType="moviefileoutTOP", family="TOP")
        self.scene.add(op)
        r = control_mod.set_par({"op": op.path, "pars": {"file": "C:/Users/victim/.ssh/id_rsa"}})
        self.assertIn("file", r.get("failed", {}))      # rejected, reported not fatal
        self.assertNotIn("file", r.get("applied", {}))


class TestUniversalCodePointerDeny(unittest.TestCase):
    """Red-team re-audit: callbacks/*script reference params point at DATs TD runs as host Python on ANY op
    (the largest RCE class, since DAT text is attacker-controllable). Denied universally as a PARAM (the op
    stays usable for its data params)."""

    def setUp(self):
        self.server, self.scene = install()

    def test_code_pointer_params_denied_on_any_optype(self):
        for par in ("callbacks", "dragscript", "dropscript", "dropdestscript",
                    "droptypescript", "dragdropcallbacks", "datexpr"):
            for ot in ("timerCHOP", "moviefileinTOP", "webserverDAT", "containerCOMP", "fileinDAT"):
                with self.assertRaises(PermissionError, msg="%s.%s must be denied" % (ot, par)):
                    self.server.check_par_allowed(ot, par)

    def test_data_params_on_those_ops_still_allowed(self):
        # The op stays fully usable -- only the code-pointer param is denied.
        for ot, par in (("timerCHOP", "length"), ("moviefileinTOP", "file"),
                        ("webserverDAT", "port"), ("containerCOMP", "w")):
            self.assertEqual(self.server.check_par_allowed(ot, par), par)

    def test_set_par_refuses_callbacks_as_failed(self):
        op = MockOp("/project1/tmr", pars={"callbacks": MockPar("callbacks", val=""),
                                           "length": MockPar("length", val=10, style="Float")},
                    opType="timerCHOP", family="CHOP")
        self.scene.add(op)
        r = control_mod.set_par({"op": op.path, "pars": {"callbacks": "/project1/evil", "length": 5}})
        self.assertIn("callbacks", r.get("failed", {}))       # code-pointer refused
        self.assertIn("data-only boundary", r["failed"]["callbacks"])
        self.assertEqual(r["applied"].get("length"), 5)       # data param still applied
        self.assertEqual(op.par.callbacks.eval(), "")         # evil DAT never assigned


class TestSetParManyGuarded(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_set_par_many_clamps_like_set_par(self):
        op = MockOp("/project1/n", pars={"g": MockPar("g", style="Float", clampMax=True, max=1.0)},
                    opType="testTOP", family="TOP")  # non-catalogued => allowlist deny-only; tests the clamp path
        self.scene.add(op)
        control_mod.set_par_many({"ops": ["/project1/n"], "pars": {"g": 9.0}})
        self.assertEqual(op.par.g.eval(), 1.0)   # F3: clamped through _guard_par_value


if __name__ == "__main__":
    unittest.main()
