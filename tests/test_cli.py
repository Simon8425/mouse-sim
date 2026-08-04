import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mouse_sim import cli

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from mouse_sim.pipeline import run_pipeline  # noqa: F401

    PIPELINE_MISSING = False
except Exception:
    PIPELINE_MISSING = True


MINIMAL_PROJECT = {
    "schema_id": "gms.project-document",
    "schema_version": 1,
    "project": {"meta": {"entity_type": "Project", "id": "proj-1", "schema_version": 1}},
}

VALID_CATALOG = {
    "materials": [
        {
            "name": "Test Polymer",
            "family": "thermoplastic",
            "properties": {"density": 1100, "young_modulus": 2.1e9, "poissons_ratio": 0.36},
        }
    ]
}

INVALID_CATALOG = {"materials": [{"name": "Test Polymer", "properties": {"young_modulus": 2.1e9}}]}


def run_cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "mouse_sim"] + list(arguments),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


class VersionTests(unittest.TestCase):
    def test_version_prints_and_exits_zero(self):
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "mouse-sim 0.1.0")


class MaterialValidateTests(unittest.TestCase):
    def test_valid_catalog_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(VALID_CATALOG), encoding="utf-8")
            result = run_cli("material", "validate", "--input", str(path))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"valid": True, "errors": []})

    def test_invalid_catalog_exits_twenty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(INVALID_CATALOG), encoding="utf-8")
            result = run_cli("material", "validate", "--input", str(path))
        self.assertEqual(result.returncode, 20)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])
        self.assertTrue(any("density" in error for error in payload["errors"]))


class UsageTests(unittest.TestCase):
    def test_unknown_command_exits_sixty_four(self):
        result = run_cli("frobnicate")
        self.assertEqual(result.returncode, 64)


class RunTests(unittest.TestCase):
    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_creates_artifacts_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(MINIMAL_PROJECT), encoding="utf-8")
            result = run_cli("run", "--input", str(project), "--output", str(root / "reports"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "reports" / "report.json").is_file())
            self.assertTrue((root / "reports" / "report.html").is_file())
            self.assertTrue((root / "reports" / "manifest.json").is_file())
            json.loads((root / "reports" / "report.json").read_text(encoding="utf-8"))

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_passes_document_objects_to_pipeline(self):
        document = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "units": "mm",
            "objects": [
                {"id": "shell", "geometry": {"type": "box", "size": [100, 60, 40]}, "material": "ABS"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(document), encoding="utf-8")
            result = run_cli("run", "--input", str(project), "--output", str(root / "reports"))
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "reports" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report["geometry_summary"]["objects"]), 1)
            self.assertIsNotNone(report["mass"]["mass_kg"])
            self.assertAlmostEqual(report["mass"]["mass_kg"], 0.1 * 0.06 * 0.04 * 1040.0, delta=0.01)
            self.assertIsNotNone(report["validation"])
            self.assertEqual(report["errors"], [])

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_preserves_document_options_when_strict_is_used(self):
        document = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "options": {"cache_dir": "document-cache", "seed": 17, "strict": False},
        }
        bundle = {"mode": "exploration", "qualification": {"qualified": False}, "errors": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(document), encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "run",
                    "--input",
                    str(project),
                    "--output",
                    str(root / "reports"),
                    "--emit",
                    "json",
                    "--stdout",
                    "none",
                    "--strict",
                ]
            )
            with patch("mouse_sim.pipeline.run_pipeline", return_value=bundle) as run_pipeline:
                with patch("mouse_sim.cli._write_atomic"), patch("mouse_sim.cli.render_json_report", return_value="{}"):
                    self.assertEqual(cli._cmd_run(args), 0)
        request = run_pipeline.call_args.args[0]
        self.assertEqual(request["options"], {"cache_dir": "document-cache", "seed": 17, "strict": True})

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_with_invalid_geometry_exits_twenty(self):
        document = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "objects": [
                {"id": "bogus", "geometry": {"type": "torus", "radius": 5}, "material": "ABS"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(document), encoding="utf-8")
            result = run_cli("run", "--input", str(project), "--output", str(root / "reports"))
        self.assertEqual(result.returncode, 20)

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_validate_with_invalid_geometry_exits_twenty(self):
        document = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "objects": [
                {"id": "bogus", "geometry": {"type": "torus", "radius": 5}, "material": "ABS"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(document), encoding="utf-8")
            result = run_cli("validate", "--input", str(project))
        self.assertEqual(result.returncode, 20)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])
        self.assertTrue(any("GEOMETRY_PARSE_FAILED" in issue["code"] for issue in payload["issues"]))

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_respects_document_mode_qualification(self):
        document = {"schema_id": "gms.project/1", "mode": "qualification"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(document), encoding="utf-8")
            result = run_cli("run", "--input", str(project), "--output", str(root / "reports"))
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("mode=qualification", result.stdout)
        self.assertIn("decision=not_qualified", result.stdout)

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_cache_dir_populated_and_reused(self):
        document = {
            "schema_id": "gms.project/1",
            "mode": "exploration",
            "units": "mm",
            "objects": [
                {"id": "shell", "geometry": {"type": "box", "size": [100, 60, 40]}, "material": "ABS"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(document), encoding="utf-8")
            cache_dir = root / "cache"
            result_one = run_cli(
                "run", "--input", str(project), "--output", str(root / "out1"), "--cache-dir", str(cache_dir)
            )
            self.assertEqual(result_one.returncode, 0, result_one.stderr)
            entries = list(cache_dir.glob("*.json"))
            self.assertEqual(len(entries), 1)
            result_two = run_cli(
                "run", "--input", str(project), "--output", str(root / "out2"), "--cache-dir", str(cache_dir)
            )
            self.assertEqual(result_two.returncode, 0, result_two.stderr)
            self.assertEqual(len(list(cache_dir.glob("*.json"))), 1)
            first = json.loads((root / "out1" / "report.json").read_text(encoding="utf-8"))
            second = json.loads((root / "out2" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(
                (root / "out1" / "report.json").read_bytes(),
                (root / "out2" / "report.json").read_bytes(),
            )

    def test_run_with_invalid_json_exits_twenty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text("{not valid json", encoding="utf-8")
            result = run_cli("run", "--input", str(project))
        self.assertEqual(result.returncode, 20)

    @unittest.skipIf(PIPELINE_MISSING, "pipeline is being built in parallel")
    def test_run_stdout_json_is_parseable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text(json.dumps(MINIMAL_PROJECT), encoding="utf-8")
            result = run_cli(
                "run", "--input", str(project), "--output", str(root / "reports"), "--stdout", "json"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema_id"], "gms.report/1")

    def test_run_invalid_json_error_format_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.json"
            project.write_text("not json", encoding="utf-8")
            result = run_cli("run", "--input", str(project), "--error-format", "json")
        self.assertEqual(result.returncode, 20)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["schema"], "gms.error/1")
        self.assertEqual(payload["error"]["code"], "E_INVALID_INPUT")


class ImportTests(unittest.TestCase):
    def test_unsupported_format_exits_thirty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.step"
            source.write_text("ISO-10303-21;", encoding="utf-8")
            result = run_cli("import", "--input", str(source), "--format", "step", "--units", "mm")
        self.assertEqual(result.returncode, 30)

    def test_json_geometry_import_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "box.json"
            source.write_text(
                json.dumps({"type": "box", "size": [10.0, 10.0, 10.0], "units": "mm"}),
                encoding="utf-8",
            )
            result = run_cli("import", "--input", str(source), "--format", "json", "--units", "mm")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "gms.normalized-geometry/1")
        self.assertEqual(payload["format"], "json")

    def test_parse_error_exits_twenty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.json"
            source.write_text("{broken", encoding="utf-8")
            result = run_cli("import", "--input", str(source), "--format", "json", "--units", "mm")
        self.assertEqual(result.returncode, 20)


class ServeTests(unittest.TestCase):
    def test_serve_help_lists_options(self):
        result = run_cli("serve", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--web-dist", result.stdout)
        self.assertIn("--cors-origin", result.stdout)
        self.assertIn("--cache-dir", result.stdout)

    def test_serve_invalid_port_exits_usage(self):
        result = run_cli("serve", "--port", "notanumber")
        self.assertEqual(result.returncode, 64)

    def test_serve_bind_failure_exits_internal(self):
        result = subprocess.run(
            [sys.executable, "-m", "mouse_sim", "serve", "--port", "-1", "--quiet"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 40)
        self.assertIn("E_INTERNAL", result.stderr)
