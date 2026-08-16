import unittest

from mouse_sim import Box, TriangleMesh, run_validation
from mouse_sim.validation import (
    LENS_DEFOCUS_BUDGET_M,
    ValidationFinding,
    ValidationReport,
    check_classification,
    check_geometry_health,
    check_material,
    check_optical_defocus,
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

    def test_display_tessellation_topology_is_approximate(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        findings = check_geometry_health(mesh, "plate", display_tessellation=True)
        codes = {item.code: item for item in findings}
        self.assertEqual(codes["GEOMETRY_OPEN_MESH"].severity, "warning")
        self.assertFalse(codes["GEOMETRY_OPEN_MESH"].evidence_blocking)
        self.assertIn("approximate", codes["GEOMETRY_OPEN_MESH"].message)

    def test_run_validation_honors_display_tessellation_option(self):
        vertices = [
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 1.0),
        ]
        triangles = [
            (0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (3, 7, 6), (3, 6, 2),
        ]
        mesh = TriangleMesh(vertices, triangles)
        strict = run_validation({"case": mesh}, {}, {}, {})
        self.assertEqual(strict.status, "fail")
        lax = run_validation({"case": mesh}, {}, {}, {"display_tessellation": True})
        self.assertEqual(lax.status, "warn")
        self.assertTrue(
            all(item.severity != "error" for item in lax.findings if item.code.startswith("GEOMETRY_"))
        )

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


class NonFiniteInputTests(unittest.TestCase):
    def test_nan_density_flagged_in_basic_checks(self):
        findings = check_material({"density": float("nan"), "approval_state": "approved"}, "case")
        codes = {item.code for item in findings}
        self.assertIn("MATERIAL_INVALID", codes)
        self.assertNotIn("MAT_UNAPPROVED_PROVENANCE", codes)

    def test_nan_modulus_flagged_in_basic_checks(self):
        findings = check_material({"young_modulus": float("inf")}, "case")
        self.assertIn("MATERIAL_INVALID", {item.code for item in findings})

    def test_nan_poisson_ratio_flagged_in_basic_checks(self):
        findings = check_material({"poissons_ratio": float("nan")}, "case")
        self.assertIn("MATERIAL_INVALID", {item.code for item in findings})

    def test_nan_wall_thickness_warns_unknown(self):
        findings = check_wall_thickness({"wall_thickness_m": float("nan")}, "case", 0.001, 0.05)
        self.assertEqual(findings[0].code, "THICKNESS_UNKNOWN")
        self.assertEqual(findings[0].severity, "warning")

    def test_display_tessellation_zero_volume_remains_error(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1, 2)])
        findings = check_geometry_health(mesh, "plate", display_tessellation=True)
        zero = next(item for item in findings if item.code == "GEOMETRY_ZERO_VOLUME")
        self.assertEqual(zero.severity, "error")
        self.assertTrue(zero.evidence_blocking)
        open_mesh = next(item for item in findings if item.code == "GEOMETRY_OPEN_MESH")
        self.assertEqual(open_mesh.severity, "warning")
        self.assertFalse(open_mesh.evidence_blocking)


class OpticalDefocusTests(unittest.TestCase):
    """The optical sensor lens z-defocus model: a 40x60x1.6 mm FR-4 PCB
    (clamped-plate bending under shock) carrying a sensor; lens z-shift
    beyond the 0.15 mm budget emits OPTICAL_TRACKING_LOD_SHIFT."""

    def _pcb(self, thickness=0.0016, length=0.06, width=0.04):
        return Box((width, length, thickness))

    def _sensor(self, center=True):
        # 4x4x1 mm sensor package, centered or near an edge of the PCB.
        if center:
            return Box((0.004, 0.004, 0.001), transform={"translation": (0.02, 0.03, 0.0008)})
        return Box((0.004, 0.004, 0.001), transform={"translation": (0.036, 0.05, 0.0008)})

    def test_no_defocus_at_mild_shock(self):
        findings = check_optical_defocus(
            self._pcb(), self._sensor(), "sensor-01", 20.0, lens_height_m=1.5e-3
        )
        self.assertEqual(findings, ())

    def test_high_shock_triggers_lod_shift_warning(self):
        findings = check_optical_defocus(
            self._pcb(), self._sensor(), "sensor-01", 300.0, lens_height_m=1.2e-3
        )
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "OPTICAL_TRACKING_LOD_SHIFT")
        self.assertEqual(finding.severity, "warning")
        self.assertFalse(finding.evidence_blocking)
        self.assertIn("0.15", finding.message)
        self.assertEqual(finding.affected_ids, ("sensor-01",))

    def test_threshold_boundary(self):
        # Just under the budget: no finding; just over: warning.
        under = check_optical_defocus(
            self._pcb(), self._sensor(), "sensor-01", 50.0,
            lens_height_m=1.2e-3, lens_defocus_budget_m=1e6,
        )
        self.assertEqual(under, ())
        over = check_optical_defocus(
            self._pcb(), self._sensor(), "sensor-01", 50.0,
            lens_height_m=1.2e-3, lens_defocus_budget_m=1e-9,
        )
        self.assertEqual(over[0].code, "OPTICAL_TRACKING_LOD_SHIFT")

    def test_missing_shock_data_warns_unknown(self):
        findings = check_optical_defocus(self._pcb(), self._sensor(), "sensor-01", 0.0)
        self.assertEqual(findings[0].code, "OPTICAL_TRACKING_LOD_UNKNOWN")
        self.assertEqual(findings[0].severity, "warning")

    def test_missing_geometry_warns_unknown(self):
        findings = check_optical_defocus(None, self._sensor(), "sensor-01", 100.0)
        self.assertEqual(findings[0].code, "OPTICAL_TRACKING_LOD_UNKNOWN")

    def test_lens_height_band_constants(self):
        # The gaming-mouse lens stack is a design band 1.2-1.5 mm; the
        # defocus budget is 0.15 mm.
        self.assertAlmostEqual(LENS_DEFOCUS_BUDGET_M, 0.15e-3)

    def test_run_validation_wires_optical_check(self):
        report = run_validation(
            {"pcb": self._pcb(), "sensor": self._sensor()},
            {},
            {},
            {
                "pcb_object_id": "pcb",
                "sensor_object_id": "sensor",
                "drop_peak_accel_g": 300.0,
            },
        )
        codes = {item.code for item in report.findings}
        self.assertIn("OPTICAL_TRACKING_LOD_SHIFT", codes)
        self.assertEqual(report.status, "warn")  # warning-only -> warn, not fail


if __name__ == "__main__":
    unittest.main()
