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
    mass_properties,
)
from mouse_sim.importers import GeometryLoadResult, repair_open_mesh

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

PLATE_WITH_HOLE_FACES = (
    # 10 x 10 x 2 mm plate with a 4 x 4 mm through hole.  Each face is a
    # tuple of loops: the first loop is the outer bound, the remaining loops
    # are inner bounds (holes).
    (
        ((0.0, 0.0, 2.0), (10.0, 0.0, 2.0), (10.0, 10.0, 2.0), (0.0, 10.0, 2.0)),
        ((3.0, 3.0, 2.0), (7.0, 3.0, 2.0), (7.0, 7.0, 2.0), (3.0, 7.0, 2.0)),
    ),
    (
        ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)),
        ((3.0, 3.0, 0.0), (7.0, 3.0, 0.0), (7.0, 7.0, 0.0), (3.0, 7.0, 0.0)),
    ),
    (((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 2.0), (0.0, 0.0, 2.0)),),
    (((10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (10.0, 10.0, 2.0), (10.0, 0.0, 2.0)),),
    (((10.0, 10.0, 0.0), (0.0, 10.0, 0.0), (0.0, 10.0, 2.0), (10.0, 10.0, 2.0)),),
    (((0.0, 10.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 2.0), (0.0, 10.0, 2.0)),),
    (((3.0, 3.0, 0.0), (7.0, 3.0, 0.0), (7.0, 3.0, 2.0), (3.0, 3.0, 2.0)),),
    (((7.0, 3.0, 0.0), (7.0, 7.0, 0.0), (7.0, 7.0, 2.0), (7.0, 3.0, 2.0)),),
    (((7.0, 7.0, 0.0), (3.0, 7.0, 0.0), (3.0, 7.0, 2.0), (7.0, 7.0, 2.0)),),
    (((3.0, 7.0, 0.0), (3.0, 3.0, 0.0), (3.0, 3.0, 2.0), (3.0, 7.0, 2.0)),),
)


def _box_faces(size, z0=0.0):
    """Six quad faces of a box of side ``size`` starting at height ``z0``."""
    z1 = z0 + size
    return (
        (((0.0, 0.0, z1), (size, 0.0, z1), (size, size, z1), (0.0, size, z1)),),
        (((0.0, 0.0, z0), (size, 0.0, z0), (size, size, z0), (0.0, size, z0)),),
        (((0.0, 0.0, z0), (size, 0.0, z0), (size, 0.0, z1), (0.0, 0.0, z1)),),
        (((size, 0.0, z0), (size, size, z0), (size, size, z1), (size, 0.0, z1)),),
        (((size, size, z0), (0.0, size, z0), (0.0, size, z1), (size, size, z1)),),
        (((0.0, size, z0), (0.0, 0.0, z0), (0.0, 0.0, z1), (0.0, size, z1)),),
    )


def _faceted_step_text(shell_faces, void_shells=()):
    """Build a minimal faceted STEP file from face loop descriptions.

    ``shell_faces`` is a sequence of faces; each face is a sequence of loops;
    each loop is a sequence of ``(x, y, z)`` points.  A face's first loop is
    its outer bound; the remaining loops are inner bounds (holes).
    ``void_shells`` is a sequence of extra closed shells subtracted via
    BREP_WITH_VOIDS.  The body is a MANIFOLD_SOLID_BREP (or BREP_WITH_VOIDS
    when void shells are given) with millimetre units, matching the style of
    the fixtures in ``tests/fixtures``.
    """
    lines = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('generated faceted solid'),'2;1');",
        "FILE_NAME('inline_faceted.step','2026-01-01T00:00:00',('Author'),(''),'','','');",
        "FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));",
        "ENDSEC;",
        "DATA;",
    ]
    state = {"next": 1}
    point_ids = {}
    vertex_ids = {}
    edge_ids = {}

    def point_id(point):
        if point not in point_ids:
            current = state["next"]
            state["next"] += 1
            point_ids[point] = current
            lines.append("#{}=CARTESIAN_POINT('P{}',({},{},{}));".format(current, current, *point))
        return point_ids[point]

    def vertex_id(point):
        if point not in vertex_ids:
            current = state["next"]
            state["next"] += 1
            vertex_ids[point] = current
            lines.append("#{}=VERTEX_POINT('V{}',#{});".format(current, current, point_id(point)))
        return vertex_ids[point]

    def edge_id(edge):
        if edge not in edge_ids:
            current = state["next"]
            state["next"] += 1
            edge_ids[edge] = current
            lines.append(
                "#{}=EDGE_CURVE('E{}',#{},#{},$,.T.);".format(
                    current, current, vertex_id(edge[0]), vertex_id(edge[1])
                )
            )
        return edge_ids[edge]

    def loop_id(points):
        current = state["next"]
        state["next"] += 1
        refs = []
        for index, point in enumerate(points):
            oriented = state["next"]
            state["next"] += 1
            lines.append(
                "#{}=ORIENTED_EDGE('O{}',*,*,#{},.T.);".format(
                    oriented, oriented, edge_id((point, points[(index + 1) % len(points)]))
                )
            )
            refs.append("#{}".format(oriented))
        lines.append("#{}=EDGE_LOOP('L{}',({}));".format(current, current, ",".join(refs)))
        return current

    def face_id(loops):
        bounds = []
        for index, loop_points in enumerate(loops):
            bound = state["next"]
            state["next"] += 1
            loop = loop_id(loop_points)
            if index == 0:
                lines.append("#{}=FACE_OUTER_BOUND('B{}',#{});".format(bound, bound, loop))
            else:
                lines.append("#{}=FACE_BOUND('B{}',#{},.T.);".format(bound, bound, loop))
            bounds.append("#{}".format(bound))
        current = state["next"]
        state["next"] += 1
        lines.append("#{}=ADVANCED_FACE('F{}',({}),$,.T.);".format(current, current, ",".join(bounds)))
        return current

    def shell_id(faces):
        current = state["next"]
        state["next"] += 1
        face_refs = ["#{}".format(face_id(loops)) for loops in faces]
        lines.append("#{}=CLOSED_SHELL('S{}',({}));".format(current, current, ",".join(face_refs)))
        return current

    outer = shell_id(shell_faces)
    if void_shells:
        void_refs = ["#{}".format(shell_id(faces)) for faces in void_shells]
        current = state["next"]
        state["next"] += 1
        lines.append("#{}=BREP_WITH_VOIDS('WithVoids',#{},({}));".format(current, outer, ",".join(void_refs)))
    else:
        current = state["next"]
        state["next"] += 1
        lines.append("#{}=MANIFOLD_SOLID_BREP('Solid',#{});".format(current, outer))
    base = state["next"]
    lines.append("#{}=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));".format(base))
    lines.append("#{}=(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.));".format(base + 1))
    lines.append("#{}=(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT());".format(base + 2))
    lines.append(
        "#{}=UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),#{},'distance_accuracy_value','confusion accuracy');".format(
            base + 3, base
        )
    )
    lines.append(
        "#{}=GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{}))GLOBAL_UNIT_ASSIGNED_CONTEXT((#{},#{},#{}));".format(
            base + 4, base + 3, base, base + 1, base + 2
        )
    )
    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")
    return "\n".join(lines)


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

    def test_binary_stl_two_thousand_triangles_parse_completes(self):
        count = 2000
        binary = b" " * 80 + struct.pack("<I", count)
        for index in range(count):
            base = index * 3
            binary += struct.pack(
                "<12f",
                0.0, 0.0, 1.0,
                float(base), 0.0, 0.0,
                float(base + 1), 0.0, 0.0,
                float(base + 1), float(base + 2), 0.0,
            ) + struct.pack("<H", 0)
        result = load_geometry(binary, fmt="stl", units="mm")
        self.assertEqual(len(result.geometry.triangles), count)
        self.assertEqual(len(result.geometry.vertices), count * 3)
        self.assertEqual(result.geometry.vertices[0], (0.0, 0.0, 0.0))

    def test_stl_dedup_preserves_first_seen_vertex_order(self):
        binary = b" " * 80 + struct.pack("<I", 2)
        binary += struct.pack("<12f", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<H", 0)
        binary += struct.pack("<12f", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<H", 0)
        result = load_geometry(binary, fmt="stl", units="mm")
        self.assertEqual(len(result.geometry.vertices), 3)
        self.assertEqual(result.geometry.triangles, ((0, 1, 2), (0, 1, 2)))


    def test_obj_requires_explicit_units(self):
        with self.assertRaises(UnitError):
            load_geometry(self.OBJ, fmt="obj")


class MeshRepairTests(unittest.TestCase):
    CUBE_VERTICES = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    CUBE_TRIANGLES = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]

    def _unwelded_cube(self):
        """A cube where every triangle carries its own vertex copies (the
        classic STL export shape: 36 vertices, open topology)."""
        vertices = []
        triangles = []
        for triangle in self.CUBE_TRIANGLES:
            start = len(vertices)
            vertices.extend(self.CUBE_VERTICES[index] for index in triangle)
            triangles.append((start, start + 1, start + 2))
        return TriangleMesh(vertices, triangles)

    def test_weld_repair_stitches_open_seams_into_closed_mesh(self):
        mesh = self._unwelded_cube()
        self.assertFalse(mesh.diagnostics().safe_for_mass_properties)
        repaired, diagnostics = repair_open_mesh(mesh)
        self.assertIsNot(repaired, mesh)
        self.assertTrue(repaired.diagnostics().safe_for_mass_properties)
        self.assertEqual(len(repaired.vertices), 8)
        self.assertEqual(len(repaired.triangles), 12)
        self.assertAlmostEqual(repaired.volume(), 8.0)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].code, "mesh_weld_repair")
        self.assertEqual(diagnostics[0].severity, "info")

    def test_repair_leaves_closed_and_unweldable_meshes_untouched(self):
        closed = TriangleMesh(self.CUBE_VERTICES, self.CUBE_TRIANGLES)
        repaired, diagnostics = repair_open_mesh(closed)
        self.assertIs(repaired, closed)
        self.assertEqual(diagnostics, ())

        flat = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        repaired, diagnostics = repair_open_mesh(flat)
        self.assertIs(repaired, flat)
        self.assertEqual(diagnostics, ())

    def test_repair_only_accepted_when_topology_certifies_mass(self):
        # A genuinely open single-sided surface cannot be stitched: welding
        # changes nothing, so no repair is claimed.
        vertices = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 0.5, 1)]
        triangles = [(4, 1, 2), (4, 2, 3), (4, 3, 0), (4, 0, 1)]
        mesh = TriangleMesh(vertices, triangles)
        repaired, diagnostics = repair_open_mesh(mesh)
        self.assertIs(repaired, mesh)
        self.assertEqual(diagnostics, ())

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

    def test_faceted_step_inner_bounds_block_mass_certification(self):
        path = os.path.join(FIXTURES, "square_with_hole.step")
        result = load_geometry(path, step_backend="stdlib")
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, "step_topology_unsupported")
        self.assertEqual(result.diagnostic.severity, "blocker")
        self.assertIn("inner bounds (holes)", result.diagnostic.message)
        self.assertIn("imported volume may be overestimated", result.diagnostic.message)
        self.assertIn("mass is not certified", result.diagnostic.message)
        details = dict(result.diagnostic.details)
        self.assertEqual(details.get("kind"), "holes")
        self.assertEqual(result.errors, (result.diagnostic,))
        mass = mass_properties({"plate": result}, {"plate": 1000})
        self.assertEqual(mass.mass_status, "unknown")
        self.assertIsNone(mass.mass_kg)
        self.assertIsNone(mass.objects[0].volume_m3)
        self.assertTrue(
            any(
                "import_diagnostic:step_topology_unsupported" in item
                for item in mass.objects[0].diagnostics
            )
        )

    def test_faceted_step_plate_with_hole_blocks_mass_certification(self):
        text = _faceted_step_text(PLATE_WITH_HOLE_FACES)
        result = load_geometry(text.encode("utf-8"), step_backend="stdlib")
        self.assertIsInstance(result, GeometryLoadResult)
        self.assertEqual(result.format, "step")
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, "step_topology_unsupported")
        self.assertEqual(result.diagnostic.severity, "blocker")
        self.assertIn("inner bounds (holes)", result.diagnostic.message)
        self.assertIn("imported volume may be overestimated", result.diagnostic.message)
        self.assertIn("mass is not certified", result.diagnostic.message)
        details = dict(result.diagnostic.details)
        self.assertEqual(details.get("kind"), "holes")
        self.assertEqual(result.errors, (result.diagnostic,))
        mass = mass_properties({"plate": result}, {"plate": 1000})
        self.assertEqual(mass.mass_status, "unknown")
        self.assertIsNone(mass.mass_kg)
        self.assertIsNone(mass.objects[0].volume_m3)
        self.assertEqual(mass.completeness, 0.0)
        self.assertTrue(
            any(
                "import_diagnostic:step_topology_unsupported" in item
                for item in mass.objects[0].diagnostics
            )
        )

    def test_faceted_step_brep_with_voids_blocks_mass_certification(self):
        text = _faceted_step_text(_box_faces(10.0), void_shells=(_box_faces(4.0, z0=3.0),))
        result = load_geometry(text.encode("utf-8"), step_backend="stdlib")
        self.assertIsInstance(result, GeometryLoadResult)
        self.assertEqual(result.format, "step")
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, "step_topology_unsupported")
        self.assertEqual(result.diagnostic.severity, "blocker")
        self.assertIn("void shells", result.diagnostic.message)
        self.assertIn("imported volume may be overestimated", result.diagnostic.message)
        self.assertIn("mass is not certified", result.diagnostic.message)
        details = dict(result.diagnostic.details)
        self.assertEqual(details.get("kind"), "voids")
        self.assertEqual(result.errors, (result.diagnostic,))
        mass = mass_properties({"solid": result}, {"solid": 1000})
        self.assertEqual(mass.mass_status, "unknown")
        self.assertIsNone(mass.mass_kg)
        self.assertIsNone(mass.objects[0].volume_m3)
        self.assertTrue(
            any(
                "import_diagnostic:step_topology_unsupported" in item
                for item in mass.objects[0].diagnostics
            )
        )

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

    def test_step_unit_scale_honors_declared_inches(self):
        """An inch-declared STEP must scale by 0.0254, not the mm 0.001.

        The kernel's metre scale comes from the declared STEP length unit; a
        hardcoded 0.001 would shrink an inch model 25.4x (volume x16387,
        mass x16387).
        """
        from mouse_sim.step_kernel import _settings, step_unit_hint

        inch_step = (
            b"ISO-10303-21;\nHEADER;\n"
            b"FILE_DESCRIPTION((''),'2;1');\n"
            b"FILE_NAME('x.stp','',(''),(''),'','','');\n"
            b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
            b"ENDSEC;\nDATA;\n"
            b"#1 = CONVERSION_BASED_UNIT('INCH', #2);\n"
            b"#2 = LENGTH_UNIT() GLOBAL_UNIT_ASSIGNED_CONTEXT(#1);\n"
            b"ENDSEC;\nEND-ISO-10303-21;\n"
        )
        self.assertEqual(step_unit_hint(inch_step), "in")
        self.assertEqual(_settings("in")["scale_to_m"], 0.0254)
        self.assertEqual(_settings("mm")["scale_to_m"], 0.001)
        self.assertEqual(_settings("ft")["scale_to_m"], 0.3048)

if __name__ == "__main__":
    unittest.main()
