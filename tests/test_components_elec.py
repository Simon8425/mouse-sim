"""Tests for the electronics component failure models."""

import json
import math
import unittest

from mouse_sim.components_elec import (
    COMPONENT_TYPES,
    DETENTS_PER_REVOLUTION,
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


class ComponentElecBasicsTests(unittest.TestCase):
    def test_component_types(self):
        self.assertEqual(COMPONENT_TYPES, ("pcb", "battery", "switch", "encoder"))

    def test_defaults_work_for_every_type(self):
        for ctype in COMPONENT_TYPES:
            spec = defaults(ctype)
            self.assertIsInstance(spec, dict)
            self.assertTrue(spec)
        with self.assertRaises(ValueError):
            defaults("nope")

    def test_fresh_unit_not_evaluated(self):
        for ctype in COMPONENT_TYPES:
            result = analyze({"component_id": "c", "type": ctype}, FRESH_CONTEXT)
            self.assertEqual(result["status"], "not_evaluated", ctype)
            self.assertEqual(result["validity"], "not_evaluated", ctype)

    def test_unknown_type_graceful(self):
        result = analyze({"component_id": "x", "type": "warp_drive"}, FRESH_CONTEXT)
        self.assertEqual(result["status"], "not_evaluated")
        self.assertTrue(result["findings"])

    def test_garbage_input_graceful(self):
        result = analyze("not-a-dict", None)
        self.assertEqual(result["status"], "not_evaluated")
        result = analyze_many([{"type": "battery"}, None, 42], None)
        self.assertEqual(len(result), 3)

    def test_deterministic_and_json_clean(self):
        context = {
            "drop": {"peak_accel_g": 300.0},
            "lifecycle": {"actuation_cycles": 5_000_000, "scroll_encoder_rotations": 100_000},
        }
        first = analyze({"component_id": "c", "type": "switch"}, context)
        second = analyze({"component_id": "c", "type": "switch"}, context)
        self.assertEqual(first, second)
        json.dumps(first)


class BatteryComponentTests(unittest.TestCase):
    def test_shock_limit_fail(self):
        result = analyze(
            {"component_id": "b", "type": "battery", "mass_kg": 0.02},
            {"drop": {"peak_accel_g": 600.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "BATTERY_SHOCK_EXCEEDED" for f in result["findings"]))
        self.assertGreaterEqual(result["metrics"]["shock_margin"], 1.0)

    def test_crush_transmission_pass(self):
        # 0.02 kg * 400 g * 9.81 * 0.5 transmission = 39.2 N < 130 N crush.
        # A cradle rated for the drop (latch retention >= transmitted cell
        # inertia) keeps the cell seated.
        result = analyze(
            {
                "component_id": "b",
                "type": "battery",
                "mass_kg": 0.02,
                "latch_retention_n": 40.0,
            },
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "pass")
        self.assertAlmostEqual(result["metrics"]["transmitted_force_n"], 39.227, places=3)

    def test_latch_dislodgement_fails(self):
        # F_inertia = m * a_peak * 0.5 = 0.02 * 400 g * 9.81 * 0.5 = 39.2 N
        # exceeds the 8 N retention hook: BLOCKER-class finding.
        result = analyze(
            {"component_id": "b", "type": "battery", "mass_kg": 0.02},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any(f["code"] == "BATTERY_LATCH_DISLODGED" for f in result["findings"])
        )
        self.assertGreater(result["metrics"]["latch_inertia_n"], result["metrics"]["latch_retention_n"])

    def test_latch_margin_at_150g(self):
        # 150 g is the top of the gaming-mouse design band:
        # 0.02 * 150 * 9.81 * 0.5 = 14.7 N vs the 8 N hook -> still dislodges.
        result = analyze(
            {"component_id": "b", "type": "battery", "mass_kg": 0.02},
            {"drop": {"peak_accel_g": 150.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "BATTERY_LATCH_DISLODGED" for f in result["findings"]))

    def test_crush_fail(self):
        result = analyze(
            {"component_id": "b", "type": "battery", "mass_kg": 0.1},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "BATTERY_CRUSH_RISK" for f in result["findings"]))

    def test_temperature_warn(self):
        result = analyze(
            {"component_id": "b", "type": "battery"},
            {"environment_temperature_k": 353.15},
        )
        self.assertEqual(result["status"], "warn")
        self.assertTrue(any(f["code"] == "BATTERY_TEMPERATURE_LIMIT" for f in result["findings"]))


class PcbComponentTests(unittest.TestCase):
    def test_high_shock_fails(self):
        result = analyze(
            {"component_id": "p", "type": "pcb", "component_mass_kg": 0.05},
            {"drop": {"peak_accel_g": 3000.0}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any(
                f["code"] in ("PCB_FLEX_OVER_STRESS", "PCB_SOLDER_SHOCK_FAILURE")
                for f in result["findings"]
            )
        )
        self.assertIsNotNone(result["metrics"]["flex_stress_pa"])

    def test_thermal_fatigue_warn_at_hot_long_life(self):
        result = analyze(
            {"component_id": "p", "type": "pcb", "thermal_cycles_per_day": 8},
            {"lifecycle": {"age_days": 1825}, "environment_temperature_k": 313.15},
        )
        self.assertEqual(result["status"], "warn")
        self.assertGreater(result["metrics"]["thermal_damage"], 0.3)

    def test_thermal_fatigue_fail_at_hot_long_life(self):
        result = analyze(
            {"component_id": "p", "type": "pcb", "thermal_cycles_per_day": 20, "delta_temperature_k": 50},
            {"lifecycle": {"age_days": 3650}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertGreaterEqual(result["metrics"]["thermal_damage"], 1.0)

    def test_mild_shock_passes(self):
        result = analyze(
            {"component_id": "p", "type": "pcb"},
            {"drop": {"peak_accel_g": 150.0}},
        )
        self.assertEqual(result["status"], "pass")


class SwitchComponentTests(unittest.TestCase):
    def test_rated_life_exceeded(self):
        result = analyze(
            {"component_id": "s", "type": "switch", "switch_type": "mechanical"},
            {"lifecycle": {"actuation_cycles": 25_000_000}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertGreaterEqual(result["metrics"]["usage_damage"], 1.0)

    def test_optical_switch_higher_rating(self):
        result = analyze(
            {"component_id": "s", "type": "switch", "switch_type": "optical"},
            {"lifecycle": {"actuation_cycles": 25_000_000}},
        )
        self.assertEqual(result["status"], "pass")

    def test_stalk_fatigue_from_force(self):
        # A heavy actuation force on a thin stalk drives stalk fatigue.
        result = analyze(
            {
                "component_id": "s",
                "type": "switch",
                "actuation_force_n": 1.5,
                "button_stalk_diameter_m": 0.0015,
            },
            {"lifecycle": {"actuation_cycles": 10_000_000}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(f["code"] == "SWITCH_STALK_FATIGUE" for f in result["findings"]))


class EncoderComponentTests(unittest.TestCase):
    def test_detent_conversion_mechanical_fail(self):
        # 720,000 wheel steps / 24 = 30,000 revolutions = 1.2x the rating
        # (the marginal band is 1.0-1.2; fail starts at 1.2).
        result = analyze(
            {"component_id": "e", "type": "encoder", "encoder_type": "mechanical"},
            {"lifecycle": {"scroll_encoder_rotations": 720_000}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertAlmostEqual(result["metrics"]["usage_rotations"], 30_000.0, places=3)
        self.assertAlmostEqual(result["metrics"]["usage_damage"], 1.2, places=6)
        # Exactly at the rating is a marginal warn, not a hard fail.
        marginal = analyze(
            {"component_id": "e", "type": "encoder", "encoder_type": "mechanical"},
            {"lifecycle": {"scroll_encoder_rotations": 600_000}},
        )
        self.assertEqual(marginal["status"], "warn")

    def test_optical_encoder_healthy(self):
        result = analyze(
            {"component_id": "e", "type": "encoder", "encoder_type": "optical"},
            {"lifecycle": {"scroll_encoder_rotations": 600_000}},
        )
        self.assertEqual(result["status"], "pass")

    def test_mechanical_healthy_at_moderate_use(self):
        result = analyze(
            {"component_id": "e", "type": "encoder"},
            {"lifecycle": {"scroll_encoder_rotations": 100_000}},
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(DETENTS_PER_REVOLUTION, 24)


class KnifeEdgeBoundaryTests(unittest.TestCase):
    """Every channel must implement the unified knife-edge policy: exactly
    1.0 -> warn (marginal), 1.19 -> warn, 1.2 -> fail, 1.21 -> fail."""

    def test_battery_shock_boundary(self):
        # shock_margin = accel_g / shock_limit; crush channel stays ~0.45.
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            result = analyze(
                {"component_id": "b", "type": "battery", "shock_limit_g": 500.0, "latch_retention_n": 100.0},
                {"drop": {"peak_accel_g": 500.0 * ratio}},
            )
            self.assertEqual(result["status"], expected, "shock ratio {:.2f}".format(ratio))
            self.assertAlmostEqual(result["metrics"]["shock_margin"], ratio, places=6)
            if expected == "warn":
                self.assertTrue(any(f["code"] == "BATTERY_SHOCK_MARGINAL" for f in result["findings"]))

    def test_switch_boundary(self):
        # usage_damage = actuation_cycles / 20M (mechanical class rating).
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            result = analyze(
                {"component_id": "s", "type": "switch", "switch_type": "mechanical"},
                {"lifecycle": {"actuation_cycles": 20_000_000 * ratio}},
            )
            self.assertEqual(result["status"], expected, "switch ratio {:.2f}".format(ratio))
            if expected == "warn":
                self.assertTrue(any(f["code"] == "SWITCH_RATED_LIFE_MARGINAL" for f in result["findings"]))

    def test_encoder_boundary(self):
        # 24 detents/revolution: 600,000 steps * ratio / 24 = 25,000 * ratio
        # rotations against the 25,000 rotation rating.
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            result = analyze(
                {"component_id": "e", "type": "encoder", "encoder_type": "mechanical"},
                {"lifecycle": {"scroll_encoder_rotations": 600_000 * ratio}},
            )
            self.assertEqual(result["status"], expected, "encoder ratio {:.2f}".format(ratio))
            if expected == "warn":
                self.assertTrue(any(f["code"] == "ENCODER_RATED_LIFE_MARGINAL" for f in result["findings"]))

    def test_pcb_thermal_boundary(self):
        # 40 K daily cycle -> 20,000 cycles to failure; 8 cycles/day makes
        # damage == age_days / 2500 exactly (integer arithmetic).
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            result = analyze(
                {"component_id": "p", "type": "pcb", "thermal_cycles_per_day": 8, "delta_temperature_k": 40},
                {"lifecycle": {"age_days": 2500.0 * ratio}},
            )
            self.assertEqual(result["status"], expected, "thermal ratio {:.2f}".format(ratio))
            self.assertAlmostEqual(result["metrics"]["thermal_damage"], ratio, places=6)
            if expected == "warn":
                # The [1.0, 1.2) band must emit the MARGINAL finding, not
                # the earlier margin-low WEAR branch.
                self.assertTrue(any(f["code"] == "PCB_SOLDER_THERMAL_MARGINAL" for f in result["findings"]))
                self.assertFalse(any(f["code"] == "PCB_SOLDER_THERMAL_WEAR" for f in result["findings"]))

    @staticmethod
    def _flex_stress(accel_g):
        # Mirrors the model's plate-bending arithmetic for the default
        # 40x60x1.6 mm board (b/a = 1.5 -> beta = 0.46) so the round-trip
        # allowable lands the ratio exactly on the target.
        a, b, thickness = 0.04, 0.06, 0.0016
        board_mass = 1850.0 * a * b * thickness
        load = (board_mass + 0.002) * accel_g * 9.80665
        q = load / (a * b)
        return 0.46 * q * a ** 2 / (thickness ** 2)

    def test_pcb_flex_boundary(self):
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            result = analyze(
                {"component_id": "p", "type": "pcb", "allowable_flex_stress_pa": self._flex_stress(100.0) / ratio},
                {"drop": {"peak_accel_g": 100.0}},
            )
            self.assertEqual(result["status"], expected, "flex ratio {:.2f}".format(ratio))
            if expected == "warn":
                self.assertTrue(any(f["code"] == "PCB_FLEX_MARGINAL" for f in result["findings"]))
                self.assertFalse(any(f["code"] == "PCB_FLEX_MARGIN_LOW" for f in result["findings"]))

    @staticmethod
    def _shock_shear(accel_g):
        # Mirrors the model's direct-inertia solder shear for a 0.05 kg
        # component on 200 joints of 2e-7 m^2.
        force = 0.05 * accel_g * 9.80665
        return force / (200 * 2e-7)

    def test_pcb_solder_shock_boundary(self):
        for ratio, expected in ((1.0, "warn"), (1.19, "warn"), (1.2, "fail"), (1.21, "fail")):
            result = analyze(
                {
                    "component_id": "p",
                    "type": "pcb",
                    "component_mass_kg": 0.05,
                    "solder_joint_area_m2": 2e-7,
                    "solder_joint_count": 200,
                    "solder_allowable_shear_pa": self._shock_shear(1000.0) / ratio,
                    "allowable_flex_stress_pa": 1e9,
                },
                {"drop": {"peak_accel_g": 1000.0}},
            )
            self.assertEqual(result["status"], expected, "shock ratio {:.2f}".format(ratio))
            if expected == "warn":
                self.assertTrue(any(f["code"] == "PCB_SOLDER_SHOCK_MARGINAL" for f in result["findings"]))


class InvalidInputElecTests(unittest.TestCase):
    def test_nan_and_inf_spec_values_rejected(self):
        specs = (
            {"component_id": "b", "type": "battery", "mass_kg": float("nan")},
            {"component_id": "p", "type": "pcb", "allowable_flex_stress_pa": float("inf")},
            {"component_id": "s", "type": "switch", "rated_cycles": float("-inf")},
        )
        for spec in specs:
            result = analyze(spec, {"drop": {"peak_accel_g": 400.0}})
            self.assertEqual(result["status"], "not_evaluated", spec)
            self.assertTrue(any(f["code"] == "NOT_EVALUATED" for f in result["findings"]))

    def test_huge_finite_spec_value_rejected(self):
        result = analyze(
            {"component_id": "b", "type": "battery", "crush_load_n": 1e13},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "not_evaluated")


if __name__ == "__main__":
    unittest.main()
