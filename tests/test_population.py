import json
import math
import os
import subprocess
import sys
import unittest

from mouse_sim.drop_sim import _unit_variation, box_inertia, support_points
from mouse_sim.population import (
    MAX_SAMPLE_COUNT,
    MIN_SAMPLE_COUNT,
    PER_UNIT_FAILURES_CAP,
    _sig,
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
        # These population-engine tests exercise the statistics machinery
        # (determinism, sensitivity, survival, Wilson CI, unevaluated
        # handling), not contact physics: pin the explicit screening
        # linear-spring stiffness so their premises are unchanged.  The
        # calibrated DEFAULT (Hertz point contact) is exercised by the
        # dedicated HertzContactDefaultTests below.
        "contact_stiffness_n_per_m": 1e5,
        "components": None,
    }
    config.update(overrides)
    return config


def battery_only_config(**overrides):
    config = make_config(**overrides)
    # A cradle rated for the drop (latch retention 25 N: clears the whole
    # 0.008-0.02 kg cell class across the 50-500 g shock band at the 0.5
    # transmission factor).  The bare default 8 N hook is a weak-cradle
    # scenario that dislodges even at low drops — not the reference design.
    config["components"] = [
        {
            "component_id": "battery_pack",
            "type": "battery",
            "mass_kg": 0.008,
            "latch_retention_n": 25.0,
        }
    ]
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


class WorstCaseShellMathTests(unittest.TestCase):
    """Worst-case shell edge math must be the exact closed-form scaling
    laws: SF_wc = SF_nom * s * t^2, stress = nominal/t^2 and
    deflection = nominal/(E*t^3) on a small synthetic nominal."""

    def _make_context(self):
        context = make_context()
        context["shell"] = {
            "nominal": {
                "safety_factor": 1.25,
                "peak_stress_pa": 1.0e6,
                "max_displacement_m": 0.0025,
                "t_m": 0.001,
            }
        }
        return context

    def test_shell_edge_math_exact(self):
        for ts, identity in ((1.0, False), (0.0, True)):
            result = run_population(
                make_config(
                    tolerance_scale=ts,
                    worst_case={
                        "wall_thickness": "min",
                        "shell_modulus": "min",
                        "shell_strength": "min",
                        "shell_density": "max",
                        "com_offset": "max",
                    },
                ),
                self._make_context(),
            )
            t = 1.0 - 0.05 * ts
            e = 1.0 - 0.05 * ts
            s = 1.0 - 0.05 * ts
            shell = result["shell"]
            # Same exact expression order as the implementation, so the
            # comparison is exact float equality (modulo the 3-significant-
            # figure reporting of _sig).
            self.assertEqual(shell["safety_factor"], _sig(1.25 * s * t * t))
            self.assertEqual(shell["peak_stress_pa"], _sig(1.0e6 / (t * t)))
            self.assertEqual(shell["max_displacement_m"], _sig(0.0025 / (e * t ** 3)))
            if identity:
                # At tolerance_scale=0 every edge is 1.0: the reported values
                # must equal the nominal exactly.
                self.assertEqual(shell["safety_factor"], 1.25)
                self.assertEqual(shell["peak_stress_pa"], 1.0e6)
                self.assertEqual(shell["max_displacement_m"], 0.0025)

    def test_worst_case_schema_unchanged(self):
        # The frontend depends on this schema: min/max band keys, numeric
        # drop_height/orientation, unknown keys rejected.
        with self.assertRaises(ValueError):
            run_population(
                make_config(worst_case={"wall_thickness": "min", "bogus": "x"}),
                make_context(),
            )
        with self.assertRaises(ValueError):
            run_population(
                make_config(worst_case={"wall_thickness": "big"}), make_context()
            )
        with self.assertRaises(ValueError):
            run_population(
                make_config(worst_case={"drop_height": float("nan")}), make_context()
            )


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
        # The aligned component analyzers use a [1.0, 1.2) warn band (the
        # class-constant screening uncertainty band); a genuine fail needs a
        # shock ratio >= 1.2, which at 0.1 kg requires the 2.0 m drop.
        high = run_population(
            battery_only_config(sample_count=100, drop_height_m=2.0), self.context
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

    def test_tolerance_scale_nan_inf_and_negative_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf"), -1.0):
            with self.assertRaises(ValueError):
                run_population(make_config(tolerance_scale=bad), self.context)

    def test_nonfinite_and_nonsensical_numeric_config_rejected(self):
        cases = (
            {"sample_count": float("nan")},
            {"sample_count": float("inf")},
            {"lifespan_days": float("nan")},
            {"lifespan_days": float("inf")},
            {"lifespan_days": -10},
            {"base_seed": float("nan")},
            {"workers": float("nan")},
            {"workers": 0},
            {"contact_stiffness_n_per_m": float("nan")},
            {"contact_stiffness_n_per_m": float("inf")},
            {"contact_stiffness_n_per_m": -5.0},
            {"drop_height_m": float("nan")},
        )
        for overrides in cases:
            with self.assertRaises(ValueError):
                run_population(make_config(**overrides), self.context)

    def test_tolerance_scale_above_range_clamped_with_disclosure(self):
        result = run_population(make_config(tolerance_scale=5.0), self.context)
        self.assertTrue(
            any(
                "tolerance_scale" in line and "clamped" in line
                for line in result["diagnostics"]
            )
        )
        # Clamped to 2.0, so the mass band doubles: std(mass_scale) ~
        # 0.03*2/sqrt(3) ~ 0.035 — well above the scale-1.0 value of ~0.017.
        by_name = {entry["parameter"]: entry for entry in result["sensitivity"]}
        self.assertGreater(by_name["mass_scale"]["std_value"], 0.03)

    def test_unevaluated_units_reported_and_excluded_from_failure_rate(self):
        config = make_config(
            components=[
                # A rated cradle (25 N) on the reference 0.008 kg cell keeps
                # the battery healthy at the default 0.75 m profile; the
                # mystery part is unevaluated.
                {
                    "component_id": "battery_pack",
                    "type": "battery",
                    "mass_kg": 0.008,
                    "latch_retention_n": 25.0,
                },
                {"component_id": "mystery_part", "type": "quantum_flux_capacitor"},
            ]
        )
        result = run_population(config, self.context)
        self.assertEqual(result["units_unevaluated"], result["sample_count"])
        self.assertEqual(result["evaluated_units"], 0)
        self.assertTrue(result["analysis_incomplete"])
        self.assertEqual(result["units_failed"], 0)
        self.assertEqual(result["failure_rate"], 0.0)
        self.assertEqual(result["wilson_ci"], {"low": 0.0, "high": 0.0})
        self.assertTrue(
            any("analysis incomplete" in line for line in result["diagnostics"])
        )
        for component in result["component_failure_rates"]:
            self.assertEqual(component["failures"], 0)

    def test_unevaluated_mixed_with_failures_keeps_certain_failures(self):
        config = make_config(
            drop_height_m=2.0,
            components=[
                {"component_id": "battery_pack", "type": "battery"},
                {"component_id": "mystery_part", "type": "quantum_flux_capacitor"},
            ],
        )
        result = run_population(config, self.context)
        self.assertEqual(result["units_unevaluated"], result["sample_count"])
        self.assertEqual(result["evaluated_units"], 0)
        self.assertTrue(result["analysis_incomplete"])
        # Known component failures are still reported...
        self.assertGreater(result["units_failed"], 0)
        self.assertGreater(
            sum(c["failures"] for c in result["component_failure_rates"]), 0
        )
        # ...and a unit that FAILED is a certain failure even when another
        # component was unevaluated: it counts in the rate and CI.  Only
        # units whose outcome is unknown (unevaluated AND not failed) are
        # excluded from the denominator.
        self.assertGreater(result["failure_rate"], 0.0)
        self.assertLessEqual(result["failure_rate"], 1.0)
        self.assertLessEqual(result["wilson_ci"]["low"], result["failure_rate"])
        self.assertGreaterEqual(result["wilson_ci"]["high"], result["failure_rate"])
        # The survival curve and the rate share one denominator.
        survival_end = result["survival"][-1]["survival_rate"]
        self.assertAlmostEqual(survival_end, 1.0 - result["failure_rate"], places=6)
        self.assertTrue(
            any("analysis incomplete" in line for line in result["diagnostics"])
        )

    def test_subprocess_determinism_byte_identical(self):
        # The determinism contract must hold across real interpreter
        # processes regardless of PYTHONHASHSEED.  Note: the test python is
        # NOT on PATH, so the subprocess reuses sys.executable (the full
        # Python 3.12 path the suite runs under).
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            "import json, sys\n"
            "from mouse_sim.drop_sim import box_inertia, support_points\n"
            "from mouse_sim.population import run_population\n"
            "support = support_points([(x, y, z) for x in (-0.05, 0.05) "
            "for y in (-0.05, 0.05) for z in (-0.05, 0.05)])\n"
            "context = {'mass_kg': 0.1, 'inertia_kg_m2': box_inertia(0.1, "
            "((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05))), 'support': support, "
            "'materials': {'shell': 'abs', 'skate': 'ptfe'}, "
            "'environment_temperature_k': 296.0}\n"
            "config = {'sample_count': 50, 'profile': 'esports_fps', "
            "'lifespan_days': 730, 'base_seed': 0, 'workers': 2, "
            "'drop_height_m': 0.75, 'drop_surface': 'concrete', "
            "'drop_orientation': 'flat', 'tolerance_scale': 1.0, "
            "'components': None}\n"
            "sys.stdout.write(json.dumps(run_population(config, context), "
            "sort_keys=True, separators=(',', ':')))\n"
        )
        outputs = []
        for hash_seed in ("0", "12345"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = hash_seed
            proc = subprocess.run(
                [sys.executable, "-S", "-c", script],
                capture_output=True,
                text=True,
                env=env,
                cwd=repo_root,
                timeout=180,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs.append(proc.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_bad_surface_raises(self):
        with self.assertRaises(ValueError):
            run_population(make_config(drop_surface="jello"), self.context)


class HertzContactDefaultTests(unittest.TestCase):
    """The population default drop-contact model is the nonlinear Hertz
    point-contact law (E_eff from the shell material pair and the floor
    surface, corner blend radius 2.0 mm), NOT the uncalibrated 1e5 N/m
    linear spring; an explicit contact_stiffness_n_per_m keeps the linear
    override."""

    def setUp(self):
        self.context = make_context()
        # The pipeline resolves the shell material pair (E, nu) into the
        # context; ABS on concrete here.
        self.context["shell_hertz_pair"] = (2.3e9, 0.35)
        self.config = make_config(sample_count=100, workers=1)
        del self.config["contact_stiffness_n_per_m"]

    def test_default_contact_model_is_hertz(self):
        result = run_population(self.config, self.context)
        drop = result["model"]["drop"]
        self.assertIsNone(drop["contact_stiffness_n_per_m"])
        self.assertIn("Hertz", drop["impact_model"])
        # E_eff(ABS on concrete) ~ 2.4 GPa and the default corner blend
        # radius is 2.0 mm.
        self.assertAlmostEqual(drop["effective_modulus_pa"], 2.418e9, delta=1e7)
        self.assertEqual(drop["contact_radius_m"], 0.002)

    def test_default_hertz_physics_drives_component_shock_failures(self):
        # 0.1 kg from 0.75 m on concrete under the calibrated Hertz default:
        # peak deceleration is ~2700 g, far beyond the 500 g cell shock
        # class, so the battery screening channel fails every unit.  The
        # old 1e5 N/m default (~390 g) passed — the premise codified here is
        # the honest consequence of calibrated contact physics.
        result = run_population(self.config, self.context)
        self.assertEqual(result["units_failed"], result["sample_count"])
        battery = next(
            c for c in result["component_failure_rates"] if c["component_id"] == "battery_pack"
        )
        self.assertEqual(battery["rate"], 1.0)

    def test_explicit_stiffness_override_reported(self):
        config = dict(self.config)
        config["contact_stiffness_n_per_m"] = 1e5
        result = run_population(config, self.context)
        drop = result["model"]["drop"]
        self.assertEqual(drop["contact_stiffness_n_per_m"], 1e5)
        self.assertIn("linear spring", drop["impact_model"])
        self.assertIsNone(drop["effective_modulus_pa"])
        # The explicit screening stiffness keeps the reference design
        # healthy at 0.75 m (the override path is unchanged).
        self.assertEqual(result["units_failed"], 0)

    def test_missing_shell_material_pair_disclosed(self):
        context = dict(self.context)
        del context["shell_hertz_pair"]
        result = run_population(self.config, context)
        self.assertTrue(
            any(
                "HERTZ_EFFECTIVE_MODULUS_ASSUMED" in line and "generic polymer" in line
                for line in result["diagnostics"]
            ),
            "missing shell material E/nu must be disclosed, never silent",
        )
        drop = result["model"]["drop"]
        self.assertIn("Hertz", drop["impact_model"])
        # Generic polymer (E=2.0e9, nu=0.36) on concrete.
        self.assertAlmostEqual(drop["effective_modulus_pa"], 2.140e9, delta=1e7)

    def test_explicit_corner_radius_override(self):
        config = dict(self.config)
        config["contact_radius_m"] = 0.004
        result = run_population(config, self.context)
        self.assertEqual(result["model"]["drop"]["contact_radius_m"], 0.004)

    def test_invalid_radius_rejected(self):
        for bad in (0.0, -0.001, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                run_population(
                    make_config(contact_radius_m=bad), self.context
                )


class SurvivalAndWeibullTests(unittest.TestCase):
    """Regression tests for the survival-curve boundary semantics and the
    Weibull/B10 estimator (audit: multi-drop degradation & fleet reliability)."""

    def _bins(self, *counts):
        # counts[0] is bin 1 (0 < u_f <= 0.1); bin 0 is always empty.
        bins = [0] * 11
        for index, count in enumerate(counts, start=1):
            bins[index] = count
        return bins

    def test_survival_excludes_exact_horizon_failures(self):
        # 90 survivors + 10 units failing exactly AT u_f = 1.0 (ratio 1.0):
        # S(1.0) must be 0.9 (the exact-horizon failures are DEAD at 1.0),
        # and S(0.9) must be 1.0 (they are alive just before the horizon).
        from mouse_sim.population import _survival

        bins = self._bins()
        bins[10] = 10
        curve = _survival(bins, 90, 100)
        self.assertEqual(curve[-1]["survival_rate"], 0.9)
        self.assertEqual(curve[-2]["survival_rate"], 1.0)
        # Monotonic non-increasing.
        rates = [entry["survival_rate"] for entry in curve]
        for lower, higher in zip(rates, rates[1:]):
            self.assertLessEqual(higher, lower + 1e-9)

    def test_survival_bin_boundary(self):
        # 90 survivors + 10 units failing in (0.2, 0.3] (bin 3): alive at
        # u=0.2 (u_f > 0.2), dead at u=0.3 and beyond.
        from mouse_sim.population import _survival

        bins = self._bins()
        bins[3] = 10
        curve = _survival(bins, 90, 100)
        by_u = {entry["usage_fraction"]: entry["survival_rate"] for entry in curve}
        self.assertEqual(by_u[0.1], 1.0)
        self.assertEqual(by_u[0.2], 1.0)
        self.assertEqual(by_u[0.3], 0.9)
        self.assertEqual(by_u[1.0], 0.9)

    def test_weibull_fit_median_rank_and_b10(self):
        from mouse_sim.population import _weibull_fit

        # 50 survivors, 50 failures uniformly spread over bins 4..10
        # (u_f in (0.3, 1.0]); a Weibull fit must exist, be finite, and
        # the B10 usage fraction must be strictly inside (0, 1).
        bins = self._bins()
        for k in range(4, 11):
            bins[k] = 7
        bins[10] += 1  # 50 total
        fit = _weibull_fit(bins, 50, 100)
        self.assertIsNotNone(fit)
        self.assertGreater(fit["beta"], 0.0)
        self.assertGreater(fit["eta"], 0.0)
        self.assertGreater(fit["b10_usage_fraction"], 0.0)
        self.assertLess(fit["b10_usage_fraction"], 1.0)
        self.assertEqual(fit["failures_fit"], 50)
        # B10 formula: t_10 = eta * (-ln(0.9))^(1/beta).
        expected_b10 = fit["eta"] * ((-math.log(0.9)) ** (1.0 / fit["beta"]))
        self.assertAlmostEqual(fit["b10_usage_fraction"], expected_b10, places=3)

    def test_weibull_zero_failures_is_none(self):
        from mouse_sim.population import _weibull_fit

        # 0 failures -> infinite life -> no finite eta/beta (no NaN/Inf).
        self.assertIsNone(_weibull_fit([0] * 11, 100, 100))
        self.assertIsNone(_weibull_fit([0] * 11, 0, 100))

    def test_weibull_all_fail_at_horizon_is_none_or_finite(self):
        from mouse_sim.population import _weibull_fit

        # All 100 units fail exactly at u_f = 1.0: the fit is degenerate
        # (zero variance in x) and must NOT emit NaN/Inf — None is the
        # safe answer.
        bins = self._bins()
        bins[10] = 100
        fit = _weibull_fit(bins, 0, 100)
        self.assertIsNone(fit)

    def test_weibull_few_failures_is_none(self):
        from mouse_sim.population import _weibull_fit

        bins = self._bins()
        bins[5] = 2  # only 2 failures
        self.assertIsNone(_weibull_fit(bins, 98, 100))

    def test_population_result_carries_weibull_key(self):
        # The assembled population result must carry the weibull block
        # (None when nothing failed, a dict when failures exist) and the
        # survival endpoint must stay consistent with the failure rate.
        result = run_population(
            battery_only_config(sample_count=200, drop_height_m=2.0), make_context()
        )
        self.assertIn("weibull", result)
        if result["units_failed"] > 0:
            self.assertIsNotNone(result["weibull"])
            self.assertIn("b10_usage_fraction", result["weibull"])
        survival_end = result["survival"][-1]["survival_rate"]
        self.assertAlmostEqual(survival_end, 1.0 - result["failure_rate"], places=4)


if __name__ == "__main__":
    unittest.main()
