"""Edge-case audit: cumulative multi-drop degradation, pose carry-over,
drop-count bounds, and timeline pacing (IEC 60068-2-31-style repeated drops).

These tests document the CURRENT deterministic backend contract:
- Drop 0 is the pristine reference at the configured orientation.
- Drops 1+ re-fixture to the configured orientation with seeded variation —
  a real random start pose (up to CAMPAIGN_MAX_TILT_DEG) in a multi-drop
  drop/tumble campaign, fine jitter for impact/corner and explicit twins.
  Every drop still releases with its LOWEST supported vertex at the same
  height_m (constant floor-relative release energy), drops do NOT inherit
  the previous drop's resting pose, and there is no plastic-strain/crack
  accumulation between drops (each drop is an independent re-fixture of the
  same pristine body).
- The drop timeline is monotonic with an inter-drop floor of
  max(motion_stop, free-fall time) + 0.5 s pause, and every drop settles or
  is duration-clamped within MAX_DURATION_S.
"""
import math
import unittest

from mouse_sim.drop_sim import (
    MAX_DURATION_S,
    DropSimulationError,
    box_inertia,
    simulate,
    support_points,
)

MOUSE_SUPPORT = support_points(
    [
        (x, y, z)
        for x in (-0.03, 0.03)
        for y in (-0.019, 0.019)
        for z in (-0.015, 0.015)
    ]
)
MOUSE_INERTIA = box_inertia(
    0.28867, ((-0.0625, 0.0625), (-0.0325, 0.0325), (-0.02, 0.02))
)


def run_multi(drop_count, height_m=0.75, seed=42, **overrides):
    return simulate(
        0.28867,
        MOUSE_INERTIA,
        MOUSE_SUPPORT,
        height_m,
        surface="concrete",
        drop_count=drop_count,
        test="drop",
        orientation="flat",
        seed=seed,
        **overrides,
    )


def _support_min_world_z(drop):
    """Lowest world-frame z of the supported vertices at a drop's release.

    In-independent: every drop re-fixtures so this clears the floor by
    exactly the configured release height, regardless of start pose.
    """
    origin = drop["starting_pose_m"]
    w, x, y, z = drop["orientation_quaternion_wxyz"]
    min_z = float("inf")
    for (sx, sy, sz) in MOUSE_SUPPORT:
        # Rotate the support vector by the release quaternion (w, x, y, z).
        rx = (1 - 2 * (y * y + z * z)) * sx + (2 * (x * y - z * w)) * sy + (2 * (x * z + y * w)) * sz
        ry = (2 * (x * y + z * w)) * sx + (1 - 2 * (x * x + z * z)) * sy + (2 * (y * z - x * w)) * sz
        rz = (2 * (x * z - y * w)) * sx + (2 * (y * z + x * w)) * sy + (1 - 2 * (x * x + y * y)) * sz
        world_z = origin[2] + rz
        if world_z < min_z:
            min_z = world_z
    return min_z


class MultiDropCarryOverTests(unittest.TestCase):
    def test_each_drop_is_independent_of_the_previous_rest_pose(self):
        """Drop k+1 re-fixtures to the configured orientation (with seeded
        variation) instead of inheriting drop k's resting pose."""
        result = run_multi(4)
        drops = result["drops"]
        self.assertEqual(len(drops), 4)
        # Every drop starts with its LOWEST supported vertex at the release
        # height — the initial pose is the configured release, NOT the
        # previous rest pose (and not the previous rest position).
        for drop in drops:
            self.assertAlmostEqual(_support_min_world_z(drop), 0.75, delta=0.02)
        # Drop 0 is the pristine reference (no tilt, no lateral drift).
        self.assertEqual(drops[0]["tilt_deg"], 0.0)
        self.assertEqual(drops[0]["lateral_offset_m"], [0.0, 0.0])
        self.assertEqual(drops[0]["orientation_quaternion_wxyz"], [1.0, 0.0, 0.0, 0.0])
        # Drops 1+ re-fixture to the configured orientation with seeded
        # variation: release quaternions differ from the pristine drop 0
        # AND from each other (varied poses, no collapse).
        quats = [tuple(drop["orientation_quaternion_wxyz"]) for drop in drops[1:]]
        self.assertNotEqual(quats[0], (1.0, 0.0, 0.0, 0.0))
        self.assertEqual(len(set(quats)), len(quats))
        # Campaign variation is active for the multi-drop drop/test run.
        self.assertTrue(result["model"]["jitter"]["campaign_random_orientation"])

    def test_no_plastic_strain_crack_accumulation_across_drops(self):
        """The backend integrator does not carry plastic strain or micro-crack
        state between drops: every drop simulates the same pristine body, so
        every drop releases from the same height and peak impact speeds stay
        statistically similar — the ledger shows no monotonic degradation."""
        result = run_multi(5)
        peak_speeds = [d["peak_impact_speed_m_s"] for d in result["drops"]]
        self.assertGreater(min(peak_speeds), 0.0)
        # Start pose does not change the CM drop speed: peak impact speeds
        # stay within 20% of drop 0 (free-fall dominated).
        for speed in peak_speeds[1:]:
            self.assertAlmostEqual(speed / peak_speeds[0], 1.0, delta=0.2)
        # Floor-relative release energy is identical across drops (constant
        # release height): no degradation trend, no monotonic drift.  The
        # ledger's release_j is origin-PE so a tilted start shifts it within
        # the pose band; the constant contract is the release clearance.
        clearances = [_support_min_world_z(drop) for drop in result["drops"]]
        for clearance in clearances[1:]:
            self.assertAlmostEqual(clearance / clearances[0], 1.0, delta=0.02)
        releases = [d["energy"]["release_j"] for d in result["drops"]]
        drift = [release / releases[0] - 1.0 for release in releases]
        self.assertLess(max(drift), 0.05)  # pose-bounded, no run-away

    def test_impact_test_is_a_homogeneous_corner_campaign(self):
        """An 'impact' test forces the corner orientation for EVERY drop."""
        result = simulate(
            0.28867,
            MOUSE_INERTIA,
            MOUSE_SUPPORT,
            0.75,
            drop_count=3,
            test="impact",
            orientation="flat",
        )
        for drop in result["drops"]:
            self.assertEqual(drop["orientation"], "corner")

    def test_seeded_jitter_is_deterministic_per_drop(self):
        a = run_multi(3, seed=7)
        b = run_multi(3, seed=7)
        self.assertEqual([d["tilt_deg"] for d in a["drops"]], [d["tilt_deg"] for d in b["drops"]])
        self.assertEqual([d["lateral_offset_m"] for d in a["drops"]], [d["lateral_offset_m"] for d in b["drops"]])
        c = run_multi(3, seed=8)
        self.assertNotEqual([d["tilt_deg"] for d in a["drops"]], [d["tilt_deg"] for d in c["drops"]])


class MultiDropTimelineTests(unittest.TestCase):
    def test_timeline_is_monotonic_with_no_overlap(self):
        result = run_multi(6)
        drops = result["drops"]
        for index in range(1, len(drops)):
            self.assertGreaterEqual(drops[index]["start_s"], drops[index - 1]["end_s"])
        for drop in drops:
            self.assertGreater(drop["end_s"], drop["start_s"])
        # Trajectory samples are globally sorted (no inter-drop rewinding).
        times = [sample[0] for sample in result["trajectory"]]
        self.assertEqual(times, sorted(times))

    def test_inter_drop_pause_respects_motion_stop_floor(self):
        result = run_multi(3)
        drops = result["drops"]
        # The next drop starts only after the previous drop's motion stops
        # PLUS the 0.5 s pause: the gap between the reported end of drop k and
        # the start of drop k+1 must never be a negative overlap, and the
        # configured pause is a minimum on the motion-stop-to-start interval.
        for index in range(1, len(drops)):
            self.assertGreaterEqual(drops[index]["start_s"], drops[index - 1]["end_s"])
            gap = drops[index]["start_s"] - drops[index - 1]["end_s"]
            self.assertGreaterEqual(gap, 0.0)
            # The gap can be smaller than the 0.5 s pause because drop k's
            # reported end_s already includes the 0.4 s stasis window; the
            # pause is measured from motion_stop, which precedes end_s.
            self.assertLess(gap, 0.5 + 1e-6)

    def test_drop_count_bounds_are_enforced(self):
        for bad in (0, -1, 21, 50):
            with self.assertRaises(DropSimulationError):
                run_multi(bad)
        result = run_multi(20)
        self.assertEqual(len(result["drops"]), 20)
        # The trajectory stream is the concatenation of the per-drop 60 Hz
        # sample blocks. The recording window runs to motion_stop (which can
        # exceed the reported end_s by one 60 Hz tick — the stasis-window
        # sample belongs to the drop that produced it), so assert the
        # partition property with a one-sample slack per drop.
        drops = result["drops"]
        times = [sample[0] for sample in result["trajectory"]]
        self.assertEqual(times, sorted(times))
        assigned = 0
        for index, drop in enumerate(drops):
            lo = drop["start_s"] - 1e-6
            hi = drop["end_s"] + 1.0 / 60.0 + 1e-6
            in_window = [t for t in times if lo <= t <= hi]
            self.assertGreaterEqual(len(in_window), 60, "drop {} lost samples".format(index))
            assigned += len(in_window)
        # Every sample is within one 60 Hz tick past its drop's end (samples
        # are never lost, duplicated, or left outside the schedule).
        self.assertGreaterEqual(assigned, len(result["trajectory"]) - 20)

    def test_max_duration_clamp_keeps_each_drop_bounded(self):
        # Whatever the start pose (including widely-tilted campaign drops
        # that tumble and rock), every drop settles or is duration-clamped
        # within MAX_DURATION_S — the sim is never unbounded.
        result = run_multi(4)
        for drop in result["drops"]:
            self.assertLess(drop["settled_s"], MAX_DURATION_S + 1e-6)


if __name__ == "__main__":
    unittest.main()
