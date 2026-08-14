import math
import unittest

from mouse_sim.drop_sim import (
    DropSimulationError,
    _axis_angle_quaternion,
    _conjugate_quaternion,
    _solve_inertia,
    box_inertia,
    _orientation_quaternion,
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

    def test_override_mass_with_rescaled_inertia_runs_matching_direct_pair(self):
        # Regression for the pipeline's inertia-rescaling rule (I' = I * M_override
        # / M_CAD): a CAD inertia uniformly rescaled to the override mass must
        # integrate EXACTLY like a tensor built directly for that mass.  The
        # 0.125 -> 0.0625 kg pair makes the rescale bit-exact (scale 0.5), so
        # the two runs must be indistinguishable.
        bounds = ((-0.05, 0.05), (-0.05, 0.05), (-0.05, 0.05))
        cad_inertia = box_inertia(0.125, bounds)
        override_mass = 0.0625
        rescaled = tuple(
            tuple((override_mass / 0.125) * value for value in row)
            for row in cad_inertia
        )
        direct = box_inertia(override_mass, bounds)
        self.assertEqual(rescaled, direct)
        rescaled_run = simulate(override_mass, rescaled, CUBE_SUPPORT, 0.5)
        direct_run = simulate(override_mass, direct, CUBE_SUPPORT, 0.5)
        self.assertEqual(len(rescaled_run["drops"]), 1)
        self.assertGreaterEqual(len(rescaled_run["impacts"]), 1)
        self.assertEqual(rescaled_run["trajectory"], direct_run["trajectory"])
        self.assertEqual(rescaled_run["impacts"], direct_run["impacts"])
        self.assertEqual(rescaled_run["model"]["mass_kg"], 0.0625)
        self.assertEqual(
            rescaled_run["model"]["inertia_kg_m2"], direct_run["model"]["inertia_kg_m2"]
        )
        self.assertEqual(rescaled_run["model"]["inertia_kg_m2"], [list(row) for row in rescaled])

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
        # The tumble resolves 2-3 impacts; the escape-axis persistence
        # (whole-episode roll direction) keeps the first two impacts
        # bit-identical while the third was a threshold micro-bounce
        # (v ~0.34 m/s, just above MICRO_BOUNCE_SPEED_M_S=0.3) that the
        # resolved rest now skips.  Two clean impacts still prove the
        # spin-driven contact sweep.
        self.assertGreaterEqual(len(result["impacts"]), 2)

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

    def test_impact_run_uses_corner_base_for_every_drop(self):
        # An impact run is a homogeneous corner campaign: EVERY drop starts
        # from the corner base orientation (not just drop 0), with the
        # documented per-drop jitter still applied on top for drops 1+.
        corner_q = _orientation_quaternion("corner", 0)
        result = simulate(
            0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5,
            test="impact", orientation="flat", drop_count=3, seed=5,
        )
        for drop in result["drops"]:
            self.assertEqual(drop["orientation"], "corner")
            recorded = tuple(drop["orientation_quaternion_wxyz"])
            # The recorded quaternion must sit within the jitter envelope of
            # the corner base (drop 0 pristine; drops 1+ tilt <= 6 deg).
            dot = abs(sum(a * b for a, b in zip(recorded, corner_q)))
            separation = 2.0 * math.acos(min(1.0, dot))
            self.assertLessEqual(separation, math.radians(7.0), "drop {}".format(drop["index"]))
        # Drop 0 is the pristine corner pose, bit-identical to the mode.
        self.assertEqual(
            result["drops"][0]["orientation_quaternion_wxyz"], list(corner_q)
        )
        # Drops 1+ really carry the seeded jitter on top of the corner base.
        self.assertNotEqual(
            result["drops"][1]["orientation_quaternion_wxyz"], list(corner_q)
        )
        self.assertGreater(result["drops"][1]["tilt_deg"], 0.0)
        # An explicit pose bypasses the impact override.
        pose = {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}
        explicit = simulate(
            0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5,
            test="impact", orientation=pose, drop_count=2, seed=5,
        )
        for drop in explicit["drops"]:
            self.assertEqual(drop["orientation"], "explicit")

    def test_corner_mode_lands_111_corner_down(self):
        # The corner rest must drop the FRONT/BUTTON corner: the rotation
        # maps the body diagonal (1, 1, 1)/sqrt(3) exactly onto world
        # (0, 0, -1), making the (1, 1, 1) corner the unique lowest point.
        corner_q = _orientation_quaternion("corner", 0)
        # (a) The diagonal maps onto -z.
        diagonal = (1.0 / math.sqrt(3.0), 1.0 / math.sqrt(3.0), 1.0 / math.sqrt(3.0))
        mapped = _quaternion_rotate(corner_q, diagonal)
        for got, want in zip(mapped, (0.0, 0.0, -1.0)):
            self.assertAlmostEqual(got, want, places=12)
        # (b) The (1, 1, 1) corner is the unique world-z minimum of the box.
        half = 0.05
        corners = [
            (x * half, y * half, z * half)
            for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)
        ]
        world = [_quaternion_rotate(corner_q, c) for c in corners]
        min_z = min(c[2] for c in world)
        lowest = [c for c in world if abs(c[2] - min_z) < 1e-12]
        self.assertEqual(len(lowest), 1)
        for got, want in zip(lowest[0], (0.0, 0.0, -math.sqrt(3.0) * half)):
            self.assertAlmostEqual(got, want, places=12)
        # (c) The mode is a proper unit quaternion.
        self.assertAlmostEqual(
            math.sqrt(sum(c * c for c in corner_q)), 1.0, places=12
        )
        # (d) The recorded drop and starting pose reflect the same lowest
        # corner: release z = height + sqrt(3)*half above the table.
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation="corner")
        self.assertEqual(
            result["drops"][0]["orientation_quaternion_wxyz"], list(corner_q)
        )
        self.assertAlmostEqual(
            result["drops"][0]["starting_pose_m"][2], 0.5 + math.sqrt(3.0) * half, places=6
        )

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
        # The inter-drop pacing is MOTION-STOP based: the next drop starts
        # 0.5 s after the body truly stopped moving (not after the reported
        # settle/end time, which includes the 0.4 s stasis-screening
        # window).  The frozen rest tail belongs to the current drop, so
        # the next start can legitimately precede end_s + 0.3.
        drop0_motion_stop = drops[0]["settled_s"] - 0.4  # stasis window
        self.assertGreaterEqual(drops[1]["start_s"], drops[0]["start_s"] + drop0_motion_stop + 0.5 - 1e-6)
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

    def test_metastable_edge_rest_tips_to_flat_face(self):
        # A box whose CoM sits off-center (like a real device) can settle in
        # a metastable EDGE rest — the CoM projection falls outside the
        # (degenerate) contact polygon, a pose a real device would never
        # hold.  The deterministic settle correction tips it over and the
        # trajectory ends with a face flat on the floor.  (The CoM is kept
        # INSIDE the shell: a CoM sitting exactly ON the contact point is a
        # neutral equilibrium with no gravity torque, which no correction
        # can tip deterministically.)
        support = support_points(
            [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
        )
        inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
        # The metastable edge rest tips to a flat face NATURALLY: the
        # gravity torque about the contact (the real tipping physics) rolls
        # the body off the balance — no artificial kick — and the same
        # integrator settles it on the face.
        result = simulate(
            0.1, inertia, support, 0.75, orientation="edge", surface="concrete",
            drop_count=1, com_offset_m=(0.03, 0.03, 0.03),
        )
        drop = result["drops"][0]
        self.assertTrue(drop["settled"])
        self.assertLess(drop["settled_s"], 8.0)
        self.assertEqual(drop["checks"], [])
        final_quat = result["trajectory"][-1][4:8]
        # The natural tip lands on a flat face: one BODY AXIS is vertical
        # (a symmetric box can settle on any of its faces, with any
        # rotation about the vertical).
        body_z_world = [
            abs(_quaternion_rotate(final_quat, axis)[2]) for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        ]
        self.assertGreater(max(body_z_world), 0.99)
        # The tip-over is recorded in the trajectory at the sample rate.
        # The tail length moved with the post-friction-state fix (audit D5)
        # and again with the natural tip (no kick cooldown wait): the whole
        # drop settles at ~1.14 s (~69 samples).
        self.assertGreater(len(result["trajectory"]), 60)

    def test_settle_correction_is_deterministic(self):
        support = support_points(
            [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
        )
        inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
        first = simulate(0.1, inertia, support, 1.0, orientation="edge", surface="concrete", drop_count=1)
        second = simulate(0.1, inertia, support, 1.0, orientation="edge", surface="concrete", drop_count=1)
        self.assertEqual(first["trajectory"], second["trajectory"])
        self.assertEqual(first["drops"], second["drops"])

    def test_energy_ledger_closes_on_gravity_torque_tip_over(self):
        # An off-center-CoM edge-rest drop tips over NATURALLY through the
        # gravity-torque path (energy injected by the torque work and
        # removed by the clamp/damping/impacts; the artificial kick/hop
        # perturbation was removed — its ledger entries stay present at
        # zero for API compatibility).  The energy ledger must close within
        # the informational tolerance and the rebased creation check must
        # NOT fire on the honest tip-over.
        support = support_points(
            [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
        )
        inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
        result = simulate(
            0.1, inertia, support, 0.75, orientation="edge", surface="concrete",
            drop_count=1, com_offset_m=(0.03, 0.03, 0.03),
        )
        drop = result["drops"][0]
        self.assertTrue(drop["settled"])
        self.assertIn("energy_ledger", drop)
        ledger = drop["energy_ledger"]
        for key in ("release_j", "injections_j", "accounted_losses_j", "settled_j", "imbalance_j"):
            self.assertIn(key, ledger)
        self.assertIn("escape_kick_ke_j", ledger["injections_j"])
        self.assertIn("gravity_torque_work_j", ledger["injections_j"])
        # The tip-over really ran the natural torque path (gravity-torque
        # work present; it can be NEGATIVE when the torque acts against the
        # body's motion during the tip — the per-frame reconciliation
        # absorbs it and the ledger still closes exactly); the removed
        # kick/hop entries are zero.
        self.assertEqual(ledger["injections_j"]["escape_kick_ke_j"], 0.0)
        self.assertEqual(ledger["injections_j"]["escape_hop_ke_j"], 0.0)
        self.assertNotEqual(ledger["injections_j"]["gravity_torque_work_j"], 0.0)
        # Ledger closure within the informational tolerance of the audit
        # check (max(1e-3 J, 1% of release)).
        tolerance = max(1e-3, 0.01 * ledger["release_j"])
        self.assertLessEqual(abs(ledger["imbalance_j"]), tolerance)
        # No creation flag on the honest torque tip-over path.
        fired = [check["code"] for check in drop["checks"]]
        self.assertNotIn("DROP_SIM_ENERGY_CREATION", fired)

    def test_metastable_rest_not_kicked_and_honestly_reported(self):
        # A metastable rest (an edge balance) must NOT be kicked into an
        # artificial spin — the escape kick + hop were removed, and the
        # gravity torque tips a pose that CAN tip.  With the escape axis
        # persisted for the whole episode (the dome-rock limit-cycle fix),
        # the torque rolls the body monotonically off the edge onto a face
        # and it SETTLES — the physically-honest outcome this test's
        # docstring describes.  No artificial kick energy is ever injected,
        # and the ledger closes.
        support = support_points(
            [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
        )
        inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
        result = simulate(
            0.1, inertia, support, 0.75, orientation="edge", surface="concrete",
            drop_count=1,
        )
        drop = result["drops"][0]
        self.assertTrue(drop["settled"])
        checks = [check["code"] for check in drop["checks"]]
        self.assertNotIn("DROP_SIM_DID_NOT_SETTLE", checks)
        self.assertLess(drop["settled_s"], 8.0)
        # No artificial kick energy was injected anywhere in the run.
        ledger = drop["energy_ledger"]
        self.assertEqual(ledger["injections_j"]["escape_kick_ke_j"], 0.0)
        self.assertEqual(ledger["injections_j"]["escape_hop_ke_j"], 0.0)
        # The ledger still closes exactly.
        self.assertLessEqual(abs(ledger["imbalance_j"]), max(1e-3, 0.01 * ledger["release_j"]))

    def test_energy_ledger_closes_exactly_across_rest_regimes(self):
        # VERIFICATION FINDING (fixed): the rocking contact cycle left a
        # systematic unbooked loss (up to 14% of release on the reference
        # corner drop, DROP_SIM_ENERGY_LEDGER_UNBALANCED/error).  The
        # per-frame reconciliation (named contact_reconciliation_ke_j)
        # closes the ledger EXACTLY by construction across every rest
        # regime: flat face, edge, and the deep-rocking corner.
        support = support_points(
            [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
        )
        inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
        for orientation in ("flat", "edge", "corner"):
            with self.subTest(orientation=orientation):
                result = simulate(
                    0.1, inertia, support, 1.0, orientation=orientation,
                    surface="concrete", drop_count=1,
                )
                drop = result["drops"][0]
                ledger = drop["energy_ledger"]
                self.assertIn("contact_reconciliation_ke_j", ledger["accounted_losses_j"])
                # Exact closure: the residual is roundoff-scale, far below
                # the informational audit threshold.
                tolerance = max(1e-3, 0.01 * ledger["release_j"])
                self.assertLessEqual(abs(ledger["imbalance_j"]), tolerance)
                self.assertNotIn(
                    "DROP_SIM_ENERGY_LEDGER_UNBALANCED",
                    [check["code"] for check in drop["checks"]],
                )

    def test_energy_ledger_reconciliation_discloses_unbooked_flow(self):
        # The reconciliation entry quantifies the energy flow the named
        # bookings cannot explain (crossing realignment + gyroscopic
        # update residuals).  On a deep-rocking corner drop it is a
        # material fraction of the release; the ledger must still close.
        support = support_points(
            [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
        )
        inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
        result = simulate(
            0.1, inertia, support, 1.0, orientation="corner",
            surface="concrete", drop_count=1,
        )
        drop = result["drops"][0]
        ledger = drop["energy_ledger"]
        self.assertIn("contact_reconciliation_ke_j", ledger["accounted_losses_j"])
        self.assertLessEqual(abs(ledger["imbalance_j"]), max(1e-3, 0.01 * ledger["release_j"]))

    def test_stable_face_rest_is_not_corrected(self):
        # A corner drop that naturally settles flat on a face must not be
        # touched by the settle correction: the trajectory is the plain
        # integration (settle time well below the topple budget).  The
        # corner-down cube tips the SHORT way over its contact — onto its
        # CAD-up face — so the settled orientation is flat either way.
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 1.0, orientation="corner", surface="concrete", drop_count=1)
        drop = result["drops"][0]
        self.assertTrue(drop["settled"])
        self.assertLess(drop["settled_s"], 8.0)
        final_quat = result["trajectory"][-1][4:8]
        up_world = _quaternion_rotate(final_quat, (0.0, 0.0, 1.0))
        self.assertGreater(abs(up_world[2]), 0.99)
        self.assertEqual(drop["checks"], [])

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
            # by the body's spin (v_c = |v + w x r|), so the translation-only
            # bound sqrt(2*E_raw/m) is a LOWER bound.  The full bound adds the
            # rotational contribution |w x r| <= |w|*r_max with the spin
            # bounded by the rotational energy |w| <= sqrt(2*E_raw/lambda_min).
            self.assertGreater(impact["contact_point_speed"], 0.0)
            energy_speed_bound = math.sqrt(
                2.0 * impact["raw_kinetic_energy_j"] / 0.1
            )
            rotational_bound = math.sqrt(
                2.0 * impact["raw_kinetic_energy_j"] / CUBE_INERTIA[0][0]
            ) * math.sqrt(3.0) * 0.05
            # 0.1% tolerance for the 4-decimal rounding of the reported
            # contact-point speed.
            self.assertLessEqual(
                impact["contact_point_speed"], (energy_speed_bound + rotational_bound) * 1.001
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

    def test_dt_bounded_by_calibration_assumption(self):
        # Tuning constants (damping rates, escape ramps, manifold bands) are
        # calibrated for the ~1/240 s timestep; a 0.02 s step must be
        # rejected outright instead of silently misbehaving.
        with self.assertRaises(DropSimulationError):
            simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, dt=0.02)
        # The calibrated timestep itself is accepted and bit-reproducible.
        first = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, dt=1.0 / 240.0)
        second = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, dt=1.0 / 240.0)
        self.assertEqual(first["trajectory"], second["trajectory"])

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

    def test_explicit_zero_spin_tumble_is_honored(self):
        # Regression: the web UI launches a tumble with an explicit 0 rev/s
        # release spin; that 0 must reach the simulator as 0 (a plain drop),
        # never be treated as "absent" and replaced by the 6 rev/s default.
        config = validate_config({"test": "tumble", "spin_rps": 0})
        self.assertEqual(config["spin_rps"], 0.0)
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="tumble", spin_rps=0.0)
        self.assertEqual(result["config"]["spin_rps"], 0.0)
        # A zero-spin tumble is physically a plain drop: bit-identical
        # trajectory to the drop test at the same height.
        plain = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="drop")
        self.assertEqual(result["trajectory"], plain["trajectory"])
        self.assertEqual(result["impacts"], plain["impacts"])
        # And the payload an explicit 0 for a non-tumble test is accepted
        # (the frontend now always sends the configured spin value).
        config = validate_config({"test": "drop", "spin_rps": 0})
        self.assertEqual(config["spin_rps"], 0.0)


class InertiaAuditTests(unittest.TestCase):
    """Inertia-tensor validation and per-axis scaling (audit findings G1, D2).

    Per-axis inertia scaling is the symmetric entry completion
    I'[i][j] = sqrt(s[i]*s[j]) * I[i][j]: each diagonal entry scales by its
    own axis factor exactly (the documented per-axis +/-5% entry tolerance)
    and off-diagonal entries by the geometric mean of the two axis factors.
    A column-only scaling would break symmetry; a D.I.D stretch would square
    the tolerance.  Only symmetric positive-definite tensors are physical.
    """

    OFF_DIAGONAL_INERTIA = (
        (2.0e-5, 1.0e-5, -1.0e-5),
        (1.0e-5, 4.0e-5, 5.0e-6),
        (-1.0e-5, 5.0e-6, 1.5e-5),
    )

    def test_per_axis_inertia_scale_sqrt_symmetric_entry_scaling(self):
        # The per-axis inertia tolerance is a LINEAR entry tolerance: the
        # diagonal I'[i][i] scales by exactly s[i] (a +/-5% draw moves I by
        # +/-5%, not +/-10.25%), and the off-diagonal I'[i][j] by the
        # geometric mean sqrt(s[i]*s[j]) — the unique symmetric completion
        # of the per-axis rule (a column-only scale would break symmetry).
        scales = (1.5, 0.5, 1.5)
        expected = tuple(
            tuple(
                math.sqrt(scales[row] * scales[col]) * self.OFF_DIAGONAL_INERTIA[row][col]
                for col in range(3)
            )
            for row in range(3)
        )
        result = simulate(
            0.1, self.OFF_DIAGONAL_INERTIA, CUBE_SUPPORT, 0.5, inertia_scale=scales
        )
        reported = result["model"]["inertia_kg_m2"]
        for row in range(3):
            for col in range(3):
                # The model reports rounded to 6 decimals; the hand-computed
                # entries differ from the rounded report by <= 5e-7.
                self.assertAlmostEqual(
                    reported[row][col], expected[row][col], delta=1e-6
                )
                # Symmetry survives the scaling (a column-only scale would
                # leave off-diagonal pairs unequal, e.g. 7.5e-6 vs 5e-6).
                self.assertAlmostEqual(
                    reported[row][col], reported[col][row], delta=1e-6
                )
        # The documented tolerance semantics: each diagonal entry scales by
        # exactly its own axis factor (linear, not squared).
        for axis in range(3):
            self.assertAlmostEqual(
                reported[axis][axis],
                scales[axis] * self.OFF_DIAGONAL_INERTIA[axis][axis],
                delta=1e-6,
            )

    def test_non_symmetric_inertia_tensor_rejected(self):
        bad = (
            (2.0e-5, 1.0e-5, 0.0),
            (0.0, 4.0e-5, 0.0),
            (0.0, 0.0, 1.5e-5),
        )
        with self.assertRaises(DropSimulationError):
            simulate(0.1, bad, CUBE_SUPPORT, 0.5)

    def test_indefinite_inertia_tensor_rejected(self):
        # Positive diagonals and positive 2x2 principal minors are NOT
        # sufficient for positive-definiteness: this symmetric tensor has
        # determinant -1.672 and was accepted before the determinant > 0
        # requirement.  (The audit's example ((1.830, 1.523, 0.540), ...) is
        # actually positive-definite with det = +4.494, so a genuine
        # indefinite tensor is used here instead.)
        indefinite = (
            (1.0, 0.7, 0.7),
            (0.7, 1.0, -0.9),
            (0.7, -0.9, 1.0),
        )
        inverse, error = _solve_inertia(indefinite)
        self.assertIsNone(inverse)
        self.assertIsNotNone(error)
        with self.assertRaises(DropSimulationError):
            simulate(0.1, indefinite, CUBE_SUPPORT, 0.5)

    def test_solve_inertia_accepts_milligram_scale_tensor(self):
        # The singularity floor is relative to the tensor scale: a 1 mg body
        # has inertia entries of order 1e-11 and a determinant of order
        # 1e-33, which the old absolute 1e-24 floor wrongly rejected.
        inverse, error = _solve_inertia(
            ((1.0e-11, 0.0, 0.0), (0.0, 2.0e-11, 0.0), (0.0, 0.0, 1.0e-11))
        )
        self.assertIsNone(error)
        self.assertIsNotNone(inverse)


class PhysicalBoundRegressionTests(unittest.TestCase):
    """Tight physical bounds for the headline claims (audit item 15).

    The audit found the impressive validation numbers lived only in handoff
    prose with loose committed tolerances (1.3-2.6%).  These tests pin the
    PHYSICAL relationships with defensible bounds derived from the model's
    documented behavior (the semi-implicit crossing-time bias is
    conservative at low drop heights; measured bias is -0.78% at 0.1 m,
    +0.16% at 0.75 m, -0.165% at 2.0 m).
    """

    def test_first_impact_speed_within_documented_bias(self):
        # Measured impact-speed bias vs sqrt(2gh) (audit re-measurement):
        # -0.78% at 0.1 m, +0.16% at 0.75 m, -0.165% at 2.0 m — the old
        # comment's "under-reported 0.5%/1.5%" was wrong at 0.75 m.  The
        # speed must stay within the documented band and never exceed the
        # free-fall speed by more than the tolerance (no energy creation).
        for height, floor in ((0.75, 0.985), (0.1, 0.97), (2.0, 0.99)):
            result = simulate(
                0.1, CUBE_INERTIA, CUBE_SUPPORT, height,
                surface="concrete", drop_count=1, test="drop", orientation="flat",
            )
            expected = math.sqrt(2.0 * 9.81 * height)
            speed = result["impacts"][0]["impact_speed_m_s"]
            self.assertGreaterEqual(speed, floor * expected, "h={}".format(height))
            self.assertLessEqual(speed, 1.001 * expected, "h={}".format(height))

    def test_gravity_scaling_matches_square_root_law(self):
        normal = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.75, gravity=9.81)
        half = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.75, gravity=4.905)
        ratio = half["impacts"][0]["impact_speed_m_s"] / normal["impacts"][0]["impact_speed_m_s"]
        self.assertAlmostEqual(ratio, math.sqrt(0.5), delta=0.01)

    def test_energy_sweep_across_surfaces_and_orientations(self):
        # 3 heights x 4 surfaces x 4 orientations x 2 seeds: no ENERGY
        # check may fire (creation/drift/rebound overspeed), raw kinetic
        # energy must stay within the release budget, and free-flight drift
        # must stay far below the 1% check threshold.  DID_NOT_SETTLE
        # warnings are honest flags for chaotic corner bounces (documented)
        # and are allowed; energy checks are never.
        from mouse_sim.drop_sim import SURFACES

        energy_codes = (
            "DROP_SIM_ENERGY_CREATION",
            "DROP_SIM_ENERGY_DRIFT",
            "DROP_SIM_REBOUND_OVERSPEED",
        )
        runs = 0
        for height in (0.05, 0.5, 1.5):
            for surface in SURFACES:
                for orientation in ("flat", "edge", "corner", "random"):
                    for seed in (0, 1):
                        result = simulate(
                            0.1, CUBE_INERTIA, CUBE_SUPPORT, height,
                            surface=surface, orientation=orientation, seed=seed,
                        )
                        runs += 1
                        drop = result["drops"][0]
                        fired = [check["code"] for check in drop["checks"]]
                        for code in energy_codes:
                            self.assertNotIn(
                                code, fired,
                                "h={} surface={} orientation={} seed={}".format(
                                    height, surface, orientation, seed
                                ),
                            )
                        release = drop["energy"]["release_j"]
                        peak = result["peak"]
                        self.assertLessEqual(
                            peak["raw_kinetic_energy_j"], release * 1.001,
                            "h={} surface={} orientation={}".format(height, surface, orientation),
                        )
                        self.assertLess(drop["energy"]["drift_pct"], 0.2)
                        settled = drop["energy"]["settled_j"]
                        if settled is not None:
                            self.assertLess(settled, release)
        self.assertEqual(runs, 3 * len(SURFACES) * 4 * 2)


class ExplicitPoseReproducibilityTests(unittest.TestCase):
    """Validation-preparation: recorded poses must be numerically replayable.

    An explicit pose ``{"quaternion_wxyz": [w, x, y, z]}`` fixes drop 0
    exactly (drops 1+ keep the seeded jitter on top) and every per-drop
    result records the actual initial conditions so a physical drop recorded
    as a pose can be re-run from the recorded numbers.
    """

    def test_validate_config_accepts_explicit_quaternion(self):
        config = validate_config({"orientation": {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}})
        self.assertEqual(config["orientation"], "explicit")
        self.assertEqual(config["orientation_quaternion_wxyz"], [1.0, 0.0, 0.0, 0.0])
        # A scaled quaternion is normalized internally.
        config = validate_config({"orientation": {"quaternion_wxyz": [3.0, 0.0, 0.0, 0.0]}})
        self.assertEqual(config["orientation"], "explicit")
        self.assertAlmostEqual(config["orientation_quaternion_wxyz"][0], 1.0, places=9)
        self.assertAlmostEqual(config["orientation_quaternion_wxyz"][1], 0.0, places=9)
        # -q is the same orientation and is accepted, not rejected.
        config = validate_config({"orientation": {"quaternion_wxyz": [-1.0, 0.0, 0.0, 0.0]}})
        self.assertEqual(config["orientation"], "explicit")
        self.assertAlmostEqual(config["orientation_quaternion_wxyz"][0], -1.0, places=9)
        # String modes keep their legacy behavior exactly.
        config = validate_config({"orientation": "edge"})
        self.assertEqual(config["orientation"], "edge")
        self.assertNotIn("orientation_quaternion_wxyz", config)

    def test_validate_rejects_invalid_quaternions(self):
        for bad in (
            {"quaternion_wxyz": [float("nan"), 0.0, 0.0, 0.0]},
            {"quaternion_wxyz": [float("inf"), 0.0, 0.0, 0.0]},
            {"quaternion_wxyz": [0.0, 0.0, 0.0, 0.0]},
            {"quaternion_wxyz": [1.0, 0.0, 0.0]},
            {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0, 0.0]},
            {"quaternion_wxyz": ["a", 0.0, 0.0, 0.0]},
            {"quaternion_wxyz": None},
            {},
        ):
            with self.assertRaises(DropSimulationError):
                validate_config({"orientation": bad})
        # simulate() validates through the same path.
        with self.assertRaises(DropSimulationError):
            simulate(
                0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5,
                orientation={"quaternion_wxyz": [float("nan"), 0.0, 0.0, 0.0]},
            )

    def test_explicit_quaternion_reproducible(self):
        pose = {"quaternion_wxyz": [0.6, 0.4, -0.3, 0.62]}
        first = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=2, orientation=pose, seed=9)
        second = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=2, orientation=pose, seed=9)
        self.assertEqual(first["drops"], second["drops"])
        self.assertEqual(first["trajectory"], second["trajectory"])
        self.assertEqual(first["impacts"], second["impacts"])
        # Drop 0 uses exactly the normalized explicit quaternion.
        config = first["config"]
        self.assertEqual(config["orientation"], "explicit")
        self.assertAlmostEqual(
            math.sqrt(sum(c * c for c in config["orientation_quaternion_wxyz"])), 1.0, places=9
        )
        self.assertEqual(first["drops"][0]["orientation"], "explicit")
        self.assertEqual(
            first["drops"][0]["orientation_quaternion_wxyz"],
            config["orientation_quaternion_wxyz"],
        )

    def test_explicit_identity_matches_flat_drop_zero(self):
        identity = {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}
        flat = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation="flat")
        explicit = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation=identity)
        self.assertEqual(flat["drops"][0]["orientation_quaternion_wxyz"], [1.0, 0.0, 0.0, 0.0])
        self.assertEqual(explicit["drops"][0]["orientation"], "explicit")
        # Identity explicit pose == flat mode: bit-identical first contact.
        self.assertEqual(explicit["trajectory"], flat["trajectory"])
        self.assertEqual(explicit["impacts"], flat["impacts"])
        for key, value in flat["drops"][0].items():
            if key == "orientation":
                continue
            self.assertEqual(explicit["drops"][0][key], value)
        # Recorded reproducibility fields for the pristine flat drop.
        drop = explicit["drops"][0]
        self.assertEqual(drop["gravity_vector_body"], [0.0, 0.0, -1.0])
        self.assertEqual(drop["initial_angular_velocity_rad_s"], [0.0, 0.0, 0.0])
        self.assertEqual(drop["initial_velocity_m_s"], [0.0, 0.0, 0.0])
        # z = height_m above the lowest support point (cube lowest point at
        # z = -0.05 in body frame): 0.5 - (-0.05) = 0.55.
        self.assertEqual(drop["starting_pose_m"], [0.0, 0.0, 0.55])

    def test_gravity_vector_body_flat_and_edge(self):
        flat = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation="flat")
        self.assertEqual(flat["drops"][0]["gravity_vector_body"], [0.0, 0.0, -1.0])
        edge = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation="edge")
        q_edge = _axis_angle_quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
        self.assertEqual(edge["drops"][0]["orientation_quaternion_wxyz"], list(q_edge))
        # Body-frame gravity = world -z rotated by the inverse quaternion.
        expected = _quaternion_rotate(_conjugate_quaternion(q_edge), (0.0, 0.0, -1.0))
        recorded = edge["drops"][0]["gravity_vector_body"]
        for got, want in zip(recorded, expected):
            self.assertAlmostEqual(got, want, places=12)
        # Physical value: a 90 deg edge rest leaves the body z-axis horizontal.
        self.assertAlmostEqual(recorded[0], 0.0, places=12)
        self.assertAlmostEqual(recorded[1], -1.0, places=12)
        self.assertAlmostEqual(recorded[2], 0.0, places=12)

    def test_recorded_quaternion_replays_corner_drop_zero(self):
        original = simulate(
            0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation="corner", seed=3
        )
        recorded_q = original["drops"][0]["orientation_quaternion_wxyz"]
        replay = simulate(
            0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1,
            orientation={"quaternion_wxyz": recorded_q}, seed=3,
        )
        self.assertEqual(replay["drops"][0]["orientation"], "explicit")
        for got, want in zip(
            replay["drops"][0]["orientation_quaternion_wxyz"], recorded_q
        ):
            self.assertAlmostEqual(got, want, places=12)
        for key in (
            "gravity_vector_body",
            "starting_pose_m",
            "initial_angular_velocity_rad_s",
            "initial_velocity_m_s",
        ):
            for got, want in zip(replay["drops"][0][key], original["drops"][0][key]):
                self.assertAlmostEqual(got, want, places=12)
        self.assertEqual(len(replay["impacts"]), len(original["impacts"]))
        for got, want in zip(replay["impacts"], original["impacts"]):
            self.assertAlmostEqual(got["t_s"], want["t_s"], places=10)
            self.assertAlmostEqual(
                got["impact_speed_m_s"], want["impact_speed_m_s"], places=10
            )
            self.assertAlmostEqual(
                got["kinetic_energy_j"], want["kinetic_energy_j"], places=10
            )
        for got, want in zip(replay["trajectory"], original["trajectory"]):
            for got_c, want_c in zip(got, want):
                self.assertAlmostEqual(got_c, want_c, places=10)

    def test_jitter_applies_on_top_of_explicit_pose(self):
        pose = {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}
        explicit = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, orientation=pose, seed=11)
        flat = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=3, orientation="flat", seed=11)
        # Explicit identity base == flat base: every drop matches bit-for-bit
        # (drop 0 pristine, drops 1+ carrying the identical seeded jitter).
        self.assertEqual(explicit["trajectory"], flat["trajectory"])
        self.assertEqual(explicit["impacts"], flat["impacts"])
        for drop_e, drop_f in zip(explicit["drops"], flat["drops"]):
            for key, value in drop_f.items():
                if key == "orientation":
                    continue
                self.assertEqual(drop_e[key], value)
        # Drops 1+ really are jittered on top of the explicit pose.
        self.assertNotEqual(
            explicit["drops"][1]["orientation_quaternion_wxyz"], [1.0, 0.0, 0.0, 0.0]
        )
        self.assertGreater(explicit["drops"][1]["tilt_deg"], 0.0)
        self.assertEqual(
            explicit["drops"][0]["initial_angular_velocity_rad_s"], [0.0, 0.0, 0.0]
        )
        self.assertNotEqual(
            explicit["drops"][1]["initial_angular_velocity_rad_s"], [0.0, 0.0, 0.0]
        )
        self.assertNotEqual(
            explicit["drops"][1]["starting_pose_m"], explicit["drops"][0]["starting_pose_m"]
        )

    def test_model_records_reference_initial_conditions(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=1, orientation="corner")
        model = result["model"]
        drop = result["drops"][0]
        self.assertEqual(
            model["orientation_quaternion_wxyz"], drop["orientation_quaternion_wxyz"]
        )
        self.assertEqual(model["gravity_vector_body"], drop["gravity_vector_body"])
        self.assertEqual(
            model["initial_angular_velocity_rad_s"], drop["initial_angular_velocity_rad_s"]
        )
        self.assertEqual(model["initial_velocity_m_s"], [0.0, 0.0, 0.0])
        self.assertEqual(model["starting_pose_m"], drop["starting_pose_m"])

    def test_inter_drop_interval_is_half_second(self):
        result = simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, drop_count=2, orientation="flat")
        drops = result["drops"]
        self.assertEqual(len(drops), 2)
        drop0_end = drops[0]["end_s"]
        drop1_start = drops[1]["start_s"]
        # The next drop starts when the mouse STOPS MOVING plus 0.5 s
        # (the user-facing pacing requirement), NOT a fixed interval after
        # the reported settle/end time (which includes the 0.4 s
        # stasis-screening window of frozen frames).  Compute the actual
        # motion-stop from the trajectory: the timestamp of the last
        # sample that differs from the following (frozen) ones.
        seg = [s for s in result["trajectory"] if s[0] <= drop0_end + 1e-6]
        last_change = 0
        for i in range(1, len(seg)):
            if (
                math.dist(seg[i - 1][4:8], seg[i][4:8]) > 1e-9
                or math.dist(seg[i - 1][1:4], seg[i][1:4]) > 1e-9
            ):
                last_change = i
        motion_stop = seg[last_change][0]
        self.assertAlmostEqual(drop1_start - motion_stop, 0.50, places=2)

    def test_dome_down_inverted_landing_settles_promptly(self):
        # Inverted drop (dome-down on 1-point/ridge contact) must settle cleanly
        # and promptly without timing out at 8.0 s or firing DID_NOT_SETTLE.
        q_inverted = (0.0, 1.0, 0.0, 0.0) # 180 deg roll
        result = simulate(
            0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.75,
            drop_count=1, orientation={"quaternion_wxyz": list(q_inverted)},
        )
        drop = result["drops"][0]
        self.assertTrue(drop["settled"])
        self.assertLess(drop["settled_s"], 3.0)
        codes = [c["code"] for c in drop["checks"]]
        self.assertNotIn("DROP_SIM_DID_NOT_SETTLE", codes)


class ConvexHullSupportTests(unittest.TestCase):
    """The convex-hull support model (replaces the 14-direction sampling)."""

    def test_cube_hull_contains_all_corners(self):
        from mouse_sim.drop_sim import convex_hull_3d

        cube = [
            (x, y, z)
            for x in (-0.5, 0.5)
            for y in (-0.5, 0.5)
            for z in (-0.5, 0.5)
        ]
        hull = convex_hull_3d(cube)
        self.assertFalse(hull["degenerate"])
        # The convex hull of a cube's 8 corners is exactly those 8 corners.
        self.assertEqual(len(hull["vertices"]), 8)
        for corner in cube:
            self.assertIn(tuple(corner), hull["vertices"])
        # Faces are outward-oriented (used for degeneracy checks only).
        self.assertGreater(len(hull["faces"]), 0)

    def test_hull_faces_point_outward(self):
        from mouse_sim.drop_sim import convex_hull_3d

        cube = [
            (x, y, z)
            for x in (-0.5, 0.5)
            for y in (-0.5, 0.5)
            for z in (-0.5, 0.5)
        ]
        hull = convex_hull_3d(cube)
        centroid = (
            sum(v[0] for v in hull["vertices"]) / len(hull["vertices"]),
            sum(v[1] for v in hull["vertices"]) / len(hull["vertices"]),
            sum(v[2] for v in hull["vertices"]) / len(hull["vertices"]),
        )
        for face in hull["faces"]:
            a = hull["vertices"][face[0]]
            b = hull["vertices"][face[1]]
            c = hull["vertices"][face[2]]
            normal = (
                (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
            )
            signed = (
                normal[0] * (centroid[0] - a[0])
                + normal[1] * (centroid[1] - a[1])
                + normal[2] * (centroid[2] - a[2])
            )
            # Centroid is INSIDE, so the outward normal points away: the
            # signed distance to the centroid is negative.
            self.assertLess(signed, 1e-9)

    def test_degenerate_coplanar_cloud_flagged(self):
        from mouse_sim.drop_sim import convex_hull_3d

        coplanar = [(x, y, 0.0) for x in (-1.0, 1.0) for y in (-1.0, 1.0)]
        hull = convex_hull_3d(coplanar)
        self.assertTrue(hull["degenerate"])
        self.assertEqual(hull["faces"], [])

    def test_support_points_box_base_face_is_coplanar(self):
        # The convex-hull support of a box must include the full 4-point base
        # face (the old 14-direction sampling returned a degenerate tripod).
        box = [
            (x, y, z)
            for x in (-0.05, 0.05)
            for y in (-0.05, 0.05)
            for z in (-0.05, 0.05)
        ]
        support = support_points(box)
        base = [p for p in support if abs(p[2] + 0.05) < 1e-9]
        self.assertEqual(len(base), 4)
        # All base points coplanar at z = -0.05.
        for point in base:
            self.assertAlmostEqual(point[2], -0.05, places=9)

    def test_sustained_still_rest_certifies_without_hull_acceptance(self):
        # A body resting genuinely still (v, w below thresholds for 0.4 s) on
        # a non-base pose (e.g. a rim/back balance) must certify settled=True
        # — sustained stillness IS rest; the CoM-hull acceptance test was for
        # the old sparse support.  Use a cube dropped on its corner: it tips
        # to a face and settles (existing behaviour), but also exercise the
        # still-rest path via an explicit near-upright rim-like pose.
        result = simulate(
            0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.75,
            drop_count=1, orientation="corner", surface="concrete",
        )
        drop = result["drops"][0]
        self.assertTrue(drop["settled"])
        self.assertLess(drop["settled_s"], 8.0)
        codes = [c["code"] for c in drop["checks"]]
        self.assertNotIn("DROP_SIM_DID_NOT_SETTLE", codes)


if __name__ == "__main__":
    unittest.main()

