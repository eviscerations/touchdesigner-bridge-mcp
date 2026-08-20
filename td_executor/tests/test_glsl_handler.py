"""P1 GLSL handler (offline, via _tdmock):
  (a) consent OFF  -> set_glsl refuses and writes nothing;
  (b) consent ON + invalid source -> refuses BEFORE any __mcp_pixel.text write (DAT text never set);
  (c) consent ON + valid source -> __mcp_pixel.text == source, glslTOP.pixeldat wired to it, res/npasses clamped;
  (d) DELIVERY FENCE -> a code-pointer param (callbacks) pointed at the owned shader DAT is STILL refused by the
      unchanged _DENY_PARAM_NAMES_UNIVERSAL guard, proving the GLSL lane opened no code-pointer hole.
Consent is forced ON by monkeypatching the handler's _read_allow_glsl (default-off otherwise)."""
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import glsl as glsl_mod


_VALID = ("#version 420\nuniform sampler2D sTD2DInputs[1];\nout vec4 c;\nvoid main(){\n"
          "  vec4 a = vec4(0.0);\n  for(int i=0;i<16;i++){ a += texture(sTD2DInputs[0], vec2(0.5)); }\n"
          "  c = a; }")
_INVALID = "#version 330\nvoid main(){ while(true){} }"   # unbounded loop -> loop.unbounded_form


def _make_glsltop(scene, path="/project1/glsl1"):
    pars = {
        "pixeldat": MockPar("pixeldat", ""),
        "resolutionw": MockPar("resolutionw", 3840),
        "resolutionh": MockPar("resolutionh", 2160),
        "npasses": MockPar("npasses", 16),
        "outputresolution": MockPar("outputresolution", "useinput", style="Menu"),
    }
    top = MockOp(path, pars=pars, opType="glslTOP", family="TOP")
    scene.add(top)
    return top


class TestGlslHandler(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.top = _make_glsltop(self.scene)
        self._saved_consent = glsl_mod._read_allow_glsl

    def tearDown(self):
        glsl_mod._read_allow_glsl = self._saved_consent

    def _consent(self, on):
        glsl_mod._read_allow_glsl = (lambda: on)

    def _owned_dat(self):
        for c in self.top.children:
            if c.name == "__mcp_pixel":
                return c
        return None

    # (a) consent OFF -> refuse, nothing written --------------------------------------------------
    def test_consent_off_refuses_and_writes_nothing(self):
        self._consent(False)
        with self.assertRaises(PermissionError):
            glsl_mod.set_glsl({"op": self.top.path, "stage": "pixel", "source": _VALID})
        self.assertIsNone(self._owned_dat(), "no shader DAT should be created when consent is off")

    # (b) consent ON + invalid -> refuse BEFORE any .text write -----------------------------------
    def test_consent_on_invalid_source_never_writes(self):
        self._consent(True)
        # pre-create the owned DAT with text=None so we can prove the write never happened (reuse path).
        dat = self.top.create("textDAT", "__mcp_pixel")
        self.assertIsNone(dat.text)
        with self.assertRaises(ValueError):
            glsl_mod.set_glsl({"op": self.top.path, "stage": "pixel", "source": _INVALID})
        self.assertIsNone(dat.text, "invalid source must not write __mcp_pixel.text")

    # (c) consent ON + valid -> text written, pixeldat wired, resolution/npasses clamped -----------
    def test_consent_on_valid_source_applies(self):
        self._consent(True)
        out = glsl_mod.set_glsl({"op": self.top.path, "stage": "pixel", "source": _VALID})
        self.assertTrue(out["applied"])
        dat = self._owned_dat()
        self.assertIsNotNone(dat)
        self.assertEqual(dat.text, _VALID)                       # the single sanctioned DAT-.text write
        self.assertEqual(dat.name, "__mcp_pixel")
        # pixeldat wired to the owned DAT
        wired = self.top.par.pixeldat.eval()
        self.assertEqual(getattr(wired, "path", wired), dat.path)
        # resolution + npasses clamped to the low build ceiling
        self.assertEqual(self.top.par.resolutionw.eval(), 1280)
        self.assertEqual(self.top.par.resolutionh.eval(), 720)
        self.assertEqual(self.top.par.npasses.eval(), 4)

    # (d) DELIVERY FENCE -- a code-pointer param at the owned DAT is still refused -----------------
    def test_delivery_fence_code_pointer_still_denied(self):
        self._consent(True)
        glsl_mod.set_glsl({"op": self.top.path, "stage": "pixel", "source": _VALID})
        dat_path = self._owned_dat().path
        # the unchanged universal code-pointer guard must still refuse callbacks pointing at the shader DAT.
        with self.assertRaises(PermissionError):
            self.server.check_par_allowed("timerCHOP", "callbacks")
        # and through the real set_par path it is reported as a per-par failure, never applied.
        timer = MockOp("/project1/timer1", pars={"callbacks": MockPar("callbacks", "")}, opType="timerCHOP",
                       family="CHOP")
        self.scene.add(timer)
        res = self.server._REGISTRY["set_par"]["fn"]({"op": timer.path, "pars": {"callbacks": dat_path}})
        self.assertIn("callbacks", res.get("failed", {}))
        self.assertEqual(res.get("applied", {}), {})


if __name__ == "__main__":
    unittest.main()
