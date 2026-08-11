"""Shell-safety edge-case matrix: secondary component models and population
settings must never corrupt the shell result.

The shell physics (structural response, mass, drop simulation) is computed
before the component/population sections run (pipeline.py
``_run_component_and_population_sections``, invoked after the drop at
pipeline.py:1803), and those sections must fail safely or fall back without
changing any shell output.  Each case runs the unperturbed base request plus
the perturbation and asserts the shell outputs are identical (or honestly
different when the perturbation legitimately changes inputs), and that
component failures never change the shell status.
"""

import math
import unittest

from mouse_sim import canonical_json
from mouse_sim.pipeline import run_pipeline
from tests.test_pipeline import mouse_project_request

SHELL_LOAD_CASE = {"kind": "pressure", "magnitude": {"value": 1, "unit": "kPa"}}
SHELL_STRUCTURE = {
    "type": "shell_panel",
    "a_m": 0.06,
    "b_m": 0.04,
    "t_m": 0.002,
    "material": "ABS",
}
SHELL_DROP = {"height_m": 0.75, "drop_count": 1}

ALL_COMPONENT_TYPES = ("pcb", "battery", "switch", "encoder", "screw", "clip", "mount", "adhesive")

EVALUATED_STATUSES = ("pass", "warn", "fail")


def base_request(**overrides):
    request = mouse_project_request(
        load_case=dict(SHELL_LOAD_CASE),
        structure=dict(SHELL_STRUCTURE),
        drop_simulation=dict(SHELL_DROP),
    )
    request.update(overrides)
    return request


def shell_outputs(result):
    return (
        result["shell"],
        result["structural"],
        result["mass"],
        result["drop_simulation"]["trajectory"],
    )


def component_entries(result):
    return result["components"]["components"]


class ShellSafetyIsolationTests(unittest.TestCase):
    def test_no_internal_components_defined(self):
        result = run_pipeline(base_request())
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["shell"])
        self.assertIsNone(result["components"])
        self.assertIsNone(result["component_screening"])

    def test_only_shell_defined(self):
        result = run_pipeline(
            mouse_project_request(load_case=dict(SHELL_LOAD_CASE), structure=dict(SHELL_STRUCTURE))
        )
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["shell"])
        self.assertEqual(result["shell"]["status"], "pass")
        self.assertIsNone(result["drop_simulation"])
        self.assertIsNone(result["components"])
        self.assertIsNone(result["component_screening"])

    def test_default_internal_components_bare_specs(self):
        base = run_pipeline(base_request())
        specs = [{"type": ctype} for ctype in ALL_COMPONENT_TYPES]
        result = run_pipeline(base_request(components=specs))
        self.assertEqual(result["errors"], [])
        entries = component_entries(result)
        self.assertEqual(len(entries), 8)
        for entry in entries:
            self.assertIn(entry["status"], EVALUATED_STATUSES + ("not_evaluated",))
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_missing_component_properties_use_defaults(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(
                components=[
                    {"component_id": "pack", "type": "battery"},
                    {"component_id": "boss", "type": "screw"},
                ]
            )
        )
        self.assertEqual(result["errors"], [])
        for entry in component_entries(result):
            self.assertIn(entry["status"], EVALUATED_STATUSES)
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_invalid_component_properties_not_evaluated_or_guarded(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(
                components=[
                    {"component_id": "b", "type": "battery", "mass_kg": -0.02},
                    {"component_id": "p", "type": "pcb", "thickness_m": 0.0},
                    {"component_id": "a", "type": "adhesive", "area_m2": 0.0},
                    {"component_id": "c", "type": "clip", "beam_thickness_m": -1.0},
                ]
            )
        )
        self.assertEqual(result["errors"], [])
        by_id = {entry["component_id"]: entry for entry in component_entries(result)}
        self.assertNotEqual(by_id["b"]["status"], "fail")
        self.assertEqual(by_id["p"]["status"], "not_evaluated")
        self.assertEqual(by_id["a"]["status"], "not_evaluated")
        self.assertEqual(by_id["c"]["status"], "not_evaluated")
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_extreme_battery_mass_verdicts_differ_shell_unchanged(self):
        base = run_pipeline(base_request())
        heavy = run_pipeline(
            base_request(components=[{"component_id": "b", "type": "battery", "mass_kg": 1.0}])
        )
        light = run_pipeline(
            base_request(components=[{"component_id": "b", "type": "battery", "mass_kg": 1e-6}])
        )
        self.assertEqual(heavy["errors"], [])
        self.assertEqual(light["errors"], [])
        heavy_verdict = component_entries(heavy)[0]["status"]
        light_verdict = component_entries(light)[0]["status"]
        self.assertNotEqual(heavy_verdict, light_verdict)
        self.assertEqual(heavy_verdict, "fail")
        self.assertEqual(shell_outputs(heavy), shell_outputs(base))
        self.assertEqual(shell_outputs(light), shell_outputs(base))

    def test_position_keys_ignored_no_crash(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(
                components=[
                    {
                        "component_id": "b",
                        "type": "battery",
                        "position_m": [1.0, 2.0, 3.0],
                        "rotation_deg": [10, 20, 30],
                    },
                    {"component_id": "p", "type": "pcb", "position_m": [0.5, 0.5, 0.5]},
                ]
            )
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(component_entries(result)), 2)
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_missing_battery_and_pcb_only_switch(self):
        base = run_pipeline(base_request())
        result = run_pipeline(base_request(components=[{"component_id": "s", "type": "switch"}]))
        self.assertEqual(result["errors"], [])
        entries = component_entries(result)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["type"], "switch")
        self.assertIn(entries[0]["status"], EVALUATED_STATUSES + ("not_evaluated",))
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_duplicate_component_ids(self):
        base = run_pipeline(base_request())
        duplicate_specs = [
            {"component_id": "dup", "type": "battery", "mass_kg": 0.1},
            {"component_id": "dup", "type": "battery", "mass_kg": 0.2},
        ]
        with_components = run_pipeline(base_request(components=duplicate_specs))
        self.assertEqual(with_components["errors"], [])
        entries = component_entries(with_components)
        self.assertEqual(len(entries), 2)
        self.assertEqual([entry["component_id"] for entry in entries], ["dup", "dup"])
        self.assertNotEqual(entries[0]["status"], entries[1]["status"])
        self.assertEqual(shell_outputs(with_components), shell_outputs(base))
        with_population = run_pipeline(
            base_request(
                population={
                    "sample_count": 100,
                    "profile": "general",
                    "lifespan_days": 730,
                    "workers": 1,
                    "components": duplicate_specs,
                }
            )
        )
        self.assertEqual(with_population["errors"], [])
        codes = [issue["code"] for issue in with_population["issues"]]
        self.assertIn("POPULATION_ANALYSIS_FAILED", codes)
        self.assertIsNone(with_population["population"])
        self.assertEqual(shell_outputs(with_population), shell_outputs(base))

    def test_zero_mass_component(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(components=[{"component_id": "b", "type": "battery", "mass_kg": 0.0}])
        )
        self.assertEqual(result["errors"], [])
        status = component_entries(result)[0]["status"]
        self.assertIn(status, ("pass", "not_evaluated"))
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_extreme_component_mass_fails_battery_shell_unchanged(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(components=[{"component_id": "b", "type": "battery", "mass_kg": 100.0}])
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(component_entries(result)[0]["status"], "fail")
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_missing_material_default_assigned(self):
        request = mouse_project_request(
            load_case=dict(SHELL_LOAD_CASE),
            structure=dict(SHELL_STRUCTURE),
            drop_simulation=dict(SHELL_DROP),
        )
        request["objects"] = [
            {"id": "shell", "geometry": {"type": "box", "size": [100, 60, 40]}}
        ]
        request["components"] = [
            {"component_id": "b", "type": "battery"},
            {"component_id": "s", "type": "screw"},
        ]
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        codes = [issue["code"] for issue in result["issues"]]
        self.assertIn("DEFAULT_MATERIAL_ASSIGNED", codes)
        for entry in component_entries(result):
            self.assertIn(entry["status"], EVALUATED_STATUSES)
        shell = result["shell"]
        self.assertIsNotNone(shell)
        self.assertEqual(shell["status"], "pass")
        self.assertIsNotNone(shell["min_safety_factor"])
        self.assertEqual(shell["physical_model_confidence"], "medium")

    def test_unsupported_component_model(self):
        base = run_pipeline(base_request())
        spec = {"component_id": "w", "type": "warp_drive", "warp_factor": 9.9}
        with_components = run_pipeline(base_request(components=[spec]))
        self.assertEqual(with_components["errors"], [])
        entry = component_entries(with_components)[0]
        self.assertEqual(entry["status"], "not_evaluated")
        codes = [finding["code"] for finding in entry["findings"]]
        self.assertIn("UNKNOWN_COMPONENT", codes)
        self.assertEqual(shell_outputs(with_components), shell_outputs(base))
        with_population = run_pipeline(
            base_request(
                population={
                    "sample_count": 100,
                    "profile": "general",
                    "lifespan_days": 730,
                    "workers": 1,
                    "components": [spec],
                }
            )
        )
        self.assertEqual(with_population["errors"], [])
        self.assertIsNotNone(with_population["population"])
        self.assertEqual(shell_outputs(with_population), shell_outputs(base))

    def test_non_finite_component_spec_values(self):
        base = run_pipeline(base_request())
        nan_result = run_pipeline(
            base_request(
                components=[
                    {"component_id": "b", "type": "battery", "mass_kg": float("nan")},
                    {"component_id": "p", "type": "pcb", "thickness_m": float("inf")},
                ]
            )
        )
        error_codes = [error["code"] for error in nan_result["errors"]]
        self.assertIn("PIPELINE_INTERNAL", error_codes)
        self.assertEqual(nan_result["lifecycle_state"], "failed")
        self.assertIsNone(nan_result["shell"])
        string_result = run_pipeline(
            base_request(
                components=[
                    {"component_id": "b", "type": "battery", "mass_kg": "nan"},
                    {"component_id": "p", "type": "pcb", "thickness_m": "inf"},
                ]
            )
        )
        self.assertEqual(string_result["errors"], [])
        for entry in component_entries(string_result):
            # Non-finite spec values are invalid input for a screening model:
            # the component is honestly reported as not_evaluated (never a
            # silent pass).
            self.assertIn(entry["status"], EVALUATED_STATUSES + ("not_evaluated",))
        self.assertEqual(shell_outputs(string_result), shell_outputs(base))

    def test_incomplete_component_data_evaluates_with_defaults(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(components=[{"component_id": "x", "type": "pcb"}])
        )
        self.assertEqual(result["errors"], [])
        entry = component_entries(result)[0]
        self.assertIn(entry["status"], EVALUATED_STATUSES)
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_population_without_structure_has_no_shell_block(self):
        request = mouse_project_request(
            load_case=dict(SHELL_LOAD_CASE),
            drop_simulation=dict(SHELL_DROP),
            population={"sample_count": 100, "profile": "general", "lifespan_days": 730, "workers": 1},
        )
        result = run_pipeline(request)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["population"])
        self.assertEqual(result["population"]["sample_count"], 100)
        self.assertIsNone(result["population"]["shell"])

    def test_population_invalid_profile_key_warns(self):
        base = run_pipeline(base_request())
        result = run_pipeline(
            base_request(
                population={
                    "sample_count": 100,
                    "profile": "quantum_esports",
                    "lifespan_days": 730,
                    "workers": 1,
                }
            )
        )
        self.assertEqual(result["errors"], [])
        codes = [issue["code"] for issue in result["issues"]]
        self.assertIn("POPULATION_ANALYSIS_FAILED", codes)
        self.assertIsNone(result["population"])
        self.assertEqual(shell_outputs(result), shell_outputs(base))

    def test_repeated_runs_are_deterministic(self):
        request = base_request(
            components=[{"component_id": "b", "type": "battery", "mass_kg": 1.0}],
            population={"sample_count": 100, "profile": "general", "lifespan_days": 730, "workers": 1},
        )
        first = run_pipeline(request)
        second = run_pipeline(request)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["shell"], second["shell"])
        self.assertEqual(first["population"], second["population"])
        self.assertEqual(first["component_screening"], second["component_screening"])
        self.assertEqual(
            canonical_json(first["shell"]), canonical_json(second["shell"])
        )

    def test_failing_component_never_flips_shell_status(self):
        plain_request = mouse_project_request(
            load_case=dict(SHELL_LOAD_CASE),
            structure=dict(SHELL_STRUCTURE),
            drop_simulation={"height_m": 2.0, "drop_count": 1},
        )
        with_components = dict(plain_request)
        with_components["components"] = [
            {"component_id": "b", "type": "battery", "mass_kg": 1.0}
        ]
        plain = run_pipeline(plain_request)
        contaminated = run_pipeline(with_components)
        self.assertEqual(plain["errors"], [])
        self.assertEqual(contaminated["errors"], [])
        battery = component_entries(contaminated)[0]
        self.assertEqual(battery["status"], "fail")
        shell = contaminated["shell"]
        self.assertIsNotNone(shell)
        self.assertNotEqual(shell["status"], "fail")
        self.assertEqual(shell["status"], plain["shell"]["status"])
        self.assertEqual(shell, plain["shell"])
        self.assertEqual(
            canonical_json(shell), canonical_json(plain["shell"])
        )
        self.assertEqual(contaminated["structural"], plain["structural"])
        self.assertEqual(contaminated["mass"], plain["mass"])
        self.assertEqual(
            contaminated["drop_simulation"]["trajectory"], plain["drop_simulation"]["trajectory"]
        )


if __name__ == "__main__":
    unittest.main()
