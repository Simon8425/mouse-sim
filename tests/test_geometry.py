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

        faceted_step = (
            b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
            b"#1=CARTESIAN_POINT('P0',(0.,0.,0.));\n"
            b"#2=CARTESIAN_POINT('P1',(10.,0.,0.));\n"
            b"#3=CARTESIAN_POINT('P2',(0.,10.,0.));\n"
            b"#4=POLY_LOOP('L',(#1,#2,#3));\n"
            b"#5=FACE_OUTER_BOUND('B',#4);\n"
            b"#6=FACE_SURFACE('F',(#5),#7,.T.);\n"
            b"#7=PLANE('P',#8);\n"
            b"#8=AXIS2_PLACEMENT_3D('A',#1,#9,#10);\n"
            b"#9=DIRECTION('Z',(0.,0.,1.));\n"
            b"#10=DIRECTION('X',(1.,0.,0.));\n"
            b"#11=CLOSED_SHELL('S',(#6));\n"
            b"#12=MANIFOLD_SOLID_BREP('Solid',#11);\n"
            b"ENDSEC;\nEND-ISO-10303-21;"
        )
        faceted_result = load_geometry(faceted_step, fmt="step")
        self.assertFalse(faceted_result.unsupported)
        self.assertEqual(faceted_result.format, "step")
        self.assertEqual(faceted_result.source_units, "mm")
        self.assertEqual(len(faceted_result.geometry.vertices), 3)
        self.assertEqual(len(faceted_result.geometry.triangles), 1)
        self.assertEqual(faceted_result.geometry.vertices[1], (0.01, 0.0, 0.0))

        advanced_step = (
            b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
            b"#1=ADVANCED_BREP_SHAPE_REPRESENTATION('A',(#2),#3);\n"
            b"#2=B_SPLINE_SURFACE('S',1,1,((#4,#5),(#6,#7)),.UNSPECIFIED.,.F.,.F.,.F.);\n"
            b"#3=GEOMETRIC_REPRESENTATION_CONTEXT(3);\n"
            b"#4=CARTESIAN_POINT('P',(0.,0.,0.));\n"
            b"#5=CARTESIAN_POINT('P',(1.,0.,0.));\n"
            b"#6=CARTESIAN_POINT('P',(0.,1.,0.));\n"
            b"#7=CARTESIAN_POINT('P',(1.,1.,0.));\n"
            b"ENDSEC;\nEND-ISO-10303-21;"
        )
        unsupported = load_geometry(advanced_step, fmt="step")
        self.assertTrue(unsupported.unsupported)
        self.assertIsNone(unsupported.geometry)
        self.assertEqual(unsupported.diagnostic.code, "unsupported_format")

    def test_mesh_derived_values_are_cached_per_instance(self):
        """Repeated diagnostics passes reuse the per-mesh topology and world
        vertex caches; identical inputs must yield identical objects."""
        mesh = cube_mesh()
        self.assertIs(mesh._world_vertices(), mesh._world_vertices())
        self.assertIs(mesh._topology(), mesh._topology())
        self.assertIs(mesh._integrals(), mesh._integrals())
        first = cube_mesh()
        second = cube_mesh()
        self.assertEqual(first._topology(), second._topology())
        self.assertEqual(first._world_vertices(), second._world_vertices())
        self.assertIsNot(first._world_vertices(), second._world_vertices())


class SelfIntersectionIntegrityTests(unittest.TestCase):
    """Adversarial geometry-integrity matrix: invalid geometry must never
    receive a trustworthy mass/structural result."""

    @staticmethod
    def _shift(vertices, triangles, offset, base):
        vertices = list(vertices)
        triangles = list(triangles)
        base = list(base)
        shifted = [(x + offset[0], y + offset[1], z + offset[2]) for x, y, z in vertices]
        return base + shifted, triangles + [
            (i + len(base), j + len(base), k + len(base)) for i, j, k in triangles
        ]

    def _prism(self, sides=8, twist_deg=0.0):
        vertices = []
        for k in range(sides):
            angle = 2.0 * math.pi * k / sides
            vertices.append((math.cos(angle), math.sin(angle), -1.0))
        for k in range(sides):
            angle = 2.0 * math.pi * k / sides + math.radians(twist_deg) * k / (sides - 1)
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
        return vertices, triangles

    def test_valid_manifold_is_clean(self):
        mesh = cube_mesh()
        self.assertEqual(mesh.diagnostics().issues, ())
        self.assertTrue(mesh.diagnostics().safe_for_mass_properties)

    def test_normal_shared_edges_are_clean(self):
        mesh = cube_mesh()
        self.assertFalse(mesh.diagnostics().closed is False)
        self.assertEqual(mesh.diagnostics().issues, ())

    def test_fan_sharing_a_center_vertex_is_clean(self):
        vertices = [
            (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 1.7, 0.0),
            (0.0, 2.0, 0.0), (-1.0, 1.7, 0.0), (-2.0, 0.0, 0.0),
        ]
        triangles = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]
        mesh = TriangleMesh(vertices, triangles)
        self.assertNotIn("self_intersecting", mesh.diagnostics().issues)

    def test_crossing_triangles_sharing_a_vertex_are_flagged(self):
        vertices = [
            (0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (0.0, 4.0, 0.0),
            (3.0, 1.0, 1.0), (-1.0, 1.0, -1.0),
        ]
        triangles = [(0, 1, 2), (0, 3, 4)]
        mesh = TriangleMesh(vertices, triangles)
        self.assertIn("self_intersecting", mesh.diagnostics().issues)

    def test_crossing_triangles_sharing_an_edge_fold_is_flagged(self):
        # Two triangles sharing edge AB where edge AD pierces the other's
        # interior away from the shared edge (a genuine fold).
        vertices = [
            (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 1.0, 1.0),
            (1.0, -1.0, -1.0), (-1.0, 1.0, 1.0),
        ]
        triangles = [(0, 1, 2), (3, 4, 1)]
        mesh = TriangleMesh(vertices, triangles)
        self.assertIn("self_intersecting", mesh.diagnostics().issues)

    def test_flat_square_adjacent_pair_is_not_flagged(self):
        vertices = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (2.0, 2.0, 0.0)]
        triangles = [(0, 1, 3), (0, 3, 2)]
        mesh = TriangleMesh(vertices, triangles)
        self.assertNotIn("self_intersecting", mesh.diagnostics().issues)

    def test_twisted_prism_is_flagged_and_mass_is_blocked(self):
        straight_vertices, straight_triangles = self._prism(8, 0.0)
        twisted_vertices, twisted_triangles = self._prism(8, 90.0)
        straight = TriangleMesh(straight_vertices, straight_triangles)
        twisted = TriangleMesh(twisted_vertices, twisted_triangles)
        self.assertEqual(straight.diagnostics().issues, ())
        self.assertTrue(straight.diagnostics().safe_for_mass_properties)
        self.assertIn("self_intersecting", twisted.diagnostics().issues)
        self.assertFalse(twisted.diagnostics().safe_for_mass_properties)
        # The twisted (invalid) shell must never certify mass.
        with self.assertRaises(ValueError):
            twisted.inertia_tensor(1000.0)

    def test_interpenetrating_face_aligned_boxes_are_flagged(self):
        # Face-aligned overlap: every contact line coincides with an edge and
        # no vertex lands strictly inside the other solid — the historical
        # blind spot for the pair sweep and vertex containment.
        base_vertices, base_triangles = cube_mesh().vertices, cube_mesh().triangles
        vertices, triangles = self._shift(base_vertices, base_triangles, (0.5, 0.0, 0.0), list(base_vertices))
        mesh = TriangleMesh(vertices, triangles)
        self.assertIn("self_intersecting", mesh.diagnostics().issues)
        self.assertFalse(mesh.diagnostics().safe_for_mass_properties)

    def test_touching_unions_are_not_flagged(self):
        base_vertices, base_triangles = cube_mesh().vertices, cube_mesh().triangles
        base_vertices = list(base_vertices)
        base_triangles = list(base_triangles)
        for offset, label in (
            ((2.0, 0.0, 0.0), "face-face"),
            ((2.0, 2.0, 0.0), "edge-edge"),
            ((2.0, 2.0, 2.0), "corner-corner"),
        ):
            vertices, triangles = self._shift(base_vertices, base_triangles, offset, base_vertices)
            mesh = TriangleMesh(vertices, triangles)
            issues = mesh.diagnostics().issues
            self.assertNotIn("self_intersecting", issues, label)
            self.assertTrue(mesh.diagnostics().safe_for_mass_properties, label)

    def test_duplicate_faces_are_flagged(self):
        for duplicate in (cube_mesh().triangles[0], tuple(reversed(cube_mesh().triangles[0]))):
            mesh = TriangleMesh(cube_mesh().vertices, list(cube_mesh().triangles) + [duplicate])
            self.assertIn("duplicate_faces", mesh.diagnostics().issues)
            self.assertFalse(mesh.diagnostics().safe_for_mass_properties)

    def test_nested_geometry_is_not_safe(self):
        base_vertices, base_triangles = cube_mesh().vertices, cube_mesh().triangles
        inner_vertices = [(0.5 * x, 0.5 * y, 0.5 * z) for x, y, z in base_vertices]
        vertices = list(base_vertices) + inner_vertices
        triangles = list(base_triangles) + [(i + 8, j + 8, k + 8) for i, j, k in base_triangles]
        mesh = TriangleMesh(vertices, triangles)
        issues = mesh.diagnostics().issues
        self.assertIn("nested_shells", issues)
        self.assertFalse(mesh.diagnostics().safe_for_mass_properties)

    def test_coplanar_x_overlap_is_flagged(self):
        vertices = [
            (0.0, 0.0, 0.0), (2.0, 1.0, 0.0), (1.0, 2.0, 0.0),
            (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (2.0, 2.0, 0.0),
        ]
        triangles = [(0, 1, 2), (3, 4, 5)]
        mesh = TriangleMesh(vertices, triangles)
        self.assertIn("self_intersecting", mesh.diagnostics().issues)

    def test_coplanar_corner_touching_is_not_flagged(self):
        vertices = [
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (1.0, 2.0, 0.0),
        ]
        triangles = [(0, 1, 2), (3, 4, 5)]
        mesh = TriangleMesh(vertices, triangles)
        self.assertNotIn("self_intersecting", mesh.diagnostics().issues)

    def test_coplanar_containment_is_winding_independent(self):
        # A triangle strictly contained in a larger coplanar triangle is a
        # genuine overlap regardless of the larger triangle's winding.
        contained = [(4, 1, 0), (6, 1, 0), (5, 2, 0)]
        for big in ([(0, 0, 0), (10, 0, 0), (0, 10, 0)],
                    [(0, 0, 0), (0, 10, 0), (10, 0, 0)]):
            mesh = TriangleMesh(big + contained, [(0, 1, 2), (3, 4, 5)])
            self.assertIn("self_intersecting", mesh.diagnostics().issues)

    def test_near_touching_unions_are_not_flagged(self):
        # Epsilon-gap unions and PARTIAL face-to-face contact (an edge of one
        # box lying on the other's face diagonal) are valid touching
        # geometry — the far-diagonal acceptance must not fire on them.
        base_vertices, base_triangles = cube_mesh().vertices, cube_mesh().triangles
        base_vertices = list(base_vertices)
        base_triangles = list(base_triangles)
        for gap in (1e-10, 1e-12, 1e-8):
            vertices, triangles = self._shift(base_vertices, base_triangles, (2.0 + gap, 0.0, 0.0), base_vertices)
            mesh = TriangleMesh(vertices, triangles)
            self.assertNotIn("self_intersecting", mesh.diagnostics().issues, "gap {}".format(gap))
        for offset in ((2.0, 0.0, 1.0), (2.0, 0.0, 0.5), (2.0, 0.5, 0.5)):
            vertices, triangles = self._shift(base_vertices, base_triangles, offset, base_vertices)
            mesh = TriangleMesh(vertices, triangles)
            issues = mesh.diagnostics().issues
            self.assertNotIn("self_intersecting", issues, str(offset))
            self.assertTrue(mesh.diagnostics().safe_for_mass_properties, str(offset))


if __name__ == "__main__":
    unittest.main()
