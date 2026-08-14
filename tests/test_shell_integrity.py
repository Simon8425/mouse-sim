"""Shell-integrity regression tests for the defect-resolution phase.

These pin the trust boundaries that must never be crossed:
- invalid geometry must never certify trustworthy mass / high confidence;
- fabricated correlation data must never unlock high physical-model
  confidence;
- the impact safety factor must use the shell's ACTUAL resolved material;
- the engine hash must cover every result-affecting module;
- microscopic (but valid) geometry must be labeled OUTSIDE_SUPPORTED_PHYSICAL_SCALE,
  not "invalid geometry".
"""

import math
import os
import tempfile
import unittest

from mouse_sim import TriangleMesh, mass
from mouse_sim.pipeline import _ENGINE_BEHAVIOR_MODULES, _engine_hash, run_pipeline

SHELL = {"type": "box", "size": [60, 40, 10], "units": "mm"}
PCB = {"type": "box", "size": [50, 30, 1.6], "units": "mm"}
BATTERY = {"type": "box", "size": [40, 20, 8], "units": "mm"}


def mouse_request(**overrides):
    request = {
        "schema_id": "gms.project-document/1",
        "mode": "exploration",
        "units": "mm",
        "objects": [
            {"id": "shell", "geometry": SHELL, "material": "ABS", "structural_behavior": "shell"},
            {"id": "pcb", "geometry": PCB, "material": "FR4", "structural_behavior": "rigid"},
            {"id": "battery", "geometry": BATTERY, "material": "LiPo", "structural_behavior": "rigid"},
        ],
        "options": {"min_thickness_m": 0.001, "max_thickness_m": 0.05},
    }
    request.update(overrides)
    return request


def torus_mesh(major=0.05, minor=0.015, n_major=60, n_minor=45):
    """Closed, manifold torus with > 5000 triangles (5400) and no degenerate
    vertices — the self-intersection sweep is intentionally not attempted."""
    vertices = []
    for i in range(n_major):
        u = 2.0 * math.pi * i / n_major
        for j in range(n_minor):
            v = 2.0 * math.pi * j / n_minor
            radius = major + minor * math.cos(v)
            vertices.append((radius * math.cos(u), radius * math.sin(u), minor * math.sin(v)))
    triangles = []
    for i in range(n_major):
        next_i = (i + 1) % n_major
        for j in range(n_minor):
            next_j = (j + 1) % n_minor
            a = i * n_minor + j
            b = next_i * n_minor + j
            c = next_i * n_minor + next_j
            d = i * n_minor + next_j
            triangles.append((a, b, c))
            triangles.append((a, c, d))
    return TriangleMesh(vertices, triangles)


class ImpactMaterialSelectionTests(unittest.TestCase):
    def test_impact_safety_factor_uses_shell_material(self):
        # Two deliberately different allowables: the object's material must
        # drive the impact SF, not catalog insertion order.
        catalog = {
            "WEAK": {
                "name": "WEAK",
                "properties": {
                    "density": {"value": 1040, "unit": "kg/m^3"},
                    "young_modulus": {"value": 2.3e9, "unit": "Pa"},
                    "poissons_ratio": 0.35,
                    "yield_strength": {"value": 10e6, "unit": "Pa"},
                    "tensile_allowable": {"value": 5e6, "unit": "Pa"},
                    "fatigue_strength_at_1e6_pa": 5e6,
                    "fatigue_exponent_k": 6,
                },
                "approval_state": "approved",
                "provenance": {
                    "source_type": "supplier",
                    "source_id": "supplier-lot-weak",
                    "condition": "23 C, dry",
                    "confidence": "high",
                },
            },
            "ABS": {
                "name": "ABS",
                "properties": {
                    "density": {"value": 1040, "unit": "kg/m^3"},
                    "young_modulus": {"value": 2.3e9, "unit": "Pa"},
                    "poissons_ratio": 0.35,
                    "yield_strength": {"value": 40e6, "unit": "Pa"},
                    "tensile_allowable": {"value": 20e6, "unit": "Pa"},
                    "fatigue_strength_at_1e6_pa": 14e6,
                    "fatigue_exponent_k": 6,
                },
                "approval_state": "approved",
                "provenance": {
                    "source_type": "supplier",
                    "source_id": "supplier-lot-42",
                    "condition": "23 C, dry",
                    "confidence": "high",
                },
            },
        }
        request = mouse_request(
            materials=catalog,
            objects=[
                {"id": "shell", "geometry": SHELL, "material": "WEAK",
                 "structural_behavior": "shell"},
                {"id": "pcb", "geometry": PCB, "material": "ABS", "structural_behavior": "rigid"},
            ],
            impact={
                "fall_height_m": 0.5,
                "contact_stiffness_n_per_m": 1e5,
                "load_path_area_m2": 1e-4,
            },
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        impact_result = result["impact"]["result"]
        self.assertEqual(impact_result["validity"], "valid")
        self.assertGreater(float(impact_result["safety_factor"]), 0.0)
        # 5 MPa allowable over the load-path stress — NOT ABS (20 MPa) and
        # NOT catalog insertion order.
        self.assertAlmostEqual(
            float(impact_result["safety_factor"]),
            5e6 / float(impact_result["load_path_stress_pa"]),
            places=6,
        )
        self.assertEqual(result["impact"]["material"], "shell")


class CorrelationConfidenceTests(unittest.TestCase):
    def _run(self, drops):
        request = mouse_request(
            drop_simulation={"height_m": 0.5, "drop_count": 1},
            correlation={"acceptance": {}, "measured_drops": drops},
        )
        return run_pipeline(request)

    def _drop(self, index, height, measured, drop_id=None):
        return {
            "drop_id": drop_id or "D{}".format(index + 1),
            "height_m": height,
            "surface": "concrete",
            "orientation": "flat",
            "measured_peak_accel_g": measured,
            "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
        }

    def test_genuine_correlation_passes(self):
        # Three independent heights with measured values close to the
        # predictions (~2877 g at 0.5 m, ~3675 g at 0.75 m, ~4371 g at 1.0 m
        # for this fixture's mass under the default Hertz point-contact
        # model).
        drops = [
            self._drop(0, 0.5, 2880.0),
            self._drop(1, 0.75, 3680.0),
            self._drop(2, 1.0, 4370.0),
        ]
        result = self._run(drops)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertNotIn(
            "no passed measured-drop correlation",
            result["shell"]["limitations"],
        )

    def test_duplicate_conditions_cannot_unlock_high_confidence(self):
        # Identical conditions (same drop_id/height/surface/orientation) are
        # one measurement, not three: the verdict must fail and the shell
        # confidence must be capped below high.
        drops = [self._drop(0, 0.5, 190.0, drop_id="D1") for _ in range(3)]
        result = self._run(drops)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")
        self.assertTrue(
            any("correlation" in item for item in result["shell"]["limitations"])
        )

    def test_degenerate_measured_values_cannot_unlock_high_confidence(self):
        # Same measured value repeated (two distinct values for three
        # conditions): the dataset cannot define a meaningful R^2.
        drops = [
            self._drop(0, 0.5, 190.0),
            self._drop(1, 0.5, 190.0),
            self._drop(2, 0.5, 220.0),
        ]
        result = self._run(drops)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")

    def test_single_height_repeats_cannot_unlock_high_confidence(self):
        # Three repeats at ONE height: the predictor has zero variance, so
        # R^2 is undefined — fail closed rather than pass vacuously.
        drops = [
            self._drop(0, 0.5, 190.0),
            self._drop(1, 0.5, 200.0),
            self._drop(2, 0.5, 180.0),
        ]
        result = self._run(drops)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")

    def test_repeated_condition_under_different_ids_cannot_unlock_high(self):
        # Two heights with the 0.75 m condition repeated under a different
        # drop_id is only TWO independent conditions: the duplicate triple
        # must fail closed even with distinct measured values.
        drops = [
            self._drop(0, 0.5, 470.0),
            self._drop(1, 0.75, 580.0),
            self._drop(2, 0.75, 585.0),
        ]
        result = self._run(drops)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertIn("duplicate", result["correlation"]["explanation"])
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")

    def test_negative_measured_value_fails_pipeline_correlation(self):
        drops = [
            self._drop(0, 0.5, -470.0),
            self._drop(1, 0.75, 580.0),
            self._drop(2, 1.0, 670.0),
        ]
        result = self._run(drops)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")


class GeometryIntegrityConfidenceTests(unittest.TestCase):
    def test_unverified_self_intersection_blocks_safe_and_high_confidence(self):
        mesh = torus_mesh()
        diagnostics = mesh.diagnostics()
        # The fixture itself must be a clean large closed mesh (no other
        # integrity issue contaminates the assertion).
        self.assertTrue(diagnostics.closed)
        self.assertIn("self_intersection_unverified", diagnostics.issues)
        self.assertGreater(len(mesh.triangles), 5000)

        request = mouse_request(
            objects=[{"id": "shell", "geometry": mesh.to_dict(), "material": "ABS",
                      "structural_behavior": "shell"}],
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002,
                       "material": "ABS"},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        finding_codes = [item["code"] for item in result["validation"]["findings"]]
        self.assertIn("SELF_INTERSECTION_UNVERIFIED", finding_codes)
        self.assertEqual(result["validation"]["status"], "warn")
        shell = result["shell"]
        self.assertNotEqual(shell["classification"], "safe")
        self.assertNotEqual(shell["physical_model_confidence"], "high")
        self.assertTrue(
            any("self-intersection unverified" in item for item in shell["limitations"])
        )

    def test_tiny_geometry_is_unsupported_scale_not_invalid(self):
        # A mesh cube at 1e-9 m: relatively fine, absolutely below the
        # certified scale — must be labeled OUTSIDE_SUPPORTED_PHYSICAL_SCALE,
        # not "invalid geometry".
        vertices = [(-5e-10, -5e-10, -5e-10), (5e-10, -5e-10, -5e-10), (5e-10, 5e-10, -5e-10),
                    (-5e-10, 5e-10, -5e-10), (-5e-10, -5e-10, 5e-10), (5e-10, -5e-10, 5e-10),
                    (5e-10, 5e-10, 5e-10), (-5e-10, 5e-10, 5e-10)]
        triangles = [
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ]
        tiny_mesh = TriangleMesh(vertices, triangles)
        request = mouse_request(
            objects=[{"id": "shell", "geometry": tiny_mesh.to_dict(), "material": "ABS",
                      "structural_behavior": "shell"}],
            load_case={"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            structure={"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002,
                       "material": "ABS"},
        )
        result = run_pipeline(request)
        # Geometry parsed fine — this is NOT invalid input.
        self.assertNotIn("GEOMETRY_PARSE_FAILED", [item["code"] for item in result["issues"]])
        finding_codes = [item["code"] for item in result["validation"]["findings"]]
        self.assertIn("OUTSIDE_SUPPORTED_PHYSICAL_SCALE", finding_codes)
        self.assertNotIn("GEOMETRY_ZERO_VOLUME", finding_codes)
        self.assertEqual(result["mass"]["mass_status"], "unknown")
        self.assertEqual(result["shell"]["classification"], "insufficient_evidence")


class EngineHashCoverageTests(unittest.TestCase):
    def test_engine_hash_covers_result_affecting_modules(self):
        # The audit found importers/model/canonical/schema/step processing
        # were missing from the hash: a change there can alter results but
        # leave the run_id unchanged, serving stale cached physics.
        for name in (
            "importers",
            "canonical",
            "model",
            "schema",
            "step_kernel",
            "freecad_step_worker",
        ):
            self.assertIn(name, _ENGINE_BEHAVIOR_MODULES)

    def test_engine_hash_changes_when_importers_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in _ENGINE_BEHAVIOR_MODULES:
                with open(os.path.join(directory, name + ".py"), "w", encoding="utf-8") as stream:
                    stream.write("# baseline\n")
            baseline = _engine_hash(root=directory)
            with open(os.path.join(directory, "importers.py"), "a", encoding="utf-8") as stream:
                stream.write("# geometry parse changed\n")
            changed = _engine_hash(root=directory)
            self.assertNotEqual(baseline, changed)


if __name__ == "__main__":
    unittest.main()
