"""End-to-end tests for the mouse_sim web API adapter."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlsplit

from mouse_sim.canonical import canonical_json
from mouse_sim.geometry import TriangleMesh
from mouse_sim.importers import GeometryLoadResult, ImportDiagnostic
from mouse_sim.pipeline import run_pipeline
from mouse_sim.web_api import WebConfig, build_server, register_step_asset, slim_result_for_web

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "examples" / "mouse_baseline.json"

OBJ_SOURCE = "v 0 0 0\nv 10 0 0\nv 0 10 0\nf 1 2 3\n"
STL_SOURCE = (
    "solid sample\n"
    "facet normal 0 0 1\n"
    "outer loop\n"
    "vertex 0 0 0\n"
    "vertex 1 0 0\n"
    "vertex 0 1 0\n"
    "endloop\n"
    "endfacet\n"
    "endsolid sample\n"
)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def request(base_url, method, path, body=None, headers=None):
    """Perform one HTTP request and return ``(response, body_bytes)``."""
    parts = urlsplit(base_url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response, data
    finally:
        connection.close()


class WebApiTests(unittest.TestCase):
    def start_server(self, **overrides):
        """Start an in-process server on an ephemeral port and register teardown."""
        if "port" not in overrides:
            overrides["port"] = 0
        config = WebConfig(**overrides)
        server = build_server(config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address[:2]
        return server, "http://{}:{}".format(host, port)

    def post_json(self, base_url, path, payload, content_type="application/json"):
        body = json.dumps(payload).encode("utf-8")
        return request(base_url, "POST", path, body=body, headers={"Content-Type": content_type})

    def load_baseline(self):
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def test_health(self):
        server, base_url = self.start_server()
        response, data = request(base_url, "GET", "/api/health")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("application/json"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-health/1")
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["engine_version"], "0.1.0")
        for fmt in ("json", "obj", "stl", "step"):
            self.assertIn(fmt, payload["supported_formats"])
        self.assertTrue(payload["solver_capabilities"])
        self.assertFalse(payload["cache_active"])
        self.assertGreater(payload["max_json_bytes"], 0)
        self.assertGreater(payload["max_geometry_bytes"], 0)
        self.assertTrue(payload["deterministic"])
        self.assertEqual(payload["step_backend"], "auto")
        self.assertIn("step_kernel_available", payload)
        self.assertEqual(payload["advanced_step_backend"], "kernel")
        self.assertTrue(payload["advanced_step_uses_kernel"])
        self.assertNotIn("time", payload)
        self.assertFalse(any("timestamp" in key for key in payload))

    def test_health_cache_active_with_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            server, base_url = self.start_server(cache_dir=Path(directory))
            response, data = request(base_url, "GET", "/api/health")
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(data)["cache_active"])

    def test_health_step_kernel_probe_failure_is_structured_200(self):
        """The health endpoint must stay 200 with structured step fields even
        when the optional FreeCAD kernel probe itself raises."""
        server, base_url = self.start_server()
        with mock.patch(
            "mouse_sim.step_kernel.kernel_available",
            side_effect=RuntimeError("probe exploded"),
        ):
            response, data = request(base_url, "GET", "/api/health")
        self.assertEqual(response.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-health/1")
        self.assertIs(payload["step_kernel_available"], False)
        self.assertEqual(payload["step_kernel_backend"], "freecad-occt")
        self.assertEqual(payload["advanced_step_backend"], "kernel")
        self.assertTrue(payload["advanced_step_uses_kernel"])
        self.assertNotIn(b"probe exploded", data)
        self.assertNotIn(b"Traceback", data)

    def test_baseline(self):
        server, base_url = self.start_server()
        response, data = request(base_url, "GET", "/api/projects/baseline")
        self.assertEqual(response.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-baseline/1")
        self.assertEqual(payload["source"], "examples/mouse_baseline.json")
        self.assertEqual(payload["project"]["schema_id"], "gms.project/1")
        self.assertEqual(payload["project"]["units"], "mm")
        self.assertEqual(len(payload["project"]["objects"]), 9)

    def test_baseline_missing_examples_four_oh_four(self):
        with tempfile.TemporaryDirectory() as directory:
            server, base_url = self.start_server(project_root=Path(directory))
            response, data = request(base_url, "GET", "/api/projects/baseline")
            self.assertEqual(response.status, 404)
            payload = json.loads(data)
            self.assertEqual(payload["schema_id"], "gms.web-error/1")
            self.assertEqual(payload["error"]["code"], "E_NOT_FOUND")

    def test_materials(self):
        server, base_url = self.start_server()
        response, data = request(base_url, "GET", "/api/materials")
        self.assertEqual(response.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-material-catalog/1")
        self.assertEqual(payload["catalog_source"], "builtin")
        materials = payload["materials"]
        self.assertGreaterEqual(len(materials), 10)
        for entry in materials:
            for key in (
                "key",
                "name",
                "family",
                "density_kg_m3",
                "young_modulus_pa",
                "approval_state",
                "confidence",
                "source_type",
            ):
                self.assertIn(key, entry)
            self.assertEqual(entry["approval_state"], "draft")
        keys = [entry["key"] for entry in materials]
        self.assertEqual(keys, sorted(keys, key=str.casefold))

    def test_analyze_matches_direct_pipeline(self):
        document = self.load_baseline()
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"strict": False, "use_cache": True},
        }
        server, base_url = self.start_server()
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-analysis-response/1")
        self.assertEqual(payload["engine_version"], "0.1.0")
        direct = run_pipeline(dict(document, options={"strict": False}))
        self.assertEqual(payload["run_id"], direct["run_id"])
        self.assertEqual(
            canonical_json(payload["result"]),
            canonical_json(slim_result_for_web(direct)),
        )
        self.assertEqual(
            payload["result"]["manifest"]["manifest_hash"],
            direct["manifest"]["manifest_hash"],
        )
        self.assertEqual(payload["result"]["lifecycle_state"], direct["lifecycle_state"])
        self.assertEqual(payload["result"]["mass"]["mass_kg"], direct["mass"]["mass_kg"])
        self.assertIsNotNone(payload["result"]["mass"]["mass_kg"])
        self.assertEqual(
            payload["result"]["validation"]["status"], direct["validation"]["status"]
        )
        self.assertEqual(
            payload["result"]["structural"]["response"]["max_displacement_m"],
            direct["structural"]["response"]["max_displacement_m"],
        )
        self.assertEqual(
            payload["result"]["impact"]["result"]["peak_force_n"],
            direct["impact"]["result"]["peak_force_n"],
        )
        self.assertEqual(
            payload["result"]["qualification"]["evidence_disposition"], "exploration_only"
        )
        self.assertEqual(payload["result"]["errors"], [])
        self.assertTrue(payload["materials"])
        self.assertEqual(payload["materials"][0]["approval_state"], "draft")

    def test_analyze_manifest_stays_replayable(self):
        """W5-04: the web response must NOT slim the manifest inputs — a
        slimmed manifest no longer matched its recorded manifest_hash, so
        every web-served manifest was rejected by reproduce_from_manifest
        (replay impossible on the web path).  The served manifest is now
        byte-identical to the pipeline result's manifest."""
        document = self.load_baseline()
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"strict": False, "use_cache": True},
        }
        server, base_url = self.start_server()
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        manifest = payload["result"]["manifest"]
        self.assertIsInstance(manifest, dict)
        inputs = manifest["inputs"]
        self.assertIsInstance(inputs, dict)
        # The full objects snapshot must be present (not a count/sha summary).
        objects = inputs["objects"]
        self.assertIsInstance(objects, list)
        self.assertEqual(len(objects), len(document["objects"]))
        # The served manifest must replay on the web path.
        from mouse_sim.pipeline import reproduce_from_manifest

        replay = reproduce_from_manifest(manifest)
        self.assertTrue(replay["supported"], replay)

    def test_analyze_cache_reuse(self):
        document = self.load_baseline()
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"strict": False, "use_cache": True},
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            server, base_url = self.start_server(cache_dir=cache_dir)
            response, data = self.post_json(base_url, "/api/analyze", envelope)
            self.assertEqual(response.status, 200, data.decode("utf-8"))
            first = json.loads(data)
            response, data = self.post_json(base_url, "/api/analyze", envelope)
            self.assertEqual(response.status, 200, data.decode("utf-8"))
            second = json.loads(data)
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(canonical_json(first["result"]), canonical_json(second["result"]))
            response, data = request(base_url, "GET", "/api/health")
            self.assertTrue(json.loads(data)["cache_active"])
            self.assertEqual(len(list(cache_dir.glob("*.json"))), 1)

    def test_analyze_malformed_json_bad_request(self):
        server, base_url = self.start_server()
        response, data = request(
            base_url,
            "POST",
            "/api/analyze",
            body=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "E_PARSE")
        response, data = request(
            base_url,
            "POST",
            "/api/analyze",
            body=b"[1, 2]",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_INVALID_ENVELOPE")

    def test_analyze_invalid_envelope(self):
        document = self.load_baseline()
        server, base_url = self.start_server()
        extra = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "extra": 1,
        }
        response, data = self.post_json(base_url, "/api/analyze", extra)
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_INVALID_ENVELOPE")
        wrong = {"schema_id": "gms.web-wrong/1", "request": dict(document)}
        response, data = self.post_json(base_url, "/api/analyze", wrong)
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_INVALID_ENVELOPE")
        self.assertIn("gms.web-analysis-request/1", payload["error"]["message"])

    def test_analyze_rejects_non_finite(self):
        body = json.dumps(
            {
                "schema_id": "gms.web-analysis-request/1",
                "request": {"value": float("nan")},
            }
        ).encode("utf-8")
        server, base_url = self.start_server()
        response, data = request(
            base_url,
            "POST",
            "/api/analyze",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_NON_FINITE")

    def test_analyze_unsupported_artifacts(self):
        server, base_url = self.start_server()
        document = {"schema_id": "gms.project-document", "schema_version": 1}
        envelope = {"schema_id": "gms.web-analysis-request/1", "request": document}
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "UNSUPPORTED_ARTIFACT")
        reference_only = {"schema_id": "gms.project/1"}
        envelope = {"schema_id": "gms.web-analysis-request/1", "request": reference_only}
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "UNSUPPORTED_ARTIFACT")
        self.assertIn("no inline geometry", payload["error"]["message"])

    def test_analyze_invalid_options(self):
        document = self.load_baseline()
        server, base_url = self.start_server()
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"strict": "yes"},
        }
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_INVALID_ENVELOPE")
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"bogus": True},
        }
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_INVALID_ENVELOPE")

    def test_analyze_preserves_existing_request_options(self):
        document = self.load_baseline()
        options = {"min_thickness_m": 0.0005, "max_thickness_m": 0.05, "debug": True}
        request_doc = dict(document, options=dict(options))
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": request_doc,
            "options": {"strict": False, "use_cache": False},
        }
        server, base_url = self.start_server()
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        direct = run_pipeline(
            dict(request_doc, options={"min_thickness_m": 0.0005, "max_thickness_m": 0.05, "debug": True, "strict": False})
        )
        self.assertEqual(payload["run_id"], direct["run_id"])
        self.assertEqual(
            canonical_json(payload["result"]),
            canonical_json(slim_result_for_web(direct)),
        )

    def test_analyze_pipeline_validation_fail_returns_422(self):
        document = self.load_baseline()
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"strict": True, "use_cache": False},
        }
        server, base_url = self.start_server()
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 422, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "E_VALIDATION")
        self.assertNotIn("result", payload)

    def test_analyze_qualification_integrity_gates_surface(self):
        document = self.load_baseline()
        request_doc = dict(
            document,
            mode="qualification",
            load_case={
                "kind": "torque",
                "reviewed": True,
                "acceptance_requirement_refs": [{"id": "req-1"}],
            },
        )
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": request_doc,
            "options": {"strict": False, "use_cache": False},
        }
        server, base_url = self.start_server()
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        qualification = payload["result"]["qualification"]
        self.assertEqual(qualification["evidence_disposition"], "qualification_blocked")
        self.assertIn("ANALYSIS_VALIDITY", qualification["blocking_keys"])
        for key in (
            "integrity_gates",
            "requirement_evaluations",
            "convergence_evidence",
            "force_balance",
            "structural_validity",
        ):
            self.assertIn(key, qualification)
        gate_keys = {gate["key"] for gate in qualification["integrity_gates"]}
        self.assertEqual(
            gate_keys,
            {
                "ANALYSIS_VALIDITY",
                "IMPACT_VALIDITY",
                "CORRELATION_ERROR",
                "CORRELATION_MEASURED",
                "REQUIREMENT_EVALUATION",
                "CONVERGENCE_EVIDENCE",
                "COMPONENT_CHECKS_CLEAN",
            },
        )

    def test_analyze_pipeline_geometry_error_returns_422_envelope(self):
        document = self.load_baseline()
        bad_objects = dict(document, objects=[{"id": "x", "geometry": {"type": "torus", "radius": 5}, "material": "ABS"}])
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": bad_objects,
            "options": {"strict": False, "use_cache": True},
        }
        server, base_url = self.start_server()
        response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 422, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "GEOMETRY_PARSE_FAILED")
        self.assertFalse(any("result" in payload for key in payload))

    def test_analyze_pipeline_internal_error_returns_500_envelope(self):
        document = self.load_baseline()
        envelope = {
            "schema_id": "gms.web-analysis-request/1",
            "request": dict(document),
            "options": {"strict": False, "use_cache": True},
        }
        server, base_url = self.start_server()
        failed = {
            "run_id": "run-failed",
            "errors": [{"code": "PIPELINE_INTERNAL", "message": "boom"}],
        }
        with mock.patch("mouse_sim.pipeline.run_pipeline", return_value=failed):
            response, data = self.post_json(base_url, "/api/analyze", envelope)
        self.assertEqual(response.status, 500, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "E_INTERNAL")
        self.assertIn("boom", payload["error"]["message"])
        self.assertNotIn("result", payload)

    def test_normalize_obj_with_units(self):
        server, base_url = self.start_server()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=obj&units=mm&name=cover.obj",
            body=OBJ_SOURCE.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.geometry-preview/1")
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["format"], "obj")
        self.assertEqual(payload["source_units"], "mm")
        self.assertEqual(payload["geometry"]["type"], "mesh")
        self.assertEqual(payload["geometry"]["vertices"][1], [0.01, 0.0, 0.0])
        self.assertEqual(payload["source_name"], "cover.obj")

    def test_normalize_obj_without_units(self):
        server, base_url = self.start_server()
        response, data = request(
            base_url, "POST", "/api/geometry/normalize?format=obj", body=OBJ_SOURCE.encode("utf-8")
        )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertFalse(payload["supported"])
        self.assertIsNone(payload["geometry"])
        self.assertIsNone(payload["source_units"])
        self.assertEqual(payload["diagnostics"][0]["code"], "invalid_units")
        self.assertEqual(payload["diagnostics"][0]["severity"], "blocker")

    def test_normalize_stl_with_and_without_units(self):
        server, base_url = self.start_server()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=stl&units=cm",
            body=STL_SOURCE.encode("utf-8"),
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["format"], "stl")
        self.assertEqual(payload["geometry"]["type"], "mesh")
        response, data = request(
            base_url, "POST", "/api/geometry/normalize?format=stl", body=STL_SOURCE.encode("utf-8")
        )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertFalse(payload["supported"])
        self.assertEqual(payload["diagnostics"][0]["code"], "invalid_units")

    def test_normalize_step_success(self):
        server, base_url = self.start_server()
        body = (FIXTURES_DIR / "faceted_cube.step").read_bytes()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=step",
            body=body,
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.geometry-preview/1")
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["format"], "step")
        self.assertEqual(payload["source_units"], "mm")
        self.assertEqual(payload["geometry"]["type"], "mesh")
        self.assertEqual(len(payload["geometry"]["vertices"]), 8)
        self.assertEqual(len(payload["geometry"]["triangles"]), 12)

    def test_normalize_step_advanced_faces_success(self):
        server, base_url = self.start_server()
        body = (FIXTURES_DIR / "faceted_cube_advanced_faces.step").read_bytes()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=step",
            body=body,
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["format"], "step")
        self.assertEqual(payload["source_units"], "mm")
        self.assertEqual(len(payload["geometry"]["triangles"]), 12)

    def test_normalize_step_edge_loops_success(self):
        server, base_url = self.start_server()
        body = (FIXTURES_DIR / "faceted_cube_edge_loops.step").read_bytes()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=step",
            body=body,
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["format"], "step")
        self.assertEqual(payload["source_units"], "mm")
        self.assertEqual(len(payload["geometry"]["triangles"]), 12)

    def test_normalize_advanced_step_rejected(self):
        server, base_url = self.start_server()
        body = (FIXTURES_DIR / "advanced_brep.step").read_bytes()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=step",
            body=body,
        )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.geometry-preview/1")
        self.assertFalse(payload["supported"])
        self.assertIsNone(payload["geometry"])
        self.assertEqual(payload["format"], "step")
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "unsupported_format")
        self.assertEqual(diagnostic["severity"], "blocker")

    def test_normalize_step_kernel_unavailable_is_structured_422(self):
        """An assembly STEP upload with no usable kernel must yield the
        structured step_kernel_unavailable diagnostic, never a 500 with a raw
        exception string."""
        server, base_url = self.start_server()
        body = (
            b"ISO-10303-21;\nDATA;\n"
            b"#1=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#2,#3);\n"
            b"#2=SHAPE_REPRESENTATION_RELATIONSHIP();\n"
            b"#3=MANIFOLD_SOLID_BREP('',#4);\n"
            b"ENDSEC;\nEND-ISO-10303-21;"
        )
        with mock.patch("mouse_sim.step_kernel.freecadcmd_path", return_value=None):
            response, data = request(
                base_url,
                "POST",
                "/api/geometry/normalize?format=step",
                body=body,
            )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.geometry-preview/1")
        self.assertFalse(payload["supported"])
        self.assertIsNone(payload["geometry"])
        self.assertEqual(payload["format"], "step")
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "step_kernel_unavailable")
        self.assertEqual(diagnostic["severity"], "blocker")
        self.assertNotIn(b"Traceback", data)

    def test_normalize_step_kernel_error_types_map_to_422(self):
        """StepKernelUnavailable/StepKernelFailure raised by the importer must
        map to structured 422 preview failures, not the generic 500 handler."""
        from mouse_sim.step_kernel import StepKernelFailure, StepKernelUnavailable

        server, base_url = self.start_server()
        with mock.patch(
            "mouse_sim.importers.load_geometry",
            side_effect=StepKernelUnavailable("FreeCADCmd is unavailable"),
        ):
            response, data = request(
                base_url,
                "POST",
                "/api/geometry/normalize?format=step",
                body=b"step",
            )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.geometry-preview/1")
        self.assertFalse(payload["supported"])
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "step_kernel_unavailable")
        self.assertEqual(diagnostic["message"], "FreeCADCmd is unavailable")
        with mock.patch(
            "mouse_sim.importers.load_geometry",
            side_effect=StepKernelFailure("worker exploded"),
        ):
            response, data = request(
                base_url,
                "POST",
                "/api/geometry/normalize?format=step",
                body=b"step",
            )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["diagnostics"][0]["code"], "step_kernel_failed")
        self.assertNotIn(b"Traceback", data)

    def test_registered_step_asset_serving_is_sanitized(self):
        asset_id = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (asset_id + ".glb")
            path.write_bytes(b"glTF\x02binary")
            public = register_step_asset(
                {
                    "asset_id": asset_id,
                    "path": str(path),
                    "sha256": "b" * 64,
                    "bytes": path.stat().st_size,
                    "object_count": 1,
                    "triangle_count": 2,
                    "backend": "freecad-occt",
                    "tessellation_deflection_mm": 0.1,
                }
            )
            self.assertIsNotNone(public)
            self.assertNotIn("path", public)
            server, base_url = self.start_server(cors_origins=("http://localhost:5173",))
            response, data = request(
                base_url,
                "GET",
                public["url"],
                headers={"Origin": "http://localhost:5173"},
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(data, b"glTF\x02binary")
            self.assertEqual(response.getheader("Content-Type"), "model/gltf-binary")
            self.assertEqual(response.getheader("Content-Length"), str(len(data)))
            self.assertEqual(
                response.getheader("Cache-Control"), "public, max-age=31536000, immutable"
            )
            self.assertEqual(
                response.getheader("Access-Control-Allow-Origin"), "http://localhost:5173"
            )
            response, data = request(base_url, "GET", "/api/geometry/assets/not-safe.glb")
            self.assertEqual(response.status, 404)
            self.assertEqual(json.loads(data)["error"]["code"], "E_NOT_FOUND")
            response, data = request(
                base_url, "GET", "/api/geometry/assets/{}.glb".format("c" * 64)
            )
            self.assertEqual(response.status, 404)
            self.assertEqual(json.loads(data)["error"]["code"], "E_NOT_FOUND")

    def test_normalize_exposes_public_display_asset_and_url(self):
        asset_id = "d" * 64
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], units="m")
        diagnostic = ImportDiagnostic(
            "step_kernel_tessellated", "info", "display mesh", (("backend", "freecad-occt"),)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (asset_id + ".glb")
            path.write_bytes(b"glTF\x02asset")
            result = GeometryLoadResult(
                geometry=mesh,
                format="step",
                source_units="mm",
                diagnostics=(diagnostic,),
                source_name="assembly.step",
                display_asset={
                    "asset_id": asset_id,
                    "path": str(path),
                    "format": "glb",
                    "sha256": "e" * 64,
                    "bytes": path.stat().st_size,
                    "object_count": 1,
                    "triangle_count": 1,
                    "backend": "freecad-occt",
                    "tessellation_deflection_mm": 0.1,
                },
            )
            with mock.patch("mouse_sim.importers.load_geometry", return_value=result):
                server, base_url = self.start_server()
                response, data = request(
                    base_url,
                    "POST",
                    "/api/geometry/normalize?format=step",
                    body=b"step",
                )
            self.assertEqual(response.status, 200, data.decode("utf-8"))
            payload = json.loads(data)
            self.assertIn("display_asset", payload)
            self.assertEqual(payload["display_asset"]["format"], "glb")
            self.assertTrue(payload["display_asset"]["url"].endswith(".glb"))
            self.assertNotIn("path", payload["display_asset"])
            response, data = request(base_url, "GET", payload["display_asset"]["url"])
            self.assertEqual(response.status, 200)
            self.assertEqual(data, b"glTF\x02asset")

    def test_registered_step_asset_parts_json_endpoint(self):
        asset_id = "e" * 64
        with tempfile.TemporaryDirectory() as directory:
            glb_path = Path(directory) / (asset_id + ".glb")
            glb_path.write_bytes(b"glTF\x02binary")
            parts_path = Path(directory) / (asset_id + ".parts.json")
            parts_payload = {
                "parts": [
                    {
                        "id": "part-0",
                        "name": "Shell Top",
                        "geometry": {
                            "type": "mesh",
                            "vertices": [[0.0, 0.0, 0.0]],
                            "triangles": [],
                            "units": "m",
                            "transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                        },
                    }
                ]
            }
            parts_path.write_text(json.dumps(parts_payload), encoding="utf-8")
            public = register_step_asset(
                {
                    "asset_id": asset_id,
                    "path": str(glb_path),
                    "sha256": "f" * 64,
                    "bytes": glb_path.stat().st_size,
                    "object_count": 1,
                    "triangle_count": 1,
                    "backend": "freecad-occt",
                    "tessellation_deflection_mm": 0.1,
                    "parts": [{"id": "part-0", "name": "Shell Top"}],
                    "parts_path": str(parts_path),
                }
            )
            self.assertIsNotNone(public)
            self.assertEqual(public["parts"], [{"id": "part-0", "name": "Shell Top"}])
            self.assertEqual(
                public["parts_url"], "/api/geometry/assets/{}.parts.json".format(asset_id)
            )
            self.assertNotIn("path", public)
            self.assertNotIn("parts_path", public)
            server, base_url = self.start_server(cors_origins=("http://localhost:5173",))
            response, data = request(
                base_url,
                "GET",
                public["parts_url"],
                headers={"Origin": "http://localhost:5173"},
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(data), parts_payload)
            self.assertTrue(response.getheader("Content-Type").startswith("application/json"))
            self.assertEqual(response.getheader("Content-Length"), str(len(data)))
            self.assertEqual(
                response.getheader("Cache-Control"), "public, max-age=31536000, immutable"
            )
            self.assertEqual(
                response.getheader("Access-Control-Allow-Origin"), "http://localhost:5173"
            )
            response, data = request(
                base_url, "GET", "/api/geometry/assets/{}.parts.json".format("g" * 64)
            )
            self.assertEqual(response.status, 404)
            self.assertEqual(json.loads(data)["error"]["code"], "E_NOT_FOUND")
            response, data = request(base_url, "GET", "/api/geometry/assets/not-safe.parts.json")
            self.assertEqual(response.status, 404)
            self.assertEqual(json.loads(data)["error"]["code"], "E_NOT_FOUND")
            response, data = request(base_url, "GET", public["url"])
            self.assertEqual(response.status, 200)
            self.assertEqual(data, b"glTF\x02binary")

    def test_normalize_exposes_parts_metadata_and_url(self):
        asset_id = "1" * 64
        mesh = TriangleMesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], units="m")
        diagnostic = ImportDiagnostic(
            "step_kernel_tessellated", "info", "display mesh", (("backend", "freecad-occt"),)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (asset_id + ".glb")
            path.write_bytes(b"glTF\x02asset")
            parts_path = Path(directory) / (asset_id + ".parts.json")
            parts_payload = {
                "parts": [
                    {"id": "part-0", "name": "Shell Top"},
                    {"id": "part-1", "name": "Shell Bottom"},
                ]
            }
            parts_path.write_text(json.dumps(parts_payload), encoding="utf-8")
            result = GeometryLoadResult(
                geometry=mesh,
                format="step",
                source_units="mm",
                diagnostics=(diagnostic,),
                source_name="assembly.step",
                display_asset={
                    "asset_id": asset_id,
                    "path": str(path),
                    "format": "glb",
                    "sha256": "i" * 64,
                    "bytes": path.stat().st_size,
                    "object_count": 2,
                    "triangle_count": 1,
                    "backend": "freecad-occt",
                    "tessellation_deflection_mm": 0.1,
                    "parts": [
                        {"id": "part-0", "name": "Shell Top"},
                        {"id": "part-1", "name": "Shell Bottom"},
                    ],
                    "parts_path": str(parts_path),
                },
            )
            with mock.patch("mouse_sim.importers.load_geometry", return_value=result):
                server, base_url = self.start_server()
                response, data = request(
                    base_url,
                    "POST",
                    "/api/geometry/normalize?format=step",
                    body=b"step",
                )
            self.assertEqual(response.status, 200, data.decode("utf-8"))
            payload = json.loads(data)
            display = payload["display_asset"]
            self.assertEqual(
                display["parts"],
                [{"id": "part-0", "name": "Shell Top"}, {"id": "part-1", "name": "Shell Bottom"}],
            )
            self.assertTrue(display["parts_url"].endswith(".parts.json"))
            self.assertNotIn("path", display)
            self.assertNotIn("parts_path", display)
            response, data = request(base_url, "GET", display["parts_url"])
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(data), parts_payload)

    def test_normalize_invalid_format_and_media_type(self):
        server, base_url = self.start_server()
        response, data = request(
            base_url, "POST", "/api/geometry/normalize?format=iges", body=b"x"
        )
        self.assertEqual(response.status, 422)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "E_INVALID_FORMAT")
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize",
            body=b"x",
            headers={"Content-Type": "application/xml"},
        )
        self.assertEqual(response.status, 415)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_UNSUPPORTED_MEDIA_TYPE")

    def test_normalize_name_sanitization(self):
        server, base_url = self.start_server()
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=obj&units=mm&name=..%2Fevil%2Fcover.obj",
            body=OBJ_SOURCE.encode("utf-8"),
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["source_name"], "cover.obj")
        evil_name = "bad\x01\x02name.obj"
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=obj&units=mm&name=" + quote(evil_name),
            body=OBJ_SOURCE.encode("utf-8"),
        )
        self.assertEqual(response.status, 200, data.decode("utf-8"))
        payload = json.loads(data)
        self.assertEqual(payload["source_name"], "badname.obj")

    def test_body_limits(self):
        server, base_url = self.start_server(max_json_bytes=64)
        body = json.dumps(
            {
                "schema_id": "gms.web-analysis-request/1",
                "request": {"objects": [{"padding": "x" * 200}]},
            }
        ).encode("utf-8")
        self.assertGreater(len(body), 64)
        response, data = request(
            base_url,
            "POST",
            "/api/analyze",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 413)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_BODY_TOO_LARGE")
        self.assertEqual(response.getheader("Connection"), "close")
        server, base_url = self.start_server(max_geometry_bytes=64)
        response, data = request(
            base_url,
            "POST",
            "/api/geometry/normalize?format=obj&units=mm",
            body=b"x" * 200,
        )
        self.assertEqual(response.status, 413)
        payload = json.loads(data)
        self.assertEqual(payload["error"]["code"], "E_BODY_TOO_LARGE")

    def test_cors(self):
        server, base_url = self.start_server(cors_origins=("http://localhost:5173",))
        response, data = request(
            base_url, "GET", "/api/health", headers={"Origin": "http://localhost:5173"}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.getheader("Access-Control-Allow-Origin"), "http://localhost:5173"
        )
        self.assertNotEqual(response.getheader("Access-Control-Allow-Origin"), "*")
        self.assertEqual(
            response.getheader("Access-Control-Allow-Methods"), "GET, POST, OPTIONS"
        )
        self.assertEqual(response.getheader("Access-Control-Allow-Headers"), "Content-Type")
        self.assertEqual(response.getheader("Access-Control-Max-Age"), "86400")
        self.assertEqual(response.getheader("Vary"), "Origin")
        response, data = request(
            base_url, "OPTIONS", "/api/health", headers={"Origin": "http://localhost:5173"}
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(
            response.getheader("Access-Control-Allow-Origin"), "http://localhost:5173"
        )
        response, data = request(
            base_url, "GET", "/api/health", headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_static_serving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")
            assets = root / "assets"
            assets.mkdir()
            (assets / "app.js").write_text("console.log(1);", encoding="utf-8")
            server, base_url = self.start_server(web_dist=root)
            response, data = request(base_url, "GET", "/")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/html; charset=utf-8")
            self.assertEqual(response.getheader("Cache-Control"), "no-cache")
            self.assertEqual(data, b"<h1>hello</h1>")
            response, data = request(base_url, "GET", "/assets/app.js")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/javascript")
            self.assertEqual(
                response.getheader("Cache-Control"), "public, max-age=31536000, immutable"
            )
            self.assertEqual(data, b"console.log(1);")
            response, data = request(base_url, "GET", "/nested", headers={"Accept": "text/html"})
            self.assertEqual(response.status, 200)
            self.assertEqual(data, b"<h1>hello</h1>")
            response, data = request(
                base_url, "GET", "/missing.txt", headers={"Accept": "application/json"}
            )
            self.assertEqual(response.status, 404)
            payload = json.loads(data)
            self.assertEqual(payload["schema_id"], "gms.web-error/1")
            self.assertEqual(payload["error"]["code"], "E_NOT_FOUND")
            response, data = request(base_url, "GET", "/..%2f..%2fetc%2fpasswd")
            self.assertEqual(response.status, 404)
            payload = json.loads(data)
            self.assertEqual(payload["schema_id"], "gms.web-error/1")
            self.assertNotIn(b"root:", data)
            response, data = request(base_url, "GET", "/../mouse_sim/web_api.py")
            self.assertEqual(response.status, 404)
            payload = json.loads(data)
            self.assertEqual(payload["schema_id"], "gms.web-error/1")

    def test_unknown_api_path_and_no_web_dist(self):
        server, base_url = self.start_server()
        response, data = request(base_url, "GET", "/api/nope")
        self.assertEqual(response.status, 404)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "E_NOT_FOUND")
        response, data = request(base_url, "GET", "/")
        self.assertEqual(response.status, 404)
        payload = json.loads(data)
        self.assertEqual(payload["schema_id"], "gms.web-error/1")
        self.assertEqual(payload["error"]["code"], "E_NOT_FOUND")


class AssetRegistryLifecycleTests(unittest.TestCase):
    def setUp(self):
        import mouse_sim.web_api as web_api

        self._registry = web_api._STEP_ASSET_REGISTRY
        self._parts_registry = web_api._STEP_ASSET_PARTS_REGISTRY
        self._cap = web_api._STEP_ASSET_REGISTRY_CAP
        with web_api._STEP_ASSET_REGISTRY_LOCK:
            web_api._STEP_ASSET_REGISTRY.clear()
            web_api._STEP_ASSET_PARTS_REGISTRY.clear()

    def tearDown(self):
        import mouse_sim.web_api as web_api

        with web_api._STEP_ASSET_REGISTRY_LOCK:
            web_api._STEP_ASSET_REGISTRY.clear()
            web_api._STEP_ASSET_PARTS_REGISTRY.clear()
        web_api._STEP_ASSET_REGISTRY_CAP = self._cap

    def test_register_existing_assets_restores_parts_from_manifest(self):
        import mouse_sim.web_api as web_api
        from mouse_sim.web_api import register_existing_assets

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_id = "a" * 64
            glb = root / (asset_id + ".glb")
            glb.write_bytes(b"glTF\x02\x00\x00\x00")
            parts = root / (asset_id + ".parts.json")
            parts.write_text(json.dumps({"parts": [{"id": "part-0", "name": "Shell"}]}), encoding="utf-8")
            manifest = root / (asset_id + ".manifest.json")
            manifest.write_text(
                json.dumps(
                    {
                        "parts": [
                            {
                                "id": "part-0",
                                "name": "Shell",
                                "color": [0.36, 1.0, 0.41],
                                "vertex_count": 1,
                                "triangle_count": 1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(register_existing_assets(directory), 1)
            with web_api._STEP_ASSET_REGISTRY_LOCK:
                self.assertIn(asset_id, web_api._STEP_ASSET_REGISTRY)
                self.assertIn(asset_id, web_api._STEP_ASSET_PARTS_REGISTRY)

    def test_register_existing_assets_skips_incomplete_and_bad_names(self):
        from mouse_sim.web_api import register_existing_assets

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ("b" * 64 + ".glb")).write_bytes(b"glTF\x02\x00\x00\x00")
            (root / ("nothex" + ".glb")).write_bytes(b"glTF\x02\x00\x00\x00")
            self.assertEqual(register_existing_assets(directory), 0)

    def test_registry_eviction_is_lru(self):
        import mouse_sim.web_api as web_api

        web_api._STEP_ASSET_REGISTRY_CAP = 2
        with tempfile.TemporaryDirectory() as directory:
            ids = []
            for index in range(3):
                asset_id = format(index, "064x")
                glb = Path(directory) / (asset_id + ".glb")
                glb.write_bytes(b"glTF\x02\x00\x00\x00")
                register_step_asset(
                    {
                        "asset_id": asset_id,
                        "path": str(glb),
                        "parts_path": None,
                    }
                )
                ids.append(asset_id)
            with web_api._STEP_ASSET_REGISTRY_LOCK:
                self.assertEqual(set(web_api._STEP_ASSET_REGISTRY.keys()), {ids[1], ids[2]})
            # Re-register the evicted id: it becomes the newest entry again.
            glb = Path(directory) / (ids[0] + ".glb")
            register_step_asset({"asset_id": ids[0], "path": str(glb), "parts_path": None})
            with web_api._STEP_ASSET_REGISTRY_LOCK:
                self.assertEqual(set(web_api._STEP_ASSET_REGISTRY.keys()), {ids[2], ids[0]})


if __name__ == "__main__":
    unittest.main()
