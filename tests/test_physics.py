import json
import math
import unittest

from mouse_sim.physics import (
    MOUSE_LOAD_TEMPLATES,
    POINT_LOAD_SINGULARITY,
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


if __name__ == "__main__":
    unittest.main()
