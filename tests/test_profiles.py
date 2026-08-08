"""Tests for the usage-profile catalog, lifetime projections, and merging."""

import unittest

from mouse_sim import lifecycle
from mouse_sim import profiles


class ProfileCatalogTests(unittest.TestCase):
    def test_catalog_covers_all_profile_keys(self):
        self.assertEqual(set(profiles.PROFILE_CATALOG), set(profiles.PROFILE_KEYS))
        self.assertEqual(len(profiles.PROFILE_KEYS), 4)
        for key in profiles.PROFILE_KEYS:
            entry = profiles.PROFILE_CATALOG[key]
            self.assertIsInstance(entry["name"], str)
            self.assertTrue(entry["name"])
            self.assertIsInstance(entry["description"], str)
            self.assertTrue(entry["description"])
            usage = entry["usage"]
            for field in (
                "clicks_per_day",
                "slide_km_per_day",
                "scroll_per_day",
                "drops_every_days",
            ):
                self.assertIn(field, usage)
                self.assertGreater(usage[field], 0)


class ProfileUsageTests(unittest.TestCase):
    def test_fps_totals_over_730_days(self):
        usage = profiles.profile_usage("esports_fps", 730)["usage"]
        self.assertEqual(usage["actuation_cycles"], 5_840_000)
        self.assertIsInstance(usage["actuation_cycles"], int)
        self.assertEqual(usage["slide_distance_km"], 1460.0)
        self.assertEqual(usage["prior_drops"], 8)
        self.assertIsInstance(usage["prior_drops"], int)
        self.assertEqual(usage["scroll_encoder_rotations"], 146_000)
        self.assertIsInstance(usage["scroll_encoder_rotations"], int)
        self.assertAlmostEqual(
            usage["prior_impact_energy_j"],
            8 * profiles.DROP_ENERGY_REFERENCE_J,
            places=6,
        )

    def test_all_profiles_produce_positive_totals(self):
        for key in profiles.PROFILE_KEYS:
            usage = profiles.profile_usage(key, 730)["usage"]
            for field in (
                "prior_drops",
                "prior_impact_energy_j",
                "actuation_cycles",
                "slide_distance_km",
                "age_days",
                "scroll_encoder_rotations",
            ):
                self.assertGreater(usage[field], 0, "{} {}".format(key, field))
            self.assertEqual(usage["temperature_cycles_per_day"], 1)
            self.assertEqual(usage["delta_temperature_k"], 30)
            self.assertEqual(usage["transport_vibration_g_rms"], 3.0)

    def test_result_structure(self):
        result = profiles.profile_usage("general")
        self.assertEqual(
            set(result),
            {"profile", "name", "lifespan_days", "usage", "summary", "assumptions"},
        )
        self.assertEqual(result["profile"], "general")
        self.assertEqual(result["lifespan_days"], 730)
        self.assertEqual(result["name"], profiles.PROFILE_CATALOG["general"]["name"])

    def test_usage_fields_match_lifecycle_schema(self):
        usage = profiles.profile_usage("esports_moba")["usage"]
        self.assertEqual(
            set(usage),
            {
                "prior_drops",
                "prior_impact_energy_j",
                "actuation_cycles",
                "slide_distance_km",
                "age_days",
                "scroll_encoder_rotations",
                "temperature_cycles_per_day",
                "transport_vibration_g_rms",
                "delta_temperature_k",
                "pad_surface",
            },
        )
        self.assertEqual(usage["age_days"], 730)
        rest, fric, damage, diagnostics = lifecycle.degradation_factors(usage)
        self.assertGreaterEqual(rest, 0.5)
        self.assertGreaterEqual(fric, 1.0)
        # The profile's usage history is disclosed: fatigue accumulation from
        # the projected drops and skate wear from the projected slide
        # distance must appear in the diagnostics.
        self.assertTrue(
            any("fatigue" in line or "skate" in line for line in diagnostics),
            "history must be disclosed in the diagnostics",
        )

    def test_fps_profile_triggers_expected_screening_flags(self):
        usage = profiles.profile_usage("esports_fps", 730)["usage"]
        _, _, damage, _ = lifecycle.degradation_factors(usage)
        # 146,000 wheel steps / 24 detents per revolution = 6,083 encoder
        # revolutions, below the 25,000-revolution mechanical rating: scroll
        # wear is honestly not a driver for the profile.
        self.assertFalse(damage["scroll_encoder_exceeded"])
        # 5.84M clicks stay below the 20M mechanical switch rating.
        self.assertFalse(damage["actuation_exceeded"])
        self.assertGreater(damage["skate_remaining_mm"], 0.0)

    def test_lifespan_scaling_halves_totals(self):
        for key in profiles.PROFILE_KEYS:
            half = profiles.profile_usage(key, 365)
            full = profiles.profile_usage(key, 730)
            for field in (
                "prior_drops",
                "prior_impact_energy_j",
                "actuation_cycles",
                "slide_distance_km",
                "age_days",
                "scroll_encoder_rotations",
            ):
                self.assertEqual(
                    half["usage"][field], full["usage"][field] / 2, "{} {}".format(key, field)
                )
            self.assertEqual(half["summary"]["years"], full["summary"]["years"] / 2)
            for field in (
                "clicks_per_year",
                "slide_km_per_year",
                "scroll_per_year",
                "drops_per_year",
            ):
                self.assertEqual(half["summary"][field], full["summary"][field])

    def test_summary_consistent_with_totals(self):
        result = profiles.profile_usage("esports_fps", 730)
        summary = result["summary"]
        usage = result["usage"]
        self.assertEqual(summary["years"], 2.0)
        self.assertEqual(summary["clicks_per_year"], 2_920_000)
        self.assertEqual(summary["slide_km_per_year"], 730.0)
        self.assertEqual(summary["scroll_per_year"], 73_000)
        self.assertEqual(summary["drops_per_year"], 4.0)
        self.assertAlmostEqual(
            summary["clicks_per_year"] * summary["years"], usage["actuation_cycles"], places=2
        )
        self.assertAlmostEqual(
            summary["slide_km_per_year"] * summary["years"], usage["slide_distance_km"], places=2
        )
        self.assertAlmostEqual(
            summary["drops_per_year"] * summary["years"], usage["prior_drops"], places=2
        )


class ProfileValidationTests(unittest.TestCase):
    def test_validate_profile_normalizes_keys(self):
        self.assertEqual(profiles.validate_profile("esports_fps"), "esports_fps")
        self.assertEqual(profiles.validate_profile("Esports_FPS"), "esports_fps")
        self.assertEqual(profiles.validate_profile("  PRODUCTIVITY "), "productivity")
        self.assertEqual(profiles.validate_profile("General"), "general")

    def test_validate_profile_rejects_unknown(self):
        for bad in ("unknown", "esports", "esports-fps", "", " "):
            with self.assertRaises(ValueError):
                profiles.validate_profile(bad)
        with self.assertRaises(ValueError):
            profiles.validate_profile(None)

    def test_profile_usage_normalizes_case(self):
        self.assertEqual(
            profiles.profile_usage("ESports_FPS", 730), profiles.profile_usage("esports_fps", 730)
        )

    def test_profile_usage_rejects_bad_lifespan(self):
        for bad in (0, -30, 0.0):
            with self.assertRaises(ValueError):
                profiles.profile_usage("general", bad)
        with self.assertRaises(ValueError):
            profiles.profile_usage("general", "lots")


class DeterminismTests(unittest.TestCase):
    def test_same_key_and_lifespan_yields_identical_result(self):
        first = profiles.profile_usage("esports_moba", 730)
        second = profiles.profile_usage("esports_moba", 730)
        self.assertEqual(first, second)
        for key in profiles.PROFILE_KEYS:
            self.assertEqual(
                profiles.profile_usage(key, 500), profiles.profile_usage(key, 500)
            )


class CombineUsageTests(unittest.TestCase):
    def test_combine_sums_numeric_fields(self):
        fps = profiles.profile_usage("esports_fps", 730)["usage"]
        moba = profiles.profile_usage("esports_moba", 730)["usage"]
        combined = profiles.combine_usage(fps, moba)
        for field in (
            "prior_drops",
            "prior_impact_energy_j",
            "actuation_cycles",
            "slide_distance_km",
            "age_days",
            "scroll_encoder_rotations",
            "temperature_cycles_per_day",
            "transport_vibration_g_rms",
            "delta_temperature_k",
        ):
            self.assertEqual(combined[field], fps[field] + moba[field], field)
        self.assertEqual(combined["actuation_cycles"], 5_840_000 + 8_760_000)
        self.assertEqual(combined["slide_distance_km"], 1460.0 + 730.0)
        self.assertEqual(combined["prior_drops"], 8 + 6)

    def test_combine_is_commutative_and_handles_disjoint(self):
        a = {"actuation_cycles": 5_000, "age_days": 100}
        b = {"actuation_cycles": 7_000, "slide_distance_km": 12.5}
        expected = {"actuation_cycles": 12_000, "age_days": 100, "slide_distance_km": 12.5}
        self.assertEqual(profiles.combine_usage(a, b), expected)
        self.assertEqual(profiles.combine_usage(b, a), expected)
        self.assertEqual(profiles.combine_usage({}, b), b)
        self.assertEqual(profiles.combine_usage(b, {}), b)

    def test_combine_preserves_non_numeric_on_conflict(self):
        a = {"actuation_cycles": 1, "switch_type": "mechanical"}
        b = {"actuation_cycles": 2, "switch_type": "optical"}
        combined = profiles.combine_usage(a, b)
        self.assertEqual(combined["actuation_cycles"], 3)
        self.assertEqual(combined["switch_type"], "mechanical")


class AssumptionsTests(unittest.TestCase):
    def test_assumptions_are_non_empty_and_documented(self):
        for key in profiles.PROFILE_KEYS:
            result = profiles.profile_usage(key)
            self.assertEqual(len(result["assumptions"]), len(profiles.ASSUMPTIONS))
            self.assertGreaterEqual(len(result["assumptions"]), 3)
            for assumption in result["assumptions"]:
                self.assertIsInstance(assumption, str)
                self.assertTrue(assumption)
            text = " ".join(result["assumptions"])
            self.assertIn("0.49 J", text)
            self.assertIn("ISTA", text)


if __name__ == "__main__":
    unittest.main()
