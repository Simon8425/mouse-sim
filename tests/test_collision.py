import json
import unittest

from mouse_sim import (
    CLEARANCE_NOT_CERTIFIED,
    STATUS_CLEAR,
    STATUS_CONTACT,
    STATUS_ESTIMATE,
    STATUS_INTERFERENCE,
    STATUS_UNKNOWN,
    Box,
    TriangleMesh,
    clamp,
    clearance_between,
    pair_clearance_matrix,
    sign,
)


def unit_box(offset=(0.0, 0.0, 0.0)):
    return Box((1.0, 1.0, 1.0), transform={"translation": offset})


def open_triangle_mesh():
    return TriangleMesh([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [(0, 1, 2)])


class HelperMathTests(unittest.TestCase):
    def test_clamp_and_sign(self):
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-1, 0, 3), 0)
        self.assertEqual(clamp(2, 0, 3), 2)
        self.assertEqual(sign(-2.5), -1.0)
        self.assertEqual(sign(0.0), 0.0)
        self.assertEqual(sign(4.0), 1.0)


class ClearanceBetweenTests(unittest.TestCase):
    def test_separated_boxes_are_clear(self):
        result = clearance_between(unit_box(), unit_box((2.0, 0.0, 0.0)))
        self.assertEqual(result.status, STATUS_CLEAR)
        self.assertAlmostEqual(result.nominal_clearance_m, 1.0)
        self.assertAlmostEqual(result.worst_case_clearance_m, 1.0)
        self.assertFalse(result.interference)
        self.assertFalse(result.touch)
        self.assertEqual(result.method, "aabb_estimate")

    def test_touching_boxes_are_contact(self):
        result = clearance_between(unit_box(), unit_box((1.0, 0.0, 0.0)))
        self.assertEqual(result.status, STATUS_CONTACT)
        self.assertAlmostEqual(result.nominal_clearance_m, 0.0)
        self.assertTrue(result.touch)

    def test_intersecting_boxes_are_interference(self):
        result = clearance_between(unit_box(), unit_box((0.5, 0.0, 0.0)))
        self.assertEqual(result.status, STATUS_INTERFERENCE)
        self.assertAlmostEqual(result.nominal_clearance_m, -0.5)
        self.assertTrue(result.interference)

    def test_tolerance_crossing_flips_to_interference(self):
        result = clearance_between(unit_box(), unit_box((2.0, 0.0, 0.0)), tolerance_a_m=0.6, tolerance_b_m=0.6)
        self.assertEqual(result.status, STATUS_INTERFERENCE)
        self.assertAlmostEqual(result.worst_case_clearance_m, -0.2)
        self.assertTrue(result.interference)
        self.assertIn("tolerance_applied", result.flags)

    def test_task_scenario_gap_with_tolerances(self):
        first = unit_box()
        second = unit_box((1.01, 0.0, 0.0))
        result = clearance_between(first, second, tolerance_a_m=0.006, tolerance_b_m=0.006)
        self.assertAlmostEqual(result.nominal_clearance_m, 0.01, places=12)
        self.assertAlmostEqual(result.worst_case_clearance_m, -0.002, places=12)
        self.assertTrue(result.interference)
        self.assertEqual(result.status, STATUS_INTERFERENCE)

    def test_open_mesh_is_unknown_and_uncertified(self):
        result = clearance_between(open_triangle_mesh(), unit_box())
        self.assertEqual(result.status, STATUS_UNKNOWN)
        self.assertIn(CLEARANCE_NOT_CERTIFIED, result.flags)
        self.assertTrue(result.diagnostics)

    def test_plain_dict_input_is_accepted(self):
        first = {"type": "box", "size": [1.0, 1.0, 1.0]}
        second = {"geometry": unit_box((3.0, 0.0, 0.0))}
        result = clearance_between(first, second)
        self.assertEqual(result.status, STATUS_CLEAR)
        self.assertAlmostEqual(result.nominal_clearance_m, 2.0)

    def test_pair_rule_consumes_gap_to_estimate(self):
        rule = {"tolerance_a_m": 0.5, "tolerance_b_m": 0.5, "label": "slip fit"}
        result = clearance_between(unit_box(), unit_box((2.0, 0.0, 0.0)), pair_rule=rule)
        self.assertEqual(result.status, STATUS_ESTIMATE)
        self.assertAlmostEqual(result.worst_case_clearance_m, 0.0)
        self.assertIn("pair_rule_applied", result.flags)
        self.assertIn("stackup_consumed_gap", result.flags)

    def test_to_dict_is_json_friendly(self):
        data = clearance_between(unit_box(), unit_box((2.0, 0.0, 0.0))).to_dict()
        json.dumps(data)
        self.assertEqual(data["method"], "aabb_estimate")
        self.assertEqual(data["status"], STATUS_CLEAR)


class PairClearanceMatrixTests(unittest.TestCase):
    def test_records_sorted_by_key_and_json_friendly(self):
        objects = {"zeta": unit_box(), "alpha": unit_box((2.0, 0.0, 0.0)), "mid": unit_box((1.0, 0.0, 0.0))}
        matrix = pair_clearance_matrix(objects)
        self.assertEqual(matrix["object_names"], ["alpha", "mid", "zeta"])
        self.assertEqual(matrix["count"], 3)
        self.assertEqual(matrix["units"], "m")
        pairs = [tuple(record["pair"]) for record in matrix["pairs"]]
        self.assertEqual(pairs, [("alpha", "mid"), ("alpha", "zeta"), ("mid", "zeta")])
        json.dumps(matrix)
        self.assertEqual(matrix, pair_clearance_matrix(objects))

    def test_pair_rules_by_sorted_key(self):
        objects = {"a": unit_box(), "b": unit_box((2.0, 0.0, 0.0))}
        rules = {("a", "b"): {"tolerance_a_m": 0.6, "tolerance_b_m": 0.6}}
        record = pair_clearance_matrix(objects, pair_rules=rules)["pairs"][0]
        self.assertEqual(record["status"], STATUS_INTERFERENCE)
        self.assertEqual(record["a"], "a")
        self.assertEqual(record["b"], "b")


if __name__ == "__main__":
    unittest.main()
