import json
import math
from dataclasses import replace
import unittest

import mouse_sim.physics as physics_module
from mouse_sim.errors import UnitError
from mouse_sim.materials import builtin_materials
from mouse_sim.physics import (
    INVALID_LOAD_UNITS,
    INVALID_LOAD_LOCATION,
    INVALID_LOAD_VALUE,
    INVALID_POISSON_RATIO,
    INVALID_TEMPERATURE_K,
    MISSING_BEAM_DIMENSION,
    MISSING_LOAD_MAGNITUDE,
    MISSING_ORTHOTROPIC_E2,
    MOUSE_LOAD_TEMPLATES,
    NUMERIC_OVERFLOW,
    POINT_LOAD_SINGULARITY,
    POINT_LOAD_STRESS_ORDER_DEPENDENT,
    SCREENING_SURROGATE_MODEL_ID,
    SERIES_NOT_CONVERGED,
    SMALL_DEFLECTION_VIOLATED,
    SOLVER_EVALUATION_ERROR,
    THIN_SHELL_OUT_OF_RANGE,
    UNDERCONSTRAINED_REACTIONS,
    UNKNOWN_BEAM_SUPPORT,
    UNKNOWN_LOAD_KIND,
    UNSUPPORTED_ANISOTROPY,
    UNSUPPORTED_STIFFNESS_REDUCTION,
    USAGE_TEMPERATURE_OUT_OF_RANGE,
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

    def test_safety_factor_uses_raw_stress_not_filtered(self):
        res = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0,
                                   allowable_pa=20e6)
        self.assertIsNotNone(res.safety_factor)
        self.assertAlmostEqual(res.safety_factor, 20e6 / res.max_stress_pa, places=12)
        self.assertNotAlmostEqual(res.safety_factor, 20e6 / res.max_stress_filtered_pa, places=12)
        self.assertEqual(res.safety_factor_status, "pass")

    def test_filtered_location_components_are_metres(self):
        a, b, t = 0.1, 0.1, 0.002
        res = shell_panel_response(a, b, t, 2e9, 0.35, 1000.0)
        x, y, z = res.filtered_location
        self.assertGreaterEqual(x, 0.0)
        self.assertLessEqual(x, a)
        self.assertGreaterEqual(y, 0.0)
        self.assertLessEqual(y, b)
        self.assertAlmostEqual(abs(z), t / 2.0, places=12)
        disp = res.max_displacement_location
        self.assertLessEqual(x, max(disp[0], disp[1], a, b))
        self.assertLessEqual(y, max(disp[0], disp[1], a, b))

    def test_square_panel_stress_series_not_converged_flag(self):
        res = shell_panel_response(0.1, 0.1, 0.001, 2.3e9, 0.35, 5000.0, series_order=3)
        self.assertIn(SERIES_NOT_CONVERGED, res.flags)
        self.assertEqual(res.validity, "approximate")

    def test_converged_square_panel_stress_has_no_series_flag(self):
        # 2 mm panel at 1 kPa: deflection is ~0.16% of the span, so both the
        # series convergence and the small-deflection validity hold.
        res = shell_panel_response(0.1, 0.1, 0.002, 2.3e9, 0.35, 1000.0, series_order=9)
        self.assertNotIn(SERIES_NOT_CONVERGED, res.flags)
        self.assertEqual(res.validity, "valid")

    def test_absurdly_thin_wall_downgrades_validity(self):
        # A 1 mm panel at 5 kPa deflects ~10% of its span: linear small-
        # deflection plate theory is violated by an order of magnitude, so
        # the response must be downgraded instead of presented as valid.
        res = shell_panel_response(0.1, 0.1, 0.001, 2.3e9, 0.35, 5000.0, series_order=9)
        self.assertEqual(res.validity, "approximate")
        self.assertIn(SMALL_DEFLECTION_VIOLATED, res.flags)

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
        # The audit found the headline "0.061%" claim lived only in prose
        # while the committed tolerance was 2% (70x the measured error of
        # ~0.003% at order 9 vs the 300-term reference).  Tighten to a
        # defensible bound: the series is converged to well under 0.05%.
        self.assertLessEqual(abs(order9.max_displacement_m - reference) / abs(reference), 0.0005)
        self.assertLessEqual(abs(order13.max_displacement_m - reference) / abs(reference), 0.0005)
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


class OrthotropicShellTests(unittest.TestCase):
    """Audit finding: the orthotropic Navier solver must reduce exactly to
    the isotropic one for D11=D22=D, D12=nu*D, D66=D*(1-nu)/2."""

    def test_orthotropic_reduces_to_isotropic(self):
        a, b, t = 0.1, 0.1, 0.002
        E, nu, p = 2e9, 0.35, 1000.0
        D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
        isotropic = shell_panel_response(a, b, t, E, nu, p, allowable_pa=20e6)
        orthotropic = shell_panel_response(
            a, b, t, E, nu, p, allowable_pa=20e6,
            D11=D, D12=nu * D, D22=D, D66=D * (1.0 - nu) / 2.0,
        )
        self.assertAlmostEqual(orthotropic.max_displacement_m, isotropic.max_displacement_m, places=12)
        self.assertAlmostEqual(orthotropic.max_stress_pa, isotropic.max_stress_pa, places=12)
        self.assertAlmostEqual(
            orthotropic.max_stress_filtered_pa, isotropic.max_stress_filtered_pa, places=12
        )
        self.assertAlmostEqual(orthotropic.safety_factor, isotropic.safety_factor, places=12)

    def test_fr4_anisotropic_panel_deflects_more_than_isotropic_run(self):
        # FR-4 in-plane E=22 GPa with G12=7 GPa gives D66 well below the
        # isotropic D(1-nu)/2, so the orthotropic panel is softer under
        # twisting-dominated bending: deflection increases (documented
        # direction of the anisotropy correction).
        fr4 = builtin_materials()["FR4"]
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        orthotropic = solve_load_case(load, structure, fr4)
        isotropic = shell_panel_response(
            0.1, 0.1, 0.002, fr4.properties.young_modulus.value_si,
            fr4.properties.poissons_ratio, 1000.0,
        )
        self.assertGreater(orthotropic.max_displacement_m, isotropic.max_displacement_m)
        self.assertEqual(orthotropic.validity, "valid")
        self.assertNotIn(UNSUPPORTED_ANISOTROPY, orthotropic.flags)

    def test_anisotropy_supported_without_directional_data_flagged(self):
        abs_mat = builtin_materials()["ABS"]
        flagged = replace(abs_mat, anisotropy_supported=True)
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, flagged)
        self.assertIn(UNSUPPORTED_ANISOTROPY, res.flags)
        self.assertEqual(res.validity, "approximate")
        self.assertTrue(any("anisotropic" in item for item in res.assumptions))
        self.assertTrue(any("under-predicts deflection" in item for item in res.assumptions))

    def test_anisotropic_material_on_beam_path_flagged(self):
        fr4 = builtin_materials()["FR4"]
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4,
                     "section_modulus_m3": 1e-6, "support": "cantilever"}
        res = solve_load_case({"kind": "force", "force_n": 10.0}, structure, fr4)
        self.assertIn(UNSUPPORTED_ANISOTROPY, res.flags)
        self.assertEqual(res.validity, "approximate")

    def directional_material(self, E2, E1=2.3e9, nu12=0.35):
        return {
            "young_modulus_pa": E1,
            "poissons_ratio": nu12,
            "young_modulus_transverse_pa": E2,
            "young_modulus_thickness_pa": E2,
            "shear_modulus_xy_pa": E1 / (2.0 * (1.0 + nu12)),
            "shear_modulus_thickness_pa": E1 / (2.0 * (1.0 + nu12)),
            "poissons_ratio_xy": nu12,
            "poissons_ratio_xz": nu12,
        }

    def test_halving_E2_changes_deflection_and_stress(self):
        # Audit finding S2: the orthotropic D22 must be built from E2, never
        # silently replaced by D11.  Halving E2 (same E1, G12, nu12) softens
        # the panel in the 2-direction: the exact full-CLT center-deflection
        # ratio is ~1.32 (+32%) for this case (the audit's ~50% estimate was
        # a rough single-term Navier bound); both documented directions:
        # deflection INCREASES, center stress DECREASES because D12 drops.
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        full = solve_load_case(load, structure, self.directional_material(2.3e9))
        half = solve_load_case(load, structure, self.directional_material(1.15e9))
        ratio = half.max_displacement_m / full.max_displacement_m
        self.assertGreater(ratio, 1.25)
        self.assertLess(ratio, 1.45)
        stress_ratio = half.max_stress_pa / full.max_stress_pa
        self.assertGreater(stress_ratio, 0.90)
        self.assertLess(stress_ratio, 0.97)
        self.assertEqual(full.validity, "valid")
        self.assertEqual(half.validity, "valid")
        self.assertNotIn(MISSING_ORTHOTROPIC_E2, half.flags)

    def test_missing_E2_with_anisotropy_supported_flags_new_constant(self):
        abs_mat = builtin_materials()["ABS"]
        flagged = replace(abs_mat, anisotropy_supported=True)
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, flagged)
        self.assertIn(MISSING_ORTHOTROPIC_E2, res.flags)
        self.assertIn(UNSUPPORTED_ANISOTROPY, res.flags)
        self.assertEqual(res.validity, "approximate")
        self.assertTrue(any("E2" in item and "missing" in item for item in res.assumptions))
        # The isotropic E1 fallback is explicit, never a silent E2=E1 claim.
        self.assertTrue(any("not assumed equal to E1" in item for item in res.assumptions))

    def test_partial_directional_data_without_E2_flags(self):
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35,
                    "shear_modulus_xy_pa": 8e8, "poissons_ratio_xy": 0.35}
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, material)
        self.assertIn(MISSING_ORTHOTROPIC_E2, res.flags)
        self.assertEqual(res.validity, "approximate")
        self.assertTrue(any("E2" in item and "missing" in item for item in res.assumptions))


class TemperatureDeratingTests(unittest.TestCase):
    """Audit finding: linear modulus/allowable derating above 293.15 K with
    per-material coefficients; disclosed, never silent."""

    def test_abs_at_80c_derates_modulus_and_allowable(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 353.15}
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        hot = solve_load_case(load, structure, builtin_materials()["ABS"])
        cold = solve_load_case(
            {"kind": "pressure", "magnitude_pa": 1000.0},
            {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002},
            builtin_materials()["ABS"],
        )
        self.assertEqual(hot.validity, "approximate")
        self.assertIn("temperature derating applied at T=353.15 K", hot.validity_reasons)
        self.assertTrue(any("linear temperature derating" in item and "353.15" in item
                            for item in hot.assumptions))
        self.assertLess(hot.safety_factor, cold.safety_factor)
        self.assertGreater(hot.max_displacement_m, cold.max_displacement_m)

    def test_no_derating_at_or_below_reference_temperature(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 293.15}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure,
                              builtin_materials()["ABS"])
        self.assertEqual(res.validity, "valid")
        self.assertFalse(any("derating" in item for item in res.assumptions))

    def test_usage_temperature_outside_continuous_use_range_reported(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 453.15}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure,
                              builtin_materials()["ABS"])
        self.assertEqual(res.validity, "approximate")
        self.assertIn("usage temperature outside continuous-use range", res.validity_reasons)

    def test_abs_at_600k_flagged_finite_never_crashes(self):
        # Audit finding S3: the linear derating factor went negative at
        # 600 K (E = -0.88 GPa) and solve_load_case raised "E_pa must be
        # positive" with no try/except.  The factor is now clamped at the
        # 0.1 floor and the dispatch never lets a ValueError escape.
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 600.0}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure,
                              builtin_materials()["ABS"])
        self.assertIsNotNone(res.max_displacement_m)
        self.assertTrue(math.isfinite(res.max_displacement_m))
        self.assertTrue(math.isfinite(res.max_stress_pa))
        self.assertEqual(res.validity, "approximate")
        self.assertIn("usage temperature outside continuous-use range", res.validity_reasons)
        self.assertNotIn(NUMERIC_OVERFLOW, res.flags)
        self.assertTrue(any("clamped at floor 0.1" in item for item in res.assumptions))

    def test_derating_factor_floor_plateau(self):
        # The floor temperature for ABS (k = 0.0045) is
        # 293.15 + (1 - 0.1)/0.0045 = 493.15 K; at and beyond it the factor
        # is pinned at 0.1, so 493.15 K and 600 K produce identical
        # stiffness.
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        at_floor = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                    "temperature_k": 493.15}
        beyond = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                  "temperature_k": 600.0}
        floor_res = solve_load_case(load, at_floor, builtin_materials()["ABS"])
        hot_res = solve_load_case(load, beyond, builtin_materials()["ABS"])
        self.assertAlmostEqual(floor_res.max_displacement_m, hot_res.max_displacement_m, places=9)
        self.assertAlmostEqual(floor_res.max_stress_pa, hot_res.max_stress_pa, places=9)

    def test_derating_skipped_reason_for_unknown_material(self):
        # Audit finding E5: a mapping payload without name/family/key has no
        # derating coefficient; the skip is disclosed instead of silent.
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 373.15}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, material)
        self.assertIn("temperature derating skipped", " ".join(res.validity_reasons))
        self.assertEqual(res.validity, "approximate")

    def test_dict_and_definition_derate_consistently(self):
        # Audit finding E5: the Mapping branch used to skip derating entirely
        # (name/family/material_key were never extracted), so preflight and
        # solve disagreed.  A flat dict carrying the catalog identity must
        # now derate exactly like the MaterialDefinition.
        definition = builtin_materials()["ABS"]
        as_dict = {
            "name": "ABS",
            "family": "thermoplastic",
            "young_modulus_pa": 2.3e9,
            "poissons_ratio": 0.35,
            "tensile_allowable_pa": 20e6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 363.15,
        }
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 373.15}
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        from_dict = solve_load_case(load, structure, as_dict)
        from_def = solve_load_case(load, structure, definition)
        self.assertAlmostEqual(from_dict.max_displacement_m, from_def.max_displacement_m, places=12)
        self.assertAlmostEqual(from_dict.max_stress_pa, from_def.max_stress_pa, places=12)
        self.assertAlmostEqual(from_dict.safety_factor, from_def.safety_factor, places=12)
        self.assertIn("temperature derating applied", " ".join(from_dict.validity_reasons))


class FeatureStressConcentrationTests(unittest.TestCase):
    """Audit finding: feature_peak_stress_pa is disclosed-only; the nominal
    max_stress_pa and safety factor stay unchanged."""

    def test_button_press_applies_kf_but_keeps_nominal_stress(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        load = {"kind": "force", "force_n": 5.0, "point_load": True}
        res = solve_load_case(load, structure, builtin_materials()["ABS"])
        k_f = 1.0 + 0.6 * (3.0 - 1.0)
        self.assertIsNotNone(res.feature_peak_stress_pa)
        self.assertAlmostEqual(res.feature_peak_stress_pa, res.max_stress_pa * k_f, places=12)
        self.assertTrue(any("stress-concentration K_f=2.2" in item for item in res.assumptions))
        direct = solve_load_case(load, structure, builtin_materials()["ABS"])
        self.assertEqual(res.max_stress_pa, direct.max_stress_pa)
        self.assertEqual(res.safety_factor, direct.safety_factor)

    def test_localized_pressure_applies_kf(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        load = {"kind": "pressure", "magnitude_pa": 5000.0, "distribution": "localized"}
        res = solve_load_case(load, structure, builtin_materials()["ABS"])
        k_f = 1.0 + 0.6 * (2.0 - 1.0)
        self.assertAlmostEqual(res.feature_peak_stress_pa, res.max_stress_pa * k_f, places=12)

    def test_uniform_pressure_has_no_feature_concentration(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure,
                              builtin_materials()["ABS"])
        self.assertIsNone(res.feature_peak_stress_pa)

    def test_feature_peak_stress_serializes_in_to_dict(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "force", "force_n": 5.0, "point_load": True},
                              structure, builtin_materials()["ABS"])
        data = res.to_dict()
        self.assertIn("feature_peak_stress_pa", data)
        self.assertIn("validity_reasons", data)
        json.dumps(data)


class WeldLineDisclosureTests(unittest.TestCase):
    """Audit finding: weld-line knockdown is disclosed as a secondary
    allowable, never silently re-verdicting the primary safety factor."""

    def test_weld_line_derated_allowable_disclosed(self):
        material = builtin_materials()["ABS"]
        welded = replace(material, properties=replace(material.properties, weld_line_factor=0.6))
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure, welded)
        allowable = material.properties.tensile_allowable.value_si
        self.assertEqual(res.weld_line_derated_allowable_pa, allowable * 0.6)
        self.assertTrue(any("weld-line strength knockdown" in item for item in res.assumptions))
        self.assertEqual(res.validity, "valid")
        plain = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0}, structure,
                                builtin_materials()["ABS"])
        self.assertEqual(res.safety_factor, plain.safety_factor)
        self.assertEqual(res.max_stress_pa, plain.max_stress_pa)


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


class NumericOverflowGuardTests(unittest.TestCase):
    """Degenerate magnitudes must produce a flagged inconclusive response,
    never a crash, NaN, or inf that would corrupt JSON serialization."""

    def _assert_clean_overflow(self, response, method_id):
        self.assertEqual(response.validity, "inconclusive")
        self.assertIn(NUMERIC_OVERFLOW, response.flags)
        self.assertEqual(response.method_id, method_id)
        text = json.dumps(response.to_dict())
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)

    def test_shell_tiny_dimensions_no_division_crash(self):
        response = shell_panel_response(1e-200, 1e-200, 1e-200, 2e9, 0.35, 1000.0)
        self._assert_clean_overflow(response, "shell_navier_v1")

    def test_shell_extreme_dimensions_no_nan_output(self):
        response = shell_panel_response(1e-160, 1e-160, 1e-160, 2e9, 0.35, 1000.0)
        self._assert_clean_overflow(response, "shell_navier_v1")

    def test_shell_huge_pressure_no_infinite_reactions(self):
        response = shell_panel_response(100.0, 100.0, 0.002, 2e9, 0.35, 1e308)
        self._assert_clean_overflow(response, "shell_navier_v1")

    def test_beam_extreme_lengths_no_crash(self):
        response = beam_response("cantilever_point", L_m=1e308, E_pa=1e-308, I_m4=1e-308,
                                 A_m2=1.0, nu=0.3, force_n=1e308)
        self._assert_clean_overflow(response, "beam_closed_form_v1")

    def test_beam_huge_uniform_load_no_infinite_reactions(self):
        response = beam_response("simply_supported_uniform", L_m=1e300, E_pa=1e-300,
                                 I_m4=1e-300, A_m2=1.0, nu=0.3, q_n_per_m=1e300)
        self._assert_clean_overflow(response, "beam_closed_form_v1")

    def test_shell_point_load_extreme_moment_flagged(self):
        response = solve_load_case(
            {"kind": "force", "force_n": 1e300, "point_load": True, "location": (0.05, 1e299)},
            {"type": "shell_panel", "a_m": 0.1, "b_m": 1e300, "t_m": 0.002},
            {"young_modulus_pa": 2e9, "poissons_ratio": 0.35},
        )
        self.assertIn(NUMERIC_OVERFLOW, response.flags)

    def test_shell_series_order_is_capped(self):
        response = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0, series_order=10 ** 9)
        self.assertEqual(response.validity, "valid")
        self.assertTrue(any("series order 49" in item for item in response.assumptions))

    def test_beam_rejects_invalid_poisson_ratio(self):
        with self.assertRaises(ValueError):
            beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                          A_m2=1e-4, nu=0.5, force_n=10.0)
        with self.assertRaises(ValueError):
            beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                          A_m2=1e-4, nu=5.0, force_n=10.0)
        with self.assertRaises(ValueError):
            beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                          A_m2=1e-4, nu=float("nan"), force_n=10.0)

    def test_nan_allowable_yields_not_available_safety(self):
        response = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0,
                                        allowable_pa=float("nan"))
        self.assertIsNone(response.safety_factor)
        self.assertEqual(response.safety_factor_status, "not_available")
        text = json.dumps(response.to_dict())
        self.assertNotIn("NaN", text)

    def test_beam_solve_invalid_width_flagged_not_crash(self):
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4}
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        for width in (float("nan"), 0.0, "not-a-number"):
            structure["width_m"] = width
            response = solve_load_case(
                {"kind": "pressure", "magnitude_pa": 1000.0}, structure, material
            )
            self.assertEqual(response.validity, "inconclusive", width)
            self.assertIn(INVALID_LOAD_VALUE, response.flags)

    def test_normal_magnitudes_still_valid(self):
        response = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        self.assertEqual(response.validity, "valid")
        self.assertNotIn(NUMERIC_OVERFLOW, response.flags)


class InteriorStressPeakTests(unittest.TestCase):
    """Audit finding S4: max_stress_pa is the interior 3x3 grid maximum; the
    corner twisting-moment von Mises value is a separate diagnostic."""

    def test_uniform_shell_reports_interior_peak_location(self):
        res = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        x, y, z = res.peak_location_m
        self.assertAlmostEqual(x, 0.05, places=6)
        self.assertAlmostEqual(y, 0.05, places=6)
        self.assertAlmostEqual(abs(z), 0.001, places=6)
        self.assertIsNotNone(res.corner_twisting_vm_pa)
        self.assertGreater(res.corner_twisting_vm_pa, res.max_stress_pa)

    def test_point_load_peak_location_is_interior(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        res = solve_load_case({"kind": "force", "force_n": 5.0, "point_load": True},
                              structure, material)
        x, y, z = res.peak_location_m
        self.assertGreaterEqual(x, 0.025)
        self.assertLessEqual(x, 0.075)
        self.assertGreaterEqual(y, 0.025)
        self.assertLessEqual(y, 0.075)
        self.assertIsNotNone(res.corner_twisting_vm_pa)

    def test_new_diagnostic_fields_serialize(self):
        shell = shell_panel_response(0.1, 0.1, 0.002, 2e9, 0.35, 1000.0)
        beam = beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                             A_m2=1e-4, nu=0.3, force_n=10.0)
        for response in (shell, beam):
            data = response.to_dict()
            self.assertIn("max_shear_pa", data)
            self.assertIn("corner_twisting_vm_pa", data)
            self.assertIn("peak_location_m", data)
            json.dumps(data)


class PointLoadOrderDependenceTests(unittest.TestCase):
    """Audit finding S1: point-load stress is series-truncation dependent and
    must be disclosed as unsuitable for acceptance."""

    def test_point_load_stress_order_dependent_flag_and_assumption(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        res = solve_load_case({"kind": "force", "force_n": 5.0, "point_load": True},
                              structure, material)
        self.assertIn(POINT_LOAD_SINGULARITY, res.flags)
        self.assertIn(POINT_LOAD_STRESS_ORDER_DEPENDENT, res.flags)
        self.assertTrue(any(
            "order-truncation dependent" in item and "not suitable for acceptance" in item
            for item in res.assumptions
        ))
        self.assertEqual(res.validity, "approximate")

    def test_point_load_preflight_carries_order_caveat(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        issues = preflight_structural_case(
            {"kind": "force", "force_n": 5.0, "point_load": True}, structure, material
        )
        codes = [item["code"] for item in issues]
        self.assertIn(POINT_LOAD_SINGULARITY, codes)
        self.assertIn(POINT_LOAD_STRESS_ORDER_DEPENDENT, codes)


class BeamDisclosureTests(unittest.TestCase):
    """Audit findings S5/S6: max_shear_pa, the non-co-located VM caveat, and
    support/location disclosure for beams."""

    def beam_structure(self, **overrides):
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4,
                     "section_modulus_m3": 1e-6, "support": "cantilever"}
        structure.update(overrides)
        return structure

    def test_beam_max_shear_matches_analytic(self):
        res = beam_response("simply_supported_uniform", L_m=0.1, E_pa=200e9,
                            I_m4=1e-8, A_m2=1e-4, nu=0.3, q_n_per_m=100.0)
        expected = 1.5 * (100.0 * 0.1 / 2.0) / 1e-4
        self.assertAlmostEqual(res.max_shear_pa, expected, places=12)

    def test_beam_vm_non_colocated_assumption(self):
        res = beam_response("cantilever_point", L_m=0.1, E_pa=200e9, I_m4=1e-8,
                            A_m2=1e-4, nu=0.3, force_n=10.0)
        self.assertTrue(any("non-co-located" in item for item in res.assumptions))
        self.assertTrue(any("max_shear_pa reports" in item for item in res.assumptions))

    def test_support_missing_defaults_to_cantilever_with_assumption(self):
        structure = self.beam_structure()
        del structure["support"]
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        res = solve_load_case({"kind": "force", "force_n": 10.0}, structure, material)
        q = 10.0 / 0.1
        expected = q * 0.1 ** 4 / (8.0 * 200e9 * 1e-8)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=12)
        self.assertTrue(any(
            "support unspecified: defaulted to cantilever" in item for item in res.assumptions
        ))
        # The same disclosure applies on the pressure path.
        pressure_structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4,
                              "section_modulus_m3": 1e-6, "width_m": 0.05}
        pressure = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0},
                                   pressure_structure, material)
        self.assertTrue(any(
            "support unspecified: defaulted to cantilever" in item
            for item in pressure.assumptions
        ))

    def test_ss_beam_point_load_location_honored(self):
        structure = self.beam_structure(support="simply_supported")
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        x0 = 0.03
        load = {"kind": "force", "force_n": 10.0, "point_load": True, "location": (x0, 0.0)}
        res = solve_load_case(load, structure, material)
        b = 0.1 - x0
        expected = 10.0 * x0 * x0 * b * b / (3.0 * 200e9 * 1e-8 * 0.1)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=12)
        self.assertAlmostEqual(res.reactions["R1"], 10.0 * b / 0.1, places=12)
        self.assertAlmostEqual(res.reactions["R2"], 10.0 * x0 / 0.1, places=12)
        self.assertTrue(any("x0 = 0.03" in item for item in res.assumptions))

    def test_cantilever_beam_point_load_location_honored(self):
        structure = self.beam_structure(support="cantilever")
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        load = {"kind": "force", "force_n": 10.0, "point_load": True, "location": 0.04}
        res = solve_load_case(load, structure, material)
        expected = 10.0 * 0.04 ** 3 / (3.0 * 200e9 * 1e-8)
        self.assertAlmostEqual(res.max_displacement_m, expected, places=12)
        self.assertAlmostEqual(res.reactions["M1"], -10.0 * 0.04, places=12)
        self.assertAlmostEqual(res.reactions["R1"], 10.0, places=12)

    def test_beam_point_load_default_location_disclosed(self):
        structure = self.beam_structure(support="cantilever")
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        res = solve_load_case({"kind": "force", "force_n": 10.0, "point_load": True},
                              structure, material)
        self.assertTrue(any("free tip" in item and "no location specified" in item
                            for item in res.assumptions))

    def test_beam_point_load_location_out_of_span_rejected(self):
        structure = self.beam_structure(support="simply_supported")
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        load = {"kind": "force", "force_n": 10.0, "point_load": True, "location": (0.2, 0.0)}
        res = solve_load_case(load, structure, material)
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn(INVALID_LOAD_LOCATION, res.flags)


class PreflightBeamAndLoadTests(unittest.TestCase):
    """Audit finding S7: preflight mirrors solve_load_case validation."""

    def test_preflight_beam_missing_dims_produce_issues(self):
        structure = {"type": "beam", "L_m": 0.1, "support": "cantilever"}
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        issues = preflight_structural_case({"kind": "force", "force_n": 10.0},
                                           structure, material)
        codes = [item["code"] for item in issues]
        self.assertIn(MISSING_BEAM_DIMENSION, codes)
        messages = " ".join(item["message"] for item in issues)
        self.assertIn("I_m4", messages)
        self.assertIn("A_m2", messages)
        self.assertEqual(issues[0]["severity"], "error")

    def test_preflight_beam_pressure_requires_width(self):
        structure = {"type": "beam", "L_m": 0.1, "I_m4": 1e-8, "A_m2": 1e-4}
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        issues = preflight_structural_case({"kind": "pressure", "magnitude_pa": 1000.0},
                                           structure, material)
        codes = [item["code"] for item in issues]
        self.assertIn(MISSING_BEAM_DIMENSION, codes)
        self.assertIn("width_m", " ".join(item["message"] for item in issues))

    def test_preflight_mapping_magnitude_accepted(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        issues = preflight_structural_case(
            {"kind": "pressure", "magnitude_pa": {"value": 1.0, "unit": "kPa"}},
            structure, material,
        )
        codes = [item["code"] for item in issues]
        self.assertNotIn(MISSING_LOAD_MAGNITUDE, codes)
        self.assertNotIn(INVALID_LOAD_UNITS, codes)

    def test_preflight_unknown_load_kind_issue(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        issues = preflight_structural_case({"kind": "shear", "force_n": 1.0},
                                           structure, material)
        self.assertIn(UNKNOWN_LOAD_KIND, [item["code"] for item in issues])

    def test_preflight_temperature_out_of_range_issue(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 453.15}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35, "name": "ABS",
                    "continuous_use_temperature_min_k": 233.15,
                    "continuous_use_temperature_max_k": 363.15}
        issues = preflight_structural_case({"kind": "pressure", "magnitude_pa": 1000.0},
                                           structure, material)
        self.assertIn(USAGE_TEMPERATURE_OUT_OF_RANGE, [item["code"] for item in issues])


class DistinctFlagEmissionTests(unittest.TestCase):
    """Audit finding F11: the overloaded UNSUPPORTED_STIFFNESS_REDUCTION flag
    is supplemented by distinct constants, both emitted for compatibility."""

    def test_missing_beam_dimension_emits_both_flags(self):
        structure = {"type": "beam", "L_m": 0.1, "A_m2": 1e-4}
        material = {"young_modulus_pa": 200e9, "poissons_ratio": 0.3}
        res = solve_load_case({"kind": "force", "force_n": 10.0}, structure, material)
        self.assertIn(MISSING_BEAM_DIMENSION, res.flags)
        self.assertIn(UNSUPPORTED_STIFFNESS_REDUCTION, res.flags)
        self.assertEqual(res.validity, "inconclusive")

    def test_missing_load_magnitude_emits_both_flags(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        res = solve_load_case({"kind": "pressure"}, structure, material)
        self.assertIn(MISSING_LOAD_MAGNITUDE, res.flags)
        self.assertIn(UNSUPPORTED_STIFFNESS_REDUCTION, res.flags)

    def test_unknown_load_kind_emits_both_flags(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        res = solve_load_case({"kind": "shear", "force_n": 1.0}, structure, material)
        self.assertIn(UNKNOWN_LOAD_KIND, res.flags)
        self.assertIn(UNSUPPORTED_STIFFNESS_REDUCTION, res.flags)
        self.assertEqual(res.validity, "inconclusive")


class DeterminismTests(unittest.TestCase):
    """Outputs must be byte-deterministic across repeated runs."""

    def test_solve_load_case_outputs_identical_across_runs(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 600.0}
        material = builtin_materials()["ABS"]
        load = {"kind": "force", "force_n": 5.0, "point_load": True}
        first = solve_load_case(load, structure, material).to_dict()
        second = solve_load_case(load, structure, material).to_dict()
        self.assertEqual(first, second)


class NeverCrashContractTests(unittest.TestCase):
    """Audit finding S3: solve_load_case must never let a ValueError escape."""

    def test_injected_solver_failure_converts_to_flagged_inconclusive(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        material = {"young_modulus_pa": 2e9, "poissons_ratio": 0.35}
        original = physics_module.shell_panel_response

        def boom(*args, **kwargs):
            raise ValueError("injected dispatch failure")

        physics_module.shell_panel_response = boom
        try:
            res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0},
                                  structure, material)
        finally:
            physics_module.shell_panel_response = original
        self.assertEqual(res.validity, "inconclusive")
        self.assertIn(SOLVER_EVALUATION_ERROR, res.flags)
        self.assertTrue(any("injected dispatch failure" in item for item in res.assumptions))
        json.dumps(res.to_dict())


class VerificationRegressionTests(unittest.TestCase):
    """Regression tests for the independent verification pass (physics.py)."""

    def _beam_structure(self, **overrides):
        structure = {
            "type": "beam",
            "L_m": 0.1,
            "I_m4": 1e-11,
            "A_m2": 1e-4,
            "section_modulus_m3": 1e-6,
        }
        structure.update(overrides)
        return structure

    def test_material_definition_with_none_properties_never_crashes(self):
        # Verification MEDIUM finding: MaterialDefinition(properties=None)
        # raised AttributeError out of solve_load_case and preflight,
        # violating the never-crash contract.
        definition = replace(builtin_materials()["ABS"], properties=None)
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0},
                              structure, definition)
        self.assertIn(UNSUPPORTED_STIFFNESS_REDUCTION, res.flags)
        issues = preflight_structural_case({"kind": "pressure", "magnitude_pa": 1000.0},
                                           structure, definition)
        codes = [issue["code"] for issue in issues]
        self.assertIn(UNSUPPORTED_STIFFNESS_REDUCTION, codes)

    def test_material_snapshot_nested_properties_unwrapped(self):
        # A MaterialDefinition.to_dict() snapshot nests the property payload
        # under "properties" with Quantity encodings; the Mapping branch must
        # unwrap it so stiffness and derating are identical to the definition.
        definition = builtin_materials()["ABS"]
        snapshot = definition.to_dict()
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": 373.15}
        load = {"kind": "pressure", "magnitude_pa": 1000.0}
        from_snapshot = solve_load_case(load, structure, snapshot)
        from_definition = solve_load_case(load, structure, definition)
        self.assertNotIn(UNSUPPORTED_STIFFNESS_REDUCTION, from_snapshot.flags)
        self.assertAlmostEqual(
            from_snapshot.max_displacement_m, from_definition.max_displacement_m, places=12
        )

    def test_missing_section_modulus_flagged_not_silent(self):
        structure = self._beam_structure()
        del structure["section_modulus_m3"]
        res = solve_load_case({"kind": "force", "force_n": 1.0}, structure,
                              {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertIn(MISSING_BEAM_DIMENSION, res.flags)
        self.assertTrue(any("section_modulus_m3 missing" in item for item in res.assumptions))
        issues = preflight_structural_case({"kind": "force", "force_n": 1.0}, structure,
                                           {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertTrue(any(issue["code"] == MISSING_BEAM_DIMENSION for issue in issues))

    def test_non_numeric_temperature_flagged_not_silent(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002,
                     "temperature_k": "hot"}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": 1000.0},
                              structure, {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertIn(INVALID_TEMPERATURE_K, res.flags)
        self.assertTrue(any("derating skipped" in item for item in res.assumptions))
        issues = preflight_structural_case({"kind": "pressure", "magnitude_pa": 1000.0},
                                           structure, {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertTrue(any(issue["code"] == INVALID_TEMPERATURE_K for issue in issues))

    def test_magnitude_with_unit_but_no_value_is_missing(self):
        structure = {"type": "shell_panel", "a_m": 0.1, "b_m": 0.1, "t_m": 0.002}
        res = solve_load_case({"kind": "pressure", "magnitude_pa": {"unit": "Pa"}},
                              structure, {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertIn(MISSING_LOAD_MAGNITUDE, res.flags)

    def test_unknown_beam_support_disclosed(self):
        structure = self._beam_structure(support="hinged")
        res = solve_load_case({"kind": "force", "force_n": 1.0}, structure,
                              {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertIn(UNKNOWN_BEAM_SUPPORT, res.flags)
        self.assertTrue(any("hinged" in item for item in res.assumptions))
        issues = preflight_structural_case({"kind": "force", "force_n": 1.0}, structure,
                                           {"young_modulus_pa": 2e9, "poissons_ratio": 0.35})
        self.assertTrue(any(issue["code"] == UNKNOWN_BEAM_SUPPORT for issue in issues))

    def test_single_sided_temperature_range_enforced(self):
        # Only the max continuous-use bound present; T beyond it must flag.
        from mouse_sim.physics import _material_props, _usage_temperature_out_of_range
        _, _, _, info = _material_props({
            "young_modulus_pa": 2e9, "poissons_ratio": 0.35,
            "continuous_use_temperature_max_k": 343.15,
        }, 353.15)
        self.assertTrue(_usage_temperature_out_of_range(info))


if __name__ == "__main__":
    unittest.main()
