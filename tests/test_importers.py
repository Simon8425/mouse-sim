import os
import struct
import tempfile
import unittest

from mouse_sim import (
    Box,
    Compound,
    Cone,
    Cylinder,
    Frustum,
    Sphere,
    TriangleMesh,
    UnitError,
    geometry_from_dict,
    load_geometry,
)
from mouse_sim.importers import GeometryLoadResult

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class GeometryFromDictRoundTripTests(unittest.TestCase):
    def assert_round_trip(self, geometry):
        rebuilt = geometry_from_dict(geometry.to_dict())
        self.assertEqual(rebuilt.to_dict(), geometry.to_dict())
        self.assertAlmostEqual(rebuilt.volume(), geometry.volume())
        self.assertAlmostEqual(rebuilt.centroid()[0], geometry.centroid()[0])
        self.assertAlmostEqual(rebuilt.centroid()[1], geometry.centroid()[1])
        self.assertAlmostEqual(rebuilt.centroid()[2], geometry.centroid()[2])

    def test_analytic_primitives_round_trip(self):
        for geometry in (
            Box((10, 20, 30), units="mm"),
            Sphere(5, units="mm"),
            Cylinder(10, 20, units="mm"),
            Cone(10, 20, units="mm"),
            Frustum(10, 5, 20, units="mm"),
        ):
            self.assert_round_trip(geometry)

    def test_mesh_round_trip(self):
        mesh = TriangleMesh(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            [(0, 1, 2)],
            units="mm",
        )
        rebuilt = geometry_from_dict(mesh.to_dict())
        self.assertEqual(rebuilt.vertices, mesh.vertices)
        self.assertEqual(rebuilt.triangles, mesh.triangles)
        self.assertAlmostEqual(rebuilt.volume(), mesh.volume())

    def test_compound_round_trip(self):
        compound = Compound((Box((10, 10, 10), units="mm"), Sphere(5, units="mm")))
        self.assert_round_trip(compound)


class SampleFileImportTests(unittest.TestCase):
    OBJ = b"v 0 0 0\nv 10 0 0\nv 0 10 0\nf 1 2 3\n"
    STL = (
        b"solid sample\n"
        b"facet normal 0 0 1\n"
        b" outer loop\n"
        b"  vertex 0 0 0\n"
        b"  vertex 10 0 0\n"
        b"  vertex 0 10 0\n"
        b" endloop\n"
        b"endfacet\n"
        b"endsolid sample\n"
    )

    def test_obj_sample_file_parses_with_mm_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sample.obj")
            with open(path, "wb") as stream:
                stream.write(self.OBJ)
            result = load_geometry(path, units="mm")
        self.assertIsInstance(result, GeometryLoadResult)
        self.assertEqual(result.format, "obj")
        self.assertEqual(result.source_units, "mm")
        self.assertEqual(result.geometry.vertices[1], (0.01, 0.0, 0.0))

    def test_ascii_stl_sample_file_parses_with_mm_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sample.stl")
            with open(path, "wb") as stream:
                stream.write(self.STL)
            result = load_geometry(path, units="mm")
        self.assertEqual(result.format, "stl")
        self.assertEqual(len(result.geometry.triangles), 1)
        self.assertEqual(result.geometry.vertices[1], (0.01, 0.0, 0.0))

    def test_binary_stl_sample_parses(self):
        binary = b" " * 80 + struct.pack("<I", 1)
        binary += struct.pack("<12f", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<H", 0)
        result = load_geometry(binary, fmt="stl", units="mm")
        self.assertEqual(result.geometry.vertices[1], (0.001, 0.0, 0.0))

    def test_faceted_step_parses(self):
        path = os.path.join(FIXTURES, "faceted_cube.step")
        result = load_geometry(path)
        self.assertIsInstance(result, GeometryLoadResult)
        self.assertEqual(result.format, "step")
        self.assertFalse(result.unsupported)
        self.assertIsInstance(result.geometry, TriangleMesh)
        self.assertEqual(len(result.geometry.vertices), 8)
        self.assertEqual(len(result.geometry.triangles), 12)
        self.assertEqual(result.source_units, "mm")
        self.assertEqual(result.geometry.vertices[2], (0.01, 0.01, 0.0))
        self.assertEqual(result.errors, ())

    def test_faceted_step_with_advanced_faces_parses(self):
        path = os.path.join(FIXTURES, "faceted_cube_advanced_faces.step")
        result = load_geometry(path)
        self.assertIsInstance(result, GeometryLoadResult)
        self.assertEqual(result.format, "step")
        self.assertFalse(result.unsupported)
        self.assertIsInstance(result.geometry, TriangleMesh)
        self.assertEqual(len(result.geometry.vertices), 8)
        self.assertEqual(len(result.geometry.triangles), 12)
        self.assertEqual(result.source_units, "mm")
        self.assertEqual(result.errors, ())

    def test_faceted_step_with_edge_loops_parses(self):
        path = os.path.join(FIXTURES, "faceted_cube_edge_loops.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(len(result.geometry.vertices), 8)
        self.assertEqual(len(result.geometry.triangles), 12)
        self.assertEqual(result.source_units, "mm")
        self.assertTrue(result.geometry.diagnostics().safe_for_mass_properties)
        self.assertEqual(result.errors, ())

    def test_faceted_step_circular_edges_approximated(self):
        path = os.path.join(FIXTURES, "quarter_disk_circle.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(len(result.geometry.vertices), 8)
        self.assertEqual(len(result.geometry.triangles), 6)
        warning = next(
            (item for item in result.diagnostics if item.code == "step_curved_edges_approximated"),
            None,
        )
        self.assertIsNotNone(warning)
        self.assertEqual(warning.severity, "warning")
        self.assertEqual(result.errors, ())

    def test_faceted_step_tessellated_set_parses(self):
        path = os.path.join(FIXTURES, "faceted_cube_tessellated.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(len(result.geometry.vertices), 8)
        self.assertEqual(len(result.geometry.triangles), 12)
        self.assertEqual(result.source_units, "mm")
        self.assertTrue(result.geometry.diagnostics().safe_for_mass_properties)
        self.assertEqual(result.errors, ())

    def test_faceted_step_bspline_edges_approximated(self):
        path = os.path.join(FIXTURES, "quarter_disk_bspline.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(result.source_units, "mm")
        warning = next(
            (item for item in result.diagnostics if item.code == "step_curved_edges_approximated"),
            None,
        )
        self.assertIsNotNone(warning)
        self.assertEqual(warning.severity, "warning")
        self.assertEqual(result.errors, ())
        vertices = result.geometry.vertices
        self.assertGreater(len(vertices), 5)
        self.assertTrue(
            any(
                abs(point[0] - 0.007071) < 0.0005 and abs(point[1] - 0.002929) < 0.0005
                for point in vertices
            ),
            "B-spline arc should pass through the quarter-circle midpoint",
        )

    def test_faceted_step_unsupported_curve_reports_blocker(self):
        path = os.path.join(FIXTURES, "quarter_disk_ellipse.step")
        result = load_geometry(path)
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, "unsupported_step_entities")
        self.assertEqual(result.diagnostic.severity, "blocker")
        details = dict(result.diagnostic.details)
        self.assertIn("ELLIPSE", details.get("entities", ""))
        self.assertEqual(result.errors, (result.diagnostic,))

    def test_faceted_step_degenerate_loop_skipped(self):
        path = os.path.join(FIXTURES, "faceted_cube_degenerate_loop.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(len(result.geometry.triangles), 10)
        warning = next(
            (item for item in result.diagnostics if item.code == "step_degenerate_loop_skipped"),
            None,
        )
        self.assertIsNotNone(warning)
        self.assertEqual(warning.severity, "warning")

    def test_faceted_step_concave_face_triangulated(self):
        path = os.path.join(FIXTURES, "l_shaped_face.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(len(result.geometry.vertices), 6)
        self.assertEqual(len(result.geometry.triangles), 4)
        vertices = result.geometry.vertices
        signs = []
        for (a, b, c) in result.geometry.triangles:
            area = (vertices[b][0] - vertices[a][0]) * (vertices[c][1] - vertices[a][1]) - (
                vertices[b][1] - vertices[a][1]
            ) * (vertices[c][0] - vertices[a][0])
            signs.append(area)
        self.assertTrue(all(sign > 0 for sign in signs) or all(sign < 0 for sign in signs))
        self.assertAlmostEqual(sum(abs(sign) for sign in signs) / 2.0, 75.0e-6)
        self.assertEqual(result.errors, ())

    def test_faceted_step_inner_bounds_skipped(self):
        path = os.path.join(FIXTURES, "square_with_hole.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(len(result.geometry.triangles), 2)
        warning = next(
            (item for item in result.diagnostics if item.code == "step_inner_bounds_skipped"),
            None,
        )
        self.assertIsNotNone(warning)
        self.assertEqual(warning.severity, "warning")
        self.assertEqual(result.errors, ())

    def test_step_units_fallback_warns(self):
        path = os.path.join(FIXTURES, "faceted_cube_no_units.step")
        result = load_geometry(path)
        self.assertFalse(result.unsupported)
        self.assertEqual(result.source_units, "mm")
        self.assertEqual(len(result.geometry.vertices), 8)
        self.assertEqual(len(result.geometry.triangles), 12)
        warning = next(
            (item for item in result.diagnostics if item.code == "step_units_assumed_mm"),
            None,
        )
        self.assertIsNotNone(warning)
        self.assertEqual(warning.severity, "warning")

    def test_advanced_step_rejected_with_structured_diagnostic(self):
        path = os.path.join(FIXTURES, "advanced_brep.step")
        result = load_geometry(path)
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, "unsupported_format")
        self.assertEqual(result.diagnostic.severity, "blocker")
        details = dict(result.diagnostic.details)
        self.assertIn("ADVANCED_BREP_SHAPE_REPRESENTATION", details.get("entities", ""))
        self.assertEqual(result.errors, (result.diagnostic,))

    def test_malformed_step_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sample.step")
            with open(path, "wb") as stream:
                stream.write(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;")
            with self.assertRaises(ValueError):
                load_geometry(path)

    def test_obj_requires_explicit_units(self):
        with self.assertRaises(UnitError):
            load_geometry(self.OBJ, fmt="obj")


if __name__ == "__main__":
    unittest.main()
