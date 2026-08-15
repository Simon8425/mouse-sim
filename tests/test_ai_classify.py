"""Tests for mouse_sim.ai_classify — descriptors, renderer, client, consensus, cache."""
import json
import os
import struct
import unittest
import zlib
from pathlib import Path

try:
    import pytest
except ImportError:  # pragma: no cover - stdlib-only `-S` unittest discovery
    # This module is pytest-first (plain classes + fixtures).  Under the
    # documented `python3 -S -m unittest discover` command site-packages are
    # excluded, so pytest is unavailable: surface an explicit skip instead of
    # a module-level ImportError that fails the whole discovery run.  The
    # pytest-style classes below are plain classes (no unittest.TestCase), so
    # unittest discovery ignores them; pytest remains the runner for them.
    pytest = None

    class AiClassifyRequiresPytest(unittest.TestCase):
        def test_pytest_required(self):
            self.skipTest(
                "test_ai_classify.py requires pytest (run with `python3 -m pytest tests/test_ai_classify.py`)"
            )

from mouse_sim import ai_classify
from mouse_sim.ai_classify import (
    ClassificationCache,
    build_system_prompt,
    build_user_prompt,
    call_openrouter,
    classify_parts,
    is_enabled,
    merge_classification,
    normalize_part_name,
    parse_classify_response,
    part_descriptors,
    part_hash,
    render_part_thumbnail,
)

BOX_GEOMETRY = {
    "type": "mesh",
    "vertices": [
        [0.0, 0.0, 0.0], [0.06, 0.0, 0.0], [0.06, 0.04, 0.0], [0.0, 0.04, 0.0],
        [0.0, 0.0, 0.01], [0.06, 0.0, 0.01], [0.06, 0.04, 0.01], [0.0, 0.04, 0.01],
    ],
    "triangles": [
        [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
        [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
        [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
    ],
}


class TestDescriptors:
    def test_box_descriptors(self):
        d = part_descriptors(BOX_GEOMETRY, "TD011-TOP-C_2")
        # "TD011-TOP-C_2": the trailing "_2" is an assembly suffix; the
        # single-letter leading token rule does not apply ("td011" is a
        # product code, not a prefix). "c" remains a trailing token.
        assert d["name_normalized"] == "td011_top_c"
        assert d["geometry_type"] == "mesh"
        assert d["vertex_count"] == 8
        assert d["size_m"] == [0.06, 0.04, 0.01]
        assert d["max_dim_m"] == 0.06
        assert d["flatness"] == pytest.approx(0.01 / 0.06, rel=1e-3)
        assert d["volume_m3"] == pytest.approx(0.06 * 0.04 * 0.01, rel=1e-3)
        assert d["surface_area_m2"] == pytest.approx(
            2 * (0.06 * 0.04 + 0.06 * 0.01 + 0.04 * 0.01), rel=1e-3
        )
        assert 0 < d["footprint_coverage"] <= 1

    def test_name_normalization(self):
        # "C-WHEEL" is the product code prefix "C" + "WHEEL"; the single-letter
        # prefix token is dropped ("C-" is a CAD prefix).
        assert normalize_part_name("C-WHEEL-01FK_1") == "wheel_01fk"
        # Middle single-letter tokens are kept (product codes like "KEY-B").
        assert normalize_part_name("TD011-KEY-B_2_ASM") == "td011_key_b"
        assert normalize_part_name("MANIFOLD_SOLID_BREP_7") == "manifold_solid_brep"
        assert normalize_part_name("part (copy)") == "part"
        assert normalize_part_name(None) == ""

    def test_empty_geometry_is_safe(self):
        d = part_descriptors({"type": "mesh", "vertices": [], "triangles": []})
        assert d["vertex_count"] == 0
        assert d["max_dim_m"] == 0


class TestThumbnail:
    def test_renders_valid_png(self):
        png = render_part_thumbnail(BOX_GEOMETRY["vertices"], BOX_GEOMETRY["triangles"])
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        # Decode IHDR.
        width, height = struct.unpack(">II", png[16:24])
        assert width == 192 * 3
        assert height == 192
        # IDAT present and decompressible.
        idat_start = png.index(b"IDAT")
        length = struct.unpack(">I", png[idat_start - 4 : idat_start])[0]
        data = png[idat_start + 4 : idat_start + 4 + length]
        raw = zlib.decompress(data)
        assert len(raw) == height * (1 + width * 3)

    def test_empty_mesh_blank_png(self):
        png = render_part_thumbnail([], [])
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


class TestOpenRouterClient:
    def test_call_openrouter_success(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, key, timeout):
            captured["url"] = url
            captured["payload"] = payload
            captured["key"] = key
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "object_id": "p1",
                                    "component_type": "scroll_wheel",
                                    "confidence": 0.9,
                                    "reasons": ["wheel shape"],
                                }
                            )
                        }
                    }
                ]
            }

        monkeypatch.setattr(ai_classify, "_http_post_json", fake_post)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("MOUSE_SIM_AI_ENABLED", "1")
        parts = [
            {
                "object_id": "p1",
                "name": "wheel",
                "thumbnail_png": render_part_thumbnail(BOX_GEOMETRY["vertices"], BOX_GEOMETRY["triangles"]),
                "descriptor": part_descriptors(BOX_GEOMETRY, "wheel"),
            }
        ]
        result = call_openrouter(parts, api_key_value="test-key")
        assert len(result) == 1
        assert result[0]["component_type"] == "scroll_wheel"
        assert captured["key"] == "test-key"
        assert captured["payload"]["temperature"] == 0

    def test_call_openrouter_retries_then_fails(self, monkeypatch):
        calls = {"count": 0}

        def fail(*args):
            calls["count"] += 1
            raise TimeoutError("boom")

        monkeypatch.setattr(ai_classify, "_http_post_json", fail)
        result = call_openrouter([], api_key_value="k", retries=1)
        assert result == []

    def test_missing_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = call_openrouter([], api_key_value=None)
        assert result == []


class TestResponseParser:
    def test_parses_strict_json(self):
        text = '{"object_id": "p1", "component_type": "pcb", "confidence": 0.88, "reasons": ["flat plate"]}'
        parsed = parse_classify_response(text)
        assert parsed["p1"]["component_type"] == "pcb"
        assert parsed["p1"]["confidence"] == 0.88

    def test_parses_wrapped_list(self):
        text = json.dumps(
            {
                "parts": [
                    {"object_id": "a", "component_type": "battery", "confidence": 0.7},
                    {"object_id": "b", "component_type": "top_shell", "confidence": 0.95},
                ]
            }
        )
        parsed = parse_classify_response(text)
        assert parsed["a"]["component_type"] == "battery"
        assert parsed["b"]["component_type"] == "top_shell"

    def test_extracts_embedded_json(self):
        parsed = parse_classify_response(
            'Sure! Here is the result: {"object_id": "x", "component_type": "encoder", "confidence": 0.6, "reasons": []} hope that helps'
        )
        assert parsed["x"]["component_type"] == "encoder"

    def test_unknown_label_becomes_unresolved(self):
        parsed = parse_classify_response(
            '{"object_id": "x", "component_type": "quantum_flux_capacitor", "confidence": 0.99}'
        )
        assert parsed["x"]["component_type"] == "unresolved"

    def test_garbage_returns_empty(self):
        assert parse_classify_response("not json at all") == {}
        assert parse_classify_response("") == {}

    def test_degenerate_llm_shapes_do_not_crash(self):
        # A bare string / number / dict-of-strings / list-of-strings must not
        # raise; non-object entries are skipped.
        assert parse_classify_response('"just a string"') == {}
        assert parse_classify_response("42") == {}
        assert parse_classify_response('{"part-0": "top_shell"}') == {}
        assert parse_classify_response('["top_shell", "pcb"]') == {}
        assert parse_classify_response('{"parts": ["top_shell", "pcb"]}') == {}

    def test_dict_of_dicts_gets_key_as_object_id(self):
        parsed = parse_classify_response(
            '{"p1": {"component_type": "battery", "confidence": 0.8}}'
        )
        assert parsed["p1"]["component_type"] == "battery"
        assert parsed["p1"]["confidence"] == 0.8


class TestConsensus:
    def test_request_wins(self):
        rule = {"component_type": "pcb", "confidence": 0.9}
        request = {"component_type": "battery", "confidence": 0.5}
        merged = merge_classification("p1", rule, None, request)
        assert merged["component_type"] == "battery"
        assert merged["source"] == "user"
        assert merged["needs_review"] is False

    def test_ai_agree_wins(self):
        rule = {"component_type": "scroll_wheel", "confidence": 0.6}
        ai = {"component_type": "scroll_wheel", "confidence": 0.9, "reasons": ["wheel shape"]}
        merged = merge_classification("p1", rule, ai, None)
        assert merged["component_type"] == "scroll_wheel"
        assert merged["source"] == "openrouter_vision"
        assert merged["confidence"] == pytest.approx(0.9)

    def test_ai_disagree_falls_back_to_rule(self):
        rule = {"component_type": "pcb", "confidence": 0.7}
        ai = {"component_type": "battery", "confidence": 0.95}
        merged = merge_classification("p1", rule, ai, None)
        assert merged["component_type"] == "pcb"
        assert merged["source"] == "heuristic"
        assert merged["needs_review"] is True
        # Plan matrix: min(ai, rule) × 0.6.
        assert merged["confidence"] == pytest.approx(min(0.7, 0.95) * 0.6, rel=1e-6)

    def test_low_confidence_ai_used_with_review(self):
        rule = {"component_type": "unresolved", "confidence": 0.2}
        ai = {"component_type": "sensor", "confidence": 0.5}
        merged = merge_classification("p1", rule, ai, None)
        assert merged["component_type"] == "sensor"
        assert merged["needs_review"] is True

    def test_no_ai_falls_back_to_rule(self):
        rule = {"component_type": "top_shell", "confidence": 0.95}
        merged = merge_classification("p1", rule, None, None)
        assert merged["component_type"] == "top_shell"
        assert merged["source"] == "heuristic"


class TestCache:
    def test_round_trip_and_prune(self, tmp_path):
        cache = ClassificationCache(tmp_path, capacity=1)
        key = "abc123"
        assert cache.get(key) is None
        cache.put(key, {"component_type": "pcb", "confidence": 0.9})
        assert cache.get(key)["component_type"] == "pcb"
        cache.put("def456", {"component_type": "battery", "confidence": 0.8})
        cache.prune()
        # Capacity 1 keeps only the newest entry (def456).
        assert cache.get("def456") is not None
        assert cache.get("abc123") is None

    def test_corrupt_entry_is_a_miss(self, tmp_path):
        cache = ClassificationCache(tmp_path)
        cache.put("good", {"component_type": "pcb", "confidence": 0.9})
        (tmp_path / "junk.json").write_text("{not valid json", encoding="utf-8")
        (tmp_path / "badconf.json").write_text(
            json.dumps({"component_type": "pcb", "confidence": 7.0}), encoding="utf-8"
        )
        (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
        assert cache.get("junk") is None
        assert cache.get("badconf") is None
        assert cache.get("empty") is None
        assert cache.get("good")["component_type"] == "pcb"


class TestClassifyParts:
    def test_user_request_skips_ai(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("MOUSE_SIM_AI_ENABLED", "1")
        called = {"count": 0}

        def fake_call(parts, **kwargs):
            called["count"] += 1
            return [{"component_type": "battery", "confidence": 0.9}]

        monkeypatch.setattr(ai_classify, "call_openrouter", fake_call)
        result = classify_parts(
            [
                {
                    "object_id": "p1",
                    "name": "wheel",
                    "geometry": BOX_GEOMETRY,
                    "rule": {"component_type": "scroll_wheel", "confidence": 0.95},
                    "request": {"component_type": "top_shell", "confidence": 0.99},
                }
            ],
            cache=ClassificationCache(tmp_path),
        )
        assert called["count"] == 0
        assert result[0]["component_type"] == "top_shell"
        assert result[0]["source"] == "user"

    def test_disabled_ai_falls_back_to_rule(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("MOUSE_SIM_AI_ENABLED", raising=False)
        result = classify_parts(
            [
                {
                    "object_id": "p1",
                    "name": "wheel_2",
                    "geometry": BOX_GEOMETRY,
                    "rule": {"component_type": "scroll_wheel", "confidence": 0.95},
                }
            ],
            cache=ClassificationCache(tmp_path),
        )
        assert result[0]["component_type"] == "scroll_wheel"
        assert result[0]["source"] == "heuristic"

    def test_cache_hit_skips_ai(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("MOUSE_SIM_AI_ENABLED", "1")
        called = {"count": 0}

        def fake_call(parts, **kwargs):
            called["count"] += 1
            # Order-preserving: one entry per part, carrying object_id.
            return [
                {"object_id": p.get("object_id"), "component_type": "scroll_wheel", "confidence": 0.9, "reasons": []}
                for p in parts
            ]

        monkeypatch.setattr(ai_classify, "call_openrouter", fake_call)
        cache = ClassificationCache(tmp_path)
        parts = [
            {
                "object_id": "p1",
                "name": "wheel",
                "geometry": BOX_GEOMETRY,
                "rule": {"component_type": "scroll_wheel", "confidence": 0.5},
            }
        ]
        classify_parts(parts, cache=cache)
        assert called["count"] == 1
        # Second run: cached, no AI call.
        classify_parts(parts, cache=cache)
        assert called["count"] == 1

    def test_partial_batch_results_map_to_correct_parts(self, tmp_path, monkeypatch):
        """A batch where the model omits a middle part must not shift the
        remaining classifications onto the wrong parts."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("MOUSE_SIM_AI_ENABLED", "1")

        def fake_call(parts, **kwargs):
            # Model returns only p2 and p3, dropping p1 entirely.
            return [
                {"object_id": "p2", "component_type": "pcb", "confidence": 0.9},
                {"object_id": "p3", "component_type": "battery", "confidence": 0.8},
            ]

        monkeypatch.setattr(ai_classify, "call_openrouter", fake_call)
        result = classify_parts(
            [
                {
                    "object_id": "p1",
                    "name": "top_cover",
                    "geometry": BOX_GEOMETRY,
                    "rule": {"component_type": "top_shell", "confidence": 0.95},
                },
                {
                    "object_id": "p2",
                    "name": "board",
                    "geometry": BOX_GEOMETRY,
                    "rule": {"component_type": "unresolved", "confidence": 0.0},
                },
                {
                    "object_id": "p3",
                    "name": "cell",
                    "geometry": BOX_GEOMETRY,
                    "rule": {"component_type": "unresolved", "confidence": 0.0},
                },
            ],
            cache=ClassificationCache(tmp_path),
        )
        by_id = {item["object_id"]: item for item in result}
        # p1 has no AI result: falls back to its rule (top_shell).
        assert by_id["p1"]["component_type"] == "top_shell"
        assert by_id["p1"]["source"] == "heuristic"
        # p2 and p3 get their OWN AI labels, not each other's.
        assert by_id["p2"]["component_type"] == "pcb"
        assert by_id["p3"]["component_type"] == "battery"

    def test_model_renames_object_id_falls_back_to_rule(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("MOUSE_SIM_AI_ENABLED", "1")

        def fake_call(parts, **kwargs):
            # Model returns an unknown object_id; nothing should match.
            return [{"object_id": "totally_different", "component_type": "pcb", "confidence": 0.9}]

        monkeypatch.setattr(ai_classify, "call_openrouter", fake_call)
        result = classify_parts(
            [
                {
                    "object_id": "p1",
                    "name": "top_cover",
                    "geometry": BOX_GEOMETRY,
                    "rule": {"component_type": "top_shell", "confidence": 0.95},
                }
            ],
            cache=ClassificationCache(tmp_path),
        )
        assert result[0]["object_id"] == "p1"
        assert result[0]["component_type"] == "top_shell"
        assert result[0]["source"] == "heuristic"


class TestPartHash:
    def test_changes_with_descriptor(self):
        a = part_hash({"a": 1}, b"pngdata")
        b = part_hash({"a": 2}, b"pngdata")
        assert a != b
