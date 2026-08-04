import unittest

from mouse_sim import Box, TriangleMesh, run_validation
from mouse_sim.validation import (
    ValidationFinding,
    ValidationReport,
    check_classification,
    check_geometry_health,
    check_material,
    check_pcb_clearance,
    check_wall_thickness,
)


class WallThicknessTests(unittest.TestCase):
    def test_box_thickness_is_min_dimension_and_passes_range(self):
        self.assertEqual(check_wall_thickness(Box((0.05, 0.03, 0.01)), "case", 0.001, 0.05), ())

    def test_box_too_thin_and_too_thick_are_errors(self):
        thin = check_wall_thickness(Box((0.05, 0.03, 0.0005)), "case", 0.001, 0.05)
        self.assertEqual(thin[0].code, "WALL_THICKNESS_TOO_THIN")
        self.assertEqual(thin[0].severity, "error")
        thick = check_wall_thickness(Box((0.06, 0.08, 0.1)), "case", 0.001, 0.05)
        self.assertEqual(thick[0].code, "WALL_THICKNESS_TOO_THICK")

    def test_explicit_dict_thickness_is_exact(self):
        self.assertEqual(check_wall_thickness({"wall_thickness_m": 0.002}, "case", 0.001, 0.05), ())
        self.assertEqual(check_wall_thickness({"geometry": {"wall_thickness_m": 0.0005}}, "case", 0.001, 0.05)[0].code, "WALL_THICKNESS_TOO_THIN")

    def test_unknown_representation_warns(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        findings = check_wall_thickness(mesh, "plate", 0.001, 0.05)
        self.assertEqual(findings[0].code, "THICKNESS_UNKNOWN")
        self.assertEqual(findings[0].severity, "warning")
        self.assertIn("no exact thickness", findings[0].message)

    def test_box_min_dimension_is_exact_thickness(self):
        box = Box((0.06, 0.08, 0.1))
        self.assertEqual(min(box.size), 0.06)
        self.assertEqual(check_wall_thickness(box, "case", 0.06, 0.1), ())


class GeometryHealthTests(unittest.TestCase):
    def test_open_mesh_flagged_as_error(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        findings = check_geometry_health(mesh, "plate")
        open_finding = next(item for item in findings if item.code == "GEOMETRY_OPEN_MESH")
        self.assertEqual(open_finding.severity, "error")
        self.assertTrue(open_finding.evidence_blocking)
        self.assertEqual(open_finding.affected_ids, ("plate",))

    def test_unreviewed_repairs_warn_only_when_unreviewed(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        codes = {item.code for item in check_geometry_health(mesh, "plate", repair_records=[{"operation": "stitch", "reviewed": False}])}
        self.assertIn("GEOMETRY_REPAIRS_UNREVIEWED", codes)
        codes = {item.code for item in check_geometry_health(mesh, "plate", repair_records=[{"operation": "stitch", "reviewed": True}])}
        self.assertNotIn("GEOMETRY_REPAIRS_UNREVIEWED", codes)


class MaterialTests(unittest.TestCase):
    def test_approved_material_passes(self):
        material = {
            "approval_state": "approved",
            "provenance": {"confidence": "high"},
            "properties": {"density": 1040, "young_modulus": 2.3e9},
        }
        self.assertEqual(check_material(material, "case"), ())

    def test_draft_approval_warns(self):
        findings = check_material({"properties": {}}, "case")
        self.assertEqual(next(item for item in findings if item.code == "MAT_UNAPPROVED_PROVENANCE").severity, "warning")

    def test_bad_confidence_and_invalid_values(self):
        findings = check_material({"density": -5, "provenance": {"confidence": "extreme"}, "approval_state": "approved"}, "case")
        codes = {item.code for item in findings}
        self.assertIn("MATERIAL_INVALID", codes)
        self.assertIn("MAT_PROVENANCE_CONFIDENCE", codes)
        self.assertNotIn("MAT_UNAPPROVED_PROVENANCE", codes)


class ClassificationTests(unittest.TestCase):
    def test_unresolved_component_warns(self):
        findings = check_classification({"component_type": "unresolved", "structural_behavior": "solid"}, "case")
        self.assertEqual(next(item for item in findings if item.code == "CLASSIFICATION_UNRESOLVED").severity, "warning")

    def test_missing_structural_behavior_errors(self):
        findings = check_classification({"component_type": "solid"}, "case")
        self.assertEqual(next(item for item in findings if item.code == "CLASSIFICATION_MISSING_BEHAVIOR").severity, "error")


class PcbClearanceTests(unittest.TestCase):
    def setUp(self):
        self.pcb = Box((0.01, 0.02, 0.001))
        self.shell = Box((0.01, 0.02, 0.001), transform={"translation": (0.0, 0.0, 0.004)})

    def test_pass_without_tolerance(self):
        self.assertEqual(check_pcb_clearance(self.pcb, self.shell, 0.002), ())

    def test_thin_margin_warns_with_tolerance(self):
        findings = check_pcb_clearance(self.pcb, self.shell, 0.002, tolerance_m=0.002)
        self.assertEqual(findings[0].code, "PCB_CLEARANCE_MARGIN_THIN")
        self.assertEqual(findings[0].severity, "warning")
        self.assertFalse(findings[0].evidence_blocking)

    def test_failure_flips_to_blocker(self):
        close = Box((0.01, 0.02, 0.001), transform={"translation": (0.0, 0.0, 0.001)})
        findings = check_pcb_clearance(self.pcb, close, 0.002)
        self.assertEqual(findings[0].code, "PCB_CLEARANCE_FAIL")
        self.assertEqual(findings[0].severity, "blocker")
        self.assertTrue(findings[0].evidence_blocking)
        self.assertEqual(findings[0].affected_ids, ("pcb", "shell"))


class ReportTests(unittest.TestCase):
    def test_findings_sorted_by_severity_then_code(self):
        findings = [
            ValidationFinding(code="Z_LATER", severity="warning"),
            ValidationFinding(code="A_EARLY", severity="blocker"),
            ValidationFinding(code="B_BLOCKER", severity="blocker"),
            ValidationFinding(code="M_INFO", severity="info"),
            ValidationFinding(code="C_ERROR", severity="error"),
        ]
        report = ValidationReport.build(findings)
        self.assertEqual([item.code for item in report.findings], ["A_EARLY", "B_BLOCKER", "C_ERROR", "Z_LATER", "M_INFO"])

    def test_build_is_deterministic_regardless_of_input_order(self):
        findings = [
            ValidationFinding(code="X", severity="warning"),
            ValidationFinding(code="Y", severity="info"),
            ValidationFinding(code="A", severity="error"),
            ValidationFinding(code="B", severity="info"),
        ]
        first = ValidationReport.build(findings)
        second = ValidationReport.build(list(reversed(findings)))
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_status_and_validity_state(self):
        self.assertEqual((ValidationReport.build(()).status, ValidationReport.build(()).validity_state), ("pass", "valid"))
        warn = ValidationReport.build([ValidationFinding(code="W", severity="warning")])
        self.assertEqual((warn.status, warn.validity_state), ("warn", "approximate"))
        fail = ValidationReport.build([ValidationFinding(code="E", severity="error")])
        self.assertEqual((fail.status, fail.validity_state), ("fail", "failed"))


class RunValidationTests(unittest.TestCase):
    def test_clean_inputs_pass(self):
        report = run_validation(
            {"case": Box((0.05, 0.03, 0.01))},
            {"case": {"approval_state": "approved", "provenance": {"confidence": "high"}, "properties": {"density": 1040, "young_modulus": 2.3e9}}},
            {"case": {"component_type": "solid", "structural_behavior": "solid"}},
            {"min_thickness_m": 0.001, "max_thickness_m": 0.05},
        )
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.validity_state, "valid")
        self.assertEqual(report.findings, ())

    def test_open_mesh_fails_report(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        report = run_validation(
            {"plate": mesh},
            {},
            {"plate": {"component_type": "surface", "structural_behavior": "shell"}},
            {},
        )
        self.assertEqual(report.status, "fail")
        self.assertTrue(any(item.code == "GEOMETRY_OPEN_MESH" and item.evidence_blocking for item in report.findings))


    def test_strict_promotes_warnings_to_fail(self):
        geometry = {"case": Box((0.05, 0.03, 0.01))}
        classifications = {"case": {"component_type": "solid", "structural_behavior": "solid"}}
        options = {
            "min_thickness_m": 0.001,
            "max_thickness_m": 0.05,
            "repair_records": {"case": [{"operation": "stitch", "reviewed": False}]},
        }
        lax = run_validation(geometry, {}, classifications, options)
        self.assertEqual((lax.status, lax.validity_state), ("warn", "approximate"))
        warning = next(item for item in lax.findings if item.code == "GEOMETRY_REPAIRS_UNREVIEWED")
        self.assertEqual(warning.severity, "warning")
        self.assertFalse(warning.evidence_blocking)

        strict = run_validation(geometry, {}, classifications, dict(options, strict=True))
        self.assertEqual((strict.status, strict.validity_state), ("fail", "failed"))
        self.assertFalse(any(item.severity == "warning" for item in strict.findings))
        promoted = next(item for item in strict.findings if item.code == "GEOMETRY_REPAIRS_UNREVIEWED")
        self.assertEqual(promoted.severity, "error")
        self.assertTrue(promoted.evidence_blocking)

    def test_strict_clean_inputs_still_pass(self):
        report = run_validation(
            {"case": Box((0.05, 0.03, 0.01))},
            {"case": {"approval_state": "approved", "provenance": {"confidence": "high"}, "properties": {"density": 1040, "young_modulus": 2.3e9}}},
            {"case": {"component_type": "solid", "structural_behavior": "solid"}},
            {"min_thickness_m": 0.001, "max_thickness_m": 0.05, "strict": True},
        )
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.validity_state, "valid")


if __name__ == "__main__":
    unittest.main()
