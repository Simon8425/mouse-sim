"""BASELINE / UNCALIBRATED snapshot verifier (freeze-phase item 11).

The first physical comparison must be against this untouched baseline:
reference/shell_baseline_uncalibrated.json records the exact engine hash,
configuration digests, run id, and full simulated outputs captured BEFORE
any measured data influenced the model.

Default run = verification: recompute every digest and the engine hash and
assert equality (a physics change that drifts the engine hash HARD-FAILS
here), then re-run the request and compare outputs within the documented
bands.  Regenerate with:  python tests/test_shell_baseline.py --regenerate
"""

import argparse
import json
import os
import sys
import unittest

from mouse_sim import canonical
from mouse_sim.pipeline import _engine_hash, _input_hashes, _run_id_for, _collect_inputs, run_pipeline
from mouse_sim.shell_validation import build_shell_trace

BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reference",
    "shell_baseline_uncalibrated.json",
)
REFERENCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reference",
    "shell_validation_reference.json",
)

# Outputs compared within these relative bands (same semantics as the
# reference-case test: regression bands, not physical validation claims).
RELATIVE_BAND = 0.005


def compute_config_hashes(request):
    """Deterministic config digests for the baseline (hashseed-independent:
    canonical_bytes is sorted-key UTF-8 JSON hashed with sha256)."""
    from mouse_sim import canonical as canon

    def digest(value):
        return canon.sha256_bytes(canon.canonical_bytes(value))

    return {
        "geometry_digest": digest(request.get("objects") or []),
        "structure_digest": digest(request.get("structure") or {}),
        "load_case_digest": digest(request.get("load_case") or {}),
        "validation_digest": digest(request.get("validation") or {}),
        "request_digest": digest(request),
        "run_id": _run_id_for(
            str(request.get("mode") or "exploration"),
            _input_hashes(_collect_inputs(request)),
            dict(request.get("options") or {}),
        ),
    }


def capture_baseline():
    """Run the reference request and capture the full uncalibrated snapshot."""
    with open(REFERENCE_PATH, encoding="utf-8") as stream:
        reference = json.load(stream)
    request = reference["request"]
    result = run_pipeline(request)
    assert not result["errors"], result["errors"]
    shell = result["shell"]
    sweep = shell["validation"]["contact_stiffness_sweep"]
    return {
        "kind": "BASELINE/UNCALIBRATED",
        "note": (
            "the first physical comparison MUST be against this untouched "
            "baseline; never fit parameters to individual tests (freeze-phase "
            "items 10-11)"
        ),
        "engine": {
            "version": result.get("engine_version"),
            "engine_hash": _engine_hash(),
        },
        "config_hashes": compute_config_hashes(request),
        "request": request,
        "trace": build_shell_trace(request, result),
        "outputs": {
            "mass": result["mass"],
            "structural_response": result["structural"]["response"],
            "drop_model": result["drop_simulation"]["model"],
            "drop_peak": result["drop_simulation"]["peak"],
            "drop_first": {
                "settled_s": result["drop_simulation"]["drops"][0]["settled_s"],
                "settled": result["drop_simulation"]["drops"][0]["settled"],
                "energy": result["drop_simulation"]["drops"][0]["energy"],
                "peak_impact_speed_m_s": result["drop_simulation"]["drops"][0]["peak_impact_speed_m_s"],
                "peak_kinetic_energy_j": result["drop_simulation"]["drops"][0]["peak_kinetic_energy_j"],
            },
            "peak_force_estimate": result["drop_simulation"]["peak_force_estimate"],
            "peak_force_estimate_n": result["drop_simulation"]["peak_force_estimate_n"],
            "sweep": sweep,
            "uncertainty_bands": shell["validation"]["uncertainty_bands"],
            "sensitivity": shell["validation"]["sensitivity"],
            "model_status": shell["model_status"],
            "physical_validation": shell["physical_validation"],
            "invalidating_assumptions": shell["invalidating_assumptions"],
            "validation_tracks": shell["validation"]["tracks"],
        },
    }


def _within(value, expected, relative):
    if expected == 0.0:
        return abs(value) <= 1e-12
    return abs(value - expected) / abs(expected) <= relative


class ShellBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(BASELINE_PATH, encoding="utf-8") as stream:
            cls.baseline = json.load(stream)
        cls.result = run_pipeline(cls.baseline["request"])
        cls.outputs = cls.baseline["outputs"]

    def test_kind_label(self):
        self.assertEqual(self.baseline["kind"], "BASELINE/UNCALIBRATED")

    def test_engine_hash_unchanged(self):
        # A physics change that drifts the engine hash HARD-FAILS: the
        # baseline must never silently drift.
        self.assertEqual(
            _engine_hash(),
            self.baseline["engine"]["engine_hash"],
            "engine hash drifted since the baseline was frozen — the shell "
            "physics changed; re-freeze deliberately, never silently",
        )

    def test_config_digests_reproduce(self):
        current = compute_config_hashes(self.baseline["request"])
        self.assertEqual(current, self.baseline["config_hashes"])

    def test_run_is_clean(self):
        self.assertEqual(self.result["errors"], [])

    def test_model_status_uncalibrated(self):
        self.assertEqual(self.result["shell"]["model_status"], "unvalidated")

    def test_mass_output_unchanged(self):
        expected = self.outputs["mass"]
        self.assertTrue(_within(self.result["mass"]["mass_kg"], expected["mass_kg"], RELATIVE_BAND))
        self.assertEqual(self.result["mass"]["mass_status"], expected["mass_status"])

    def test_structural_output_unchanged(self):
        expected = self.outputs["structural_response"]
        actual = self.result["structural"]["response"]
        for key in ("safety_factor", "max_stress_pa", "max_displacement_m"):
            self.assertTrue(_within(actual[key], expected[key], RELATIVE_BAND), key)

    def test_drop_output_unchanged(self):
        expected_model = self.outputs["drop_model"]
        actual_model = self.result["drop_simulation"]["model"]
        for key in ("mass_kg", "restitution", "friction", "gravity_m_s2", "timestep_s"):
            self.assertEqual(actual_model[key], expected_model[key], key)
        self.assertTrue(
            _within(
                self.result["drop_simulation"]["peak"]["impact_speed_m_s"],
                self.outputs["drop_peak"]["impact_speed_m_s"],
                RELATIVE_BAND,
            )
        )
        self.assertTrue(
            _within(
                self.result["drop_simulation"]["peak_force_estimate_n"],
                self.outputs["peak_force_estimate_n"],
                RELATIVE_BAND,
            )
        )

    def test_sweep_output_unchanged(self):
        expected_rows = self.outputs["sweep"]["rows"]
        actual_rows = self.result["shell"]["validation"]["contact_stiffness_sweep"]["rows"]
        for expected, actual in zip(expected_rows, actual_rows):
            for key in ("peak_force_n", "peak_acceleration_m_s2", "contact_duration_s", "contact_compression_m"):
                self.assertTrue(
                    _within(actual[key], expected[key], RELATIVE_BAND),
                    "k={} {}".format(actual["contact_stiffness_n_per_m"], key),
                )

    def test_inertia_output_unchanged(self):
        # W8-06: the verifier previously skipped the stored inertia tensor —
        # a corrupted baseline passed 9/9.  The inertia is a physics output
        # and must be verified like every other stored leaf.
        expected = self.outputs["mass"]
        actual = self.result["mass"]
        expected_inertia = expected.get("inertia_tensor_kg_m2")
        actual_inertia = actual.get("inertia_tensor_kg_m2")
        if expected_inertia is not None:
            for row_index, expected_row in enumerate(expected_inertia):
                for col_index, expected_value in enumerate(expected_row):
                    self.assertTrue(
                        _within(actual_inertia[row_index][col_index], expected_value, RELATIVE_BAND),
                        "inertia[{}][{}]".format(row_index, col_index),
                    )
        expected_model_inertia = self.outputs["drop_model"].get("inertia_kg_m2")
        actual_model_inertia = self.result["drop_simulation"]["model"].get("inertia_kg_m2")
        if expected_model_inertia is not None:
            for row_index, expected_row in enumerate(expected_model_inertia):
                for col_index, expected_value in enumerate(expected_row):
                    self.assertTrue(
                        _within(
                            actual_model_inertia[row_index][col_index],
                            expected_value,
                            RELATIVE_BAND,
                        ),
                        "drop_model inertia[{}][{}]".format(row_index, col_index),
                    )

    def test_drop_first_output_unchanged(self):
        # W8-06: settled_s / energy / peak of the first drop were never
        # verified.
        expected = self.outputs.get("drop_first") or {}
        actual = self.result["drop_simulation"]["drops"][0]
        for key in ("settled_s", "settled", "peak_impact_speed_m_s", "peak_kinetic_energy_j"):
            if key in expected and expected[key] is not None:
                if isinstance(expected[key], bool):
                    self.assertEqual(actual[key], expected[key], key)
                else:
                    self.assertTrue(_within(actual[key], expected[key], RELATIVE_BAND), key)

    def test_sensitivity_output_unchanged(self):
        # W8-06: the sensitivity rows (the campaign's top-3 ranking source)
        # were never verified against the baseline.
        expected_rows = (self.outputs.get("sensitivity") or {}).get("rows") or []
        actual = self.result["shell"]["validation"]["sensitivity"]
        actual_rows = actual.get("rows") or []
        for expected, row in zip(expected_rows, actual_rows):
            for key in ("mean_relative_response", "sensitivity_up", "sensitivity_down"):
                if key in expected and expected[key] is not None:
                    self.assertTrue(_within(row[key], expected[key], RELATIVE_BAND), key)
        self.assertEqual(actual.get("top_parameters"), (self.outputs.get("sensitivity") or {}).get("top_parameters"))

    def test_uncertainty_bands_unchanged(self):
        # W8-06: uncertainty bands (sweep-derived) were never verified.
        expected = (self.outputs.get("uncertainty_bands") or {}).get("band") or {}
        actual = (self.result["shell"]["validation"].get("uncertainty_bands") or {}).get("band") or {}
        for key, expected_entry in expected.items():
            actual_entry = actual.get(key) or {}
            for subkey in ("low", "high", "nominal"):
                if subkey in expected_entry and expected_entry[subkey] is not None:
                    self.assertTrue(
                        _within(actual_entry[subkey], expected_entry[subkey], RELATIVE_BAND),
                        "{} {}".format(key, subkey),
                    )

    def test_trace_unchanged(self):
        # W8-06/W12-01: the executed-configuration trace was never verified —
        # the flat-key loop silently skipped the nested trace dict, so trace
        # corruption passed 14/14.  Recursively compare the stored trace
        # leaves against the fresh run's trace.
        expected_trace = self.baseline.get("trace") or {}
        actual_trace = self.result["shell"]["inputs_trace"]

        def walk(expected, actual, path):
            if isinstance(expected, dict):
                self.assertIsInstance(actual, dict, path)
                for key, value in expected.items():
                    walk(value, actual.get(key), path + [key])
                return
            if isinstance(expected, (list, tuple)):
                self.assertIsInstance(actual, (list, tuple), path)
                for index, value in enumerate(expected):
                    walk(value, actual[index], path + [str(index)])
                return
            if expected is None:
                self.assertIsNone(actual, path)
                return
            # SENIOR-01: strings and booleans must match EXACTLY (the old
            # walk skipped them, so trace.engine.version "0.1.0" -> "9.9.9"
            # or -> 0.1 went undetected).
            if isinstance(expected, str) or isinstance(expected, bool):
                self.assertEqual(actual, expected, "trace {}".format(".".join(path)))
                return
            try:
                expected_float = float(expected)
            except (TypeError, ValueError):
                return
            if actual is None:
                self.fail("missing trace leaf {}".format(".".join(path)))
            try:
                actual_float = float(actual)
            except (TypeError, ValueError):
                self.fail("trace leaf {} changed type".format(".".join(path)))
            self.assertTrue(
                _within(actual_float, expected_float, RELATIVE_BAND),
                "trace {}: {} vs {}".format(".".join(path), actual_float, expected_float),
            )

        walk(expected_trace, actual_trace, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Baseline verifier/regenerator")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="re-run the reference request and overwrite the baseline file",
    )
    args, remaining = parser.parse_known_args()
    if args.regenerate:
        snapshot = capture_baseline()
        with open(BASELINE_PATH, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, indent=2)
            stream.write("\n")
        print("baseline regenerated at", BASELINE_PATH)
        print("engine hash:", snapshot["engine"]["engine_hash"])
        sys.exit(0)
    sys.argv = [sys.argv[0]] + remaining
    unittest.main()
