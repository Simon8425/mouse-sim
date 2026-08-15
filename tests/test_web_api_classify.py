"""Tests for the AI classification endpoints (POST /api/classify + job status)."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from mouse_sim.web_api import WebConfig, build_server, register_step_asset

PARTS = {
    "parts": [
        {
            "id": "part-0",
            "name": "C-WHEEL-01FK",
            "geometry": {
                "type": "mesh",
                "vertices": [
                    [0, 0, 0], [0.01, 0, 0], [0.01, 0.01, 0], [0, 0.01, 0],
                    [0, 0, 0.005], [0.01, 0, 0.005], [0.01, 0.01, 0.005], [0, 0.01, 0.005],
                ],
                "triangles": [
                    [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
                    [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
                    [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
                ],
            },
        },
        {
            "id": "part-1",
            "name": "TD011-PCB-1",
            "geometry": {
                "type": "mesh",
                "vertices": [
                    [0, 0, 0], [0.05, 0, 0], [0.05, 0.03, 0], [0, 0.03, 0],
                    [0, 0, 0.0016], [0.05, 0, 0.0016], [0.05, 0.03, 0.0016], [0, 0.03, 0.0016],
                ],
                "triangles": [
                    [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
                    [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
                    [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
                ],
            },
        },
    ]
}


def make_request(base_url, method, path, body=None, headers=None):
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response, data
    finally:
        connection.close()


class ClassifyEndpointTests(unittest.TestCase):
    def start_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = Path(tmp)
            config = WebConfig(port=0, cache_dir=self.tmp, log_requests=False)
            server = build_server(config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            host, port = server.server_address[:2]
            return "http://{}:{}".format(host, port)

    def register_asset(self):
        # Keep the asset files inside the server's cache dir (self.tmp) so
        # they live for the test and are cleaned up with it.  The previous
        # pattern deleted the temp dir before writing the files, leaking a
        # recreated dir and leaving the registered asset unresolvable.
        asset_dir = self.tmp / "assets"
        import os

        asset_id = "a" * 64
        os.makedirs(asset_dir, exist_ok=True)
        parts_path = asset_dir / (asset_id + ".parts.json")
        parts_path.write_text(json.dumps(PARTS), encoding="utf-8")
        glb_path = asset_dir / (asset_id + ".glb")
        glb_path.write_bytes(b"GLB" + b"\x00" * 32)
        registered = register_step_asset(
            {"asset_id": asset_id, "path": str(glb_path), "parts_path": str(parts_path), "parts": [
                {"id": "part-0", "name": "C-WHEEL-01FK"},
                {"id": "part-1", "name": "TD011-PCB-1"},
            ]}
        )
        return asset_id

    def test_classify_missing_key_falls_back_to_heuristic(self):
        base_url = self.start_server()
        asset_id = self.register_asset()
        with mock.patch.dict("os.environ", {}, clear=False):
            # Explicit disable switch: the lazy .env loader only fills keys
            # that are ABSENT, so an explicitly-set MOUSE_SIM_AI_ENABLED=0
            # wins over a repository .env and the heuristic fallback is
            # exercised deterministically (no network call).
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ["MOUSE_SIM_AI_ENABLED"] = "0"
            payload = {"asset_id": asset_id}
            response, data = make_request(
                base_url, "POST", "/api/classify",
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(response.status, 202, data.decode("utf-8"))
            body = json.loads(data)
            self.assertIn("job_id", body)
            # Poll until done (heuristic-only completes immediately).
            for _ in range(100):
                status_response, status_data = make_request(base_url, "GET", "/api/classify/jobs/" + body["job_id"])
                status_body = json.loads(status_data)
                if status_body.get("status") in ("done", "error"):
                    break
                time.sleep(0.05)
            self.assertEqual(status_body.get("status"), "done", status_data.decode("utf-8"))
            results = status_body.get("results", [])
            self.assertEqual(len(results), 2)
            by_id = {r["object_id"]: r for r in results}
            # Without AI the rule classifier returns its conservative names.
            self.assertIn("part-0", by_id)
            self.assertIn("part-1", by_id)

    def test_classify_unknown_asset_422(self):
        base_url = self.start_server()
        response, data = make_request(
            base_url, "POST", "/api/classify",
            body=json.dumps({"asset_id": "b" * 64}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 422)

    def test_classify_job_404(self):
        base_url = self.start_server()
        response, data = make_request(base_url, "GET", "/api/classify/jobs/cj-0000000000000000")
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
