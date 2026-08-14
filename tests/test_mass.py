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

    def test_open_three_dimensional_mesh_produces_estimated_mass(self):
        # Cube with the top face removed: open, but spans 3D extent, so the
        # bounding-box envelope estimate yields a disclosed non-zero mass.
        vertices = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        ]
        triangles = [
            (0, 2, 1), (0, 3, 2),
            (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6),
            (3, 0, 4), (3, 4, 7),
        ]
        mesh = TriangleMesh(vertices, triangles)
        result = mass_properties({"shell": mesh}, {"shell": 1000})
        item = result.objects[0]
        self.assertEqual(item.mass_status, "estimated")
        self.assertAlmostEqual(item.mass_kg, 4000.0)
        self.assertAlmostEqual(item.volume_m3, 4.0)
        self.assertEqual(item.center_of_mass_m, (0.0, 0.0, 0.0))
        self.assertIsNotNone(item.inertia_tensor_kg_m2)
        self.assertEqual(item.completeness, 0.5)
        self.assertTrue(any("mass_estimated_from_bounding_box" in d for d in item.diagnostics))
        self.assertEqual(result.mass_status, "estimated")
        self.assertAlmostEqual(result.mass_kg, 4000.0)

    def test_estimated_and_calculated_mix_to_partial_mixed(self):
        vertices = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        ]
        triangles = [
            (0, 2, 1), (0, 3, 2),
            (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6),
            (3, 0, 4), (3, 4, 7),
        ]
        open_mesh = TriangleMesh(vertices, triangles)
        result = mass_properties(
            {"solid": Box((1, 1, 1)), "shell": open_mesh},
            {"solid": 1000, "shell": 1000},
        )
        self.assertEqual(result.mass_status, "mixed")
        self.assertAlmostEqual(result.mass_kg, 5000.0)
        self.assertIsNotNone(result.center_of_mass_m)
        self.assertIsNotNone(result.inertia_tensor_kg_m2)

    def test_large_closed_mesh_with_unverified_self_intersection_is_estimated(self):
        # G3 audit fix: a closed mesh above the self-intersection sweep limit
        # (5000 triangles) is disclosed as self_intersection_unverified — the
        # CAD path must NOT certify it as "calculated"/completeness 1.0, it is
        # demoted to the existing "estimated" vocabulary with completeness 0.5
        # and an explicit flag.  A 1300-sided prism: 4*1300 = 5200 triangles,
        # closed, consistent winding, no other integrity issue.
        sides = 1300
        vertices = []
        for k in range(sides):
            angle = 2.0 * math.pi * k / sides
            vertices.append((math.cos(angle), math.sin(angle), -1.0))
        for k in range(sides):
            angle = 2.0 * math.pi * k / sides
            vertices.append((math.cos(angle), math.sin(angle), 1.0))
        vertices.append((0.0, 0.0, -1.0))
        vertices.append((0.0, 0.0, 1.0))
        triangles = []
        for k in range(sides):
            triangles.append((2 * sides, (k + 1) % sides, k))
            triangles.append((k + sides, (k + 1) % sides + sides, 2 * sides + 1))
        for k in range(sides):
            a, b = k, (k + 1) % sides
            triangles.append((a, b, b + sides))
            triangles.append((a, b + sides, a + sides))
        mesh = TriangleMesh(vertices, triangles)
        self.assertGreater(len(mesh.triangles), 5000)
        diagnostics = mesh.diagnostics()
        self.assertTrue(diagnostics.closed)
        self.assertTrue(diagnostics.safe_for_mass_properties)
        self.assertEqual(diagnostics.issues, ("self_intersection_unverified",))
        result = mass_properties({"prism": mesh}, {"prism": 1000})
        item = result.objects[0]
        self.assertEqual(item.mass_status, "estimated")
        self.assertEqual(item.completeness, 0.5)
        self.assertLess(item.completeness, 1.0)
        self.assertIn("self_intersection_unverified", item.diagnostics)
        self.assertIn("mass_estimated_self_intersection_unverified", item.diagnostics)
        # The mass value is still computed and disclosed, never faked.  The
        # expected value uses the exact triangulated n-gon volume (the flat
        # fan area n/2*sin(2*pi/n) differs from pi by the tessellation error).
        self.assertIsNotNone(item.mass_kg)
        polygon_area = sides / 2.0 * math.sin(2.0 * math.pi / sides)
        self.assertAlmostEqual(item.mass_kg, 1000.0 * 2.0 * polygon_area, places=6)
        self.assertEqual(result.mass_status, "estimated")
        # Aggregate completeness is the fraction of objects with per-object
        # completeness >= 1.0 (the same convention as the envelope-estimate
        # path): a demoted object counts as incomplete, so the aggregate is
        # below 1.0.
        self.assertLess(result.completeness, 1.0)

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

    def test_zero_mass_override_is_rejected_and_falls_back_to_cad_mass(self):
        # A measured override of exactly 0.0 kg must be rejected like a
        # negative one (it previously passed the mass >= 0 gate and crashed
        # the CoM aggregation with 1.0 / total_mass = ZeroDivisionError).
        # Rejection routes to the CAD density path, never a crash.
        for override in (0.0, {"value": 0, "unit": "kg"}, -1.0):
            result = mass_properties(
                {"a": Box((1, 1, 1))},
                {"a": 1000},
                {"a": override},
            )
            self.assertEqual(result.mass_status, "calculated")
            self.assertAlmostEqual(result.mass_kg, 1000.0)
            self.assertTrue(
                any(
                    "invalid_mass_override: measured mass must be positive" in item
                    for item in result.objects[0].diagnostics
                )
            )
            self.assertIsNotNone(result.inertia_tensor_kg_m2)

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
