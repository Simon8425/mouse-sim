"""Tests for the lifecycle usage/degradation model and its pipeline wiring."""

import unittest

from mouse_sim import lifecycle
from mouse_sim.pipeline import run_pipeline

BASELINE = {
    "schema_id": "gms.project/1",
    "mode": "exploration",
    "units": "m",
    "objects": [
        {
            "id": "shell",
            "geometry": {
                "type": "box",
                "size": [0.1, 0.06, 0.035],
                "units": "m",
            },
            "material": "ABS",
        },
    ],
}


class LifecycleModelTests(unittest.TestCase):
    def test_fatigue_damage_index_zero_for_fresh_unit(self):
        self.assertEqual(lifecycle.fatigue_damage_index(0, 0.0), 0.0)
        self.assertEqual(lifecycle.fatigue_damage_index(0, 50.0), 0.0)
        self.assertEqual(lifecycle.fatigue_damage_index(10, 0.0), 0.0)

    def test_fatigue_damage_accumulates_with_energy(self):
        small = lifecycle.fatigue_damage_index(1, 0.5)
        large = lifecycle.fatigue_damage_index(1, 50.0)
        self.assertGreater(large, small)
        self.assertGreater(small, 0.0)

    def test_fatigue_damage_is_linear_in_drop_count(self):
        # 64 drops of 0.5 J each: Ebar = 0.5 J, N = 1e6, D = 64/1e6.
        sixty_four = lifecycle.fatigue_damage_index(64, 32.0)
        self.assertAlmostEqual(sixty_four, 64.0 / 1e6, places=12)
        # Doubling the count doubles the damage (no n^2.5 inflation).
        one_twenty_eight = lifecycle.fatigue_damage_index(128, 64.0)
        self.assertAlmostEqual(one_twenty_eight, 2.0 * sixty_four, places=12)

    def test_fatigue_damage_average_energy_law(self):
        # One event at 2 J: N = 1e6*(0.5/2)^2.5 = 1e6/32 = 31250, D = 1/31250.
        damage = lifecycle.fatigue_damage_index(1, 2.0)
        self.assertAlmostEqual(damage, 1.0 / 31250.0, places=12)
        # Eight events at 2 J have the same average: D = 8/31250 (linear).
        eight = lifecycle.fatigue_damage_index(8, 16.0)
        self.assertAlmostEqual(eight, 8.0 / 31250.0, places=12)

    def test_fatigue_damage_inflation_regression(self):
        # Audit regression: the old lumped law reported D ~ 0.979 for this
        # history (n^2.5 inflation, ~33,000x); the event-wise law is linear.
        damage = lifecycle.fatigue_damage_index(64, 32.0)
        self.assertLess(damage, 1e-3)
        self.assertAlmostEqual(damage, 64.0 / 1e6, places=12)


class EventWiseFatigueTests(unittest.TestCase):
    """Mathematical properties of the event-wise Miner accumulation."""

    def test_event_wise_matches_hand_calculation(self):
        # One event at 2 J: N = 1e6*(0.5/2)^2.5 = 31250, D = 1/31250.
        damage = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[2.0])
        self.assertAlmostEqual(damage, 1.0 / 31250.0, places=12)
        # 100 identical events at 0.5 J: D = 100/1e6.
        damage = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[0.5] * 100)
        self.assertAlmostEqual(damage, 100.0 / 1e6, places=12)

    def test_damage_is_monotone_in_events(self):
        import random
        rng = random.Random(7)
        history = []
        previous = 0.0
        for _ in range(200):
            history.append(10.0 ** rng.uniform(-2.0, 2.0))
            damage = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=history)
            self.assertGreaterEqual(damage + 1e-18, previous)
            self.assertGreaterEqual(damage, 0.0)
            previous = damage

    def test_split_and_merge_are_consistent(self):
        first = [0.5, 1.0, 2.0, 0.25]
        second = [3.0, 0.75, 1.5]
        split = lifecycle.fatigue_damage_index(
            0, 0.0, drop_energies_j=first
        ) + lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=second)
        merged = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=first + second)
        self.assertAlmostEqual(split, merged, places=12)

    def test_reordering_does_not_change_damage(self):
        events = [0.5, 4.0, 0.25, 2.0, 1.0]
        baseline = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=events)
        self.assertAlmostEqual(
            baseline,
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=list(reversed(events))),
            places=12,
        )

    def test_zero_energy_events_contribute_nothing(self):
        without_zero = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[0.5, 1.0])
        with_zero = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[0.5, 0.0, 1.0, 0.0])
        self.assertAlmostEqual(without_zero, with_zero, places=12)

    def test_tiny_events_produce_tiny_damage(self):
        damage = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[1e-9] * 1000)
        self.assertGreater(damage, 0.0)
        self.assertLess(damage, 1e-12)

    def test_events_above_failure_threshold_exhaust_life(self):
        # N(1e6 J) << 1, so a single event exceeds D = 1.
        damage = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[1e6])
        self.assertGreaterEqual(damage, 1.0)
        # Saturation keeps the result finite and reportable.
        huge = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[1e6, 1e6])
        self.assertLessEqual(huge, lifecycle._DAMAGE_SATURATION)
        self.assertGreaterEqual(huge, 1.0)

    def test_huge_count_does_not_crash(self):
        # Audit regression: prior_drops=1e300 with small energy overflowed the
        # pow; the log-space computation must return a finite result.
        damage = lifecycle.fatigue_damage_index(1e300, 0.5)
        self.assertGreaterEqual(damage, 0.0)
        self.assertLess(damage, 1.0)

    def test_invalid_inputs_raise_not_convert(self):
        for call in (
            lambda: lifecycle.fatigue_damage_index(float("nan"), 1.0),
            lambda: lifecycle.fatigue_damage_index(1.0, float("inf")),
            lambda: lifecycle.fatigue_damage_index(-1, 1.0),
            lambda: lifecycle.fatigue_damage_index(1, -1.0),
            lambda: lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[float("nan")]),
            lambda: lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[-1.0]),
        ):
            with self.assertRaises(ValueError):
                call()

    def test_degradation_factors_clamps_huge_finite_with_disclosure(self):
        # Audit regression: prior_drops=1e300 must not crash the pipeline;
        # huge-but-finite values are clamped to the documented domain with a
        # disclosure diagnostic.
        rest, _, damage, diagnostics = lifecycle.degradation_factors(
            {"prior_drops": 1e300, "prior_impact_energy_j": 1e300}
        )
        self.assertTrue(
            any("documented screening maximum" in item for item in diagnostics)
        )
        self.assertGreaterEqual(damage["fatigue_index"], 0.0)
        self.assertGreaterEqual(rest, 0.5)

    def test_degradation_factors_rejects_nan(self):
        for usage in (
            {"prior_drops": float("nan")},
            {"prior_impact_energy_j": float("inf")},
            {"actuation_cycles": float("nan")},
            {"slide_distance_km": float("nan")},
            {"prior_drop_energies_j": [float("nan")]},
            {"prior_drop_energies_j": [1.0, -0.5]},
        ):
            with self.assertRaises(ValueError):
                lifecycle.degradation_factors(usage)

    def test_fatigue_model_field_discloses_the_path(self):
        _, _, aggregate, _ = lifecycle.degradation_factors(
            {"prior_drops": 10, "prior_impact_energy_j": 5.0}
        )
        self.assertEqual(aggregate["fatigue_model"], "uniform_event_approximation")
        _, _, event, _ = lifecycle.degradation_factors(
            {"prior_drop_energies_j": [0.5, 1.0, 2.0]}
        )
        self.assertEqual(event["fatigue_model"], "event_wise_energies")
        self.assertTrue(
            any("UNIFORM-EVENT APPROXIMATION" in item for item in
                lifecycle.degradation_factors(
                    {"prior_drops": 10, "prior_impact_energy_j": 5.0}
                )[3])
        )

    def test_next_usage_event_wise_chaining_is_exact(self):
        usage = {"prior_drop_energies_j": [0.5, 1.0]}
        first = lifecycle.next_usage(usage, 2, 1.5, drop_energies_j=[1.0, 0.5])
        self.assertEqual(first["prior_drop_energies_j"], [0.5, 1.0, 1.0, 0.5])
        second = lifecycle.next_usage(first, 1, 0.75, drop_energies_j=[0.75])
        self.assertEqual(second["prior_drop_energies_j"], [0.5, 1.0, 1.0, 0.5, 0.75])
        # Split vs merged runs give the same accumulated damage.
        merged = lifecycle.next_usage(usage, 3, 2.25, drop_energies_j=[1.0, 0.5, 0.75])
        d_split = lifecycle.fatigue_damage_index(
            0, 0.0, drop_energies_j=second["prior_drop_energies_j"]
        )
        d_merged = lifecycle.fatigue_damage_index(
            0, 0.0, drop_energies_j=merged["prior_drop_energies_j"]
        )
        self.assertAlmostEqual(d_split, d_merged, places=12)

    def test_next_usage_negative_drop_count_raises(self):
        with self.assertRaises(ValueError):
            lifecycle.next_usage({}, -1, 0.5)

    def test_negative_fractional_counts_raise_not_truncate(self):
        # int(-0.5) truncates toward zero; the coercion must reject the
        # negative value instead of silently converting it to a valid count.
        for usage in (
            {"prior_drops": -0.5},
            {"actuation_cycles": -0.5},
            {"scroll_encoder_rotations": -0.5},
        ):
            with self.assertRaises(ValueError):
                lifecycle.degradation_factors(usage)

    def test_next_usage_rejects_non_finite_run_energy(self):
        for bad in (float("inf"), float("nan"), -1.0):
            with self.assertRaises(ValueError):
                lifecycle.next_usage({}, 1, bad)

    def test_next_usage_keeps_tiny_event_energies_exactly(self):
        # Rounding stored energies would destroy sub-micron events and break
        # split/merge consistency of the chained history.
        usage = lifecycle.next_usage(
            {"prior_drop_energies_j": [1e-9]}, 1, 1e-9, drop_energies_j=[2e-9]
        )
        self.assertEqual(usage["prior_drop_energies_j"], [1e-9, 2e-9])
        chained = lifecycle.next_usage(usage, 0, 0.0, drop_energies_j=[3e-9])
        self.assertEqual(chained["prior_drop_energies_j"], [1e-9, 2e-9, 3e-9])
        self.assertEqual(
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=chained["prior_drop_energies_j"]),
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[1e-9, 2e-9, 3e-9]),
        )

    def test_scalar_energies_argument_is_rejected_clearly(self):
        with self.assertRaises(ValueError):
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=5.0)

    def test_saturation_preserves_exhaustion_under_split_merge(self):
        # At/above the saturation cap the exact value is capped but the
        # exhaustion verdict (D >= 1) is preserved across splitting.
        events = [1e6] * 4
        merged = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=events)
        split = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=events[:2]) + lifecycle.fatigue_damage_index(
            0, 0.0, drop_energies_j=events[2:]
        )
        self.assertGreaterEqual(merged, 1.0)
        self.assertGreaterEqual(split, 1.0)
        self.assertLessEqual(merged, lifecycle._DAMAGE_SATURATION)

    def test_degradation_factors_fresh_unit_are_identity(self):
        rest, fric, damage, diagnostics = lifecycle.degradation_factors({})
        self.assertEqual(rest, 1.0)
        self.assertEqual(fric, 1.0)
        self.assertFalse(damage["fatigue_exhausted"])
        self.assertEqual(damage["skate_remaining_mm"], lifecycle.SKATE_INITIAL_MM)
        self.assertEqual(damage["switch_type"], "unknown")
        self.assertEqual(damage["pad_surface"], "cloth")
        self.assertFalse(damage["scroll_encoder_exceeded"])
        self.assertEqual(diagnostics, [])

    def test_degradation_factors_wear_and_damage(self):
        rest, fric, damage, diagnostics = lifecycle.degradation_factors(
            {
                "prior_drops": 200,
                "prior_impact_energy_j": 100.0,
                "slide_distance_km": 100.0,
                "actuation_cycles": 30_000_000,
            }
        )
        self.assertLess(rest, 1.0)
        self.assertGreater(fric, 1.0)
        self.assertTrue(damage["actuation_exceeded"])
        self.assertLess(damage["skate_remaining_mm"], lifecycle.SKATE_INITIAL_MM)
        self.assertGreaterEqual(len(diagnostics), 3)

    def test_rest_restitution_derate_is_7_percent(self):
        # Full fatigue index derates restitution by FATIGUE_RESTITUTION_DERATE
        # (audit: 7% ~ 10-13% stiffness loss, mid-range of the band).
        self.assertEqual(lifecycle.FATIGUE_RESTITUTION_DERATE, 0.07)
        rest, _, damage, _ = lifecycle.degradation_factors(
            {"prior_drops": int(2e6), "prior_impact_energy_j": 1e6}
        )
        self.assertTrue(damage["fatigue_exhausted"])
        self.assertAlmostEqual(rest, 1.0 - 0.07, places=9)

    def test_skate_wear_rate_depends_on_pad_surface(self):
        self.assertAlmostEqual(
            lifecycle.skate_wear_rate_mm_per_km("cloth"), 0.0001, places=9
        )
        self.assertAlmostEqual(
            lifecycle.skate_wear_rate_mm_per_km("hard"), 0.002, places=9
        )
        # 100 km on a cloth pad: 0.01 mm; on a hard pad: 0.2 mm.
        self.assertAlmostEqual(lifecycle.skate_remaining_mm(100.0, "cloth"), 0.39, places=9)
        self.assertAlmostEqual(lifecycle.skate_remaining_mm(100.0, "hard"), 0.2, places=9)
        self.assertAlmostEqual(
            lifecycle.skate_remaining_mm(100.0, "cloth"),
            lifecycle.skate_remaining_mm(100.0),
        )

    def test_friction_ceiling_is_2_25_with_interpolation(self):
        # Full wear: friction_scale = 1 + (1-0)*(0.35/0.10 - 1)*0.5 = 2.25.
        ceiling = 1.0 + (lifecycle.MU_POLYMER / lifecycle.MU_PTFE - 1.0) * lifecycle.FRICTION_BLENDING_FACTOR
        self.assertAlmostEqual(ceiling, 2.25, places=9)
        rest, fric, damage, _ = lifecycle.degradation_factors(
            {"slide_distance_km": 1e6, "pad_surface": "hard"}
        )
        self.assertAlmostEqual(fric, 2.25, places=6)
        self.assertEqual(damage["skate_remaining_mm"], 0.0)

    def test_switch_class_ratings(self):
        self.assertEqual(lifecycle.RATED_SWITCH_ACTUATIONS["mechanical"], 20_000_000)
        self.assertEqual(lifecycle.RATED_SWITCH_ACTUATIONS["optical"], 60_000_000)
        self.assertEqual(lifecycle.RATED_SWITCH_ACTUATIONS["unknown"], 20_000_000)
        # 25M cycles: exceeded for mechanical/unknown, not for optical.
        for switch_type, exceeded in (
            ("mechanical", True),
            ("optical", False),
            ("unknown", True),
        ):
            _, _, damage, _ = lifecycle.degradation_factors(
                {"actuation_cycles": 25_000_000, "switch_type": switch_type}
            )
            self.assertEqual(damage["actuation_exceeded"], exceeded, switch_type)
            self.assertEqual(damage["switch_type"], switch_type)
        # Invalid switch type falls back to 'unknown' (20M rating).
        _, _, damage, _ = lifecycle.degradation_factors(
            {"actuation_cycles": 25_000_000, "switch_type": "capacitive"}
        )
        self.assertTrue(damage["actuation_exceeded"])
        self.assertEqual(damage["switch_type"], "unknown")

    def test_scroll_encoder_exceeded_flag_and_diagnostic(self):
        # 30,000 wheel steps = 1,250 revolutions at 24 steps/revolution —
        # well below the 25,000-revolution mechanical rating.
        damage = lifecycle.degradation_factors(
            {"scroll_encoder_rotations": 30_000}
        )[2]
        self.assertFalse(damage["scroll_encoder_exceeded"])
        self.assertEqual(damage["scroll_encoder_rotations"], 30_000)
        # 600,001 steps = 25,000.04 revolutions — just past the rating.
        _, _, damage, diagnostics = lifecycle.degradation_factors(
            {"scroll_encoder_rotations": 600_001}
        )
        self.assertTrue(damage["scroll_encoder_exceeded"])
        self.assertTrue(
            any("rated 25000" in item for item in diagnostics)
        )
        _, _, fresh_damage, fresh_diagnostics = lifecycle.degradation_factors(
            {"scroll_encoder_rotations": 10_000}
        )
        self.assertFalse(fresh_damage["scroll_encoder_exceeded"])
        self.assertEqual(fresh_diagnostics, [])

    def test_age_days_disclosure_always_emitted(self):
        _, _, damage, diagnostics = lifecycle.degradation_factors({"age_days": 365})
        self.assertTrue(
            any(
                "age_days is recorded but has no mechanical effect" in item
                and "ISO 899-1" in item
                for item in diagnostics
            )
        )
        self.assertEqual(damage["age_days"], 365.0)
        # No age recorded -> no disclosure, fresh unit stays quiet.
        _, _, _, quiet = lifecycle.degradation_factors({})
        self.assertEqual(quiet, [])

    def test_next_usage_accumulates(self):
        usage = lifecycle.next_usage(
            {
                "prior_drops": 5,
                "prior_impact_energy_j": 1.0,
                "switch_type": "optical",
                "pad_surface": "hard",
                "scroll_encoder_rotations": 4000,
            },
            3,
            2.0,
        )
        self.assertEqual(usage["prior_drops"], 8)
        self.assertAlmostEqual(usage["prior_impact_energy_j"], 3.0)
        self.assertEqual(usage["switch_type"], "optical")
        self.assertEqual(usage["pad_surface"], "hard")
        self.assertEqual(usage["scroll_encoder_rotations"], 4000)
        fresh = lifecycle.next_usage({}, 1, 0.5)
        self.assertEqual(fresh["prior_drops"], 1)
        self.assertAlmostEqual(fresh["prior_impact_energy_j"], 0.5)
        self.assertEqual(fresh["switch_type"], "unknown")
        self.assertEqual(fresh["pad_surface"], "cloth")
        self.assertEqual(fresh["scroll_encoder_rotations"], 0)

    def test_deterministic_pure_function(self):
        snapshot = {"prior_drops": 50, "prior_impact_energy_j": 25.0, "slide_distance_km": 40.0}
        first = lifecycle.degradation_factors(snapshot)
        second = lifecycle.degradation_factors(snapshot)
        self.assertEqual(first, second)


class LifecyclePipelineTests(unittest.TestCase):
    def test_lifecycle_absent_leaves_no_section(self):
        result = run_pipeline(dict(BASELINE))
        self.assertIsNone(result["lifecycle"])

    def test_lifecycle_wired_into_drop_simulation(self):
        request = dict(BASELINE)
        request["drop_simulation"] = {
            "test": "drop",
            "height_m": 0.5,
            "drop_count": 2,
            "surface": "concrete",
        }
        request["lifecycle"] = {
            "prior_drops": 40,
            "prior_impact_energy_j": 20.0,
            "slide_distance_km": 60.0,
        }
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        section = result["lifecycle"]
        self.assertIsNotNone(section)
        self.assertEqual(section["usage_snapshot"]["prior_drops"], 40)
        self.assertGreater(section["damage"]["fatigue_index"], 0.0)
        self.assertLess(section["degraded_properties"]["restitution_scale"], 1.0)
        self.assertGreater(section["degraded_properties"]["friction_scale"], 1.0)
        self.assertEqual(section["applied_to"], ["drop_simulation"])
        # The next-usage snapshot accumulates this run's drops and energy.
        self.assertGreater(section["next_usage"]["prior_drops"], 40)
        self.assertGreater(section["next_usage"]["prior_impact_energy_j"], 20.0)
        # The degradation is applied to the actual simulation.
        self.assertLess(result["drop_simulation"]["model"]["restitution"], 0.3)
        self.assertGreater(result["drop_simulation"]["model"]["friction"], 0.6)
        codes = [item["code"] for item in result["issues"]]
        self.assertIn("LIFECYCLE_DEGRADATION_APPLIED", codes)

    def test_lifecycle_new_usage_fields_carried_through_pipeline(self):
        request = dict(BASELINE)
        request["drop_simulation"] = {"height_m": 0.5, "drop_count": 1}
        request["lifecycle"] = {
            "prior_drops": 10,
            "prior_impact_energy_j": 5.0,
            "switch_type": "optical",
            "pad_surface": "hard",
            "scroll_encoder_rotations": 600_001,
            "age_days": 100,
        }
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        section = result["lifecycle"]
        self.assertEqual(section["damage"]["switch_type"], "optical")
        self.assertEqual(section["damage"]["pad_surface"], "hard")
        self.assertTrue(section["damage"]["scroll_encoder_exceeded"])
        self.assertEqual(section["next_usage"]["switch_type"], "optical")
        self.assertEqual(section["next_usage"]["pad_surface"], "hard")
        self.assertEqual(section["next_usage"]["scroll_encoder_rotations"], 600_001)
        self.assertTrue(any("ISO 899-1" in item for item in section["diagnostics"]))
        self.assertTrue(any("rated 25000" in item for item in section["diagnostics"]))

    def test_lifecycle_deterministic(self):
        request = dict(BASELINE)
        request["drop_simulation"] = {"height_m": 0.5, "drop_count": 1}
        request["lifecycle"] = {"prior_impact_energy_j": 10.0, "slide_distance_km": 20.0}
        first = run_pipeline(request)
        second = run_pipeline(request)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["lifecycle"], second["lifecycle"])
        self.assertEqual(
            first["drop_simulation"]["trajectory"], second["drop_simulation"]["trajectory"]
        )

    def test_lifecycle_invalid_input_fails_gracefully(self):
        request = dict(BASELINE)
        request["drop_simulation"] = {"height_m": 0.5}
        request["lifecycle"] = "not-an-object"
        result = run_pipeline(request)
        self.assertEqual(result["lifecycle_state"], "failed")
        self.assertTrue(any(item["code"] == "DROP_SIMULATION_FAILED" for item in result["issues"]))


class FatigueBoundaryPinTests(unittest.TestCase):
    """Round-2 mutation-audit regression pins for the fatigue model.

    These tests pin the NUMERIC behavior at the saturation cap and the
    exhaustion boundary rather than referencing the internal constant
    ``_DAMAGE_SATURATION`` (a self-referential assertion would not detect
    1e15 -> 1e12) and they exercise damage in (0.99, 1.0), the band the
    previous suite never covered (so relaxing ``>= 1.0`` to ``> 0.99``
    was undetectable).
    """

    def test_saturation_cap_value_pinned(self):
        # A single 1e9 J event has N(1e9) << 1:
        # log10(D) = 2.5*log10(1e9/0.5) - 6 ~ 17.25, so the event damage
        # saturates at the documented cap.  The cap value is pinned
        # literally: exactly 1e15.
        self.assertEqual(
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[1e9]), 1e15
        )
        # Sum-level saturation: 10 events at 5e7 J each contribute 1e14
        # (log10(D) = 2.5*log10(5e7/0.5) - 6 = 14), summing to exactly 1e15.
        self.assertEqual(
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[5e7] * 10), 1e15
        )
        # Same saturation through the aggregate uniform-event path
        # (10 drops totalling 5e8 J, 5e7 J average per event).
        self.assertEqual(lifecycle.fatigue_damage_index(10, 5e8), 1e15)
        # Pipeline-level: 200,000 events at the documented maximum energy of
        # 1e6 J each (5.6569e9 damage per event) sum past the cap; the
        # disclosed fatigue_index reports exactly 1e15.
        _, _, damage, diagnostics = lifecycle.degradation_factors(
            {"prior_drop_energies_j": [1e6] * 200_000}
        )
        self.assertEqual(damage["fatigue_index"], 1e15)
        self.assertTrue(damage["fatigue_exhausted"])
        self.assertTrue(any("Miner-rule" in item for item in diagnostics))

    def test_exhaustion_boundary_at_one(self):
        # D = 0.995 (995,000 identical 0.5 J events: N(0.5) = 1e6, so
        # D = 995000/1e6) sits in (0.99, 1.0) and is NOT exhausted: the
        # boundary is D >= 1.0, not D > 0.99.
        self.assertAlmostEqual(
            lifecycle.fatigue_damage_index(995_000, 497_500.0), 0.995, places=12
        )
        _, _, damage, _ = lifecycle.degradation_factors(
            {"prior_drops": 995_000, "prior_impact_energy_j": 497_500.0}
        )
        self.assertAlmostEqual(damage["fatigue_index"], 0.995, places=6)
        self.assertFalse(damage["fatigue_exhausted"])
        # 999,000 events: D = 0.999, still not exhausted.
        _, _, damage, _ = lifecycle.degradation_factors(
            {"prior_drops": 999_000, "prior_impact_energy_j": 499_500.0}
        )
        self.assertFalse(damage["fatigue_exhausted"])
        # D = 1.0 exactly (1,000,000 events at 0.5 J) IS exhausted: >= 1.0.
        self.assertAlmostEqual(
            lifecycle.fatigue_damage_index(1_000_000, 500_000.0), 1.0, places=12
        )
        _, _, damage, _ = lifecycle.degradation_factors(
            {"prior_drops": 1_000_000, "prior_impact_energy_j": 500_000.0}
        )
        self.assertAlmostEqual(damage["fatigue_index"], 1.0, places=6)
        self.assertTrue(damage["fatigue_exhausted"])
        # And D > 1.0 is exhausted too.
        _, _, damage, _ = lifecycle.degradation_factors(
            {"prior_drops": 1_100_000, "prior_impact_energy_j": 550_000.0}
        )
        self.assertTrue(damage["fatigue_exhausted"])

    def test_fatigue_law_pinned_at_boundary(self):
        # The audit's detector value: 8 events at 2 J.
        # N(2) = 1e6*(0.5/2)^2.5 = 31250, so D = 8/31250 = 2.56e-4.
        # Pinning to 12 decimals catches 2e-9-level perturbations of the
        # fatigue law constants.
        event_wise = lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[2.0] * 8)
        self.assertAlmostEqual(event_wise, 8.0 / 31250.0, places=12)
        aggregate = lifecycle.fatigue_damage_index(8, 16.0)
        self.assertAlmostEqual(aggregate, 8.0 / 31250.0, places=12)
        self.assertAlmostEqual(event_wise, aggregate, places=12)
        self.assertAlmostEqual(event_wise, 2.56e-4, places=12)
        # Single event at 2 J: D = 1/31250, the base of the boundary pin.
        self.assertAlmostEqual(
            lifecycle.fatigue_damage_index(0, 0.0, drop_energies_j=[2.0]),
            1.0 / 31250.0,
            places=12,
        )


if __name__ == "__main__":
    unittest.main()
