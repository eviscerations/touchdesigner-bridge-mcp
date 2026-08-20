"""P1 GLSL validator: the full red-team corpus (_selftest) must report ZERO failures, plus explicit
must-fail assertions for the headline DoS / hygiene / stage classes. Standalone -- the validator imports
nothing from td/op."""
import unittest

from td_executor.glsl_validator import validate_glsl, GlslValidationError, _selftest


class TestGlslValidator(unittest.TestCase):
    def test_selftest_zero_failures(self):
        res = _selftest()
        self.assertEqual(res["failed"], 0, "must-pass/must-fail corpus failures: %s" % res["details"])
        self.assertGreater(res["passed"], 0)

    def _reject(self, src, stage="pixel"):
        with self.assertRaises(GlslValidationError):
            validate_glsl(src, stage)

    def test_include_rejected(self):
        self._reject("#version 330\n#include \"x.glsl\"\nvoid main(){}")

    def test_while_rejected(self):
        self._reject("#version 330\nvoid main(){ while(true){} }")

    def test_unbounded_for_rejected(self):
        self._reject("#version 330\nuniform int n;\nvoid main(){ for(int i=0;i<n;i++){} }")

    def test_huge_for_rejected(self):
        self._reject("#version 330\nvoid main(){ for(int i=0;i<5000;i++){} }")

    def test_non_ascii_rejected(self):
        self._reject("#version 330\nvoid main(){ /* é */ }")

    def test_compute_image_decl_rejected(self):
        self._reject("#version 330\nlayout(rgba8) uniform image2D img;\nvoid main(){}")

    def test_compute_stage_rejected(self):
        self._reject("#version 330\nvoid main(){}", stage="compute")

    def test_valid_shader_passes(self):
        # a bounded-loop shader with a texture fetch inside a static loop must validate cleanly.
        src = ("#version 420\nuniform sampler2D sTD2DInputs[1];\nout vec4 c;\nvoid main(){\n"
               "  vec4 a = vec4(0.0);\n  for(int i=0;i<32;i++){ a += texture(sTD2DInputs[0], vec2(0.5)); }\n"
               "  c = a; }")
        self.assertIsNone(validate_glsl(src, "pixel"))


if __name__ == "__main__":
    unittest.main()
