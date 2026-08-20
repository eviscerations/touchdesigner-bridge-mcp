"""Executor-side path confinement (parity with the Houdini executor's confined_path test AND the
Rust gateway's confine_path tests -- the "confined twice, independently" property in SECURITY.md).

confined_path is the LAST line of defense: the executor never trusts the gateway's confinement. It
must accept paths under the working dir, REJECT escapes (absolute-outside, ../ traversal, junctions),
keep the bridge CONFIG dir off-limits even when it sits under the working dir, and normalize Windows
extended-length (\\?\) prefixes so a canonicalized path still matches the root."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from td_executor.tests._tdmock import install


class TestConfinedPath(unittest.TestCase):
    def setUp(self):
        self.server, _ = install()
        # Save + override the module globals so the test controls the confinement root deterministically.
        self._saved_wd = self.server.WORKING_DIR
        self._saved_cfg = self.server._CONFIG_DIR
        self.wd = os.path.realpath(tempfile.mkdtemp(prefix="tdmcp_conf_"))
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

    # ---- accept inside ----
    def test_accepts_path_under_working_dir(self):
        inside = os.path.join(self.wd, "previews", "out.png")  # not-yet-existing leaf is fine
        resolved = self.server.confined_path(inside)
        self.assertTrue(os.path.realpath(resolved).startswith(self.wd))

    def test_accepts_working_dir_root_itself(self):
        self.assertEqual(os.path.realpath(self.server.confined_path(self.wd)), self.wd)

    # ---- reject escapes ----
    def test_rejects_absolute_outside(self):
        outside = (r"C:\Windows\System32\drivers\etc\hosts"
                   if sys.platform.startswith("win") else "/etc/passwd")
        with self.assertRaises(PermissionError):
            self.server.confined_path(outside)
        # also a sibling temp path outside the WD
        other = os.path.join(tempfile.gettempdir(), "tdmcp_evil_%d.txt" % os.getpid())
        with self.assertRaises(PermissionError):
            self.server.confined_path(other)

    def test_rejects_parent_traversal(self):
        with self.assertRaises(PermissionError):
            self.server.confined_path(os.path.join(self.wd, "..", "..", "escape.txt"))

    # ---- config dir off-limits, even when it sits UNDER the working dir ----
    def test_rejects_bridge_config_dir(self):
        with self.assertRaises(PermissionError) as cm:
            self.server.confined_path(os.path.join(self.cfg, "arm.json"))
        self.assertIn("config dir", str(cm.exception).lower())
        with self.assertRaises(PermissionError):
            self.server.confined_path(self.cfg)  # the dir itself

    # ---- Windows extended-length prefix normalization ----
    def test_normalizes_extended_length_prefix(self):
        if not sys.platform.startswith("win"):
            self.skipTest("\\\\?\\ normalization is Windows-only")
        inside_abs = os.path.join(self.wd, "sub", "file.png")
        extended = "\\\\?\\" + inside_abs  # e.g. \\?\C:\...\sub\file.png
        resolved = self.server.confined_path(extended)
        self.assertTrue(os.path.realpath(resolved).startswith(self.wd))

    # ---- Windows junction inside the WD pointing OUTSIDE it must not tunnel out ----
    def test_rejects_junction_escaping_root(self):
        if not sys.platform.startswith("win"):
            self.skipTest("junction-escape vector is Windows-only")
        outside = os.path.realpath(tempfile.mkdtemp(prefix="tdmcp_jout_"))
        with open(os.path.join(outside, "secret.txt"), "w") as f:
            f.write("top secret")
        link = os.path.join(self.wd, "escape")
        made = subprocess.run(["cmd", "/C", "mklink", "/J", link, outside],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        try:
            if not made:
                self.skipTest("mklink /J unavailable")
            with self.assertRaises(PermissionError):
                self.server.confined_path(os.path.join(link, "secret.txt"))
        finally:
            subprocess.run(["cmd", "/C", "rmdir", link],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
