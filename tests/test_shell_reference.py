"""Nominal shell validation reference case — the physics regression baseline.

Re-runs the canonical reference request (reference/shell_validation_reference.json)
and asserts every recorded output stays within its documented band.  A future
physics change that moves any of these values (mass, CoM, inertia, structural
response, drop dynamics, k-sweep forces) fails here on purpose: the change must
be justified against the reference before the model is trusted.

The bands are tight (0.5-1%) but not noise-frozen: they are regression bands,
not physical validation claims.
"""

import json
import math
import os
import unittest

from mouse_sim.pipeline import run_pipeline

REFERENCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reference",
    "shell_validation_reference.json",
)


def _within(value, expected, relative):
    if expected == 0.0:
        return abs(value) <= 1e-12
    return abs(value - expected) / abs(expected) <= relative


class ShellReferenceCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(REFERENCE_PATH, encoding="utf-8") as stream:
            cls.reference = json.load(stream)
        cls.result = run_pipeline(cls.reference["request"])
        cls.bands = cls.reference["bands"]
        cls.relative = cls.bands["relative"]

    def test_reference_run_is_clean(self):
        self.assertEqual(self.result["errors"], [])

    def test_mass_unchanged(self):
        mass = self.result["mass"]
        expected = self.reference["expected"]
        self.assertEqual(mass["mass_status"], expected["mass_status"])
        self.assertTrue(_within(mass["mass_kg"], expected["mass_kg"], self.relative))
        for axis in range(3):
            self.assertTrue(
                _within(mass["center_of_mass_m"][axis], expected["com_m"][axis], self.relative),
                "CoM axis {}".format(axis),
            )
        for row, expected_row in zip(mass["inertia_tensor_kg_m2"], expected["inertia_kg_m2"]):
            for value, expected_value in zip(row, expected_row):
                self.assertTrue(_within(value, expected_value, self.relative))

    def test_structural_response_unchanged(self):
        response = self.result["structural"]["response"]
        expected = self.reference["expected"]["structural"]
        self.assertTrue(_within(response["safety_factor"], expected["safety_factor"], self.relative))
        self.assertTrue(_within(response["max_stress_pa"], expected["peak_stress_pa"], self.relative))
        self.assertTrue(
            _within(response["max_displacement_m"], expected["max_displacement_m"], self.relative)
        )

    def test_drop_dynamics_unchanged(self):
        model = self.result["drop_simulation"]["model"]
        drops = self.result["drop_simulation"]["drops"]
        peak = self.result["drop_simulation"]["peak"]
        expected = self.reference["expected"]["drop"]
        # Model constants are asserted exact (they are configuration pins).
        self.assertEqual(model["gravity_m_s2"], expected["gravity_m_s2"])
        self.assertEqual(model["restitution"], expected["restitution"])
        self.assertEqual(model["friction"], expected["friction"])
        self.assertEqual(model["timestep_s"], expected["timestep_s"])
        self.assertEqual(model["orientation_quaternion_wxyz"], expected["orientation_quaternion_wxyz"])
        # Computed quantities stay within the relative band.
        self.assertTrue(_within(model["mass_kg"], expected["mass_kg"], self.relative))
        self.assertTrue(_within(peak["impact_speed_m_s"], expected["impact_speed_m_s"], self.relative))
        self.assertTrue(
            _within(drops[0]["energy"]["release_j"], expected["release_j"], self.relative)
        )
        self.assertTrue(
            abs(drops[0]["settled_s"] - expected["settled_s"])
            <= self.bands["settled_s_absolute_tolerance"]
        )

    def test_peak_force_estimate_unchanged(self):
        estimate = self.result["drop_simulation"]["peak_force_estimate"]
        expected = self.reference["expected"]["peak_force_estimate"]
        for key in ("mass_kg", "restitution", "energy_j", "impact_speed_m_s", "contact_stiffness_n_per_m"):
            self.assertTrue(_within(estimate[key], expected[key], self.relative), key)

    def test_contact_stiffness_sweep_unchanged(self):
        rows = self.result["shell"]["validation"]["contact_stiffness_sweep"]["rows"]
        expected_forces = self.reference["expected"]["sweep_peak_force_n"]
        for row, expected in zip(rows, expected_forces):
            self.assertTrue(
                _within(row["peak_force_n"], expected, self.bands["sweep_force_relative"]),
                "k={}".format(row["contact_stiffness_n_per_m"]),
            )

    def test_sensitivity_top_parameters_stable(self):
        top = self.result["shell"]["validation"]["sensitivity"]["top_parameters"]
        expected = self.reference["expected"]["sensitivity_top_three"]
        # Exact-order assertion: the documented top-three must be the first
        # three ranked parameters (membership-only checks hid the mass/E
        # ordering drift; the baseline's ranking is authoritative).
        self.assertEqual(list(top[:3]), list(expected))


if __name__ == "__main__":
    unittest.main()
