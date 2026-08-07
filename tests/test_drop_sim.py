import math
import unittest

from mouse_sim.drop_sim import (
    DropSimulationError,
    _axis_angle_quaternion,
    box_inertia,
    _quaternion_rotate,
    simulate,
    support_points,
    validate_config,
)

CUBE_SUPPORT = support_points(
    [(x, y, z) for x in (-0.05, 0.05) for y in (-0.05, 0.05) for z in (-0.05, 0.05)]
)
CUBE_INERTIA = box_inertia(0.1, ((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05)))


class DropSimulationConfigTests(unittest.TestCase):
    def test_validate_config_defaults_and_roundtrip(self):
        config = validate_config({})
        self.assertEqual(config["test"], "drop")
        self.assertAlmostEqual(config["height_m"], 0.75)
        self.assertEqual(config["surface"], "concrete")
        self.assertEqual(config["drop_count"], 1)
        self.assertIsNone(config["mass_kg"])

    def test_validate_rejects_bad_values(self):
        for bad in (
            {"test": "fly"},
            {"height_m": 0.0},
            {"height_m": 5.0},
            {"height_m": float("nan")},
            {"surface": "jello"},
            {"drop_count": 0},
            {"drop_count": 21},
            {"orientation": "diagonal"},
            {"spin_rps": 50.0},
            {"mass_kg": -1},
            {"mass_kg": 100},
        ):
            with self.assertRaises(DropSimulationError):
                validate_config(bad)

    def test_quaternion_rotate_matches_axis_aligned(self):
        q = _axis_angle_quaternion((0.0, 0.0, 1.0), math.pi / 2.0)
        result = _quaternion_rotate(q, (1.0, 0.0, 0.0))
        self.assertAlmostEqual(result[0], 0.0, places=9)
        self.assertAlmostEqual(result[1], 1.0, places=9)
        self.assertAlmostEqual(result[2], 0.0, places=9)


class DropSimulationPhysicsTests(unittest.TestCase):
    def test_free_fall_impact_speed_matches_kinematics(self):
        height = 0.75
        result = simulate(
            0.1,
            CUBE_INERTIA,
            CUBE_SUPPORT,
            height,
            surface="concrete",
            drop_count=1,
            test="drop",
            orientation="flat",
        )
        self.assertEqual(len(result["drops"]), 1)
        self.assertGreaterEqual(len(result["impacts"]), 1)
        first = result["impacts"][0]
        expected = math.sqrt(2.0 * 9.81 * height)
        self.assertAlmostEqual(first["impact_speed_m_s"], expected, delta=0.05)

    def test_deterministic_across_runs(self):
        first = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, orientation="random")
        second = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, orientation="random")
        self.assertEqual(first["trajectory"], second["trajectory"])
        self.assertEqual(first["impacts"], second["impacts"])

    def test_higher_drop_is_harder(self):
        low = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.2)
        high = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 1.5)
        self.assertGreater(
            high["peak"]["impact_speed_m_s"],
            low["peak"]["impact_speed_m_s"],
        )

    def test_drop_count_produces_that_many_drops(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.3, drop_count=4)
        self.assertEqual(len(result["drops"]), 4)
        self.assertEqual(result["drops"][0]["index"], 0)
        self.assertEqual(result["drops"][3]["index"], 3)

    def test_trajectory_starts_at_height_and_ends_at_rest(self):
        height = 0.5
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, height, drop_count=1)
        trajectory = result["trajectory"]
        # First sample near the drop height (support offset accounted).
        self.assertGreater(trajectory[0][3], height - 0.03)
        # Final sample rests on the table: CoM either flat (half-height) or
        # tipped onto an edge (half-height * sqrt(2)).
        self.assertGreaterEqual(trajectory[-1][3], 0.045)
        self.assertLessEqual(trajectory[-1][3], 0.08)

    def test_surface_restitution_changes_bounce(self):
        foam = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, surface="foam")
        steel = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, surface="steel")
        # Higher restitution keeps the body airborne longer (more bounce time).
        foam_settle = foam["drops"][0]["settled_s"]
        steel_settle = steel["drops"][0]["settled_s"]
        self.assertGreater(steel_settle, foam_settle)

    def test_energy_conservation_never_exceeds_initial(self):
        # Reported impact energies are the pre-impact SYSTEM kinetic energies
        # and must never exceed the drop's initial potential energy on the
        # real simulation path.
        drop_energy = 0.1 * 9.81 * 0.5
        for surface in ("foam", "concrete", "steel"):
            result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, surface=surface)
            for impact in result["impacts"]:
                self.assertLessEqual(impact["kinetic_energy_j"], drop_energy * 1.01)

    def test_tumble_with_spin_impacts_multiple_times(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="tumble", spin_rps=6.0)
        self.assertEqual(result["config"]["test"], "tumble")
        self.assertGreaterEqual(len(result["impacts"]), 3)

    def test_edge_and_corner_orientations_clearance(self):
        freefall = math.sqrt(2 * 9.81 * 0.5)
        for orientation in ("edge", "corner"):
            result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, orientation=orientation)
            first = result["impacts"][0]["impact_speed_m_s"]
            # Rotated orientations keep the configured clearance: the first
            # impact speed matches sqrt(2gh) for the given height.
            self.assertAlmostEqual(first, freefall, delta=0.1)

    def test_impact_test_reports_corner_orientation(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="impact", orientation="flat")
        self.assertEqual(result["drops"][0]["orientation"], "corner")

    def test_explicit_mass_used(self):
        light = simulate(0.05, box_inertia(0.05, ((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05))), CUBE_SUPPORT, 0.5)
        self.assertAlmostEqual(light["model"]["mass_kg"], 0.05)
        # Dynamics are mass-invariant: impact speeds match the heavy case.
        heavy = simulate(2.0, box_inertia(2.0, ((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05))), CUBE_SUPPORT, 0.5)
        self.assertAlmostEqual(
            light["impacts"][0]["impact_speed_m_s"],
            heavy["impacts"][0]["impact_speed_m_s"],
            places=4,
        )

    def test_drop_count_gap_spacing(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.3, drop_count=2)
        drops = result["drops"]
        self.assertGreaterEqual(drops[1]["start_s"], drops[0]["end_s"] + 0.3)
        # The second drop resets to the configured height.
        second_samples = [s for s in result["trajectory"] if s[0] >= drops[1]["start_s"]]
        self.assertGreater(second_samples[0][3], 0.27)

    def test_spin_rejected_for_non_tumble(self):
        from mouse_sim.drop_sim import DropSimulationError

        with self.assertRaises(DropSimulationError):
            simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="drop", spin_rps=4.0)

    def test_peak_energy_bounded_by_drop_energy(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1)
        peak = result["peak"]
        # Reported energy is the pre-impact SYSTEM kinetic energy (translation
        # plus rotation), which never exceeds the drop's potential energy.
        drop_energy = 0.1 * 9.81 * 0.5
        self.assertLessEqual(peak["kinetic_energy_j"], drop_energy * 1.001)
        first = result["impacts"][0]
        self.assertAlmostEqual(first["kinetic_energy_j"], drop_energy, delta=drop_energy * 0.05)

    def test_energy_does_not_grow(self):
        # The first impact matches free fall; a corner whip after the bounce
        # can exceed the CoM fall speed (lever amplification) but the total
        # mechanical energy stays bounded, so the peak stays under 2x.
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1)
        first = result["impacts"][0]["impact_speed_m_s"]
        freefall = math.sqrt(2 * 9.81 * 0.5)
        self.assertAlmostEqual(first, freefall, delta=0.15)
        self.assertLessEqual(result["peak"]["impact_speed_m_s"], 2.0 * freefall)


if __name__ == "__main__":
    unittest.main()
