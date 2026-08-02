import json
from dataclasses import replace
import unittest

from mouse_sim import (
    ApprovalState,
    Component,
    EntityRef,
    MassOverride,
    MaterialAssignment,
    Project,
    ProjectDocument,
    Quantity,
    ValidationError,
    new_meta,
)
from mouse_sim.materials import (
    builtin_materials,
    load_material_catalog,
    qualification_material_gates,
    resolve_assignments,
    validate_mass_override,
    validate_material,
)


class MaterialCatalogTests(unittest.TestCase):
    def test_builtin_catalog_contains_mouse_materials_in_si(self):
        catalog = builtin_materials()
        self.assertEqual(len(catalog), 11)
        self.assertEqual(
            set(catalog),
            {"ABS", "PC", "PC/ABS", "POM", "nylon", "TPU", "FR4", "LiPo", "steel", "PTFE", "magnesium/aluminum"},
        )
        self.assertIn("ABS", catalog)
        self.assertIn("pc/abs", catalog)
        self.assertIn("lipo", catalog)
        self.assertIn("Nylon", catalog)
        self.assertIn("Steel", catalog)
        self.assertIn("magnesium/aluminum", catalog)
        self.assertAlmostEqual(catalog["ABS"].properties.density.value_si, 1040.0)
        self.assertEqual(catalog["ABS"].properties.density.unit, "kg/m^3")
        self.assertEqual(catalog["ABS"].approval_state, ApprovalState.DRAFT)
        self.assertEqual(catalog["ABS"].provenance.confidence, "low")
        lipo = catalog["LiPo"].properties
        for field_name in (
            "yield_strength",
            "ultimate_strength",
            "tensile_allowable",
            "compressive_allowable",
            "shear_allowable",
        ):
            quantity = getattr(lipo, field_name)
            self.assertIsNotNone(quantity)
            self.assertEqual(quantity.unit, "Pa")
            self.assertGreater(quantity.value_si, 0)

    def test_empty_catalog_source_is_optional(self):
        self.assertEqual(len(load_material_catalog()), 0)

    def test_json_catalog_loading_accepts_numeric_si_properties(self):
        payload = {
            "materials": [
                {
                    "name": "Test Polymer",
                    "family": "thermoplastic",
                    "properties": {
                        "density": 1100,
                        "young_modulus": 2100000000,
                        "poissons_ratio": 0.36,
                        "yield_strength": 40000000,
                        "friction_coefficient": 0.3,
                    },
                    "provenance": {
                        "source_type": "supplier",
                        "source_id": "supplier-card-1",
                        "conditioning": "23 C, 50 percent RH",
                        "confidence": "high",
                    },
                    "approval_state": "approved",
                }
            ]
        }
        catalog = load_material_catalog(json.dumps(payload))
        material = catalog["test polymer"]
        self.assertEqual(material.approval_state, ApprovalState.APPROVED)
        self.assertEqual(material.provenance.condition, "23 C, 50 percent RH")
        self.assertEqual(material.properties.young_modulus.unit, "Pa")
        self.assertIs(validate_material(material), material)

    def test_json_catalog_normalizes_explicit_non_si_units_and_preserves_key(self):
        payload = {
            "materials": {
                "vendor_polymer_v2": {
                    "name": "Vendor Polymer",
                    "properties": {
                        "density": {"value": 1.1, "unit": "g/cm^3"},
                        "young_modulus": {"value": 2.1, "unit": "GPa"},
                        "poissons_ratio": 0.36,
                    },
                }
            }
        }
        catalog = load_material_catalog(json.dumps(payload))
        self.assertIn("vendor_polymer_v2", catalog)
        self.assertAlmostEqual(catalog["vendor_polymer_v2"].properties.density.value_si, 1100.0)
        self.assertEqual(catalog["vendor_polymer_v2"].properties.density.unit, "kg/m^3")
        self.assertAlmostEqual(catalog["vendor_polymer_v2"].properties.young_modulus.value_si, 2.1e9)

    def test_malformed_catalog_reports_entry_location(self):
        with self.assertRaises(ValidationError) as context:
            load_material_catalog({"materials": [{"name": "broken", "properties": None}]})
        self.assertIn("materials[0]", str(context.exception))

    def test_invalid_material_ranges_are_rejected(self):
        material = builtin_materials()["ABS"]
        invalid = replace(
            material,
            properties=replace(material.properties, poissons_ratio=0.5),
        )
        with self.assertRaises(ValidationError):
            validate_material(invalid)


class MaterialAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.material = builtin_materials()["ABS"]
        self.component = Component(
            new_meta("Component", "component_shell"),
            name="Shell",
            structural_behavior="shell",
        )
        self.assignment = MaterialAssignment(
            new_meta("MaterialAssignment", "assignment_shell"),
            component_ref=EntityRef(self.component.meta.id),
            material_ref=EntityRef(self.material.meta.id, self.material.meta.content_hash),
            structural_behavior="shell",
        )

    def test_assignment_resolves_by_component_and_material_id(self):
        result = resolve_assignments(
            (self.assignment,),
            {"ABS": self.material},
            (self.component,),
        )
        self.assertEqual(len(result), 1)
        self.assertIs(result[0].material, self.material)
        self.assertIs(result[0].component, self.component)
        self.assertEqual(result[0].structural_behavior, "shell")

    def test_document_infers_materials_components_and_assignments(self):
        document = ProjectDocument(
            project=Project(new_meta("Project", "project_materials")),
            components=(self.component,),
            material_definitions=(self.material,),
            material_assignments=(self.assignment,),
        )
        result = resolve_assignments(document)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].component.meta.id, self.component.meta.id)

    def test_non_strict_resolution_retains_assignment_errors(self):
        broken = replace(self.assignment, component_ref=EntityRef("missing_component"))
        result = resolve_assignments((broken,), {"ABS": self.material}, (self.component,), strict=False)
        self.assertTrue(result[0].errors)
        self.assertIn("unknown component", result[0].errors[0])

    def test_excluded_mass_requires_measured_mass(self):
        component = replace(self.component, structural_behavior="excluded_mass")
        assignment = replace(
            self.assignment,
            component_ref=EntityRef(component.meta.id),
            material_ref=None,
            structural_behavior="excluded_mass",
        )
        with self.assertRaises(ValidationError):
            resolve_assignments((assignment,), {"ABS": self.material}, (component,))

        measured = MassOverride(
            measured_mass=Quantity.from_value(100, "g"),
            uncertainty=Quantity.from_value(1, "g"),
            reviewed=True,
        )
        component = replace(component, mass_override=measured)
        resolved = resolve_assignments((assignment,), {"ABS": self.material}, (component,))
        self.assertIsNone(resolved[0].material)
        self.assertEqual(resolved[0].structural_behavior, "excluded_mass")


class MaterialQualificationTests(unittest.TestCase):
    def approved(self):
        material = builtin_materials()["ABS"]
        return replace(
            material,
            approval_state=ApprovalState.APPROVED,
            provenance=replace(
                material.provenance,
                source_type="supplier",
                source_id="supplier-lot-42",
                condition="23 C, dry, conditioned 48 h",
                confidence="high",
            ),
        )

    def test_builtin_data_is_exploratory_only(self):
        result = qualification_material_gates({"ABS": builtin_materials()["ABS"]})
        self.assertFalse(result.eligible)
        self.assertTrue(any(not check.passed for check in result.checks))

    def test_assignment_errors_block_qualification(self):
        material = self.approved()
        broken = MaterialAssignment(
            new_meta("MaterialAssignment", "assignment_broken"),
            component_ref=EntityRef("missing_component"),
            material_ref=EntityRef(material.meta.id),
            structural_behavior="shell",
        )
        result = qualification_material_gates({"ABS": material}, (broken,), (self._component(),))
        self.assertFalse(result.eligible)
        self.assertTrue(any("unknown component" in check.explanation for check in result.checks))

    @staticmethod
    def _component():
        return Component(new_meta("Component", "component_for_gate"), structural_behavior="shell")

    def test_approved_traceable_material_passes_gate(self):
        result = qualification_material_gates({"ABS": self.approved()})
        self.assertTrue(result.eligible)
        self.assertTrue(all(check.passed for check in result.checks))

    def test_approved_abs_missing_tensile_allowable_fails_structural_gate(self):
        material = self.approved()
        material = replace(material, properties=replace(material.properties, tensile_allowable=None))
        component = self._component()
        assignment = MaterialAssignment(
            new_meta("MaterialAssignment", "assignment_structural"),
            component_ref=EntityRef(component.meta.id),
            material_ref=EntityRef(material.meta.id),
            structural_behavior="shell",
        )

        result = qualification_material_gates({"ABS": material}, (assignment,), (component,))

        self.assertFalse(result.eligible)

    def test_document_gate_infers_materials_assignments_and_components(self):
        material = self.approved()
        component = self._component()
        assignment = MaterialAssignment(
            new_meta("MaterialAssignment", "assignment_document"),
            component_ref=EntityRef(component.meta.id),
            material_ref=EntityRef(material.meta.id),
            structural_behavior="shell",
        )
        document = ProjectDocument(
            project=Project(new_meta("Project", "project_gate")),
            components=(component,),
            material_definitions=(material,),
            material_assignments=(assignment,),
        )
        result = qualification_material_gates(document)
        self.assertTrue(result.eligible)
        self.assertTrue(all(check.passed for check in result.checks))

    def test_mass_override_requires_review_and_traceability(self):
        override = MassOverride(Quantity.from_value(100, "g"))
        with self.assertRaises(ValidationError):
            validate_mass_override(override, qualification=True)


if __name__ == "__main__":
    unittest.main()
