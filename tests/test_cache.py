import json
import tempfile
import unittest

from mouse_sim import cache_key
from mouse_sim.cache import ArtifactCache, cache_key_for_inputs


class ArtifactCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = ArtifactCache(self.directory.name)

    def test_store_load_round_trip_returns_payload(self):
        payload = {"run_id": "abc", "mass_kg": 0.287, "tags": ["a", "b"]}
        self.cache.store("roundtrip", payload)
        self.assertEqual(self.cache.load("roundtrip"), payload)

    def test_corrupted_payload_file_returns_none(self):
        key = "corrupt"
        self.cache.store(key, {"value": 1})
        with self.cache.path_for(key).open("w", encoding="utf-8") as stream:
            stream.write("{ not valid json")
        self.assertIsNone(self.cache.load(key))

    def test_different_inputs_produce_different_keys(self):
        first = cache_key_for_inputs({"mode": "exploration", "objects": []})
        second = cache_key_for_inputs({"mode": "qualification", "objects": []})
        self.assertNotEqual(first, second)

    def test_same_inputs_produce_same_key(self):
        inputs = {"mode": "exploration", "objects": [{"id": "a", "size": [1, 2, 3]}]}
        self.assertEqual(cache_key_for_inputs(inputs), cache_key_for_inputs(inputs))

    def test_digest_verification_rejects_tampered_payload(self):
        key = "tamper"
        self.cache.store(key, {"value": 1, "nested": {"x": 2}})
        path = self.cache.path_for(key)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["value"] = 999
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertIsNone(self.cache.load(key))

    def test_contains_true_after_store_false_for_unknown(self):
        key = "known"
        self.cache.store(key, {"value": 1})
        self.assertTrue(self.cache.contains(key))
        self.assertFalse(self.cache.contains("missing"))
        self.assertTrue(self.cache.path_for(key).exists())


class CacheKeyTests(unittest.TestCase):
    def test_cache_key_for_inputs_includes_engine_version(self):
        plain = cache_key({"inputs": {"a": 1}})
        wrapped = cache_key_for_inputs({"a": 1})
        self.assertNotEqual(plain, wrapped)


if __name__ == "__main__":
    unittest.main()


class ArtifactCacheRobustnessTests(unittest.TestCase):
    def test_truncated_stored_file_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ArtifactCache(directory)
            key = "truncated"
            cache.store(key, {"value": [1, 2, 3], "nested": {"x": 1}})
            path = cache.path_for(key)
            raw = path.read_text(encoding="utf-8")
            path.write_text(raw[: len(raw) // 2], encoding="utf-8")
            self.assertIsNone(cache.load(key))
            self.assertTrue(cache.contains(key))

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ArtifactCache(directory)
            self.assertIsNone(cache.load("never-stored"))
