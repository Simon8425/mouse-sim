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
    fatigue_endurance_limit_pa,
    load_material_catalog,
    qualification_material_gates,
    resolve_assignments,
    validate_mass_override,
    validate_material,
)


class MaterialCatalogTests(unittest.TestCase):
    def test_builtin_catalog_contains_mouse_materials_in_si(self):
        catalog = builtin_materials()
        self.assertEqual(len(catalog), 18)
        self.assertEqual(
            set(catalog),
            {"ABS", "PC", "PC/ABS", "POM", "nylon", "TPU", "FR4", "LiPo", "steel", "PTFE", "magnesium/aluminum", "SLA_8001", "SLA_9000HE", "ECO_RESIN", "MJF_PA12_HP", "MJF_PA11_HP", "MJF_PA12S_HP", "default"},
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

    def test_builtin_fatigue_and_use_range_fields(self):
        catalog = builtin_materials()
        cases = {
            "ABS": (14e6, 7, 233.15, 363.15),
            "PC": (25e6, 8, 233.15, 398.15),
            "PC/ABS": (20e6, 8, 233.15, 373.15),
            "POM": (30e6, 10, 233.15, 363.15),
            "nylon": (20e6, 6, 233.15, 373.15),
            "FR4": (65e6, 10, 233.15, 403.15),
            "default": (14e6, 6, 233.15, 363.15),
            "steel": (180e6, 10, 233.15, 473.15),
        }
        for key, (fatigue, k, use_min, use_max) in cases.items():
            properties = catalog[key].properties
            self.assertIsNotNone(properties.fatigue_strength_at_1e6_pa, key)
            self.assertEqual(properties.fatigue_strength_at_1e6_pa.value_si, fatigue, key)
            self.assertEqual(properties.fatigue_exponent_k, k, key)
            self.assertEqual(properties.continuous_use_temperature_min_k, use_min, key)
            self.assertEqual(properties.continuous_use_temperature_max_k, use_max, key)
            # Legacy temperature fields stay 293.15 data-validity placeholders.
            self.assertEqual(properties.temperature_min_k, 293.15, key)
            self.assertEqual(properties.temperature_max_k, 293.15, key)
        for key in ("TPU", "LiPo", "PTFE", "magnesium/aluminum"):
            properties = catalog[key].properties
            self.assertIsNone(properties.fatigue_strength_at_1e6_pa, key)
            self.assertIsNone(properties.fatigue_exponent_k, key)
            self.assertIsNotNone(properties.continuous_use_temperature_min_k, key)

    def test_builtin_steel_carries_endurance_limit_knee(self):
        catalog = builtin_materials()
        self.assertEqual(fatigue_endurance_limit_pa(catalog["steel"]), 180e6)
        self.assertIsNone(fatigue_endurance_limit_pa(catalog["ABS"]))
        self.assertIsNone(fatigue_endurance_limit_pa(catalog["FR4"]))
        validate_material(catalog["steel"])

    def test_every_builtin_fatigue_curve_respects_uts_bound(self):
        # Audit E1: the Basquin law implies sigma(1e3) = sigma_ref*1000^(1/k);
        # every built-in curve must keep that short-life point at or below
        # 90% of the ultimate strength (physically possible and conservative).
        catalog = builtin_materials()
        for key, material in catalog.items():
            properties = material.properties
            anchor = properties.fatigue_strength_at_1e6_pa
            exponent = properties.fatigue_exponent_k
            uts = properties.ultimate_strength
            if anchor is None or exponent is None:
                continue
            implied = anchor.value_si * 1000.0 ** (1.0 / exponent)
            self.assertLessEqual(
                implied,
                0.9 * uts.value_si,
                "{} curve implies sigma(1e3) = {:g} Pa > 0.9*UTS {:g} Pa".format(
                    key, implied, 0.9 * uts.value_si
                ),
            )
            self.assertLessEqual(implied, uts.value_si, key)
            validate_material(material)

    def test_catalog_validation_rejects_implausible_fatigue_curves(self):
        # The old steel curve (220 MPa @ 1e6, slope 5) implied sigma(1e3) =
        # 876 MPa against a 400 MPa UTS; validation must reject curves whose
        # implied sigma(1e3) exceeds UTS (hard error) or sits in the
        # 0.9*UTS..UTS band (no screening margin).
        material = builtin_materials()["steel"]
        over_uts = replace(
            material,
            properties=replace(
                material.properties,
                fatigue_strength_at_1e6_pa=Quantity.from_value(220e6, "Pa"),
                fatigue_exponent_k=5,
            ),
        )
        with self.assertRaises(ValidationError) as context:
            validate_material(over_uts)
        self.assertIn("exceeding the ultimate strength", " ".join(context.exception.errors))
        near_uts = replace(
            material,
            properties=replace(
                material.properties,
                fatigue_strength_at_1e6_pa=Quantity.from_value(195e6, "Pa"),
                fatigue_exponent_k=10,
            ),
        )
        with self.assertRaises(ValidationError) as context:
            validate_material(near_uts)
        self.assertIn("exceeding 90% of the ultimate strength", " ".join(context.exception.errors))
        nonpositive_k = replace(
            material,
            properties=replace(material.properties, fatigue_exponent_k=0),
        )
        with self.assertRaises(ValidationError):
            validate_material(nonpositive_k)

    def test_fatigue_endurance_limit_roundtrips_through_catalog_json(self):
        payload = {
            "materials": {
                "Alloy": {
                    "name": "Alloy",
                    "properties": {
                        "density": 7800,
                        "young_modulus": 200e9,
                        "poissons_ratio": 0.30,
                        "yield_strength": 250e6,
                        "ultimate_strength": 400e6,
                        "tensile_allowable": 150e6,
                        "compressive_allowable": 250e6,
                        "shear_allowable": 100e6,
                        "friction_coefficient": 0.45,
                        "fatigue_strength_at_1e6_pa": 180e6,
                        "fatigue_exponent_k": 10,
                        "fatigue_endurance_limit_pa": 180e6,
                    },
                },
                "Polymer": {
                    "name": "Polymer",
                    "properties": {
                        "density": 1040,
                        "young_modulus": 2.3e9,
                        "poissons_ratio": 0.35,
                        "yield_strength": 40e6,
                        "ultimate_strength": 45e6,
                        "tensile_allowable": 20e6,
                        "compressive_allowable": 60e6,
                        "shear_allowable": 25e6,
                        "friction_coefficient": 0.35,
                        "fatigue_strength_at_1e6_pa": 14e6,
                        "fatigue_exponent_k": 7,
                    },
                },
            }
        }
        catalog = load_material_catalog(json.dumps(payload))
        self.assertEqual(fatigue_endurance_limit_pa(catalog["Alloy"]), 180e6)
        self.assertIsNone(fatigue_endurance_limit_pa(catalog["Polymer"]))

    def test_builtin_fr4_allowable_keeps_laminate_safety_factor(self):
        # Audit LOW: FR-4 is a brittle laminate; a 2-2.5x factor between
        # yield and tensile allowable is typical.  125 MPa gives SF 2.0.
        properties = builtin_materials()["FR4"].properties
        self.assertEqual(properties.tensile_allowable.value_si, 125e6)
        self.assertAlmostEqual(
            properties.yield_strength.value_si / properties.tensile_allowable.value_si, 2.0, places=6
        )

    def test_builtin_fr4_carries_anisotropy_quartet(self):
        definition = builtin_materials()["FR4"]
        self.assertTrue(definition.anisotropy_supported)
        properties = definition.properties
        self.assertEqual(properties.young_modulus_transverse_pa.value_si, 22e9)
        self.assertEqual(properties.young_modulus_thickness_pa.value_si, 9e9)
        self.assertEqual(properties.shear_modulus_xy_pa.value_si, 7e9)
        self.assertEqual(properties.shear_modulus_thickness_pa.value_si, 3.5e9)
        self.assertEqual(properties.poissons_ratio_xy, 0.14)
        self.assertEqual(properties.poissons_ratio_xz, 0.34)
        validate_material(definition)

    def test_anisotropy_supported_requires_quartet(self):
        material = builtin_materials()["ABS"]
        flagged = replace(material, anisotropy_supported=True)
        with self.assertRaises(ValidationError) as context:
            validate_material(flagged)
        self.assertIn("anisotropy_supported requires", " ".join(context.exception.errors))
        missing_e2 = replace(
            material,
            anisotropy_supported=True,
            properties=replace(
                material.properties,
                young_modulus_transverse_pa=Quantity.from_value(22e9, "Pa"),
                young_modulus_thickness_pa=Quantity.from_value(9e9, "Pa"),
                poissons_ratio_xy=0.14,
            ),
        )
        with self.assertRaises(ValidationError) as context:
            validate_material(missing_e2)
        self.assertIn("shear_modulus_xy_pa", " ".join(context.exception.errors))

    def test_fatigue_pair_must_be_set_together(self):
        material = builtin_materials()["ABS"]
        strength_only = replace(
            material,
            properties=replace(
                material.properties,
                fatigue_strength_at_1e6_pa=Quantity.from_value(14e6, "Pa"),
                fatigue_exponent_k=None,
            ),
        )
        with self.assertRaises(ValidationError) as context:
            validate_material(strength_only)
        self.assertIn("must be set together", " ".join(context.exception.errors))
        exponent_only = replace(
            material,
            properties=replace(
                material.properties,
                fatigue_strength_at_1e6_pa=None,
                fatigue_exponent_k=6.0,
            ),
        )
        with self.assertRaises(ValidationError) as context:
            validate_material(exponent_only)
        self.assertIn("must be set together", " ".join(context.exception.errors))

    def test_weld_line_factor_range_enforced(self):
        material = builtin_materials()["ABS"]
        for value in (0.3, 0.9, -0.5, 1.5):
            invalid = replace(material, properties=replace(material.properties, weld_line_factor=value))
            with self.assertRaises(ValidationError):
                validate_material(invalid)
        valid = replace(material, properties=replace(material.properties, weld_line_factor=0.6))
        validate_material(valid)

    def test_new_fields_serialize_in_to_dict(self):
        properties = builtin_materials()["FR4"].properties
        data = properties.to_dict()
        for field_name in (
            "fatigue_strength_at_1e6_pa",
            "fatigue_exponent_k",
            "young_modulus_transverse_pa",
            "young_modulus_thickness_pa",
            "shear_modulus_xy_pa",
            "shear_modulus_thickness_pa",
            "poissons_ratio_xy",
            "poissons_ratio_xz",
            "weld_line_factor",
            "continuous_use_temperature_min_k",
            "continuous_use_temperature_max_k",
        ):
            self.assertIn(field_name, data)
        self.assertEqual(data["fatigue_exponent_k"], 10)
        self.assertEqual(data["poissons_ratio_xy"], 0.14)
        self.assertEqual(data["young_modulus_transverse_pa"]["value_si"], 22e9)
        definition = builtin_materials()["ABS"].to_dict()
        self.assertIn("anisotropy_supported", definition)
        self.assertFalse(definition["anisotropy_supported"])


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
