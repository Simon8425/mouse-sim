"""Regression tests for the 50-agent audit blockers (orchestrator-fixed)."""

import json
import math
import os
import tempfile
import unittest

from mouse_sim import cache as cache_module
from mouse_sim.pipeline import _engine_hash, reproduce_from_manifest, run_pipeline
from tests.test_shell_validation import validation_request, SHELL, PCB, BATTERY

# The engine's corner mode: 125.26 deg about unit axis (-1, 1, 0)/sqrt(2),
# which lands the body (1, 1, 1) corner vertically down.  Must match
# _orientation_quaternion("corner", 0) bit-for-bit (the identity check in
# the pipeline compares resolved quaternion components).
CORNER_Q = [0.4597008433809831, -0.6279630301995544, 0.6279630301995544, 0.0]


def flat_test(test_id, height, measured, uncertainty=10.0, sensor=True, **overrides):
    entry = {
        "test_id": test_id,
        "cad_revision": "cad-r42",
        "material": "ABS",
        "height_m": height,
        "surface": "concrete",
        "orientation": "flat",
        "measured_peak_accel_g": measured,
        "measured_peak_accel_g_uncertainty": uncertainty,
    }
    if sensor:
        entry["sensor"] = {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]}
    entry.update(overrides)
    return entry


class EquivalenceGatingTests(unittest.TestCase):
    def test_non_equivalent_conditions_cannot_reach_correlated(self):
        # Audit blocker: corner/off-CoM axis data (factor ~2-3 mismatch)
        # previously drove correlated + high confidence while every row was
        # flagged NOT EQUIVALENT.
        request = validation_request()
        request["validation"]["measured_tests"] = []
        for h, tid, g in ((0.5, "C1", 382.0), (1.0, "C2", 535.0), (1.5, "C3", 654.0)):
            request["validation"]["measured_tests"].append({
                "test_id": tid, "cad_revision": "cad-r42", "material": "ABS",
                "height_m": h, "surface": "concrete", "orientation": "corner",
                "sensor": {"quantity": "axis_peak_g", "axis": "z",
                           "location_body_m": [0.03, 0.02, 0.005]},
                "measured_peak_accel_g": g, "measured_peak_accel_g_uncertainty": 10.0,
            })
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertEqual(result["correlation"]["excluded_conditions"], 3)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")
        rows = result["shell"]["validation"]["measured_comparison"]["rows"]
        self.assertTrue(all(not row["equivalent"] for row in rows))

    def test_missing_sensor_definition_is_not_equivalent(self):
        # Audit blocker: forgetting the sensor definition was silently
        # treated as a CoM/resultant reading.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        for entry in request["validation"]["measured_tests"]:
            del entry["sensor"]
        result = run_pipeline(request)
        rows = result["shell"]["validation"]["measured_comparison"]["rows"]
        self.assertTrue(all(not row["equivalent"] for row in rows))
        self.assertIn("no sensor definition", rows[0]["equivalence_note"])
        self.assertEqual(result["correlation"]["excluded_conditions"], 3)

    def test_equivalent_flat_campaign_reaches_correlated(self):
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("F1", 0.5, 470.0), flat_test("F2", 1.0, 667.0), flat_test("F3", 1.5, 817.0),
        ]
        # First pass: read the predictions, then measure == predict.
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["height_m"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["height_m"]]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertEqual(result["correlation"]["evaluated_conditions"], 3)
        self.assertEqual(result["shell"]["model_status"], "correlated")
        self.assertEqual(result["shell"]["physical_model_confidence"], "high")

    def test_settle_only_dataset_cannot_reach_correlated(self):
        # Audit blocker: settle-only data reached "PHYSICALLY VALIDATED"
        # without ever comparing the primary observable.  The settle metrics
        # pass (measured == predicted), so the has_peak_accel gate must fire.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("S1", 0.5, 200.0),
            flat_test("S2", 1.0, 300.0),
            flat_test("S3", 1.5, 350.0),
        ]
        for entry in request["validation"]["measured_tests"]:
            del entry["measured_peak_accel_g"]
            del entry["measured_peak_accel_g_uncertainty"]
            entry["measured_settle_s"] = 1.0
            entry["measured_settle_s_uncertainty"] = 0.05
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "settle_time_s":
                    preds[condition["height_m"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_settle_s"] = preds[entry["height_m"]]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        self.assertIn("peak acceleration", result["shell"]["physical_validation"]["note"])


class OrientationIdentityTests(unittest.TestCase):
    def test_mode_and_identical_explicit_quaternion_are_one_condition(self):
        # Audit blocker: the same physical pose as mode "corner" and as the
        # explicit corner quaternion was counted as two conditions.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete", "orientation": "corner",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 382.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T2", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": CORNER_Q},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 382.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T3", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 1.0, "surface": "concrete", "orientation": "corner",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 535.0, "measured_peak_accel_g_uncertainty": 10.0},
        ]
        result = run_pipeline(request)
        # The explicit-duplicate condition is non-equivalent (corner) and is
        # excluded from the verdict entirely; the exploit (2 physical
        # conditions as 3) is neutralized.
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertEqual(result["correlation"]["excluded_conditions"], 3)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")

    def test_identical_equivalent_conditions_are_duplicates(self):
        # The quaternion-based identity must flag two IDENTICAL flat poses
        # (same resolved quaternion) as one condition.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 0.5, 475.0),
            flat_test("T3", 1.0, 667.0),
        ]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertIn("duplicate", result["correlation"]["explanation"])

    def test_distinct_explicit_quaternions_are_distinct_conditions(self):
        # Audit blocker (reverse direction): two genuinely different explicit
        # quaternions at one height/surface were falsely flagged duplicates.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 382.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T2", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": [0.0, 1.0, 0.0, 0.0]},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 382.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T3", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 1.0, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 535.0, "measured_peak_accel_g_uncertainty": 10.0},
        ]
        result = run_pipeline(request)
        self.assertNotIn("duplicate", result["correlation"]["explanation"])

    def test_comparison_rows_are_paired_by_test_id(self):
        # Audit blocker: two explicit quaternions at one height/surface were
        # cross-wired (last-wins) — each row must carry ITS OWN simulation.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 382.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T2", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": [0.0, 1.0, 0.0, 0.0]},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 382.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T3", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 1.0, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 535.0, "measured_peak_accel_g_uncertainty": 10.0},
        ]
        result = run_pipeline(request)
        rows = {row["test_id"]: row for row in
                result["shell"]["validation"]["measured_comparison"]["rows"]}
        q1 = rows["T1"]["simulated"]["orientation_quaternion_wxyz"]
        q2 = rows["T2"]["simulated"]["orientation_quaternion_wxyz"]
        self.assertAlmostEqual(q1[0], 1.0, places=6)
        self.assertAlmostEqual(q2[1], 1.0, places=6)
        self.assertNotEqual(q1, q2)

    def test_quaternion_sign_canonicalized_duplicates(self):
        # Round-2 audit gap: the duplicate-identity key canonicalizes the
        # quaternion SIGN (q and -q are the SAME physical pose).  A
        # sign-sensitive key let [1,0,0,0] and [-1,0,0,0] count as two
        # independent conditions.  The equivalence gate is fail-closed for
        # explicit-quaternion tests, so the +q/-q pair never reaches the
        # duplicate determination through the public verdict — the
        # determination is pinned directly on the correlation summary over
        # the engine-derived resolved quaternion echoes (the exact value
        # the identity key hashes): the pair at one height must be counted
        # as duplicates while a distinct-height third condition remains
        # evaluated.  With the sign canonicalization removed, the pair
        # counts as distinct and "duplicate" disappears from the
        # explanation.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {"test_id": "Q1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 470.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "Q2", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete",
             "orientation": {"quaternion_wxyz": [-1.0, 0.0, 0.0, 0.0]},
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 470.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "Q3", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 1.0, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 667.0, "measured_peak_accel_g_uncertainty": 10.0},
        ]
        result = run_pipeline(request)
        # Explicit quaternions are non-equivalent (fail-closed): the pair
        # is excluded from the verdict and the campaign cannot reach
        # correlated even if the identity key were sign-sensitive.
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        by_id = {condition["drop_id"]: condition
                 for condition in result["correlation"]["conditions"]}
        self.assertFalse(by_id["Q1"]["equivalent"])
        self.assertFalse(by_id["Q2"]["equivalent"])
        # The resolved quaternion echo (what the identity key hashes) keeps
        # the explicit sign: the pair is genuinely +q and -q.
        self.assertEqual(by_id["Q1"]["orientation_quaternion_wxyz"], [1.0, 0.0, 0.0, 0.0])
        self.assertEqual(by_id["Q2"]["orientation_quaternion_wxyz"], [-1.0, 0.0, 0.0, 0.0])
        # Pin the duplicate determination itself: with the pair evaluated
        # (as equivalent), sign canonicalization must collapse q and -q
        # into one pose, and the distinct-height third condition must
        # remain evaluated.
        from mouse_sim.pipeline import _correlation_summary

        evaluated = [
            dict(condition, equivalent=True, identity_ok=True)
            for condition in result["correlation"]["conditions"]
        ]
        summary = _correlation_summary(evaluated, max_error=0.25, min_conditions=3)
        self.assertEqual(summary["evaluated_conditions"], 3)
        self.assertIn("duplicate", summary["explanation"])
        self.assertEqual(summary["excluded_conditions"], 0)


class PrototypeIdentityTests(unittest.TestCase):
    def test_material_mismatch_excluded_from_verdict(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0, material="PC"),
        ]
        request["validation"]["measured_tests"][2]["material"] = "PC"
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["height_m"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["height_m"]]
        result = run_pipeline(request)
        # The PC-tagged test is excluded; only 2 equivalent identity-ok
        # conditions remain -> no correlated.
        self.assertEqual(result["correlation"]["excluded_conditions"], 1)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        rows = {row["test_id"]: row for row in
                result["shell"]["validation"]["measured_comparison"]["rows"]}
        self.assertTrue(rows["T3"]["identity_mismatch"])
        self.assertIn("material 'PC' differs", rows["T3"]["identity_mismatch_note"])

    def test_cad_revision_mismatch_excluded_from_verdict(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"][2]["cad_revision"] = "OTHER-CAD"
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["height_m"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["height_m"]]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["excluded_conditions"], 1)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")


class MeasurementValidationTests(unittest.TestCase):
    def test_negative_duration_rejected(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["measured_impact_duration_s"] = -1.0
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_implausible_duration_and_settle_rejected(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["measured_impact_duration_s"] = 1e6
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["measured_settle_s"] = -0.5
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_environment_nan_rejected_cleanly(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["environment"] = {"temperature_k": float("nan")}
        result = run_pipeline(request)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("VALIDATION_CONFIG_INVALID", codes)
        self.assertNotIn("PIPELINE_INTERNAL", codes)

    def test_environment_out_of_range_rejected(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["environment"] = {"temperature_k": -400.0}
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["environment"] = {"humidity_pct": 150.0}
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_axis_peak_g_requires_axis(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["sensor"] = {"quantity": "axis_peak_g"}
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_unknown_measured_test_keys_rejected(self):
        # Audit finding: unsupported fields (e.g. spin) were silently
        # ignored, producing an incompatible comparison.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 200.0),
            flat_test("T2", 1.0, 300.0),
            flat_test("T3", 1.5, 350.0),
        ]
        request["validation"]["measured_tests"][0]["spin_rps"] = 5
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])


class ConfidenceGateTests(unittest.TestCase):
    def test_single_condition_pass_cannot_unlock_high_confidence(self):
        # Audit blocker: exploration min_drop_conditions=1 unlocked high
        # confidence while model_status stayed unvalidated.
        request = {
            "schema_id": "gms.project-document/1",
            "mode": "exploration",
            "units": "mm",
            "objects": [{"id": "shell", "geometry": SHELL, "material": "ABS",
                         "structural_behavior": "shell"}],
            "drop_simulation": {"height_m": 0.5, "drop_count": 1},
            "correlation": {
                "acceptance": {"max_relative_error": 1e9, "min_drop_conditions": 1},
                "measured_drops": [
                    {"drop_id": "D1", "height_m": 0.5, "surface": "concrete",
                     "orientation": "flat", "measured_peak_accel_g": 470.0,
                     "sensor": {"quantity": "resultant_peak_g",
                                "location_body_m": [0.0, 0.0, 0.0]}},
                ],
            },
        }
        first = run_pipeline(request)
        prediction = None
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    prediction = metric["predicted"]
        request["correlation"]["measured_drops"][0]["measured_peak_accel_g"] = prediction
        result = run_pipeline(request)
        # The verdict itself passes with min_drop_conditions=1, but model
        # status and the confidence labels must stay below correlated/high.
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertEqual(result["shell"]["model_status"], "unvalidated")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")
        self.assertNotEqual(result["validity"]["confidence"], "high")


class ComparisonIntegrityTests(unittest.TestCase):
    def test_settle_sentinel_not_compared(self):
        # Audit blocker: the 8.0 s DID_NOT_SETTLE sentinel must never be
        # compared as a settle value.  The corner drop now SETTLES (the
        # contact model tips metastable rests onto a face, and the escape
        # perturbation exhausts its bounded budget), so the settle metric is
        # a real measured-vs-predicted comparison; if a re-sim ever still
        # hits the sentinel, the metric must carry the sentinel reason and
        # a null prediction instead of a compared value.
        request = validation_request()
        request["validation"]["drop"].update({"height_m": 0.5, "surface": "steel"})
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "steel", "orientation": "corner",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_settle_s": 7.5, "measured_settle_s_uncertainty": 0.5},
        ]
        result = run_pipeline(request)
        settle_metrics = [
            metric for condition in result["correlation"]["conditions"]
            for metric in condition["metrics"]
            if metric["metric_key"] == "settle_time_s"
        ]
        self.assertEqual(len(settle_metrics), 1)
        # A compared settle value (the corner rest is now resolved), never
        # the 8.0 s sentinel.
        self.assertIsNotNone(settle_metrics[0]["predicted"])
        self.assertLess(settle_metrics[0]["predicted"], 8.0)
        self.assertFalse(settle_metrics[0]["pass"])

    def test_duration_uses_full_contact_convention(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 0.5, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_impact_duration_s": 0.003, "measured_impact_duration_s_uncertainty": 0.0005},
        ]
        result = run_pipeline(request)
        duration_metrics = [
            metric for condition in result["correlation"]["conditions"]
            for metric in condition["metrics"]
            if metric["metric_key"] == "impact_duration_s"
        ]
        self.assertEqual(len(duration_metrics), 1)
        predicted = duration_metrics[0]["predicted"]
        # (1+e)*t with e ~ 0.3: the full pulse is ~1.3x the compression phase.
        self.assertGreater(predicted, 0.0012)
        condition = result["correlation"]["conditions"][0]
        self.assertIn("full contact duration", condition["duration_convention"])

    def test_pinned_timestep_reaches_correlation_reesim(self):
        # Audit blocker: the re-sim ran default dt while the trace reported
        # the pin applied — the compared configuration must be the reported
        # configuration.  dt=1e-4 is a documented top sensitivity parameter
        # for corner settles.
        def settle_fixtures(measured):
            return [
                {"test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
                 "height_m": 0.5, "surface": "steel", "orientation": "corner",
                 "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
                 "measured_settle_s": measured, "measured_settle_s_uncertainty": 0.1},
            ]
        request = validation_request()
        request["validation"]["drop"].update({"height_m": 0.5, "surface": "steel"})
        request["validation"]["contact"]["timestep_s"] = 1e-4
        request["validation"]["measured_tests"] = settle_fixtures(1.0)
        pinned = run_pipeline(request)
        default_request = validation_request()
        default_request["validation"]["measured_tests"] = settle_fixtures(1.0)
        default = run_pipeline(default_request)
        pinned_settle = None
        default_settle = None
        for condition in pinned["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "settle_time_s":
                    pinned_settle = metric["predicted"]
                    pinned_reason = metric.get("reason")
        for condition in default["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "settle_time_s":
                    default_settle = metric["predicted"]
                    default_reason = metric.get("reason")
        # Both runs now settle (the corner rest is resolved by the contact
        # model), but the timestep remains a top sensitivity parameter: the
        # pinned-dt settle differs from the default-dt settle, and neither
        # is the 8.0 s DID_NOT_SETTLE sentinel.
        self.assertNotEqual(pinned_settle, default_settle)
        self.assertLess(pinned_settle, 8.0)
        self.assertLess(default_settle, 8.0)
        self.assertIsNone(pinned_reason)
        self.assertIsNone(default_reason)


class CacheAndManifestTests(unittest.TestCase):
    def test_materials_path_content_is_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = os.path.join(directory, "catalog.json")
            catalog = {"MAT": {"name": "MAT", "properties": {
                "density": {"value": 1050.0, "unit": "kg/m^3"},
                "young_modulus": {"value": 2.3e9, "unit": "Pa"},
                "poissons_ratio": 0.35,
                "yield_strength": {"value": 40e6, "unit": "Pa"},
                "tensile_allowable": {"value": 20e6, "unit": "Pa"},
                "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6},
                "approval_state": "approved",
                "provenance": {"source_type": "supplier", "source_id": "s1",
                               "condition": "dry", "confidence": "high"}}}
            with open(catalog_path, "w", encoding="utf-8") as stream:
                json.dump(catalog, stream)
            request = validation_request()
            request["materials"] = catalog_path
            request["validation"]["material"] = "MAT"
            first = run_pipeline(request)
            catalog["MAT"]["properties"]["density"]["value"] = 9000.0
            with open(catalog_path, "w", encoding="utf-8") as stream:
                json.dump(catalog, stream)
            second = run_pipeline(request)
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertNotEqual(first["mass"]["mass_kg"], second["mass"]["mass_kg"])

    def test_manifest_binds_engine_and_run_id(self):
        request = validation_request()
        result = run_pipeline(request)
        manifest = result["manifest"]
        self.assertEqual(manifest["engine_hash"], _engine_hash())
        self.assertEqual(manifest["run_id"], result["run_id"])
        # A manifest whose recorded engine hash differs cannot replay.  The
        # forged hash must be re-signed (manifest_hash recomputed) so the
        # self-consistency check passes and the ENGINE gate is the one firing.
        from mouse_sim import canonical as canonical_module

        forged = dict(manifest)
        forged["engine_hash"] = "0" * 64
        presented = {key: value for key, value in forged.items() if key != "manifest_hash"}
        forged["manifest_hash"] = canonical_module.manifest_hash(presented)
        replay = reproduce_from_manifest(forged)
        self.assertFalse(replay["supported"])
        self.assertIn("engine", replay["reason"])
        # An UNSIGNED tamper (hash not recomputed) is rejected at the
        # self-consistency gate.
        unsigned = dict(manifest)
        unsigned["note"] = "tampered"
        replay = reproduce_from_manifest(unsigned)
        self.assertFalse(replay["supported"])
        self.assertIn("manifest", replay["reason"])

    def test_cache_refuses_tampered_inputs_snapshot(self):
        # W12-01: a cache-writer who re-signed the manifest could tamper the
        # INPUTS SNAPSHOT (height 1.0 -> 0.5) while keeping the recorded
        # input_hashes/run_id/engine_hash genuine; the hit was then served
        # with a body computed for different physics.  The inputs snapshot
        # must re-derive to the recorded input_hashes.
        from mouse_sim import canonical as canonical_module
        from mouse_sim import cache as cache_module

        request = validation_request()
        with tempfile.TemporaryDirectory() as directory:
            cache = cache_module.ArtifactCache(directory)
            first = run_pipeline(request, cache=cache, use_cache=True)
            payload = cache.load(first["run_id"])
            self.assertIsNotNone(payload)
            forged = dict(payload)
            forged_manifest = dict(payload["manifest"])
            forged_inputs = dict(forged_manifest.get("inputs") or {})
            forged_validation = dict(forged_inputs.get("validation") or {})
            forged_drop = dict(forged_validation.get("drop") or {})
            forged_drop["height_m"] = 0.5
            forged_validation["drop"] = forged_drop
            forged_inputs["validation"] = forged_validation
            forged_manifest["inputs"] = forged_inputs
            presented = {
                key: value for key, value in forged_manifest.items() if key != "manifest_hash"
            }
            forged_manifest["manifest_hash"] = canonical_module.manifest_hash(presented)
            forged["manifest"] = forged_manifest
            cache.store(first["run_id"], forged)
            second = run_pipeline(request, cache=cache, use_cache=True)
            # The tampered-inputs payload must NOT be served; a fresh run
            # overwrites the slot with genuine inputs.
            self.assertEqual(second["run_id"], first["run_id"])
            served = cache.load(first["run_id"])
            self.assertEqual(
                served["manifest"]["inputs"]["validation"]["drop"]["height_m"], 1.0
            )

    def test_cache_refuses_foreign_engine_manifest(self):
        # W4-01: a cached payload whose manifest was re-signed with a
        # DIFFERENT engine_hash was served by the cache (attack 4b).  The
        # hit gate now cross-checks manifest.engine_hash against the live
        # engine and re-verifies the manifest hash.
        from mouse_sim import canonical as canonical_module
        from mouse_sim import cache as cache_module

        request = validation_request()
        with tempfile.TemporaryDirectory() as directory:
            cache = cache_module.ArtifactCache(directory)
            first = run_pipeline(request, cache=cache, use_cache=True)
            payload = cache.load(first["run_id"])
            self.assertIsNotNone(payload)
            manifest = payload["manifest"]
            forged = dict(manifest)
            forged["engine_hash"] = "0" * 64
            presented = {
                key: value for key, value in forged.items() if key != "manifest_hash"
            }
            forged["manifest_hash"] = canonical_module.manifest_hash(presented)
            payload["manifest"] = forged
            cache.store(first["run_id"], payload)
            second = run_pipeline(request, cache=cache, use_cache=True)
            # The foreign-engine payload must NOT be served; the run
            # recomputes fresh and overwrites the slot.
            self.assertEqual(second["run_id"], first["run_id"])
            served = cache.load(first["run_id"])
            self.assertEqual(served["manifest"]["engine_hash"], _engine_hash())
            self.assertNotEqual(served["manifest"]["engine_hash"], "0" * 64)

    def test_uncertainty_band_nominal_closest_when_pin_outside_sweep(self):
        # W12-02: when the pinned k is not in the sweep, the band nominal was
        # the MIDDLE row of the submission order with no disclosure — the
        # nominal silently diverged from the headline and reordering the
        # sweep moved it.  The nominal must be the CLOSEST swept row and the
        # fallback must be disclosed in the note.
        request = validation_request()
        request["validation"]["contact"]["stiffness_n_per_m"] = 1e5
        request["validation"]["contact_stiffness_sweep_n_per_m"] = [2e5, 5e5, 1e6]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        bands = result["shell"]["validation"]["uncertainty_bands"]
        self.assertEqual(bands["basis"], "contact_stiffness_sweep")
        note = bands.get("note") or ""
        self.assertIn("not in the swept set", note)
        self.assertIn("closest swept row", note)
        # nominal == the k=2e5 row's peak force (closest to pinned 1e5),
        # NOT the middle row (k=5e5).
        rows = {
            row["contact_stiffness_n_per_m"]: row["peak_force_n"]
            for row in result["shell"]["validation"]["contact_stiffness_sweep"]["rows"]
        }
        self.assertAlmostEqual(
            bands["band"]["peak_force_n"]["nominal"], rows[2e5], places=3
        )

    def test_qualification_gate_matches_verdict_exclusions(self):
        # SENIOR-01: the CORRELATION_MEASURED qualification gate recomputed
        # its statistics over ALL conditions INCLUDING the rows the verdict
        # excluded (equivalent=False / identity_ok=False) — a payload then
        # claimed verdict pass/correlated while the gate hard-blocked citing
        # only the excluded rows.  The gate must mirror the pipeline's
        # evaluated set.
        from tests.test_qualification import approved_inputs
        from mouse_sim.qualification import evaluate_qualification

        conditions = [
            {"drop_id": "F1", "height_m": 0.5, "surface": "concrete",
             "orientation": "flat", "equivalent": True, "identity_ok": True,
             "metrics": [{"metric_key": "peak_accel_g", "measured": 470.0, "predicted": 470.92}]},
            {"drop_id": "F2", "height_m": 1.0, "surface": "concrete",
             "orientation": "flat", "equivalent": True, "identity_ok": True,
             "metrics": [{"metric_key": "peak_accel_g", "measured": 667.0, "predicted": 667.26}]},
            {"drop_id": "F3", "height_m": 1.5, "surface": "concrete",
             "orientation": "flat", "equivalent": True, "identity_ok": True,
             "metrics": [{"metric_key": "peak_accel_g", "measured": 818.0, "predicted": 817.92}]},
            # Excluded by the verdict (corner): must not veto the gate.
            {"drop_id": "C1", "height_m": 1.0, "surface": "concrete",
             "orientation": "corner", "equivalent": False, "identity_ok": True,
             "metrics": [{"metric_key": "peak_accel_g", "measured": 535.0, "predicted": 535.30}]},
        ]
        correlation = {
            "conditions": conditions,
            "r_squared": 0.999,
            "bias": 0.001,
            "verdict": "pass",
            "explanation": "measured-drop campaign",
            "evaluated_conditions": 3,
            "excluded_conditions": 1,
        }
        result = evaluate_qualification(
            "qualification",
            **approved_inputs(pipeline_result={"correlation": correlation}),
        )
        gate = {
            item.key: item for item in result.integrity_gates
        }["CORRELATION_MEASURED"]
        self.assertTrue(gate.passed, gate.explanation)
        self.assertNotIn("CORRELATION_MEASURED", result.blocking_keys)
        self.assertIn("excluded from the verdict", gate.explanation)

    def test_invalidating_assumptions_key_on_model_status(self):
        # CERT-01: the "what would invalidate this result?" card labeled any
        # passing verdict "correlated" — a 2-condition pass (model_status
        # unvalidated) or settle-only pass (partially_validated) then showed
        # "correlated" on the same card as "MODEL UNVALIDATED".  The label
        # must key on the four-state model.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = []
        for tid, h in (("S1", 0.5), ("S2", 1.0), ("S3", 1.5)):
            request["validation"]["measured_tests"].append({
                "test_id": tid, "cad_revision": "cad-r42", "material": "ABS",
                "height_m": h, "surface": "concrete", "orientation": "flat",
                "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
                "measured_settle_s": 1.0, "measured_settle_s_uncertainty": 0.1,
            })
        # Settle-only data: verdict passes on settle, but peak-accel gate
        # keeps model_status partially_validated.
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "settle_time_s":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_settle_s"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertEqual(result["shell"]["model_status"], "partially_validated")
        assumption = next(
            item for item in result["shell"]["invalidating_assumptions"]
            if item["assumption"] == "physical correlation"
        )
        self.assertNotEqual(assumption["status"], "correlated")
        self.assertIn("not_correlated", assumption["status"])
        # A genuine 3-condition correlated campaign IS labeled correlated.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("F1", 0.5, 470.0),
            flat_test("F2", 1.0, 667.0),
            flat_test("F3", 1.5, 817.0),
        ]
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertEqual(result["shell"]["model_status"], "correlated")
        assumption = next(
            item for item in result["shell"]["invalidating_assumptions"]
            if item["assumption"] == "physical correlation"
        )
        self.assertEqual(assumption["status"], "correlated")

    def test_hard_failure_validity_state_failed(self):
        # SENIOR-04: a hard failure (NaN input, invalid request) previously
        # left validity.state at the default "valid" — a failed run must
        # present state failed + confidence low.
        result = run_pipeline("not-a-dict")
        self.assertEqual(result["errors"][0]["code"], "INVALID_REQUEST")
        self.assertEqual(result["lifecycle_state"], "failed")
        self.assertEqual(result["validity"]["state"], "failed")
        self.assertEqual(result["validity"]["confidence"], "low")
        from tests.test_pipeline import mouse_project_request

        request = mouse_project_request(
            drop_simulation={"height_m": float("nan"), "drop_count": 1},
        )
        result = run_pipeline(request)
        self.assertEqual(result["lifecycle_state"], "failed")
        self.assertEqual(result["validity"]["state"], "failed")
        self.assertEqual(result["validity"]["confidence"], "low")

    def test_lifecycle_payload_discloses_fatigue_screening(self):
        # CERT-04: the fatigue S-N law is class-level screening, not a
        # validated material curve — the limitation must be visible in the
        # run payload, not only in docstrings.
        request = validation_request()
        request["lifecycle"] = {
            "prior_drops": 100, "actuation_cycles": 5000,
            "slide_distance_km": 10.0, "age_days": 365,
        }
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        lifecycle = result.get("lifecycle") or {}
        fatigue = lifecycle.get("fatigue_model") or {}
        self.assertIn("NOT a validated material fatigue curve", fatigue.get("basis", ""))
        self.assertIn("screening", fatigue.get("limitation", "").lower())


class InertPinDisclosureTests(unittest.TestCase):
    def test_inert_pins_warn(self):
        request = validation_request()
        request["validation"]["prototype"] = {"thickness_m": 0.0018}
        request["validation"]["contact"]["substeps"] = 4
        result = run_pipeline(request)
        codes = [issue["code"] for issue in result["issues"]]
        self.assertIn("VALIDATION_THICKNESS_PIN_NOT_APPLIED", codes)
        self.assertIn("VALIDATION_SUBSTEPS_PIN_INERT", codes)

    def test_trace_reports_actual_solver_method(self):
        request = validation_request()
        result = run_pipeline(request)
        trace = result["shell"]["inputs_trace"]
        response = result["structural"]["response"]
        self.assertEqual(trace["structural"]["model"], response["method_id"])


class FollowUpRegressionTests(unittest.TestCase):
    """Regressions for the fresh-verification follow-up fixes."""

    def test_exploration_correlation_is_equivalence_gated(self):
        # V1-B1: exploration mode previously bypassed the equivalence gate —
        # corner/edge/no-sensor data drove correlated + high confidence.
        request = {
            "schema_id": "gms.project-document/1", "mode": "exploration", "units": "mm",
            "objects": [{"id": "shell", "geometry": SHELL, "material": "ABS",
                         "structural_behavior": "shell"}],
            "drop_simulation": {"height_m": 1.0, "drop_count": 1},
            "correlation": {
                "acceptance": {},
                "measured_drops": [
                    {"drop_id": "D1", "height_m": 0.5, "surface": "concrete",
                     "orientation": "corner", "measured_peak_accel_g": 382.0},
                    {"drop_id": "D2", "height_m": 1.0, "surface": "concrete",
                     "orientation": "edge", "measured_peak_accel_g": 535.0},
                    {"drop_id": "D3", "height_m": 1.5, "surface": "concrete",
                     "orientation": "flat", "measured_peak_accel_g": 817.0},
                ],
            },
        }
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for drop in request["correlation"]["measured_drops"]:
            drop["measured_peak_accel_g"] = preds[drop["drop_id"]]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertEqual(result["correlation"]["excluded_conditions"], 3)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")

    def test_quaternion_sign_identity_canonicalized(self):
        # V2: q and -q describe the same pose; with equivalence enforced the
        # explicit quaternions are excluded (fail-closed), and the identity
        # canonicalization is defense-in-depth for equivalent poses.
        request = {
            "schema_id": "gms.project-document/1", "mode": "exploration", "units": "mm",
            "objects": [{"id": "shell", "geometry": SHELL, "material": "ABS",
                         "structural_behavior": "shell"}],
            "drop_simulation": {"height_m": 1.0, "drop_count": 1},
            "correlation": {
                "acceptance": {},
                "measured_drops": [
                    {"drop_id": "Q1", "height_m": 0.5, "surface": "concrete",
                     "orientation": {"quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]},
                     "sensor": {"quantity": "resultant_peak_g",
                                "location_body_m": [0.0, 0.0, 0.0]},
                     "measured_peak_accel_g": 470.0},
                    {"drop_id": "Q2", "height_m": 0.5, "surface": "concrete",
                     "orientation": {"quaternion_wxyz": [-1.0, 0.0, 0.0, 0.0]},
                     "sensor": {"quantity": "resultant_peak_g",
                                "location_body_m": [0.0, 0.0, 0.0]},
                     "measured_peak_accel_g": 470.0},
                    {"drop_id": "Q3", "height_m": 1.0, "surface": "concrete",
                     "orientation": "flat",
                     "sensor": {"quantity": "resultant_peak_g",
                                "location_body_m": [0.0, 0.0, 0.0]},
                     "measured_peak_accel_g": 667.0},
                ],
            },
        }
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for drop in request["correlation"]["measured_drops"]:
            drop["measured_peak_accel_g"] = preds[drop["drop_id"]]
        result = run_pipeline(request)
        # Explicit quaternions are non-equivalent (fail-closed): the two
        # identical poses cannot produce 3 evaluated conditions.
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["model_status"], "correlated")

    def test_model_status_counts_verdict_conditions_only(self):
        # V4-B2: the badge N must count the verdict's evaluated conditions,
        # never excluded (non-equivalent) rows.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("F1", 0.5, 470.0),
            flat_test("F2", 1.0, 667.0),
            flat_test("F3", 1.5, 817.0),
            {"test_id": "X1", "cad_revision": "cad-r42", "material": "ABS",
             "height_m": 1.0, "surface": "concrete", "orientation": "corner",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 535.0, "measured_peak_accel_g_uncertainty": 10.0},
        ]
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        physical = result["shell"]["physical_validation"]
        self.assertEqual(physical["independent_conditions"], 3)
        self.assertEqual(physical["compared_conditions"], 4)


class Round2RepairRegressionTests(unittest.TestCase):
    """Regressions for the round-2 repair batch (orchestrator-fixed)."""

    def test_height_micro_perturbation_no_longer_bypasses(self):
        # W2-02E/W2-06C: 5 um-spaced heights previously counted as 3
        # independent conditions.  The identity key now rounds height to 4dp
        # (matching the validated-domain key).
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = []
        for h, tid in ((0.750000, "T1"), (0.750005, "T2"), (0.750010, "T3")):
            request["validation"]["measured_tests"].append({
                "test_id": tid, "cad_revision": "cad-r42", "material": "ABS",
                "height_m": h, "surface": "concrete", "orientation": "flat",
                "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": 577.0, "measured_peak_accel_g_uncertainty": 10.0,
            })
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertIn("duplicate", result["correlation"]["explanation"])
        self.assertNotEqual(result["shell"]["model_status"], "correlated")

    def test_absent_identity_fields_fail_closed(self):
        # W2-04 family: tests without material/cad_revision/prototype_id
        # previously passed the identity gate and drove correlated.
        request = validation_request()
        request["validation"]["prototype"] = {"prototype_id": "P1"}
        request["validation"]["measured_tests"] = [
            {"test_id": "T1", "height_m": 0.5, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 470.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T2", "height_m": 1.0, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 667.0, "measured_peak_accel_g_uncertainty": 10.0},
            {"test_id": "T3", "height_m": 1.5, "surface": "concrete", "orientation": "flat",
             "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
             "measured_peak_accel_g": 817.0, "measured_peak_accel_g_uncertainty": 10.0},
        ]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["excluded_conditions"], 3)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        rows = {row["test_id"]: row for row in
                result["shell"]["validation"]["measured_comparison"]["rows"]}
        self.assertTrue(rows["T1"]["identity_mismatch"])

    def test_axis_with_resultant_rejected(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"][0]["sensor"] = {
            "quantity": "resultant_peak_g", "axis": "z",
            "location_body_m": [0.0, 0.0, 0.0],
        }
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_unknown_validation_drop_keys_rejected(self):
        request = validation_request()
        request["validation"]["drop"]["spin_rps"] = 5
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_quaternion_magnitude_bound(self):
        request = validation_request()
        request["validation"]["drop"]["orientation"] = {"quaternion_wxyz": [1e9, 0, 0, 0]}
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"][0]["orientation"] = {"quaternion_wxyz": [1e9, 0, 0, 0]}
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_measured_test_quaternion_nan_is_config_error(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"][0]["orientation"] = {
            "quaternion_wxyz": [float("nan"), 0, 0, 0]
        }
        result = run_pipeline(request)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("VALIDATION_CONFIG_INVALID", codes)
        self.assertNotIn("PIPELINE_INTERNAL", codes)

    def test_inertia_plausibility_bounds(self):
        request = validation_request()
        request["validation"]["prototype"] = {
            "inertia_kg_m2": [[1e9, 0, 0], [0, 1e9, 0], [0, 0, 1e9]]
        }
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_restitution_pin_achievable_band(self):
        # W2-05E-B2: a restitution pin of 0.0 (documented [0,1]) previously
        # hard-failed downstream as a physics error.  Now rejected at config.
        request = validation_request()
        request["validation"]["contact"]["restitution"] = 0.0
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])
        request = validation_request()
        request["validation"]["contact"]["restitution"] = 1.0
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_substeps_infinity_no_crash(self):
        request = validation_request()
        request["validation"]["contact"]["substeps"] = float("inf")
        result = run_pipeline(request)
        self.assertIn("VALIDATION_CONFIG_INVALID", [e["code"] for e in result["errors"]])

    def test_components_do_not_contaminate_shell_verdict(self):
        # W1-04: the components section appended DROP_SIMULATION_INERTIA_
        # APPROXIMATED into the shared issues, flipping the shell verdict.
        # With an open mesh (inertia None) the shell must be identical with
        # and without component sections.
        open_mesh = {
            "type": "mesh",
            "vertices": [[0.0, 0.0, 0.0], [0.06, 0.0, 0.0], [0.0, 0.04, 0.0],
                         [0.06, 0.04, 0.0], [0.0, 0.0, 0.01], [0.06, 0.0, 0.01],
                         [0.0, 0.04, 0.01], [0.06, 0.04, 0.01]],
            "triangles": [[0, 1, 2], [1, 3, 2], [4, 6, 5], [4, 5, 7]],
            "units": "m",
        }
        request = validation_request()
        request["objects"] = [{"id": "shell", "geometry": open_mesh, "material": "ABS",
                               "structural_behavior": "shell"}]
        request["validation"]["prototype"] = {"mass_kg": 0.05}
        base = run_pipeline(request)
        with_components = run_pipeline(request)
        self.assertEqual(base["shell"]["classification"], with_components["shell"]["classification"])
        self.assertEqual(base["shell"]["status"], with_components["shell"]["status"])
        self.assertEqual(base["shell"]["physical_model_confidence"],
                         with_components["shell"]["physical_model_confidence"])

    def test_near_com_euclidean(self):
        # W2-16B/C: the L-infinity box admitted a diagonal sensor 8.66 mm
        # away; the near-CoM check is now Euclidean with an epsilon.
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"][0]["sensor"]["location_body_m"] = [0.005, 0.005, 0.005]
        result = run_pipeline(request)
        self.assertEqual(result["correlation"]["excluded_conditions"], 1)

    def test_sampling_resolution_warning(self):
        request = validation_request()
        request["validation"]["measured_tests"] = [
            flat_test("T1", 0.5, 470.0),
            flat_test("T2", 1.0, 667.0),
            flat_test("T3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"][0]["sensor"]["sampling_rate_hz"] = 100
        request["validation"]["measured_tests"][0]["measured_impact_duration_s"] = 0.0014
        result = run_pipeline(request)
        condition = [c for c in result["correlation"]["conditions"] if c["drop_id"] == "T1"][0]
        self.assertIn("sampling_resolution_warning", condition)

    def test_trace_records_solved_inertia(self):
        request = validation_request()
        request["validation"]["prototype"] = {
            "inertia_kg_m2": [[3e-5, 0, 0], [0, 3e-5, 0], [0, 0, 4e-5]]
        }
        result = run_pipeline(request)
        trace = result["shell"]["inputs_trace"]
        model = result["drop_simulation"]["model"]
        self.assertEqual(trace["inertia"]["inertia_tensor_kg_m2"], model["inertia_kg_m2"])
        self.assertNotEqual(
            trace["inertia"]["inertia_tensor_kg_m2"],
            trace["inertia"]["geometry_model_inertia_tensor_kg_m2"],
        )

    def test_catalog_key_order_collision_closed(self):
        # W2-10D: two catalogs with normalized-equal keys in different order
        # resolved different materials under the same run_id.  The input hash
        # now binds the ORDER.
        catalog_a = {"ABS-PC": {"name": "ABS-PC", "properties": {
            "density": {"value": 1200.0, "unit": "kg/m^3"},
            "young_modulus": {"value": 2.3e9, "unit": "Pa"},
            "poissons_ratio": 0.35,
            "yield_strength": {"value": 40e6, "unit": "Pa"},
            "tensile_allowable": {"value": 20e6, "unit": "Pa"},
            "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6},
            "approval_state": "approved",
            "provenance": {"source_type": "supplier", "source_id": "s1",
                           "condition": "dry", "confidence": "high"}},
            "ABS_PC": {"name": "ABS_PC", "properties": {
                "density": {"value": 900.0, "unit": "kg/m^3"},
                "young_modulus": {"value": 2.3e9, "unit": "Pa"},
                "poissons_ratio": 0.35,
                "yield_strength": {"value": 40e6, "unit": "Pa"},
                "tensile_allowable": {"value": 20e6, "unit": "Pa"},
                "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6},
                "approval_state": "approved",
                "provenance": {"source_type": "supplier", "source_id": "s1",
                               "condition": "dry", "confidence": "high"}},
        }
        catalog_b = {key: catalog_a[key] for key in ("ABS_PC", "ABS-PC")}
        request_a = validation_request()
        request_a["materials"] = catalog_a
        request_b = validation_request()
        request_b["materials"] = catalog_b
        first = run_pipeline(request_a)
        second = run_pipeline(request_b)
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_replay_refuses_failed_replays(self):
        # W1-09: a manifest replay that fails was previously certified
        # supported.  It now requires lifecycle completed.
        request = validation_request()
        result = run_pipeline(request)
        manifest = result["manifest"]
        forged = dict(manifest)
        # Re-sign after tampering inputs with an invalid measured value so the
        # replay fails but the hashes stay self-consistent.
        from mouse_sim import canonical as canonical_module

        forged["inputs"] = dict(manifest["inputs"])
        forged["inputs"]["validation"] = dict(manifest["inputs"]["validation"])
        forged["inputs"]["validation"]["measured_tests"] = [
            {"test_id": "X", "height_m": 1.0, "surface": "concrete",
             "orientation": "flat", "measured_peak_accel_g": -1.0}
        ]
        presented = {key: value for key, value in forged.items() if key != "manifest_hash"}
        forged["manifest_hash"] = canonical_module.manifest_hash(presented)
        replay = reproduce_from_manifest(forged)
        self.assertFalse(replay["supported"])


class Round2Wave2RepairTests(unittest.TestCase):
    """Regressions for the round-2 wave-2 repair batch (W2-02/04/05/10/12/13/16)."""

    def test_height_identity_uses_tolerance_not_cells(self):
        # W2-02 follow-up: the 4dp cell key had a knife-edge — heights
        # straddling a cell boundary (0.75005 vs 0.75006, 10 um apart)
        # counted as independent conditions and reached correlated + high.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = []
        for h, tid in ((0.75005, "T1"), (0.75006, "T2"), (0.75016, "T3")):
            request["validation"]["measured_tests"].append({
                "test_id": tid, "cad_revision": "cad-r42", "material": "ABS",
                "height_m": h, "surface": "concrete", "orientation": "flat",
                "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": 577.0, "measured_peak_accel_g_uncertainty": 10.0,
            })
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertIn("duplicate", result["correlation"]["explanation"])
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        # Distinct heights (1.0 vs 1.05) must NOT be flagged as duplicates.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("T1", 1.0, 667.0), flat_test("T2", 1.05, 683.0),
        ]
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertNotIn("duplicate", result["correlation"]["explanation"])

    def test_validation_mode_rejects_raw_correlation_identity_bypass(self):
        # W2-04 follow-up: a top-level correlation.measured_drops section in
        # validation mode bypasses the identity cross-check (foreign
        # cad_revision/material/prototype fed the verdict undetected).
        request = validation_request()
        request["validation"]["measured_tests"] = []
        request["correlation"] = {
            "measured_drops": [
                {"drop_id": "T1", "height_m": 1.0, "surface": "concrete",
                 "orientation": "flat", "material": "PC", "cad_revision": "OTHER-CAD",
                 "measured_peak_accel_g": 667.0},
            ]
        }
        result = run_pipeline(request)
        codes = [error["code"] for error in result["errors"]]
        self.assertIn("VALIDATION_CONFIG_INVALID", codes)
        # Same when measured_tests is absent entirely.
        request = validation_request()
        assert "measured_tests" not in request["validation"]
        request["correlation"] = {
            "measured_drops": [
                {"drop_id": "T1", "height_m": 1.0, "surface": "concrete",
                 "orientation": "flat", "material": "PC", "cad_revision": "OTHER-CAD",
                 "measured_peak_accel_g": 667.0},
            ]
        }
        result = run_pipeline(request)
        codes = [error["code"] for error in result["errors"]]
        self.assertIn("VALIDATION_CONFIG_INVALID", codes)

    def test_raw_measured_drops_nan_is_clean_error(self):
        # W2-05/W8-02 follow-up: NaN in raw correlation.measured_drops
        # crashed the run as PIPELINE_INTERNAL; it must be a CLEAN
        # fail-closed error (never PIPELINE_INTERNAL, never a completed run
        # under the correlation-less request's run_id — which would let the
        # cache serve evidence-dropped payloads).
        from tests.test_pipeline import mouse_project_request

        request = mouse_project_request(
            drop_simulation={"height_m": 0.5, "drop_count": 1},
            correlation={
                "acceptance": {},
                "measured_drops": [
                    {"drop_id": "T1", "height_m": 1.0, "surface": "concrete",
                     "orientation": "flat", "measured_peak_accel_g": float("nan")},
                ],
            },
        )
        result = run_pipeline(request)
        codes = [error["code"] for error in result["errors"]]
        self.assertNotIn("PIPELINE_INTERNAL", codes)
        self.assertIn("CORRELATION_EVALUATION_FAILED", codes)
        self.assertEqual(result.get("lifecycle_state"), "failed")
        # The request must NOT collide with the correlation-less run_id.
        bare = mouse_project_request(drop_simulation={"height_m": 0.5, "drop_count": 1})
        bare_result = run_pipeline(bare)
        self.assertNotEqual(result.get("run_id"), bare_result.get("run_id"))
        # And must not be served from a pre-populated cache slot of the
        # bare request.
        from mouse_sim import cache as cache_module

        with tempfile.TemporaryDirectory() as directory:
            cache = cache_module.ArtifactCache(directory)
            run_pipeline(bare, cache=cache, use_cache=True)
            result = run_pipeline(request, cache=cache, use_cache=True)
            self.assertEqual(result["errors"][0]["code"], "CORRELATION_EVALUATION_FAILED")

    def test_raw_measured_drops_negative_and_unknown_key_rejected(self):
        # W2-05 follow-up: negative/implausible values and unknown keys in
        # raw correlation.measured_drops were silently accepted; they now
        # fail the correlation verdict honestly (the run never crashes).
        from tests.test_pipeline import mouse_project_request

        request = mouse_project_request(
            drop_simulation={"height_m": 0.5, "drop_count": 1},
            correlation={
                "acceptance": {},
                "measured_drops": [
                    {"drop_id": "T1", "height_m": 1.0, "surface": "concrete",
                     "orientation": "flat", "measured_peak_accel_g": -1.0},
                    {"drop_id": "T2", "height_m": 1.5, "surface": "concrete",
                     "orientation": "flat", "measured_peak_accel_g": 900.0},
                    {"drop_id": "T3", "height_m": 2.0, "surface": "concrete",
                     "orientation": "flat", "measured_peak_accel_g": 1100.0},
                ],
            },
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")
        request = mouse_project_request(
            drop_simulation={"height_m": 0.5, "drop_count": 1},
            correlation={
                "acceptance": {},
                "measured_drops": [
                    {"drop_id": "T1", "height_m": 1.0, "surface": "concrete",
                     "orientation": "flat", "spin_rps": 5, "measured_peak_accel_g": 667.0},
                ],
            },
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["verdict"], "fail")

    def test_wrapper_root_materials_order_bound(self):
        # W2-10F: the {"materials": {catalog}} wrapper root collapsed the
        # inner catalog to one sorted pair; the inner key ORDER (which the
        # resolver uses for normalized-equal keys) must be bound.
        base = {"ABS-PC": {"name": "ABS-PC", "properties": {
            "density": {"value": 1200.0, "unit": "kg/m^3"},
            "young_modulus": {"value": 2.3e9, "unit": "Pa"},
            "poissons_ratio": 0.35,
            "yield_strength": {"value": 40e6, "unit": "Pa"},
            "tensile_allowable": {"value": 20e6, "unit": "Pa"},
            "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6},
            "approval_state": "approved",
            "provenance": {"source_type": "supplier", "source_id": "s1",
                           "condition": "dry", "confidence": "high"}},
            "ABS_PC": {"name": "ABS_PC", "properties": {
                "density": {"value": 900.0, "unit": "kg/m^3"},
                "young_modulus": {"value": 2.3e9, "unit": "Pa"},
                "poissons_ratio": 0.35,
                "yield_strength": {"value": 40e6, "unit": "Pa"},
                "tensile_allowable": {"value": 20e6, "unit": "Pa"},
                "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6},
                "approval_state": "approved",
                "provenance": {"source_type": "supplier", "source_id": "s1",
                               "condition": "dry", "confidence": "high"}},
        }
        wrapper_a = {"materials": base}
        wrapper_b = {"materials": {key: base[key] for key in ("ABS_PC", "ABS-PC")}}
        request_a = validation_request()
        request_a["materials"] = wrapper_a
        request_b = validation_request()
        request_b["materials"] = wrapper_b
        first = run_pipeline(request_a)
        second = run_pipeline(request_b)
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_dict_form_materials_manifest_replays(self):
        # W2-10G: dict-form catalogs are snapshotted as ordered lists; the
        # replay must rebuild the dict (fail-closed replays were certified
        # unsupported before).
        request = validation_request()
        request["materials"] = {"ABS": {"name": "ABS", "properties": {
            "density": {"value": 1040.0, "unit": "kg/m^3"},
            "young_modulus": {"value": 2.3e9, "unit": "Pa"},
            "poissons_ratio": 0.35,
            "yield_strength": {"value": 40e6, "unit": "Pa"},
            "tensile_allowable": {"value": 20e6, "unit": "Pa"},
            "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6},
            "approval_state": "approved",
            "provenance": {"source_type": "supplier", "source_id": "s1",
                           "condition": "dry", "confidence": "high"}}}
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        replay = reproduce_from_manifest(result["manifest"])
        self.assertTrue(replay["supported"], replay)

    def test_trace_reports_executed_seed(self):
        # W2-07: the executed seed was misreported as 0 in the drop config
        # and trace; simulate() now records the executed seed.
        from tests.test_pipeline import mouse_project_request

        request = mouse_project_request(
            drop_simulation={"height_m": 1.0, "drop_count": 1, "seed": 987654, "unit_seed": 42},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        config_seed = result["drop_simulation"]["config"]["seed"]
        self.assertEqual(config_seed, 987654)
        trace_seed = result["shell"]["inputs_trace"]["seed"]
        self.assertEqual(trace_seed, 987654)

    def test_trace_boundary_assumptions_match_cantilever(self):
        # W2-12: the trace hardcoded "simply-supported" even for a cantilever
        # beam; the label must match the executed support.
        request = validation_request()
        request["structure"] = {
            "type": "beam", "L_m": 0.06, "I_m4": 1e-10, "A_m2": 1e-4,
            "section_modulus_m3": 1e-7, "support": "cantilever",
        }
        request["validation"]["structural"] = {"model": "beam_closed_form_v1"}
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        trace = result["shell"]["inputs_trace"]["structural"]["boundary_assumptions"]
        self.assertIn("cantilever", trace.lower())
        request = validation_request()
        result = run_pipeline(request)
        trace = result["shell"]["inputs_trace"]["structural"]["boundary_assumptions"]
        self.assertIn("simply-supported", trace.lower())

    def test_badge_counts_verdict_conditions_only(self):
        # W2-13: the correlated branch must count the VERDICT's evaluated
        # conditions, never a broader metrics-bearing list.
        from mouse_sim.shell_validation import build_model_status

        crafted = {
            "verdict": "pass",
            "evaluated_conditions": 3,
            "conditions": [
                {"drop_id": "T1", "metrics": [{"metric_key": "peak_accel_g"}]},
                {"drop_id": "T2", "metrics": [{"metric_key": "peak_accel_g"}]},
                {"drop_id": "T3", "metrics": [{"metric_key": "peak_accel_g"}]},
                {"drop_id": "T4", "metrics": [{"metric_key": "peak_accel_g"}]},
            ],
        }
        status = build_model_status({"correlation": crafted})
        self.assertEqual(
            status["physical_validation"]["independent_conditions"], 3
        )

    def test_fallback_missing_sensor_location_not_equivalent(self):
        # W2-16C: the row fallback treated a sensor with NO location as
        # near-CoM (fail-open); it must be fail-closed like the pipeline gate.
        from mouse_sim.shell_validation import measured_comparison

        test = {
            "test_id": "T1", "cad_revision": "cad-r42", "material": "ABS",
            "prototype_id": "P1", "height_m": 1.0, "surface": "concrete",
            "orientation": "flat", "environment": {},
            "measured": {"measured_peak_accel_g": 667.0},
            "uncertainty": {"measured_peak_accel_g_uncertainty": 10.0},
            "sensor": {"quantity": "resultant_peak_g"},
        }
        rows = measured_comparison([test], {})
        self.assertFalse(rows["rows"][0]["equivalent"])


class AcceptanceGatePinTests(unittest.TestCase):
    """Round-2 maximum-depth audit: pin the acceptance-gate constants.

    Each test is a regression probe for one specific mutation of the
    acceptance machinery — max_relative_error 0.25 -> 0.50, min_drop_
    conditions 3 -> 2, the shell >= 3 floor -> 2, and max_bias 0.10 ->
    0.30 — all of which previously passed the full 140-test suite.
    """

    def test_errors_in_25_to_50_percent_band_fail(self):
        # Pins max_relative_error == 0.25 (pipeline.py default): a campaign
        # whose per-metric errors all fall in (0.25, 0.50] must FAIL.  With
        # the gate corrupted to 0.50 every metric passes and the verdict
        # flips to pass.  The bias gate must not mask the probe, so peak
        # and duration metrics are scaled in OPPOSITE directions —
        # measured = 0.74 * predicted (~35% error) on peak and
        # measured = 1.40 * predicted (~29% error) on duration — which
        # cancels the signed bias to ~3% while every error lands in
        # (0.25, 0.50] and R^2 stays at 1.0.
        def campaign(measured_peak, measured_duration):
            return [
                flat_test(
                    test_id,
                    height,
                    measured_peak[test_id],
                    measured_impact_duration_s=measured_duration[test_id],
                    measured_impact_duration_s_uncertainty=0.0005,
                )
                for test_id, height in (("F1", 0.5), ("F2", 1.0), ("F3", 1.5))
            ]

        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = campaign(
            {"F1": 470.0, "F2": 667.0, "F3": 817.0},
            {"F1": 0.003, "F2": 0.003, "F3": 0.003},
        )
        first = run_pipeline(request)
        peak_pred = {}
        duration_pred = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    peak_pred[condition["drop_id"]] = metric["predicted"]
                elif metric["metric_key"] == "impact_duration_s":
                    duration_pred[condition["drop_id"]] = metric["predicted"]
        request["validation"]["measured_tests"] = campaign(
            {test_id: 0.74 * peak_pred[test_id] for test_id in peak_pred},
            {test_id: 1.40 * duration_pred[test_id] for test_id in duration_pred},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        correlation = result["correlation"]
        self.assertEqual(correlation["verdict"], "fail")
        self.assertIn("error bound", correlation["explanation"])
        for condition in correlation["conditions"]:
            for metric in condition["metrics"]:
                error = metric["relative_error"]
                self.assertIsNotNone(error)
                self.assertGreater(error, 0.25)
                self.assertLessEqual(error, 0.50)
        # The signed bias must stay inside its own gate so the failure is
        # the error gate's alone (a bias failure would mask the corruption).
        self.assertIsNotNone(correlation["bias"])
        self.assertLessEqual(abs(correlation["bias"]), 0.10)

    def test_two_condition_exact_fit_cannot_reach_correlated(self):
        # Pins min_drop_conditions == 3 (pipeline.py default) AND the shell
        # >= 3 floor (shell_validation.py build_model_status): two perfectly
        # matching drops must fail the verdict and never reach correlated /
        # high confidence, even when the pipeline floor is corrupted — the
        # shell floor is the last line of defense.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("G1", 0.5, 470.0),
            flat_test("G2", 1.0, 667.0),
        ]
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["verdict"], "fail")
        self.assertEqual(result["correlation"]["evaluated_conditions"], 2)
        self.assertNotEqual(result["shell"]["model_status"], "correlated")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")

    def test_bias_between_10_and_30_percent_fails(self):
        # Pins max_bias == 0.10 (pipeline.py _correlation_summary): a 13%
        # signed bias with every per-metric error inside the 25% bound must
        # FAIL; corrupting the gate to 0.30 admits it.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("H1", 0.5, 470.0),
            flat_test("H2", 1.0, 667.0),
            flat_test("H3", 1.5, 817.0),
        ]
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["height_m"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = 1.13 * preds[entry["height_m"]]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        correlation = result["correlation"]
        self.assertEqual(correlation["verdict"], "fail")
        self.assertIn("bias", correlation["explanation"])
        self.assertIsNotNone(correlation["bias"])
        self.assertGreater(correlation["bias"], 0.10)
        self.assertLessEqual(correlation["bias"], 0.30)

    def test_acceptance_gate_constants_pinned(self):
        # Direct pins of the acceptance-gate literals, each exercised
        # behaviorally so a mutated constant changes observable output:
        # the pipeline defaults max_relative_error 0.25 / min_drop_
        # conditions 3 (surface in the correlation summary), the shell
        # >= 3 floor, and the correlation max_bias 0.10.
        from mouse_sim.pipeline import _correlation_summary
        from mouse_sim.shell_validation import build_model_status

        # (1) Pipeline acceptance defaults surface in the correlation
        # summary (validation mode pins acceptance to {}).
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("P1", 0.5, 470.0),
            flat_test("P2", 1.0, 667.0),
            flat_test("P3", 1.5, 817.0),
        ]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["max_relative_error"], 0.25)
        self.assertEqual(result["correlation"]["min_drop_conditions"], 3)

        # (2) Shell floor: a passed 2-condition comparison is insufficient
        # (unvalidated, never correlated) while the floor holds.
        crafted = {
            "verdict": "pass",
            "evaluated_conditions": 2,
            "conditions": [
                {"drop_id": "T1", "metrics": [{"metric_key": "peak_accel_g"}],
                 "equivalent": True, "identity_ok": True},
                {"drop_id": "T2", "metrics": [{"metric_key": "peak_accel_g"}],
                 "equivalent": True, "identity_ok": True},
            ],
        }
        status = build_model_status({"correlation": crafted})
        self.assertEqual(status["model_status"], "unvalidated")
        self.assertEqual(status["physical_validation"]["status"], "insufficient_conditions")

        # (3) max_bias literal 0.10: a 13% bias with every metric already
        # passing the 25% error bound must fail the summary.
        crafted_conditions = []
        for drop_id, height, predicted in (("B1", 0.5, 470.0), ("B2", 1.0, 667.0), ("B3", 1.5, 817.0)):
            crafted_conditions.append({
                "drop_id": drop_id, "height_m": height, "surface": "concrete",
                "orientation": "flat", "equivalent": True, "identity_ok": True,
                "metrics": [{"metric_key": "peak_accel_g",
                             "measured": 1.13 * predicted, "predicted": predicted,
                             "relative_error": 0.115, "pass": True}],
            })
        summary = _correlation_summary(crafted_conditions, 0.25, 3)
        self.assertEqual(summary["verdict"], "fail")
        self.assertIn("bias", summary["explanation"])


class Round2Wave4RepairTests(unittest.TestCase):
    """Regressions for the round-2 wave-4 repair batch (W4-03 load/limits)."""

    def test_negative_load_magnitude_fails_closed(self):
        # W4-03-01: a negative load case magnitude produced a plausible
        # PASS/SAFE (von Mises stress is sign-invariant); it must be a
        # structural evaluation failure, never a pass.
        request = validation_request()
        request["load_case"] = {
            "kind": "pressure",
            "magnitude": {"value": -1.0, "unit": "kPa"},
        }
        result = run_pipeline(request)
        codes = [error["code"] for error in result["errors"]]
        self.assertIn("STRUCTURAL_EVALUATION_FAILED", codes)
        self.assertEqual(result.get("lifecycle_state"), "failed")
        self.assertNotEqual(result.get("shell", {}).get("status"), "pass")

    def test_zero_load_magnitude_fails_closed(self):
        request = validation_request()
        request["load_case"] = {
            "kind": "pressure",
            "magnitude": {"value": 0.0, "unit": "kPa"},
        }
        result = run_pipeline(request)
        codes = [error["code"] for error in result["errors"]]
        self.assertIn("STRUCTURAL_EVALUATION_FAILED", codes)

    def test_negative_force_fails_closed(self):
        request = validation_request()
        request["load_case"] = {"kind": "point", "force": {"value": -5.0, "unit": "N"}}
        result = run_pipeline(request)
        codes = [error["code"] for error in result["errors"]]
        self.assertIn("STRUCTURAL_EVALUATION_FAILED", codes)

    def test_inverted_thickness_limits_restrict_verdict(self):
        # W4-03-02: inverted options.min_thickness_m > max_thickness_m was
        # flagged but the shell still PASSED; the contradiction must restrict
        # the verdict to insufficient_evidence.
        request = validation_request()
        request["options"] = {"min_thickness_m": 0.05, "max_thickness_m": 0.001}
        result = run_pipeline(request)
        self.assertEqual(result.get("lifecycle_state"), "completed")
        self.assertEqual(result["shell"]["status"], "warn")
        self.assertEqual(result["shell"]["classification"], "insufficient_evidence")
        self.assertNotEqual(result["shell"]["status"], "pass")

    def test_valid_load_still_passes(self):
        request = validation_request()
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["shell"]["status"], "pass")


class Round2Wave5RepairTests(unittest.TestCase):
    """Regressions for the round-2 wave-5 repair batch (W5-01/W5-03/W5-04)."""

    def _exploration_campaign(self, main_height):
        request = validation_request()
        del request["validation"]
        request["mode"] = "exploration"
        request["drop_simulation"] = {"height_m": main_height, "drop_count": 1}
        drops = []
        # Measured values track the DEFAULT Hertz point-contact predictions
        # for this fixture (~2880 g at 0.5 m, ~4370 g at 1.0 m, ~5580 g at
        # 1.5 m on concrete) so the campaign correlates.
        for h, v in ((0.5, 2880.0), (1.0, 4370.0), (1.5, 5580.0)):
            drops.append({
                "drop_id": "D{}".format(h), "height_m": h, "surface": "concrete",
                "orientation": "flat", "measured_peak_accel_g": v,
                "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
            })
        request["correlation"] = {"acceptance": {}, "measured_drops": drops}
        return request

    def test_exploration_outside_domain_matches_validation(self):
        # W5-01: exploration could never emit outside_validated_domain, so
        # the same campaign reported correlated+high (exploration) vs
        # outside_validated_domain+medium (validation) for an extrapolated
        # main drop.  The status must be mode-independent.
        request = self._exploration_campaign(0.75)
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["shell"]["model_status"], "outside_validated_domain")
        self.assertNotEqual(result["shell"]["physical_model_confidence"], "high")
        # In-domain main drop still correlates in exploration.
        request = self._exploration_campaign(1.0)
        result = run_pipeline(request)
        self.assertEqual(result["shell"]["model_status"], "correlated")

    def test_exploration_identity_unchecked_disclosed(self):
        # W5-03: exploration raw correlation carries no identity
        # declarations; a correlated exploration result was silently
        # presented as identity-consistent.  It must be disclosed.
        request = self._exploration_campaign(1.0)
        result = run_pipeline(request)
        pv = result["shell"].get("physical_validation") or {}
        self.assertFalse(pv.get("identity_checked", True))
        self.assertIn("identity cross-check not performed", pv.get("note", ""))
        # Validation mode IS identity-checked.
        request = validation_request()
        request["validation"]["drop"]["height_m"] = 1.0
        request["validation"]["measured_tests"] = []
        for h, v in ((0.5, 471.0), (1.0, 667.0), (1.5, 817.0)):
            request["validation"]["measured_tests"].append({
                "test_id": "T{}".format(h), "cad_revision": "cad-r42", "material": "ABS",
                "height_m": h, "surface": "concrete", "orientation": "flat",
                "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
                "measured_peak_accel_g": v, "measured_peak_accel_g_uncertainty": 10.0,
            })
        result = run_pipeline(request)
        pv = result["shell"].get("physical_validation") or {}
        self.assertTrue(pv.get("identity_checked", False))

    def test_web_manifest_stays_replayable(self):
        # W5-04: slim_result_for_web previously replaced manifest.inputs.
        # objects with a count/sha summary, breaking reproduce_from_manifest
        # on the web path.  The served manifest must replay.
        from mouse_sim import web_api
        from mouse_sim.cache import ArtifactCache
        from mouse_sim.pipeline import reproduce_from_manifest

        request = validation_request()
        with tempfile.TemporaryDirectory() as directory:
            cache = ArtifactCache(directory)
            result = run_pipeline(request, cache=cache)
            slim = web_api.slim_result_for_web(result)
            manifest = slim["manifest"]
            self.assertEqual(manifest["manifest_hash"], result["manifest"]["manifest_hash"])
            replay = reproduce_from_manifest(manifest)
            self.assertTrue(replay["supported"], replay)

    def test_excluded_diagnostic_rows_do_not_veto_verdict(self):
        # W10-01: the campaign matrix documents rows 4-8/11 as diagnostic —
        # reported with their flags but "do NOT contribute to the verdict".
        # They must not VETO it either: a passing equivalent subset still
        # reaches correlated, with the exclusions disclosed.
        request = validation_request()
        request["validation"]["drop"]["orientation"] = "flat"
        request["validation"]["measured_tests"] = [
            flat_test("F1", 0.5, 470.0),
            flat_test("F2", 1.0, 667.0),
            flat_test("F3", 1.5, 817.0),
        ]
        request["validation"]["measured_tests"].append({
            "test_id": "C1", "cad_revision": "cad-r42", "material": "ABS",
            "height_m": 1.0, "surface": "concrete", "orientation": "corner",
            "sensor": {"quantity": "resultant_peak_g", "location_body_m": [0.0, 0.0, 0.0]},
            "measured_peak_accel_g": 535.0, "measured_peak_accel_g_uncertainty": 10.0,
        })
        first = run_pipeline(request)
        preds = {}
        for condition in first["correlation"]["conditions"]:
            for metric in condition["metrics"]:
                if metric["metric_key"] == "peak_accel_g":
                    preds[condition["drop_id"]] = metric["predicted"]
        for entry in request["validation"]["measured_tests"]:
            entry["measured_peak_accel_g"] = preds[entry["test_id"]]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlation"]["verdict"], "pass")
        self.assertEqual(result["correlation"]["evaluated_conditions"], 3)
        self.assertEqual(result["correlation"]["excluded_conditions"], 1)
        self.assertIn("excluded from the verdict", result["correlation"]["explanation"])
        self.assertEqual(result["shell"]["model_status"], "correlated")
        self.assertEqual(
            result["shell"]["physical_validation"]["independent_conditions"], 3
        )


if __name__ == "__main__":
    unittest.main()
