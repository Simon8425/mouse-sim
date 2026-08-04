import tempfile
import unittest

from mouse_sim import canonical_json
from mouse_sim.cache import ArtifactCache
from mouse_sim.pipeline import ENGINE_VERSION, run_pipeline

SHELL = {"type": "box", "size": [100, 60, 40]}
PCB = {"type": "box", "size": [60, 40, 1.6]}
BATTERY = {"type": "box", "size": [50, 30, 8]}

EXPECTED_MASS_KG = (
    0.1 * 0.06 * 0.04 * 1040.0
    + 0.06 * 0.04 * 0.0016 * 1850.0
    + 0.05 * 0.03 * 0.008 * 2500.0
)

APPROVED_MATERIALS = {
    "ABS": {
        "name": "ABS",
        "properties": {
            "density": {"value": 1040, "unit": "kg/m^3"},
            "young_modulus": {"value": 2.3e9, "unit": "Pa"},
            "poissons_ratio": 0.35,
            "yield_strength": {"value": 40e6, "unit": "Pa"},
        },
        "approval_state": "approved",
        "provenance": {
            "source_type": "supplier",
            "source_id": "supplier-lot-42",
            "condition": "23 C, dry, conditioned 48 h",
            "confidence": "high",
        },
    }
}


def mouse_project_request(**overrides):
    request = {
        "schema_id": "gms.project-document/1",
        "mode": "exploration",
        "units": "mm",
        "objects": [
            {"id": "shell", "geometry": SHELL, "material": "ABS", "structural_behavior": "shell"},
            {"id": "pcb", "geometry": PCB, "material": "FR4", "structural_behavior": "rigid"},
            {"id": "battery", "geometry": BATTERY, "material": "LiPo", "structural_behavior": "rigid"},
        ],
        "options": {"min_thickness_m": 0.001, "max_thickness_m": 0.05},
    }
    request.update(overrides)
    return request


def qualification_request(**overrides):
    request = mouse_project_request(mode="qualification")
    request.update(
        {
            "objects": [
                {"id": "shell", "geometry": SHELL, "material": "ABS", "structural_behavior": "shell"},
            ],
            "materials": APPROVED_MATERIALS,
            "method": {
                "method_key": "static_v1",
                "method_type": "static",
                "approved_for_qualification": True,
                "approval_state": "approved",
                "required_capability_keys": ["solve_load_case"],
                "solver_policy": {"require_convergence": True, "require_force_balance": True},
                "required_correlation_policy": {
                    "required": True,
                    "required_record_types": ("static_correlation",),
                    "require_reviewed_records": True,
                },
            },
            "geometry": {"closed": True, "reviewed": True, "repairs_reviewed": True},
            "fixtures": {"fixture_type": "simply_supported", "reviewed": True},
            "tolerance_profile": {"process_profile": "molding"},
            "correlation_records": [
                {"record_type": "static_correlation", "review_state": "approved"},
            ],
            "requirement": {"status": "active", "acceptance": {"metric_key": "deflection"}},
            "reviewed_flags": {"geometry_reviewed": True, "load_case_reviewed": True},
            "convergence_evidence": True,
            "force_balance": True,
            "load_case": {
                "kind": "pressure",
                "magnitude": {"value": 1, "unit": "kPa"},
                "reviewed": True,
                "acceptance_requirement_refs": [{"id": "req-1"}],
            },
            "structure": {"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
        }
    )
    request.update(overrides)
    return request


class PipelineTests(unittest.TestCase):
    def test_full_exploration_run_completes_without_errors(self):
        result = run_pipeline(mouse_project_request())
        self.assertEqual(result["schema_id"], "gms.pipeline-result/1")
        self.assertEqual(result["engine_version"], ENGINE_VERSION)
        self.assertEqual(result["lifecycle_state"], "completed")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["run_id"])
        self.assertEqual(result["mode"], "exploration")

    def test_mass_total_within_expected_range(self):
        result = run_pipeline(mouse_project_request())
        mass_kg = result["mass"]["mass_kg"]
        self.assertAlmostEqual(mass_kg, EXPECTED_MASS_KG, delta=EXPECTED_MASS_KG * 0.05)
        self.assertEqual(result["mass"]["mass_status"], "calculated")

    def test_validation_findings_present(self):
        result = run_pipeline(mouse_project_request())
        self.assertIsNotNone(result["validation"])
        self.assertGreater(len(result["validation"]["findings"]), 0)
        self.assertTrue(all("code" in item for item in result["validation"]["findings"]))

    def test_shell_panel_load_case_produces_structural_response(self):
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
        )
        result = run_pipeline(request)
        structural = result["structural"]
        self.assertIsNotNone(structural)
        self.assertAlmostEqual(structural["load_case"]["magnitude_pa"], 1000.0, places=6)
        self.assertGreater(structural["response"]["max_stress_pa"], 0.0)
        self.assertIsNotNone(structural["response"]["max_displacement_m"])
        self.assertIsInstance(structural["preflight"], list)

    def test_exploration_disposition_even_with_full_inputs(self):
        request = mouse_project_request(
            method={"method_type": "closed_form", "approved_for_qualification": True,
                    "approval_state": "approved", "required_capability_keys": ["solve_load_case"]},
            requirement={"status": "active", "acceptance": {"safety_factor_min": 1.0}},
            geometry={"closed": True, "reviewed": True, "repairs_reviewed": True},
            fixtures={"fixture_type": "simply_supported", "reviewed": True},
            tolerance_profile={"dimensional": "0.1 mm"},
            correlation_records=[{"record_type": "shell_panel", "review_state": "approved"}],
            reviewed_flags={"geometry_reviewed": True, "load_case_reviewed": True},
            convergence_evidence=True,
            force_balance=True,
            load_case={"kind": "pressure", "magnitude_pa": 1000.0},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
        )
        result = run_pipeline(request)
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "exploration_only")
        self.assertFalse(qualification["qualified"])
        self.assertEqual(result["errors"], [])

    def test_missing_material_issue_and_unknown_mass(self):
        request = mouse_project_request(
            objects=[{"id": "shell", "geometry": SHELL, "material": "Titanium-7"}]
        )
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("MATERIAL_NOT_FOUND", codes)
        self.assertIn(result["mass"]["mass_status"], ("unknown", "partial"))
        self.assertIsNone(result["mass"]["mass_kg"])
        self.assertEqual(result["lifecycle_state"], "completed")

    def test_impact_requested_includes_unsupported_failure_modes(self):
        request = mouse_project_request(
            impact={"fall_height_m": 0.5, "contact_stiffness_n_per_m": 1e5}
        )
        result = run_pipeline(request)
        impact_section = result["impact"]
        self.assertIsNotNone(impact_section)
        self.assertGreater(impact_section["result"]["peak_force_n"], 0.0)
        self.assertEqual(impact_section["result"]["validity"], "valid")
        self.assertTrue(impact_section["unsupported_failure_modes"])
        self.assertTrue(impact_section["result"]["unsupported_failure_modes"])
        self.assertTrue(result["validity"]["unsupported_failure_modes"])

    def test_validity_aggregates_nested_unsupported_and_missing_impact_result(self):
        no_mass = run_pipeline(mouse_project_request(
            objects=[{"id": "shell", "geometry": SHELL, "material": "Titanium-7"}],
            impact={"fall_height_m": 0.5},
        ))
        validity = no_mass["validity"]
        self.assertEqual(validity["state"], "failed")
        self.assertEqual(validity["confidence"], "low")
        self.assertEqual(no_mass["impact"]["result"], None)
        self.assertTrue(any("mass" in reason for reason in validity["reasons"]))
        self.assertTrue(validity["unsupported_failure_modes"])

        unsupported_run = run_pipeline(mouse_project_request(
            impact={"fall_height_m": 0.5, "contact_stiffness_n_per_m": 1e5}
        ))
        impact_validity = unsupported_run["validity"]
        self.assertEqual(impact_validity["state"], "inconclusive")
        self.assertEqual(impact_validity["confidence"], "low")
        self.assertEqual(
            impact_validity["unsupported_failure_modes"],
            sorted(unsupported_run["impact"]["unsupported_failure_modes"]),
        )

    def test_deterministic_results(self):
        first = run_pipeline(mouse_project_request())
        second = run_pipeline(mouse_project_request())
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["run_id"], second["run_id"])

    def test_cache_hit_returns_same_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ArtifactCache(directory)
            request = mouse_project_request()
            first = run_pipeline(request, cache=cache)
            second = run_pipeline(request, cache=cache)
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertTrue(cache.contains(first["run_id"]))
            self.assertEqual(
                first["manifest"]["manifest_hash"], second["manifest"]["manifest_hash"]
            )

    def test_invalid_geometry_fails_cleanly_with_issue(self):
        request = mouse_project_request(
            objects=[{"id": "bogus", "geometry": {"type": "torus", "radius": 5}, "material": "ABS"}]
        )
        result = run_pipeline(request)
        self.assertEqual(result["lifecycle_state"], "failed")
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("GEOMETRY_PARSE_FAILED", codes)
        self.assertNotEqual(result["errors"], [])

    def test_objects_non_mapping_non_array_reports_invalid_objects(self):
        request = mouse_project_request(objects="shell")
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        error_codes = [item["code"] for item in result["errors"]]
        self.assertIn("INVALID_OBJECTS", codes)
        self.assertIn("INVALID_OBJECTS", error_codes)
        self.assertEqual(result["geometry_summary"]["objects"], [])
        self.assertIsNone(result["mass"]["mass_kg"])

    def test_duplicate_object_ids_are_skipped(self):
        request = mouse_project_request(
            objects=[
                {"id": "shell", "geometry": SHELL, "material": "ABS"},
                {"id": "shell", "geometry": PCB, "material": "FR4"},
            ]
        )
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("DUPLICATE_OBJECT_ID", codes)
        object_ids = [entry["object_id"] for entry in result["geometry_summary"]["objects"]]
        self.assertEqual(object_ids, ["shell"])

    def test_blank_object_id_reports_invalid_object_id(self):
        request = mouse_project_request(objects=[{"id": "  ", "geometry": SHELL, "material": "ABS"}])
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("INVALID_OBJECT_ID", codes)
        self.assertEqual(result["geometry_summary"]["objects"], [])

    def test_collision_pairs_from_options_with_explicit_tolerance(self):
        request = mouse_project_request(
            options={
                "min_thickness_m": 0.001,
                "max_thickness_m": 0.05,
                "collision_pairs": [["shell", "pcb"]],
                "collision_tolerance_m": 0.002,
            }
        )
        result = run_pipeline(request)
        collision_section = result["collision"]
        self.assertEqual(collision_section["status"], "evaluated")
        self.assertTrue(collision_section["configured"])
        self.assertEqual(collision_section["count"], 1)
        pair = collision_section["pairs"][0]
        self.assertEqual(sorted(pair["pair"]), ["pcb", "shell"])
        self.assertIn("tolerance_applied", pair["flags"])
        self.assertAlmostEqual(
            pair["worst_case_clearance_m"], pair["nominal_clearance_m"] - 0.002, places=9
        )

    def test_collision_clearance_pairs_fallback_to_tolerance_profile_margin(self):
        request = mouse_project_request(
            options={"min_thickness_m": 0.001, "clearance_pairs": [["shell", "pcb"]]},
            tolerance_profile={"clearance_margin_m": 0.003},
        )
        result = run_pipeline(request)
        collision_section = result["collision"]
        self.assertEqual(collision_section["status"], "evaluated")
        pair = collision_section["pairs"][0]
        self.assertIn("tolerance_applied", pair["flags"])
        self.assertAlmostEqual(
            pair["worst_case_clearance_m"], pair["nominal_clearance_m"] - 0.003, places=9
        )

    def test_collision_unconfigured_stays_skipped(self):
        result = run_pipeline(mouse_project_request())
        collision_section = result["collision"]
        self.assertEqual(collision_section["status"], "skipped")
        self.assertFalse(collision_section["configured"])
        self.assertEqual(collision_section["count"], 0)
        self.assertEqual(result["errors"], [])

    def test_materials_reports_assigned_evidence_only(self):
        result = run_pipeline(mouse_project_request())
        materials = result["materials"]
        self.assertEqual(
            materials["assignments"],
            {"shell": "ABS", "pcb": "FR4", "battery": "LiPo"},
        )
        self.assertEqual(sorted(materials["definitions"]), ["ABS", "FR4", "LiPo"])
        definition = materials["definitions"]["ABS"]
        self.assertEqual(definition["name"], "ABS")
        self.assertIn("properties", definition)
        self.assertNotIn("PC", materials["definitions"])
        self.assertNotIn("POM", materials["definitions"])

    def test_materials_evidence_includes_unresolvable_assignment(self):
        request = mouse_project_request(
            objects=[{"id": "shell", "geometry": SHELL, "material": "Titanium-7"}]
        )
        result = run_pipeline(request)
        materials = result["materials"]
        self.assertEqual(materials["assignments"], {"shell": "Titanium-7"})
        self.assertEqual(materials["definitions"], {})


class QualificationIntegrityPipelineTests(unittest.TestCase):
    def test_qualification_mode_clean_inputs_reach_pending_review(self):
        result = run_pipeline(
            qualification_request(
                structure=None,
                convergence_evidence=False,
                force_balance=False,
                method={
                    **qualification_request()["method"],
                    "solver_policy": {"require_convergence": False, "require_force_balance": False},
                },
            )
        )
        self.assertEqual(result["errors"], [])
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_pending_review")
        self.assertTrue(qualification["qualified"])
        self.assertEqual(qualification["blocking_keys"], [])
        self.assertEqual(len(qualification["integrity_gates"]), 5)

    def test_qualification_mode_invalid_structural_response_blocks(self):
        result = run_pipeline(qualification_request(
            load_case={
                "kind": "torque",
                "reviewed": True,
                "acceptance_requirement_refs": [{"id": "req-1"}],
            },
        ))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["structural"]["response"]["validity"], "inconclusive")
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", qualification["blocking_keys"])
        self.assertIn("CONVERGENCE_EVIDENCE", qualification["blocking_keys"])
        self.assertEqual(qualification["structural_validity"], "inconclusive")

    def test_qualification_mode_structural_unsupported_modes_block(self):
        result = run_pipeline(qualification_request())
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", qualification["blocking_keys"])
        gate = {
            item["key"]: item for item in qualification["integrity_gates"]
        }["ANALYSIS_VALIDITY"]
        self.assertIn("unsupported failure modes", gate["explanation"])

    def test_qualification_mode_impact_requested_blocks(self):
        result = run_pipeline(qualification_request(
            impact={"fall_height_m": 0.5, "contact_stiffness_n_per_m": 1e5},
        ))
        self.assertEqual(result["errors"], [])
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_blocked")
        self.assertIn("IMPACT_VALIDITY", qualification["blocking_keys"])
        self.assertTrue(result["impact"]["result"]["qualification_blocked"])

    def test_qualification_requirement_target_failure_blocks(self):
        result = run_pipeline(qualification_request(
            requirement={"status": "active", "target": {"metric": "mass_kg", "max": 0.001}},
        ))
        self.assertEqual(result["errors"], [])
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_blocked")
        self.assertIn("REQUIREMENT_EVALUATION", qualification["blocking_keys"])
        evaluation = qualification["requirement_evaluations"][0]
        self.assertEqual(evaluation["status"], "fail")
        target = evaluation["targets"][0]
        self.assertEqual(target["metric"], "mass_kg")
        self.assertAlmostEqual(target["measured"], result["mass"]["mass_kg"])
        self.assertLess(target["margins"]["max"], 0.0)

    def test_qualification_requirement_target_pass_reaches_pending_review(self):
        result = run_pipeline(qualification_request(
            requirement={"status": "active", "target": {"metric": "mass_kg", "max": 10.0}},
            structure=None,
            convergence_evidence=False,
            force_balance=False,
            method={
                **qualification_request()["method"],
                "solver_policy": {"require_convergence": False, "require_force_balance": False},
            },
        ))
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_pending_review")
        self.assertEqual(qualification["requirement_evaluations"][0]["status"], "pass")

    def test_qualification_requirement_without_target_stays_not_evaluated(self):
        result = run_pipeline(qualification_request())
        evaluations = result["qualification"]["requirement_evaluations"]
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0]["status"], "not_evaluated")

    def test_impact_advanced_contact_inputs_reach_pipeline_solver(self):
        result = run_pipeline(
            mouse_project_request(
                impact={
                    "fall_height_m": 0.5,
                    "effective_modulus_pa": 1e9,
                    "contact_radius_m": 0.005,
                    "inertia_tensor_kg_m2": [[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
                    "contact_location_m": [0.01, 0.0, 0.0],
                    "center_of_mass_m": [0.0, 0.0, 0.0],
                },
            )
        )
        estimate = result["impact"]["result"]
        self.assertEqual(estimate["contact_model"], "hertz_nonlinear")
        self.assertIsNotNone(estimate["energy_partition"])


if __name__ == "__main__":
    unittest.main()
