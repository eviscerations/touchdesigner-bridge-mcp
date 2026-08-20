"""Security + registration tests for the session-4 helper tools: show, write_csv, set_par_many,
import_segmented_model.

The load-bearing one is write_csv -- the ONLY new FILE-WRITE surface. It must (1) realpath-confine every
path to the working dir (reject absolute-outside + ../ traversal + the config dir), and (2) whitelist
tabular extensions so no code file can ever be written. Plus: all four endpoints register, and the new
handler source carries no arbitrary-code / OS-shell sink (parity with the other 'no sink in source' tests).
"""
import inspect
import os
import re
import shutil
import sys
import tempfile
import unittest

from td_executor.tests._tdmock import install
from td_executor.handlers import io as io_mod
from td_executor.handlers import control as control_mod


class TestHelperToolsRegistered(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_all_four_endpoints_registered(self):
        for name in ("show", "write_csv", "set_par_many", "import_segmented_model"):
            self.assertIn(name, self.server._REGISTRY, "%s must be a registered endpoint" % name)

    def test_no_new_endpoint_is_rce_shaped(self):
        for name in ("show", "write_csv", "set_par_many", "import_segmented_model"):
            self.assertFalse(self.server._name_is_rce_shaped(name), "%s reads RCE-shaped" % name)


class TestNewHandlerSourceDiscipline(unittest.TestCase):
    """The new handlers must not reach for arbitrary code / OS shells. write_csv legitimately uses open()
    (a confined tabular write); nothing may use eval/exec/compile/os.system/subprocess/__import__."""
    # Negative lookbehind excludes METHOD calls (TD's data-only p.eval() param read, n.compile(), etc.);
    # we only forbid the BUILTIN eval/exec/compile and OS shells.
    FORBIDDEN = (r"\bos\.system\b", r"\bsubprocess\b", r"(?<![.\w])eval\s*\(", r"(?<![.\w])exec\s*\(",
                 r"(?<![.\w])compile\s*\(", r"__import__", r"\bos\.popen\b")

    def test_io_and_control_source_have_no_code_or_shell_sink(self):
        for mod in (io_mod, control_mod):
            src = inspect.getsource(mod)
            for pat in self.FORBIDDEN:
                self.assertIsNone(re.search(pat, src),
                                  "%s source matches forbidden %r" % (mod.__name__, pat))

    def test_write_csv_routes_through_confined_path_and_ext_whitelist(self):
        src = inspect.getsource(io_mod.write_csv)
        self.assertIn("confined_path", src, "write_csv must confine its path")
        self.assertIn("_TABLE_EXTS", src, "write_csv must enforce the tabular extension whitelist")


class TestWriteCsvConfinement(unittest.TestCase):
    """write_csv is the new write surface -- confine every path to the working dir, tabular ext only."""

    def setUp(self):
        self.server, self.scene = install()
        self._saved_wd = self.server.WORKING_DIR
        self._saved_cfg = self.server._CONFIG_DIR
        self.wd = os.path.realpath(tempfile.mkdtemp(prefix="tdmcp_wcsv_"))
        self.cfg = os.path.realpath(os.path.join(self.wd, ".td-bridge-config"))
        os.makedirs(self.cfg, exist_ok=True)
        self.server.WORKING_DIR = self.wd
        self.server._WD_OVERRIDE = self.wd   # working_dir() honors this over the live arm.json
        self.server._CONFIG_DIR = self.cfg

    def tearDown(self):
        self.server.WORKING_DIR = self._saved_wd
        self.server._WD_OVERRIDE = None
        self.server._CONFIG_DIR = self._saved_cfg
        shutil.rmtree(self.wd, ignore_errors=True)

    # ---- happy path ----
    def test_writes_a_confined_csv(self):
        r = io_mod.write_csv({"path": "cues/show.csv", "content": "length,sec00,sec01\n2,1,0\n2,0,1"})
        saved = r["saved"]
        self.assertTrue(os.path.realpath(saved).startswith(self.wd))
        with open(saved, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text, "length,sec00,sec01\n2,1,0\n2,0,1\n")  # trailing newline appended
        self.assertEqual(r["lines"], 3)

    def test_tsv_and_dat_and_txt_allowed(self):
        for ext in (".tsv", ".dat", ".txt", ".csv"):
            r = io_mod.write_csv({"path": "t" + ext, "content": "a,b\n1,2"})
            self.assertTrue(os.path.exists(r["saved"]))

    # ---- extension whitelist: NO code files ----
    def test_rejects_code_and_other_extensions(self):
        for bad in ("evil.py", "evil.pyw", "x.tox", "x.toe", "x.dll", "x.exe", "x.bat", "x.ps1", "x.sh", "x.json"):
            with self.assertRaises(ValueError, msg="%s must be rejected" % bad):
                io_mod.write_csv({"path": bad, "content": "a,b"})

    def test_rejects_py_even_with_confined_dir(self):
        # A .py inside the working dir (where the executor package lives) is the worst case -- must refuse.
        with self.assertRaises(ValueError):
            io_mod.write_csv({"path": "td_executor/handlers/pwn.py", "content": "print('x')"})

    # ---- path confinement (parity with confined_path tests) ----
    def test_rejects_absolute_outside(self):
        outside = (os.path.join(tempfile.gettempdir(), "tdmcp_evil_%d.csv" % os.getpid()))
        with self.assertRaises(PermissionError):
            io_mod.write_csv({"path": outside, "content": "a,b"})

    def test_rejects_parent_traversal(self):
        with self.assertRaises(PermissionError):
            io_mod.write_csv({"path": os.path.join("..", "..", "escape.csv"), "content": "a,b"})

    def test_rejects_config_dir(self):
        with self.assertRaises(PermissionError):
            io_mod.write_csv({"path": os.path.join(".td-bridge-config", "cues.csv"), "content": "a,b"})

    # ---- input validation ----
    def test_rejects_non_string_content(self):
        with self.assertRaises(ValueError):
            io_mod.write_csv({"path": "x.csv", "content": [["a", "b"]]})

    def test_rejects_oversize_content(self):
        big = "a,b\n" * 600000  # > 2 MB
        with self.assertRaises(ValueError):
            io_mod.write_csv({"path": "x.csv", "content": big})


if __name__ == "__main__":
    unittest.main()
