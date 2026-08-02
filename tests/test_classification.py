import unittest

from mouse_sim import Box, TriangleMesh, classify_objects


class NameSynonymClassificationTests(unittest.TestCase):
    def assert_type(self, name, expected):
        item = classify_objects({name: Box((1, 1, 1))})[name]
        self.assertEqual(item.component_type, expected)
        self.assertFalse(item.unresolved)
        self.assertGreater(item.confidence, 0.5)
        self.assertTrue(item.reasons)
        self.assertFalse(item.semantic_separation_claimed)
        return item

    def test_synonym_mapping_for_mouse_component_names(self):
        cases = {
            "wheel": "wheel",
            "scroll": "wheel",
            "scroll_wheel": "wheel",
            "pcb": "pcb",
            "board": "pcb",
            "battery": "battery",
            "lipo": "battery",
            "shell_top": "shell_top",
            "top_shell": "shell_top",
            "shell_bottom": "shell_bottom",
            "bottom_cover": "shell_bottom",
            "skate": "skate",
            "mouse_foot": "skate",
            "screw": "screw",
            "bolt": "screw",
            "button": "button",
            "microswitch": "button",
        }
        for name, expected in cases.items():
            item = self.assert_type(name, expected)
            self.assertIn(expected, item.reasons[0])

    def test_indexed_component_ids_match_synonyms(self):
        self.assertEqual(
            classify_objects({"wheel_2": Box((1, 1, 1))})["wheel_2"].component_type,
            "wheel",
        )
        self.assertEqual(
            classify_objects({"button-3": Box((1, 1, 1))})["button-3"].component_type,
            "button",
        )

    def test_unknown_name_preserves_geometric_fallback(self):
        item = classify_objects({"widget": Box((1, 1, 1))})["widget"]
        self.assertEqual(item.component_type, "solid")
        self.assertFalse(item.unresolved)

    def test_unknown_name_with_open_mesh_stays_unresolved(self):
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        item = classify_objects({"widget": mesh})["widget"]
        self.assertEqual(item.component_type, "surface")
        self.assertTrue(item.unresolved)

    def test_fused_object_never_claims_semantic_separation(self):
        item = classify_objects({"scroll": {"geometry": Box((1, 1, 1)), "fused": True}})["scroll"]
        self.assertTrue(item.unresolved)
        self.assertTrue(item.fused)
        self.assertFalse(item.semantic_separation_claimed)
        self.assertLessEqual(item.confidence, 0.2)
        self.assertTrue(any("semantic separation" in reason for reason in item.reasons))

    def test_record_name_is_used_when_id_is_generic(self):
        item = classify_objects({"object-7": {"name": "scroll", "geometry": Box((1, 1, 1))}})["object-7"]
        self.assertEqual(item.component_type, "wheel")


if __name__ == "__main__":
    unittest.main()
