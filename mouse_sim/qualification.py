"""Hard exploration/qualification separation.

Qualification gates evaluate readiness evidence without ever promoting a
result: exploration output is always ``exploration_only`` and unqualified,
while qualification output reaches at most ``qualification_pending_review``.
Automatic promotion to accepted evidence is intentionally never performed.
"""

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .errors import ValidationError
from .materials import TRACEABLE_SOURCE_TYPES
from .model import (
    ApprovalState,
    EvidenceDisposition,
    RequirementStatus,
    ResultMode,
    ReviewState,
    ValidityState,
)


GATE_SPECS = (
    ("METHOD_APPROVED", "Method approved for qualification"),
    ("GEOMETRY_APPROVED", "Geometry closed, reviewed, repairs reviewed"),
    ("MATERIALS_APPROVED", "All materials approved with traceable provenance"),
    ("LOADCASE_PINNED", "Load case pinned, reviewed, with acceptance requirement"),
    ("FIXTURES_REVIEWED", "All fixtures reviewed"),
    ("TOLERANCE_PROFILE", "Tolerance profile available"),
    ("SOLVER_CAPABLE", "Solver capabilities satisfy the method"),
    ("CONVERGENCE", "Solver convergence evidence"),
    ("FORCE_BALANCE", "Force balance evidence"),
    ("CORRELATION", "Required correlation records exist and reviewed"),
    ("REQUIREMENT_ACTIVE", "Governing requirement active with acceptance criterion"),
    ("NO_BLOCKING_ISSUES", "No blocking validation issues"),
    ("ANALYSIS_VALIDITY", "Underlying analysis validity is clean"),
    ("IMPACT_VALIDITY", "Impact state is not blocked and carries no unsupported modes"),
    ("CORRELATION_ERROR", "Correlation error fractions within policy maximum"),
    ("REQUIREMENT_EVALUATION", "Structured requirement targets evaluate to pass"),
    ("CONVERGENCE_EVIDENCE", "Claimed convergence/force-balance evidence is substantiated"),
)

_GATE_LABELS = dict(GATE_SPECS)


@dataclass(frozen=True)
class QualificationGate:
    """A single qualification readiness check."""

    key: str = ""
    label: str = ""
    passed: bool = False
    evaluable: bool = True
    blocker: bool = False
    explanation: str = ""

    def to_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "passed": self.passed,
            "evaluable": self.evaluable,
            "blocker": self.blocker,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class QualificationResult:
    """Immutable outcome of a full qualification evaluation."""

    mode: str = "qualification"
    qualified: bool = False
    evidence_disposition: str = EvidenceDisposition.QUALIFICATION_BLOCKED.value
    gates: Tuple[QualificationGate, ...] = ()
    blocking_keys: Tuple[str, ...] = ()
    summary: str = ""
    integrity_gates: Tuple[QualificationGate, ...] = ()
    requirement_evaluations: Tuple[Mapping, ...] = ()
    convergence_evidence: bool = False
    force_balance: bool = False
    structural_validity: Optional[str] = None

    def to_dict(self):
        return {
            "mode": self.mode,
            "qualified": self.qualified,
            "evidence_disposition": self.evidence_disposition,
            "gates": [gate.to_dict() for gate in self.gates],
            "blocking_keys": list(self.blocking_keys),
            "summary": self.summary,
            "integrity_gates": [gate.to_dict() for gate in self.integrity_gates],
            "requirement_evaluations": [dict(item) for item in self.requirement_evaluations],
            "convergence_evidence": self.convergence_evidence,
            "force_balance": self.force_balance,
            "structural_validity": self.structural_validity,
        }


def _get(value, *names, default=None):
    """Read a field from a model object or a mapping; first hit wins."""
    for name in names:
        if isinstance(value, Mapping):
            result = value.get(name, default)
        else:
            result = getattr(value, name, default)
        if result is not None:
            return result
    return default


def _flag(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in (
            "1", "true", "yes", "on", "approved", "closed", "active", "reviewed"
        )
    return default if value is None else bool(value)


def _enum(cls, value, default=None):
    if value is None or isinstance(value, cls):
        return value
    if isinstance(value, str):
        try:
            return cls(value)
        except ValueError:
            return default
    return default


def _mode(mode):
    if isinstance(mode, ResultMode):
        return mode.value
    if isinstance(mode, str):
        value = mode.strip().casefold()
        if value in ("exploration", "qualification"):
            return value
    raise ValidationError("mode must be 'exploration' or 'qualification'")


def _review_flag(flags, *names):
    if not isinstance(flags, Mapping):
        return False
    for name in names:
        value = flags.get(name)
        if value is not None:
            return _flag(value)
    return False


def _gate(key, passed=False, evaluable=True, blocker=False, explanation=""):
    return QualificationGate(
        key=key,
        label=_GATE_LABELS.get(key, key),
        passed=bool(passed),
        evaluable=bool(evaluable),
        blocker=bool(blocker),
        explanation=explanation,
    )


def _method_gate(method):
    """METHOD_APPROVED: approved_for_qualification and approved state."""
    if method is None:
        return _gate("METHOD_APPROVED", False, False, True, "no analysis method provided")
    approved = bool(_get(method, "approved_for_qualification", default=False))
    state = _enum(ApprovalState, _get(method, "approval_state"), ApprovalState.DRAFT)
    if approved and state == ApprovalState.APPROVED:
        return _gate("METHOD_APPROVED", True, True, True, "method is approved for qualification")
    reason = (
        "method is not approved for qualification"
        if not approved
        else "method approval_state is not approved"
    )
    return _gate("METHOD_APPROVED", False, True, True, reason)


def _geometry_gate(geometry, reviewed_flags):
    """GEOMETRY_APPROVED: closed, reviewed, repairs reviewed."""
    if geometry is None:
        return _gate("GEOMETRY_APPROVED", False, False, True, "geometry is missing")
    health = _get(geometry, "geometry_health")
    closed = _get(geometry, "closed")
    if closed is None and health is not None:
        solid_status = str(_get(health, "solid_status", default="") or "")
        closed = solid_status.casefold() == "closed"
    if closed is None:
        closed = False
    closed = bool(closed)
    reviewed = _get(geometry, "reviewed")
    if reviewed is None and health is not None:
        reviewed = _get(health, "reviewed")
    if reviewed is None:
        reviewed = _review_flag(reviewed_flags, "geometry_reviewed", "geometry", "GEOMETRY_APPROVED")
    reviewed = bool(reviewed)
    repairs_reviewed = _get(geometry, "repairs_reviewed")
    if repairs_reviewed is None:
        repair_records = _get(geometry, "repair_records", default=()) or ()
        repair_required = _get(health, "repair_required") if health is not None else None
        repairs_reviewed = all(bool(_get(record, "reviewed", default=False)) for record in repair_records)
        if repair_required and not repair_records:
            repairs_reviewed = False
    repairs_reviewed = bool(repairs_reviewed)
    failures = []
    if not closed:
        failures.append("geometry is not closed")
    if not reviewed:
        failures.append("geometry is not reviewed")
    if not repairs_reviewed:
        failures.append("geometry repairs are not reviewed")
    explanation = "; ".join(failures) if failures else (
        "geometry is closed, reviewed, and repairs are reviewed"
    )
    return _gate("GEOMETRY_APPROVED", passed=not failures, evaluable=True, blocker=True, explanation=explanation)


def _materials(materials):
    if materials is None:
        return ()
    if isinstance(materials, Mapping):
        if any(name in materials for name in ("approval_state", "meta", "properties", "definition")):
            return (materials,)
        return tuple(materials.values())
    if isinstance(materials, (str, bytes)):
        return (materials,)
    return tuple(materials)


def _provenance_traceable(provenance):
    if provenance is None:
        return False
    source_type = str(_get(provenance, "source_type", default="") or "").casefold()
    if source_type not in TRACEABLE_SOURCE_TYPES:
        return False
    if not _get(provenance, "source_id", default=None):
        return False
    if not _get(provenance, "condition", "conditioning", default=None):
        return False
    confidence = str(_get(provenance, "confidence", default="") or "").casefold()
    return confidence in ("medium", "high")


def _materials_gate(materials):
    """MATERIALS_APPROVED: all approved with traceable provenance."""
    items = _materials(materials)
    if not items:
        return _gate("MATERIALS_APPROVED", False, False, True, "no materials supplied")
    failures = []
    for index, material in enumerate(items):
        state = _enum(ApprovalState, _get(material, "approval_state"), ApprovalState.DRAFT)
        if state != ApprovalState.APPROVED:
            failures.append("materials[{}] is not approved".format(index))
        if not _provenance_traceable(_get(material, "provenance")):
            failures.append("materials[{}] lacks traceable provenance".format(index))
    explanation = "; ".join(failures) if failures else (
        "all materials are approved with traceable provenance"
    )
    return _gate("MATERIALS_APPROVED", passed=not failures, evaluable=True, blocker=True, explanation=explanation)


def _load_case_gate(load_case, reviewed_flags):
    """LOADCASE_PINNED: present, reviewed, has acceptance requirement."""
    if load_case is None:
        return _gate("LOADCASE_PINNED", False, False, True, "load case is missing")
    reviewed = _get(load_case, "reviewed")
    if reviewed is None:
        reviewed = _review_flag(reviewed_flags, "load_case_reviewed", "loadcase_reviewed", "LOADCASE_PINNED")
    reviewed = bool(reviewed)
    refs = _get(load_case, "acceptance_requirement_refs", "acceptance_requirement_ref", default=()) or ()
    failures = []
    if not reviewed:
        failures.append("load case is not reviewed")
    if not refs:
        failures.append("load case has no acceptance requirement")
    explanation = "; ".join(failures) if failures else (
        "load case is pinned, reviewed, and has an acceptance requirement"
    )
    return _gate("LOADCASE_PINNED", passed=not failures, evaluable=True, blocker=True, explanation=explanation)


def _fixtures(fixtures):
    if fixtures is None:
        return ()
    if isinstance(fixtures, Mapping):
        if any(name in fixtures for name in ("reviewed", "fixture_type", "meta", "name", "target_region_ref")):
            return (fixtures,)
        return tuple(fixtures.values())
    return tuple(fixtures)


def _fixtures_gate(fixtures):
    """FIXTURES_REVIEWED: all fixtures reviewed."""
    items = _fixtures(fixtures)
    if not items:
        return _gate("FIXTURES_REVIEWED", False, False, True, "no fixtures supplied")
    unreviewed = [
        index for index, fixture in enumerate(items)
        if not bool(_get(fixture, "reviewed", default=False))
    ]
    explanation = (
        "; ".join("fixtures[{}] is not reviewed".format(index) for index in unreviewed)
        if unreviewed else "all fixtures are reviewed"
    )
    return _gate("FIXTURES_REVIEWED", passed=not unreviewed, evaluable=True, blocker=True, explanation=explanation)


def _required_capabilities(method):
    keys = _get(method, "required_capability_keys", "required_capabilities", default=None)
    if keys is None:
        return frozenset()
    return frozenset(str(key).casefold() for key in keys)


def method_supports(method, capability_keys):
    """Return whether ``capability_keys`` satisfy the method's requirements."""
    if method is None:
        return False
    required = _required_capabilities(method)
    capabilities = frozenset(str(key).casefold() for key in (capability_keys or ()))
    return required.issubset(capabilities)


def _policy_flag(method, policy_name, field, default=False):
    policy = _get(method, policy_name)
    if policy is None:
        return default
    return _flag(_get(policy, field, default=default), default=default)


def _tolerance_gate(method, tolerance_profile):
    """TOLERANCE_PROFILE: required by method and supplied."""
    if method is None:
        return _gate("TOLERANCE_PROFILE", False, False, True, "no analysis method provided")
    if not _flag(_get(method, "requires_tolerance_profile", default=False)):
        return _gate("TOLERANCE_PROFILE", True, True, True, "tolerance profile not required by method")
    if tolerance_profile is None:
        return _gate("TOLERANCE_PROFILE", False, False, True, "tolerance profile is missing")
    return _gate("TOLERANCE_PROFILE", True, True, True, "tolerance profile supplied")


def _solver_gate(method, solver):
    """SOLVER_CAPABLE: capability keys satisfy the method."""
    if method is None:
        return _gate("SOLVER_CAPABLE", False, False, True, "no analysis method provided")
    if solver is None:
        return _gate("SOLVER_CAPABLE", False, False, True, "solver is missing")
    capability_keys = _get(solver, "capability_keys", default=()) or ()
    if method_supports(method, capability_keys):
        return _gate("SOLVER_CAPABLE", True, True, True, "solver capabilities satisfy the method")
    missing = sorted(
        _required_capabilities(method) - frozenset(str(key).casefold() for key in capability_keys)
    )
    return _gate(
        "SOLVER_CAPABLE", False, True, True,
        "solver capability keys do not satisfy the method: missing {}".format(", ".join(missing)),
    )


def _evidence_gate(key, method, solver_policy_field, evidence):
    """CONVERGENCE / FORCE_BALANCE: required by solver policy and evidenced."""
    if method is None:
        return _gate(key, False, False, True, "no analysis method provided")
    if not _policy_flag(method, "solver_policy", solver_policy_field, default=False):
        return _gate(key, True, True, True, "not required by method solver policy")
    if evidence:
        label = key.casefold().replace("_", " ")
        return _gate(key, True, True, True, "{} evidence supplied".format(label))
    label = key.casefold().replace("_", " ")
    return _gate(key, False, False, True, "{} evidence is missing".format(label))


def _correlation_records(records):
    if records is None:
        return ()
    if isinstance(records, Mapping):
        if any(name in records for name in ("record_type", "meta", "review_state", "specimen")):
            return (records,)
        return tuple(records.values())
    if isinstance(records, (str, bytes)):
        return (records,)
    return tuple(records)


def _record_reviewed(record):
    return _enum(ReviewState, _get(record, "review_state"), ReviewState.UNREVIEWED) == ReviewState.APPROVED


def _correlation_gate(method, correlation_records):
    """CORRELATION: required records exist and are reviewed."""
    if method is None:
        return _gate("CORRELATION", False, False, True, "no analysis method provided")
    policy = _get(method, "required_correlation_policy")
    if not _flag(_get(policy, "required", default=False)):
        return _gate("CORRELATION", True, True, True, "correlation not required by method")
    records = _correlation_records(correlation_records)
    if not records:
        return _gate("CORRELATION", False, False, True, "required correlation records are missing")
    record_types = frozenset(str(_get(record, "record_type", default="") or "") for record in records)
    required_types = tuple(_get(policy, "required_record_types", default=()) or ())
    missing_types = [record_type for record_type in required_types if record_type not in record_types]
    require_reviewed = _flag(_get(policy, "require_reviewed_records", default=True))
    unreviewed = [
        str(_get(record, "record_type", default="") or "")
        for record in records if not _record_reviewed(record)
    ]
    failures = []
    if missing_types:
        failures.append("missing record types: {}".format(", ".join(missing_types)))
    if require_reviewed and unreviewed:
        failures.append("unreviewed records: {}".format(", ".join(unreviewed)))
    explanation = "; ".join(failures) if failures else (
        "required correlation records exist and are reviewed"
    )
    return _gate("CORRELATION", passed=not failures, evaluable=True, blocker=True, explanation=explanation)


def _requirement_gate(requirement):
    """REQUIREMENT_ACTIVE: active status plus acceptance criterion."""
    if requirement is None:
        return _gate("REQUIREMENT_ACTIVE", False, False, True, "governing requirement is missing")
    status = _enum(RequirementStatus, _get(requirement, "status"), RequirementStatus.DRAFT)
    acceptance = _get(requirement, "acceptance")
    structured = _structured_targets(requirement)
    failures = []
    if status != RequirementStatus.ACTIVE:
        failures.append("requirement status is not active")
    if acceptance is None and not structured:
        failures.append("requirement has no acceptance criterion")
    explanation = "; ".join(failures) if failures else (
        "requirement is active with an acceptance criterion"
    )
    return _gate("REQUIREMENT_ACTIVE", passed=not failures, evaluable=True, blocker=True, explanation=explanation)


def _validation_gate(validation_report):
    """NO_BLOCKING_ISSUES: no findings with evidence_blocking."""
    if validation_report is None:
        return _gate("NO_BLOCKING_ISSUES", False, False, True, "no validation report provided")
    if isinstance(validation_report, Mapping):
        findings = validation_report.get("findings", ())
    else:
        findings = validation_report
    blocking = [
        finding for finding in findings or ()
        if _flag(_get(finding, "evidence_blocking", default=False))
    ]
    if blocking:
        codes = ", ".join(
            str(_get(finding, "code", "evidence_blocking")) for finding in blocking
        )
        return _gate(
            "NO_BLOCKING_ISSUES", False, True, True,
            "validation report contains blocking findings: {}".format(codes),
        )
    return _gate("NO_BLOCKING_ISSUES", True, True, True, "validation report has no blocking findings")


def _numeric(value):
    """Return a finite float from a plain number, quantity, or value dict."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Mapping):
        for key in ("value_si", "value"):
            if key in value:
                return _numeric(value.get(key))
        return None
    for attr in ("value_si", "value"):
        if hasattr(value, attr):
            return _numeric(getattr(value, attr))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _structural_validity(structural_response):
    """Return ``(validity_state_or_None, unsupported_modes)`` for a response."""
    if structural_response is None:
        return None, ()
    unsupported = []
    validity = _get(structural_response, "validity")
    if isinstance(validity, Mapping):
        state = _enum(ValidityState, validity.get("state"), None)
        unsupported.extend(_get(validity, "unsupported_failure_modes", default=()) or ())
    else:
        state = _enum(ValidityState, _get(validity, "state", default=validity), None)
        if validity is not None:
            unsupported.extend(_get(validity, "unsupported_failure_modes", default=()) or ())
    unsupported.extend(_get(structural_response, "unsupported_failure_modes", default=()) or ())
    return state, tuple(sorted({str(item) for item in unsupported}))


def _analysis_validity_gate(structural_response, validation_report):
    """ANALYSIS_VALIDITY: the underlying analysis must be valid and complete."""
    failures = []
    if structural_response is not None:
        state, unsupported = _structural_validity(structural_response)
        if state is None:
            failures.append("structural response carries no validity state")
        elif state != ValidityState.VALID:
            failures.append("structural response validity is {}".format(state.value))
        if unsupported:
            failures.append(
                "structural response carries unsupported failure modes: {}".format(
                    ", ".join(unsupported)
                )
            )
    if validation_report is not None:
        report_status = str(_get(validation_report, "status", default="") or "").casefold()
        if report_status == "fail":
            failures.append("validation report status is fail")
    if failures:
        return _gate("ANALYSIS_VALIDITY", False, True, True, "; ".join(failures))
    explanation = (
        "underlying analysis is valid with no unsupported failure modes"
        if structural_response is not None
        else "no structural analysis performed; no validity constraints apply"
    )
    return _gate("ANALYSIS_VALIDITY", True, True, True, explanation)


def _impact_gate(impact):
    """IMPACT_VALIDITY: impact-based qualification needs an unblocked, clean impact."""
    if impact is None:
        return _gate("IMPACT_VALIDITY", True, True, True, "impact analysis not requested")
    failures = []
    impact_result = _get(impact, "result")
    if impact_result is None:
        failures.append(
            str(_get(impact, "reason", default="") or "") or "impact result is missing"
        )
    else:
        if _flag(_get(impact_result, "qualification_blocked", default=False)):
            failures.append("impact result is qualification_blocked")
        unsupported = list(_get(impact, "unsupported_failure_modes", default=()) or ())
        unsupported.extend(_get(impact_result, "unsupported_failure_modes", default=()) or ())
        unsupported = sorted({str(item) for item in unsupported})
        if unsupported:
            failures.append(
                "impact analysis carries unsupported failure modes: {}".format(
                    ", ".join(unsupported)
                )
            )
        validity = str(_get(impact_result, "validity", default="") or "").casefold()
        if validity and validity not in ("valid", "no_impact"):
            failures.append("impact result validity is {}".format(validity))
    if failures:
        return _gate("IMPACT_VALIDITY", False, True, True, "; ".join(failures))
    return _gate("IMPACT_VALIDITY", True, True, True, "impact state is clean and unblocked")


def _comparison_error_fraction(comparison):
    """Measured-to-predicted error fraction of a comparison, or None."""
    if comparison is None:
        return None
    relative = _get(comparison, "relative_error")
    if relative is not None:
        value = _numeric(relative)
        if value is not None:
            return abs(value)
    measured = _numeric(_get(comparison, "measured"))
    predicted = _numeric(_get(comparison, "predicted"))
    if measured is None or predicted is None or predicted == 0.0:
        return None
    return abs(measured - predicted) / abs(predicted)


def _correlation_error_gate(method, correlation_records):
    """CORRELATION_ERROR: error fractions must not exceed the policy maximum."""
    if method is None:
        return _gate("CORRELATION_ERROR", False, False, True, "no analysis method provided")
    policy = _get(method, "required_correlation_policy")
    if not _flag(_get(policy, "required", default=False)):
        return _gate("CORRELATION_ERROR", True, True, True, "correlation not required by method")
    maximum = _numeric(_get(policy, "maximum_error_fraction"))
    if maximum is None:
        return _gate(
            "CORRELATION_ERROR", True, True, True, "no maximum error fraction configured"
        )
    records = _correlation_records(correlation_records)
    if not records:
        return _gate("CORRELATION_ERROR", True, True, True, "no correlation records supplied")
    violations = []
    for record in records:
        record_type = str(_get(record, "record_type", default="") or "")
        for comparison in _get(record, "comparisons", default=()) or ():
            fraction = _comparison_error_fraction(comparison)
            if fraction is None or fraction <= maximum:
                continue
            metric = str(_get(comparison, "metric_key", default="") or "") or "metric"
            violations.append(
                "{}[{}]: error fraction {:.6g} exceeds maximum {:.6g}".format(
                    record_type, metric, fraction, maximum
                )
            )
    if violations:
        return _gate(
            "CORRELATION_ERROR", False, True, True,
            "correlation error fractions exceed the policy maximum: {}".format(
                "; ".join(violations)
            ),
        )
    return _gate(
        "CORRELATION_ERROR", True, True, True,
        "correlation error fractions are within the policy maximum",
    )


_METRIC_PATHS = {
    "mass_kg": ("mass", "mass_kg"),
    "max_displacement_m": ("structural", "response", "max_displacement_m"),
    "max_stress_pa": ("structural", "response", "max_stress_pa"),
    "safety_factor": ("structural", "response", "safety_factor"),
    "peak_force_n": ("impact", "result", "peak_force_n"),
}


def _resolve_metric(pipeline_result, metric):
    """Resolve a metric name against the pipeline result, or return None."""
    if pipeline_result is None:
        return None
    path = _METRIC_PATHS.get(metric)
    if path is None:
        path = tuple(str(metric).split("."))
    node = pipeline_result
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _structured_targets(requirement):
    """Collect structured ``{"metric": ..., "max"/"min": ...}`` targets."""
    if requirement is None:
        return ()
    targets = []
    raw_targets = _get(requirement, "targets", "target")
    if raw_targets is not None:
        candidates = raw_targets if isinstance(raw_targets, (list, tuple)) else (raw_targets,)
        targets.extend(candidates)
    acceptance = _get(requirement, "acceptance")
    if isinstance(acceptance, Mapping):
        targets.append(acceptance)
    return tuple(
        target for target in targets
        if isinstance(target, Mapping)
        and "metric" in target
        and any(bound in target for bound in ("max", "min"))
    )


def _evaluate_target(target, pipeline_result):
    """Evaluate one structured target; never pretend a missing value passed."""
    metric = str(_get(target, "metric", default="") or "")
    entry = {"metric": metric, "status": "pass"}
    measured = _numeric(_resolve_metric(pipeline_result, metric))
    if measured is None:
        entry["status"] = "not_available"
        entry["measured"] = None
        entry["reason"] = "measured value unavailable for metric {!r}".format(metric)
        return entry
    entry["measured"] = measured
    margins = {}
    if "max" in target:
        limit = _numeric(_get(target, "max"))
        if limit is None:
            entry["status"] = "not_available"
            entry["reason"] = "max bound is not numeric"
            return entry
        entry["max"] = limit
        margins["max"] = limit - measured
        if measured > limit:
            entry["status"] = "fail"
    if "min" in target:
        limit = _numeric(_get(target, "min"))
        if limit is None:
            entry["status"] = "not_available"
            entry["reason"] = "min bound is not numeric"
            return entry
        entry["min"] = limit
        margins["min"] = measured - limit
        if measured < limit:
            entry["status"] = "fail"
    if margins:
        entry["margins"] = margins
    return entry


def _evaluate_requirement(requirement, pipeline_result):
    """Emit a pass/fail/not_evaluated evaluation for one requirement."""
    targets = _structured_targets(requirement)
    label = str(_get(requirement, "external_id", default="") or "")
    if not label:
        label = str(_get(requirement, "title", default="") or "")
    if not label:
        label = "requirement"
    if not targets:
        return {
            "requirement": label,
            "status": "not_evaluated",
            "reason": "requirement carries no structured metric target",
            "targets": [],
        }
    evaluated = [_evaluate_target(target, pipeline_result) for target in targets]
    statuses = [item["status"] for item in evaluated]
    if "fail" in statuses:
        status = "fail"
    elif "not_available" in statuses:
        status = "not_available"
    else:
        status = "pass"
    return {"requirement": label, "status": status, "targets": evaluated}


def _requirement_evaluation_gate(evaluations):
    """REQUIREMENT_EVALUATION: structured targets must measure to pass."""
    failures = []
    for evaluation in evaluations:
        status = evaluation.get("status")
        label = str(evaluation.get("requirement", "requirement"))
        if status == "fail":
            failures.append("requirement {!r} failed its target(s)".format(label))
        elif status == "not_available":
            failures.append("requirement {!r} target(s) could not be measured".format(label))
    if failures:
        return _gate("REQUIREMENT_EVALUATION", False, True, True, "; ".join(failures))
    evaluated = [item for item in evaluations if item.get("status") != "not_evaluated"]
    explanation = (
        "all evaluated requirements pass"
        if evaluated else "no structured requirement targets to evaluate"
    )
    return _gate("REQUIREMENT_EVALUATION", True, True, True, explanation)


def _convergence_evidence_gate(convergence_evidence, force_balance, structural_response):
    """CONVERGENCE_EVIDENCE: claimed evidence must be backed by a valid response."""
    claims = []
    if convergence_evidence:
        claims.append("convergence")
    if force_balance:
        claims.append("force balance")
    if not claims:
        return _gate(
            "CONVERGENCE_EVIDENCE", True, True, True,
            "no convergence or force-balance evidence claimed",
        )
    label = " and ".join(claims)
    if structural_response is None:
        return _gate(
            "CONVERGENCE_EVIDENCE", False, True, True,
            "{} evidence claimed without a structural response; cannot be substantiated".format(label),
        )
    state, _ = _structural_validity(structural_response)
    if state != ValidityState.VALID:
        return _gate(
            "CONVERGENCE_EVIDENCE", False, True, True,
            "{} evidence claimed but structural response validity is {}".format(
                label, state.value if state is not None else "unknown"
            ),
        )
    return _gate(
        "CONVERGENCE_EVIDENCE", True, True, True,
        "{} evidence substantiated by a valid structural response".format(label),
    )


def evaluate_qualification(
    mode,
    method=None,
    geometry=None,
    materials=None,
    load_case=None,
    fixtures=None,
    tolerance_profile=None,
    correlation_records=None,
    requirement=None,
    validation_report=None,
    solver=None,
    convergence_evidence=False,
    force_balance=False,
    reviewed_flags=None,
    structural_response=None,
    impact=None,
    requirements=None,
    pipeline_result=None,
    **kwargs,
):
    """Evaluate the hard qualification gate set for a mode.

    All gates are evaluated in both modes.  Exploration output is always
    ``exploration_only`` and unqualified; qualification output reaches at
    most ``qualification_pending_review``.  Nothing here ever promotes
    evidence to accepted status.

    The integrity gate set additionally hard-blocks whenever the underlying
    analysis is invalid, incomplete, or unsupported: an inconclusive or
    failed structural response, unsupported failure modes, a failed
    validation report, a qualification-blocked or unsupported impact result,
    correlation error fractions beyond the policy maximum, unmeasurable or
    failing structured requirement targets, or claimed convergence/force
    balance evidence that a valid structural response cannot substantiate.
    """
    mode_value = _mode(mode)
    if requirements is None:
        requirement_list = (requirement,) if requirement is not None else ()
    elif isinstance(requirements, (list, tuple)):
        requirement_list = tuple(requirements)
    else:
        requirement_list = (requirements,)
    governing_requirement = requirement
    if governing_requirement is None and requirement_list:
        governing_requirement = requirement_list[0]
    requirement_evaluations = tuple(
        _evaluate_requirement(item, pipeline_result) for item in requirement_list
    )
    gates = [
        _method_gate(method),
        _geometry_gate(geometry, reviewed_flags),
        _materials_gate(materials),
        _load_case_gate(load_case, reviewed_flags),
        _fixtures_gate(fixtures),
        _tolerance_gate(method, tolerance_profile),
        _solver_gate(method, solver),
        _evidence_gate("CONVERGENCE", method, "require_convergence", convergence_evidence),
        _evidence_gate("FORCE_BALANCE", method, "require_force_balance", force_balance),
        _correlation_gate(method, correlation_records),
        _requirement_gate(governing_requirement),
        _validation_gate(validation_report),
    ]
    integrity_gates = [
        _analysis_validity_gate(structural_response, validation_report),
        _impact_gate(impact),
        _correlation_error_gate(method, correlation_records),
        _requirement_evaluation_gate(requirement_evaluations),
        _convergence_evidence_gate(convergence_evidence, force_balance, structural_response),
    ]
    gates = tuple(sorted(gates, key=lambda gate: gate.key))
    integrity_gates = tuple(sorted(integrity_gates, key=lambda gate: gate.key))
    all_gates = gates + integrity_gates
    blocking_keys = tuple(
        gate.key for gate in all_gates if (not gate.evaluable) or (gate.blocker and not gate.passed)
    )
    if mode_value == "exploration":
        qualified = False
        disposition = EvidenceDisposition.EXPLORATION_ONLY.value
    elif blocking_keys:
        qualified = False
        disposition = EvidenceDisposition.QUALIFICATION_BLOCKED.value
    else:
        qualified = True
        disposition = EvidenceDisposition.QUALIFICATION_PENDING_REVIEW.value
    if mode_value == "exploration":
        summary = "exploration mode: evidence disposition is exploration_only; results cannot qualify"
    elif blocking_keys:
        summary = "qualification blocked by {} gate(s): {}".format(
            len(blocking_keys), ", ".join(blocking_keys)
        )
    else:
        summary = "qualification pending review: all {} gates passed".format(len(all_gates))
    structural_validity, _ = _structural_validity(structural_response)
    return QualificationResult(
        mode=mode_value,
        qualified=qualified,
        evidence_disposition=disposition,
        gates=gates,
        blocking_keys=blocking_keys,
        summary=summary,
        integrity_gates=integrity_gates,
        requirement_evaluations=requirement_evaluations,
        convergence_evidence=bool(convergence_evidence),
        force_balance=bool(force_balance),
        structural_validity=(
            structural_validity.value if structural_validity is not None else None
        ),
    )


def impact_qualification_status(method=None, validated=False):
    """Impact/energy methods are blocked unless validated and method approved."""
    validated = bool(validated)
    if method is None:
        return {
            "eligible": False,
            "blocked": True,
            "impact_energy": False,
            "method_approved": False,
            "validated": validated,
            "reason": "no analysis method provided; impact/energy qualification is blocked",
        }
    method_type = str(_get(method, "method_type", default="") or "").casefold()
    impact_energy = "impact" in method_type or "energy" in method_type
    approved = bool(_get(method, "approved_for_qualification", default=False)) and (
        _enum(ApprovalState, _get(method, "approval_state"), ApprovalState.DRAFT)
        == ApprovalState.APPROVED
    )
    if not impact_energy:
        eligible = approved
        reason = (
            "method is approved for qualification"
            if eligible else "method is not approved for qualification"
        )
    elif not approved:
        eligible = False
        reason = "impact/energy method is not approved for qualification"
    elif not validated:
        eligible = False
        reason = "impact/energy method requires validated evidence"
    else:
        eligible = True
        reason = "impact/energy method is approved and validated"
    return {
        "eligible": eligible,
        "blocked": not eligible,
        "impact_energy": impact_energy,
        "method_approved": approved,
        "validated": validated,
        "reason": reason,
    }


__all__ = [
    "GATE_SPECS",
    "QualificationGate",
    "QualificationResult",
    "evaluate_qualification",
    "method_supports",
    "impact_qualification_status",
]
