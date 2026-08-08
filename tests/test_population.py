import json
import unittest

from mouse_sim.drop_sim import _unit_variation, box_inertia, support_points
from mouse_sim.population import (
    MAX_SAMPLE_COUNT,
    MIN_SAMPLE_COUNT,
    PER_UNIT_FAILURES_CAP,
    clamp_sample_count,
    draw_unit_parameters,
    profile_usage,
    run_population,
)

CUBE_SUPPORT = support_points(
    [(x, y, z) for x in (-0.05, 0.05) for y in (-0.05, 0.05) for z in (-0.05, 0.05)]
)
CUBE_INERTIA = box_inertia(0.1, ((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05)))


def make_context():
    return {
        "mass_kg": 0.1,
        "inertia_kg_m2": CUBE_INERTIA,
        "support": CUBE_SUPPORT,
        "materials": {"shell": "abs", "skate": "ptfe"},
        "environment_temperature_k": 296.0,
    }


def make_config(**overrides):
    config = {
        "sample_count": 100,
        "profile": "esports_fps",
        "lifespan_days": 730,
        "base_seed": 0,
        "workers": 1,
        "drop_height_m": 0.75,
        "drop_surface": "concrete",
        "drop_orientation": "flat",
        "tolerance_scale": 1.0,
        "components": None,
    }
    config.update(overrides)
    return config


def battery_only_config(**overrides):
    config = make_config(**overrides)
    config["components"] = [{"component_id": "battery_pack", "type": "battery"}]
    return config


class ClampSampleCountTests(unittest.TestCase):
    def test_sample_count_clamped(self):
        self.assertEqual(clamp_sample_count(50), MIN_SAMPLE_COUNT)
        self.assertEqual(clamp_sample_count(100), MIN_SAMPLE_COUNT)
        self.assertEqual(clamp_sample_count(10**6), MAX_SAMPLE_COUNT)
        self.assertEqual(clamp_sample_count(10**5), MAX_SAMPLE_COUNT)
        self.assertEqual(clamp_sample_count(5000), 5000)

    def test_run_clamps_low_end(self):
        result = run_population(make_config(sample_count=10), make_context())
        self.assertEqual(result["sample_count"], MIN_SAMPLE_COUNT)


class DrawUnitParametersTests(unittest.TestCase):
    def test_deterministic_and_nominal(self):
        first = draw_unit_parameters(7, 1.0, CUBE_SUPPORT)
        second = draw_unit_parameters(7, 1.0, CUBE_SUPPORT)
        self.assertEqual(first, second)
        nominal = draw_unit_parameters(7, 0.0, CUBE_SUPPORT)
        self.assertEqual(nominal["mass_scale"], 1.0)
        self.assertEqual(nominal["inertia_scale"], (1.0, 1.0, 1.0))
        self.assertEqual(nominal["com_offset_m"], (0.0, 0.0, 0.0))
        self.assertEqual(nominal["battery_offset_m"], (0.0, 0.0))
        for name in (
            "screw_preload_scale",
            "clip_thickness_scale",
            "pcb_thickness_scale",
            "adhesive_area_scale",
            "switch_force_scale",
        ):
            self.assertEqual(nominal[name], 1.0)
        self.assertAlmostEqual(first["mass_scale"], 1.0, delta=0.03)

    def test_matches_drop_sim_unit_variation(self):
        seed = 12345
        mass_scale, inertia_scale, com_offset, _, _ = _unit_variation(seed, CUBE_SUPPORT)
        drawn = draw_unit_parameters(seed, 1.0, CUBE_SUPPORT)
        self.assertAlmostEqual(drawn["mass_scale"], mass_scale, places=12)
        for axis in range(3):
            self.assertAlmostEqual(drawn["inertia_scale"][axis], inertia_scale[axis], places=12)
            self.assertAlmostEqual(drawn["com_offset_m"][axis], com_offset[axis], places=12)

    def test_seed_changes_draws(self):
        first = draw_unit_parameters(1, 1.0, CUBE_SUPPORT)
        second = draw_unit_parameters(2, 1.0, CUBE_SUPPORT)
        self.assertNotEqual(first["mass_scale"], second["mass_scale"])

    def test_tolerance_scale_scales_band(self):
        half = draw_unit_parameters(5, 0.5, CUBE_SUPPORT)
        full = draw_unit_parameters(5, 1.0, CUBE_SUPPORT)
        self.assertAlmostEqual(
            (half["mass_scale"] - 1.0) * 2.0, full["mass_scale"] - 1.0, delta=1e-9
        )


class ProfileUsageTests(unittest.TestCase):
    def test_known_profile(self):
        usage = profile_usage("esports_fps", 730)
        self.assertEqual(usage["actuation_cycles"], 8000 * 730)
        self.assertEqual(usage["scroll_encoder_rotations"], 200 * 730)
        self.assertEqual(usage["age_days"], 730.0)
        self.assertEqual(usage["prior_drops"], 8)
        self.assertIn("slide_distance_km", usage)

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            profile_usage("no_such_profile", 730)
        with self.assertRaises(ValueError):
            run_population(make_config(profile="no_such_profile"), make_context())


class PopulationRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.default_config = make_config(sample_count=100, workers=1)
        cls.default_result = run_population(cls.default_config, cls.context)
        cls.partial_result = run_population(
            make_config(
                sample_count=100, workers=1, profile="esports_fps", lifespan_days=1825
            ),
            cls.context,
        )

    def test_run_deterministic_serial(self):
        rerun = run_population(self.default_config, self.context)
        self.assertEqual(rerun, self.default_result)

    def test_run_parallel_matches_serial(self):
        parallel = run_population(make_config(sample_count=100, workers=2), self.context)
        for key, value in parallel.items():
            if key in ("workers", "diagnostics"):
                continue
            self.assertEqual(value, self.default_result[key], key)

    def test_default_run_healthy_at_two_years(self):
        # The 2-year esports reference design passes every component: the
        # platform baseline must be healthy so failure statistics have
        # contrast against design variants.
        result = self.default_result
        self.assertEqual(result["units_failed"], 0)
        self.assertEqual(result["failure_rate"], 0.0)
        self.assertEqual(max(c["failures"] for c in result["component_failure_rates"]), 0)

    def test_partial_failure_run_has_contrast(self):
        result = self.partial_result
        self.assertGreater(result["units_failed"], 0)
        self.assertLess(result["units_failed"], result["sample_count"])

    def test_component_failure_rates_consistent(self):
        result = self.default_result
        total = result["total_component_failures"]
        self.assertEqual(
            sum(c["failures"] for c in result["component_failure_rates"]), total
        )
        self.assertGreaterEqual(total, result["units_failed"])
        self.assertEqual(len(result["per_unit_failures"]), min(total, PER_UNIT_FAILURES_CAP))
        by_id = {c["component_id"]: c for c in result["component_failure_rates"]}
        counts = {}
        previous_unit = -1
        for record in result["per_unit_failures"]:
            self.assertIn(record["component_id"], by_id)
            self.assertEqual(by_id[record["component_id"]]["type"], record["type"])
            self.assertGreaterEqual(record["unit"], previous_unit)
            previous_unit = record["unit"]
            key = (record["component_id"], record["type"])
            counts[key] = counts.get(key, 0) + 1
        for (component_id, ctype), count in counts.items():
            self.assertLessEqual(count, by_id[component_id]["failures"])
        ranks = [c["rank"] for c in result["component_failure_rates"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        self.assertEqual(len(result["weakest_components"]), 3)
        self.assertEqual(
            result["weakest_components"][0]["component_id"],
            result["component_failure_rates"][0]["component_id"],
        )

    def test_sensitivity_nonempty_and_sorted(self):
        sensitivity = self.partial_result["sensitivity"]
        # 14 component/manufacturing parameters + 4 shell parameters.
        self.assertEqual(len(sensitivity), 18)
        self.assertEqual(
            {entry["parameter"] for entry in sensitivity},
            set(self.partial_result["model"]["manufacturing_tolerances"]),
        )
        correlations = [entry["correlation"] for entry in sensitivity]
        self.assertIsNotNone(correlations[0])
        seen_none = False
        previous_abs = None
        for entry in sensitivity:
            if entry["correlation"] is None:
                seen_none = True
            else:
                self.assertFalse(seen_none)
                self.assertLessEqual(abs(entry["correlation"]), 1.0)
                if previous_abs is not None:
                    self.assertLessEqual(abs(entry["correlation"]), previous_abs + 1e-9)
                previous_abs = abs(entry["correlation"])
            self.assertIsInstance(entry["mean_value"], float)
            self.assertIsInstance(entry["std_value"], float)

    def test_survival_monotonic_non_increasing(self):
        for result in (self.default_result, self.partial_result):
            survival = result["survival"]
            self.assertEqual(
                [entry["usage_fraction"] for entry in survival],
                [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            )
            rates = [entry["survival_rate"] for entry in survival]
            for lower, higher in zip(rates, rates[1:]):
                self.assertLessEqual(higher, lower + 1e-9)

    def test_wilson_interval_bounds(self):
        result = self.default_result
        for interval in [result["wilson_ci"]] + [
            c["wilson_ci"] for c in result["component_failure_rates"]
        ]:
            self.assertLessEqual(interval["low"], interval["high"])
            self.assertGreaterEqual(interval["low"], 0.0)
            self.assertLessEqual(interval["high"], 1.0)

    def test_result_json_clean(self):
        text = json.dumps(self.default_result)
        self.assertEqual(json.loads(text), self.default_result)

    def test_battery_high_drop_fails_benign_does_not(self):
        high = run_population(
            battery_only_config(sample_count=100, drop_height_m=1.5), self.context
        )
        benign = run_population(
            battery_only_config(sample_count=100, drop_height_m=0.2), self.context
        )
        battery_high = high["component_failure_rates"][0]
        battery_benign = benign["component_failure_rates"][0]
        self.assertEqual(battery_high["component_id"], "battery_pack")
        self.assertGreater(battery_high["failures"], 0)
        self.assertGreater(high["units_failed"], 0)
        self.assertEqual(battery_benign["failures"], 0)
        self.assertEqual(benign["units_failed"], 0)
        self.assertEqual(benign["failure_rate"], 0.0)
        self.assertEqual(benign["wilson_ci"]["low"], 0.0)
        self.assertLess(benign["wilson_ci"]["high"], 0.05)

    def test_seed_sensitivity(self):
        zero = self.partial_result
        one = run_population(
            make_config(sample_count=100, workers=1, profile="general", base_seed=1),
            self.context,
        )
        self.assertNotEqual(zero["units_failed"], one["units_failed"])
        self.assertNotEqual(zero["failure_rate"], one["failure_rate"])

    def test_tolerance_scale_zero_nominal_run(self):
        result = run_population(make_config(sample_count=100, tolerance_scale=0.0), self.context)
        by_name = {entry["parameter"]: entry for entry in result["sensitivity"]}
        self.assertEqual(by_name["mass_scale"]["mean_value"], 1.0)
        self.assertEqual(by_name["mass_scale"]["std_value"], 0.0)
        self.assertEqual(by_name["screw_preload_scale"]["mean_value"], 1.0)

    def test_bad_surface_raises(self):
        with self.assertRaises(ValueError):
            run_population(make_config(drop_surface="jello"), self.context)


if __name__ == "__main__":
    unittest.main()
