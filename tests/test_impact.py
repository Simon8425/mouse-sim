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
    INVALID_CONTACT_OFFSET,
    INVALID_INERTIA_TENSOR,
    INVALID_KINEMATICS,
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
        self.assertAlmostEqual(result.contact_duration_s, 2.94 * delta_max / velocity, places=6)
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
        partition = result.energy_partition
        self.assertAlmostEqual(partition["translational_energy_j"], t_trans, places=9)
        self.assertAlmostEqual(partition["rotational_energy_j"], t_rot, places=9)
        self.assertAlmostEqual(partition["rotational_fraction"], t_rot / total, places=9)
        self.assertAlmostEqual(partition["translational_fraction"], t_trans / total, places=9)
        self.assertEqual(partition["contact_offset_m"], [offset, 0.0, 0.0])

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


if __name__ == "__main__":
    unittest.main()
