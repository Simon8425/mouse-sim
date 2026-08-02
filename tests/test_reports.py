import json
import unittest

from mouse_sim.reports import render_evidence_package, render_html_report, render_json_report


def sample_bundle(**overrides):
    bundle = {
        "schema_id": "gms.bundle/1",
        "engine_version": "0.1.0",
        "run_id": "run-0001",
        "mode": "exploration",
        "lifecycle_state": "completed",
        "validity": {"state": "valid", "reasons": [], "assumptions": [], "confidence": "low"},
        "issues": [],
        "geometry_summary": {"representation": "mesh", "body_count": 1, "closed": True},
        "mass": {"total_mass_kg": 0.012, "status": "estimated"},
        "validation": {"valid": True, "findings": []},
        "structural": {"method": "beam", "status": "ok", "unsupported_failure_modes": ["UNSUPPORTED_BATTERY_CRUSH"]},
        "impact": {"unsupported_failure_modes": ["UNSUPPORTED_BATTERY_CRUSH", "UNSUPPORTED_PCB_SHOCK"]},
        "qualification": {
            "qualified": False,
            "gates": [],
            "inputs": {"requirements": [{"id": "req-1", "title": "Survive drop <script>", "internal": True}]},
        },
        "manifest": {"engine_version": "0.1.0", "run_id": "run-0001", "input_hashes": {"project": "abc123"}},
        "errors": [],
    }
    bundle.update(overrides)
    return bundle


class RenderJsonReportTests(unittest.TestCase):
    def test_deterministic_output(self):
        self.assertEqual(render_json_report(sample_bundle()), render_json_report(sample_bundle()))

    def test_sorted_keys(self):
        report = json.loads(render_json_report(sample_bundle()))
        self.assertEqual(list(report), sorted(report))

    def test_top_level_fields_present(self):
        report = json.loads(render_json_report(sample_bundle()))
        self.assertEqual(report["schema_id"], "gms.report/1")
        for key in (
            "schema_id", "run_id", "engine_version", "mode", "decision",
            "evidence_disposition", "lifecycle_state", "validity", "provenance",
            "geometry_summary", "materials", "mass", "validation", "analysis",
            "impact", "qualification", "requirements", "issues",
            "unsupported_failure_modes", "errors",
        ):
            self.assertIn(key, report)
        self.assertIn("structural", report["analysis"])

    def test_raises_on_nan(self):
        bundle = sample_bundle(mass={"total_mass_kg": float("nan")})
        with self.assertRaises(ValueError):
            render_json_report(bundle)

    def test_decision_derivation(self):
        self.assertEqual(
            json.loads(render_json_report(sample_bundle()))["decision"], "not_qualified"
        )
        qualified = sample_bundle(qualification={"qualified": True, "gates": []})
        self.assertEqual(json.loads(render_json_report(qualified))["decision"], "qualified")
        without_qualification = sample_bundle()
        del without_qualification["qualification"]
        self.assertEqual(
            json.loads(render_json_report(without_qualification))["decision"], "completed"
        )

    def test_unsupported_failure_modes_union(self):
        report = json.loads(render_json_report(sample_bundle()))
        self.assertEqual(
            report["unsupported_failure_modes"],
            ["UNSUPPORTED_BATTERY_CRUSH", "UNSUPPORTED_PCB_SHOCK"],
        )


class RenderHtmlReportTests(unittest.TestCase):
    def test_escapes_script_content(self):
        bundle = sample_bundle(
            issues=[{"code": "E_TEST", "severity": "warning", "message": "<script>alert('x')</script>"}]
        )
        html = render_html_report(bundle)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<\\/script", html)
        self.assertEqual(html.count("</script>"), 1)

    def test_escapes_uppercase_script_close_in_json_blob(self):
        bundle = sample_bundle(
            issues=[{"code": "E_TEST", "severity": "warning", "message": "</Script> not </SCRIPT> escaped"}]
        )
        html = render_html_report(bundle)
        self.assertNotIn("</Script>", html)
        self.assertNotIn("</SCRIPT>", html)
        self.assertEqual(html.count("</script>"), 1)
        start = html.index('<script id="report-data" type="application/json">')
        start += len('<script id="report-data" type="application/json">')
        end = html.rindex("</script>")
        blob = json.loads(html[start:end])
        message = blob["issues"][0]["message"]
        self.assertEqual(message, "</Script> not </SCRIPT> escaped")

    def test_fixed_section_names(self):
        html = render_html_report(sample_bundle())
        for name in (
            "Decision", "Mode &amp; Validity", "Mass", "Validation", "Analysis",
            "Impact", "Qualification Gates", "Requirements", "Issues",
            "Unsupported Failure Modes", "Provenance",
        ):
            self.assertIn("<h2>{}</h2>".format(name), html)

    def test_no_timestamp(self):
        html = render_html_report(sample_bundle())
        self.assertNotIn("timestamp", html)
        self.assertNotIn("created_at", html)
        self.assertNotIn("2026", html)

    def test_byte_identical_for_identical_bundle(self):
        self.assertEqual(render_html_report(sample_bundle()), render_html_report(sample_bundle()))


class RenderEvidencePackageTests(unittest.TestCase):
    def test_redacts_internal_entries(self):
        bundle = sample_bundle(
            qualification={
                "qualified": False,
                "gates": [],
                "inputs": {
                    "requirements": [
                        {"id": "req-1", "title": "Public requirement", "internal": False},
                        {"id": "req-2", "title": "Internal requirement", "internal": True},
                        {"id": "req-3", "title": "Legacy requirement"},
                    ]
                },
            }
        )
        package = render_evidence_package(bundle)
        ids = [entry["requirement_id"] for entry in package["requirements"]]
        self.assertEqual(ids, ["req-1", "req-3"])
        self.assertNotIn("internal", package["requirements"][0])

    def test_include_internal_entries(self):
        package = render_evidence_package(sample_bundle(), include_internal=True)
        self.assertEqual([entry["requirement_id"] for entry in package["requirements"]], ["req-1"])

    def test_matrix_fields(self):
        bundle = sample_bundle(
            qualification={
                "qualified": False,
                "gates": [],
                "inputs": {
                    "requirements": [
                        {
                            "id": "req-9",
                            "title": "Survive drop",
                            "acceptance": {
                                "metric_key": "peak_stress",
                                "operator": "<",
                                "upper": {"value_si": 1e6, "unit": "Pa"},
                            },
                            "result": "pass",
                            "evidence_refs": ["ev-1", "ev-2"],
                            "deviation": None,
                        }
                    ]
                },
            }
        )
        entry = render_evidence_package(bundle)["requirements"][0]
        self.assertEqual(entry["requirement_id"], "req-9")
        self.assertEqual(entry["result"], "pass")
        self.assertEqual(entry["evidence_refs"], ["ev-1", "ev-2"])
        self.assertEqual(entry["acceptance_criterion"]["metric_key"], "peak_stress")
        self.assertIsNone(entry["deviation"])
