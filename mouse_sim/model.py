"""Versioned, serializable project and analysis data models.

This module intentionally contains data and validation primitives only.  It
does not import a geometry kernel, solver, pipeline, or UI dependency.
"""

from dataclasses import dataclass, field, fields, replace
from enum import Enum
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union, get_args, get_origin, get_type_hints

from .errors import SerializationError, ValidationError
from .units import from_si, normalize_unit, to_si, unit_dimension


SCHEMA_VERSION = 1
SCHEMA_ID = "gms.project-document"


class StringEnum(str, Enum):
    """Enum whose serialized representation is its stable string value."""

    def __str__(self):
        return self.value


class ResultMode(StringEnum):
    EXPLORATION = "exploration"
    QUALIFICATION = "qualification"


class ValidityState(StringEnum):
    VALID = "valid"
    APPROXIMATE = "approximate"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


class ReviewState(StringEnum):
    UNREVIEWED = "unreviewed"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class LifecycleState(StringEnum):
    DRAFT = "draft"
    PREFLIGHT_FAILED = "preflight_failed"
    PREFLIGHT_PASSED = "preflight_passed"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"
    REVIEWED = "reviewed"


class EvidenceDisposition(StringEnum):
    EXPLORATION_ONLY = "exploration_only"
    QUALIFICATION_BLOCKED = "qualification_blocked"
    QUALIFICATION_PENDING_REVIEW = "qualification_pending_review"
    QUALIFICATION_ACCEPTED = "qualification_accepted"
    QUALIFICATION_REJECTED = "qualification_rejected"
    QUALIFICATION_SUPERSEDED = "qualification_superseded"


class IssueSeverity(StringEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKER = "blocker"


class IssueState(StringEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"


class ApprovalState(StringEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"
    REJECTED = "rejected"


class ResultStatus(StringEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_AVAILABLE = "not_available"


class RequirementStatus(StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    FAILED = "failed"
    WAIVED = "waived"
    OBSOLETE = "obsolete"


def _tuple():
    return field(default_factory=tuple)


def _identity_basis():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class ModelBase:
    """Shared explicit recursive dict serialization for all persisted models."""

    def to_dict(self):
        return _encode(self)

    def to_json_dict(self):
        return self.to_dict()

    @classmethod
    def from_dict(cls, data):
        return _decode_model(cls, data)

    @classmethod
    def from_json_dict(cls, data):
        return cls.from_dict(data)


def _encode(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ModelBase):
        return {item.name: _encode(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("model values must be finite")
        return value
    return value


def _decode(value, hint):
    if hint is Any or hint is None:
        return value
    origin = get_origin(hint)
    args = get_args(hint)
    if origin is Union:
        if value is None and type(None) in args:
            return None
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode(value, option)
            except (TypeError, ValueError, SerializationError):
                continue
        return value
    if origin in (tuple, Tuple):
        if not isinstance(value, (list, tuple)):
            raise SerializationError("expected an array")
        if not args:
            return tuple(value)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(item, args[0]) for item in value)
        return tuple(_decode(item, item_hint) for item, item_hint in zip(value, args))
    if origin in (list, List):
        item_hint = args[0] if args else Any
        return [_decode(item, item_hint) for item in value]
    if origin in (dict, Dict):
        key_hint, value_hint = args if len(args) == 2 else (Any, Any)
        return {
            _decode(key, key_hint): _decode(item, value_hint)
            for key, item in value.items()
        }
    try:
        if isinstance(hint, type) and issubclass(hint, Enum):
            return hint(value)
    except TypeError:
        pass
    if isinstance(hint, type) and issubclass(hint, ModelBase):
        if value is None:
            return None
        return hint.from_dict(value)
    return value


def _decode_model(cls, data):
    if not isinstance(data, Mapping):
        raise SerializationError("{} must deserialize from an object".format(cls.__name__))
    model_fields = {item.name: item for item in fields(cls)}
    unknown = set(data) - set(model_fields)
    if unknown:
        raise SerializationError(
            "{} contains unknown fields: {}".format(cls.__name__, ", ".join(sorted(unknown)))
        )
    try:
        hints = get_type_hints(cls)
    except (NameError, TypeError):
        hints = {item.name: item.type for item in fields(cls)}
    values = {
        name: _decode(data[name], hints.get(name, item.type))
        for name, item in model_fields.items()
        if name in data
    }
    try:
        return cls(**values)
    except (TypeError, ValueError) as exc:
        raise SerializationError("invalid {}: {}".format(cls.__name__, exc))


@dataclass(frozen=True)
class EntityMeta(ModelBase):
    entity_type: str
    schema_version: int = SCHEMA_VERSION
    id: str = ""
    revision: int = 0
    created_at: str = ""
    content_hash: str = ""


@dataclass(frozen=True)
class EntityRef(ModelBase):
    id: str
    content_hash: str = ""


@dataclass(frozen=True)
class ArtifactRef(ModelBase):
    sha256: str
    media_type: str = "application/octet-stream"
    byte_length: int = 0
    relative_path: Optional[str] = None


@dataclass(frozen=True)
class Quantity(ModelBase):
    """A finite SI-normalized quantity with an explicit SI unit."""

    value_si: float
    unit: str

    def __post_init__(self):
        if not math.isfinite(float(self.value_si)):
            raise ValidationError("quantity value must be finite")
        normalize_unit(self.unit)

    @classmethod
    def from_value(cls, value, unit):
        source_unit = normalize_unit(unit)
        value_si = to_si(value, source_unit)
        from .units import si_unit_for_dimension

        return cls(value_si, si_unit_for_dimension(unit_dimension(source_unit)))

    @classmethod
    def from_unit(cls, value, unit):
        return cls.from_value(value, unit)

    def as_unit(self, unit):
        return from_si(self.value_si, unit, unit_dimension(self.unit))

    def to_si(self):
        return self.value_si


@dataclass(frozen=True)
class Provenance(ModelBase):
    source_type: str = "user"
    source_id: Optional[str] = None
    citation: Optional[str] = None
    condition: Optional[str] = None
    temperature_k: Optional[float] = None
    moisture_condition: Optional[str] = None
    strain_rate_s: Optional[float] = None
    confidence: str = "low"


@dataclass(frozen=True)
class Validity(ModelBase):
    state: ValidityState = ValidityState.INCONCLUSIVE
    reasons: Tuple[str, ...] = _tuple()
    assumptions: Tuple[str, ...] = _tuple()
    unsupported_failure_modes: Tuple[str, ...] = _tuple()
    confidence: str = "low"


@dataclass(frozen=True)
class UnitPolicy(ModelBase):
    internal_system: str = "SI"
    display_length_unit: str = "mm"
    display_mass_unit: str = "g"
    display_force_unit: str = "N"
    display_pressure_unit: str = "MPa"
    absolute_length_tolerance_m: float = 1e-6
    relative_tolerance: float = 1e-6


@dataclass(frozen=True)
class TolerancePolicy(ModelBase):
    name: str = "default"
    process_profile: str = "unspecified"
    bilateral_dimensions_m: Tuple[float, ...] = _tuple()
    clearance_margin_m: float = 0.0
    assembly_stackup_enabled: bool = True
    statistical_analysis_enabled: bool = False


ToleranceProfile = TolerancePolicy


@dataclass(frozen=True)
class GeometryHealth(ModelBase):
    representation: str = "unknown"
    solid_status: str = "unknown"
    body_count: int = 0
    repair_required: bool = False
    reviewed: bool = False
    diagnostic_issue_refs: Tuple[EntityRef, ...] = _tuple()


@dataclass(frozen=True)
class RepairRecord(ModelBase):
    operation: str = ""
    target_signature: str = ""
    changed_geometry: bool = False
    reviewed: bool = False
    affects_analysis: bool = False
    description: str = ""


@dataclass(frozen=True)
class GeometryAsset(ModelBase):
    meta: EntityMeta
    asset_kind: str = "source"
    source_format: str = ""
    source_artifact: Optional[ArtifactRef] = None
    derived_from_ref: Optional[EntityRef] = None
    source_units: str = ""
    normalized_to_si: bool = False
    coordinate_frame_ref: Optional[EntityRef] = None
    geometry_health: GeometryHealth = field(default_factory=GeometryHealth)
    repair_records: Tuple[RepairRecord, ...] = _tuple()
    immutable: bool = True


@dataclass(frozen=True)
class RegionRef(ModelBase):
    component_ref: Optional[EntityRef] = None
    semantic_name: Optional[str] = None
    geometry_signature: Optional[str] = None
    selector_type: str = "semantic"
    selector_value: str = ""
    reviewed: bool = False


@dataclass(frozen=True)
class GeometryRef(ModelBase):
    asset_ref: Optional[EntityRef] = None
    body_id: str = ""
    component_frame_ref: Optional[EntityRef] = None
    region_refs: Tuple[RegionRef, ...] = _tuple()


@dataclass(frozen=True)
class Classification(ModelBase):
    component_type: str = "unresolved"
    source: str = "imported"
    confidence: float = 0.0
    confidence_reasons: Tuple[str, ...] = _tuple()
    review_state: ReviewState = ReviewState.UNREVIEWED


@dataclass(frozen=True)
class MassOverride(ModelBase):
    measured_mass: Optional[Quantity] = None
    uncertainty: Optional[Quantity] = None
    provenance: Provenance = field(default_factory=Provenance)
    reviewed: bool = False


@dataclass(frozen=True)
class Component(ModelBase):
    meta: EntityMeta
    name: str = ""
    classification: Classification = field(default_factory=Classification)
    structural_behavior: str = "solid"
    geometry_refs: Tuple[GeometryRef, ...] = _tuple()
    material_assignment_refs: Tuple[EntityRef, ...] = _tuple()
    parent_component_ref: Optional[EntityRef] = None
    protected_region_names: Tuple[str, ...] = _tuple()
    mass_override: Optional[MassOverride] = None
    active: bool = True


@dataclass(frozen=True)
class MaterialProperties(ModelBase):
    density: Optional[Quantity] = None
    young_modulus: Optional[Quantity] = None
    poissons_ratio: Optional[float] = None
    yield_strength: Optional[Quantity] = None
    ultimate_strength: Optional[Quantity] = None
    tensile_allowable: Optional[Quantity] = None
    compressive_allowable: Optional[Quantity] = None
    shear_allowable: Optional[Quantity] = None
    friction_coefficient: Optional[float] = None
    temperature_min_k: Optional[float] = None
    temperature_max_k: Optional[float] = None
    # Fatigue data: stress amplitude at 1e6 cycles (R ~ 0.1) and Basquin
    # slope k (S = S_1e6 * (N/1e6)^(-1/k)); both must be set together.
    fatigue_strength_at_1e6_pa: Optional[Quantity] = None
    fatigue_exponent_k: Optional[float] = None
    # Directional (orthotropic laminate / flow-oriented polymer) stiffness.
    # E1 is young_modulus; E2/E3/G12/G13 and nu12/nu13 complete the plate data.
    young_modulus_transverse_pa: Optional[Quantity] = None
    young_modulus_thickness_pa: Optional[Quantity] = None
    shear_modulus_xy_pa: Optional[Quantity] = None
    shear_modulus_thickness_pa: Optional[Quantity] = None
    poissons_ratio_xy: Optional[float] = None
    poissons_ratio_xz: Optional[float] = None
    # Weld-line strength knockdown factor (0.4-0.8 for molded thermoplastics).
    weld_line_factor: Optional[float] = None
    # Continuous-use service temperature range (K). The legacy
    # temperature_min_k/max_k remain data-validity fields for the data record.
    continuous_use_temperature_min_k: Optional[float] = None
    continuous_use_temperature_max_k: Optional[float] = None

    def validation_errors(self):
        errors = []
        if self.density is not None and self.density.value_si <= 0:
            errors.append("density must be positive")
        if self.young_modulus is not None and self.young_modulus.value_si <= 0:
            errors.append("young_modulus must be positive")
        if self.poissons_ratio is not None and not (-1.0 < self.poissons_ratio < 0.5):
            errors.append("poissons_ratio must be between -1 and 0.5")
        for name in (
            "yield_strength",
            "ultimate_strength",
            "tensile_allowable",
            "compressive_allowable",
            "shear_allowable",
        ):
            quantity = getattr(self, name)
            if quantity is not None and quantity.value_si <= 0:
                errors.append("{} must be positive".format(name))
        for name in (
            "fatigue_strength_at_1e6_pa",
            "young_modulus_transverse_pa",
            "young_modulus_thickness_pa",
            "shear_modulus_xy_pa",
            "shear_modulus_thickness_pa",
        ):
            quantity = getattr(self, name)
            if quantity is not None and quantity.value_si <= 0:
                errors.append("{} must be positive".format(name))
        if self.fatigue_strength_at_1e6_pa is not None or self.fatigue_exponent_k is not None:
            if self.fatigue_strength_at_1e6_pa is None or self.fatigue_exponent_k is None:
                errors.append(
                    "fatigue_strength_at_1e6_pa and fatigue_exponent_k must be set together"
                )
        for name in ("poissons_ratio_xy", "poissons_ratio_xz"):
            ratio = getattr(self, name)
            if ratio is not None and not (-1.0 < ratio < 0.5):
                errors.append("{} must be between -1 and 0.5".format(name))
        if self.weld_line_factor is not None and not (0.4 <= self.weld_line_factor <= 0.8):
            errors.append("weld_line_factor must be between 0.4 and 0.8")
        if self.friction_coefficient is not None and self.friction_coefficient < 0:
            errors.append("friction_coefficient must be non-negative")
        return tuple(errors)

    def validate(self):
        errors = self.validation_errors()
        if errors:
            raise ValidationError("invalid material properties", errors)
        return self


@dataclass(frozen=True)
class MaterialDefinition(ModelBase):
    meta: EntityMeta
    name: str = ""
    family: str = ""
    properties: MaterialProperties = field(default_factory=MaterialProperties)
    provenance: Provenance = field(default_factory=Provenance)
    approval_state: ApprovalState = ApprovalState.DRAFT
    material_lot: Optional[str] = None
    anisotropy_supported: bool = False


@dataclass(frozen=True)
class MaterialAssignment(ModelBase):
    meta: EntityMeta
    component_ref: Optional[EntityRef] = None
    region_ref: Optional[RegionRef] = None
    material_ref: Optional[EntityRef] = None
    structural_behavior: str = "solid"
    property_overrides: Optional[MaterialProperties] = None
    assignment_provenance: Provenance = field(default_factory=Provenance)
    reviewed: bool = False


@dataclass(frozen=True)
class ReferenceFrame(ModelBase):
    meta: EntityMeta
    name: str = ""
    kind: str = "project"
    parent_ref: Optional[EntityRef] = None
    origin_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    basis: Tuple[Tuple[float, float, float], ...] = field(default_factory=_identity_basis)
    axis_names: Tuple[str, str, str] = ("x", "y", "z")
    handedness: str = "right"
    reviewed: bool = False

    def validation_errors(self, tolerance=1e-6):
        errors = []
        if len(self.origin_m) != 3 or len(self.basis) != 3 or any(len(row) != 3 for row in self.basis):
            return ("frame origin and basis must be 3-dimensional",)
        for row in self.basis:
            if any(not math.isfinite(float(value)) for value in row):
                errors.append("frame basis must be finite")
        for i in range(3):
            norm = sum(self.basis[i][j] ** 2 for j in range(3))
            if abs(norm - 1.0) > tolerance:
                errors.append("frame basis vectors must be unit length")
            for j in range(i):
                dot = sum(self.basis[i][k] * self.basis[j][k] for k in range(3))
                if abs(dot) > tolerance:
                    errors.append("frame basis vectors must be orthogonal")
        determinant = (
            self.basis[0][0] * (self.basis[1][1] * self.basis[2][2] - self.basis[1][2] * self.basis[2][1])
            - self.basis[0][1] * (self.basis[1][0] * self.basis[2][2] - self.basis[1][2] * self.basis[2][0])
            + self.basis[0][2] * (self.basis[1][0] * self.basis[2][1] - self.basis[1][1] * self.basis[2][0])
        )
        if self.handedness == "right" and determinant <= 0:
            errors.append("right-handed frame must have positive determinant")
        if self.handedness == "left" and determinant >= 0:
            errors.append("left-handed frame must have negative determinant")
        return tuple(errors)


@dataclass(frozen=True)
class LoadSpec(ModelBase):
    kind: str = "force"
    region_ref: Optional[RegionRef] = None
    frame_ref: Optional[EntityRef] = None
    magnitude: Optional[Quantity] = None
    vector: Optional[Tuple[float, float, float]] = None
    distribution: str = "uniform"
    point_load: bool = False
    source_description: str = ""


@dataclass(frozen=True)
class InterfaceAssumption(ModelBase):
    first_region_ref: Optional[RegionRef] = None
    second_region_ref: Optional[RegionRef] = None
    behavior: str = "bonded"
    rationale: str = ""
    reviewed: bool = False


@dataclass(frozen=True)
class LoadCase(ModelBase):
    meta: EntityMeta
    name: str = ""
    case_type: str = ""
    reference_frame_ref: Optional[EntityRef] = None
    component_refs: Tuple[EntityRef, ...] = _tuple()
    load_specs: Tuple[LoadSpec, ...] = _tuple()
    fixture_refs: Tuple[EntityRef, ...] = _tuple()
    interface_assumptions: Tuple[InterfaceAssumption, ...] = _tuple()
    acceptance_requirement_refs: Tuple[EntityRef, ...] = _tuple()
    enabled: bool = True


@dataclass(frozen=True)
class Fixture(ModelBase):
    meta: EntityMeta
    name: str = ""
    fixture_type: str = "fixed"
    target_region_ref: Optional[RegionRef] = None
    reference_frame_ref: Optional[EntityRef] = None
    constrained_dofs: Tuple[bool, bool, bool, bool, bool, bool] = (True, True, True, True, True, True)
    stiffness: Optional[Quantity] = None
    semantic_basis: str = ""
    reviewed: bool = False


@dataclass(frozen=True)
class SolverSpec(ModelBase):
    backend: str = ""
    version: str = ""
    capability_keys: Tuple[str, ...] = _tuple()
    configuration: Tuple[Tuple[str, str], ...] = _tuple()
    deterministic: bool = True
    random_seed: Optional[int] = None


@dataclass(frozen=True)
class ResultMetric(ModelBase):
    metric_key: str = ""
    value: Optional[Quantity] = None
    location: Optional[Tuple[float, float, float]] = None
    status: ResultStatus = ResultStatus.NOT_AVAILABLE
    engineering_filtered: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True)
class GateCheck(ModelBase):
    check_key: str = ""
    passed: bool = False
    blocker: bool = False
    explanation: str = ""


@dataclass(frozen=True)
class GateResult(ModelBase):
    eligible: bool = False
    evidence_disposition: EvidenceDisposition = EvidenceDisposition.QUALIFICATION_BLOCKED
    checks: Tuple[GateCheck, ...] = _tuple()
    blocking_issue_refs: Tuple[EntityRef, ...] = _tuple()


@dataclass(frozen=True)
class AnalysisRun(ModelBase):
    meta: EntityMeta
    project_ref: Optional[EntityRef] = None
    mode: ResultMode = ResultMode.EXPLORATION
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    analysis_type: str = ""
    method_ref: Optional[EntityRef] = None
    load_case_refs: Tuple[EntityRef, ...] = _tuple()
    fixture_refs: Tuple[EntityRef, ...] = _tuple()
    run_manifest_ref: Optional[EntityRef] = None
    solver: SolverSpec = field(default_factory=SolverSpec)
    requested_outputs: Tuple[str, ...] = _tuple()
    result_artifacts: Tuple[ArtifactRef, ...] = _tuple()
    result_metrics: Tuple[ResultMetric, ...] = _tuple()
    validity: Validity = field(default_factory=Validity)
    issue_refs: Tuple[EntityRef, ...] = _tuple()
    gate_result: GateResult = field(default_factory=GateResult)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass(frozen=True)
class ValidationIssue(ModelBase):
    meta: EntityMeta
    code: str = ""
    severity: IssueSeverity = IssueSeverity.INFO
    state: IssueState = IssueState.OPEN
    category: str = ""
    message: str = ""
    affected_entity_refs: Tuple[EntityRef, ...] = _tuple()
    phase: str = "validation"
    evidence_blocking: bool = False
    resolution: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None


@dataclass(frozen=True)
class AcceptanceCriterion(ModelBase):
    metric_key: str = ""
    operator: str = "pass"
    lower: Optional[Quantity] = None
    upper: Optional[Quantity] = None
    tolerance: Optional[Quantity] = None
    comparison_method: str = "exact"


@dataclass(frozen=True)
class Requirement(ModelBase):
    meta: EntityMeta
    external_id: str = ""
    revision_label: str = ""
    title: str = ""
    source_artifact: Optional[ArtifactRef] = None
    source_section: Optional[str] = None
    product_variants: Tuple[str, ...] = _tuple()
    load_case_or_test_method: str = ""
    acceptance: Optional[AcceptanceCriterion] = None
    evidence_required: Tuple[str, ...] = _tuple()
    interpretation_notes: Tuple[str, ...] = _tuple()
    owner: str = ""
    reviewer: Optional[str] = None
    status: RequirementStatus = RequirementStatus.DRAFT


@dataclass(frozen=True)
class GeometryMethodPolicy(ModelBase):
    allowed_representations: Tuple[str, ...] = _tuple()
    require_closed_geometry: bool = False
    require_reviewed_geometry: bool = False
    allow_repaired_geometry: bool = True
    require_region_signatures: bool = False


@dataclass(frozen=True)
class MaterialMethodPolicy(ModelBase):
    allowed_provenance: Tuple[str, ...] = _tuple()
    require_approved_materials: bool = False
    allowed_behaviors: Tuple[str, ...] = _tuple()
    require_conditioning: bool = False


@dataclass(frozen=True)
class SolverMethodPolicy(ModelBase):
    allowed_element_types: Tuple[str, ...] = _tuple()
    material_model: str = "linear_elastic_isotropic"
    small_strain: bool = True
    require_mesh_quality: bool = False
    require_convergence: bool = False
    require_force_balance: bool = False
    allow_approximate_solver: bool = True


@dataclass(frozen=True)
class CorrelationMethodPolicy(ModelBase):
    required: bool = False
    required_record_types: Tuple[str, ...] = _tuple()
    maximum_error_fraction: Optional[float] = None
    require_reviewed_records: bool = False


@dataclass(frozen=True)
class AnalysisMethod(ModelBase):
    meta: EntityMeta
    method_key: str = ""
    name: str = ""
    revision_label: str = ""
    method_type: str = ""
    approval_state: ApprovalState = ApprovalState.DRAFT
    approved_for_qualification: bool = False
    geometry_policy: GeometryMethodPolicy = field(default_factory=GeometryMethodPolicy)
    material_policy: MaterialMethodPolicy = field(default_factory=MaterialMethodPolicy)
    solver_policy: SolverMethodPolicy = field(default_factory=SolverMethodPolicy)
    allowed_fixture_types: Tuple[str, ...] = _tuple()
    required_correlation_policy: CorrelationMethodPolicy = field(default_factory=CorrelationMethodPolicy)
    requires_tolerance_profile: bool = False
    assumptions: Tuple[str, ...] = _tuple()
    known_limitations: Tuple[str, ...] = _tuple()
    approver: Optional[str] = None
    approved_at: Optional[str] = None


@dataclass(frozen=True)
class ReviewRecord(ModelBase):
    meta: EntityMeta
    subject_ref: Optional[EntityRef] = None
    action: str = ""
    reviewer_id: str = ""
    reviewer_role: str = ""
    decision: ReviewState = ReviewState.UNREVIEWED
    timestamp: str = ""
    comments: str = ""
    supersedes_ref: Optional[EntityRef] = None


@dataclass(frozen=True)
class SpecimenContext(ModelBase):
    specimen_id: str = ""
    product_variant: str = ""
    geometry_revision: str = ""
    material_lot: Optional[str] = None
    process_condition: Optional[str] = None
    fixture_artifact: Optional[ArtifactRef] = None
    environment: Tuple[Tuple[str, str], ...] = _tuple()


@dataclass(frozen=True)
class MeasuredSeries(ModelBase):
    metric_key: str = ""
    unit: str = ""
    values: Tuple[Tuple[float, float], ...] = _tuple()
    uncertainty: Optional[Quantity] = None
    sensor_type: Optional[str] = None
    sampling_rate_hz: Optional[float] = None
    calibration_state: str = "unknown"


@dataclass(frozen=True)
class CorrelationComparison(ModelBase):
    metric_key: str = ""
    predicted: Optional[Quantity] = None
    measured: Optional[Quantity] = None
    absolute_error: Optional[Quantity] = None
    relative_error: Optional[float] = None
    status: ResultStatus = ResultStatus.NOT_AVAILABLE
    explanation: str = ""


@dataclass(frozen=True)
class CorrelationRecord(ModelBase):
    meta: EntityMeta
    record_type: str = ""
    specimen: SpecimenContext = field(default_factory=SpecimenContext)
    test_method_revision: str = ""
    performed_at: str = ""
    raw_artifacts: Tuple[ArtifactRef, ...] = _tuple()
    processed_artifacts: Tuple[ArtifactRef, ...] = _tuple()
    measured_series: Tuple[MeasuredSeries, ...] = _tuple()
    reference_run_ref: Optional[EntityRef] = None
    comparisons: Tuple[CorrelationComparison, ...] = _tuple()
    review_state: ReviewState = ReviewState.UNREVIEWED
    reviewer: Optional[str] = None


@dataclass(frozen=True)
class RequirementEvidence(ModelBase):
    requirement_ref: Optional[EntityRef] = None
    evidence_refs: Tuple[EntityRef, ...] = _tuple()
    result: str = "incomplete"
    deviation: Optional[str] = None


@dataclass(frozen=True)
class ReportMetric(ModelBase):
    key: str = ""
    value: Optional[Quantity] = None
    location: Optional[Tuple[float, float, float]] = None
    status: ResultStatus = ResultStatus.NOT_AVAILABLE
    engineering_filtered: bool = False
    unavailable_reason: Optional[str] = None


@dataclass(frozen=True)
class Report(ModelBase):
    meta: EntityMeta
    report_type: str = ""
    project_ref: Optional[EntityRef] = None
    analysis_run_ref: Optional[EntityRef] = None
    run_manifest_ref: Optional[EntityRef] = None
    mode: ResultMode = ResultMode.EXPLORATION
    report_status: ReviewState = ReviewState.UNREVIEWED
    evidence_disposition: EvidenceDisposition = EvidenceDisposition.EXPLORATION_ONLY
    validity: Validity = field(default_factory=Validity)
    governing_requirement_ref: Optional[EntityRef] = None
    requirement_evidence: Tuple[RequirementEvidence, ...] = _tuple()
    metrics: Tuple[ReportMetric, ...] = _tuple()
    issue_refs: Tuple[EntityRef, ...] = _tuple()
    assumptions: Tuple[str, ...] = _tuple()
    unsupported_failure_modes: Tuple[str, ...] = _tuple()
    output_artifacts: Tuple[ArtifactRef, ...] = _tuple()
    review_record_refs: Tuple[EntityRef, ...] = _tuple()
    supersedes_ref: Optional[EntityRef] = None


@dataclass(frozen=True)
class SnapshotEntry(ModelBase):
    entity_type: str = ""
    entity_id: str = ""
    revision: int = 0
    content_hash: str = ""
    canonical_artifact: Optional[ArtifactRef] = None


@dataclass(frozen=True)
class RunManifest(ModelBase):
    """An immutable snapshot of every input used by an analysis run."""

    meta: EntityMeta
    project_ref: Optional[EntityRef] = None
    project_revision: int = 0
    mode: ResultMode = ResultMode.EXPLORATION
    engine_version: str = ""
    schema_version: int = SCHEMA_VERSION
    solver: SolverSpec = field(default_factory=SolverSpec)
    snapshots: Tuple[SnapshotEntry, ...] = _tuple()
    source_artifacts: Tuple[ArtifactRef, ...] = _tuple()
    derived_artifacts: Tuple[ArtifactRef, ...] = _tuple()
    execution_options: Tuple[Tuple[str, str], ...] = _tuple()

    def content_hash(self):
        from .canonical import manifest_hash

        return manifest_hash(self)

    def cache_key(self):
        from .canonical import cache_key_for_manifest

        return cache_key_for_manifest(self)


@dataclass(frozen=True)
class Project(ModelBase):
    meta: EntityMeta
    name: str = ""
    description: str = ""
    default_mode: ResultMode = ResultMode.EXPLORATION
    unit_policy: UnitPolicy = field(default_factory=UnitPolicy)
    tolerance_policy: TolerancePolicy = field(default_factory=TolerancePolicy)
    project_frame_ref: Optional[EntityRef] = None
    geometry_asset_refs: Tuple[EntityRef, ...] = _tuple()
    component_refs: Tuple[EntityRef, ...] = _tuple()
    material_definition_refs: Tuple[EntityRef, ...] = _tuple()
    material_assignment_refs: Tuple[EntityRef, ...] = _tuple()
    reference_frame_refs: Tuple[EntityRef, ...] = _tuple()
    load_case_refs: Tuple[EntityRef, ...] = _tuple()
    fixture_refs: Tuple[EntityRef, ...] = _tuple()
    requirement_refs: Tuple[EntityRef, ...] = _tuple()
    method_refs: Tuple[EntityRef, ...] = _tuple()
    correlation_record_refs: Tuple[EntityRef, ...] = _tuple()
    analysis_run_refs: Tuple[EntityRef, ...] = _tuple()
    report_refs: Tuple[EntityRef, ...] = _tuple()
    run_manifest_refs: Tuple[EntityRef, ...] = _tuple()


@dataclass(frozen=True)
class ProjectDocument(ModelBase):
    """The versioned JSON document exchanged by the foundation package."""

    project: Project
    schema_id: str = SCHEMA_ID
    schema_version: int = SCHEMA_VERSION
    geometry_assets: Tuple[GeometryAsset, ...] = _tuple()
    components: Tuple[Component, ...] = _tuple()
    material_definitions: Tuple[MaterialDefinition, ...] = _tuple()
    material_assignments: Tuple[MaterialAssignment, ...] = _tuple()
    reference_frames: Tuple[ReferenceFrame, ...] = _tuple()
    load_cases: Tuple[LoadCase, ...] = _tuple()
    fixtures: Tuple[Fixture, ...] = _tuple()
    requirements: Tuple[Requirement, ...] = _tuple()
    methods: Tuple[AnalysisMethod, ...] = _tuple()
    correlation_records: Tuple[CorrelationRecord, ...] = _tuple()
    analysis_runs: Tuple[AnalysisRun, ...] = _tuple()
    validation_issues: Tuple[ValidationIssue, ...] = _tuple()
    reports: Tuple[Report, ...] = _tuple()
    run_manifests: Tuple[RunManifest, ...] = _tuple()
    review_records: Tuple[ReviewRecord, ...] = _tuple()

    def entities(self):
        groups = (
            self.geometry_assets,
            self.components,
            self.material_definitions,
            self.material_assignments,
            self.reference_frames,
            self.load_cases,
            self.fixtures,
            self.requirements,
            self.methods,
            self.correlation_records,
            self.analysis_runs,
            self.validation_issues,
            self.reports,
            self.run_manifests,
            self.review_records,
        )
        return tuple(entity for group in groups for entity in group)


def new_meta(entity_type, entity_id="", revision=0, created_at="", schema_version=SCHEMA_VERSION):
    """Create metadata; content hashes are populated with ``hashed_entity``."""

    return EntityMeta(entity_type, schema_version, entity_id, revision, created_at, "")


def entity_ref(entity):
    """Create a reference to an entity using its current metadata hash."""

    return EntityRef(entity.meta.id, entity.meta.content_hash)


def with_content_hash(entity):
    from .canonical import hashed_entity

    return hashed_entity(entity)


# Friendly aliases used by clients that call these concepts "states".
ValidityStatus = ValidityState
ResultState = ResultStatus
ReviewStatus = ReviewState


__all__ = [
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "StringEnum",
    "ResultMode",
    "ValidityState",
    "ReviewState",
    "LifecycleState",
    "EvidenceDisposition",
    "IssueSeverity",
    "IssueState",
    "ApprovalState",
    "ResultStatus",
    "RequirementStatus",
    "EntityMeta",
    "EntityRef",
    "ArtifactRef",
    "Quantity",
    "Provenance",
    "Validity",
    "UnitPolicy",
    "TolerancePolicy",
    "ToleranceProfile",
    "GeometryHealth",
    "RepairRecord",
    "GeometryAsset",
    "RegionRef",
    "GeometryRef",
    "Classification",
    "MassOverride",
    "Component",
    "MaterialProperties",
    "MaterialDefinition",
    "MaterialAssignment",
    "ReferenceFrame",
    "LoadSpec",
    "InterfaceAssumption",
    "LoadCase",
    "Fixture",
    "SolverSpec",
    "ResultMetric",
    "GateCheck",
    "GateResult",
    "AnalysisRun",
    "ValidationIssue",
    "AcceptanceCriterion",
    "Requirement",
    "GeometryMethodPolicy",
    "MaterialMethodPolicy",
    "SolverMethodPolicy",
    "CorrelationMethodPolicy",
    "AnalysisMethod",
    "ReviewRecord",
    "SpecimenContext",
    "MeasuredSeries",
    "CorrelationComparison",
    "CorrelationRecord",
    "RequirementEvidence",
    "ReportMetric",
    "Report",
    "SnapshotEntry",
    "RunManifest",
    "Project",
    "ProjectDocument",
    "new_meta",
    "entity_ref",
    "with_content_hash",
]
