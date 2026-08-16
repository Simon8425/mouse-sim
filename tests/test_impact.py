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
    HERTZ_REGIME_EXCEEDED,
    IMPACT_ACCELERATION_IMPLAUSIBLE,
    IMPACT_STRESS_IMPLAUSIBLE,
    INVALID_CONTACT_OFFSET,
    INVALID_INERTIA_TENSOR,
    INVALID_KINEMATICS,
    INVALID_LOAD_PATH,
    INVALID_MASS,
    INVALID_RESTITUTION,
    INVALID_SN_CURVE,
    INVALID_STIFFNESS,
    INSUFFICIENT_PARAMETERS,
    PEAK_FORCE_NOT_ESTIMATED,
    SCREENING_SURROGATE_MODEL_ID,
    UNSUPPORTED_BATTERY_CRUSH,
    desk_edge_impact,
    effective_modulus,
    estimate_impact,
    impact_qualification_status,
    repeat_impact_cycles,
)
from mouse_sim.materials import builtin_materials


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
        # The R-ratio/amplitude interpretation is always recorded (README
        # contract: assumptions are always present).
        self.assertTrue(
            any("R~0" in item and "amplitude" in item for item in with_material["assumptions"])
        )

    def test_r_ratio_assumption_always_recorded(self):
        summary = repeat_impact_cycles(cycles_n=1, stress_amplitude_pa=1e6, s_n_curve={1e6: 1e4})
        self.assertTrue(
            any("R~0" in item and "amplitude" in item for item in summary["assumptions"])
        )

    def test_material_basquin_laws_exact_lives(self):
        # Per-material S-N data matching the shipped catalog (polymer fatigue
        # compilations, R ~ 0.1): ABS 14 MPa @ 1e6, slope 7;
        # PC 25 MPa @ 1e6, slope 8; POM 30 MPa @ 1e6, slope 10;
        # FR-4 65 MPa @ 1e6, slope 10.
        # N = 1e6*(sigma_ref/sigma)^k, hand-computed below.
        materials = (
            ("ABS", 14e6, 7, 1e6 / 128.0),      # at 2*sigma_ref: 7812.5
            ("PC", 25e6, 8, 1e6 / 256.0),       # 3906.25
            ("POM", 30e6, 10, 1e6 / 1024.0),    # 976.5625
            ("FR-4", 65e6, 10, 1e6 / 1024.0),   # 976.5625
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


class FatigueAuditRegressionTests(unittest.TestCase):
    """Audit E1/C2/LOW regressions: consistent built-in curves, endurance
    knee, invalid-curve handling, and skipped-level disclosure."""

    def test_steel_below_endurance_limit_is_infinite_life(self):
        # Steel curve (catalog): 180 MPa @ 1e6, slope 10, endurance knee at
        # 180 MPa.  Stress at or below the knee is screening-infinite life
        # (the _MAX_SCREENING_LIFE_CYCLES cap of 1e18).
        for stress in (100e6, 180e6):
            summary = repeat_impact_cycles(
                cycles_n=1,
                stress_amplitude_pa=stress,
                fatigue_strength_at_1e6_pa=180e6,
                fatigue_exponent_k=10,
                endurance_limit_pa=180e6,
            )
            self.assertEqual(summary["levels"][0]["cycles_to_failure"], 1e18, stress)
            self.assertAlmostEqual(summary["damage_sum"], 1e-18, places=22, msg=stress)
            self.assertTrue(any("endurance limit" in item for item in summary["assumptions"]))

    def test_steel_above_endurance_limit_is_finite_life(self):
        summary = repeat_impact_cycles(
            cycles_n=1,
            stress_amplitude_pa=181e6,
            fatigue_strength_at_1e6_pa=180e6,
            fatigue_exponent_k=10,
            endurance_limit_pa=180e6,
        )
        life = summary["levels"][0]["cycles_to_failure"]
        self.assertLess(life, 1e6)
        self.assertGreater(life, 9e5)

    def test_curve_without_endurance_limit_keeps_generic_path(self):
        # No endurance limit supplied: the generic polymer fallback still
        # caps tiny stresses at the screening-infinite value (unchanged).
        summary = repeat_impact_cycles(cycles_n=100, stress_amplitude_pa=1e-300)
        self.assertEqual(summary["levels"][0]["cycles_to_failure"], 1e18)
        self.assertIn(FATIGUE_GENERIC_FALLBACK, summary["flags"])

    def test_invalid_sn_curve_returns_failed_result_not_raise(self):
        bad_life = repeat_impact_cycles(cycles_n=1, stress_amplitude_pa=1e6, s_n_curve={1e6: "lots"})
        self.assertIn(INVALID_SN_CURVE, bad_life["flags"])
        self.assertEqual(bad_life["levels"], [])
        self.assertEqual(bad_life["damage_sum"], 0.0)
        self.assertTrue(any("must be numeric" in item for item in bad_life["assumptions"]))
        bad_key = repeat_impact_cycles(
            cycles_n=1, stress_amplitude_pa=5e5, s_n_curve={"1e6": 1e4, "low": "many"}
        )
        self.assertIn(INVALID_SN_CURVE, bad_key["flags"])

    def test_zero_and_negative_stress_levels_skipped_with_disclosure(self):
        summary = repeat_impact_cycles(
            [(10, 1e7), (5, 0.0), (3, -1e6)], s_n_curve={1e7: 1e3}
        )
        self.assertEqual(summary["skipped_levels"], 2)
        self.assertEqual(summary["cycles_evaluated"], 10.0)
        self.assertTrue(
            any("2 zero/negative stress level(s) skipped" in item for item in summary["assumptions"])
        )
        self.assertEqual(len(summary["levels"]), 1)

    def test_builtin_catalog_curves_respect_uts_at_1e3_cycles(self):
        # Every shipped curve must keep sigma(1e3) = sigma_ref*1000^(1/k) at
        # or below 0.9*UTS when evaluated through the screening estimator.
        catalog = builtin_materials()
        for key, material in catalog.items():
            properties = material.properties
            anchor = properties.fatigue_strength_at_1e6_pa
            exponent = properties.fatigue_exponent_k
            uts = properties.ultimate_strength
            if anchor is None or exponent is None:
                continue
            implied = anchor.value_si * 1000.0 ** (1.0 / exponent)
            self.assertLessEqual(implied, 0.9 * uts.value_si, key)


class HertzRegimeTests(unittest.TestCase):
    """Audit C2: the Hertz small-deformation regime is screened and the
    breach is disclosed instead of silently extrapolating."""

    def test_soft_contact_exceeding_regime_flags(self):
        # Foam-surface case: 120 g at 4 m/s on a soft contact with R = 2 mm
        # compresses ~1.1 mm, delta_max/R ~ 0.55 >> 0.1.
        result = estimate_impact(
            mass_kg=0.12,
            velocity_m_s=4.0,
            effective_modulus_pa=1e9,
            contact_radius_m=0.002,
        )
        self.assertIn(HERTZ_REGIME_EXCEEDED, result.flags)
        self.assertTrue(
            any("delta_max/R" in item and "0.1" in item for item in result.assumptions)
        )
        ratio = result.contact_compression_m / 0.002
        self.assertGreater(ratio, 0.1)

    def test_stiff_contact_within_regime_no_flag(self):
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            effective_modulus_pa=1e9,
            contact_radius_m=0.02,
        )
        self.assertNotIn(HERTZ_REGIME_EXCEEDED, result.flags)

    def test_regime_flag_is_disclosure_only(self):
        result = estimate_impact(
            mass_kg=0.12,
            velocity_m_s=4.0,
            effective_modulus_pa=1e9,
            contact_radius_m=0.002,
        )
        self.assertEqual(result.validity, "valid")
        self.assertGreater(result.peak_force_n, 0.0)


class DeterminismTests(unittest.TestCase):
    """Two runs of the same inputs must produce identical payloads."""

    def test_estimate_impact_runs_are_byte_identical(self):
        kwargs = dict(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            load_path_area_m2=1e-4,
            allowable_pa=2e6,
            inertia_tensor_kg_m2=[[1e-6, 0.0, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]],
            contact_location_m=(0.01, 0.0, 0.0),
        )
        first = estimate_impact(**kwargs)
        second = estimate_impact(**kwargs)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(json.dumps(first.to_dict()), json.dumps(second.to_dict()))

    def test_hertz_and_fatigue_runs_are_byte_identical(self):
        hertz_kwargs = dict(
            mass_kg=0.12,
            velocity_m_s=4.0,
            effective_modulus_pa=1e9,
            contact_radius_m=0.002,
        )
        self.assertEqual(
            estimate_impact(**hertz_kwargs).to_dict(), estimate_impact(**hertz_kwargs).to_dict()
        )
        fatigue_kwargs = dict(
            cycles_n=[(10, 1e7), (2, 0.0)],
            fatigue_strength_at_1e6_pa=180e6,
            fatigue_exponent_k=10,
            endurance_limit_pa=180e6,
        )
        self.assertEqual(
            repeat_impact_cycles(**fatigue_kwargs), repeat_impact_cycles(**fatigue_kwargs)
        )


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
        # Pass-through of estimate_impact kwargs (reduced mass with
        # target_mass_kg=0.1 gives m_eff = 0.1*0.1/(0.1+0.1) = 0.05).
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005, target_mass_kg=0.1
        )
        self.assertAlmostEqual(result.effective_mass_kg, 0.05, places=9)

    def test_desk_edge_combined_radius_uses_cylinder_formula(self):
        # Sphere R1 = 5 mm on a cylindrical edge R2 = 5 mm:
        # R_eff = R1*sqrt(R2/(R1+R2)) = 5*sqrt(0.5) = 3.5355 mm.
        r1 = 0.005
        r2 = 0.005
        r_eff = r1 * math.sqrt(r2 / (r1 + r2))
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=r1, desk_edge_radius_m=r2
        )
        k_h = (4.0 / 3.0) * 1e9 * math.sqrt(r_eff)
        delta_max = ((5.0 / 4.0) * 0.1 * 4.0 * 4.0 / k_h) ** (2.0 / 5.0)
        self.assertAlmostEqual(result.peak_force_n, k_h * delta_max ** 1.5, places=6)
        alone = desk_edge_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=r1)
        self.assertLess(result.peak_force_n, alone.peak_force_n)
        # Hertz peak force scales as R^0.2 (F ~ k_h*delta^1.5 with delta ~
        # (1/k_h)^0.4 and k_h ~ sqrt(R)), so the overstatement using R1 alone
        # is (R1/R_eff)^0.2 ~ 1.07 for R2 = R1.
        self.assertAlmostEqual(
            alone.peak_force_n / result.peak_force_n, (r1 / r_eff) ** 0.2, places=6
        )
        self.assertTrue(
            any("R_eff = R1*sqrt(R2/(R1+R2))" in item for item in result.assumptions)
        )
        self.assertTrue(any("R2 = 0.005" in item for item in result.assumptions))

    def test_desk_edge_default_discloses_shell_radius_alone(self):
        result = desk_edge_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005)
        self.assertTrue(
            any("shell radius alone" in item and "non-conservative" in item for item in result.assumptions)
        )
        self.assertTrue(
            any("R_eff = R1*sqrt(R2/(R1+R2))" in item for item in result.assumptions)
        )

    def test_desk_edge_invalid_edge_radius_failed(self):
        for bad in (0.0, -0.001):
            result = desk_edge_impact(
                mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005, desk_edge_radius_m=bad
            )
            self.assertEqual(result.validity, "failed", bad)
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005, desk_edge_radius_m="x"
        )
        self.assertEqual(result.validity, "failed")

    def test_desk_edge_linear_stiffness_ignores_edge_radius_with_disclosure(self):
        result = desk_edge_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_radius_m=0.005,
            desk_edge_radius_m=0.005,
            shell_stiffness_n_per_m=1e5,
        )
        self.assertEqual(result.contact_model, CONTACT_MODEL_LINEAR_CALIBRATED)
        self.assertAlmostEqual(result.peak_force_n, 4.0 * math.sqrt(0.1 * 1e5), places=9)
        self.assertTrue(
            any("desk_edge_radius_m ignored" in item for item in result.assumptions)
        )


class EffectiveModulusTests(unittest.TestCase):
    """The Hertz effective contact modulus E_eff = ((1-nu1^2)/E1 +
    (1-nu2^2)/E2)^-1 must be shared by every Hertz call site and reject
    invalid material data instead of silently substituting."""

    def test_effective_modulus_abs_on_concrete(self):
        e = effective_modulus(2.3e9, 0.35, 30e9, 0.20)
        expected = 1.0 / ((1.0 - 0.35 ** 2) / 2.3e9 + (1.0 - 0.20 ** 2) / 30e9)
        self.assertAlmostEqual(e, expected, places=9)
        self.assertAlmostEqual(e / 1e9, 2.418, delta=0.01)

    def test_effective_modulus_abs_on_steel(self):
        e = effective_modulus(2.3e9, 0.35, 200e9, 0.30)
        self.assertLess(e, 2.7e9)
        self.assertGreater(e, 2.3e9)

    def test_effective_modulus_invalid_inputs_rejected(self):
        for args in (
            (0.0, 0.35, 30e9, 0.2),
            (-2.3e9, 0.35, 30e9, 0.2),
            (2.3e9, 0.6, 30e9, 0.2),
            (2.3e9, -1.0, 30e9, 0.2),
            (2.3e9, 0.35, 30e9, 1.0),
            (2.3e9, 0.35, float("nan"), 0.2),
        ):
            with self.assertRaises(ValueError):
                effective_modulus(*args)

    def test_desk_edge_routes_material_pair_through_effective_modulus(self):
        result = desk_edge_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_radius_m=0.005,
            shell_young_modulus_pa=2.3e9,
            shell_poissons_ratio=0.35,
            floor_young_modulus_pa=30e9,
            floor_poissons_ratio=0.20,
        )
        e_eff = effective_modulus(2.3e9, 0.35, 30e9, 0.20)
        k_h = (4.0 / 3.0) * e_eff * math.sqrt(0.005)
        delta_max = ((5.0 / 4.0) * 0.1 * 4.0 * 4.0 / k_h) ** (2.0 / 5.0)
        self.assertEqual(result.contact_model, CONTACT_MODEL_HERTZ_NONLINEAR)
        self.assertAlmostEqual(result.peak_force_n, k_h * delta_max ** 1.5, places=6)
        self.assertTrue(any("E_eff = ((1-nu1^2)/E1" in item for item in result.assumptions))

    def test_desk_edge_explicit_effective_modulus_override(self):
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005,
            effective_modulus_pa=2.4e9,
        )
        k_h = (4.0 / 3.0) * 2.4e9 * math.sqrt(0.005)
        delta_max = ((5.0 / 4.0) * 0.1 * 4.0 * 4.0 / k_h) ** (2.0 / 5.0)
        self.assertAlmostEqual(result.peak_force_n, k_h * delta_max ** 1.5, places=6)

    def test_desk_edge_default_effective_modulus_preserved(self):
        # Documented behavior preserved: no E/nu supplied keeps the crude
        # 1e9 Pa default.
        result = desk_edge_impact(mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005)
        k_h = (4.0 / 3.0) * 1e9 * math.sqrt(0.005)
        delta_max = ((5.0 / 4.0) * 0.1 * 4.0 * 4.0 / k_h) ** (2.0 / 5.0)
        self.assertAlmostEqual(result.peak_force_n, k_h * delta_max ** 1.5, places=6)
        self.assertTrue(any("crude assumption" in item for item in result.assumptions))

    def test_desk_edge_partial_material_pair_failed(self):
        result = desk_edge_impact(
            mass_kg=0.1, velocity_m_s=4.0, contact_radius_m=0.005,
            shell_young_modulus_pa=2.3e9, shell_poissons_ratio=0.35,
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_STIFFNESS, result.flags)


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

    def test_singular_inertia_fails_closed(self):
        # W2-05D: a zero (singular) inertia tensor is not physically
        # possible; it is rejected as INVALID_INERTIA_TENSOR instead of
        # silently producing a valid-looking result.
        result = estimate_impact(
            mass_kg=0.1,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            inertia_tensor_kg_m2=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            contact_location_m=(0.01, 0.0, 0.0),
        )
        self.assertEqual(result.validity, "failed")
        self.assertIn(INVALID_INERTIA_TENSOR, result.flags)
        self.assertIsNone(result.energy_partition)

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

    def test_partition_impulse_consistency_with_restitution(self):
        # Audit fix: the partition must use the SAME impulse the result
        # reports (m*(1+e)*v), not the plastic-only m*v.  With e = 1 the
        # old code used HALF the reported impulse, understating the
        # rotational share before the energy scaling.
        mass, velocity, offset, inertia = 0.1, 4.0, 0.01, 1e-6
        result = estimate_impact(
            mass_kg=mass,
            velocity_m_s=velocity,
            restitution=1.0,
            contact_stiffness_n_per_m=1e5,
            total_mass_kg=mass,
            inertia_tensor_kg_m2=[[inertia, 0.0, 0.0], [0.0, inertia, 0.0], [0.0, 0.0, inertia]],
            contact_location_m=(offset, 0.0, 0.0),
            center_of_mass_m=(0.0, 0.0, 0.0),
        )
        partition = result.energy_partition
        self.assertIsNotNone(partition)
        reported_impulse = result.impulse_n_s
        self.assertAlmostEqual(reported_impulse, mass * 2.0 * velocity, places=9)
        self.assertAlmostEqual(partition["impulse_n_s"], reported_impulse, places=9)
        # Raw partition totals with the restitution-corrected impulse:
        impulse = mass * 2.0 * velocity
        t_trans = impulse * impulse / (2.0 * mass)
        t_rot = 0.5 * impulse * impulse * offset * offset / inertia
        total = t_trans + t_rot
        scale = result.impact_energy_j / total
        self.assertAlmostEqual(
            partition["rotational_energy_j"], t_rot * scale, places=6
        )
        self.assertAlmostEqual(
            partition["translational_energy_j"], t_trans * scale, places=6
        )
        # The plastic-impulse bookkeeping would have reported a HALF impulse.
        self.assertGreater(
            partition["impulse_n_s"], mass * velocity
        )


class ExtremeMassTests(unittest.TestCase):
    """Boundary-value audit: ultralight 1 g parts and heavy 500 g battery
    packs must produce finite, physically plausible screening results."""

    def test_ultralight_1g_part(self):
        result = estimate_impact(
            mass_kg=0.001,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
        )
        self.assertEqual(result.validity, "valid")
        self.assertTrue(math.isfinite(result.peak_force_n))
        self.assertTrue(math.isfinite(result.peak_acceleration_m_s2))
        self.assertAlmostEqual(result.peak_force_n, 4.0 * math.sqrt(0.001 * 1e5), places=9)
        self.assertAlmostEqual(result.peak_acceleration_m_s2, result.peak_force_n / 0.001, places=9)
        self.assertNotIn(IMPACT_ACCELERATION_IMPLAUSIBLE, result.flags)

    def test_heavy_500g_battery_pack(self):
        result = estimate_impact(
            mass_kg=0.5,
            velocity_m_s=4.0,
            contact_stiffness_n_per_m=1e5,
            load_path_area_m2=1e-4,
            allowable_pa=3e6,
        )
        self.assertEqual(result.validity, "valid")
        self.assertTrue(math.isfinite(result.peak_force_n))
        self.assertAlmostEqual(result.peak_force_n, 4.0 * math.sqrt(0.5 * 1e5), places=9)
        self.assertAlmostEqual(result.load_path_stress_pa, result.peak_force_n / 1e-4, places=6)
        self.assertAlmostEqual(result.safety_factor, 3e6 / result.load_path_stress_pa, places=9)

    def test_heavy_500g_hertz_half_sine_consistency(self):
        # The half-sine branch must stay impulse-consistent for a heavy pack.
        result = estimate_impact(
            mass_kg=0.5,
            velocity_m_s=4.0,
            restitution=0.3,
            contact_duration_s=0.002,
        )
        self.assertEqual(result.validity, "valid")
        self.assertAlmostEqual(result.peak_force_n, math.pi * result.impulse_n_s / (2.0 * 0.002), places=9)
        self.assertAlmostEqual(result.impulse_n_s, 0.5 * 1.3 * 4.0, places=9)

    def test_hertz_near_incompressible_and_near_zero_poisson(self):
        # nu -> 0.499 (incompressible) and nu -> 0.0 must both stay finite
        # and physically ordered in the effective modulus.
        low = effective_modulus(2.3e9, 0.0, 1e9, 0.0)
        high = effective_modulus(2.3e9, 0.499, 1e9, 0.499)
        self.assertTrue(math.isfinite(low))
        self.assertTrue(math.isfinite(high))
        # Incompressible materials are stiffer in contact.
        self.assertGreater(high, low)
        # nu = 0.5 exactly is rejected (incompressibility limit is open).
        with self.assertRaises(ValueError):
            effective_modulus(2.3e9, 0.5, 1e9, 0.3)


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
