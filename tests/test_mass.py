import math
import unittest

from mouse_sim import Box, Quantity, TriangleMesh, classify_objects, mass_properties


class MassAggregationTests(unittest.TestCase):
    def test_calculated_mass_center_and_parallel_axis_inertia(self):
        document = {
            "left": Box((1, 1, 1)),
            "right": Box((1, 1, 1), transform={"translation": (1, 0, 0)}),
        }
        result = mass_properties(document, {
            "left": Quantity.from_value(1, "g/cm^3"),
            "right": {"value": 2, "unit": "g/cm^3"},
        })
        self.assertEqual(result.mass_status, "calculated")
        self.assertAlmostEqual(result.mass_kg, 3000.0)
        self.assertAlmostEqual(result.center_of_mass_m[0], 2.0 / 3.0)
        self.assertEqual(result.completeness, 1.0)
        self.assertGreater(result.inertia_tensor_kg_m2[1][1], result.inertia_tensor_kg_m2[0][0])

    def test_measured_override_uncertainty_and_unknown_completeness(self):
        result = mass_properties(
            {"measured": Box((1, 1, 1)), "unknown": Box((1, 1, 1))},
            {},
            {"measured": {"value": 500, "unit": "g", "uncertainty": {"value": 2, "unit": "g"}}},
        )
        self.assertEqual(result.mass_status, "partial")
        self.assertAlmostEqual(result.mass_kg, 0.5)
        self.assertAlmostEqual(result.uncertainty_kg, 0.002)
        self.assertAlmostEqual(result.completeness, 0.5)
        self.assertEqual(result.objects[1].mass_status, "unknown")

    def test_open_mesh_cannot_produce_calculated_mass(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        result = mass_properties({"plate": mesh}, {"plate": 1000})
        self.assertEqual(result.mass_status, "unknown")
        self.assertEqual(result.completeness, 0.0)
        self.assertTrue(any("closed_geometry_required" in item for item in result.objects[0].diagnostics))

    def test_analytic_geometry_reference_values(self):
        box = Box((2.0, 3.0, 4.0), transform={"translation": (1.0, 1.5, 2.0)})
        result = mass_properties({"case": box}, {"case": 1.0})
        item = result.objects[0]
        self.assertAlmostEqual(item.mass_kg, 24.0)
        self.assertAlmostEqual(item.volume_m3, 24.0)
        self.assertEqual(item.center_of_mass_m, (1.0, 1.5, 2.0))
        tensor = item.inertia_tensor_kg_m2
        self.assertAlmostEqual(tensor[0][0], 24.0 * 25.0 / 12.0)
        self.assertAlmostEqual(tensor[1][1], 24.0 * 20.0 / 12.0)
        self.assertAlmostEqual(tensor[2][2], 24.0 * 13.0 / 12.0)


class ClassificationTests(unittest.TestCase):
    def test_classification_is_conservative_for_fused_objects(self):
        result = classify_objects({"fused": {"geometry": Box((1, 1, 1)), "fused": True}})
        item = result["fused"]
        self.assertTrue(item.unresolved)
        self.assertTrue(item.fused)
        self.assertFalse(item.semantic_separation_claimed)
        self.assertTrue(any("semantic separation" in reason for reason in item.reasons))

    def test_open_mesh_is_unresolved_surface(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        item = classify_objects([{"id": "surface", "geometry": mesh}])[0]
        self.assertEqual(item.component_type, "surface")
        self.assertTrue(item.unresolved)
        self.assertFalse(item.semantic_separation_claimed)


class RobustnessTests(unittest.TestCase):
    def test_objects_mapping_of_records_is_unwrapped(self):
        document = {"objects": {"a": {"geometry": Box((1, 1, 1)), "material": "ABS"}}}
        result = mass_properties(document, {"a": 1000})
        self.assertEqual(result.mass_status, "calculated")
        self.assertAlmostEqual(result.mass_kg, 1000.0)

    def test_direct_mapping_of_records_is_unwrapped(self):
        document = {"a": {"geometry": Box((1, 1, 1)), "material": "ABS"}}
        result = mass_properties(document, {"a": 1000})
        self.assertEqual(result.mass_status, "calculated")
        self.assertAlmostEqual(result.mass_kg, 1000.0)

    def test_degenerate_zero_volume_mesh_is_unknown(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1, 2)])
        result = mass_properties({"plate": mesh}, {"plate": 1000})
        self.assertEqual(result.mass_status, "unknown")
        self.assertIsNone(result.mass_kg)
        self.assertEqual(result.completeness, 0.0)
        self.assertTrue(any("zero_signed_volume" in item for item in result.objects[0].diagnostics))

    def test_open_mesh_with_measured_override_is_never_complete(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        result = mass_properties({"plate": mesh}, {}, {"plate": {"value": 0.5, "unit": "kg"}})
        self.assertEqual(result.objects[0].mass_status, "measured")
        self.assertEqual(result.objects[0].completeness, 0.5)
        self.assertIsNone(result.objects[0].inertia_tensor_kg_m2)

    def test_nan_density_is_unknown_not_crash(self):
        result = mass_properties({"a": Box((1, 1, 1))}, {"a": float("nan")})
        self.assertEqual(result.mass_status, "unknown")
        self.assertIsNone(result.mass_kg)
        self.assertTrue(any("density_unknown" in item for item in result.objects[0].diagnostics))

    def test_empty_document_returns_unknown(self):
        result = mass_properties({}, {})
        self.assertEqual(result.mass_status, "unknown")
        self.assertIsNone(result.mass_kg)
        self.assertIsNone(result.center_of_mass_m)
        self.assertEqual(result.completeness, 0.0)

    def test_totals_are_finite_when_some_objects_unknown(self):
        result = mass_properties(
            {"known": Box((1, 1, 1)), "unknown": Box((2, 2, 2))},
            {"known": 1000},
        )
        self.assertEqual(result.mass_status, "partial")
        self.assertAlmostEqual(result.mass_kg, 1000.0)
        self.assertTrue(math.isfinite(result.mass_kg))


if __name__ == "__main__":
    unittest.main()
