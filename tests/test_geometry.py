import json
import math
import struct
import unittest

from mouse_sim import (
    Box,
    Compound,
    Cone,
    Cylinder,
    Frustum,
    Transform,
    TriangleMesh,
    UnitError,
    closed_mesh_diagnostics,
    geometry_from_dict,
    load_geometry,
)


def cube_mesh(units="m"):
    vertices = [
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ]
    triangles = [
        (0, 2, 1), (0, 3, 2),
        (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7),
    ]
    return TriangleMesh(vertices, triangles, units=units)


class PrimitiveTests(unittest.TestCase):
    def test_units_and_exact_primitive_formulas(self):
        box = Box((10, 20, 30), units="mm")
        self.assertAlmostEqual(box.volume(), 0.000006)
        self.assertAlmostEqual(box.surface_area(), 0.0022)
        self.assertAlmostEqual(box.inertia_tensor(1000)[0][0], 0.00000065)

        cylinder = Cylinder(10, 20, units="mm")
        self.assertAlmostEqual(cylinder.volume(), math.pi * 0.01 ** 2 * 0.02)
        cone = Cone(10, 20, units="mm")
        self.assertAlmostEqual(cone.centroid()[2], 0.005)
        frustum = Frustum(10, 5, 20, units="mm")
        self.assertAlmostEqual(frustum.centroid()[2], 0.02 * 0.39285714285714285, places=12)

    def test_rigid_transform_and_bounds(self):
        rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        transform = Transform(rotation, (1.0, 2.0, 3.0))
        point = (0.25, -0.5, 2.0)
        self.assertEqual(transform.inverse().apply_point(transform.apply_point(point)), point)
        bounds = Box((2.0, 4.0, 6.0), transform=transform).bounds()
        self.assertEqual(bounds.min_point, (-1.0, 1.0, 0.0))
        self.assertEqual(bounds.max_point, (3.0, 3.0, 6.0))

    def test_analytic_json_and_compound(self):
        geometry = geometry_from_dict({
            "type": "compound",
            "units": "mm",
            "children": [
                {"type": "box", "size": [10, 10, 10]},
                {"type": "sphere", "radius": 5},
            ],
        })
        self.assertIsInstance(geometry, Compound)
        self.assertGreater(geometry.volume(), 0.000001)


    def test_frustum_inertia_matches_cone_and_cylinder(self):
        cone = Cone(2.0, 4.0)
        frustum = Frustum(2.0, 0.0, 4.0)
        cone_props = cone.mass_properties(1.0)
        frustum_props = frustum.mass_properties(1.0)
        self.assertAlmostEqual(frustum_props["inertia_tensor_kg_m2"][0][0],
                               cone_props["inertia_tensor_kg_m2"][0][0], places=9)
        self.assertAlmostEqual(frustum_props["inertia_tensor_kg_m2"][2][2],
                               cone_props["inertia_tensor_kg_m2"][2][2], places=9)
        cylinder = Cylinder(1.0, 2.0)
        straight = Frustum(1.0, 1.0, 2.0)
        cylinder_props = cylinder.mass_properties(1.0)
        straight_props = straight.mass_properties(1.0)
        self.assertAlmostEqual(straight_props["inertia_tensor_kg_m2"][0][0],
                               cylinder_props["inertia_tensor_kg_m2"][0][0], places=9)
        self.assertAlmostEqual(straight_props["inertia_tensor_kg_m2"][2][2],
                               cylinder_props["inertia_tensor_kg_m2"][2][2], places=9)

    def test_compound_parallel_axis_hand_calculation(self):
        first = Box((2.0, 2.0, 2.0))
        second = Box((2.0, 2.0, 2.0), transform={"translation": (4.0, 0.0, 0.0)})
        compound = Compound((first, second))
        props = compound.mass_properties(1.0)
        self.assertEqual(props["centroid_m"], (2.0, 0.0, 0.0))
        local = 8.0 * 8.0 / 12.0
        self.assertAlmostEqual(props["inertia_tensor_kg_m2"][0][0], 2.0 * local)
        self.assertAlmostEqual(props["inertia_tensor_kg_m2"][1][1], 2.0 * (local + 8.0 * 2.0 * 2.0))
        self.assertAlmostEqual(props["inertia_tensor_kg_m2"][2][2], 2.0 * (local + 8.0 * 2.0 * 2.0))


class MeshAndImportTests(unittest.TestCase):
    def test_closed_mesh_signed_properties_and_open_diagnostic(self):
        mesh = cube_mesh()
        diagnostics = closed_mesh_diagnostics(mesh)
        self.assertTrue(diagnostics.closed)
        self.assertTrue(diagnostics.safe_for_mass_properties)
        self.assertAlmostEqual(diagnostics.signed_volume_m3, 8.0)
        self.assertEqual(diagnostics.centroid_m, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(mesh.inertia_tensor(1.0)[0][0], 16.0 / 3.0)

        open_mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        self.assertFalse(open_mesh.diagnostics().closed)
        with self.assertRaises(ValueError):
            open_mesh.inertia_tensor(1.0)

    def test_json_obj_ascii_binary_and_step_imports(self):
        json_result = load_geometry(json.dumps({
            "type": "box",
            "size": [10, 20, 30],
            "units": "mm",
            "review_status": "approved",
            "derived_from": "source-1",
        }).encode("utf-8"), fmt="json")
        self.assertAlmostEqual(json_result.geometry.volume(), 0.000006)
        self.assertEqual(json_result.source_units, "mm")
        self.assertEqual(json_result.review_status, "approved")
        self.assertEqual(json_result.derived_status, "derived")

        obj = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        with self.assertRaises(UnitError):
            load_geometry(obj, fmt="obj")
        obj_result = load_geometry(obj, fmt="obj", units="mm")
        self.assertEqual(obj_result.geometry.vertices[1], (0.001, 0.0, 0.0))

        ascii_stl = b"""solid triangle
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid triangle
"""
        ascii_result = load_geometry(ascii_stl, fmt="stl", units="mm")
        self.assertEqual(ascii_result.format, "stl")
        self.assertEqual(len(ascii_result.geometry.triangles), 1)

        binary = b" " * 80 + struct.pack("<I", 1)
        binary += struct.pack("<12f", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<H", 0)
        binary_result = load_geometry(binary, fmt="stl", units="mm")
        self.assertEqual(binary_result.geometry.vertices[1], (0.001, 0.0, 0.0))

        unsupported = load_geometry(b"ISO-10303-21;", fmt="step")
        self.assertTrue(unsupported.unsupported)
        self.assertIsNone(unsupported.geometry)
        self.assertEqual(unsupported.diagnostic.code, "unsupported_format")


if __name__ == "__main__":
    unittest.main()
