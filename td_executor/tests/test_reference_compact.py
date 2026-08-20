"""operator_reference compact mode (driver-seat P2): a slimmer param view that drops the bulky
norm/hard UI-range arrays so surveying many operator types doesn't blow the driver's context."""
import json
import os
import unittest

from td_executor import server
from td_executor.handlers import reference as ref

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestOperatorReferenceCompact(unittest.TestCase):
    def setUp(self):
        self._wd = getattr(server, "WORKING_DIR", None)
        server.WORKING_DIR = _REPO
        ref._CACHE["data"] = None   # force a reload from the repo catalog

    def tearDown(self):
        server.WORKING_DIR = self._wd
        ref._CACHE["data"] = None

    def test_full_mode_carries_range_arrays(self):
        r = ref.operator_reference({"optype": "blurTOP"})
        self.assertFalse(r["compact"])
        self.assertTrue(any(("norm" in p or "hard" in p) for p in r["params"]),
                        "the full dump must carry the norm/hard range arrays")

    def test_compact_drops_ranges_keeps_essentials(self):
        r = ref.operator_reference({"optype": "blurTOP", "compact": True})
        self.assertTrue(r["compact"])
        self.assertEqual(r["param_count"], len(r["params"]), "param_count is the true count")
        for p in r["params"]:
            self.assertNotIn("norm", p, "compact drops the norm range array")
            self.assertNotIn("hard", p, "compact drops the hard range array")
            self.assertIn("name", p)
            self.assertIn("style", p)
            self.assertIn("default", p)
            if "tokens" in p:
                self.assertIsNotNone(p["tokens"], "compact omits a null tokens field")

    def test_compact_serializes_smaller_than_full(self):
        full = ref.operator_reference({"optype": "blurTOP"})
        comp = ref.operator_reference({"optype": "blurTOP", "compact": True})
        self.assertLess(len(json.dumps(comp)), len(json.dumps(full)),
                        "compact must serialize smaller than the full dump")

    def test_param_naming_and_create_vector_params(self):
        # Option (b): operator_reference must TEACH the create-tuplet vs set_par-component convention so a
        # driver stops silently dropping raw component names on create tools.
        for r in (ref.operator_reference({"optype": "blurTOP"}),
                  ref.operator_reference({"optype": "blurTOP", "compact": True})):
            self.assertIn("param_naming", r)
            self.assertIn("tuplet", r["param_naming"].lower())
            cvp = r["create_vector_params"]
            # `resolution` is a real multi-component tuplet on blurTOP (resolutionw/resolutionh).
            self.assertIn("resolution", cvp)
            self.assertEqual(set(cvp["resolution"]), {"resolutionw", "resolutionh"})
            # the raw component name is NOT itself a create-vector key (that is the whole footgun).
            self.assertNotIn("resolutionw", cvp)


if __name__ == "__main__":
    unittest.main()
