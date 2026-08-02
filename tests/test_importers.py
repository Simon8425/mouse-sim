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

    def test_step_file_rejected_with_structured_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sample.step")
            with open(path, "wb") as stream:
                stream.write(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;")
            result = load_geometry(path)
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, "unsupported_format")
        self.assertEqual(result.diagnostic.severity, "blocker")
        self.assertEqual(result.errors, (result.diagnostic,))

    def test_obj_requires_explicit_units(self):
        with self.assertRaises(UnitError):
            load_geometry(self.OBJ, fmt="obj")


if __name__ == "__main__":
    unittest.main()
