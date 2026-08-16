"""Tests for the mechanical component failure models."""

import json
import math
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
        # F_pullout = pi * 0.002 * 0.005 * 1.0 * 40e6 = 1256.6 N.
        # Added load at 100 g on 0.05 kg / 4 screws = 1.23 N.
        result = analyze(
            {"type": "screw", "preload_n": 15.0},
            {"drop": {"peak_accel_g": 100.0}},
        )
        pullout = result["metrics"]["pull_out_force_n"]
        self.assertAlmostEqual(pullout, 1256.6, delta=0.5)
        margin = pullout / (15.0 + 0.05 * 100.0 * 9.80665 / 4.0)
        self.assertAlmostEqual(result["metrics"]["margin"], margin, places=3)
        self.assertEqual(result["status"], "pass")

    def test_pullout_uses_tensile_yield_shear_allowable(self):
        # The thread-stripping screening must use the boss shear-out
        # allowable tau = S_y (molded-plastic pull-out data 0.75-1.0x S_y),
        # NOT the 0.2 * S_y cantilever-bending convention: an M2 x 5 mm
        # engagement boss in ABS (S_y = 40 MPa) strips at ~754 N (0.75x) to
        # ~1257 N (1.0x), not at the old 150.8 N.
        result = analyze(
            {"type": "screw", "preload_n": 15.0, "transport_vibration_g_rms": 0.0},
            {"drop": {"peak_accel_g": 10.0}},
        )
        pullout = result["metrics"]["pull_out_force_n"]
        self.assertAlmostEqual(pullout, math.pi * 0.002 * 0.005 * 40e6, delta=0.5)
        self.assertGreater(pullout, 3.0 * math.pi * 0.002 * 0.005 * 8e6)

    def test_engagement_length_scales_pullout(self):
        # Doubling the engaged thread length doubles the stripping capacity.
        short = analyze(
            {"type": "screw", "engagement_length_m": 0.0025},
            {"drop": {"peak_accel_g": 100.0}},
        )
        long = analyze(
            {"type": "screw", "engagement_length_m": 0.005},
            {"drop": {"peak_accel_g": 100.0}},
        )
        self.assertAlmostEqual(
            long["metrics"]["pull_out_force_n"],
            2.0 * short["metrics"]["pull_out_force_n"],
            places=2,
        )

    def test_short_engagement_warns(self):
        # 1.0x screw diameter engagement is below the 2.5x recommendation.
        result = analyze(
            {"type": "screw", "engagement_length_m": 0.002},
            {"drop": {"peak_accel_g": 10.0}},
        )
        self.assertEqual(result["status"], "warn")
        self.assertTrue(any(f["code"] == "SCREW_ENGAGEMENT_SHORT" for f in result["findings"]))

    def test_thin_boss_wall_warns(self):
        # OD 2.4 mm / ID 2.0 mm -> 0.2 mm wall, below the 0.5 mm minimum.
        result = analyze(
            {
                "type": "screw",
                "boss_inner_diameter_m": 0.002,
                "boss_outer_diameter_m": 0.0024,
            },
            {"drop": {"peak_accel_g": 10.0}},
        )
        self.assertEqual(result["status"], "warn")
        self.assertTrue(any(f["code"] == "SCREW_BOSS_WALL_THIN" for f in result["findings"]))

    def test_hoop_stress_reported(self):
        # Default boss (ID 2.0 mm, OD 4.0 mm, wall 1.0 mm) reports a finite
        # hoop stress and passes at 100 g.
        result = analyze(
            {"type": "screw"},
            {"drop": {"peak_accel_g": 100.0}},
        )
        self.assertIsNotNone(result["metrics"]["hoop_stress_pa"])
        self.assertEqual(result["status"], "pass")

    def test_extreme_preload_hoop_fails(self):
        # A huge preload inflates the radial pressure until the boss hoop
        # stress exceeds yield: boss radial-crack risk.
        result = analyze(
            {"type": "screw", "preload_n": 5000.0},
            {"drop": {"peak_accel_g": 10.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "SCREW_BOSS_HOOP_FAIL" for f in result["findings"]))

    def test_peak_force_path_preferred_over_accel(self):
        # When the impact module supplies peak_force_n, the screw load uses
        # F/count instead of m*g*G/count: 100 N / 4 screws = 25 N added.
        result = analyze(
            {"type": "screw", "preload_n": 0.0},
            {"drop": {"peak_force_n": 100.0, "peak_accel_g": 2000.0}},
        )
        self.assertAlmostEqual(result["metrics"]["added_load_n"], 25.0, places=3)
        # The same joint fed only the acceleration falls back to m*g*G/count.
        fallback = analyze(
            {"type": "screw", "preload_n": 0.0},
            {"drop": {"peak_accel_g": 2000.0}},
        )
        self.assertAlmostEqual(
            fallback["metrics"]["added_load_n"],
            0.05 * 2000.0 * 9.80665 / 4.0,
            places=3,
        )

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
        # 0.02 kg * 1000 g * 9.81 / 4e-4 m^2 = 0.49 MPa = 1.63x the
        # 0.3 MPa acrylic-foam allowable: beyond the screening band -> fail.
        result = analyze(
            {"type": "adhesive", "adhesive": "acrylic_foam"},
            {"drop": {"peak_accel_g": 1000.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertGreaterEqual(result["metrics"]["utilization"], 1.2)

    def test_high_impact_marginal_warn(self):
        # 700 g gives 1.14x the allowable: inside the [1.0, 1.2) marginal
        # band, which warns (within the class-constant screening uncertainty
        # band) instead of hard-failing at exactly 1.0.
        result = analyze(
            {"type": "adhesive", "adhesive": "acrylic_foam"},
            {"drop": {"peak_accel_g": 700.0}},
        )
        self.assertEqual(result["status"], "warn")
        self.assertGreaterEqual(result["metrics"]["utilization"], 1.0)
        self.assertLess(result["metrics"]["utilization"], 1.2)

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


class KnifeEdgeBoundaryTests(unittest.TestCase):
    """Every channel must implement the unified knife-edge policy: exactly
    1.0 -> warn (marginal), 1.19 -> warn, 1.2 -> fail, 1.21 -> fail."""

    def test_screw_boundary(self):
        # Margin (allowable/load) is the screw sign convention: usage ratios
        # 1.0/1.19/1.2/1.21 map to margins 1/1.0, 1/1.19, 1/1.2, 1/1.21.
        # Vibration loosening is disabled (0 g rms) so the pull-out band
        # alone drives the status.
        pullout = math.pi * 0.002 * 0.005 * 1.0 * 40e6
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            accel = (pullout / (1.0 / ratio)) * 4.0 / (0.05 * 9.80665)
            result = analyze(
                {
                    "type": "screw",
                    "preload_n": 0.0,
                    "supported_mass_kg": 0.05,
                    "transport_vibration_g_rms": 0.0,
                },
                {"drop": {"peak_accel_g": accel}},
            )
            self.assertEqual(result["status"], expected, "screw ratio {:.2f}".format(ratio))
            self.assertAlmostEqual(result["usage_ratio"], ratio, places=5)
            if expected == "warn":
                self.assertTrue(any(f["code"] == "SCREW_PULLOUT_MARGINAL" for f in result["findings"]))

    def test_mount_boundary(self):
        # stress_ratio = supported*accel*g / (count*area) / (0.6*60e6).
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            area = math.pi * 0.0025 ** 2 / 4.0
            accel = ratio * 0.6 * 60e6 * 4.0 * area / (0.02 * 9.80665)
            result = analyze(
                {"type": "mount"},
                {"drop": {"peak_accel_g": accel}},
            )
            self.assertEqual(result["status"], expected, "mount ratio {:.2f}".format(ratio))
            if expected == "warn":
                self.assertTrue(any(f["code"] == "MOUNT_CRUSH_MARGINAL" for f in result["findings"]))

    def test_adhesive_boundary(self):
        # delta_t = 0 kills the thermal term, so utilization is exactly
        # impact_shear / allowable = mass*accel*g / (area*allowable).
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            accel = ratio * 4e-4 * 0.3e6 / (0.02 * 9.80665)
            result = analyze(
                {"type": "adhesive", "delta_temperature_k": 0.0},
                {"drop": {"peak_accel_g": accel}},
            )
            self.assertEqual(result["status"], expected, "adhesive ratio {:.2f}".format(ratio))
            self.assertAlmostEqual(result["metrics"]["utilization"], ratio, places=5)


class InvalidInputMechTests(unittest.TestCase):
    def test_nan_and_inf_spec_values_rejected(self):
        specs = (
            {"type": "screw", "preload_n": float("nan")},
            {"type": "mount", "column_diameter_m": float("inf")},
            {"type": "adhesive", "area_m2": float("-inf")},
        )
        for spec in specs:
            result = analyze(spec, {"drop": {"peak_accel_g": 400.0}})
            self.assertEqual(result["status"], "not_evaluated", spec)
            self.assertTrue(any(f["code"] == "NOT_EVALUATED" for f in result["findings"]))

    def test_huge_finite_spec_value_rejected(self):
        result = analyze(
            {"type": "adhesive", "area_m2": 1e13},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "not_evaluated")


if __name__ == "__main__":
    unittest.main()
