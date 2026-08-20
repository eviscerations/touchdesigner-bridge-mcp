"""W6 governor: the PURE, offline-testable core of the scale/VRAM advisory.

`magnitude_advice` (param-only) and `classify_band` (numbers-only) import nothing from TD and never
raise, so they are fully exercised offline. `envelope_status`/`governor_gate` are proven fail-soft
(they return a well-formed dict / only refuse on a genuinely critical band). The LIVE resource read
(system RAM band, per-TOP GPU memory) needs the running executor and is flagged for live verification.
"""
import unittest

from td_executor import governor


class TestMagnitudeAdvice(unittest.TestCase):
    def lvl(self, op, params):
        return governor.magnitude_advice(op, params)["level"]

    # ── output resolution (the dominant realtime-GPU signal + the 1280 non-commercial cap) ──────────
    def test_hd_output_ok(self):
        self.assertEqual(self.lvl("constantTOP", {"resolutionw": 1280, "resolutionh": 720}), "ok")

    def test_above_1280_cap_flags_caution(self):
        # 1920x1080 is above the non-commercial 1280 output cap -> caution, and the note cites the cap.
        mag = governor.magnitude_advice("moviefileoutTOP", {"resolutionw": 1920, "resolutionh": 1080})
        self.assertEqual(mag["level"], "caution")
        self.assertIn("1280", mag["note"])

    def test_just_above_cap_but_below_hd_is_caution_with_cap_note(self):
        mag = governor.magnitude_advice("renderTOP", {"resolutionw": 1600, "resolutionh": 900})
        self.assertEqual(mag["level"], "caution")
        self.assertIn("1280", mag["note"])

    def test_4k_output_heavy(self):
        mag = governor.magnitude_advice("moviefileoutTOP", {"resolutionw": 3840, "resolutionh": 2160})
        self.assertEqual(mag["level"], "heavy")
        self.assertIn("1280", mag["note"])  # cap note still present above 1280

    def test_4k_by_height_alone_heavy(self):
        # height >= 2160 counts as 4K even if width is unusual
        self.assertEqual(self.lvl("renderTOP", {"resolutionh": 2160, "resolutionw": 1000}), "heavy")

    def test_generic_w_h_keys(self):
        self.assertEqual(self.lvl("someTOP", {"w": 3840, "h": 2160}), "heavy")

    # ── instance / particle counts ──────────────────────────────────────────────────────────────────
    def test_small_instances_ok(self):
        self.assertEqual(self.lvl("geometryCOMP", {"instancecount": 500}), "ok")

    def test_medium_instances_caution(self):
        self.assertEqual(self.lvl("geometryCOMP", {"instancecount": 20_000}), "caution")

    def test_huge_instances_heavy(self):
        self.assertEqual(self.lvl("geometryCOMP", {"numparticles": 500_000}), "heavy")

    # ── render passes ──────────────────────────────────────────────────────────────────────────────
    def test_passes_bands(self):
        self.assertEqual(self.lvl("renderTOP", {"npasses": 2}), "ok")
        self.assertEqual(self.lvl("renderTOP", {"npasses": 8}), "caution")
        self.assertEqual(self.lvl("renderTOP", {"npasses": 64}), "heavy")

    # ── no-signal + robustness ──────────────────────────────────────────────────────────────────────
    def test_no_heuristic_is_ok(self):
        self.assertEqual(self.lvl("blurTOP", {"size": 4}), "ok")
        self.assertEqual(self.lvl("levelTOP", {}), "ok")

    def test_never_raises_on_garbage(self):
        for bad in (None, {}, {"resolutionw": "abc"}, {"resolutionw": None},
                    {"instancecount": []}, {"npasses": float("nan")}):
            r = governor.magnitude_advice("x", bad)
            self.assertIn(r["level"], ("ok", "caution", "heavy"))
        self.assertIn(governor.magnitude_advice(None, None)["level"], ("ok", "caution", "heavy"))

    def test_returns_shape(self):
        r = governor.magnitude_advice("moviefileoutTOP", {"resolutionw": 3840})
        self.assertIn("level", r)
        self.assertIn("note", r)


class TestClassifyBand(unittest.TestCase):
    def test_ram_critical(self):
        b, g = governor.classify_band(None, 2.0)
        self.assertEqual(b, "critical")

    def test_ram_caution(self):
        b, _ = governor.classify_band(None, 6.0)
        self.assertEqual(b, "caution")

    def test_ram_ok(self):
        b, _ = governor.classify_band(None, 32.0)
        self.assertEqual(b, "ok")

    def test_vram_unknown_note_appended_by_default(self):
        # default vram_known=False (TD exposes no whole-card VRAM) -> the honest note is in guidance
        b, g = governor.classify_band(None, 32.0)
        self.assertEqual(b, "ok")
        self.assertIn("not exposed", g.lower())

    def test_vram_known_path_still_works(self):
        # parity: when a measured VRAM figure IS supplied, the VRAM band applies (no unknown note)
        b, g = governor.classify_band(0.5, 32.0, vram_known=True)
        self.assertEqual(b, "critical")
        self.assertNotIn("not exposed", g.lower())

    def test_threshold_is_strict(self):
        # exactly at the threshold is NOT below it (margin semantics)
        b, _ = governor.classify_band(None, governor.RAM_CRITICAL_GB)
        self.assertNotEqual(b, "critical")

    def test_garbage_input_does_not_raise(self):
        b, _ = governor.classify_band(None, None)
        self.assertIn(b, ("ok", "caution", "critical"))


class TestEnvelopeAndGate(unittest.TestCase):
    def test_envelope_status_returns_band(self):
        env = governor.envelope_status()
        self.assertIsInstance(env, dict)
        self.assertIn("band", env)
        # always documents the honest VRAM telemetry limit
        self.assertIn("vram_basis", env)

    def test_governor_gate_fail_soft(self):
        # gate must NOT raise unless the live band is genuinely critical (fail-soft on unknown/ok/caution)
        try:
            s = governor.governor_gate("selftest")
            self.assertIsInstance(s, dict)
        except ValueError as ve:
            self.assertIn("refused", str(ve))


if __name__ == "__main__":
    unittest.main()
