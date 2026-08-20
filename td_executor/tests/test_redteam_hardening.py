"""Regression tests for the red-team hardening batch.

Covers the ADDITIVE, capability-preserving fixes (the denylist->allowlist F-EXEC-1 and the enforced
magnitude ceiling F-DOS-1 are owner-gated and intentionally NOT changed here):

  F-TRUST-1  image-write tools reject a non-image extension; the executor trust root (td_executor/*,
             INTEGRITY.json, arm.py) is off-limits to confined_path even inside the working dir.
  F-PATH-1   import_segmented_model.dir is confined (a dir outside the working dir is refused).
  F-AUTH-1   a cross-origin (non-loopback Origin/Host) request is refused; unauthenticated health hides
             the endpoint inventory.
  F-ROB-1    a negative connect index is refused (no wrap-to-last); find_errors.max_nodes is bounded.
"""
import os
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import control, io, diagnostics


class TestImageExtensionGate(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.top = MockOp("/project1/blur1", opType="blurTOP", family="TOP")
        self.scene.add(self.top)

    def test_save_top_refuses_code_extension(self):
        with self.assertRaises(ValueError):
            io.save_top({"op": "/project1/blur1", "path": "handlers/control.py"})

    def test_show_refuses_code_extension(self):
        with self.assertRaises(ValueError):
            io.show({"op": "/project1/blur1", "path": "evil.json"})

    def test_png_extension_is_accepted_past_the_gate(self):
        # A .png passes the extension gate (it then proceeds to the real save path); any error raised must
        # NOT be the extension refusal.
        try:
            io.save_top({"op": "/project1/blur1", "path": "previews/ok.png"})
        except ValueError as e:
            self.assertNotIn("image files", str(e))


class TestTrustRootOffLimits(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_executor_source_is_off_limits(self):
        with self.assertRaises(PermissionError):
            self.server.confined_path(os.path.join(self.server._PKG_DIR, "server.py"))

    def test_integrity_manifest_is_off_limits(self):
        with self.assertRaises(PermissionError):
            self.server.confined_path(self.server._MANIFEST)

    def test_arm_bootstrap_is_off_limits(self):
        with self.assertRaises(PermissionError):
            self.server.confined_path(self.server._ARM_PY)


class TestOriginHostGate(unittest.TestCase):
    def setUp(self):
        self.server, _ = install()

    def test_loopback_and_missing_pass(self):
        for v in (None, "", "127.0.0.1", "127.0.0.1:9980", "localhost",
                  "http://127.0.0.1:9980", "http://localhost", "[::1]:9980"):
            self.assertTrue(self.server._host_is_loopback(v), v)

    def test_foreign_origin_is_refused(self):
        for v in ("evil.com", "http://evil.com", "http://attacker.example:9980",
                  "https://td.attacker.com/path"):
            self.assertFalse(self.server._host_is_loopback(v), v)

    def test_dispatch_refuses_cross_origin(self):
        resp = {}
        self.server.handle(None, {"uri": "/health", "Origin": "http://evil.com"}, resp)
        import json
        body = json.loads(resp["data"])
        self.assertFalse(body["ok"])
        self.assertEqual(resp["statusCode"], 403)

    def test_unauth_health_hides_endpoints(self):
        # No token configured in the test server -> _authed is True (dev/open), so endpoints ARE present;
        # with a token set and none supplied, the inventory must be withheld.
        resp = {}
        self.server.TOKEN = "secrettoken"
        try:
            self.server.handle(None, {"uri": "/health"}, resp)
        finally:
            self.server.TOKEN = ""
        import json
        body = json.loads(resp["data"])
        self.assertTrue(body["ok"])
        self.assertNotIn("endpoints", body)


class TestConnectNegativeIndex(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.a = MockOp("/project1/a", opType="nullTOP", family="TOP")
        self.b = MockOp("/project1/b", opType="nullTOP", family="TOP")
        self.scene.add(self.a)
        self.scene.add(self.b)

    def test_negative_input_refused(self):
        with self.assertRaises(ValueError):
            control.connect({"from": "/project1/a", "to": "/project1/b", "input": -1})

    def test_negative_output_refused(self):
        with self.assertRaises(ValueError):
            control.connect({"from": "/project1/a", "to": "/project1/b", "output": -1})


class TestGap1ShaderRefGuard(unittest.TestCase):
    """GAP-1: a glslTOP shader-source ref with a validated lane (pixeldat) is refused on the raw driver path
    (write_csv->fileinDAT->set_par(pixeldat) would compile an unvalidated shader). set_glsl delivers it
    validated + owned, and does NOT route through set_par, so the lane is unaffected (test_glsl_handler)."""
    def setUp(self):
        self.server, self.scene = install()
        self.top = MockOp("/project1/glsl1",
                          pars={"pixeldat": MockPar("pixeldat", ""),
                                "resolutionw": MockPar("resolutionw", 1280, style="Int")},
                          opType="glslTOP", family="TOP")
        self.scene.add(self.top)

    def test_driver_set_par_pixeldat_refused(self):
        out = control.set_par({"op": "/project1/glsl1", "pars": {"pixeldat": "/project1/evilfilein"}})
        self.assertIn("pixeldat", out.get("failed", {}))
        self.assertIn("set_glsl", out["failed"]["pixeldat"])
        self.assertNotIn("pixeldat", out.get("applied", {}))

    def test_a_normal_glsltop_data_param_still_applies(self):
        out = control.set_par({"op": "/project1/glsl1", "pars": {"resolutionw": 1920}})
        self.assertEqual(out["applied"], {"resolutionw": 1920})

    def test_check_driver_shader_ref_unit(self):
        with self.assertRaises(PermissionError):
            self.server.check_driver_shader_ref("pixeldat")
        self.assertEqual(self.server.check_driver_shader_ref("resolutionw"), "resolutionw")


class TestBridgeSelfProtection(unittest.TestCase):
    """The AI must never MUTATE /mcp_bridge (its auth, transport, or the GUI consent toggles) -- that would let
    it self-enable a code lane. Every mutating handler calls server.assert_writable; read-only tools may still
    observe the bridge."""
    def setUp(self):
        self.server, self.scene = install()
        self.bridge = MockOp("/mcp_bridge", opType="baseCOMP", family="COMP",
                             pars={"Allowexpr": MockPar("Allowexpr", 0, style="Toggle")})
        self.child = MockOp("/mcp_bridge/webserver", opType="webserverDAT", family="DAT",
                            pars={"port": MockPar("port", 9980, style="Int")})
        self.scene.add(self.bridge)
        self.scene.add(self.child)

    def test_assert_writable_refuses_bridge_and_children(self):
        for p in ("/mcp_bridge", "/mcp_bridge/webserver", "/mcp_bridge/consent_sync"):
            with self.assertRaises(PermissionError):
                self.server.assert_writable(p)

    def test_assert_writable_allows_normal_paths(self):
        self.assertEqual(self.server.assert_writable("/project1/blur1"), "/project1/blur1")

    def test_set_par_on_bridge_refused(self):
        with self.assertRaises(PermissionError):
            control.set_par({"op": "/mcp_bridge", "pars": {"Allowexpr": 1}})

    def test_set_par_on_bridge_child_refused(self):
        with self.assertRaises(PermissionError):
            control.set_par({"op": "/mcp_bridge/webserver", "pars": {"port": 1234}})

    def test_delete_op_on_bridge_refused(self):
        with self.assertRaises(PermissionError):
            control.delete_op({"op": "/mcp_bridge"})


class TestFindErrorsBounded(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()

    def test_negative_max_nodes_still_scans_root(self):
        # clamp(.., 1, 100000): a bogus negative cap becomes 1, so the scan still runs (>=1) and never
        # spins unbounded on a huge cap.
        out = diagnostics.find_errors({"path": "/", "max_nodes": -5})
        self.assertGreaterEqual(out["scanned"], 1)

    def test_huge_max_nodes_is_capped(self):
        out = diagnostics.find_errors({"path": "/", "max_nodes": 10**12})
        self.assertLessEqual(out["scanned"], 100000)


if __name__ == "__main__":
    unittest.main()
