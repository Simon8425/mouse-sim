import json
import math
from pathlib import Path
import unittest

from mouse_sim import (
    DocumentValidationError,
    Project,
    ProjectDocument,
    Quantity,
    SCHEMA_ID,
    SCHEMA_VERSION,
    UnitError,
    UnsupportedVersionError,
    canonical_json,
    document_from_dict,
    entity_content_hash,
    load_schema,
    new_meta,
    normalize_unit,
    to_si,
    unit_dimension,
    validate_document,
    with_content_hash,
)


class UnitsTests(unittest.TestCase):
    def test_gram_alias_does_not_shadow_standard_gravity(self):
        self.assertEqual(normalize_unit("g"), "g")
        self.assertEqual(unit_dimension("g"), "mass")
        self.assertEqual(normalize_unit("gravity"), "g0")
        self.assertAlmostEqual(to_si(1, "g"), 1e-3)
        self.assertAlmostEqual(to_si(1, "g0"), 9.80665)

    def test_quantity_normalizes_to_si(self):
        quantity = Quantity.from_value(25, "mm")
        self.assertEqual(quantity.unit, "m")
        self.assertAlmostEqual(quantity.value_si, 0.025)
        self.assertAlmostEqual(quantity.as_unit("cm"), 2.5)

    def test_incompatible_units_are_rejected(self):
        with self.assertRaises(UnitError):
            Quantity.from_value(1, "kg").as_unit("m")


class ModelTests(unittest.TestCase):
    def make_document(self):
        project = Project(new_meta("Project", "project_demo"), name="Demo")
        return ProjectDocument(project=project)

    def test_project_document_round_trip(self):
        document = self.make_document()
        encoded = document.to_dict()
        decoded = ProjectDocument.from_dict(encoded)
        self.assertEqual(decoded, document)
        self.assertEqual(decoded.to_json_dict(), encoded)
        self.assertEqual(encoded["schema_id"], SCHEMA_ID)
        self.assertEqual(encoded["schema_version"], SCHEMA_VERSION)

    def test_content_hash_ignores_entity_identity(self):
        first = Project(new_meta("Project", "project_one", revision=1, created_at="a"), name="Demo")
        second = Project(new_meta("Project", "project_two", revision=9, created_at="b"), name="Demo")
        self.assertEqual(entity_content_hash(first), entity_content_hash(second))
        self.assertEqual(with_content_hash(first).meta.content_hash, entity_content_hash(first))

    def test_canonical_json_is_sorted_and_finite(self):
        self.assertEqual(canonical_json({"b": 2.0, "a": -0.0}), '{"a":0,"b":2}')
        with self.assertRaises(ValueError):
            canonical_json({"value": math.nan})


class SchemaTests(unittest.TestCase):
    def make_document(self):
        return ProjectDocument(project=Project(new_meta("Project", "project_demo")))

    def test_packaged_schema_is_loadable(self):
        schema = load_schema()
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("project", schema["properties"])
        self.assertTrue(Path(__file__).resolve().parents[1].joinpath("schemas", "mouse_sim.schema.json").is_file())

    def test_document_validation_accepts_model_and_mapping(self):
        document = self.make_document()
        self.assertIs(validate_document(document), document)
        self.assertEqual(document_from_dict(document.to_dict()), document)

    def test_unknown_reference_is_rejected(self):
        document = self.make_document().to_dict()
        document["project"]["project_frame_ref"] = {"id": "missing", "content_hash": ""}
        with self.assertRaises(DocumentValidationError):
            validate_document(document)

    def test_unsupported_version_is_rejected(self):
        document = self.make_document().to_dict()
        document["schema_version"] = 99
        with self.assertRaises(UnsupportedVersionError):
            validate_document(document)


if __name__ == "__main__":
    unittest.main()


class HashingDeterminismTests(unittest.TestCase):
    def test_hash_stable_across_dict_insertion_order(self):
        document = ProjectDocument(project=Project(new_meta("Project", "p"), name="Demo"))
        payload = document.to_dict()
        reshuffled = dict(reversed(list(payload.items())))
        self.assertEqual(
            entity_content_hash(ProjectDocument.from_dict(reshuffled)),
            entity_content_hash(document),
        )

    def test_hash_stable_across_serialization_round_trip(self):
        entity = Project(new_meta("Project", "p"), name="Demo")
        self.assertEqual(
            entity_content_hash(entity),
            entity_content_hash(Project.from_dict(entity.to_dict())),
        )

    def test_hash_stable_for_int_float_equivalent_values(self):
        from mouse_sim import Fixture
        first = Fixture(new_meta("Fixture", "f1"), name="fixed", stiffness=Quantity(1, "kg"))
        second = Fixture(new_meta("Fixture", "f1"), name="fixed", stiffness=Quantity(1.0, "kg"))
        self.assertEqual(entity_content_hash(first), entity_content_hash(second))

    def test_content_hash_excludes_identity_includes_semantics(self):
        first = Project(new_meta("Project", "one", revision=1, created_at="a"), name="Demo")
        second = Project(new_meta("Project", "two", revision=9, created_at="b"), name="Demo")
        changed = Project(new_meta("Project", "one", revision=1, created_at="a"), name="Other")
        self.assertEqual(entity_content_hash(first), entity_content_hash(second))
        self.assertNotEqual(entity_content_hash(first), entity_content_hash(changed))

    def test_manifest_hash_stable_across_runs_and_key_order(self):
        from mouse_sim import manifest_hash
        manifest = {
            "schema_id": "gms.run-manifest/1",
            "engine_version": "0.1.0",
            "mode": "exploration",
            "inputs": {"objects": [{"id": "a", "size": [1, 2, 3]}]},
            "input_hashes": {"objects": "abc"},
        }
        self.assertEqual(
            manifest_hash(manifest),
            manifest_hash(dict(reversed(list(manifest.items())))),
        )

    def test_cache_keys_equal_for_equal_and_differ_for_different_inputs(self):
        from mouse_sim import cache_key
        first = cache_key({"mode": "exploration", "objects": []})
        second = cache_key({"mode": "exploration", "objects": []})
        different = cache_key({"mode": "qualification", "objects": []})
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)
        self.assertEqual(cache_key({"a": 1, "b": 2}), cache_key({"b": 2, "a": 1}))


class SchemaErrorDetectionTests(unittest.TestCase):
    def make_document(self):
        return ProjectDocument(project=Project(new_meta("Project", "project_demo")))

    def test_duplicate_entity_ids_detected(self):
        from mouse_sim import Component, document_validation_errors
        data = self.make_document().to_dict()
        component = Component(new_meta("Component", "dup"), name="one").to_dict()
        data["components"] = [component, dict(component)]
        errors = document_validation_errors(data)
        self.assertTrue(any("duplicate entity id" in error for error in errors))

    def test_unknown_root_fields_reported_not_raised(self):
        from mouse_sim import document_validation_errors
        data = self.make_document().to_dict()
        data["bogus_field"] = 1
        errors = document_validation_errors(data)
        self.assertTrue(any("unknown root fields" in error for error in errors))

    def test_wrong_schema_version_reported(self):
        from mouse_sim import document_validation_errors
        data = self.make_document().to_dict()
        data["schema_version"] = 99
        errors = document_validation_errors(data)
        self.assertTrue(any("unsupported schema_version" in error for error in errors))
        with self.assertRaises(UnsupportedVersionError):
            validate_document(data)

    def test_bad_reference_hashes_detected(self):
        from mouse_sim import document_validation_errors
        data = self.make_document().to_dict()
        data["project"]["project_frame_ref"] = {"id": "missing", "content_hash": "deadbeef"}
        errors = document_validation_errors(data)
        self.assertTrue(any("unknown entity id" in error for error in errors))

        data = self.make_document().to_dict()
        data["project"]["project_frame_ref"] = {"id": "project_demo", "content_hash": "not-the-hash"}
        errors = document_validation_errors(data)
        self.assertTrue(any("content_hash mismatch" in error for error in errors))

    def test_non_finite_numbers_detected(self):
        from mouse_sim import document_validation_errors
        data = self.make_document().to_dict()
        data["project"]["unit_policy"]["absolute_length_tolerance_m"] = float("nan")
        errors = document_validation_errors(data)
        self.assertTrue(any("must be finite" in error for error in errors))


class TemperatureAndQuantityTests(unittest.TestCase):
    def test_mm_to_m_round_trip(self):
        from mouse_sim import convert
        self.assertAlmostEqual(convert(convert(1234.5, "mm", "m"), "m", "mm"), 1234.5)
        self.assertAlmostEqual(convert(convert(0.987, "m", "mm"), "mm", "m"), 0.987)

    def test_temperature_offset_correctness(self):
        from mouse_sim import convert, from_si
        self.assertAlmostEqual(to_si(0, "degC"), 273.15)
        self.assertAlmostEqual(to_si(32, "degF"), 273.15)
        self.assertAlmostEqual(from_si(273.15, "degC"), 0.0)
        self.assertAlmostEqual(convert(0, "degC", "K"), 273.15)
        self.assertAlmostEqual(convert(100, "degC", "degF"), 212.0)
        self.assertAlmostEqual(convert(0, "degC", "degF"), 32.0)

    def test_dimensionless_and_strain_units(self):
        self.assertEqual(normalize_unit("strain"), "strain")
        self.assertEqual(unit_dimension("strain"), "dimensionless")
        self.assertEqual(unit_dimension("%"), "dimensionless")
        self.assertAlmostEqual(to_si(50, "%"), 0.5)

    def test_dimension_mismatch_raises_unit_error(self):
        from mouse_sim import convert
        with self.assertRaises(UnitError):
            convert(1, "mm", "kg")
        with self.assertRaises(UnitError):
            to_si(1, "mm", expected_dimension="mass")
        with self.assertRaises(UnitError):
            Quantity.from_value(1, "mm").as_unit("kg")

    def test_force_value_rejected_as_pressure_dimension(self):
        with self.assertRaises(UnitError):
            to_si(1, "N", expected_dimension="pressure")
        with self.assertRaises(UnitError):
            to_si(1, "kN", expected_dimension="pressure")
        self.assertAlmostEqual(to_si(1, "N", expected_dimension="force"), 1.0)
        self.assertAlmostEqual(to_si(1, "kPa", expected_dimension="pressure"), 1000.0)
        with self.assertRaises(UnitError):
            to_si(1, "Pa", expected_dimension="force")

    def test_quantity_normalizes_to_canonical_si_spelling(self):
        self.assertEqual(Quantity.from_value(25, "mm").unit, "m")
        self.assertEqual(Quantity.from_value(2, "g").unit, "kg")
        self.assertAlmostEqual(Quantity.from_value(1000, "g").value_si, 1.0)
        self.assertEqual(Quantity.from_value(0, "degC").unit, "K")
        self.assertAlmostEqual(Quantity.from_value(0, "degC").value_si, 273.15)
        self.assertEqual(Quantity.from_value(1, "strain").unit, "1")
