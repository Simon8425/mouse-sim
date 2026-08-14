"""Shell validation preparation tests: validation-mode pinning, contact
stiffness sweep, sensitivity, measured comparison, model status, trace."""

import math
import unittest

from mouse_sim import shell_validation as validation_module
from mouse_sim.errors import ValidationError
from mouse_sim.pipeline import run_pipeline

SHELL = {"type": "box", "size": [60, 40, 10], "units": "mm"}
PCB = {"type": "box", "size": [50, 30, 1.6], "units": "mm"}
BATTERY = {"type": "box", "size": [40, 20, 8], "units": "mm"}


def validation_request(**overrides):
    request = {
        "schema_id": "gms.project-document/1",
        "mode": "validation",
        "units": "mm",
        "objects": [
            {"id": "shell", "geometry": SHELL, "material": "ABS", "structural_behavior": "shell"},
            {"id": "pcb", "geometry": PCB, "material": "FR4", "structural_behavior": "rigid"},
            {"id": "battery", "geometry": BATTERY, "material": "LiPo", "structural_behavior": "rigid"},
        ],
        "options": {"min_thickness_m": 0.001, "max_thickness_m": 0.05},
        "load_case": {"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
        "structure": {"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
        "validation": {
            "geometry": {"revision": "cad-r42", "units": "mm", "quality": "final"},
            "material": "ABS",
            "drop": {
                "height_m": 1.0,
                "orientation": "corner",
                "surface": "concrete",
                "gravity_m_s2": 9.81,
                "initial_velocity_m_s": [0, 0, 0],
                "initial_angular_velocity_rad_s": [0, 0, 0],
            },
            "contact": {"stiffness_n_per_m": 1e5, "timestep_s": 1 / 240},
            "structural": {"model": "shell_panel_navier_v1"},
            "contact_stiffness_sweep_n_per_m": [1e5, 2e5, 5e5, 1e6],
            "sensitivity": {"perturbation_fraction": 0.1},
        },
    }
    request.update(overrides)
    return request


class ValidationConfigTests(unittest.TestCase):
    def test_validation_mode_requires_section(self):
        request = validation_request()
        del request["validation"]
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_missing_required_keys_rejected(self):
        cases = [
            ("material", None),
            ("drop", None),
            ("contact", None),
        ]
        for key, _ in cases:
            request = validation_request()
            del request["validation"][key]
            result = run_pipeline(request)
            self.assertTrue(
                any(item["code"] == "VALIDATION_CONFIG_INVALID" for item in result["errors"]),
                key,
            )

    def test_geometry_revision_required(self):
        request = validation_request()
        del request["validation"]["geometry"]["revision"]
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_contact_stiffness_required_and_bounded(self):
        request = validation_request()
        del request["validation"]["contact"]["stiffness_n_per_m"]
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )
        request = validation_request()
        request["validation"]["contact"]["stiffness_n_per_m"] = -1e5
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_nonzero_initial_velocity_rejected(self):
        request = validation_request()
        request["validation"]["drop"]["initial_velocity_m_s"] = [0.5, 0.0, 0.0]
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_explicit_quaternion_orientation_accepted(self):
        request = validation_request()
        request["validation"]["drop"]["orientation"] = {
            "quaternion_wxyz": [0.4597008433809831, -0.6279630301995544, 0.6279630301995544, 0.0]
        }
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        model = result["drop_simulation"]["model"]
        expected = [0.4597008433809831, -0.6279630301995544, 0.6279630301995544, 0.0]
        for value, reference in zip(model["orientation_quaternion_wxyz"], expected):
            self.assertAlmostEqual(value, reference, places=9)

    def test_validation_run_pins_contact_and_drop(self):
        result = run_pipeline(validation_request())
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["drop_simulation"]["contact_stiffness_n_per_m"], 1e5)
        config = result["drop_simulation"]["config"]
        self.assertEqual(config["height_m"], 1.0)
        self.assertEqual(config["surface"], "concrete")
        self.assertEqual(config["orientation"], "corner")
        model = result["drop_simulation"]["model"]
        self.assertEqual(model["gravity_m_s2"], 9.81)
        self.assertEqual(model["timestep_s"], 1 / 240)
        self.assertIsNone(result.get("qualification"))


class ContactStiffnessSweepTests(unittest.TestCase):
    def test_sweep_matches_spring_analytic_scaling(self):
        # F_peak = v * sqrt(m*k): force scales with sqrt(k).
        sweep = validation_module.run_contact_stiffness_sweep(
            [1e5, 4e5], mass_kg=0.1, speed_m_s=3.13, restitution=0.3
        )
        rows = sweep["rows"]
        self.assertEqual(len(rows), 2)
        ratio = rows[1]["peak_force_n"] / rows[0]["peak_force_n"]
        self.assertAlmostEqual(ratio, math.sqrt(4e5 / 1e5), places=4)
        # Impulse is restitution/momentum-based and therefore k-independent.
        self.assertAlmostEqual(rows[0]["impulse_n_s"], rows[1]["impulse_n_s"], places=9)
        self.assertIn("NO value is claimed correct", sweep["note"])

    def test_sweep_uncertainty_bands(self):
        result = run_pipeline(validation_request())
        bands = result["shell"]["validation"]["uncertainty_bands"]
        self.assertEqual(bands["basis"], "contact_stiffness_sweep")
        band = bands["band"]["peak_force_n"]
        self.assertLess(band["low"], band["high"])
        self.assertIn("NOT a statistical confidence interval", bands["note"])


class SensitivityTests(unittest.TestCase):
    def test_sensitivity_physics_relations(self):
        request = validation_request()
        result = run_pipeline(request)
        rows = {
            row["parameter"]: row
            for row in result["shell"]["validation"]["sensitivity"]["rows"]
        }
        # strength -> SF sensitivity ~ +1.0 (SF = allowable/stress).
        strength = rows["strength"]["outputs"]
        sf_sens = [o for o in strength if o["output"] == "safety_factor" and o["sensitivity_up"] is not None]
        self.assertTrue(sf_sens)
        self.assertAlmostEqual(sf_sens[0]["sensitivity_up"], 1.0, delta=0.05)
        # contact stiffness -> peak force sensitivity ~ +0.5 (F ~ sqrt(k)).
        k_sens = [
            o for o in rows["contact_stiffness"]["outputs"]
            if o["output"] == "peak_force_n" and o["sensitivity_up"] is not None
        ]
        self.assertTrue(k_sens)
        self.assertAlmostEqual(k_sens[0]["sensitivity_up"], 0.5, delta=0.1)

    def test_unknown_parameter_rejected(self):
        request = validation_request()
        request["validation"]["sensitivity"] = {
            "perturbation_fraction": 0.1, "parameters": ["bogus"]
        }
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_SENSITIVITY_FAILED", [item["code"] for item in result["issues"]]
        )



    def test_settle_s_reported_but_excluded_from_ranking(self):
        # VERIFICATION FINDING: the settle time of a rocking contact is
        # chaotically sensitive (sub-1e-6 relative input changes flip the
        # settle branch), so its per-parameter sensitivities are knife-edge
        # artifacts.  The ranking/mean must be driven by the stable outputs
        # (peak force, acceleration, stress, displacement, SF) while the
        # settle_s response stays visible in the per-output rows, and the
        # exclusion must be disclosed in the result note.
        request = validation_request()
        result = run_pipeline(request)
        sensitivity = result["shell"]["validation"]["sensitivity"]
        rows = {row["parameter"]: row for row in sensitivity["rows"]}
        mass_row = rows["mass"]
        output_names = [item["output"] for item in mass_row["outputs"]]
        self.assertIn("settle_s", output_names)
        # The mean is dominated by the stable outputs (peak force /
        # acceleration ~0.5 per unit), not by the chaotic settle branch:
        # a settle-driven mean would be O(1-3) (measured 2.8 before the fix).
        self.assertLess(mass_row["mean_relative_response"], 0.5)
        self.assertIn("settle_s", sensitivity["note"])
        self.assertIn("EXCLUDED from the ranking", sensitivity["note"])

    def test_ranking_stable_under_micro_perturbation(self):
        # The chaotic settle branch must not move the top-3 ranking: a
        # 1e-6 mass perturbation (sub-tolerance, flips the settle branch
        # between 2-8 s on the reference corner drop) leaves the
        # stable-output ranking unchanged.
        def top_three(mass_override=None):
            request = validation_request()
            if mass_override is not None:
                request["drop_simulation"] = {"mass_kg": mass_override}
            result = run_pipeline(request)
            return list(result["shell"]["validation"]["sensitivity"]["top_parameters"][:3])
        base = top_three()
        baseline_mass = run_pipeline(validation_request())["mass"]["mass_kg"]
        perturbed = top_three(baseline_mass * (1.0 + 1e-6))
        self.assertEqual(base, perturbed)


class MeasuredComparisonTests(unittest.TestCase):
    def test_measured_tests_flow_into_correlation_and_comparison(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {
                "test_id": "T1",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "prototype_id": "P01",
                "height_m": 1.0,
                "surface": "concrete",
                "orientation": "flat",
                "environment": {"temperature_k": 293.15},
                "sensor": {"model": "accel-3axis", "quantity": "resultant_peak_g",
                           "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": 300.0,
                "measured_peak_accel_g_uncertainty": 15.0,
            },
            {
                "test_id": "T2",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "prototype_id": "P01",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "environment": {"temperature_k": 293.15},
                "sensor": {"model": "accel-3axis", "quantity": "resultant_peak_g",
                           "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": 200.0,
                "measured_peak_accel_g_uncertainty": 10.0,
            },
            {
                "test_id": "T3",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "prototype_id": "P02",
                "height_m": 1.5,
                "surface": "concrete",
                "orientation": "flat",
                "environment": {"temperature_k": 293.15},
                "sensor": {"model": "accel-3axis", "quantity": "resultant_peak_g",
                           "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": 350.0,
                "measured_peak_accel_g_uncertainty": 20.0,
            },
        ]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["correlation"])
        comparison = result["shell"]["validation"]["measured_comparison"]
        self.assertEqual(len(comparison["rows"]), 3)
        self.assertIn("count", comparison["aggregate"])
        # All three rows are flat with CoM resultant sensors -> equivalent;
        # the aggregate covers them all (non-equivalent rows are excluded
        # by the audit fix).
        self.assertEqual(comparison["aggregate"]["count"], 3)
        self.assertEqual(comparison["aggregate_excluded_non_equivalent"], 0)
        for row in comparison["rows"]:
            self.assertIn("test_id", row)
            self.assertIn("uncertainty", row)
        self.assertIn("measured data never modifies the physics", comparison["note"])
        # k-sensitivity of the comparison is reported, not applied.
        k_sens = result["shell"]["validation"]["measured_k_sensitivity"]
        self.assertEqual(len(k_sens["rows"]), 4)
        self.assertIn("does NOT select", k_sens["note"])

    def test_measured_tests_require_values(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "height_m": 1.0, "surface": "concrete", "orientation": "flat"}
        ]
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )


class ModelStatusAndTraceTests(unittest.TestCase):
    def test_unvalidated_without_correlation(self):
        result = run_pipeline(validation_request())
        shell = result["shell"]
        self.assertEqual(shell["model_status"], "unvalidated")
        self.assertEqual(shell["physical_validation"]["status"], "no_measured_tests")
        correlation_assumption = [
            item for item in shell["invalidating_assumptions"]
            if item["assumption"] == "physical correlation"
        ]
        self.assertTrue(correlation_assumption)
        self.assertIn(
            "internal consistency is not physical validation",
            correlation_assumption[0]["impact"],
        )

    def test_trace_records_authoritative_values(self):
        result = run_pipeline(validation_request())
        trace = result["shell"]["inputs_trace"]
        self.assertEqual(trace["material"]["label"], "ABS")
        self.assertEqual(trace["drop"]["height_m"], 1.0)
        self.assertEqual(trace["drop"]["gravity_m_s2"], 9.81)
        self.assertEqual(trace["contact"]["contact_stiffness_n_per_m"], 1e5)
        # Concrete restitution 0.30 is the instrumented drop-test class
        # mid-point for polymer on concrete (range 0.25-0.35); the trace
        # records the SURFACES value verbatim.
        self.assertEqual(trace["drop"]["restitution"], 0.3)
        self.assertEqual(trace["drop"]["timestep_s"], 1 / 240)
        self.assertEqual(trace["structural"]["safety_factor"], result["structural"]["response"]["safety_factor"])
        self.assertIn("engine_hash", trace["engine"])
        self.assertEqual(trace["seed"], 0)
        # The trace mass is the body the DROP actually solved (effective
        # mass); the geometry-derived model mass is recorded alongside.
        self.assertEqual(trace["mass"]["mass_kg"], result["drop_simulation"]["model"]["mass_kg"])
        self.assertEqual(trace["mass"]["geometry_model_mass_kg"], result["mass"]["mass_kg"])

    def test_trace_persists_derated_allowable_at_temperature(self):
        # E3: when the structural solve applies linear temperature derating,
        # the trace persists the DERATED tensile allowable (the value behind
        # the safety factor) so downstream consumers never confuse it with
        # the catalog (underated) allowable.
        request = validation_request()
        request["environment_temperature_k"] = 333.15
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        props = result["shell"]["inputs_trace"]["material"]["properties"]
        self.assertEqual(props["derated_tensile_allowable_pa"], 16400000.0)
        # The solve discloses the derating; the raw catalog allowable stays.
        self.assertEqual(props["tensile_allowable"]["value_si"], 20e6)
        self.assertTrue(
            any("temperature derating" in item for item in result["structural"]["response"]["assumptions"])
        )

    def test_trace_omits_derated_allowable_without_temperature(self):
        # No temperature above the derating reference: no derated value is
        # persisted (additive only — nothing changes for existing runs).
        result = run_pipeline(validation_request())
        props = result["shell"]["inputs_trace"]["material"]["properties"]
        self.assertNotIn("derated_tensile_allowable_pa", props)

    def test_invalidating_assumptions_cover_major_sources(self):
        result = run_pipeline(validation_request())
        assumptions = result["shell"]["invalidating_assumptions"]
        labels = {item["assumption"] for item in assumptions}
        self.assertIn("contact stiffness", labels)
        self.assertIn("restitution/friction", labels)
        self.assertIn("physical correlation", labels)
        self.assertIn("material properties", labels)


class TraceConsistencyTests(unittest.TestCase):
    def test_estimate_uses_effective_mass_and_restitution(self):
        # The drop-derived estimate must run on the SAME body the integrator
        # solved (effective mass incl. variation, degraded restitution).
        request = validation_request()
        request["drop_simulation"] = {"mass_kg": 0.05, "unit_seed": 7}
        result = run_pipeline(request)
        model = result["drop_simulation"]["model"]
        estimate = result["drop_simulation"]["peak_force_estimate"]
        self.assertEqual(estimate["mass_kg"], model["mass_kg"])
        self.assertEqual(estimate["restitution"], model["restitution"])

    def test_structural_material_matches_shell_material(self):
        # Unpinned structure resolves the FIRST OBJECT's material — the same
        # material the mass model used — never catalog insertion order.
        # (Exploration-mode request: validation mode always pins the material
        # explicitly by design.)
        request = {
            "schema_id": "gms.project-document/1",
            "mode": "exploration",
            "units": "mm",
            "objects": [
                {"id": "shell", "geometry": SHELL, "material": "ABS", "structural_behavior": "shell"},
                {"id": "pcb", "geometry": PCB, "material": "FR4", "structural_behavior": "rigid"},
            ],
            "options": {"min_thickness_m": 0.001, "max_thickness_m": 0.05},
            "load_case": {"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            "structure": {"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002},
        }
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["structural"]["material"], "shell")
        trace = result["shell"]["inputs_trace"]
        self.assertEqual(trace["material"]["label"], "shell")
        # ABS's tensile_allowable (20 MPa) drives the SF — not the catalog's
        # first entry by insertion order.
        self.assertIn("tensile_allowable", trace["material"]["properties"])


class AuditFixRegressionTests(unittest.TestCase):
    """Regressions for the adversarial-audit findings on the validation layer."""

    def test_pinned_restitution_and_friction_reach_integrator(self):
        request = validation_request()
        request["validation"]["contact"]["restitution"] = 0.1
        request["validation"]["contact"]["friction"] = 0.2
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        model = result["drop_simulation"]["model"]
        self.assertAlmostEqual(model["restitution"], 0.1, places=6)
        self.assertAlmostEqual(model["friction"], 0.2, places=6)
        estimate = result["drop_simulation"]["peak_force_estimate"]
        self.assertAlmostEqual(estimate["restitution"], 0.1, places=6)

    def test_drop_body_overrides_reach_integrator(self):
        request = validation_request()
        request["validation"]["drop"]["mass_scale"] = 1.5
        request["validation"]["drop"]["inertia_scale"] = 2.0
        request["validation"]["drop"]["com_override_m"] = [0.001, 0.002, 0.003]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        model = result["drop_simulation"]["model"]
        baseline_mass = 0.0454
        self.assertAlmostEqual(model["mass_kg"], baseline_mass * 1.5, places=6)
        self.assertEqual(model["com_offset_m"], [0.001, 0.002, 0.003])
        applied = result["shell"]["validation"]["config"]["drop"]
        self.assertEqual(applied["mass_scale"], 1.5)

    def test_unknown_or_inline_pinned_material_fails_closed(self):
        unknown = validation_request()
        unknown["validation"]["material"] = "ABS9"
        result = run_pipeline(unknown)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )
        inline = validation_request()
        inline["validation"]["material"] = {"name": "X", "properties": {}}
        result = run_pipeline(inline)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_uncertainty_band_nominal_is_pinned_k_row(self):
        result = run_pipeline(validation_request())
        bands = result["shell"]["validation"]["uncertainty_bands"]
        rows = result["shell"]["validation"]["contact_stiffness_sweep"]["rows"]
        pinned_row = next(
            row for row in rows if row["contact_stiffness_n_per_m"] == 1e5
        )
        self.assertAlmostEqual(
            bands["band"]["peak_force_n"]["nominal"], pinned_row["peak_force_n"], places=3
        )

    def test_uncertainty_bands_always_present(self):
        request = validation_request()
        del request["validation"]["contact_stiffness_sweep_n_per_m"]
        result = run_pipeline(request)
        bands = result["shell"]["validation"]["uncertainty_bands"]
        self.assertEqual(bands["basis"], "not_computed")
        self.assertNotIn("band", bands)

    def test_single_condition_pass_is_not_physical_validation(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {
                "test_id": "T1",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": 1.0,
                "measured_peak_accel_g_uncertainty": 10.0,
            }
        ]
        result = run_pipeline(request)
        # One condition was compared but the acceptance is not satisfied:
        # PARTIALLY VALIDATED — never correlated, never unvalidated-without-data.
        # (W2-06A keeps all-excluded campaigns unvalidated; this test's single
        # condition IS evaluated, so the status is partially_validated.)
        self.assertEqual(result["correlation"]["excluded_conditions"], 0)
        self.assertEqual(result["correlation"]["evaluated_conditions"], 1)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        self.assertNotEqual(result["shell"]["model_status"], "unvalidated")
        self.assertEqual(result["shell"]["model_status"], "partially_validated")

    def test_substeps_non_numeric_rejected_cleanly(self):
        request = validation_request()
        request["validation"]["contact"]["substeps"] = "abc"
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_measured_settle_reaches_correlation(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {
                "test_id": "T1",
                "cad_revision": "r1",
                "material": "ABS",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 200.0,
                "measured_settle_s": 0.9,
            },
            {
                "test_id": "T2",
                "cad_revision": "r1",
                "material": "ABS",
                "height_m": 1.0,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 300.0,
            },
            {
                "test_id": "T3",
                "cad_revision": "r1",
                "material": "ABS",
                "height_m": 1.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 350.0,
            },
        ]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        metrics = {
            metric["metric_key"]
            for condition in result["correlation"]["conditions"]
            for metric in condition["metrics"]
        }
        self.assertIn("settle_time_s", metrics)

    def test_quaternion_measured_test_is_reesimulated(self):
        # Audit finding: an explicit-quaternion measured test previously died
        # in the correlation re-sim ("orientation must be one of flat...").
        quaternion = {"quaternion_wxyz": [0.4597008433809831, -0.6279630301995544, 0.6279630301995544, 0.0]}
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {
                "test_id": "T1",
                "cad_revision": "r1",
                "material": "ABS",
                "height_m": 1.0,
                "surface": "concrete",
                "orientation": quaternion,
                "measured_peak_accel_g": 300.0,
            },
            {
                "test_id": "T2",
                "cad_revision": "r1",
                "material": "ABS",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 200.0,
            },
            {
                "test_id": "T3",
                "cad_revision": "r1",
                "material": "ABS",
                "height_m": 1.5,
                "surface": "concrete",
                "orientation": "edge",
                "measured_peak_accel_g": 350.0,
            },
        ]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        quaternion_condition = [
            condition for condition in result["correlation"]["conditions"]
            if condition["drop_id"] == "T1"
        ][0]
        self.assertNotIn("error", quaternion_condition)
        self.assertTrue(quaternion_condition["metrics"])
        self.assertIn("orientation_quaternion_wxyz", quaternion_condition)
        comparison = result["shell"]["validation"]["measured_comparison"]
        row = [row for row in comparison["rows"] if row["test_id"] == "T1"][0]
        self.assertIsNotNone(row["simulated"])
        self.assertEqual(row["equivalent"], False)
        self.assertTrue(row["equivalence_note"])

    def test_revision_mismatch_is_reported(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {
                "test_id": "T1",
                "cad_revision": "OTHER-REV",
                "material": "ABS",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 200.0,
            },
            {
                "test_id": "T2",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "height_m": 1.0,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 300.0,
            },
            {
                "test_id": "T3",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "height_m": 1.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 350.0,
            },
        ]
        result = run_pipeline(request)
        comparison = result["shell"]["validation"]["measured_comparison"]
        row = [row for row in comparison["rows"] if row["test_id"] == "T1"][0]
        self.assertTrue(row["revision_mismatch"])
        self.assertIn("OTHER-REV", row["revision_mismatch_note"])
        clean = [row for row in comparison["rows"] if row["test_id"] == "T2"][0]
        self.assertNotIn("revision_mismatch", clean)

    def test_asymmetric_inertia_override_rejected(self):
        request = validation_request()
        request["validation"]["prototype"] = {
            "inertia_kg_m2": [[1e-5, 1e-6, 0], [0, 1e-5, 0], [0, 0, 1e-5]]
        }
        result = run_pipeline(request)
        self.assertIn(
            "VALIDATION_CONFIG_INVALID", [item["code"] for item in result["errors"]]
        )

    def test_off_body_com_override_rejected(self):
        # An off-body CoM makes the integrator numerically explosive: it must
        # fail clearly (audit finding: previously hung for 60+ s).
        request = validation_request()
        request["validation"]["drop"]["com_override_m"] = [0.5, 0.5, 0.5]
        result = run_pipeline(request)
        codes = [item["code"] for item in result["errors"]]
        self.assertTrue(codes, "must fail, not hang")
        for code in codes:
            self.assertNotIn("PIPELINE_INTERNAL", code)
        messages = " ".join(item["message"] for item in result["errors"])
        self.assertIn("center of mass", messages)

    def test_validation_nan_is_config_error_not_internal(self):
        request = validation_request()
        request["validation"]["prototype"] = {"mass_kg": float("nan")}
        result = run_pipeline(request)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("VALIDATION_CONFIG_INVALID", codes)
        self.assertNotIn("PIPELINE_INTERNAL", codes)

    def test_sensitivity_not_run_when_not_requested(self):
        request = validation_request()
        del request["validation"]["sensitivity"]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        validation_block = result["shell"]["validation"]
        self.assertNotIn("sensitivity", validation_block)
        self.assertNotIn("sensitivity", validation_block["config"])

    def test_repeat_conditions_do_not_contaminate_rows(self):
        # Two tests at the SAME condition: each row must carry its OWN
        # measured value (audit finding: the shared simulated block
        # previously leaked another test's measured_g into the row).
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {
                "test_id": "T1",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 200.0,
            },
            {
                "test_id": "T2",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "height_m": 0.5,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 210.0,
            },
            {
                "test_id": "T3",
                "cad_revision": "cad-r42",
                "material": "ABS",
                "height_m": 1.0,
                "surface": "concrete",
                "orientation": "flat",
                "measured_peak_accel_g": 300.0,
            },
        ]
        result = run_pipeline(request)
        comparison = result["shell"]["validation"]["measured_comparison"]
        rows = {row["test_id"]: row for row in comparison["rows"]}
        self.assertEqual(rows["T1"]["measured"]["measured_peak_accel_g"], 200.0)
        self.assertEqual(rows["T2"]["measured"]["measured_peak_accel_g"], 210.0)
        self.assertNotIn("measured_g", rows["T1"]["simulated"])


if __name__ == "__main__":
    unittest.main()
