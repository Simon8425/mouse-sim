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
