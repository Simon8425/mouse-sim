import tempfile
import unittest

from mouse_sim import canonical_json
from mouse_sim.cache import ArtifactCache
from mouse_sim.pipeline import (
    ENGINE_VERSION,
    _ENGINE_BEHAVIOR_MODULES,
    pipeline_module,
    run_pipeline,
)

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
                    "maximum_error_fraction": 0.25,
                },
            },
            "geometry": {"closed": True, "reviewed": True, "repairs_reviewed": True},
            "fixtures": {"fixture_type": "simply_supported", "reviewed": True},
            "tolerance_profile": {"process_profile": "molding"},
            "correlation_records": [
                {
                    "record_type": "static_correlation",
                    "review_state": "approved",
                    "comparisons": [
                        {"metric_key": "deflection", "measured": 1.0, "predicted": 1.05},
                    ],
                },
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

    def test_missing_material_issue_and_default_fallback(self):
        request = mouse_project_request(
            objects=[{"id": "shell", "geometry": SHELL, "material": "Titanium-7"}]
        )
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("MATERIAL_NOT_FOUND", codes)
        self.assertIn("DEFAULT_MATERIAL_ASSIGNED", codes)
        # The Default material provides full physical properties, so the
        # simulation never runs on undefined material data: mass is computed.
        self.assertEqual(result["mass"]["mass_status"], "calculated")
        self.assertIsNotNone(result["mass"]["mass_kg"])
        self.assertEqual(result["lifecycle_state"], "completed")
        assignments = result["material_assignments"]
        self.assertEqual(
            assignments,
            [{"object_id": "shell", "material": "default", "source": "default"}],
        )

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
        failed_impact = run_pipeline(mouse_project_request(
            impact={"fall_height_m": -1.0},
        ))
        validity = failed_impact["validity"]
        self.assertEqual(validity["state"], "failed")
        self.assertEqual(validity["confidence"], "low")
        self.assertEqual(failed_impact["impact"]["result"]["validity"], "failed")
        self.assertIn("INVALID_KINEMATICS", failed_impact["impact"]["result"]["flags"])
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

    def test_cache_hit_skips_execution(self):
        # The cached payload stores input hashes under manifest; a hit must
        # actually short-circuit execution (a write-only cache is a bug).
        with tempfile.TemporaryDirectory() as directory:
            cache = ArtifactCache(directory)
            request = mouse_project_request()
            first = run_pipeline(request, cache=cache)
            executed = []

            original = pipeline_module._execute

            def spy(request, mode, options, result):
                executed.append(True)
                return original(request, mode, options, result)

            pipeline_module._execute = spy
            try:
                second = run_pipeline(request, cache=cache)
            finally:
                pipeline_module._execute = original
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(executed, [])

    def test_engine_hash_detects_module_changes(self):
        # The run id must change when an engine module's source changes so
        # stale cached physics are never served.
        import hashlib
        import os
        import tempfile

        from mouse_sim.pipeline import _engine_hash

        with tempfile.TemporaryDirectory() as directory:
            for name in _ENGINE_BEHAVIOR_MODULES:
                with open(os.path.join(directory, name + ".py"), "w", encoding="utf-8") as stream:
                    stream.write("# baseline\n")
            baseline = _engine_hash(root=directory)
            with open(os.path.join(directory, "drop_sim.py"), "a", encoding="utf-8") as stream:
                stream.write("# changed\n")
            changed = _engine_hash(root=directory)
            self.assertNotEqual(baseline, changed)
            self.assertEqual(len(baseline), 64)
        # The installed engine hash is not the empty-input constant.
        self.assertNotEqual(_engine_hash(), hashlib.sha256(b"").hexdigest())

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
        # The unresolvable key is replaced by the Default material so the
        # component is always fully simulatable.
        self.assertEqual(materials["assignments"], {"shell": "default"})
        self.assertEqual(sorted(materials["definitions"]), ["default"])
        self.assertIn("DEFAULT_MATERIAL_ASSIGNED", [item["code"] for item in result["issues"]])


class QualificationIntegrityPipelineTests(unittest.TestCase):
    def test_qualification_mode_clean_inputs_reach_pending_review(self):
        result = run_pipeline(
            qualification_request(
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
        self.assertEqual(len(qualification["integrity_gates"]), 6)

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

    def test_qualification_mode_structural_unsupported_modes_disclosed_not_blocking(self):
        # Unsupported failure modes describe the closed-form model's scope
        # (buckling, fatigue, ...), not a failed analysis: they are disclosed
        # on the gate but must not hard-block an otherwise valid response.
        result = run_pipeline(qualification_request())
        self.assertEqual(result["errors"], [])
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_pending_review")
        self.assertNotIn("ANALYSIS_VALIDITY", qualification["blocking_keys"])
        gate = {
            item["key"]: item for item in qualification["integrity_gates"]
        }["ANALYSIS_VALIDITY"]
        self.assertIn("unsupported failure modes", gate["explanation"])
        self.assertTrue(result["structural"]["response"]["unsupported_failure_modes"])

    def test_impact_allowable_derived_from_material(self):
        # The impact safety factor must reach a real value from the assembly
        # material's allowable when the load-path stress is computable
        # (previously always not_available).
        request = mouse_project_request(impact={
            "fall_height_m": 0.5,
            "contact_stiffness_n_per_m": 1e5,
            "load_path_area_m2": 1e-4,
        })
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        impact_result = result["impact"]["result"]
        self.assertEqual(impact_result["validity"], "valid")
        self.assertNotEqual(impact_result["safety_factor"], "not_available")
        self.assertGreater(float(impact_result["safety_factor"]), 0.0)
        # The safety factor is the material allowable over the load-path
        # stress (ABS tensile allowable 20 MPa in the fixture catalog).
        self.assertAlmostEqual(
            float(impact_result["safety_factor"]),
            20e6 / float(impact_result["load_path_stress_pa"]),
            places=6,
        )

    def test_environment_temperature_derates_structural_response(self):
        base = run_pipeline(mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
        ))
        hot = run_pipeline(mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            environment_temperature_k=353.15,
        ))
        self.assertEqual(base["errors"], [])
        self.assertEqual(hot["errors"], [])
        base_response = base["structural"]["response"]
        hot_response = hot["structural"]["response"]
        self.assertGreater(hot_response["max_displacement_m"], base_response["max_displacement_m"])
        self.assertLess(float(hot_response["safety_factor"]), float(base_response["safety_factor"]))
        self.assertEqual(hot_response["validity"], "approximate")
        self.assertTrue(
            any("temperature" in reason for reason in hot["validity"]["reasons"])
        )

    def test_environment_temperature_out_of_range_warns(self):
        request = mouse_project_request(environment_temperature_k=500.0)
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertIn(
            "ENVIRONMENT_TEMPERATURE_OUT_OF_RANGE", [item["code"] for item in result["issues"]]
        )

    def test_correlation_measured_drops_verdict(self):
        def run_correlation(measured_values):
            drops = []
            for index, (height, measured) in enumerate(measured_values):
                drops.append(
                    {
                        "drop_id": "D{}".format(index + 1),
                        "height_m": height,
                        "surface": "concrete",
                        "orientation": "flat",
                        "measured_peak_accel_g": measured,
                        "sensor": {"quantity": "resultant_peak_g",
                                   "location_body_m": [0.0, 0.0, 0.0]},
                    }
                )
            request = mouse_project_request(
                drop_simulation={"height_m": 0.5, "drop_count": 1},
                correlation={"acceptance": {}, "measured_drops": drops},
            )
            return run_pipeline(request)

        # Measured values close to the prediction at three independent
        # heights pass the acceptance band (the fixture predicts ~187 g at
        # 0.5 m, ~230 g at 0.75 m, ~266 g at 1.0 m on concrete).
        plausible = run_correlation([(0.5, 190.0), (0.75, 233.0), (1.0, 269.0)])
        self.assertEqual(plausible["errors"], [])
        correlation = plausible["correlation"]
        self.assertIsNotNone(correlation)
        self.assertEqual(correlation["verdict"], "pass")
        self.assertEqual(len(correlation["conditions"]), 3)
        # Wildly different measured values fail the correlation honestly.
        implausible = run_correlation([(0.5, 20.0), (0.75, 30.0), (1.0, 25.0)])
        self.assertEqual(implausible["correlation"]["verdict"], "fail")
        self.assertGreaterEqual(len(implausible["correlation"]["explanation"]), 1)

    def test_correlation_invalid_section_fails_gracefully(self):
        request = mouse_project_request(
            drop_simulation={"height_m": 0.5},
            correlation={"measured_drops": "not-a-list"},
        )
        result = run_pipeline(request)
        # The drop simulation still completes; the correlation section reports
        # its own failure instead of killing the run.
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["drop_simulation"])
        self.assertIn(
            "CORRELATION_EVALUATION_FAILED", [item["code"] for item in result["issues"]]
        )

    def test_shell_result_primary_summary(self):
        # The shell is the authoritative engineering result: a consolidated
        # summary (status, stress, deflection, safety factor, critical
        # region, confidence) separate from the secondary component layer.
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        shell = result["shell"]
        self.assertIsNotNone(shell)
        self.assertEqual(shell["classification"], "safe")
        self.assertGreater(shell["min_safety_factor"], 1.0)
        self.assertIsNotNone(shell["peak_stress_pa"])
        self.assertIsNotNone(shell["max_displacement_m"])
        self.assertEqual(shell["physical_model_confidence"], "medium")
        self.assertEqual(shell["statistical_confidence"]["kind"], "single_run")
        self.assertEqual(shell["critical_region_stability"]["stable"], True)
        self.assertTrue(shell["assumptions"])

    def test_shell_result_confidence_drops_with_assumed_mass(self):
        # Assumed mass (open tessellation) must downgrade the shell
        # confidence, not silently bake a fabricated mass into a high-
        # confidence verdict.
        request = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "units": "m",
            "objects": [
                {
                    "id": "shell",
                    "geometry": {
                        "type": "mesh",
                        "vertices": [[0, 0, 0], [0.1, 0, 0], [0, 0.06, 0]],
                        "triangles": [[0, 1, 2]],
                        "units": "m",
                    },
                },
            ],
            "load_case": {"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            "structure": {"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            "drop_simulation": {"height_m": 0.5},
        }
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("DROP_SIMULATION_MASS_ASSUMED", codes)
        shell = result["shell"]
        self.assertIsNotNone(shell)
        self.assertEqual(shell["physical_model_confidence"], "medium")
        self.assertTrue(
            any("mass assumed" in limitation for limitation in shell["limitations"])
        )

    def test_components_never_contaminate_shell_result(self):
        # Architectural invariant: the secondary component models must not
        # feed back into the shell physics.  Running with and without a
        # deliberately failing component spec must produce IDENTICAL shell,
        # structural, mass, and drop results.
        base = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation={"height_m": 0.75, "drop_count": 1},
        )
        with_components = dict(base)
        with_components["components"] = [
            {"component_id": "battery_pack", "type": "battery", "mass_kg": 0.1},
        ]
        with_components["lifecycle"] = {"actuation_cycles": 50_000_000}
        plain = run_pipeline(base)
        contaminated = run_pipeline(with_components)
        self.assertEqual(plain["shell"], contaminated["shell"])
        self.assertEqual(plain["structural"], contaminated["structural"])
        self.assertEqual(plain["mass"], contaminated["mass"])
        self.assertEqual(
            plain["drop_simulation"]["trajectory"], contaminated["drop_simulation"]["trajectory"]
        )
        self.assertIsNone(plain["component_screening"])
        self.assertIsNotNone(contaminated["component_screening"])
        self.assertEqual(contaminated["component_screening"]["confidence"], "low-medium")

    def test_population_shell_robustness_block(self):
        # The population's PRIMARY answer is the shell failure probability
        # across manufacturing variation (wall thickness, modulus, strength,
        # density); the component screening stays secondary.
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 50, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.0008, "material": "ABS"},
            population={"sample_count": 200, "profile": "general", "lifespan_days": 730, "workers": 1},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        population = result["population"]
        shell = population["shell"]
        self.assertIsNotNone(shell)
        self.assertIn("nominal", shell)
        self.assertGreaterEqual(shell["failures"], 0)
        self.assertIn("wilson_ci", shell)
        self.assertTrue(shell["assumptions"])
        # The shell sensitivity includes the shell-specific tolerances.
        shell_params = {item["parameter"] for item in shell["sensitivity"]}
        self.assertIn("wall_thickness_scale", shell_params)
        self.assertIn("shell_strength_scale", shell_params)

    def test_population_without_structure_has_no_shell_block(self):
        request = mouse_project_request(
            population={"sample_count": 100, "profile": "general", "lifespan_days": 730, "workers": 1},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["population"]["shell"])

    def test_qualification_mode_pinned_load_case_without_analysis_blocks(self):
        # A load case pinned for the run with no structural analysis
        # performed must block: "all gates passed with zero physics" is a
        # false green light.
        result = run_pipeline(qualification_request(structure=None))
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["structural"])
        qualification = result["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", qualification["blocking_keys"])
        gate = {
            item["key"]: item for item in qualification["integrity_gates"]
        }["ANALYSIS_VALIDITY"]
        self.assertIn("load case pinned", gate["explanation"])

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


class PipelineRobustnessTests(unittest.TestCase):
    def test_run_pipeline_never_raises_on_non_mapping_request(self):
        result = run_pipeline("not-a-mapping")
        self.assertEqual(result["lifecycle_state"], "failed")
        self.assertEqual(result["errors"][0]["code"], "INVALID_REQUEST")
        self.assertTrue(result["run_id"])

    def test_run_pipeline_never_raises_on_none_request(self):
        result = run_pipeline(None)
        self.assertEqual(result["lifecycle_state"], "completed")
        self.assertEqual(result["errors"], [])

    def test_non_mapping_options_are_ignored_not_crashing(self):
        result = run_pipeline({"objects": [], "options": "not-an-object"})
        self.assertEqual(result["lifecycle_state"], "completed")
        self.assertEqual(result["errors"], [])

    def test_unknown_mode_falls_back_to_exploration(self):
        result = run_pipeline(mouse_project_request(mode="quantum"))
        self.assertEqual(result["mode"], "exploration")
        self.assertEqual(result["errors"], [])

    def test_missing_units_default_to_meters(self):
        request = mouse_project_request()
        del request["units"]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["geometry_summary"]["objects"][0]["units"], "m")
        self.assertTrue(result["geometry_summary"]["objects"][0]["parsed"])

    def test_bad_load_case_is_contained_and_run_completes(self):
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "bogus"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002},
        )
        result = run_pipeline(request)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("STRUCTURAL_EVALUATION_FAILED", codes)
        self.assertIsNone(result["structural"])
        self.assertIsNotNone(result["mass"])
        self.assertEqual(result["lifecycle_state"], "failed")

    def test_bad_impact_section_is_contained_and_run_completes(self):
        request = mouse_project_request(impact="not-an-object")
        result = run_pipeline(request)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("IMPACT_EVALUATION_FAILED", codes)
        self.assertIsNone(result["impact"])
        self.assertIsNotNone(result["mass"])
        self.assertEqual(result["lifecycle_state"], "failed")

    def test_bad_structure_section_is_contained(self):
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude_pa": 1000.0},
            structure="not-an-object",
        )
        result = run_pipeline(request)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("STRUCTURAL_EVALUATION_FAILED", codes)
        self.assertIsNone(result["structural"])
        self.assertEqual(result["lifecycle_state"], "failed")

    def test_empty_objects_run_completes(self):
        result = run_pipeline({"objects": [], "mode": "qualification"})
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["lifecycle_state"], "completed")
        self.assertIsNotNone(result["qualification"])
        self.assertIsNone(result["mass"]["mass_kg"])

    def test_object_without_geometry_does_not_crash_other_objects(self):
        request = mouse_project_request(
            objects=[
                {"id": "shell", "geometry": SHELL, "material": "ABS"},
                {"id": "nogeo", "material": "ABS"},
            ]
        )
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("GEOMETRY_MISSING", codes)
        self.assertEqual(result["lifecycle_state"], "failed")
        self.assertIsNotNone(result["mass"]["mass_kg"])
        self.assertEqual(result["mass"]["mass_status"], "calculated")

    def test_run_id_deterministic_across_requests_with_different_key_order(self):
        first = run_pipeline(mouse_project_request())
        second = run_pipeline(mouse_project_request())
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(canonical_json(first), canonical_json(second))


class DropSimulationPipelineTests(unittest.TestCase):
    def _box_project(self):
        request = mouse_project_request()
        request["objects"] = [
            {
                "id": "box",
                "material": "ABS",
                "geometry": {
                    "type": "box",
                    "size": [0.1, 0.1, 0.1],
                    "units": "m",
                    "transform": {
                        "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "translation": [0, 0, 0.05],
                        "units": "m",
                    },
                },
            }
        ]
        return request

    def test_drop_simulation_runs_and_wires_impact_evidence(self):
        request = self._box_project()
        request["impact"] = None
        request["drop_simulation"] = {
            "test": "drop",
            "height_m": 0.5,
            "surface": "concrete",
            "drop_count": 1,
            "orientation": "flat",
        }
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        simulation = result["drop_simulation"]
        self.assertIsNotNone(simulation)
        self.assertEqual(simulation["config"]["test"], "drop")
        self.assertEqual(len(simulation["drops"]), 1)
        self.assertGreaterEqual(len(simulation["impacts"]), 1)
        self.assertGreater(simulation["peak"]["impact_speed_m_s"], 2.0)
        self.assertGreater(simulation["peak_force_estimate_n"], 0.0)
        self.assertGreater(len(simulation["trajectory"]), 20)
        # Impact section derived from the drop so qualification can evaluate.
        self.assertIsNotNone(result["impact"])
        self.assertEqual(result["impact"]["source"], "drop_simulation")
        self.assertIsNotNone(result["impact"]["result"]["peak_force_n"])
        # Mass properties of the analytic box are safe, so no approximation
        # diagnostics.
        codes = [item["code"] for item in result["issues"]]
        self.assertNotIn("DROP_SIMULATION_MASS_ASSUMED", codes)
        self.assertNotIn("DROP_SIMULATION_INERTIA_APPROXIMATED", codes)

    def test_drop_simulation_mesh_mass_approximation_diagnostics(self):
        request = self._box_project()
        request["drop_simulation"] = {"height_m": 0.3}
        result = run_pipeline(request)
        codes = [item["code"] for item in result["issues"]]
        self.assertNotIn("DROP_SIMULATION_FAILED", codes)

    def test_drop_simulation_invalid_config_fails_cleanly(self):
        request = self._box_project()
        request["drop_simulation"] = {"height_m": 50.0}
        result = run_pipeline(request)
        self.assertIsNone(result["drop_simulation"])
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("DROP_SIMULATION_FAILED", codes)
        self.assertEqual(result["lifecycle_state"], "failed")

    def test_drop_simulation_deterministic(self):
        first = run_pipeline(self._box_project() | {"drop_simulation": {"height_m": 0.4}})
        second = run_pipeline(self._box_project() | {"drop_simulation": {"height_m": 0.4}})
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(
            first["drop_simulation"]["trajectory"],
            second["drop_simulation"]["trajectory"],
        )


if __name__ == "__main__":
    unittest.main()
