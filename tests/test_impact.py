import math
import unittest

from mouse_sim.impact import (
    CONTACT_PATCH_ASSUMPTION,
    DESK_EDGE_CONTACT_APPROXIMATION,
    FATIGUE_ESTIMATE_EXCEEDED,
    INVALID_KINEMATICS,
    INVALID_MASS,
    INVALID_RESTITUTION,
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


if __name__ == "__main__":
    unittest.main()
