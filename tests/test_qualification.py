import unittest

from mouse_sim import (
    ApprovalState,
    AnalysisMethod,
    EvidenceDisposition,
    ResultMode,
    new_meta,
)
from mouse_sim.qualification import (
    QualificationResult,
    evaluate_qualification,
    impact_qualification_status,
    method_supports,
)


def approved_inputs(**overrides):
    data = {
        "method": {
            "method_key": "static_nonlinear_v1",
            "method_type": "static_nonlinear",
            "approved_for_qualification": True,
            "approval_state": "approved",
            "requires_tolerance_profile": True,
            "required_capability_keys": ("static", "nonlinear"),
            "solver_policy": {"require_convergence": True, "require_force_balance": True},
            "required_correlation_policy": {
                "required": True,
                "required_record_types": ("static_correlation",),
                "require_reviewed_records": True,
            },
        },
        "geometry": {
            "geometry_health": {"solid_status": "closed", "reviewed": True},
            "repair_records": ({"reviewed": True},),
        },
        "materials": (
            {
                "approval_state": "approved",
                "provenance": {
                    "source_type": "supplier",
                    "source_id": "supplier-lot-42",
                    "condition": "23 C, dry, conditioned 48 h",
                    "confidence": "high",
                },
            },
        ),
        "load_case": {"reviewed": True, "acceptance_requirement_refs": ({"id": "req-1"},)},
        "fixtures": ({"reviewed": True},),
        "tolerance_profile": {"name": "default", "process_profile": "molding"},
        "correlation_records": ({"record_type": "static_correlation", "review_state": "approved"},),
        "requirement": {"status": "active", "acceptance": {"metric_key": "deflection"}},
        "validation_report": {"findings": ()},
        "solver": {"capability_keys": ("static", "nonlinear", "modal")},
        "convergence_evidence": True,
        "force_balance": True,
        "structural_response": {"validity": "valid"},
    }
    data.update(overrides)
    return data


class QualificationSeparationTests(unittest.TestCase):
    def test_exploration_never_qualifies(self):
        result = evaluate_qualification(ResultMode.EXPLORATION, **approved_inputs())
        self.assertIsInstance(result, QualificationResult)
        self.assertEqual(result.mode, "exploration")
        self.assertFalse(result.qualified)
        self.assertEqual(result.evidence_disposition, "exploration_only")
        self.assertEqual(result.evidence_disposition, EvidenceDisposition.EXPLORATION_ONLY.value)
        self.assertTrue(all(gate.evaluable for gate in result.gates))
        self.assertTrue(all(gate.passed for gate in result.gates))
        self.assertEqual(result.blocking_keys, ())

    def test_missing_method_blocks_qualification(self):
        result = evaluate_qualification("qualification", **approved_inputs(method=None))
        self.assertFalse(result.qualified)
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("METHOD_APPROVED", result.blocking_keys)
        gate = {item.key: item for item in result.gates}["METHOD_APPROVED"]
        self.assertFalse(gate.evaluable)
        self.assertTrue(gate.blocker)

    def test_approved_inputs_reach_pending_review(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        self.assertTrue(result.qualified)
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertEqual(result.blocking_keys, ())
        self.assertTrue(all(gate.evaluable and gate.passed for gate in result.gates))
        keys = [gate.key for gate in result.gates]
        self.assertEqual(keys, sorted(keys))

    def test_missing_correlation_blocks(self):
        result = evaluate_qualification("qualification", **approved_inputs(correlation_records=None))
        self.assertFalse(result.qualified)
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("CORRELATION", result.blocking_keys)
        gate = {item.key: item for item in result.gates}["CORRELATION"]
        self.assertFalse(gate.evaluable)

    def test_blocking_validation_issue_blocks(self):
        report = {"findings": ({"code": "E-1", "evidence_blocking": True},)}
        result = evaluate_qualification("qualification", **approved_inputs(validation_report=report))
        self.assertFalse(result.qualified)
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("NO_BLOCKING_ISSUES", result.blocking_keys)

    def test_unreviewed_correlation_record_blocks(self):
        records = ({"record_type": "static_correlation", "review_state": "unreviewed"},)
        result = evaluate_qualification("qualification", **approved_inputs(correlation_records=records))
        self.assertFalse(result.qualified)
        self.assertIn("CORRELATION", result.blocking_keys)

    def test_model_objects_accepted(self):
        method = AnalysisMethod(
            new_meta("AnalysisMethod", "method_v1"),
            method_key="static_v1",
            method_type="static",
            approval_state=ApprovalState.APPROVED,
            approved_for_qualification=True,
        )
        result = evaluate_qualification("qualification", **approved_inputs(method=method))
        self.assertTrue(result.qualified)
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")

    def test_to_dict_contents(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        payload = result.to_dict()
        self.assertEqual(payload["mode"], "qualification")
        self.assertTrue(payload["qualified"])
        self.assertEqual(len(payload["gates"]), 12)
        self.assertEqual(payload["gates"][0]["key"], "CONVERGENCE")
        self.assertEqual(payload["blocking_keys"], [])
        self.assertTrue(payload["summary"])


class ImpactQualificationTests(unittest.TestCase):
    def test_impact_blocked_by_default(self):
        status = impact_qualification_status()
        self.assertFalse(status["eligible"])
        self.assertTrue(status["blocked"])
        self.assertIn("blocked", status["reason"])

    def test_impact_requires_validation_even_when_approved(self):
        method = {"method_type": "impact_energy", "approved_for_qualification": True, "approval_state": "approved"}
        status = impact_qualification_status(method=method)
        self.assertFalse(status["eligible"])
        self.assertTrue(status["blocked"])
        self.assertTrue(status["impact_energy"])
        self.assertTrue(status["method_approved"])

    def test_impact_approved_and_validated_is_eligible(self):
        method = {"method_type": "impact_energy", "approved_for_qualification": True, "approval_state": "approved"}
        status = impact_qualification_status(method=method, validated=True)
        self.assertTrue(status["eligible"])
        self.assertFalse(status["blocked"])

    def test_impact_unapproved_method_blocked_even_when_validated(self):
        method = {"method_type": "impact", "approved_for_qualification": False, "approval_state": "draft"}
        status = impact_qualification_status(method=method, validated=True)
        self.assertFalse(status["eligible"])
        self.assertTrue(status["blocked"])

    def test_method_supports_capability_subset(self):
        method = {"required_capability_keys": ("static", "nonlinear")}
        self.assertTrue(method_supports(method, ("static", "nonlinear", "modal")))
        self.assertFalse(method_supports(method, ("static",)))
        self.assertFalse(method_supports(None, ("static",)))
        self.assertTrue(method_supports({}, ("static",)))


class AnalysisValidityIntegrityTests(unittest.TestCase):
    def test_invalid_structural_response_blocks_qualification(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            structural_response={"validity": "failed"},
        ))
        self.assertFalse(result.qualified)
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", result.blocking_keys)
        gate = {item.key: item for item in result.integrity_gates}["ANALYSIS_VALIDITY"]
        self.assertFalse(gate.passed)
        self.assertTrue(gate.blocker)
        self.assertIn("failed", gate.explanation)

    def test_inconclusive_structural_response_never_reaches_pending_review(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            structural_response={"validity": "inconclusive"},
        ))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", result.blocking_keys)

    def test_unsupported_failure_modes_block_even_when_validity_valid(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            structural_response={
                "validity": "valid",
                "unsupported_failure_modes": ["UNSUPPORTED_BUCKLING"],
            },
        ))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", result.blocking_keys)
        gate = {item.key: item for item in result.integrity_gates}["ANALYSIS_VALIDITY"]
        self.assertIn("UNSUPPORTED_BUCKLING", gate.explanation)

    def test_validation_report_status_fail_blocks(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            validation_report={"status": "fail", "findings": ()},
        ))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", result.blocking_keys)

    def test_valid_structural_response_allows_pending_review(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            structural_response={"validity": "valid"},
        ))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertNotIn("ANALYSIS_VALIDITY", result.blocking_keys)

    def test_claimed_evidence_without_structural_response_is_blocked(self):
        result = evaluate_qualification("qualification", **approved_inputs(structural_response=None))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        gate = {item.key: item for item in result.integrity_gates}["ANALYSIS_VALIDITY"]
        self.assertTrue(gate.passed)
        self.assertTrue(gate.explanation.strip())
        gate = {item.key: item for item in result.integrity_gates}["CONVERGENCE_EVIDENCE"]
        self.assertFalse(gate.passed)
        self.assertIn("cannot be substantiated", gate.explanation)


class ImpactIntegrityTests(unittest.TestCase):
    def test_impact_blocked_result_blocks_qualification(self):
        result = evaluate_qualification("qualification", **approved_inputs(impact={
            "result": {
                "qualification_blocked": True,
                "validity": "valid",
                "unsupported_failure_modes": [],
            },
            "unsupported_failure_modes": [],
        }))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("IMPACT_VALIDITY", result.blocking_keys)
        gate = {item.key: item for item in result.integrity_gates}["IMPACT_VALIDITY"]
        self.assertIn("qualification_blocked", gate.explanation)

    def test_impact_unsupported_modes_block_qualification(self):
        result = evaluate_qualification("qualification", **approved_inputs(impact={
            "result": {
                "qualification_blocked": False,
                "validity": "valid",
                "unsupported_failure_modes": ["UNSUPPORTED_SHEAROUT"],
            },
            "unsupported_failure_modes": [],
        }))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("IMPACT_VALIDITY", result.blocking_keys)

    def test_impact_section_unsupported_modes_block_qualification(self):
        result = evaluate_qualification("qualification", **approved_inputs(impact={
            "result": {
                "qualification_blocked": False,
                "validity": "valid",
                "unsupported_failure_modes": [],
            },
            "unsupported_failure_modes": ["UNSUPPORTED_BUCKLING"],
        }))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("IMPACT_VALIDITY", result.blocking_keys)

    def test_impact_missing_result_blocks_qualification(self):
        result = evaluate_qualification("qualification", **approved_inputs(impact={
            "result": None,
            "reason": "no mass available for impact estimate",
            "unsupported_failure_modes": [],
        }))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("IMPACT_VALIDITY", result.blocking_keys)

    def test_no_impact_requested_is_clean(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        gate = {item.key: item for item in result.integrity_gates}["IMPACT_VALIDITY"]
        self.assertTrue(gate.passed)
        self.assertNotIn("IMPACT_VALIDITY", result.blocking_keys)

    def test_unblocked_clean_impact_allows_pending_review(self):
        result = evaluate_qualification("qualification", **approved_inputs(impact={
            "result": {
                "qualification_blocked": False,
                "validity": "valid",
                "unsupported_failure_modes": [],
            },
            "unsupported_failure_modes": [],
        }))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")


class CorrelationErrorTests(unittest.TestCase):
    def _inputs(self, **overrides):
        data = approved_inputs()
        data["method"] = dict(data["method"])
        data["method"]["required_correlation_policy"] = {
            "required": True,
            "required_record_types": ("static_correlation",),
            "require_reviewed_records": True,
            "maximum_error_fraction": 0.1,
        }
        data.update(overrides)
        return data

    def test_error_fraction_exceeding_maximum_blocks(self):
        records = (
            {
                "record_type": "static_correlation",
                "review_state": "approved",
                "comparisons": (
                    {"metric_key": "deflection", "predicted": 1.0, "measured": 1.5, "relative_error": 0.5},
                ),
            },
        )
        result = evaluate_qualification("qualification", **self._inputs(correlation_records=records))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("CORRELATION_ERROR", result.blocking_keys)
        gate = {item.key: item for item in result.integrity_gates}["CORRELATION_ERROR"]
        self.assertIn("0.5", gate.explanation)
        self.assertIn("0.1", gate.explanation)

    def test_error_fraction_within_maximum_passes(self):
        records = (
            {
                "record_type": "static_correlation",
                "review_state": "approved",
                "comparisons": (
                    {"metric_key": "deflection", "predicted": 1.0, "measured": 1.05, "relative_error": 0.05},
                ),
            },
        )
        result = evaluate_qualification("qualification", **self._inputs(correlation_records=records))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertNotIn("CORRELATION_ERROR", result.blocking_keys)

    def test_error_fraction_computed_from_measured_and_predicted(self):
        records = (
            {
                "record_type": "static_correlation",
                "review_state": "approved",
                "comparisons": (
                    {"metric_key": "deflection", "predicted": 1.0, "measured": 1.2},
                ),
            },
        )
        result = evaluate_qualification("qualification", **self._inputs(correlation_records=records))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("CORRELATION_ERROR", result.blocking_keys)

    def test_no_maximum_configured_does_not_block(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        gate = {item.key: item for item in result.integrity_gates}["CORRELATION_ERROR"]
        self.assertTrue(gate.passed)
        self.assertNotIn("CORRELATION_ERROR", result.blocking_keys)

    def test_correlation_not_required_does_not_block(self):
        data = approved_inputs()
        data["method"] = dict(data["method"])
        data["method"]["required_correlation_policy"] = {"required": False}
        result = evaluate_qualification("qualification", **data)
        gate = {item.key: item for item in result.integrity_gates}["CORRELATION_ERROR"]
        self.assertTrue(gate.passed)


class RequirementEvaluationTests(unittest.TestCase):
    def test_structured_target_pass_emits_measured_and_margin(self):
        requirement = {"status": "active", "target": {"metric": "mass_kg", "max": 0.05}}
        pipeline_result = {"mass": {"mass_kg": 0.04}}
        result = evaluate_qualification("qualification", **approved_inputs(
            requirement=requirement, pipeline_result=pipeline_result,
        ))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        evaluation = result.requirement_evaluations[0]
        self.assertEqual(evaluation["status"], "pass")
        target = evaluation["targets"][0]
        self.assertEqual(target["metric"], "mass_kg")
        self.assertEqual(target["measured"], 0.04)
        self.assertEqual(target["max"], 0.05)
        self.assertAlmostEqual(target["margins"]["max"], 0.01)

    def test_structured_target_failure_blocks(self):
        requirement = {"status": "active", "target": {"metric": "mass_kg", "max": 0.05}}
        pipeline_result = {"mass": {"mass_kg": 0.06}}
        result = evaluate_qualification("qualification", **approved_inputs(
            requirement=requirement, pipeline_result=pipeline_result,
        ))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("REQUIREMENT_EVALUATION", result.blocking_keys)
        self.assertEqual(result.requirement_evaluations[0]["status"], "fail")

    def test_min_bound_target(self):
        requirement = {"status": "active", "target": {"metric": "mass_kg", "min": 0.02}}
        pipeline_result = {"mass": {"mass_kg": 0.04}}
        result = evaluate_qualification("qualification", **approved_inputs(
            requirement=requirement, pipeline_result=pipeline_result,
        ))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        target = result.requirement_evaluations[0]["targets"][0]
        self.assertEqual(target["status"], "pass")
        self.assertAlmostEqual(target["margins"]["min"], 0.02)

    def test_unmeasurable_target_blocks(self):
        requirement = {"status": "active", "target": {"metric": "max_displacement_m", "max": 0.001}}
        result = evaluate_qualification("qualification", **approved_inputs(
            requirement=requirement, pipeline_result={"mass": {"mass_kg": 0.04}},
        ))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("REQUIREMENT_EVALUATION", result.blocking_keys)
        self.assertEqual(result.requirement_evaluations[0]["status"], "not_available")

    def test_max_displacement_metric_resolved_from_structural_response(self):
        requirement = {"status": "active", "target": {"metric": "max_displacement_m", "max": 0.001}}
        pipeline_result = {"structural": {"response": {"max_displacement_m": 0.0005}}}
        result = evaluate_qualification("qualification", **approved_inputs(
            requirement=requirement, pipeline_result=pipeline_result,
        ))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertEqual(result.requirement_evaluations[0]["targets"][0]["status"], "pass")

    def test_requirement_without_structured_target_is_not_evaluated(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertEqual(result.requirement_evaluations[0]["status"], "not_evaluated")
        self.assertNotIn("REQUIREMENT_EVALUATION", result.blocking_keys)

    def test_requirements_list_is_evaluated(self):
        requirement = {"status": "active", "target": {"metric": "mass_kg", "max": 0.05}}
        other = {"status": "active", "acceptance": {"metric_key": "deflection"}}
        result = evaluate_qualification("qualification", **approved_inputs(
            requirement=requirement,
            requirements=[requirement, other],
            pipeline_result={"mass": {"mass_kg": 0.04}},
        ))
        statuses = [item["status"] for item in result.requirement_evaluations]
        self.assertEqual(statuses, ["pass", "not_evaluated"])

    def test_to_dict_includes_integrity_fields(self):
        payload = evaluate_qualification("qualification", **approved_inputs()).to_dict()
        for key in (
            "integrity_gates",
            "requirement_evaluations",
            "convergence_evidence",
            "force_balance",
            "structural_validity",
        ):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["integrity_gates"]), 5)


class ConvergenceEvidenceTests(unittest.TestCase):
    def test_convergence_claimed_with_invalid_response_blocks(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            convergence_evidence=True, force_balance=True,
            structural_response={"validity": "failed"},
        ))
        self.assertIn("CONVERGENCE_EVIDENCE", result.blocking_keys)
        gate = {item.key: item for item in result.integrity_gates}["CONVERGENCE_EVIDENCE"]
        self.assertFalse(gate.passed)
        self.assertIn("validity", gate.explanation)

    def test_convergence_claimed_with_valid_response_passes(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            convergence_evidence=True, force_balance=True,
            structural_response={"validity": "valid"},
        ))
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        gate = {item.key: item for item in result.integrity_gates}["CONVERGENCE_EVIDENCE"]
        self.assertTrue(gate.passed)

    def test_unclaimed_evidence_is_clean(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            convergence_evidence=False, force_balance=False,
        ))
        gate = {item.key: item for item in result.integrity_gates}["CONVERGENCE_EVIDENCE"]
        self.assertTrue(gate.passed)
        self.assertNotIn("CONVERGENCE_EVIDENCE", result.blocking_keys)

    def test_explicit_evidence_fields_in_result(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            convergence_evidence=True, force_balance=True,
            structural_response={"validity": "valid"},
        ))
        self.assertTrue(result.convergence_evidence)
        self.assertTrue(result.force_balance)
        self.assertEqual(result.structural_validity, "valid")
        payload = result.to_dict()
        self.assertIs(payload["convergence_evidence"], True)
        self.assertIs(payload["force_balance"], True)
        self.assertEqual(payload["structural_validity"], "valid")


class RobustnessQualificationTests(unittest.TestCase):
    def test_nan_correlation_values_do_not_crash(self):
        data = approved_inputs()
        data["method"] = dict(data["method"])
        data["method"]["required_correlation_policy"] = {
            "required": True,
            "required_record_types": ("static_correlation",),
            "require_reviewed_records": True,
            "maximum_error_fraction": 0.1,
        }
        data["correlation_records"] = (
            {
                "record_type": "static_correlation",
                "review_state": "approved",
                "comparisons": (
                    {"metric_key": "deflection", "measured": float("nan"), "predicted": 1.0},
                    {"metric_key": "stress", "predicted": 0.0, "measured": 1.0},
                    {"metric_key": "force", "relative_error": float("inf")},
                ),
            },
        )
        result = evaluate_qualification("qualification", **data)
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertNotIn("CORRELATION_ERROR", result.blocking_keys)

    def test_missing_evidence_with_conflicting_claims_blocks(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            validation_report={"findings": ({"code": "E", "evidence_blocking": True},)},
            structural_response={"validity": "failed"},
        ))
        self.assertEqual(result.evidence_disposition, "qualification_blocked")
        self.assertIn("NO_BLOCKING_ISSUES", result.blocking_keys)
        self.assertIn("ANALYSIS_VALIDITY", result.blocking_keys)
        failed_gates = [gate.key for gate in result.integrity_gates if not gate.passed]
        self.assertEqual(
            sorted(failed_gates),
            sorted(("ANALYSIS_VALIDITY", "CONVERGENCE_EVIDENCE")),
        )

    def test_requirements_list_only_still_evaluates_governing_requirement_gate(self):
        requirement = {"status": "active", "acceptance": {"metric_key": "deflection"}}
        result = evaluate_qualification(
            "qualification",
            **approved_inputs(requirement=None, requirements=[requirement]),
        )
        gate = {item.key: item for item in result.gates}["REQUIREMENT_ACTIVE"]
        self.assertTrue(gate.passed)
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")

    def test_requirements_list_only_with_unapproved_first_requirement_blocks(self):
        requirement = {"status": "draft", "acceptance": {"metric_key": "deflection"}}
        result = evaluate_qualification(
            "qualification",
            **approved_inputs(requirement=None, requirements=[requirement]),
        )
        gate = {item.key: item for item in result.gates}["REQUIREMENT_ACTIVE"]
        self.assertFalse(gate.passed)
        self.assertIn("REQUIREMENT_ACTIVE", result.blocking_keys)

    def test_materials_single_definition_mapping_gate_blocks_when_unapproved(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            materials={"meta": {"entity_type": "MaterialDefinition", "id": "mat_x"},
                       "properties": {"density": 1040},
                       "provenance": {"source_type": "supplier", "source_id": "s1",
                                      "condition": "c", "confidence": "high"}},
        ))
        gate = {item.key: item for item in result.gates}["MATERIALS_APPROVED"]
        self.assertFalse(gate.passed)
        self.assertIn("MATERIALS_APPROVED", result.blocking_keys)

    def test_empty_findings_report_passes_no_blocking_issues(self):
        result = evaluate_qualification("qualification", **approved_inputs(
            validation_report={"findings": (), "status": "pass"},
        ))
        gate = {item.key: item for item in result.gates}["NO_BLOCKING_ISSUES"]
        self.assertTrue(gate.passed)
        self.assertNotIn("NO_BLOCKING_ISSUES", result.blocking_keys)

    def test_max_disposition_is_never_accepted(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertNotEqual(result.evidence_disposition, "qualification_accepted")


if __name__ == "__main__":
    unittest.main()


class MissingInputGateTests(unittest.TestCase):
    def test_exploration_always_exploration_only_regardless_of_inputs(self):
        for inputs in ({}, {"method": None}, approved_inputs()):
            result = evaluate_qualification(ResultMode.EXPLORATION, **inputs)
            self.assertEqual(result.evidence_disposition, "exploration_only")
            self.assertFalse(result.qualified)

    def test_each_missing_input_yields_not_evaluable_blocker_gate(self):
        missing_map = {
            "method": "METHOD_APPROVED",
            "geometry": "GEOMETRY_APPROVED",
            "materials": "MATERIALS_APPROVED",
            "load_case": "LOADCASE_PINNED",
            "fixtures": "FIXTURES_REVIEWED",
            "tolerance_profile": "TOLERANCE_PROFILE",
            "solver": "SOLVER_CAPABLE",
            "convergence_evidence": "CONVERGENCE",
            "force_balance": "FORCE_BALANCE",
            "correlation_records": "CORRELATION",
            "requirement": "REQUIREMENT_ACTIVE",
            "validation_report": "NO_BLOCKING_ISSUES",
        }
        for missing, gate_key in missing_map.items():
            inputs = approved_inputs()
            inputs[missing] = None
            result = evaluate_qualification("qualification", **inputs)
            self.assertFalse(result.qualified, missing)
            self.assertEqual(result.evidence_disposition, "qualification_blocked", missing)
            self.assertIn(gate_key, result.blocking_keys, missing)
            gate = {item.key: item for item in result.gates}[gate_key]
            self.assertFalse(gate.evaluable, missing)
            self.assertTrue(gate.blocker, missing)
            self.assertTrue(gate.explanation.strip(), missing)

    def test_gate_specs_cover_the_required_gate_set(self):
        from mouse_sim.qualification import GATE_SPECS
        keys = {key for key, _ in GATE_SPECS}
        required = {
            "METHOD_APPROVED", "GEOMETRY_APPROVED", "MATERIALS_APPROVED",
            "LOADCASE_PINNED", "FIXTURES_REVIEWED", "TOLERANCE_PROFILE",
            "SOLVER_CAPABLE", "CONVERGENCE", "FORCE_BALANCE", "CORRELATION",
            "REQUIREMENT_ACTIVE", "NO_BLOCKING_ISSUES",
        }
        self.assertTrue(required.issubset(keys))

    def test_qualification_never_reaches_accepted(self):
        result = evaluate_qualification("qualification", **approved_inputs())
        self.assertEqual(result.evidence_disposition, "qualification_pending_review")
        self.assertNotEqual(result.evidence_disposition, "qualification_accepted")
        self.assertTrue(result.qualified)
