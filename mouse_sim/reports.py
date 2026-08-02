"""Deterministic report rendering for mouse simulation analysis bundles.

Every function here is a pure function of the pipeline bundle: identical
bundles produce byte-identical output and no timestamp is ever included.
"""

import html
import re

from .canonical import canonical_json
from .errors import CanonicalizationError

REPORT_SCHEMA_ID = "gms.report/1"
EVIDENCE_SCHEMA_ID = "gms.evidence/1"


def _qualification(bundle):
    value = bundle.get("qualification")
    return value if isinstance(value, dict) else {}


def _manifest(bundle):
    value = bundle.get("manifest")
    return value if isinstance(value, dict) else {}


def _decision(bundle):
    qualification = _qualification(bundle)
    if qualification.get("qualified") is True:
        return "qualified"
    if qualification:
        return "not_qualified"
    return "completed"


def _evidence_disposition(bundle):
    value = bundle.get("evidence_disposition")
    if value:
        return value
    return _qualification(bundle).get("evidence_disposition", "exploration_only")


def _input_hashes(manifest):
    for key in ("input_hashes", "input_content_hashes", "input_hash"):
        value = manifest.get(key)
        if value:
            return value
    result = {}
    snapshots = manifest.get("snapshots")
    if isinstance(snapshots, (list, tuple)):
        for entry in snapshots:
            if not isinstance(entry, dict):
                continue
            content_hash = entry.get("content_hash")
            if not content_hash:
                continue
            identifier = (
                entry.get("entity_id") or entry.get("id") or entry.get("entity_type") or "input"
            )
            result[str(identifier)] = content_hash
    return result


def _provenance(bundle):
    manifest = _manifest(bundle)
    return {
        "engine_version": bundle.get("engine_version") or manifest.get("engine_version", ""),
        "run_id": bundle.get("run_id") or manifest.get("run_id", ""),
        "input_hashes": _input_hashes(manifest),
    }


def _materials(bundle):
    value = bundle.get("materials")
    if value is None:
        qualification = _qualification(bundle)
        inputs = qualification.get("inputs")
        value = inputs.get("materials") if isinstance(inputs, dict) else None
    return value if value is not None else {}


def _requirements(bundle):
    qualification = _qualification(bundle)
    inputs = qualification.get("inputs")
    if isinstance(inputs, dict) and isinstance(inputs.get("requirements"), (list, tuple)):
        return list(inputs["requirements"])
    value = bundle.get("requirements", [])
    return list(value) if isinstance(value, (list, tuple)) else []


def _unsupported_failure_modes(bundle):
    modes = []
    for key in ("structural", "impact"):
        section = bundle.get(key)
        if not isinstance(section, dict):
            continue
        value = section.get("unsupported_failure_modes")
        if isinstance(value, (list, tuple)):
            modes.extend(str(item) for item in value)
    return sorted(set(modes))


def render_json_report(bundle):
    """Render the pipeline bundle as deterministic canonical JSON."""
    if not isinstance(bundle, dict):
        raise CanonicalizationError("report bundle must be an object")
    structural = bundle.get("structural")
    analysis = {"structural": structural} if isinstance(structural, dict) else {}
    qualification = _qualification(bundle)
    if qualification and "gates" not in qualification:
        qualification = dict(qualification)
        qualification["gates"] = []
    report = {
        "schema_id": REPORT_SCHEMA_ID,
        "run_id": bundle.get("run_id", ""),
        "engine_version": bundle.get("engine_version", ""),
        "mode": bundle.get("mode", "exploration"),
        "decision": _decision(bundle),
        "evidence_disposition": _evidence_disposition(bundle),
        "lifecycle_state": bundle.get("lifecycle_state", "completed"),
        "validity": bundle.get("validity", {}),
        "provenance": _provenance(bundle),
        "geometry_summary": bundle.get("geometry_summary", {}),
        "materials": _materials(bundle),
        "mass": bundle.get("mass", {}),
        "validation": bundle.get("validation", {}),
        "analysis": analysis,
        "impact": bundle.get("impact", {}),
        "qualification": qualification,
        "requirements": _requirements(bundle),
        "issues": bundle.get("issues", []),
        "unsupported_failure_modes": _unsupported_failure_modes(bundle),
        "errors": bundle.get("errors", []),
    }
    return canonical_json(report)


def render_evidence_package(bundle, include_internal=False):
    """Return the requirement-to-evidence matrix with internal entries redacted."""
    if not isinstance(bundle, dict):
        raise CanonicalizationError("report bundle must be an object")
    qualification = _qualification(bundle)
    inputs = qualification.get("inputs")
    raw = inputs.get("requirements") if isinstance(inputs, dict) else None
    if not isinstance(raw, (list, tuple)):
        raw = bundle.get("evidence")
    if not isinstance(raw, (list, tuple)):
        raw = bundle.get("requirements", [])
    entries = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        if bool(item.get("internal", False)) and not include_internal:
            continue
        acceptance = item.get("acceptance_criterion", item.get("acceptance"))
        result = item.get("result", item.get("status", "incomplete"))
        evidence_refs = item.get("evidence_refs", item.get("evidence", []))
        if not isinstance(evidence_refs, (list, tuple)):
            evidence_refs = []
        entries.append(
            {
                "requirement_id": item.get("id", item.get("external_id", "")),
                "title": item.get("title", ""),
                "acceptance_criterion": acceptance,
                "result": result,
                "evidence_refs": list(evidence_refs),
                "deviation": item.get("deviation"),
            }
        )
    return {"schema_id": EVIDENCE_SCHEMA_ID, "requirements": entries}


def _display(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "; ".join(
            "{}={}".format(key, _display(item)) for key, item in sorted(value.items())
        )
    if isinstance(value, (list, tuple)):
        return "; ".join(_display(item) for item in value)
    return str(value)


def _pairs(mapping):
    rows = []
    values = []
    for key, value in sorted(mapping.items()):
        if isinstance(value, dict):
            if value:
                values.append((key, value))
        elif isinstance(value, (list, tuple)):
            if value:
                values.append((key, value))
        else:
            rows.append((key, value))
    return rows, values


def _section(title, rows=(), values=()):
    parts = ['<section class="report-section">', "<h2>", html.escape(title, quote=True), "</h2>"]
    if rows:
        parts.append('<table class="rows"><tbody>')
        for label, value in rows:
            parts.append("<tr><th>")
            parts.append(html.escape(_display(label), quote=True))
            parts.append("</th><td>")
            parts.append(html.escape(_display(value), quote=True))
            parts.append("</td></tr>")
        parts.append("</tbody></table>")
    if values:
        parts.append('<ul class="items">')
        for entry in values:
            parts.append("<li>")
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                parts.append("<strong>")
                parts.append(html.escape(_display(entry[0]), quote=True))
                parts.append("</strong>: ")
                parts.append(html.escape(_display(entry[1]), quote=True))
            else:
                parts.append(html.escape(_display(entry), quote=True))
            parts.append("</li>")
        parts.append("</ul>")
    parts.append("</section>")
    return "".join(parts)


_STYLE = (
    "body{font-family:Helvetica,Arial,sans-serif;margin:2em;color:#1a1a1a}\n"
    "h1{font-size:1.4em}\n"
    "h2{font-size:1.05em;border-bottom:1px solid #cccccc;padding-bottom:.2em}\n"
    "table.rows{border-collapse:collapse;margin:.4em 0}\n"
    "table.rows th{text-align:left;font-weight:600;padding:.15em .8em .15em 0;"
    "vertical-align:top;white-space:nowrap}\n"
    "table.rows td{padding:.15em 0}\n"
    "ul.items{margin:.4em 0;padding-left:1.2em}\n"
    "li{margin:.15em 0}\n"
)


def render_html_report(bundle):
    """Render a fully self-contained, deterministic HTML report."""
    if not isinstance(bundle, dict):
        raise CanonicalizationError("report bundle must be an object")
    json_blob = re.sub(
        r"(?i)</script", lambda match: match.group(0)[:1] + "\\" + match.group(0)[1:], canonical_json(bundle)
    )
    qualification = _qualification(bundle)
    validity = bundle.get("validity") if isinstance(bundle.get("validity"), dict) else {}
    mass = bundle.get("mass") if isinstance(bundle.get("mass"), dict) else {}
    validation = bundle.get("validation") if isinstance(bundle.get("validation"), dict) else {}
    structural = bundle.get("structural") if isinstance(bundle.get("structural"), dict) else {}
    impact = bundle.get("impact") if isinstance(bundle.get("impact"), dict) else {}
    gates = qualification.get("gates") if isinstance(qualification.get("gates"), (list, tuple)) else []
    requirements = _requirements(bundle)
    issues = bundle.get("issues") if isinstance(bundle.get("issues"), (list, tuple)) else []
    mass_rows, mass_values = _pairs(mass)
    validation_rows, validation_values = _pairs(validation)
    structural_rows, structural_values = _pairs(structural)
    impact_rows, impact_values = _pairs(impact)
    provenance = _provenance(bundle)
    provenance_rows, provenance_values = _pairs(provenance)
    mode = bundle.get("mode", "exploration")
    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>mouse-sim report</title>",
        "<style>",
        _STYLE,
        "</style>",
        "</head>",
        "<body>",
        "<h1>mouse-sim report</h1>",
        _section("Decision", rows=[("decision", _decision(bundle)), ("mode", mode)]),
        _section(
            "Mode & Validity",
            rows=[
                ("mode", mode),
                ("lifecycle_state", bundle.get("lifecycle_state", "completed")),
                ("evidence_disposition", _evidence_disposition(bundle)),
                ("validity_state", validity.get("state", "")),
                ("confidence", validity.get("confidence", "")),
            ],
            values=[
                ("reasons", validity.get("reasons", ())),
                ("assumptions", validity.get("assumptions", ())),
            ],
        ),
        _section("Mass", rows=mass_rows, values=mass_values),
        _section("Validation", rows=validation_rows, values=validation_values),
        _section("Analysis", rows=structural_rows, values=structural_values),
        _section("Impact", rows=impact_rows, values=impact_values),
        _section(
            "Qualification Gates",
            rows=[
                ("qualified", qualification.get("qualified", False)),
                ("evidence_disposition", _evidence_disposition(bundle)),
            ],
            values=list(gates),
        ),
        _section("Requirements", values=requirements),
        _section("Issues", values=issues),
        _section("Unsupported Failure Modes", values=_unsupported_failure_modes(bundle)),
        _section("Provenance", rows=provenance_rows, values=provenance_values),
        '<script id="report-data" type="application/json">',
        json_blob,
        "</script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts) + "\n"
