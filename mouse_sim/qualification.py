"""Hard exploration/qualification separation.

Qualification gates evaluate readiness evidence without ever promoting a
result: exploration output is always ``exploration_only`` and unqualified,
while qualification output reaches at most ``qualification_pending_review``.
Automatic promotion to accepted evidence is intentionally never performed.
"""

from dataclasses import dataclass
from typing import Mapping, Tuple

from .errors import ValidationError
from .materials import TRACEABLE_SOURCE_TYPES
from .model import (
    ApprovalState,
    EvidenceDisposition,
    RequirementStatus,
    ResultMode,
    ReviewState,
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

    def to_dict(self):
        return {
            "mode": self.mode,
            "qualified": self.qualified,
            "evidence_disposition": self.evidence_disposition,
            "gates": [gate.to_dict() for gate in self.gates],
            "blocking_keys": list(self.blocking_keys),
            "summary": self.summary,
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
    failures = []
    if status != RequirementStatus.ACTIVE:
        failures.append("requirement status is not active")
    if acceptance is None:
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
    **kwargs,
):
    """Evaluate the hard qualification gate set for a mode.

    All gates are evaluated in both modes.  Exploration output is always
    ``exploration_only`` and unqualified; qualification output reaches at
    most ``qualification_pending_review``.  Nothing here ever promotes
    evidence to accepted status.
    """
    mode_value = _mode(mode)
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
        _requirement_gate(requirement),
        _validation_gate(validation_report),
    ]
    gates = tuple(sorted(gates, key=lambda gate: gate.key))
    blocking_keys = tuple(
        gate.key for gate in gates if (not gate.evaluable) or (gate.blocker and not gate.passed)
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
        summary = "qualification pending review: all {} gates passed".format(len(gates))
    return QualificationResult(
        mode=mode_value,
        qualified=qualified,
        evidence_disposition=disposition,
        gates=gates,
        blocking_keys=blocking_keys,
        summary=summary,
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
