"""Tests for the mechanical component failure models."""

import json
import unittest

from mouse_sim.components_mech import (
    COMPONENT_TYPES,
    analyze,
    analyze_many,
    defaults,
)

FRESH_CONTEXT = {
    "mass_kg": 0.1,
    "inertia_kg_m2": [[1e-5, 0, 0], [0, 2e-5, 0], [0, 0, 1e-5]],
    "support": [(0.05, 0.03, 0.02)],
    "materials": {},
    "drop": None,
    "lifecycle": None,
    "environment_temperature_k": None,
}


class ComponentMechBasicsTests(unittest.TestCase):
    def test_component_types(self):
        self.assertEqual(COMPONENT_TYPES, ("screw", "clip", "mount", "adhesive"))

    def test_defaults_work_for_every_type(self):
        for ctype in COMPONENT_TYPES:
            spec = defaults(ctype)
            self.assertIsInstance(spec, dict)
            self.assertTrue(spec)

    def test_fresh_unit_not_evaluated_where_no_load(self):
        self.assertEqual(analyze({"type": "mount"}, FRESH_CONTEXT)["status"], "not_evaluated")
        self.assertEqual(
            analyze({"type": "adhesive"}, FRESH_CONTEXT)["status"], "not_evaluated"
        )

    def test_unknown_type_graceful(self):
        result = analyze({"type": "flux_capacitor"}, FRESH_CONTEXT)
        self.assertEqual(result["status"], "not_evaluated")

    def test_garbage_input_graceful(self):
        result = analyze("nope", None)
        self.assertEqual(result["status"], "not_evaluated")
        results = analyze_many([{"type": "screw"}, None], None)
        self.assertEqual(len(results), 2)

    def test_deterministic_and_json_clean(self):
        context = {"drop": {"peak_accel_g": 200.0}, "lifecycle": {"age_days": 730}}
        first = analyze({"type": "clip"}, context)
        second = analyze({"type": "clip"}, context)
        self.assertEqual(first, second)
        json.dumps(first)


class ScrewComponentTests(unittest.TestCase):
    def test_margin_math(self):
        # F_pullout = pi * 0.002 * 0.003 * 0.2 * 40e6 = 150.8 N.
        # Added load at 100 g on 0.05 kg / 4 screws = 1.23 N.
        result = analyze(
            {"type": "screw", "preload_n": 15.0},
            {"drop": {"peak_accel_g": 100.0}},
        )
        pullout = result["metrics"]["pull_out_force_n"]
        self.assertAlmostEqual(pullout, 150.8, delta=0.5)
        margin = pullout / (15.0 + 0.05 * 100.0 * 9.80665 / 4.0)
        self.assertAlmostEqual(result["metrics"]["margin"], margin, places=3)
        self.assertEqual(result["status"], "pass")

    def test_low_preload_loosening_warn(self):
        result = analyze(
            {"type": "screw", "preload_n": 1.5},
            {"drop": {"peak_accel_g": 10.0}},
        )
        self.assertEqual(result["status"], "warn")
        self.assertTrue(any(f["code"] == "SCREW_LOOSENING_RISK" for f in result["findings"]))

    def test_overload_fails(self):
        result = analyze(
            {"type": "screw", "preload_n": 15.0, "supported_mass_kg": 1.0},
            {"drop": {"peak_accel_g": 2000.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "SCREW_PULLOUT_RISK" for f in result["findings"]))


class ClipComponentTests(unittest.TestCase):
    def test_default_clip_passes(self):
        result = analyze({"type": "clip"}, {})
        self.assertEqual(result["status"], "pass")
        # k = 2e9 * 0.003 * 0.001^3 / (4 * 0.008^3) = 2930 N/m;
        # engagement 0.0006 m -> 1.76 N; retention over the 50-degree release
        # ramp with mu = 0.6: 1.76 * (0.6 + 1.192) / (1 - 0.6*1.192) = 11.05 N.
        self.assertAlmostEqual(result["metrics"]["retention_force_n"], 11.05, places=2)

    def test_creep_loses_retention_at_five_years(self):
        result = analyze(
            {"type": "clip", "disassembly_force_n": 6.0},
            {"lifecycle": {"age_days": 1825}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertAlmostEqual(result["metrics"]["creep_modulus_factor"], 0.5, places=4)
        self.assertTrue(any(f["code"] == "CLIP_RETENTION_LOST" for f in result["findings"]))

    def test_thin_beam_over_stress(self):
        result = analyze(
            {"type": "clip", "beam_thickness_m": 0.0002, "engagement_depth_m": 0.01}, {}
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "CLIP_OVER_STRESS" for f in result["findings"]))

    def test_creep_interpolation(self):
        result = analyze({"type": "clip"}, {"lifecycle": {"age_days": 417}})
        self.assertAlmostEqual(result["metrics"]["creep_modulus_factor"], 0.55, places=4)


class MountComponentTests(unittest.TestCase):
    def test_high_drop_compression_fails(self):
        result = analyze(
            {"type": "mount", "supported_mass_kg": 0.05},
            {"drop": {"peak_accel_g": 2000.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "MOUNT_CRUSH" for f in result["findings"]))

    def test_mild_drop_passes(self):
        result = analyze(
            {"type": "mount", "supported_mass_kg": 0.02},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "pass")
        # The stub column (L/d = 1.6) is below the Euler transition
        # slenderness: crush governs and the buckling check is skipped.
        self.assertIsNone(result["metrics"]["buckling_margin"])

    def test_slender_column_still_checks_buckling(self):
        # A slender column (large L/d) is above the transition slenderness
        # and the Euler margin is reported.
        result = analyze(
            {"type": "mount", "column_diameter_m": 0.001, "column_height_m": 0.02},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertIsNotNone(result["metrics"]["buckling_margin"])
        self.assertGreater(result["metrics"]["buckling_load_n"], 0.0)


class AdhesiveComponentTests(unittest.TestCase):
    def test_high_impact_fails(self):
        # 0.02 kg * 700 g * 9.81 / 4e-4 m^2 = 0.343 MPa > 0.3 MPa acrylic foam.
        result = analyze(
            {"type": "adhesive", "adhesive": "acrylic_foam"},
            {"drop": {"peak_accel_g": 700.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertGreaterEqual(result["metrics"]["utilization"], 1.0)

    def test_thermal_only_case(self):
        result = analyze(
            {"type": "adhesive", "adhesive": "cyanoacrylate", "delta_temperature_k": 60.0},
            {"environment_temperature_k": 313.15},
        )
        # Default d(alpha) = 60 ppm/K (battery-on-ABS class): strain
        # 60e-6 * 60 K = 3.6e-3; shear = 1e9 * 3.6e-3 / 2 = 1.8 MPa
        # vs 10 MPa allowable -> pass.
        self.assertEqual(result["status"], "pass")
        self.assertAlmostEqual(result["metrics"]["thermal_shear_pa"], 1.8e6, delta=0.1e6)

    def test_aging_derate_turns_marginal_to_fail(self):
        # An EXPOSED joint is aged (0.5 derate) and the 400 g shock pushes
        # the utilization over the aged allowable; an internal joint is not
        # aged and passes at the full allowable.
        result = analyze(
            {"type": "adhesive", "adhesive": "acrylic_foam", "exposed": True},
            {"drop": {"peak_accel_g": 400.0}, "lifecycle": {"age_days": 730}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["metrics"]["aging_factor"], 0.5)
        internal = analyze(
            {"type": "adhesive", "adhesive": "acrylic_foam"},
            {"drop": {"peak_accel_g": 400.0}, "lifecycle": {"age_days": 730}},
        )
        self.assertEqual(internal["metrics"]["aging_factor"], 1.0)
        self.assertEqual(internal["status"], "pass")


if __name__ == "__main__":
    unittest.main()
