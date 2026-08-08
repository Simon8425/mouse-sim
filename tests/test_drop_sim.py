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
        # The RAW (uncapped) pre-impact system kinetic energy must never
        # exceed the drop's potential energy — the capped field alone would
        # pass vacuously even if the physics created energy.
        for height in (0.5, 1.5, 2.0):
            drop_energy = 0.1 * 9.81 * height
            for surface in ("foam", "concrete", "steel"):
                result = simulate(
                    0.1, CUBE_INERTIA, CUBE_SUPPORT, height, surface=surface, drop_count=2
                )
                for impact in result["impacts"]:
                    self.assertLessEqual(
                        impact["raw_kinetic_energy_j"], drop_energy * 1.001
                    )
        # No energy-creation physics check may fire on legitimate drops.
        for surface in ("foam", "concrete", "steel"):
            result = simulate(
                0.1, CUBE_INERTIA, CUBE_SUPPORT, 2.0, surface=surface, drop_count=2
            )
            for drop in result["drops"]:
                self.assertNotIn(
                    "DROP_SIM_ENERGY_CREATION", [check["code"] for check in drop["checks"]]
                )

    def test_drop_zero_is_reference(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3)
        self.assertEqual(result["drops"][0]["tilt_deg"], 0.0)
        self.assertEqual(result["drops"][0]["lateral_offset_m"], [0.0, 0.0])
        freefall = math.sqrt(2.0 * 9.81 * 0.5)
        self.assertAlmostEqual(result["impacts"][0]["impact_speed_m_s"], freefall, delta=0.05)

    def test_repeated_drops_are_unique_but_deterministic(self):
        first = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, orientation="flat")
        second = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, orientation="flat")
        # Drop 0 is the pristine reference; drops 1+ get distinct variation.
        self.assertEqual(first["drops"][0]["tilt_deg"], 0.0)
        self.assertEqual(first["drops"][0]["lateral_offset_m"], [0.0, 0.0])
        tilts = [drop["tilt_deg"] for drop in first["drops"]]
        offsets = [tuple(drop["lateral_offset_m"]) for drop in first["drops"]]
        self.assertGreater(tilts[1], 0.0)
        self.assertGreater(tilts[2], 0.0)
        self.assertNotEqual(tilts[1], tilts[2])
        self.assertNotEqual(offsets[1], offsets[2])
        self.assertNotEqual(offsets[0], offsets[1])
        # The seeded release spin makes the first-contact velocity differ
        # slightly between drops, while each stays near the free-fall speed.
        freefall = math.sqrt(2.0 * 9.81 * 0.5)
        first_speeds = []
        for drop in first["drops"]:
            for impact in first["impacts"]:
                if impact["drop"] == drop["index"]:
                    first_speeds.append(impact["impact_speed_m_s"])
                    break
        self.assertEqual(len(first_speeds), 3)
        for speed in first_speeds:
            self.assertAlmostEqual(speed, freefall, delta=0.2)
        self.assertGreater(len(set(first_speeds)), 1)
        # Fully deterministic: two runs produce bit-identical output.
        self.assertEqual(first["trajectory"], second["trajectory"])
        self.assertEqual(first["impacts"], second["impacts"])
        self.assertEqual(first["drops"], second["drops"])
        self.assertEqual(first["model"]["jitter"], second["model"]["jitter"])
        self.assertEqual(first["model"]["jitter"]["max_tilt_deg"], 6.0)
        self.assertEqual(first["model"]["jitter"]["max_lateral_fraction"], 0.03)

    def test_lateral_offset_bounded(self):
        for seed in (1, 7, 42, 12345):
            result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, seed=seed)
            self.assertEqual(result["model"]["jitter"]["seed"], seed)
            for drop in result["drops"]:
                self.assertLessEqual(drop["tilt_deg"], 6.0)
                offset = drop["lateral_offset_m"]
                magnitude = math.hypot(offset[0], offset[1])
                self.assertLessEqual(magnitude, 0.03 * 0.5 + 1e-9)
            # Drop 0 stays the pristine reference for every seed.
            self.assertEqual(result["drops"][0]["tilt_deg"], 0.0)
            self.assertEqual(result["drops"][0]["lateral_offset_m"], [0.0, 0.0])

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


class DropSimulationPhysicsCoreTests(unittest.TestCase):
    """Regression tests for the contact-manifold, energy-accounting, settle,
    unit-variation, and CoM-frame physics core."""

    def test_flat_drop_rests_near_flat(self):
        # A flat face impact must not spin the body up about a single corner:
        # the box settles with an essentially flat orientation.
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, surface="concrete", drop_count=1)
        final_quat = result["trajectory"][-1][4]
        self.assertGreater(final_quat, 0.985)  # tilt below ~14 degrees

    def test_first_impact_manifold_is_full_face(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, surface="concrete", drop_count=1)
        first = result["impacts"][0]
        # A flat box face exposes 4+ coplanar support points; the impulse acts
        # at their (unique) centroid, i.e. at the face center.
        self.assertGreaterEqual(first["manifold_size"], 4)
        self.assertLess(abs(first["contact_location"][0]), 0.01)
        self.assertLess(abs(first["contact_location"][1]), 0.01)
        self.assertEqual(first["contact_normal"], [0.0, 0.0, 1.0])

    def test_energy_checks_clean_on_normal_drops(self):
        for height in (0.02, 0.75, 2.0):
            for surface in ("steel", "foam", "concrete"):
                result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, height, surface=surface, drop_count=1)
                drop = result["drops"][0]
                self.assertEqual(drop["checks"], [], "unexpected check for h={} {}".format(height, surface))
                self.assertLess(drop["energy"]["drift_pct"], 0.1)

    def test_all_drops_settle_honestly(self):
        for height in (0.02, 0.75, 2.0):
            for surface in ("steel", "foam", "concrete"):
                result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, height, surface=surface, drop_count=1)
                drop = result["drops"][0]
                self.assertTrue(drop["settled"], "h={} {}".format(height, surface))
                self.assertLess(drop["settled_s"], 8.0)

    def test_energy_balance_accounting(self):
        height = 0.5
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, height, surface="concrete", drop_count=1)
        energy = result["drops"][0]["energy"]
        # The release energy is the true initial mechanical energy: the drop
        # budget m*g*h plus the CoM height above the lowest support point.
        release = 0.1 * 9.81 * (height + 0.05)
        self.assertAlmostEqual(energy["release_j"], release, places=5)
        drop_energy = 0.1 * 9.81 * height
        self.assertAlmostEqual(energy["first_impact_j"], drop_energy, delta=drop_energy * 0.05)
        self.assertGreater(energy["lost_contact_j"], 0.0)
        # The body rests with its CoM above the floor: PE only, below release.
        self.assertLess(energy["settled_j"], release)
        self.assertAlmostEqual(energy["drift_pct"], 0.0, places=3)

    def test_impact_records_are_rich(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.75, orientation="corner", drop_count=1)
        for impact in result["impacts"]:
            for key in (
                "raw_kinetic_energy_j",
                "contact_location",
                "contact_normal",
                "contact_point_speed",
                "tangent_speed",
                "incidence_angle_deg",
                "manifold_size",
            ):
                self.assertIn(key, impact)
            # Energy-honest bound: the contact-point speed is lever-amplified
            # but its kinetic energy can never exceed the raw system energy,
            # so v_c <= sqrt(2*E_raw/m) must hold.
            self.assertGreater(impact["contact_point_speed"], 0.0)
            energy_speed_bound = math.sqrt(
                2.0 * impact["raw_kinetic_energy_j"] / 0.1
            )
            # 0.1% tolerance for the 4-decimal rounding of the reported
            # contact-point speed.
            self.assertLessEqual(
                impact["contact_point_speed"], energy_speed_bound * 1.001
            )

    def test_unit_seed_deterministic_and_varied(self):
        baseline = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, unit_seed=None)
        self.assertEqual(baseline["model"]["variation"]["mass_scale"], 1.0)
        self.assertEqual(baseline["model"]["variation"]["friction_scale"], 1.0)
        first = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, unit_seed=42)
        again = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, unit_seed=42)
        other = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, unit_seed=43)
        self.assertEqual(first["trajectory"], again["trajectory"])
        self.assertEqual(first["model"]["variation"], again["model"]["variation"])
        self.assertNotEqual(
            first["model"]["variation"]["mass_scale"],
            other["model"]["variation"]["mass_scale"],
        )
        self.assertNotAlmostEqual(first["model"]["variation"]["mass_scale"], 1.0, places=3)
        # Restitution stays within the physically plausible band.
        self.assertGreaterEqual(first["model"]["restitution"], 0.05)
        self.assertLessEqual(first["model"]["restitution"], 0.95)

    def test_mass_scale_multiplies_unit_draws(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, mass_scale=1.05)
        self.assertAlmostEqual(result["model"]["mass_kg"], 0.105, places=5)

    def test_com_offset_frame_invariance(self):
        # The same physical body described with a shifted origin plus the
        # matching CoM offset must produce the same CoM trajectory.
        centered = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, surface="concrete", drop_count=1)
        offset_bounds = (
            (0.005, 0.105),
            (0.005, 0.105),
            (-0.0325, 0.0675),
        )
        offset_support = support_points(
            [(x, y, z) for x in offset_bounds[0] for y in offset_bounds[1] for z in offset_bounds[2]]
        )
        com = (0.055, 0.055, 0.0175)
        offset = simulate(
            0.1, CUBE_INERTIA, offset_support, 0.5, surface="concrete", drop_count=1, com_offset_m=com
        )
        self.assertLessEqual(abs(len(centered["trajectory"]) - len(offset["trajectory"])), 2)
        common = min(len(centered["trajectory"]), len(offset["trajectory"])) - 2
        # The impact-driven trajectory (free fall through the first bounce)
        # is frame-invariant; the final rest pose is determined by the last
        # rock of a chaotic impact chain, so only its envelope is bounded.
        compare = max(1, int(common * 0.6))
        for sample_c, sample_o in zip(
            centered["trajectory"][:compare], offset["trajectory"][:compare]
        ):
            self.assertAlmostEqual(sample_c[0], sample_o[0], places=6)
            self.assertAlmostEqual(sample_c[3], sample_o[3] + com[2], places=4)
        self.assertAlmostEqual(
            centered["trajectory"][-1][3], offset["trajectory"][-1][3] + com[2], delta=0.01
        )
        self.assertAlmostEqual(
            centered["drops"][0]["settled_s"], offset["drops"][0]["settled_s"], places=1
        )

    def test_tumble_reports_raw_spin_energy(self):
        height = 0.5
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, height, test="tumble", spin_rps=6.0)
        first = result["impacts"][0]
        release = 0.1 * 9.81 * height
        # The capped report stays at the drop budget; the raw value includes
        # the tumble spin energy budget.
        self.assertLessEqual(first["kinetic_energy_j"], release * 1.0001)
        self.assertGreater(first["raw_kinetic_energy_j"], release)

    def test_validate_config_accepts_seed_and_unit_seed(self):
        config = validate_config({"seed": 7, "unit_seed": 123, "test": "tumble", "spin_rps": 2})
        self.assertEqual(config["seed"], 7)
        self.assertEqual(config["unit_seed"], 123)
        with self.assertRaises(DropSimulationError):
            validate_config({"unit_seed": -1})
        with self.assertRaises(DropSimulationError):
            validate_config({"seed": 2 ** 32})

    def test_mass_override_and_gyroscopic_free_flight(self):
        # A non-spherical body spinning about a non-principal axis precesses
        # in free flight: the angular velocity changes while energy holds.
        inertia = (
            (2.0e-5, 0.0, 0.0),
            (0.0, 5.0e-5, 0.0),
            (0.0, 0.0, 1.0e-5),
        )
        result = simulate(0.1, inertia, CUBE_SUPPORT, 0.02, surface="foam", drop_count=1, spin_rps=0.0)
        self.assertTrue(result["drops"][0]["settled"])
        # Free-flight conservation: no energy checks fire for a clean drop.
        self.assertEqual(result["drops"][0]["checks"], [])

    def test_gyroscopic_update_stable_at_high_spin_asymmetric(self):
        # Regression: the torque-free gyroscopic update self-amplified to
        # infinity for asymmetric inertia at high spin (substep subdivision
        # keeps the semi-implicit Euler step stable).  A jittered tumble at
        # 20 rps on a mouse-like body must complete with finite outputs.
        mouse_inertia = box_inertia(0.1, ((-0.03, 0.03), (-0.0575, 0.0575), (-0.015, 0.015)))
        mouse_support = support_points(
            [(x, y, z) for x in (-0.03, 0.03) for y in (-0.0575, 0.0575) for z in (-0.015, 0.015)]
        )
        result = simulate(
            0.1,
            mouse_inertia,
            mouse_support,
            1.0,
            surface="steel",
            drop_count=2,
            test="tumble",
            spin_rps=20.0,
            orientation="random",
            seed=0,
            com_offset_m=(0, 0.002, 0.004),
        )
        self.assertEqual(len(result["drops"]), 2)
        for sample in result["trajectory"]:
            self.assertTrue(all(math.isfinite(value) for value in sample))
        for impact in result["impacts"]:
            self.assertTrue(all(math.isfinite(value) for value in impact["contact_location"]))

    def test_nan_inertia_and_gravity_rejected(self):
        bad_inertia = (
            (float("nan"), 0.0, 0.0),
            (0.0, 1e-5, 0.0),
            (0.0, 0.0, 1e-5),
        )
        with self.assertRaises(DropSimulationError):
            simulate(0.1, bad_inertia, CUBE_SUPPORT, 0.5)
        with self.assertRaises(DropSimulationError):
            simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, gravity=float("nan"))

    def test_high_drop_corner_whip_has_no_error_checks(self):
        # Regression: a 2 m corner-whip impact on a mouse-like body buried
        # itself below the plane (26 mm penetration, error-severity check on
        # a legitimate configuration) because the resting-contact branch
        # advanced the whole remaining window at the still-falling CoM
        # velocity.  The sequential-contact windows must re-resolve the
        # subsequent corner/face contacts instead.
        mouse_bounds = ((-0.06, 0.06), (-0.0325, 0.0325), (-0.02, 0.02))
        mouse_support = support_points(
            [(x, y, z) for x in mouse_bounds[0] for y in mouse_bounds[1] for z in mouse_bounds[2]]
        )
        mouse_inertia = box_inertia(0.1, mouse_bounds)
        for surface in ("concrete", "wood", "foam", "steel"):
            for orientation in ("corner", "random"):
                result = simulate(
                    0.1,
                    mouse_inertia,
                    mouse_support,
                    2.0,
                    surface=surface,
                    drop_count=3,
                    orientation=orientation,
                    seed=7,
                )
                for drop in result["drops"]:
                    for check in drop["checks"]:
                        self.assertNotEqual(
                            check["severity"], "error", "{}: {}".format(check["code"], check["message"])
                        )

    def test_tumble_default_spin_is_six_rev_per_s(self):
        # A tumble test without an explicit release spin must not degenerate
        # into a plain drop: 6 rev/s is the wrist-fling midpoint.
        config = validate_config({"test": "tumble"})
        self.assertEqual(config["spin_rps"], 6.0)
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="tumble")
        self.assertEqual(result["config"]["spin_rps"], 6.0)
        self.assertNotEqual(
            result["trajectory"],
            simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="tumble", spin_rps=0.0)["trajectory"],
        )


if __name__ == "__main__":
    unittest.main()
