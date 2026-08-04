import json
import math
import unittest

from mouse_sim.errors import UnitError
from mouse_sim.physics import (
    INVALID_LOAD_UNITS,
    INVALID_LOAD_LOCATION,
    INVALID_POISSON_RATIO,
    MOUSE_LOAD_TEMPLATES,
    POINT_LOAD_SINGULARITY,
    SCREENING_SURROGATE_MODEL_ID,
    THIN_SHELL_OUT_OF_RANGE,
    UNDERCONSTRAINED_REACTIONS,
    SolverCapabilities,
    beam_response,
    preflight_structural_case,
    shell_panel_response,
    solve_load_case,
)


class PhysicsTests(unittest.TestCase):
    def test_cantilever_point_deflection_formula(self):
        res = beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                            A_m2=1e-4, nu=0.3, force_n=10.0)
        expected = 10.0 * 0.1 ** 3 / (3.0 * 200e9 * 1e-8)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=12)
        self.assertEqual(res.reactions["R1"], 10.0)
        self.assertEqual(res.force_residual_n, 0.0)

    def test_ss_uniform_deflection_formula(self):
        res = beam_response("simply_supported_uniform", L_m=0.1, E_pa=200e9,
                            I_m4=1e-8, A_m2=1e-4, nu=0.3, q_n_per_m=100.0)
        expected = 5.0 * 100.0 * 0.1 ** 4 / (384.0 * 200e9 * 1e-8)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=12)
        self.assertEqual(res.reactions["R1"], 5.0)
        self.assertEqual(res.reactions["R2"], 5.0)

    def test_shell_panel_center_max_symmetry(self):
        res = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        self.assertAlmostEqual(res.max_displacement_location[0], 0.05, places=6)
        self.assertAlmostEqual(res.max_displacement_location[1], 0.05, places=6)
        self.assertGreater(res.max_displacement_m, 0.0)
        self.assertEqual(res.validity, "valid")

    def test_thin_shell_out_of_range_flag(self):
        res = shell_panel_response(0.1, 0.1, 0.02, 2e9, 0.35, 1000.0)
        self.assertIn(THIN_SHELL_OUT_OF_RANGE, res.flags)
        self.assertEqual(res.validity, "approximate")

    def test_point_load_singularity_flag(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        load = {"kind": "force", "force_n": 5.0, "point_load": True,
                "direction": (0, 0, -1)}
        res = solve_load_case(load, structure, material)
        self.assertIn(POINT_LOAD_SINGULARITY, res.flags)
        self.assertEqual(res.validity, "approximate")

    def test_distributed_force_shell_matches_uniform_pressure(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        force_n = 5.0
        load = {"kind": "force", "force_n": force_n, "point_load": False}
        res = solve_load_case(load, structure, material)
        pressure = force_n / (0.1 * 0.1)
        direct = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, pressure)
        self.assertAlmostEqual(res.max_displacement_m, direct.max_displacement_m, places=12)
        self.assertAlmostEqual(res.max_stress_filtered_pa, direct.max_stress_filtered_pa, places=12)
        self.assertEqual(res.validity, "valid")
        self.assertNotIn(POINT_LOAD_SINGULARITY, res.flags)
        self.assertTrue(any("full-panel uniform pressure" in a for a in res.assumptions))

    def test_distributed_force_defaults_to_pressure_for_shell(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        load = {"kind": "force", "force_n": 5.0}
        res = solve_load_case(load, structure, material)
        pressure = 5.0 / (0.1 * 0.1)
        direct = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, pressure)
        self.assertAlmostEqual(res.max_displacement_m, direct.max_displacement_m, places=12)
        self.assertNotIn(POINT_LOAD_SINGULARITY, res.flags)

    def test_distributed_force_beam_matches_uniform_line_load(self):
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4,
                     "section_modulus_m3": 1e-6, "support": "simply_supported"}
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        force_n = 10.0
        load = {"kind": "force", "force_n": force_n, "point_load": False}
        res = solve_load_case(load, structure, material)
        q = force_n / 0.1
        direct = beam_response("simply_supported_uniform", L_m=0.1, E_pa=200e9,
                               I_m4=1e-8, A_m2=1e-4, nu=0.3, q_n_per_m=q,
                               section_modulus_m3=1e-6)
        self.assertAlmostEqual(res.max_displacement_m, direct.max_displacement_m, places=12)
        self.assertEqual(res.reactions["R1"], q * 0.1 / 2.0)
        self.assertEqual(res.reactions["R2"], q * 0.1 / 2.0)
        self.assertEqual(res.force_residual_n, 0.0)
        self.assertEqual(res.validity, "valid")
        self.assertNotIn(POINT_LOAD_SINGULARITY, res.flags)
        self.assertTrue(any("uniform line load" in a for a in res.assumptions))

    def test_distributed_force_beam_cantilever(self):
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4,
                     "section_modulus_m3": 1e-6, "support": "cantilever"}
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        load = {"kind": "force", "force_n": 10.0, "point_load": False}
        res = solve_load_case(load, structure, material)
        q = 10.0 / 0.1
        expected = q * 0.1 ** 4 / (8.0 * 200e9 * 1e-8)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=12)
        self.assertEqual(res.reactions["R1"], q * 0.1)
        self.assertEqual(res.validity, "valid")
        self.assertNotIn(POINT_LOAD_SINGULARITY, res.flags)

    def test_safety_factor_not_available_without_allowable(self):
        res = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        self.assertIsNone(res.safety_factor)
        self.assertEqual(res.safety_factor_status, "not_available")

    def test_preflight_missing_fixture_issue(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35,
                    "tensile_allowable_pa": 20e6}
        load = {"kind": "gravity", "magnitude": 1.0}
        issues = preflight_structural_case(load, structure, material)
        codes = [issue["code"] for issue in issues]
        self.assertIn(UNDERCONSTRAINED_REACTIONS, codes)

    def test_mouse_load_templates_have_five_keys(self):
        self.assertEqual(len(MOUSE_LOAD_TEMPLATES), 5)
        for template in MOUSE_LOAD_TEMPLATES.values():
            for field_name in ("name", "description", "default_loads",
                               "fixture_assumptions", "acceptance_notes"):
                self.assertIn(field_name, template)

    def test_gravity_without_fixture_underconstrained(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        load = {"kind": "gravity", "magnitude": 1.0}
        res = solve_load_case(load, structure, material)
        self.assertIn(UNDERCONSTRAINED_REACTIONS, res.flags)
        self.assertEqual(res.validity, "approximate")

    def test_to_dict_json_serializable(self):
        res = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0,
                                   allowable_pa=20e6)
        data = res.to_dict()
        text = json.dumps(data)
        self.assertIn("method_id", json.loads(text))
        caps = SolverCapabilities().to_dict()
        json.dumps(caps)


    def test_shell_panel_matches_navier_center_reference(self):
        a = b = 0.1
        t = 0.001
        E = 2.3e9
        nu = 0.35
        p = 5000.0
        D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
        reference = 0.0
        for m in range(1, 100, 2):
            for n in range(1, 100, 2):
                coefficient = (16.0 * p / (D * math.pi ** 6 * m * n
                               * (m * m / (a * a) + n * n / (b * b)) ** 2))
                reference += coefficient * math.sin(m * math.pi / 2.0) * math.sin(n * math.pi / 2.0)
        order9 = shell_panel_response(a, b, t, E, nu, p, series_order=9)
        order13 = shell_panel_response(a, b, t, E, nu, p, series_order=13)
        self.assertLessEqual(abs(order9.max_displacement_m - reference) / abs(reference), 0.02)
        self.assertLessEqual(abs(order13.max_displacement_m - reference) / abs(reference), 0.02)
        change = abs(order13.max_displacement_m - order9.max_displacement_m) / order9.max_displacement_m
        self.assertLessEqual(change, 0.05)

    def test_single_term_series_order_one(self):
        a = b = 0.1
        t = 0.001
        E = 2.3e9
        nu = 0.35
        p = 5000.0
        D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
        res = shell_panel_response(a, b, t, E, nu, p, series_order=1)
        expected = 16.0 * p / (D * math.pi ** 6 * (1.0 / (a * a) + 1.0 / (b * b)) ** 2)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=8)

    def test_suction_pressure_deflection_not_fabricated(self):
        positive = shell_panel_response(0.1, 0.1, 0.001, 2.3e9, 0.35, 5000.0)
        suction = shell_panel_response(0.1, 0.1, 0.001, 2.3e9, 0.35, -5000.0)
        self.assertAlmostEqual(abs(suction.max_displacement_m), positive.max_displacement_m, places=12)
        self.assertAlmostEqual(suction.max_displacement_location[0], 0.05, places=6)
        self.assertAlmostEqual(suction.max_displacement_location[1], 0.05, places=6)


class LoadUnitGuardTests(unittest.TestCase):
    """Finding 5: a force value must not be silently accepted as pressure
    (and vice versa) when units are annotated."""

    def shell(self, **overrides):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        structure.update(overrides)
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        return structure, material

    def test_force_unit_rejected_as_pressure(self):
        structure, material = self.shell()
        load = {"kind": "pressure", "magnitude_pa": {"value": 10.0, "unit": "N"}}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn(INVALID_LOAD_UNITS, res.flags)

    def test_pressure_unit_rejected_as_force(self):
        structure, material = self.shell()
        load = {"kind": "force", "force_n": {"value": 10.0, "unit": "kPa"}}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn(INVALID_LOAD_UNITS, res.flags)

    def test_correct_units_accepted(self):
        structure, material = self.shell()
        load = {"kind": "pressure", "magnitude_pa": {"value": 1.0, "unit": "kPa"}}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "valid")
        self.assertNotIn(INVALID_LOAD_UNITS, res.flags)
        direct = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        self.assertAlmostEqual(res.max_displacement_m, direct.max_displacement_m, places=12)

    def test_plain_numeric_loads_still_accepted(self):
        structure, material = self.shell()
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "valid")


class StructureDimGuardTests(unittest.TestCase):
    """Finding 5: a_m/t_m/I_m4/section_modulus_m3 positivity and finiteness."""

    def shell(self, **overrides):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        structure.update(overrides)
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        return structure, material

    def beam(self, **overrides):
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4,
                     "section_modulus_m3": 1e-6, "support": "cantilever"}
        structure.update(overrides)
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        return structure, material

    def test_shell_zero_dimension_flagged_not_crash(self):
        structure, material = self.shell()
        structure["a_m"] = 0.0
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, material)
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn("a_m", res.assumptions[0] if res.assumptions else "")

    def test_shell_nan_thickness_flagged(self):
        structure, material = self.shell()
        structure["t_m"] = float("nan")
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, material)
        self.assertEqual(res.validity, "inconclusive")

    def test_beam_zero_section_modulus_flagged(self):
        structure, material = self.beam(section_modulus_m3=0.0)
        res = solve_load_case({"kind": "force", "force_n": 10.0}, structure, material)
        self.assertEqual(res.validity, "inconclusive")

    def test_beam_missing_moment_of_inertia_flagged(self):
        structure, material = self.beam()
        del structure["I_m4"]
        res = solve_load_case({"kind": "force", "force_n": 10.0}, structure, material)
        self.assertEqual(res.validity, "inconclusive")

    def test_beam_response_rejects_nonpositive_section_modulus(self):
        with self.assertRaises(ValueError):
            beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                          A_m2=1e-4, nu=0.3, force_n=10.0, section_modulus_m3=0.0)
        with self.assertRaises(ValueError):
            beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                          A_m2=1e-4, nu=0.3, force_n=10.0, section_modulus_m3=-1.0)

    def test_point_load_shell_guards_dimensions(self):
        structure, material = self.shell()
        structure["t_m"] = 0.0
        load = {"kind": "force", "force_n": 5.0, "point_load": True}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "inconclusive")

    def test_invalid_poisson_ratio_is_reported_without_division_crash(self):
        structure, material = self.shell()
        material["poissons_ratio"] = 0.5
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, material)
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn(INVALID_POISSON_RATIO, res.flags)

    def test_point_load_outside_panel_is_rejected(self):
        structure, material = self.shell()
        res = solve_load_case(
            {"kind": "force", "force_n": 5.0, "point_load": True, "location": (0.2, 0.05)},
            structure,
            material,
        )
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn(INVALID_LOAD_LOCATION, res.flags)

    def test_preflight_zero_panel_width_is_reported(self):
        structure, material = self.shell(b_m=0.0)
        issues = preflight_structural_case(
            {"kind": "pressure", "magnitude_pa": 1000.0}, structure, material
        )
        self.assertIn("INVALID_STRUCTURE_DIMENSION", [item["code"] for item in issues])


class LocalizedPressureTests(unittest.TestCase):
    """Finding 7: localized pressure keeps the full-panel dispatch but
    emits an explicit assumption/limitation entry in the response."""

    def test_localized_pressure_emits_uniform_panel_assumption(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        load = {"kind": "pressure", "magnitude_pa": 5000.0, "distribution": "localized"}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "valid")
        uniform = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 5000.0)
        self.assertAlmostEqual(res.max_displacement_m, uniform.max_displacement_m, places=12)
        self.assertTrue(any("localized pressure approximated as uniform full-panel pressure" in item
                            for item in res.assumptions))
        self.assertNotIn(POINT_LOAD_SINGULARITY, res.flags)

    def test_localized_template_documents_limitation(self):
        template = MOUSE_LOAD_TEMPLATES["localized_pressure"]
        self.assertIn("limitations", template)
        self.assertIn("uniform full-panel pressure", template["limitations"])
        self.assertEqual(template["default_loads"]["distribution"], "localized")


class SolverMetadataPhysicsTests(unittest.TestCase):
    """Finding 6: structural results are marked as screening surrogates."""

    def test_shell_response_metadata(self):
        res = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        self.assertEqual(res.solver_metadata["model_id"], SCREENING_SURROGATE_MODEL_ID)
        self.assertEqual(res.solver_metadata["backend"], "surrogate_closed_form")
        payload = res.to_dict()
        self.assertEqual(payload["solver_metadata"]["model_id"], SCREENING_SURROGATE_MODEL_ID)

    def test_dispatch_response_metadata(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, material)
        self.assertEqual(res.solver_metadata["model_id"], SCREENING_SURROGATE_MODEL_ID)


if __name__ == "__main__":
    unittest.main()
