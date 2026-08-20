"""Data-only boundary canary (parity with the Houdini executor's assert_no_rce_endpoints test):
the fixed handler registry must contain NO arbitrary-code endpoint, and the guard must FIRE the
moment a banned-shaped endpoint is injected."""
import unittest

from td_executor.tests._tdmock import install


class TestNoRceEndpoints(unittest.TestCase):
    def setUp(self):
        self.server, _ = install()

    def test_real_registry_passes(self):
        # The shipped registry (control/io/reference/diagnostics + dev_reload) must be clean.
        self.server.assert_no_rce_endpoints()  # must not raise
        self.assertIn("create_op", self.server._REGISTRY)
        self.assertIn("set_par", self.server._REGISTRY)

    def test_injected_banned_endpoint_raises(self):
        server = self.server
        for banned in ("run_code", "exec", "os_system", "wrangle", "python_eval"):
            server._REGISTRY[banned] = {"fn": lambda p: None, "auth": True}
            try:
                with self.assertRaises(RuntimeError, msg="'%s' must trip the boundary" % banned):
                    server.assert_no_rce_endpoints()
            finally:
                server._REGISTRY.pop(banned, None)
        # boundary is clean again after removing the injected endpoints
        server.assert_no_rce_endpoints()

    def test_name_shape_detector(self):
        server = self.server
        for bad in ("exec", "run_code", "node_op", "eval_thing", "do_hscript", "os_system", "script_run"):
            self.assertTrue(server._name_is_rce_shaped(bad), "%s should be flagged" % bad)
        for ok in ("create_op", "set_par", "scene_info", "read_network", "save_top", "operator_reference"):
            self.assertFalse(server._name_is_rce_shaped(ok), "%s should NOT be flagged" % ok)


if __name__ == "__main__":
    unittest.main()
