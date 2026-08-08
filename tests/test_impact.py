import json
import math
import unittest

from mouse_sim.impact import (
    CONTACT_MODEL_HERTZ_NONLINEAR,
    CONTACT_MODEL_LINEAR,
    CONTACT_MODEL_LINEAR_CALIBRATED,
    CONTACT_MODEL_STOPPING_DISTANCE,
    CONTACT_PATCH_ASSUMPTION,
    DESK_EDGE_CONTACT_APPROXIMATION,
    FATIGUE_ESTIMATE_EXCEEDED,
    FATIGUE_GENERIC_FALLBACK,
    HERTZ_CONTACT_DURATION_FACTOR,
    IMPACT_ACCELERATION_IMPLAUSIBLE,
    IMPACT_STRESS_IMPLAUSIBLE,
    INVALID_CONTACT_OFFSET,
    INVALID_INERTIA_TENSOR,
    INVALID_KINEMATICS,
    INVALID_LOAD_PATH,
    INVALID_MASS,
    INVALID_RESTITUTION,
    INVALID_STIFFNESS,
    INSUFFICIENT_PARAMETERS,
    PEAK_FORCE_NOT_ESTIMATED,
    SCREENING_SURROGATE_MODEL_ID,
    UNSUPPORTED_BATTERY_CRUSH,
    desk_edge_impact,
    estimate_impact,
    impact_qualification_status,
    repeat_impact_cycles,
)


class NoImpactTests(unittest.TestCase):
    def test_zero_closing_velocity_no_impact(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=0.0)
        self.assertEqual(result.validity, "no_impact")
        self.assertEqual(result.impact_energy_j, 0.0)
        self.assertEqual(result.impulse_n_s, 0.0)
        self.assertEqual(result.peak_force_n, 0.0)
        self.assertEqual(result.closing_velocity_m_s, 0.0)
        self.assertTrue(result.qualification_blocked)

    def test_zero_fall_height_no_impact(self):
        result = estimate_impact(mass_kg=0.1, fall_height_m=0.0)
        self.assertEqual(result.validity, "no_impact")
        self.assertEqual(result.impact_energy_j, 0.0)


class KinematicsTests(unittest.TestCase):
    def test_energy_from_fall_height(self):
        result = estimate_impact(mass_kg=0.1, fall_height_m=1.0)
        self.assertAlmostEqual(result.closing_velocity_m_s, math.sqrt(2.0 * 9.80665), places=9)
        self.assertAlmostEqual(result.impact_energy_j, 0.1 * 9.80665 * 1.0, places=9)

    def test_impulse_formula_with_restitution(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, restitution=0.5)
        self.assertAlmostEqual(result.impulse_n_s, 0.1 * 1.5 * 4.0, places=9)
        self.assertAlmostEqual(result.effective_mass_kg, 0.1)

    def test_reduced_mass_with_target(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, target_mass_kg=0.1)
        self.assertAlmostEqual(result.effective_mass_kg, 0.05)
        self.assertAlmostEqual(result.impulse_n_s, 0.05 * 4.0)

    def test_analytic_fall_reference_values(self):
        fall = estimate_impact(mass_kg=0.1, fall_height_m=1.0)
        velocity = math.sqrt(2.0 * 9.80665 * 1.0)
        self.assertAlmostEqual(fall.closing_velocity_m_s, velocity, places=12)
        self.assertAlmostEqual(fall.impact_energy_j, 0.5 * 0.1 * velocity * velocity, places=12)
        bouncing = estimate_impact(mass_kg=0.1, fall_height_m=1.0, restitution=0.5)
        self.assertAlmostEqual(bouncing.impulse_n_s, 0.1 * 1.5 * velocity, places=12)
        spring = estimate_impact(mass_kg=0.1, fall_height_m=1.0, contact_stiffness_n_per_m=1e5)
        self.assertAlmostEqual(spring.peak_force_n, velocity * math.sqrt(0.1 * 1e5), places=9)
        duration = math.pi * math.sqrt(0.1 / 1e5) / 2.0
        self.assertAlmostEqual(spring.contact_duration_s, duration, places=12)
        half_sine = math.pi * (0.1 * velocity) / (2.0 * spring.contact_duration_s)
        self.assertLess(abs(half_sine - spring.peak_force_n) / spring.peak_force_n, 1e-6)


class ForceTests(unittest.TestCase):
    def test_spring_peak_force_formula(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertAlmostEqual(result.peak_force_n, 4.0 * math.sqrt(0.1 * 1e5), places=9)
        self.assertAlmostEqual(result.contact_compression_m, 4.0 * math.sqrt(0.1 / 1e5), places=9)
        self.assertAlmostEqual(result.peak_acceleration_m_s2, result.peak_force_n / 0.1, places=9)

    def test_contact_duration_consistency(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        expected_duration = math.pi * math.sqrt(0.1 / 1e5) / 2.0
        self.assertAlmostEqual(result.contact_duration_s, expected_duration, places=12)
        half_sine_force = math.pi * result.impulse_n_s / (2.0 * result.contact_duration_s)
        self.assertLess(abs(half_sine_force - result.peak_force_n) / result.peak_force_n, 1e-6)

    def test_stopping_distance_fallback(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, stopping_distance_m=0.05)
        energy = 0.5 * 0.1 * 4.0 * 4.0
        self.assertAlmostEqual(result.peak_force_n, energy / 0.05, places=9)
        self.assertEqual(result.contact_compression_m, 0.05)
        self.assertAlmostEqual(result.contact_duration_s, 2.0 * 0.05 / 4.0, places=9)

    def test_half_sine_duration_fallback(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_duration_s=0.01)
        self.assertAlmostEqual(result.peak_force_n, math.pi * 0.1 * 4.0 / (2.0 * 0.01), places=9)


class PeakAccelerationTests(unittest.TestCase):
    def test_peak_acceleration_is_falling_body_deceleration(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertAlmostEqual(result.peak_acceleration_m_s2, result.peak_force_n / 0.1, places=9)

    def test_peak_acceleration_uses_body_mass_not_reduced_mass(self):
        # Equal masses: the two-body relative acceleration (F/m_eff) would
        # overstate the falling body's deceleration by 2x.
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5, target_mass_kg=0.1
        )
        self.assertAlmostEqual(result.effective_mass_kg, 0.05, places=9)
        self.assertAlmostEqual(result.peak_acceleration_m_s2, result.peak_force_n / 0.1, places=9)
        relative = result.peak_force_n / result.effective_mass_kg
        self.assertAlmostEqual(result.peak_acceleration_m_s2, relative / 2.0, places=9)


class ContactDurationAssumptionTests(unittest.TestCase):
    def test_compression_phase_duration_note_on_linear_branch(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertTrue(
            any("compression phase" in item and "full contact" in item for item in result.assumptions)
        )

    def test_compression_phase_duration_note_on_half_sine_branch(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_duration_s=0.01)
        self.assertTrue(
            any("compression phase" in item and "full contact" in item for item in result.assumptions)
        )


class PlausibilityFlagTests(unittest.TestCase):
    def test_absurd_stiffness_flags_implausible_acceleration(self):
        # Tiny body on an absurdly stiff contact: deceleration >> 1e6 m/s^2.
        result = estimate_impact(mass_kg=1e-6, velocity_m_s=4.0, contact_stiffness_n_per_m=1e8)
        self.assertGreater(result.peak_acceleration_m_s2, 1e6)
        self.assertIn(IMPACT_ACCELERATION_IMPLAUSIBLE, result.flags)
        self.assertEqual(result.validity, "inconclusive")
        self.assertTrue(
            any(IMPACT_ACCELERATION_IMPLAUSIBLE in item for item in result.assumptions)
        )

    def test_absurd_stress_flags_implausible_stress(self):
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e8,
            load_path_area_m2=1e-10,
        )
        self.assertGreater(result.load_path_stress_pa, 1e11)
        self.assertIn(IMPACT_STRESS_IMPLAUSIBLE, result.flags)
        self.assertEqual(result.validity, "inconclusive")
        self.assertTrue(any(IMPACT_STRESS_IMPLAUSIBLE in item for item in result.assumptions))

    def test_plausible_impacts_carry_no_implausibility_flags(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertNotIn(IMPACT_ACCELERATION_IMPLAUSIBLE, result.flags)
        self.assertNotIn(IMPACT_STRESS_IMPLAUSIBLE, result.flags)
        self.assertEqual(result.validity, "valid")


class ValidationTests(unittest.TestCase):
    def test_nonpositive_mass_error(self):
        result = estimate_impact(mass_kg=0.0, velocity_m_s=1.0)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_MASS, result.flags)
        result = estimate_impact(mass_kg=-1.0, velocity_m_s=1.0)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_MASS, result.flags)

    def test_missing_kinematics_failed(self):
        result = estimate_impact(mass_kg=0.1)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)

    def test_negative_drop_inputs_fail_instead_of_becoming_no_impact(self):
        negative_height = estimate_impact(mass_kg=0.1, fall_height_m=-0.01)
        self.assertEqual(negative_height.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, negative_height.flags)

        negative_velocity = estimate_impact(mass_kg=0.1, velocity_m_s=-1.0)
        self.assertEqual(negative_velocity.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, negative_velocity.flags)

    def test_missing_force_model_is_inconclusive_not_valid_zero_force(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0)
        self.assertEqual(result.validity, "inconclusive")
        self.assertIn(PEAK_FORCE_NOT_ESTIMATED, result.flags)
        self.assertIn(INSUFFICIENT_PARAMETERS, result.flags)
        self.assertEqual(result.peak_force_n, 0.0)

    def test_restitution_bound_error(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=1.0, restitution=-0.1)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_RESTITUTION, result.flags)
        result = estimate_impact(mass_kg=0.1, velocity_m_s=1.0, restitution=1.1)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_RESTITUTION, result.flags)

    def test_unsupported_failure_modes_always_listed(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=1.0)
        self.assertIn(UNSUPPORTED_BATTERY_CRUSH, result.unsupported_failure_modes)
        self.assertEqual(len(result.unsupported_failure_modes), 5)
        failed = estimate_impact(mass_kg=0.0, velocity_m_s=1.0)
        self.assertIn(UNSUPPORTED_BATTERY_CRUSH, failed.unsupported_failure_modes)


class StressTests(unittest.TestCase):
    def test_load_path_stress_and_safety_factor(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5,
            load_path_area_m2=1e-4, allowable_pa=2e6,
        )
        force = 4.0 * math.sqrt(0.1 * 1e5)
        self.assertAlmostEqual(result.load_path_stress_pa, force / 1e-4, places=9)
        self.assertAlmostEqual(result.safety_factor, 2e6 / (force / 1e-4), places=9)

    def test_safety_factor_not_available_without_allowable(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5, load_path_area_m2=1e-4
        )
        self.assertEqual(result.safety_factor, "not_available")

    def test_bending_stress_combined(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5,
            load_path_area_m2=1e-4, load_path_lever_arm_m=0.01, section_modulus_m3=1e-7,
        )
        force = 4.0 * math.sqrt(0.1 * 1e5)
        self.assertAlmostEqual(
            result.load_path_stress_pa, force / 1e-4 + force * 0.01 / 1e-7, places=6
        )


class FatigueTests(unittest.TestCase):
    def test_miner_rule_exceeded(self):
        summary = repeat_impact_cycles(cycles_n=1000, stress_amplitude_pa=1e8)
        self.assertIn(FATIGUE_ESTIMATE_EXCEEDED, summary["flags"])
        self.assertTrue(summary["miner_exceeded"])
        self.assertIn("coarse screening estimate", summary["label"])

    def test_miner_rule_curve_exact_match(self):
        curve = {1e6: 1e4, 1e7: 1e2}
        summary = repeat_impact_cycles(cycles_n=2000, stress_amplitude_pa=1e6, s_n_curve=curve)
        self.assertAlmostEqual(summary["damage_sum"], 2000.0 / 1e4)
        self.assertFalse(summary["miner_exceeded"])

    def test_miner_rule_multi_level_exceed(self):
        summary = repeat_impact_cycles(
            [(1000, 1e6), (10, 1e7)], s_n_curve={1e6: 1e3, 1e7: 10}
        )
        self.assertAlmostEqual(summary["damage_sum"], 2.0)
        self.assertTrue(summary["miner_exceeded"])

    def test_generic_fallback_law_matches_hand_computed_life(self):
        # Generic polymer law N = 1e6*(14e6/sigma)^6 (ABS-like compilation,
        # R ~ 0.1): at sigma = 14e6 the life is exactly 1e6; at 28e6 it is
        # 1e6/2^6 = 15625.
        at_ref = repeat_impact_cycles(cycles_n=1, stress_amplitude_pa=14e6)
        self.assertEqual(at_ref["levels"][0]["cycles_to_failure"], 1e6)
        doubled = repeat_impact_cycles(cycles_n=1, stress_amplitude_pa=28e6)
        self.assertEqual(doubled["levels"][0]["cycles_to_failure"], 15625.0)
        self.assertAlmostEqual(doubled["damage_sum"], 1.0 / 15625.0)

    def test_generic_fallback_flag_and_assumption(self):
        summary = repeat_impact_cycles(cycles_n=1000, stress_amplitude_pa=1e8)
        self.assertIn(FATIGUE_GENERIC_FALLBACK, summary["flags"])
        self.assertTrue(
            any("14 MPa @ 10^6" in item and "slope 6" in item for item in summary["assumptions"])
        )
        self.assertTrue(
            any("material-specific S-N data unavailable" in item for item in summary["assumptions"])
        )
        with_material = repeat_impact_cycles(
            cycles_n=1000,
            stress_amplitude_pa=1e8,
            fatigue_strength_at_1e6_pa=20e6,
            fatigue_exponent_k=7,
        )
        self.assertNotIn(FATIGUE_GENERIC_FALLBACK, with_material["flags"])
        self.assertEqual(with_material["assumptions"], [])

    def test_material_basquin_laws_exact_lives(self):
        # Per-material S-N data (polymer fatigue compilations, R ~ 0.1):
        # ABS 14 MPa @ 1e6, slope 6; PC 20 MPa @ 1e6, slope 7;
        # POM 30 MPa @ 1e6, slope 9; FR-4 100 MPa @ 1e6, slope 8.
        # N = 1e6*(sigma_ref/sigma)^k, hand-computed below.
        materials = (
            ("ABS", 14e6, 6, 1e6 / 64.0),      # at 2*sigma_ref: 15625.0
            ("PC", 20e6, 7, 1e6 / 128.0),      # 7812.5
            ("POM", 30e6, 9, 1e6 / 512.0),     # 1953.125
            ("FR-4", 100e6, 8, 1e6 / 256.0),   # 3906.25
        )
        for name, sigma_ref, exponent, expected_at_doubled in materials:
            at_ref = repeat_impact_cycles(
                cycles_n=1,
                stress_amplitude_pa=sigma_ref,
                fatigue_strength_at_1e6_pa=sigma_ref,
                fatigue_exponent_k=exponent,
            )
            self.assertEqual(
                at_ref["levels"][0]["cycles_to_failure"], 1e6, name
            )
            self.assertAlmostEqual(at_ref["damage_sum"], 1e-6, places=12, msg=name)
            doubled = repeat_impact_cycles(
                cycles_n=1,
                stress_amplitude_pa=2.0 * sigma_ref,
                fatigue_strength_at_1e6_pa=sigma_ref,
                fatigue_exponent_k=exponent,
            )
            self.assertEqual(doubled["levels"][0]["cycles_to_failure"], expected_at_doubled, name)
            self.assertFalse(doubled["miner_exceeded"], name)
            self.assertNotIn(FATIGUE_GENERIC_FALLBACK, doubled["flags"], name)

    def test_partial_material_data_falls_back_to_generic(self):
        only_ref = repeat_impact_cycles(
            cycles_n=1, stress_amplitude_pa=14e6, fatigue_strength_at_1e6_pa=20e6
        )
        self.assertIn(FATIGUE_GENERIC_FALLBACK, only_ref["flags"])
        only_k = repeat_impact_cycles(
            cycles_n=1, stress_amplitude_pa=14e6, fatigue_exponent_k=7
        )
        self.assertIn(FATIGUE_GENERIC_FALLBACK, only_k["flags"])

    def test_epsilon_exactly_one_lifetime_triggers_exhaustion(self):
        # At the stress where the Basquin law gives N = 1000 cycles, running
        # exactly 1000 cycles gives damage_sum == 1.0; float rounding may
        # land a hair below 1.0, so the 1e-9 epsilon must still flag it.
        sigma_ref = 14e6
        exponent = 6
        stress = sigma_ref * (1e6 / 1000.0) ** (1.0 / exponent)
        summary = repeat_impact_cycles(
            cycles_n=1000,
            stress_amplitude_pa=stress,
            fatigue_strength_at_1e6_pa=sigma_ref,
            fatigue_exponent_k=exponent,
        )
        self.assertAlmostEqual(summary["levels"][0]["cycles_to_failure"], 1000.0, places=6)
        self.assertAlmostEqual(summary["damage_sum"], 1.0, places=9)
        self.assertTrue(summary["miner_exceeded"])
        self.assertIn(FATIGUE_ESTIMATE_EXCEEDED, summary["flags"])

    def test_damage_below_one_lifetime_not_exceeded(self):
        summary = repeat_impact_cycles(
            cycles_n=999,
            stress_amplitude_pa=14e6 * (1e6 / 1000.0) ** (1.0 / 6),
            fatigue_strength_at_1e6_pa=14e6,
            fatigue_exponent_k=6,
        )
        self.assertFalse(summary["miner_exceeded"])
        self.assertEqual(summary["flags"], [])


class QualificationTests(unittest.TestCase):
    def test_qualification_blocked_by_default(self):
        status = impact_qualification_status()
        self.assertFalse(status["qualified"])
        self.assertEqual(status["disposition"], "qualification_blocked")
        self.assertIn("blocked", status["reason"])

    def test_requires_validation_even_with_approved_method(self):
        method = {"approved_for_qualification": True, "approval_state": "approved"}
        status = impact_qualification_status(method=method)
        self.assertFalse(status["qualified"])
        self.assertEqual(status["disposition"], "qualification_blocked")

    def test_qualified_with_validation_and_no_method(self):
        status = impact_qualification_status(validated=True)
        self.assertTrue(status["qualified"])
        self.assertEqual(status["disposition"], "qualification_pending_review")

    def test_qualified_only_when_approved_and_validated(self):
        method = {"approved_for_qualification": True, "approval_state": "approved"}
        self.assertTrue(impact_qualification_status(method=method, validated=True)["qualified"])
        unapproved = {"approved_for_qualification": False, "approval_state": "draft"}
        status = impact_qualification_status(method=unapproved, validated=True)
        self.assertFalse(status["qualified"])
        self.assertIn("method", status["reason"])


class DeskEdgeTests(unittest.TestCase):
    def test_desk_edge_flag_present(self):
        result = desk_edge_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005)
        self.assertIn(DESK_EDGE_CONTACT_APPROXIMATION, result.flags)
        self.assertIn(CONTACT_PATCH_ASSUMPTION, result.flags)
        self.assertGreater(result.peak_force_n, 0.0)

    def test_desk_edge_invalid_radius_failed(self):
        result = desk_edge_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.0)
        self.assertEqual(result.validity, "failed")

    def test_desk_edge_passes_through_other_kwargs(self):
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005, target_mass_kg=0.5
        )
        self.assertAlmostEqual(result.effective_mass_kg, 0.1 * 0.5 / 0.6)


class DictTests(unittest.TestCase):
    def test_to_dict_keys(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        payload = result.to_dict()
        self.assertEqual(payload["method_id"], "energy_quasi_static_v1")
        self.assertEqual(payload["validity"], "valid")
        self.assertEqual(payload["flags"], [CONTACT_PATCH_ASSUMPTION])
        self.assertEqual(len(payload["unsupported_failure_modes"]), 5)
        self.assertTrue(payload["qualification_blocked"])
        self.assertEqual(payload["solver_metadata"]["model_id"], SCREENING_SURROGATE_MODEL_ID)
        self.assertEqual(payload["contact_normal"], [0.0, 0.0, 1.0])
        self.assertEqual(payload["contact_model"], CONTACT_MODEL_LINEAR)
        self.assertEqual(payload["peak_force_estimate_n"], payload["peak_force_n"])


class HertzContactTests(unittest.TestCase):
    """Finding 1: desk-edge stiffness must be a documented nonlinear
    contact law, not a dimensionally wrong linear stiffness."""

    def test_hertz_peak_force_matches_energy_balance(self):
        mass = 0.1
        velocity = 4.0
        modulus = 1e9
        radius = 0.005
        k_h = (4.0 / 3.0) * modulus * math.sqrt(radius)
        delta_max = ((5.0 / 4.0) * mass * velocity * velocity / k_h) ** (2.0 / 5.0)
        expected_peak = k_h * delta_max ** 1.5
        result = estimate_impact(
            mass_kg=mass,
            velocity_m_s=velocity,
            effective_modulus_pa=modulus,
            contact_radius_m=radius,
        )
        self.assertEqual(result.contact_model, CONTACT_MODEL_HERTZ_NONLINEAR)
        self.assertAlmostEqual(result.peak_force_n, expected_peak, places=6)
        self.assertAlmostEqual(result.contact_compression_m, delta_max, places=6)
        # Default plastic impact (e=0): contact ends at max compression, so
        # the reported duration is half the full elastic contact duration.
        self.assertAlmostEqual(
            result.contact_duration_s, 2.94 * (1.0 + 0.0) / 2.0 * delta_max / velocity, places=6
        )
        elastic = estimate_impact(
            mass_kg=mass,
            velocity_m_s=velocity,
            effective_modulus_pa=modulus,
            contact_radius_m=radius,
            restitution=1.0,
        )
        self.assertAlmostEqual(
            elastic.contact_duration_s, 2.94 * (1.0 + 1.0) / 2.0 * delta_max / velocity, places=6
        )
        self.assertAlmostEqual(result.impact_energy_j, 0.5 * mass * velocity * velocity, places=9)
        self.assertAlmostEqual(result.impact_energy_j, (2.0 / 5.0) * k_h * delta_max ** 2.5, places=6)

    def test_desk_edge_defaults_to_hertz_nonlinear(self):
        result = desk_edge_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005)
        self.assertEqual(result.contact_model, CONTACT_MODEL_HERTZ_NONLINEAR)
        self.assertIn(DESK_EDGE_CONTACT_APPROXIMATION, result.flags)
        radius = 0.005
        k_h = (4.0 / 3.0) * 1e9 * math.sqrt(radius)
        delta_max = ((5.0 / 4.0) * 0.1 * 4.0 * 4.0 / k_h) ** (2.0 / 5.0)
        self.assertAlmostEqual(result.peak_force_n, k_h * delta_max ** 1.5, places=6)

    def test_desk_edge_explicit_stiffness_is_linear_calibrated(self):
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005, shell_stiffness_n_per_m=1e5
        )
        self.assertEqual(result.contact_model, CONTACT_MODEL_LINEAR_CALIBRATED)
        self.assertAlmostEqual(result.peak_force_n, 4.0 * math.sqrt(0.1 * 1e5), places=9)

    def test_hertz_requires_modulus_and_radius_together(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, effective_modulus_pa=1e9, contact_stiffness_n_per_m=1e5
        )
        self.assertEqual(result.contact_model, CONTACT_MODEL_LINEAR)
        self.assertAlmostEqual(result.peak_force_n, 4.0 * math.sqrt(0.1 * 1e5), places=9)
        missing_radius = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, effective_modulus_pa=1e9)
        self.assertEqual(missing_radius.validity, "failed")
        self.assertIn(INVALID_STIFFNESS, missing_radius.flags)
        missing_modulus = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005)
        self.assertEqual(missing_modulus.validity, "failed")
        self.assertIn(INVALID_STIFFNESS, missing_modulus.flags)


class ContactNormalTests(unittest.TestCase):
    """Finding 2: contact_normal must be normalized and used to resolve
    vertical/effective impact components for sloped impacts."""

    def test_default_normal_is_unchanged(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertEqual(result.contact_normal, (0.0, 0.0, 1.0))
        self.assertEqual(result.impact_angle_deg, 0.0)
        self.assertAlmostEqual(result.effective_normal_velocity_m_s, 4.0, places=9)
        self.assertAlmostEqual(result.tangential_velocity_m_s, 0.0, places=9)
        self.assertAlmostEqual(result.impact_energy_j, 0.5 * 0.1 * 16.0, places=9)

    def test_contact_normal_is_normalized(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5, contact_normal=(0.0, 0.0, 2.0)
        )
        self.assertEqual(result.contact_normal, (0.0, 0.0, 1.0))
        self.assertEqual(result.impact_angle_deg, 0.0)

    def test_sloped_normal_breaks_free_fall_into_normal_component(self):
        normal = (0.0, 1.0, math.sqrt(3.0))
        result = estimate_impact(
            mass_kg=0.1, fall_height_m=1.0, contact_stiffness_n_per_m=1e5, contact_normal=normal
        )
        velocity = math.sqrt(2.0 * 9.80665)
        n_z = math.sqrt(3.0) / 2.0
        self.assertAlmostEqual(result.impact_angle_deg, 30.0, places=9)
        self.assertAlmostEqual(result.effective_normal_velocity_m_s, velocity * n_z, places=9)
        self.assertAlmostEqual(result.vertical_velocity_component_m_s, velocity, places=9)
        self.assertAlmostEqual(result.tangential_velocity_m_s, velocity * 0.5, places=9)
        self.assertAlmostEqual(result.impact_energy_j, 0.5 * 0.1 * (velocity * n_z) ** 2, places=9)
        self.assertAlmostEqual(result.impulse_n_s, 0.1 * velocity * n_z, places=9)
        self.assertAlmostEqual(result.peak_force_n, velocity * n_z * math.sqrt(0.1 * 1e5), places=9)

    def test_sloped_normal_direct_velocity_assumed_along_normal(self):
        normal = (0.0, 1.0, math.sqrt(3.0))
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5, contact_normal=normal
        )
        self.assertAlmostEqual(result.effective_normal_velocity_m_s, 4.0, places=9)
        self.assertAlmostEqual(result.vertical_velocity_component_m_s, 4.0 * math.sqrt(3.0) / 2.0, places=9)
        self.assertAlmostEqual(result.tangential_velocity_m_s, 0.0, places=9)
        self.assertAlmostEqual(result.impact_energy_j, 0.5 * 0.1 * 16.0, places=9)

    def test_free_fall_perpendicular_to_normal_is_no_impact(self):
        result = estimate_impact(mass_kg=0.1, fall_height_m=1.0, contact_normal=(1.0, 0.0, 0.0))
        self.assertEqual(result.validity, "no_impact")
        self.assertEqual(result.impact_energy_j, 0.0)
        self.assertIn("perpendicular", result.assumptions[0])

    def test_invalid_contact_normal_still_failed(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_normal=(0.0, 0.0))
        self.assertEqual(result.validity, "failed")
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_normal=(0.0, 0.0, 0.0))
        self.assertEqual(result.validity, "failed")


class EnergyPartitionTests(unittest.TestCase):
    """Finding 3: documented translation vs rotation energy partition."""

    def test_partition_all_translational_when_contact_at_center(self):
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            inertia_tensor_kg_m2=[[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
            contact_location_m=(0.0, 0.0, 0.0),
        )
        partition = result.energy_partition
        self.assertIsNotNone(partition)
        self.assertEqual(partition["model"], "rigid_body_impulse_partition")
        self.assertAlmostEqual(partition["translational_fraction"], 1.0, places=9)
        self.assertAlmostEqual(partition["rotational_fraction"], 0.0, places=9)
        self.assertAlmostEqual(partition["translational_energy_j"], 0.5 * 0.1 * 16.0, places=9)
        self.assertEqual(partition["contact_offset_m"], [0.0, 0.0, 0.0])

    def test_partition_with_contact_offset_splits_energy(self):
        inertia = 1e-6
        mass = 0.1
        velocity = 4.0
        offset = 0.01
        result = estimate_impact(
            mass_kg=mass,
            velocity_m_s=velocity,
            contact_stiffness_n_per_m=1e5,
            total_mass_kg=mass,
            inertia_tensor_kg_m2=[[inertia, 0.0, 0.0], [0.0, inertia, 0.0], [0.0, 0.0, inertia]],
            contact_location_m=(offset, 0.0, 0.0),
            center_of_mass_m=(0.0, 0.0, 0.0),
        )
        impulse = mass * velocity
        t_trans = impulse * impulse / (2.0 * mass)
        t_rot = 0.5 * impulse * impulse * offset * offset / inertia
        total = t_trans + t_rot
        scale = result.impact_energy_j / total
        partition = result.energy_partition
        self.assertAlmostEqual(partition["translational_energy_j"], t_trans * scale, places=9)
        self.assertAlmostEqual(partition["rotational_energy_j"], t_rot * scale, places=9)
        self.assertAlmostEqual(partition["rotational_fraction"], t_rot / total, places=9)
        self.assertAlmostEqual(partition["translational_fraction"], t_trans / total, places=9)
        self.assertEqual(partition["contact_offset_m"], [offset, 0.0, 0.0])

    def test_partition_conserves_reported_impact_energy(self):
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            total_mass_kg=0.1,
            inertia_tensor_kg_m2=[[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
            contact_location_m=(0.01, 0.0, 0.0),
            center_of_mass_m=(0.0, 0.0, 0.0),
        )
        partition = result.energy_partition
        total = partition["translational_energy_j"] + partition["rotational_energy_j"]
        self.assertAlmostEqual(total, result.impact_energy_j, places=9)
        self.assertAlmostEqual(
            (total - result.impact_energy_j) / result.impact_energy_j, 0.0, places=9
        )
        self.assertTrue(any("energy conservation" in item for item in partition["notes"]))

    def test_partition_omitted_without_inertia_tensor(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertIsNone(result.energy_partition)

    def test_invalid_inertia_tensor_failed(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, inertia_tensor_kg_m2=[[1e-6, 0.0], [0.0, 1e-6]]
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_INERTIA_TENSOR, result.flags)
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, inertia_tensor_kg_m2=[[float("nan"), 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]]
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_INERTIA_TENSOR, result.flags)

    def test_invalid_contact_offset_failed(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_location_m=(0.0, 0.0))
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_CONTACT_OFFSET, result.flags)
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, center_of_mass_m=(0.0, 0.0, 0.0))
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_CONTACT_OFFSET, result.flags)

    def test_singular_inertia_skips_partition_with_note(self):
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            inertia_tensor_kg_m2=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            contact_location_m=(0.01, 0.0, 0.0),
        )
        self.assertEqual(result.validity, "valid")
        self.assertIsNone(result.energy_partition)
        self.assertTrue(any("not invertible" in item for item in result.assumptions))

    def test_partition_does_not_claim_solver_capability(self):
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            inertia_tensor_kg_m2=[[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
            contact_location_m=(0.01, 0.0, 0.0),
        )
        self.assertEqual(result.method_id, "energy_quasi_static_v1")
        self.assertTrue(any("screening estimate only" in item for item in result.assumptions))


class StoppingDistanceTests(unittest.TestCase):
    """Finding 4: stopping distance reports an average work-equivalent
    force plus a documented conservative peak-force estimate."""

    def test_stopping_distance_reports_average_and_conservative_peak(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, stopping_distance_m=0.05)
        energy = 0.5 * 0.1 * 4.0 * 4.0
        self.assertAlmostEqual(result.peak_force_n, energy / 0.05, places=9)
        self.assertAlmostEqual(result.average_force_n, energy / 0.05, places=9)
        self.assertAlmostEqual(result.peak_force_estimate_n, 2.0 * energy / 0.05, places=9)
        self.assertEqual(result.contact_model, CONTACT_MODEL_STOPPING_DISTANCE)
        self.assertTrue(any("average work-equivalent force" in item for item in result.assumptions))
        self.assertTrue(any("triangular force-pulse assumption" in item for item in result.assumptions))
        self.assertAlmostEqual(result.peak_acceleration_m_s2, result.peak_force_estimate_n / 0.1, places=9)

    def test_stopping_distance_backward_compatible_keys(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, stopping_distance_m=0.05)
        payload = result.to_dict()
        self.assertAlmostEqual(payload["peak_force_n"], result.peak_force_n, places=9)
        self.assertIn("average_force_n", payload)
        self.assertIn("peak_force_estimate_n", payload)

    def test_stopping_distance_stress_uses_conservative_peak(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, stopping_distance_m=0.05, load_path_area_m2=1e-4
        )
        self.assertAlmostEqual(result.load_path_stress_pa, 2.0 * (0.5 * 0.1 * 16.0 / 0.05) / 1e-4, places=9)
        self.assertTrue(any("screening proxy" in item for item in result.assumptions))


class SolverMetadataTests(unittest.TestCase):
    """Finding 6: results are explicitly marked as screening surrogates."""

    def test_valid_result_metadata(self):
        result = estimate_impact(mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5)
        self.assertEqual(result.solver_metadata["model_id"], SCREENING_SURROGATE_MODEL_ID)
        self.assertIn("screening", result.solver_metadata["description"])

    def test_failed_result_metadata(self):
        result = estimate_impact(mass_kg=0.0, velocity_m_s=1.0)
        self.assertEqual(result.solver_metadata["model_id"], SCREENING_SURROGATE_MODEL_ID)

    def test_load_path_stress_labeled_screening_proxy(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5, load_path_area_m2=1e-4
        )
        self.assertTrue(any("scalar load-path stress screening proxy" in item for item in result.assumptions))
        self.assertIn(
            "not a component stress prediction",
            result.solver_metadata["load_path_stress_pa_limitation"],
        )


class ExtremeMagnitudeTests(unittest.TestCase):
    """Degenerate magnitudes must fail loudly instead of emitting inf/NaN
    that would corrupt JSON serialization downstream."""

    def test_huge_magnitudes_return_failed_not_inf(self):
        result = estimate_impact(
            mass_kg=1e308, velocity_m_s=1e308, contact_stiffness_n_per_m=1e308
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)
        payload = result.to_dict()
        for value in payload.values():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value))
        text = json.dumps(payload)
        self.assertNotIn("Infinity", text)
        self.assertNotIn("NaN", text)

    def test_huge_gravity_fails(self):
        result = estimate_impact(mass_kg=0.1, fall_height_m=1.0, g=1e308)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)

    def test_huge_fall_height_fails(self):
        result = estimate_impact(mass_kg=0.1, fall_height_m=1e308)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)

    def test_tiny_magnitudes_underflow_flagged_not_valid(self):
        result = estimate_impact(mass_kg=1e-320, velocity_m_s=1e-320)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)

    def test_tiny_stopping_distance_overflow_fails(self):
        result = estimate_impact(mass_kg=1e308, velocity_m_s=1e308, stopping_distance_m=1e-320)
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)

    def test_huge_load_path_stress_fails(self):
        result = estimate_impact(
            mass_kg=1e100, velocity_m_s=1e100, contact_stiffness_n_per_m=1e200,
            load_path_area_m2=1e-308,
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_LOAD_PATH, result.flags)

    def test_energy_partition_never_emits_non_finite_fractions(self):
        result = estimate_impact(
            mass_kg=1e154,
            velocity_m_s=1e154,
            contact_stiffness_n_per_m=1e308,
            inertia_tensor_kg_m2=[[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
            contact_location_m=(0.01, 0.0, 0.0),
            total_mass_kg=1e154,
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_KINEMATICS, result.flags)

    def test_fatigue_tiny_stress_never_overflows(self):
        summary = repeat_impact_cycles(cycles_n=100, stress_amplitude_pa=1e-300)
        life = summary["levels"][0]["cycles_to_failure"]
        self.assertTrue(math.isfinite(life))
        self.assertTrue(math.isfinite(summary["damage_sum"]))

    def test_fatigue_astronomical_stress_caps_life(self):
        summary = repeat_impact_cycles(cycles_n=100, stress_amplitude_pa=1e300)
        self.assertEqual(summary["levels"][0]["cycles_to_failure"], 1.0)

    def test_fatigue_outputs_json_clean(self):
        summary = repeat_impact_cycles(cycles_n=100, stress_amplitude_pa=1e-300)
        text = json.dumps(summary)
        self.assertNotIn("Infinity", text)
        self.assertNotIn("NaN", text)

    def test_valid_results_remain_json_clean(self):
        result = estimate_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_stiffness_n_per_m=1e5,
            inertia_tensor_kg_m2=[[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
            contact_location_m=(0.01, 0.0, 0.0),
        )
        text = json.dumps(result.to_dict())
        self.assertNotIn("Infinity", text)
        self.assertNotIn("NaN", text)


if __name__ == "__main__":
    unittest.main()
