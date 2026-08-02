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


if __name__ == "__main__":
    unittest.main()
