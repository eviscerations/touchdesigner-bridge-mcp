"""check_optype_allowed: the generic create engine must refuse code-carrying operator types
(script* / execute* / cplusplus*) and allow normal operators. Parity with the Houdini executor's
node-type guard on its generic create path."""
import unittest

from td_executor.tests._tdmock import install


class TestOptypeGuard(unittest.TestCase):
    def setUp(self):
        self.server, _ = install()

    def test_allows_normal_optypes(self):
        for ot in ("compositeTOP", "blurTOP", "noiseCHOP", "geometryCOMP", "boxSOP",
                   "constantMAT", "tableDAT", "fileinDAT", "moviefileoutTOP"):
            self.assertEqual(self.server.check_optype_allowed(ot), ot)

    def test_blocks_code_carrying_optypes(self):
        for ot in ("scriptTOP", "scriptCHOP", "scriptSOP", "scriptDAT",
                   "executeDAT", "chopexecuteDAT", "datexecuteDAT", "parameterexecuteDAT",
                   "cplusplusTOP", "cplusplusCHOP",
                   "evaluateDAT"):  # red-team F1: evaluates DAT/input text as code -> exact-name deny
            with self.assertRaises(PermissionError, msg="%s must be blocked" % ot):
                self.server.check_optype_allowed(ot)

    def test_block_is_case_insensitive(self):
        for ot in ("ScriptTOP", "EXECUTEdat", "CPlusPlusCHOP"):
            with self.assertRaises(PermissionError):
                self.server.check_optype_allowed(ot)


if __name__ == "__main__":
    unittest.main()
