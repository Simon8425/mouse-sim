"""Tests for the display-only per-vertex FEA post-processor (mouse_sim/fea.py).

The FEA payload is DISPLAY-ONLY: it must never modify any existing physics
output, must be byte-deterministic, must never raise, and must fail open
(``computed: False`` + flags) on missing inputs.  The per-vertex
stress/damage field is the structural solver's simply-supported plate
bending solution (Navier double series, m,n <= 15) mapped onto the mesh
bounding box and normalized so the field max (at the plate center) equals
the shell peak stress:

    sigma_v(i) = min(sigma_peak, raw(i) * sigma_peak / raw(a/2, b/2))
    D_i        = min(1.0, sigma_v(i) / sigma_yield)

with the impact Gaussian retained for the dent layer only:

    Delta_i    = -n_hat * delta_max * exp(...) * (1 + 2*max(0,(D_i-0.7)/0.3)),
                 magnitude capped at 1.5*delta_max

When the structural response is not a uniform-pressure shell panel, the
per-vertex field falls back to the impact Gaussian (disclosed).
"""

import json
import math
import unittest
from types import SimpleNamespace
from unittest import mock

from mouse_sim import canonical_json
from mouse_sim import fea
from mouse_sim.geometry import Box, Cylinder, Sphere, Transform, TriangleMesh
from mouse_sim.pipeline import run_pipeline
from tests.test_pipeline import mouse_project_request

MESH_VERTICES = [(0.0, 0.0, 0.0), (0.03, 0.0, 0.0), (0.0, 0.03, 0.0), (0.0, 0.0, 0.03)]
MESH_TRIANGLES = [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]


def _tetrahedron(transform=None):
    return TriangleMesh(MESH_VERTICES, MESH_TRIANGLES, units="m", transform=transform)


def _fake_mesh_with_transform(rotation, translation):
    """A mesh-like object carrying an UNVALIDATED transform (the real
    ``Transform`` dataclass rejects non-orthonormal rotations at
    construction; fea must survive them at read time)."""
    return SimpleNamespace(
        transform=SimpleNamespace(rotation=rotation, translation=translation),
        vertices=list(MESH_VERTICES),
        triangles=list(MESH_TRIANGLES),
    )


def _synthetic_result(
    sigma_peak=4e7,
    sf=2.0,
    center=(0.0, 0.0, 0.0),
    normal=(0.0, 0.0, 1.0),
    mass=0.06,
    energy=0.456,
    stiffness=1e5,
    restitution=0.3,
    with_drop=True,
    material_properties=None,
    response_validity=None,
):
    """Synthetic assembled result whose drop-derived estimate is closed-form:
    delta_max = v*sqrt(m/k), v = sqrt(2*E/m)."""
    result = {
        "shell": {
            "peak_stress_pa": sigma_peak,
            "min_safety_factor": sf,
            "critical_region": list(center),
        },
        "structural": {
            "response": {
                "filtered_location": list(center),
                **({"validity": response_validity} if response_validity is not None else {}),
            }
        },
        "drop_simulation": None,
    }
    if material_properties is not None:
        # Mirror the shape of shell_validation.build_shell_trace's
        # material.properties block (SI Pa floats or quantity dicts).
        result["shell"]["inputs_trace"] = {
            "material": {"label": "synthetic", "properties": dict(material_properties)}
        }
    if with_drop:
        speed = math.sqrt(2.0 * energy / mass)
        result["drop_simulation"] = {
            "model": {"mass_kg": mass, "restitution": restitution},
            "peak": {"impact_speed_m_s": round(speed, 4), "kinetic_energy_j": energy},
            # The pipeline-stored estimate inputs (peak_force_estimate):
            # effective mass, energy-CAPPED speed/energy, degraded
            # restitution, resolved contact kwargs (pipeline.py drop
            # section).  fea consumes these, not the raw peak record.
            "peak_force_estimate": {
                "mass_kg": mass,
                "restitution": restitution,
                "energy_j": round(energy, 6),
                "impact_speed_m_s": round(speed, 6),
                "contact_stiffness_n_per_m": stiffness,
            },
            "impacts": [
                {
                    "impact_speed_m_s": round(speed, 4),
                    "contact_location": list(center),
                    "contact_normal": list(normal),
                }
            ],
            "contact_stiffness_n_per_m": stiffness,
        }
    return result


def _expected_compression(mass=0.06, energy=0.456, stiffness=1e5):
    speed = math.sqrt(2.0 * energy / mass)
    return speed * math.sqrt(mass / stiffness)


def _synthetic_plate_result(**kwargs):
    """Synthetic result WITH a uniform-pressure shell-panel structural
    section: the per-vertex field is then the plate bending solution
    (the panel domain a=0.06, b=0.04 matches the default _grid_mesh
    bounding box, so the mapping is the identity stretch)."""
    result = _synthetic_result(**kwargs)
    result["structural"] = {
        "structure": {
            "type": "shell_panel",
            "a_m": 0.06,
            "b_m": 0.04,
            "t_m": 0.002,
        },
        "load_case": {"kind": "pressure", "magnitude_pa": 1000.0},
        # Flat SI material payload (the shape physics._material_props reads
        # from a mapping): ABS-class modulus/ratio.
        "resolved_material": {"young_modulus_pa": 2.3e9, "poissons_ratio": 0.35},
        "response": result["structural"]["response"],
    }
    return result


def _grid_mesh(nx=5, ny=5):
    """A flat 5x5 grid over [-0.03, 0.03] x [-0.02, 0.02] at z = 0.

    Vertex 12 is exactly the bounding-box center (the plate center); the
    corner vertices are 0, 4, 20, 24 and the edge midpoints 2 and 22.
    """
    vertices = []
    for iy in range(ny):
        for ix in range(nx):
            vertices.append((-0.03 + 0.06 * ix / (nx - 1), -0.02 + 0.04 * iy / (ny - 1), 0.0))
    triangles = []
    for iy in range(ny - 1):
        for ix in range(nx - 1):
            a0 = iy * nx + ix
            triangles.append((a0, a0 + 1, a0 + nx))
            triangles.append((a0 + 1, a0 + nx + 1, a0 + nx))
    return TriangleMesh(vertices, triangles, units="m")


class DamageFieldTests(unittest.TestCase):
    def test_damage_at_impact_vertex_is_peak_over_yield(self):
        result = _synthetic_result(sigma_peak=4e7, sf=2.0)
        out = fea.compute_fea(result, {"shell_mesh": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertEqual(out["objects"][0]["damage"][0], round(min(1.0, 4e7 / 8e7), 6))
        self.assertEqual(out["peak"]["damage"], 0.5)
        self.assertEqual(out["peak"]["vertex_index"], 0)
        self.assertEqual(out["yield_stress_pa"], 8e7)

    def test_damage_capped_at_one(self):
        # sf < 1 -> sigma_yield < sigma_peak -> peak damage clamps to 1.0.
        result = _synthetic_result(sigma_peak=4e7, sf=0.5)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertEqual(out["objects"][0]["damage"][0], 1.0)
        self.assertEqual(out["peak"]["damage"], 1.0)
        self.assertEqual(out["yield_stress_pa"], 2e7)

    def test_monotonic_decay_with_distance(self):
        result = _synthetic_result(sigma_peak=4e7, sf=2.0)
        lam = max(0.001, min(0.05, 4.0 * _expected_compression()))
        vertices = [(0.0, 0.0, 0.0), (0.5 * lam, 0.0, 0.0), (lam, 0.0, 0.0), (2.0 * lam, 0.0, 0.0)]
        mesh = TriangleMesh(vertices, [(0, 1, 2), (0, 2, 3), (1, 3, 2)], units="m")
        out = fea.compute_fea(result, {"m": mesh})
        damage = out["objects"][0]["damage"]
        self.assertGreater(damage[0], damage[1])
        self.assertGreater(damage[1], damage[2])
        self.assertGreater(damage[2], damage[3])

    def test_damage_bounded_and_finite_everywhere(self):
        result = _synthetic_result(sigma_peak=4e7, sf=1.2)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        for obj in out["objects"]:
            for damage in obj["damage"]:
                self.assertTrue(math.isfinite(damage))
                self.assertGreaterEqual(damage, 0.0)
                self.assertLessEqual(damage, 1.0)
            for stress in obj["stress_pa"]:
                self.assertTrue(math.isfinite(stress))
                self.assertGreaterEqual(stress, 0.0)
            for displacement in obj["displacement"]:
                self.assertEqual(len(displacement), 3)
                self.assertTrue(all(math.isfinite(c) for c in displacement))

    def test_displacement_inward_along_contact_normal(self):
        result = _synthetic_result(normal=(0.0, 0.0, 1.0), sf=1.2)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        for displacement in out["objects"][0]["displacement"]:
            self.assertAlmostEqual(displacement[0], 0.0, places=9)
            self.assertAlmostEqual(displacement[1], 0.0, places=9)
            self.assertLessEqual(displacement[2], 0.0)

    def test_plastic_amplification_only_above_dent_threshold(self):
        # sf = 1/0.72 -> peak damage 0.72 (above the 0.7 dent threshold);
        # a vertex at distance lambda has damage 0.72*exp(-1) = 0.265 (below).
        sf = 1.0 / 0.72
        result = _synthetic_result(sigma_peak=1e7, sf=sf)
        delta = _expected_compression()
        lam = max(0.001, min(0.05, 4.0 * delta))
        vertices = [(0.0, 0.0, 0.0), (lam, 0.0, 0.0), (0.0, 0.0, 3.0 * lam)]
        mesh = TriangleMesh(vertices, [(0, 1, 2), (1, 2, 0)], units="m")
        out = fea.compute_fea(result, {"m": mesh})
        damage = out["objects"][0]["damage"]
        displacement = out["objects"][0]["displacement"]
        # Peak vertex: elastic factor 1 + 2*(0.72-0.7)/0.3 = 1.1333..., and
        # the depth (0.72 -> 1.1333*delta) stays below the 1.5*delta cap.
        amplification = 1.0 + 2.0 * (0.72 - fea.DENT_THRESHOLD) / fea.PLASTIC_AMPLIFICATION_RANGE
        expected_peak_depth = delta * amplification
        self.assertLess(expected_peak_depth, 1.5 * delta)
        self.assertAlmostEqual(-displacement[0][2], expected_peak_depth, places=8)
        # Vertex below the threshold: elastic factor exactly 1.0.
        self.assertLessEqual(damage[1], fea.DENT_THRESHOLD)
        expected_far_depth = delta * math.exp(-(lam * lam) / (lam * lam))
        self.assertAlmostEqual(-displacement[1][2], expected_far_depth, places=8)

    def test_dent_depth_capped_at_1_5_times_compression(self):
        # sf < 1 -> damage 1.0 at the impact vertex -> amplification 3.0 ->
        # the depth would be 3*delta but is capped at 1.5*delta.
        result = _synthetic_result(sigma_peak=4e7, sf=0.8)
        delta = _expected_compression()
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        depth = -out["objects"][0]["displacement"][0][2]
        self.assertAlmostEqual(depth, 1.5 * delta, places=8)


class PlateFieldTests(unittest.TestCase):
    """The whole-shell plate bending distribution (the user-facing fix:
    the heatmap must show a REAL stress distribution, red at the hot zone
    and blue at the edges, not a fixed Gaussian spot at the impact)."""

    def test_field_max_at_plate_center(self):
        # The normalized plate field's max sits at the plate center: the
        # vertex nearest the center (the exact bbox center vertex here) has
        # damage == min(1, sigma_peak/sigma_yield) within rounding and the
        # stress equals the authoritative shell peak.
        out = fea.compute_fea(_synthetic_plate_result(), {"m": _grid_mesh()})
        self.assertTrue(out["computed"])
        damage = out["objects"][0]["damage"]
        stress = out["objects"][0]["stress_pa"]
        center_index = 12
        self.assertEqual(damage[center_index], round(min(1.0, 4e7 / 8e7), 6))
        self.assertEqual(damage[center_index], max(damage))
        self.assertEqual(stress[center_index], 4e7)
        self.assertEqual(out["peak"]["vertex_index"], center_index)
        self.assertEqual(out["peak"]["location_model_m"], [0.0, 0.0, 0.0])
        self.assertEqual(out["peak"]["damage"], 0.5)
        self.assertEqual(out["peak"]["stress_pa"], 4e7)
        self.assertEqual(out["peak"]["stress_mpa"], 40.0)

    def test_field_decreases_toward_edges(self):
        # Corners carry less damage than the center; the free-edge midpoints
        # (Mx = My = Mxy = 0 on the ideal simply-supported edge) drop to
        # zero — blue at the edges, red at the hot zone.
        out = fea.compute_fea(_synthetic_plate_result(), {"m": _grid_mesh()})
        damage = out["objects"][0]["damage"]
        for corner in (0, 4, 20, 24):
            self.assertLess(damage[corner], damage[12])
        self.assertEqual(damage[2], 0.0)
        self.assertEqual(damage[22], 0.0)

    def test_field_not_rotationally_symmetric_around_impact(self):
        # THE key regression for the user complaint: a vertex far from the
        # impact point but near the plate center has HIGH damage, while the
        # impact-nearest corner vertex has LOW damage — the distribution no
        # longer decays with distance from the impact point.
        impact = (0.028, 0.019, 0.0)
        out = fea.compute_fea(_synthetic_plate_result(center=impact), {"m": _grid_mesh()})
        damage = out["objects"][0]["damage"]
        center_vertex = (0.0, 0.0, 0.0)
        near_vertex = (0.03, 0.02, 0.0)
        dist_sq = lambda a, b: (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
        self.assertLess(dist_sq(near_vertex, impact), dist_sq(center_vertex, impact))
        self.assertGreater(damage[12], damage[24])
        # The plate field is mirror-symmetric about the axes (a Gaussian
        # centered on the impact point would NOT be): vertices 4 and 24 are
        # reflections and carry equal damage despite different impact
        # distances.
        self.assertEqual(damage[4], damage[24])

    def test_dent_displacement_still_localized_at_impact(self):
        # The dent layer keeps the impact Gaussian: the dent is deep at the
        # impact-nearest vertex and almost flat at the plate center, even
        # though the damage field peaks at the plate center.
        impact = (0.028, 0.019, 0.0)
        out = fea.compute_fea(_synthetic_plate_result(center=impact), {"m": _grid_mesh()})
        displacement = out["objects"][0]["displacement"]
        damage = out["objects"][0]["damage"]
        delta = _expected_compression()
        lam = max(0.001, min(0.05, 4.0 * delta))
        d_sq_impact = (0.03 - impact[0]) ** 2 + (0.02 - impact[1]) ** 2
        d_sq_center = impact[0] ** 2 + impact[1] ** 2
        # Impact-nearest vertex (24): depth = delta*exp(-d^2/lambda^2) with
        # sf = 2.0 -> Gaussian-local damage 0.5*exp(...) < 0.7 -> elastic
        # factor exactly 1.0; the displacement points along -normal (0,0,-1).
        expected_depth = delta * math.exp(-d_sq_impact / (lam * lam))
        self.assertAlmostEqual(-displacement[24][2], expected_depth, places=8)
        self.assertAlmostEqual(displacement[24][0], 0.0, places=9)
        self.assertAlmostEqual(displacement[24][1], 0.0, places=9)
        # Plate center (12): far from the impact, high damage, flat dent.
        center_depth = -displacement[12][2]
        self.assertLess(center_depth, expected_depth / 10.0)
        self.assertGreater(damage[12], damage[24])

    def test_peak_record_anchors_at_plate_center_not_impact(self):
        # The peak record reports the continuous field max at the plate
        # center and anchors at the nearest vertex — NOT at the impact point.
        impact = (0.028, 0.019, 0.0)
        out = fea.compute_fea(_synthetic_plate_result(center=impact), {"m": _grid_mesh()})
        self.assertEqual(out["peak"]["vertex_index"], 12)
        self.assertEqual(out["peak"]["location_model_m"], [0.0, 0.0, 0.0])
        self.assertEqual(out["peak"]["damage"], 0.5)
        self.assertTrue(any("plate center" in a for a in out["assumptions"]))

    def test_distributed_force_load_uses_plate_field(self):
        # A distributed force is solved as uniform pressure p = F/(a*b) by
        # the structural solver; the display field is the same plate field
        # (the normalization absorbs p).
        result = _synthetic_plate_result()
        result["structural"]["load_case"] = {"kind": "force", "force_n": 3.6}
        out = fea.compute_fea(result, {"m": _grid_mesh()})
        self.assertTrue(out["computed"])
        self.assertNotIn(fea.FEA_PLATE_FIELD_UNAVAILABLE, out["flags"])
        self.assertEqual(out["objects"][0]["damage"][12], 0.5)

    def test_point_load_falls_back_to_gaussian(self):
        # A point-load solve is NOT a uniform-pressure plate: the display
        # field must fall back to the impact Gaussian, disclosed — never a
        # misleading plate distribution.
        result = _synthetic_plate_result(center=(0.028, 0.019, 0.0))
        result["structural"]["load_case"] = {
            "kind": "force",
            "force_n": 10.0,
            "point_load": True,
        }
        out = fea.compute_fea(result, {"m": _grid_mesh()})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_PLATE_FIELD_UNAVAILABLE, out["flags"])
        self.assertTrue(any("point load" in a for a in out["assumptions"]))
        # Gaussian behavior restored: peak anchored at the impact-nearest
        # vertex (24) and the far center vertex is cold.
        self.assertEqual(out["peak"]["vertex_index"], 24)
        self.assertGreater(out["objects"][0]["damage"][24], out["objects"][0]["damage"][12])

    def test_missing_structure_falls_back_to_gaussian(self):
        # No panel structure/load data (legacy/partial results): the field
        # falls back to the impact Gaussian with a disclosure flag.
        out = fea.compute_fea(_synthetic_result(), {"m": _grid_mesh()})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_PLATE_FIELD_UNAVAILABLE, out["flags"])
        self.assertTrue(any("impact Gaussian" in a for a in out["assumptions"]))
        # The Gaussian still anchors the peak at the impact point.
        self.assertEqual(out["peak"]["vertex_index"], 12)
        self.assertEqual(out["objects"][0]["damage"][12], 0.5)

    def test_degenerate_bbox_object_falls_back_per_object(self):
        # A mesh with zero y-extent cannot be projected onto the panel
        # plane: that object alone falls back to the impact Gaussian,
        # disclosed, while the payload stays computed.
        result = _synthetic_plate_result()
        vertices = [(0.0, 0.0, 0.0), (0.01, 0.0, 0.0), (0.02, 0.0, 0.0), (0.03, 0.0, 0.0)]
        mesh = TriangleMesh(vertices, [(0, 1, 2), (0, 2, 3)], units="m")
        out = fea.compute_fea(result, {"m": mesh})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_PLATE_FIELD_MESH_UNAVAILABLE, out["flags"])
        self.assertNotIn(fea.FEA_PLATE_FIELD_UNAVAILABLE, out["flags"])
        self.assertEqual(out["objects"][0]["damage"][0], 0.5)
        self.assertEqual(out["peak"]["vertex_index"], 0)

    def test_bbox_aspect_fallback_when_panel_geometry_missing(self):
        # Structural panel geometry without a_m/b_m: the plate domain uses
        # the mesh bounding-box aspect (a = max extent, b = min extent),
        # disclosed via the dedicated flag.
        result = _synthetic_plate_result()
        result["structural"]["structure"] = {"type": "shell_panel", "t_m": 0.002}
        out = fea.compute_fea(result, {"m": _grid_mesh()})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_PLATE_FIELD_BBOX_ASPECT, out["flags"])
        self.assertIn(fea.FEA_PLATE_FIELD_MIDPLANE, out["flags"])
        self.assertEqual(out["objects"][0]["damage"][12], 0.5)

    def test_plate_field_two_runs_byte_identical(self):
        result = _synthetic_plate_result(center=(0.028, 0.019, 0.0))
        geometry = {"m": _grid_mesh()}
        first = fea.compute_fea(result, geometry)
        second = fea.compute_fea(result, geometry)
        self.assertEqual(canonical_json(first), canonical_json(second))


class PeakRecordTests(unittest.TestCase):
    def test_peak_record_reports_continuous_field_max(self):
        # The Gaussian peaks AT the impact point, so the peak is the shell
        # peak stress at the nearest vertex — even when every per-vertex
        # damage value is zero (sparse mesh, impact between vertices).
        center = (0.01, 0.02, 0.03)
        result = _synthetic_result(center=center)
        far = _tetrahedron(Transform(translation=(0.1, 0.0, 0.0)))
        near = _tetrahedron(Transform(translation=(0.01, 0.02, 0.03)))
        out = fea.compute_fea(result, {"far": far, "near": near})
        self.assertEqual(out["peak"]["object_id"], "near")
        self.assertEqual(out["peak"]["vertex_index"], 0)
        self.assertEqual(out["peak"]["damage"], 0.5)
        self.assertEqual(out["peak"]["stress_pa"], 4e7)
        self.assertEqual(out["peak"]["stress_mpa"], 40.0)
        self.assertEqual(out["peak"]["location_model_m"], [0.0, 0.0, 0.0])

    def test_peak_world_location_transforms_back_consistently(self):
        # Rotation 90 deg about z: world = R*local + t.
        rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        translation = (0.01, 0.02, 0.03)
        transform = Transform(rotation=rotation, translation=translation)
        vertices = [(0.0, 0.0, 0.0), (0.0, 0.01, 0.0), (0.01, 0.0, 0.0)]
        mesh = TriangleMesh(vertices, [(0, 1, 2), (1, 2, 0)], units="m", transform=transform)
        # The model vertex (0, 0.01, 0) maps to world (0, 0.02, 0.03).
        center = (0.0, 0.02, 0.03)
        result = _synthetic_result(center=center)
        out = fea.compute_fea(result, {"m": mesh})
        self.assertEqual(out["peak"]["vertex_index"], 1)
        local = out["peak"]["location_model_m"]
        world = [
            sum(rotation[row][col] * local[col] for col in range(3)) + translation[row]
            for row in range(3)
        ]
        for axis in range(3):
            self.assertAlmostEqual(world[axis], center[axis], places=8)
        self.assertEqual(out["objects"][0]["damage"][1], 0.5)


class PrimitiveAndSchemaTests(unittest.TestCase):
    def test_primitives_get_procedural_entries_only(self):
        result = _synthetic_result()
        geometry = {
            "box": Box(size=(0.1, 0.06, 0.04)),
            "sphere": Sphere(radius=0.02),
            "cylinder": Cylinder(radius=0.01, height=0.03),
        }
        out = fea.compute_fea(result, geometry)
        self.assertTrue(out["computed"])
        self.assertEqual(out["objects"], [])
        # No mesh vertices: the peak falls back to the first procedural
        # object and reports the continuous field maximum at the impact.
        self.assertEqual(out["peak"]["object_id"], "box")
        self.assertEqual(out["peak"]["damage"], 0.5)
        self.assertEqual(out["peak"]["stress_pa"], 4e7)
        self.assertEqual([entry["object_id"] for entry in out["procedural"]], ["box", "sphere", "cylinder"])
        expected_keys = {
            "object_id",
            "impact_point_model_m",
            "falloff_radius_m",
            "contact_normal_model",
            "peak_stress_pa",
            "yield_stress_pa",
            "max_compression_m",
        }
        for entry in out["procedural"]:
            self.assertEqual(set(entry.keys()), expected_keys)
            self.assertEqual(len(entry["impact_point_model_m"]), 3)
            self.assertEqual(len(entry["contact_normal_model"]), 3)
            self.assertAlmostEqual(entry["falloff_radius_m"], 4.0 * _expected_compression(), places=8)
            self.assertEqual(entry["peak_stress_pa"], 4e7)
            self.assertEqual(entry["yield_stress_pa"], 8e7)
            self.assertAlmostEqual(entry["max_compression_m"], _expected_compression(), places=8)

    def test_schema_shape(self):
        out = fea.compute_fea(_synthetic_result(), {"m": _tetrahedron()})
        self.assertEqual(
            set(out.keys()),
            {
                "computed",
                "peak",
                "yield_stress_pa",
                "damage_basis",
                "safety_factor",
                "impact_window_s",
                "dent_threshold",
                "tear_threshold",
                "center_frame",
                "objects",
                "procedural",
                "assumptions",
                "flags",
            },
        )
        self.assertEqual(out["dent_threshold"], 0.7)
        self.assertEqual(out["tear_threshold"], 0.92)
        self.assertEqual(
            set(out["objects"][0].keys()),
            {"object_id", "vertex_count", "damage", "displacement", "stress_pa"},
        )
        self.assertEqual(out["objects"][0]["vertex_count"], 4)
        self.assertEqual(len(out["objects"][0]["damage"]), 4)
        self.assertEqual(len(out["objects"][0]["displacement"]), 4)
        self.assertEqual(len(out["objects"][0]["stress_pa"]), 4)
        # Meshed objects also carry a procedural record (fragment-space
        # Gaussian base layer for the shader).
        self.assertEqual([entry["object_id"] for entry in out["procedural"]], ["m"])
        json.dumps(out)  # JSON-serializable

    def test_impact_window_from_drop_estimate(self):
        # The drop-derived contact duration (~0.9 ms) is shorter than a
        # display frame, so the dent animation window is floored at 0.05 s
        # with a disclosure (display smoothing, never physics).
        out = fea.compute_fea(_synthetic_result(), {"m": _tetrahedron()})
        self.assertEqual(out["impact_window_s"], 0.05)
        self.assertTrue(
            any("floored" in assumption for assumption in out["assumptions"])
        )


class FailureModeTests(unittest.TestCase):
    def test_missing_peak_stress_computed_false(self):
        result = _synthetic_result(sigma_peak=None)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        self.assertIn(fea.FEA_PEAK_STRESS_UNAVAILABLE, out["flags"])
        self.assertIsNone(out["peak"])
        self.assertEqual(out["objects"], [])

    def test_no_safety_factor_uses_resolved_material_yield(self):
        # Drop-only-style run (no structural safety factor) with a resolved
        # shell material: sigma_yield is the material yield, and a tiny
        # sigma_peak yields peak damage << 1 — never 1.0 by convention.
        properties = {
            "yield_strength": {"value_si": 40e6, "unit": "Pa"},
            "tensile_allowable": {"value_si": 20e6, "unit": "Pa"},
        }
        result = _synthetic_result(sigma_peak=50.0, sf=None, material_properties=properties)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertEqual(out["yield_stress_pa"], 40e6)
        expected_damage = round(min(1.0, 50.0 / 40e6), 6)
        self.assertEqual(out["objects"][0]["damage"][0], expected_damage)
        self.assertEqual(out["peak"]["damage"], expected_damage)
        self.assertLess(out["peak"]["damage"], 1.0)
        self.assertLessEqual(out["peak"]["damage"], 1e-5)
        self.assertTrue(
            any("resolved shell material" in assumption for assumption in out["assumptions"])
        )
        # Plain SI Pa floats are accepted as well (first finite positive
        # among yield_strength, then tensile_allowable).
        result = _synthetic_result(sf=None, material_properties={"yield_strength": 0.0, "tensile_allowable": 25e6})
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertEqual(out["yield_stress_pa"], 25e6)

    def test_no_safety_factor_no_material_yield_computed_false(self):
        # No SF and no resolved material yield: no damage field is emitted —
        # the stress field must not be shown with an invented scale.
        result = _synthetic_result(sf=None)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        self.assertIn(fea.FEA_YIELD_REFERENCE_UNAVAILABLE, out["flags"])
        self.assertIsNone(out["peak"])
        self.assertIsNone(out["yield_stress_pa"])
        self.assertEqual(out["objects"], [])
        self.assertTrue(
            any("no yield reference" in assumption for assumption in out["assumptions"])
        )

    def test_inconclusive_structural_validity_is_disclosed(self):
        # The stress field inherits the solve's validity: an inconclusive
        # response is still visualized but must be flagged, never silent.
        out = fea.compute_fea(_synthetic_result(response_validity="inconclusive"), {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE, out["flags"])
        self.assertTrue(
            any("inconclusive" in assumption for assumption in out["assumptions"])
        )
        out_ok = fea.compute_fea(_synthetic_result(response_validity="valid"), {"m": _tetrahedron()})
        self.assertNotIn(fea.FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE, out_ok["flags"])

    def test_zero_peak_stress_computed_false(self):
        result = _synthetic_result(sigma_peak=0.0)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        self.assertIn(fea.FEA_PEAK_STRESS_UNAVAILABLE, out["flags"])
        self.assertIsNone(out["peak"])
        self.assertEqual(out["objects"], [])

    def test_zero_yield_resolution_never_raises(self):
        # A resolver yielding zero/negative yield values must never raise
        # ZeroDivisionError: zero falls through to the next reference, and
        # all-zero references fail open.
        result = _synthetic_result(
            sf=None,
            material_properties={
                "yield_strength": {"value_si": 0.0, "unit": "Pa"},
                "tensile_allowable": 40e6,
            },
        )
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertEqual(out["yield_stress_pa"], 40e6)
        self.assertEqual(out["objects"][0]["damage"][0], round(min(1.0, 4e7 / 40e6), 6))
        result = _synthetic_result(
            sf=None,
            material_properties={"yield_strength": 0.0, "tensile_allowable": 0.0},
        )
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        self.assertIn(fea.FEA_YIELD_REFERENCE_UNAVAILABLE, out["flags"])

    def test_no_drop_sim_uses_critical_region_with_zero_window(self):
        center = (0.005, 0.01, 0.015)
        result = _synthetic_result(with_drop=False)
        result["shell"]["critical_region"] = list(center)
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertEqual(out["impact_window_s"], 0.0)
        self.assertEqual(out["center_frame"], "panel_local")
        self.assertEqual(out["objects"][0]["displacement"], [[0.0, 0.0, 0.0]] * 4)
        self.assertIn(fea.FEA_IMPACT_CENTER_DEFAULTED, out["flags"])
        self.assertIn(fea.FEA_FALLOFF_DEFAULTED, out["flags"])
        # The impact vertex is the one nearest the critical-region center.
        self.assertEqual(out["peak"]["location_model_m"], [0.0, 0.0, 0.0])
        self.assertTrue(
            any("panel-local stand-in plate coordinate" in a for a in out["assumptions"])
        )

    def test_no_drop_panel_local_center_not_mapped_through_transforms(self):
        # E4: the fallback hotspot center is a PANEL-LOCAL Navier coordinate
        # and must be kept as the display anchor as-is — never mapped into
        # object frames.  With the center kept raw the nearest vertex is
        # index 1; mapping it through the object transform would pick index 0.
        rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        transform = Transform(rotation=rotation, translation=(0.01, 0.02, 0.03))
        center = (0.02, 0.0, 0.0)
        result = _synthetic_result(with_drop=False)
        result["shell"]["critical_region"] = list(center)
        out = fea.compute_fea(result, {"m": _tetrahedron(transform)})
        self.assertTrue(out["computed"])
        self.assertEqual(out["center_frame"], "panel_local")
        # The raw panel-local center is nearest to model vertex 1 (0.03,0,0).
        self.assertEqual(out["peak"]["vertex_index"], 1)
        self.assertNotIn(fea.FEA_TRANSFORM_ASSUMED_IDENTITY, out["flags"])

    def test_empty_geometry_flags_no_meshed_objects(self):
        out = fea.compute_fea(_synthetic_result(), {})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_NO_MESHED_OBJECTS, out["flags"])
        self.assertEqual(out["objects"], [])
        self.assertEqual(out["procedural"], [])
        self.assertIsNone(out["peak"])

    def test_never_raises_on_garbage_inputs(self):
        out = fea.compute_fea({}, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        self.assertIn(fea.FEA_PEAK_STRESS_UNAVAILABLE, out["flags"])
        out = fea.compute_fea(None, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        out = fea.compute_fea("garbage", "garbage")
        self.assertFalse(out["computed"])
        out = fea.compute_fea(
            {"shell": {"peak_stress_pa": float("nan"), "min_safety_factor": 2.0}},
            {"m": _tetrahedron()},
        )
        self.assertFalse(out["computed"])
        # Shell present but no center anywhere: fail open, never raise.
        result = {"shell": {"peak_stress_pa": 4e7, "min_safety_factor": 2.0}}
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertFalse(out["computed"])
        self.assertIn(fea.FEA_IMPACT_CENTER_UNAVAILABLE, out["flags"])


class EstimateConsistencyTests(unittest.TestCase):
    def test_capped_energy_uses_stored_estimate_inputs(self):
        # E2: the pipeline stores the energy-CAPPED estimate inputs in
        # drop_simulation.peak_force_estimate.  fea must consume those — a
        # raw-peak re-derivation would use the lever-amplified (uncapped)
        # kinetic energy and inflate delta_max (up to +84% in audit).
        mass, capped_energy, stiffness, restitution = 0.06, 0.456, 1e5, 0.3
        capped_speed = math.sqrt(2.0 * capped_energy / mass)
        raw_energy = 3.0 * capped_energy  # capped at 1/3 of the raw value
        result = _synthetic_result(mass=mass, energy=capped_energy, stiffness=stiffness,
                                   restitution=restitution)
        result["drop_simulation"]["peak"]["kinetic_energy_j"] = raw_energy
        result["drop_simulation"]["impacts"][0]["impact_speed_m_s"] = math.sqrt(
            2.0 * raw_energy / mass
        )
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        # delta_max = v*sqrt(m/k) with the CAPPED stored speed — the value
        # the pipeline computed internally from the same stored inputs.
        expected = math.sqrt(2.0 * capped_energy / mass) * math.sqrt(mass / stiffness)
        self.assertAlmostEqual(
            out["procedural"][0]["max_compression_m"], round(expected, 9), places=9
        )
        inflated = math.sqrt(2.0 * raw_energy / mass) * math.sqrt(mass / stiffness)
        self.assertLess(out["procedural"][0]["max_compression_m"], inflated)
        # The stored path is NOT the fallback: no fallback disclosure.
        self.assertNotIn(fea.FEA_DERIVED_ESTIMATE_FALLBACK, out["flags"])

    def test_missing_stored_estimate_falls_back_with_disclosure(self):
        # A legacy result without peak_force_estimate re-derives from the
        # raw peak record but MUST disclose the fallback (the raw energy may
        # be energy-capped by the pipeline).
        result = _synthetic_result()
        del result["drop_simulation"]["peak_force_estimate"]
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_DERIVED_ESTIMATE_FALLBACK, out["flags"])
        self.assertTrue(
            any("re-derived from the raw peak record" in a for a in out["assumptions"])
        )
        # The fallback still yields the closed-form linear-spring depth.
        expected = _expected_compression()
        self.assertAlmostEqual(
            out["procedural"][0]["max_compression_m"], round(expected, 9), places=9
        )


class YieldBasisTests(unittest.TestCase):
    def test_safety_factor_path_reports_derated_allowable_basis(self):
        # E3: with a shell safety factor the reported yield stress IS the
        # derated tensile allowable (SF = derated allowable / peak stress);
        # the payload must name that basis instead of silently re-labeling
        # it "material yield".
        out = fea.compute_fea(_synthetic_result(sf=2.0), {"m": _tetrahedron()})
        self.assertEqual(out["damage_basis"], "derated_allowable")
        self.assertTrue(
            any("derated tensile allowable" in a for a in out["assumptions"])
        )

    def test_material_yield_basis_reported(self):
        result = _synthetic_result(
            sf=None,
            material_properties={
                "yield_strength": {"value_si": 40e6, "unit": "Pa"},
                "tensile_allowable": {"value_si": 20e6, "unit": "Pa"},
            },
        )
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertEqual(out["damage_basis"], "material_yield")
        self.assertTrue(any("damage_basis 'material_yield'" in a for a in out["assumptions"]))

    def test_underated_allowable_basis_reported(self):
        result = _synthetic_result(sf=None, material_properties={"tensile_allowable": 25e6})
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertEqual(out["damage_basis"], "material_allowable_underated")
        self.assertEqual(out["yield_stress_pa"], 25e6)

    def test_persisted_derated_allowable_preferred_in_fallback(self):
        # E3: the trace persists derated_tensile_allowable_pa when the
        # solve derated at temperature; the material-yield fallback must
        # prefer it over the catalog (underated) references.
        result = _synthetic_result(
            sf=None,
            material_properties={
                "derated_tensile_allowable_pa": 16.4e6,
                "yield_strength": 40e6,
                "tensile_allowable": 20e6,
            },
        )
        out = fea.compute_fea(result, {"m": _tetrahedron()})
        self.assertEqual(out["damage_basis"], "material_allowable")
        self.assertEqual(out["yield_stress_pa"], 16400000.0)
        self.assertEqual(out["peak"]["damage"], round(min(1.0, 4e7 / 16.4e6), 6))


class TransformValidationTests(unittest.TestCase):
    def test_non_orthonormal_rotation_assumes_identity(self):
        # F2-7: a non-orthonormal rotation is not a valid frame map — it must
        # never kill the payload nor silently skew the field; identity is
        # assumed and disclosed.
        rotation = ((1.0, 0.1, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        geometry = _fake_mesh_with_transform(rotation, (0.01, 0.02, 0.03))
        out = fea.compute_fea(_synthetic_result(), {"m": geometry})
        self.assertTrue(out["computed"])
        self.assertIn(fea.FEA_TRANSFORM_ASSUMED_IDENTITY, out["flags"])
        # The impact point is used as-is: the peak anchors at vertex 0.
        self.assertEqual(out["peak"]["vertex_index"], 0)
        self.assertEqual(out["peak"]["location_model_m"], [0.0, 0.0, 0.0])

    def test_malformed_rotation_rows_never_kill_payload(self):
        # A rotation with non-square rows previously escaped the shape check
        # and raised IndexError inside the orthonormality test, killing the
        # whole payload.  It must degrade to an assumed identity.
        for rotation in (
            ((1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            ((1.0, 0.0, 0.0), (0.0, float("nan"), 0.0), (0.0, 0.0, 1.0)),
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ):
            geometry = _fake_mesh_with_transform(rotation, (0.0, 0.0, 0.0))
            out = fea.compute_fea(_synthetic_result(), {"m": geometry})
            self.assertTrue(out["computed"], rotation)
            self.assertIn(fea.FEA_TRANSFORM_ASSUMED_IDENTITY, out["flags"])
            self.assertEqual(out["peak"]["vertex_index"], 0)

    def test_orthonormal_rotation_kept(self):
        # R maps local (0,0.01,0) -> world (0,0.02,0.03) for this transform:
        # the impact is nearest model vertex 0 (0,0,0).  An identity fallback
        # would leave the center untransformed and anchor at vertex 3 instead.
        rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        transform = Transform(rotation=rotation, translation=(0.01, 0.02, 0.03))
        out = fea.compute_fea(_synthetic_result(center=(0.0, 0.02, 0.03)), {"m": _tetrahedron(transform)})
        self.assertTrue(out["computed"])
        self.assertNotIn(fea.FEA_TRANSFORM_ASSUMED_IDENTITY, out["flags"])
        self.assertEqual(out["peak"]["vertex_index"], 0)
        self.assertEqual(out["peak"]["location_model_m"], [0.0, 0.0, 0.0])


class DisclosureAndRoundingTests(unittest.TestCase):
    def test_peak_stress_basis_assumption_present(self):
        # F2-8: the peak is the continuous-field maximum at the impact point,
        # not a nearest-vertex argmax — the payload must say so.
        out = fea.compute_fea(_synthetic_result(), {"m": _tetrahedron()})
        self.assertTrue(
            any(
                "continuous-field maximum at the impact point" in a
                for a in out["assumptions"]
            )
        )

    def test_safety_factor_rounded_to_policy_precision(self):
        # F2-9: the emitted safety factor follows the module rounding policy
        # (6 dp) instead of leaking raw solver precision.
        sf = 1.23456789012345
        out = fea.compute_fea(_synthetic_result(sf=sf), {"m": _tetrahedron()})
        self.assertEqual(out["safety_factor"], round(sf, 6))
        out_none = fea.compute_fea(_synthetic_result(sf=None), {"m": _tetrahedron()})
        self.assertIsNone(out_none["safety_factor"])


class DeterminismAndIsolationTests(unittest.TestCase):
    def test_two_runs_byte_identical(self):
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation={"height_m": 0.75, "drop_count": 1},
        )
        first = run_pipeline(request)
        second = run_pipeline(request)
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(canonical_json(first["fea"]), canonical_json(second["fea"]))

    def test_run_id_unaffected_by_fea_output(self):
        # fea adds no input: its output must not enter the run_id closure.
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation={"height_m": 0.75, "drop_count": 1},
        )
        noop_payload = {
            "computed": False,
            "peak": None,
            "yield_stress_pa": None,
            "safety_factor": None,
            "impact_window_s": 0.0,
            "dent_threshold": 0.7,
            "tear_threshold": 0.92,
            "objects": [],
            "procedural": [],
            "assumptions": [],
            "flags": ["NOOP"],
        }
        with mock.patch("mouse_sim.fea.compute_fea", return_value=noop_payload):
            noop = run_pipeline(request)
        real = run_pipeline(request)
        self.assertEqual(noop["run_id"], real["run_id"])
        self.assertNotEqual(canonical_json(noop["fea"]), canonical_json(real["fea"]))

    def test_fea_never_perturbs_other_sections(self):
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation={"height_m": 0.75, "drop_count": 1},
        )
        noop_payload = {
            "computed": False,
            "peak": None,
            "yield_stress_pa": None,
            "safety_factor": None,
            "impact_window_s": 0.0,
            "dent_threshold": 0.7,
            "tear_threshold": 0.92,
            "objects": [],
            "procedural": [],
            "assumptions": [],
            "flags": ["NOOP"],
        }
        with mock.patch("mouse_sim.fea.compute_fea", return_value=noop_payload):
            noop = run_pipeline(request)
        real = run_pipeline(request)
        for key in real:
            if key == "fea":
                continue
            self.assertEqual(
                canonical_json(real[key]),
                canonical_json(noop[key]),
                "section {!r} changed when fea ran".format(key),
            )
        self.assertEqual(canonical_json(real["shell"]), canonical_json(noop["shell"]))
        self.assertEqual(real["shell"], noop["shell"])

    def test_fea_isolation_with_mesh_fixture(self):
        vertices = [
            (-0.03, -0.02, 0.0), (0.03, -0.02, 0.0), (0.03, 0.02, 0.0), (-0.03, 0.02, 0.0),
            (-0.03, -0.02, 0.04), (0.03, -0.02, 0.04), (0.03, 0.02, 0.04), (-0.03, 0.02, 0.04),
        ]
        triangles = [
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ]
        request = mouse_project_request(
            objects=[{"id": "shell_mesh", "geometry": {"type": "mesh", "vertices": vertices, "triangles": triangles, "units": "m"}}],
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation={"height_m": 0.75, "drop_count": 1},
        )
        real = run_pipeline(request)
        self.assertEqual(real["errors"], [])
        self.assertTrue(real["fea"]["computed"])
        self.assertEqual(len(real["fea"]["objects"]), 1)
        self.assertEqual(real["fea"]["objects"][0]["object_id"], "shell_mesh")
        self.assertEqual(real["fea"]["objects"][0]["vertex_count"], 8)
        self.assertEqual(
            [entry["object_id"] for entry in real["fea"]["procedural"]],
            ["shell_mesh"],
        )
        for damage in real["fea"]["objects"][0]["damage"]:
            self.assertTrue(math.isfinite(damage))
            self.assertGreaterEqual(damage, 0.0)
            self.assertLessEqual(damage, 1.0)
        # The drop contact point is the manifold centroid, not necessarily a
        # mesh vertex: the peak record reports the CONTINUOUS field maximum
        # at the impact (rounded to 6 dp), anchored at the nearest vertex —
        # it equals min(1, peak/yield) within rounding, while the per-vertex
        # damage can be all-zero on a sparse mesh whose vertices miss the
        # impact zone.
        yield_pa = real["fea"]["yield_stress_pa"]
        peak_pa = real["shell"]["peak_stress_pa"]
        damage = real["fea"]["objects"][0]["damage"]
        continuous = min(1.0, peak_pa / yield_pa)
        self.assertAlmostEqual(real["fea"]["peak"]["damage"], round(continuous, 6), places=6)
        # The per-vertex damage values are stored rounded to 6 dp, so the
        # discrete max can exceed the continuous peak by at most half an ulp.
        self.assertLessEqual(max(damage), real["fea"]["peak"]["damage"] + 1e-6)
        self.assertIn(
            real["fea"]["peak"]["location_model_m"],
            [list(vertex) for vertex in vertices],
        )

    def test_default_material_pipeline_path_still_computes(self):
        # No explicit material: default-material assignment must not break fea.
        vertices = [
            (-0.03, -0.02, 0.0), (0.03, -0.02, 0.0), (0.03, 0.02, 0.0), (-0.03, 0.02, 0.0),
            (-0.03, -0.02, 0.04), (0.03, -0.02, 0.04), (0.03, 0.02, 0.04), (-0.03, 0.02, 0.04),
        ]
        triangles = [
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ]
        request = mouse_project_request(
            objects=[{"id": "shell_mesh", "geometry": {"type": "mesh", "vertices": vertices, "triangles": triangles, "units": "m"}}],
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["fea"]["computed"])

    def test_pipeline_shell_with_sf_emits_derated_allowable_basis(self):
        # E3 end-to-end: the shell-with-SF path reports the yield basis as
        # the derated tensile allowable, and the drop path centers the
        # hotspot in the world frame.
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation={"height_m": 0.75, "drop_count": 1},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["fea"]["computed"])
        self.assertGreater(result["shell"]["min_safety_factor"], 0.0)
        self.assertEqual(result["fea"]["damage_basis"], "derated_allowable")
        self.assertEqual(result["fea"]["center_frame"], "world")

    def test_pipeline_no_drop_emits_panel_local_frame(self):
        request = mouse_project_request(
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002, "material": "ABS"},
            drop_simulation=None,
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["fea"]["computed"])
        self.assertEqual(result["fea"]["center_frame"], "panel_local")
        self.assertIn(fea.FEA_IMPACT_CENTER_DEFAULTED, result["fea"]["flags"])


if __name__ == "__main__":
    unittest.main()
