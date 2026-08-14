"""Hardening-phase regression suite.

Covers the durability-engine hardening fixes: drop scale validation,
geometry topology diagnostics, thin-wall plausibility, point-load
location reporting, shell classification, confidence separation,
critical-region stability, population worst-case mode, material
plausibility bounds, lifecycle non-finite counts, physical invariants,
and ensure_default_material type preservation.

Baseline: 673 tests pass, 2 skipped (pre-hardening suite).
"""

import copy
import math
import unittest

from mouse_sim import Box
from mouse_sim import lifecycle
from mouse_sim import materials
from mouse_sim import mass as mass_module
from mouse_sim import physics
from mouse_sim.drop_sim import DropSimulationError, simulate
from mouse_sim.geometry import TriangleMesh
from mouse_sim.pipeline import run_pipeline

from tests.test_drop_sim import CUBE_INERTIA, CUBE_SUPPORT
from tests.test_pipeline import mouse_project_request

ABS_CATALOG = {
    "ABS": {
        "name": "ABS",
        "properties": {
            "density": {"value": 1040, "unit": "kg/m^3"},
            "young_modulus": {"value": 2.3e9, "unit": "Pa"},
            "poissons_ratio": 0.35,
            "yield_strength": {"value": 40e6, "unit": "Pa"},
            "tensile_allowable": {"value": 20e6, "unit": "Pa"},
        },
    }
}


def _panel_request(p_kpa=1.0, t_m=0.002, a_m=0.06, b_m=0.04, **overrides):
    request = mouse_project_request(
        load_case={"kind": "pressure", "magnitude": {"value": p_kpa, "unit": "kPa"}},
        structure={
            "type": "shell_panel",
            "a_m": a_m,
            "b_m": b_m,
            "t_m": t_m,
            "material": "ABS",
        },
    )
    request.update(overrides)
    return request


def _single_shell_request(catalog, p_kpa=1.0):
    return {
        "schema_id": "gms.project/1",
        "mode": "exploration",
        "units": "m",
        "objects": [
            {
                "id": "shell",
                "geometry": {"type": "box", "size": [0.1, 0.06, 0.04], "units": "m"},
                "material": "ABS",
            }
        ],
        "load_case": {"kind": "pressure", "magnitude": {"value": p_kpa, "unit": "kPa"}},
        "structure": {
            "type": "shell_panel",
            "a_m": 0.06,
            "b_m": 0.04,
            "t_m": 0.002,
            "material": "ABS",
        },
        "materials": catalog,
    }


def _box_mesh(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    vertices = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    triangles = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]
    return TriangleMesh(vertices, triangles)


def _joined(*meshes):
    vertices = []
    triangles = []
    for mesh in meshes:
        offset = len(vertices)
        vertices.extend(mesh.vertices)
        triangles.extend(
            tuple(offset + index for index in triangle) for triangle in mesh.triangles
        )
    return TriangleMesh(vertices, triangles)


class DropScaleValidationTests(unittest.TestCase):
    def _run(self, **kwargs):
        return simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, **kwargs)

    def test_friction_scale_negative_or_nan_rejected(self):
        for value in (-1.0, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(DropSimulationError):
                    self._run(friction_scale=value)

    def test_restitution_scale_zero_three_or_nan_rejected(self):
        for value in (0.0, 3.0, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(DropSimulationError):
                    self._run(restitution_scale=value)

    def test_mass_scale_nan_or_negative_rejected(self):
        for value in (float("nan"), -1.0):
            with self.subTest(value=value):
                with self.assertRaises(DropSimulationError):
                    self._run(mass_scale=value)

    def test_com_offset_nan_rejected(self):
        with self.assertRaises(DropSimulationError):
            self._run(com_offset_m=(0.0, float("nan"), 0.0))

    def test_negative_inertia_diagonal_rejected(self):
        inertia = ((1e-5, 0.0, 0.0), (0.0, -1e-5, 0.0), (0.0, 0.0, 1e-5))
        with self.assertRaises(DropSimulationError):
            simulate(0.1, inertia, CUBE_SUPPORT, 0.5)

    def test_unit_scale_negative_rejected(self):
        with self.assertRaises(DropSimulationError):
            self._run(unit_scale=-1.0)


class GeometryTopologyTests(unittest.TestCase):
    def test_interpenetrating_closed_boxes(self):
        mesh = _joined(
            _box_mesh((0, 0, 0), (1, 1, 1)),
            _box_mesh((0.5, 0.5, 0.5), (1.5, 1.5, 1.5)),
        )
        diagnostics = mesh.diagnostics()
        self.assertFalse(diagnostics.safe_for_mass_properties)
        self.assertIn("self_intersecting", diagnostics.issues)

    def test_nested_shells_cavity(self):
        mesh = _joined(
            _box_mesh((0, 0, 0), (1, 1, 1)),
            _box_mesh((0.2, 0.1, 0.3), (0.7, 0.6, 0.7)),
        )
        diagnostics = mesh.diagnostics()
        self.assertFalse(diagnostics.safe_for_mass_properties)
        self.assertIn("nested_shells", diagnostics.issues)

    def test_tiny_closed_mesh_uses_relative_thresholds(self):
        scale = 1e-9
        mesh = TriangleMesh(
            [(0, 0, 0), (scale, 0, 0), (0, scale, 0), (0, 0, scale)],
            [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)],
        )
        diagnostics = mesh.diagnostics()
        self.assertTrue(diagnostics.safe_for_mass_properties)
        self.assertEqual(diagnostics.issues, ())
        self.assertAlmostEqual(diagnostics.signed_volume_m3, scale ** 3 / 6.0, delta=1e-34)

    def test_disconnected_closed_boxes_multiple_components(self):
        mesh = _joined(
            _box_mesh((0, 0, 0), (1, 1, 1)),
            _box_mesh((3, 0, 0), (4, 1, 1)),
        )
        diagnostics = mesh.diagnostics()
        self.assertIn("multiple_components", diagnostics.issues)


class ThinWallPlausibilityTests(unittest.TestCase):
    def test_one_mm_panel_at_5_kpa_downgraded(self):
        response = physics.shell_panel_response(0.1, 0.1, 0.001, 2.3e9, 0.35, 5000.0)
        self.assertEqual(response.validity, "approximate")
        self.assertIn("SMALL_DEFLECTION_VIOLATED", response.flags)


class PointLoadLocationTests(unittest.TestCase):
    def test_point_load_location_reported_near_load_point(self):
        response = physics.solve_load_case(
            {"kind": "force", "force_n": 5.0, "point_load": True, "location": [0.006, 0.004]},
            {"type": "shell_panel", "a_m": 0.06, "b_m": 0.04, "t_m": 0.002},
            {"young_modulus_pa": 2.3e9, "poissons_ratio": 0.35, "tensile_allowable_pa": 20e6},
        )
        location = response.max_displacement_location
        self.assertIsNotNone(location)
        load_distance = math.hypot(location[0] - 0.006, location[1] - 0.004)
        center_distance = math.hypot(location[0] - 0.03, location[1] - 0.02)
        self.assertLessEqual(load_distance, 0.005)
        self.assertGreater(center_distance, 0.005)


class ShellClassificationTests(unittest.TestCase):
    def test_valid_panel_classified_safe(self):
        result = run_pipeline(_panel_request())
        shell = result["shell"]
        self.assertEqual(shell["classification"], "safe")
        self.assertEqual(shell["status"], "pass")

    def test_marginal_safety_factor_band(self):
        result = run_pipeline(_panel_request(105.0))
        shell = result["shell"]
        self.assertGreaterEqual(shell["min_safety_factor"], 1.0)
        self.assertLess(shell["min_safety_factor"], 1.2)
        self.assertEqual(shell["classification"], "marginal")
        self.assertEqual(shell["status"], "warn")

    def test_open_single_triangle_mesh_invalid_input(self):
        request = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "units": "m",
            "objects": [
                {
                    "id": "shell",
                    "geometry": {
                        "type": "mesh",
                        "vertices": [[0, 0, 0], [0.1, 0, 0], [0, 0.06, 0]],
                        "triangles": [[0, 1, 2]],
                        "units": "m",
                    },
                    "material": "ABS",
                }
            ],
            "load_case": {"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}},
            "structure": {
                "type": "shell_panel",
                "a_m": 0.06,
                "b_m": 0.04,
                "t_m": 0.002,
                "material": "ABS",
            },
        }
        result = run_pipeline(request)
        # The open mesh PARSED but cannot certify a solid (mass unknown):
        # the shell is never declared SAFE on it — the honest state is
        # insufficient evidence, not a pass and not an input error.
        self.assertEqual(result["shell"]["classification"], "insufficient_evidence")
        self.assertEqual(result["shell"]["status"], "warn")

    def test_missing_structure_insufficient_evidence(self):
        result = run_pipeline(mouse_project_request())
        self.assertIsNone(result["structural"])
        self.assertEqual(result["shell"]["classification"], "insufficient_evidence")
        self.assertEqual(result["shell"]["status"], "not_evaluated")


class ConfidenceSeparationTests(unittest.TestCase):
    def test_medium_confidence_without_correlation(self):
        result = run_pipeline(_panel_request())
        shell = result["shell"]
        self.assertEqual(shell["physical_model_confidence"], "medium")
        self.assertEqual(shell["statistical_confidence"], {"kind": "single_run"})

    def test_passed_correlation_allows_high_confidence(self):
        request = _panel_request()
        request["drop_simulation"] = {"height_m": 0.5, "drop_count": 1}
        request["correlation"] = {
            "acceptance": {},
            "measured_drops": [
                {
                    "drop_id": "D1",
                    "height_m": 0.5,
                    "surface": "concrete",
                    "orientation": "flat",
                    "measured_peak_accel_g": 1380.0,
                    "sensor": {"quantity": "resultant_peak_g",
                               "location_body_m": [0.0, 0.0, 0.0]},
                },
                {
                    "drop_id": "D2",
                    "height_m": 0.75,
                    "surface": "concrete",
                    "orientation": "flat",
                    "measured_peak_accel_g": 1760.0,
                    "sensor": {"quantity": "resultant_peak_g",
                               "location_body_m": [0.0, 0.0, 0.0]},
                },
                {
                    "drop_id": "D3",
                    "height_m": 1.0,
                    "surface": "concrete",
                    "orientation": "flat",
                    "measured_peak_accel_g": 2090.0,
                    "sensor": {"quantity": "resultant_peak_g",
                               "location_body_m": [0.0, 0.0, 0.0]},
                },
            ],
        }
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertEqual(result["shell"]["physical_model_confidence"], "high")


class CriticalRegionStabilityTests(unittest.TestCase):
    def test_uniform_load_panel_stable_with_probes(self):
        result = run_pipeline(_panel_request())
        stability = result["shell"]["critical_region_stability"]
        self.assertIsNotNone(stability)
        self.assertTrue(stability["stable"])
        self.assertGreaterEqual(stability["probe_solves"], 2)


class PopulationWorstCaseTests(unittest.TestCase):
    def _worst_case_request(self, population=None):
        request = _panel_request(47.0, t_m=0.0013)
        request["population"] = population if population is not None else {
            "worst_case": {
                "wall_thickness": "min",
                "shell_strength": "min",
                "drop_height": 2.0,
            },
            "sample_count": 100,
            "profile": "general",
            "lifespan_days": 730,
            "workers": 1,
        }
        return request

    def test_worst_case_mode_shape(self):
        population = run_pipeline(self._worst_case_request())["population"]
        self.assertEqual(population["mode"], "deterministic_worst_case")
        self.assertEqual(population["sample_count"], 1)
        self.assertNotIn("wilson_ci", population)
        self.assertIn("verdict", population)
        self.assertIsNotNone(population["shell"])

    def test_worst_case_deterministic(self):
        first = run_pipeline(self._worst_case_request())
        second = run_pipeline(self._worst_case_request())
        self.assertEqual(first["population"], second["population"])
        self.assertEqual(first["run_id"], second["run_id"])

    def test_unknown_worst_case_key_warns(self):
        result = run_pipeline(self._worst_case_request(population={
            "worst_case": {"bogus_key": "max"},
            "sample_count": 100,
            "profile": "general",
            "lifespan_days": 730,
            "workers": 1,
        }))
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("POPULATION_ANALYSIS_FAILED", codes)
        self.assertIsNone(result["population"])

    def test_unknown_population_config_key_warns(self):
        result = run_pipeline(self._worst_case_request(population={
            "bogus_config": 1,
            "sample_count": 100,
            "profile": "general",
            "lifespan_days": 730,
            "workers": 1,
        }))
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("POPULATION_ANALYSIS_FAILED", codes)
        self.assertIsNone(result["population"])

    def test_worst_case_verdict_fail_below_one_safety_factor(self):
        result = run_pipeline(self._worst_case_request())
        population = result["population"]
        self.assertGreater(result["shell"]["min_safety_factor"], 1.0)
        self.assertLess(population["shell"]["safety_factor"], 1.0)
        self.assertEqual(population["verdict"], "fail")


class MaterialPlausibilityTests(unittest.TestCase):
    def _run_with_catalog(self, catalog):
        return run_pipeline(_single_shell_request(catalog))

    def test_implausible_density_rejected(self):
        catalog = copy.deepcopy(ABS_CATALOG)
        catalog["ABS"]["properties"]["density"] = {"value": 1e12, "unit": "kg/m^3"}
        result = self._run_with_catalog(catalog)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("MATERIAL_CATALOG_INVALID", codes)

    def test_implausible_young_modulus_rejected(self):
        catalog = copy.deepcopy(ABS_CATALOG)
        catalog["ABS"]["properties"]["young_modulus"] = {"value": 1e15, "unit": "Pa"}
        result = self._run_with_catalog(catalog)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("MATERIAL_CATALOG_INVALID", codes)

    def test_anisotropic_quartet_ratio_rejected(self):
        catalog = copy.deepcopy(ABS_CATALOG)
        catalog["ABS"]["properties"]["young_modulus_transverse_pa"] = {
            "value": 2.3e9, "unit": "Pa"
        }
        catalog["ABS"]["properties"]["young_modulus_thickness_pa"] = {
            "value": 2.3e12, "unit": "Pa"
        }
        catalog["ABS"]["properties"]["shear_modulus_xy_pa"] = {"value": 7e9, "unit": "Pa"}
        catalog["ABS"]["properties"]["poissons_ratio_xy"] = 0.14
        catalog["ABS"]["anisotropy_supported"] = True
        result = self._run_with_catalog(catalog)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("MATERIAL_CATALOG_INVALID", codes)

    def test_normal_abs_catalog_accepted(self):
        result = self._run_with_catalog(ABS_CATALOG)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["mass"]["mass_status"], "calculated")


class LifecycleNonFiniteCountsTests(unittest.TestCase):
    def test_inf_prior_drops_rejected_as_invalid_input(self):
        # Audit fix: Inf must never be converted into a valid physical value
        # (the old behavior silently turned it into 0); it is an explicit
        # invalid-input state.
        with self.assertRaises(ValueError):
            lifecycle.degradation_factors(
                {"prior_drops": float("inf"), "prior_impact_energy_j": 5.0}
            )

    def test_inf_string_prior_drops_rejected_as_invalid_input(self):
        with self.assertRaises(ValueError):
            lifecycle.degradation_factors(
                {"prior_drops": "inf", "prior_impact_energy_j": 5.0}
            )

    def test_pipeline_inf_string_prior_drops_reports_invalid_input(self):
        request = mouse_project_request(
            drop_simulation={"height_m": 0.5},
            lifecycle={"prior_drops": "inf", "prior_impact_energy_j": 1.0},
        )
        result = run_pipeline(request)
        self.assertNotEqual(result["errors"], [])
        self.assertTrue(
            any(item["code"] == "DROP_SIMULATION_FAILED" for item in result["issues"])
        )
        self.assertEqual(result["lifecycle_state"], "failed")


class PhysicalInvariantsTests(unittest.TestCase):
    def test_density_doubling_doubles_mass(self):
        box = Box((1.0, 1.0, 1.0))
        base = mass_module.mass_properties({"obj": box}, {"obj": 1000.0}).mass_kg
        double = mass_module.mass_properties({"obj": box}, {"obj": 2000.0}).mass_kg
        self.assertAlmostEqual(double, base * 2.0, places=12)

    def test_scale_doubling_scales_volume_area_mass_inertia(self):
        unit = Box((1.0, 1.0, 1.0))
        scaled = Box((2.0, 2.0, 2.0))
        base = unit.mass_properties(1000.0)
        doubled = scaled.mass_properties(1000.0)
        self.assertAlmostEqual(scaled.volume() / unit.volume(), 8.0, places=12)
        self.assertAlmostEqual(scaled.surface_area() / unit.surface_area(), 4.0, places=12)
        self.assertAlmostEqual(doubled["mass_kg"] / base["mass_kg"], 8.0, places=12)
        self.assertAlmostEqual(
            doubled["inertia_tensor_kg_m2"][0][0] / base["inertia_tensor_kg_m2"][0][0],
            32.0,
            places=12,
        )

    def test_gravity_halving_scales_impact_speed(self):
        full = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, gravity=9.81)
        half = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, gravity=4.905)
        ratio = (
            half["impacts"][0]["impact_speed_m_s"] / full["impacts"][0]["impact_speed_m_s"]
        )
        self.assertLess(abs(ratio - math.sqrt(0.5)) / math.sqrt(0.5), 0.02)

    def test_height_doubling_doubles_impact_energy(self):
        low = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5)
        high = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 1.0)
        ratio = (
            high["impacts"][0]["kinetic_energy_j"] / low["impacts"][0]["kinetic_energy_j"]
        )
        self.assertLess(abs(ratio - 2.0) / 2.0, 0.02)

    def test_density_doubling_keeps_impact_speed_bit_equal(self):
        light = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5)
        heavy = simulate(0.2, CUBE_INERTIA, CUBE_SUPPORT, 0.5)
        self.assertEqual(
            light["impacts"][0]["impact_speed_m_s"],
            heavy["impacts"][0]["impact_speed_m_s"],
        )

    def test_modulus_doubling_halves_deflection_exactly(self):
        catalog_b = copy.deepcopy(ABS_CATALOG)
        catalog_b["ABS"]["properties"]["young_modulus"] = {"value": 4.6e9, "unit": "Pa"}
        base = run_pipeline(_single_shell_request(ABS_CATALOG))
        doubled = run_pipeline(_single_shell_request(catalog_b))
        self.assertEqual(base["mass"], doubled["mass"])
        self.assertEqual(
            base["structural"]["response"]["max_stress_pa"],
            doubled["structural"]["response"]["max_stress_pa"],
        )
        self.assertAlmostEqual(
            doubled["structural"]["response"]["max_displacement_m"],
            base["structural"]["response"]["max_displacement_m"] / 2.0,
            places=15,
        )

    def test_strength_doubling_doubles_safety_factor(self):
        low = physics.shell_panel_response(0.06, 0.04, 0.002, 2.3e9, 0.35, 1000.0, allowable_pa=20e6)
        high = physics.shell_panel_response(0.06, 0.04, 0.002, 2.3e9, 0.35, 1000.0, allowable_pa=40e6)
        self.assertAlmostEqual(high.safety_factor, low.safety_factor * 2.0, places=12)

    def test_thickness_halving_scales_deflection_and_stress(self):
        thick = physics.shell_panel_response(0.06, 0.04, 0.002, 2.3e9, 0.35, 1000.0)
        thin = physics.shell_panel_response(0.06, 0.04, 0.001, 2.3e9, 0.35, 1000.0)
        deflection_ratio = thin.max_displacement_m / thick.max_displacement_m
        stress_ratio = thin.max_stress_pa / thick.max_stress_pa
        self.assertLess(abs(deflection_ratio - 8.0) / 8.0, 0.001)
        self.assertLess(abs(stress_ratio - 4.0) / 4.0, 0.001)


class EnsureDefaultMaterialTests(unittest.TestCase):
    def test_material_catalog_type_preserved_with_case_insensitive_lookup(self):
        catalog = materials.MaterialCatalog()
        abs_material = materials.load_material_catalog(
            {
                "materials": [
                    {
                        "key": "ABS",
                        "name": "ABS",
                        "properties": {
                            "density": 1040,
                            "young_modulus": 2.3e9,
                            "poissons_ratio": 0.35,
                        },
                    }
                ]
            }
        )["ABS"]
        catalog["ABS"] = abs_material
        ensured = materials.ensure_default_material(catalog)
        self.assertIsInstance(ensured, materials.MaterialCatalog)
        self.assertIn("default", ensured)
        self.assertIs(ensured["ABS"], abs_material)
        self.assertIs(ensured["abs"], abs_material)


if __name__ == "__main__":
    unittest.main()
