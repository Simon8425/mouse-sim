"""Tests for the electronics component failure models."""

import json
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
        result = analyze(
            {"component_id": "b", "type": "battery", "mass_kg": 0.02},
            {"drop": {"peak_accel_g": 400.0}},
        )
        self.assertEqual(result["status"], "pass")
        self.assertAlmostEqual(result["metrics"]["transmitted_force_n"], 39.227, places=3)

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
        # (the marginal band is 1.0-1.2; fail starts above 1.2).
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


if __name__ == "__main__":
    unittest.main()
