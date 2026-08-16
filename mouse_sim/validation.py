"""DFM-lite validation: structured findings over geometry, materials, and classifications."""

from dataclasses import dataclass, replace
import math
from typing import Mapping, Tuple

from .geometry import Box, Geometry, TriangleMesh, geometry_from_dict
from .materials import material_validation_errors

SEVERITIES = ("info", "warning", "error", "blocker")
_SEVERITY_RANK = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
_VALID_CONFIDENCE = ("low", "medium", "high")
_ZERO_VOLUME_TOLERANCE = 1e-15


@dataclass(frozen=True)
class ValidationFinding:
    """One structured finding mirroring :class:`~mouse_sim.model.ValidationIssue`."""

    code: str = ""
    severity: str = "info"
    state: str = "open"
    category: str = ""
    message: str = ""
    affected_ids: Tuple[str, ...] = ()
    phase: str = "validation"
    evidence_blocking: bool = False

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError("invalid severity {!r}".format(self.severity))
        object.__setattr__(self, "affected_ids", tuple(self.affected_ids))

    def to_dict(self):
        return {
            "code": self.code,
            "severity": self.severity,
            "state": self.state,
            "category": self.category,
            "message": self.message,
            "affected_ids": list(self.affected_ids),
            "phase": self.phase,
            "evidence_blocking": self.evidence_blocking,
        }


@dataclass(frozen=True)
class ValidationReport:
    """Findings sorted by severity then code, with aggregate status."""

    findings: Tuple[ValidationFinding, ...]
    status: str
    validity_state: str

    @classmethod
    def build(cls, findings):
        ordered = tuple(sorted(findings, key=lambda item: (_SEVERITY_RANK.get(item.severity, 3), item.code)))
        if any(item.severity in ("error", "blocker") for item in ordered):
            status, validity_state = "fail", "failed"
        elif any(item.severity == "warning" for item in ordered):
            status, validity_state = "warn", "approximate"
        else:
            status, validity_state = "pass", "valid"
        return cls(ordered, status, validity_state)

    def to_dict(self):
        return {"status": self.status, "validity_state": self.validity_state, "findings": [item.to_dict() for item in self.findings]}


def _finding(code, severity, category, message, object_id, evidence_blocking=False):
    return ValidationFinding(code, severity, "open", category, message, (str(object_id),), "validation", evidence_blocking)


def _repair_reviewed(record):
    if isinstance(record, Mapping):
        return bool(record.get("reviewed", False))
    return bool(getattr(record, "reviewed", False))


def check_geometry_health(geometry, object_id, repair_records=None, display_tessellation=False):
    """Flag open, nonmanifold, degenerate, inconsistent, or zero-volume meshes.

    Geometry errors make results uninterpretable, so they are evidence_blocking;
    pass a repair-record sequence to surface unreviewed repairs.
    """

    findings = []
    mesh = None
    if isinstance(geometry, TriangleMesh):
        mesh = geometry
    elif isinstance(geometry, Mapping):
        try:
            candidate = geometry_from_dict(geometry)
        except (TypeError, ValueError):
            candidate = None
        if isinstance(candidate, TriangleMesh):
            mesh = candidate
    if mesh is not None:
        diagnostics = mesh.diagnostics()
        approximate = " (display tessellation; approximate)" if display_tessellation else ""
        severity = "warning" if display_tessellation else "error"
        if not diagnostics.closed:
            findings.append(_finding("GEOMETRY_OPEN_MESH", severity, "geometry_health", "mesh is open with {} boundary edge(s){}".format(diagnostics.boundary_edges, approximate), object_id, evidence_blocking=not display_tessellation))
        if diagnostics.nonmanifold_edges:
            findings.append(_finding("GEOMETRY_NONMANIFOLD_EDGES", severity, "geometry_health", "mesh has {} nonmanifold edge(s){}".format(diagnostics.nonmanifold_edges, approximate), object_id, evidence_blocking=not display_tessellation))
        if diagnostics.degenerate_triangles:
            findings.append(_finding("GEOMETRY_DEGENERATE_TRIANGLES", "warning", "geometry_health", "mesh has {} degenerate triangle(s)".format(diagnostics.degenerate_triangles), object_id))
        if diagnostics.inconsistent_winding:
            findings.append(_finding("GEOMETRY_INCONSISTENT_WINDING", severity, "geometry_health", "mesh has inconsistent triangle winding{}".format(approximate), object_id, evidence_blocking=not display_tessellation))
        if "zero_signed_volume" in diagnostics.issues:
            findings.append(_finding("GEOMETRY_ZERO_VOLUME", "error", "geometry_health", "mesh has zero signed volume", object_id, evidence_blocking=True))
        elif abs(diagnostics.signed_volume_m3) <= _ZERO_VOLUME_TOLERANCE:
            # Valid-in-relative-terms but absolutely microscopic geometry: the
            # mass/inertia integrals cannot be certified.  This is a SUPPORTED
            # PHYSICAL DOMAIN boundary, not invalid geometry — label it as
            # such instead of pretending the mesh is simply broken.
            findings.append(_finding(
                "OUTSIDE_SUPPORTED_PHYSICAL_SCALE",
                "error",
                "geometry_health",
                "mesh volume {:.3e} m3 is below the supported physical scale "
                "(absolute floor {} m3, ~10 um cube); mass/inertia cannot be certified".format(
                    abs(diagnostics.signed_volume_m3), _ZERO_VOLUME_TOLERANCE
                ),
                object_id,
                evidence_blocking=True,
            ))
        if "self_intersection_unverified" in diagnostics.issues:
            # The mesh is larger than the exact pair-sweep limit: the geometry
            # integrity check is INCOMPLETE, not clean.  Mass is still computed
            # but the overall validation must reflect the uncertainty so the
            # shell result can never claim PASS / high confidence on it.
            findings.append(_finding(
                "SELF_INTERSECTION_UNVERIFIED",
                "warning",
                "geometry_health",
                "mesh exceeds the self-intersection sweep limit; geometry "
                "integrity is unverified and mass may be affected",
                object_id,
            ))
    if repair_records:
        unreviewed = [record for record in repair_records if not _repair_reviewed(record)]
        if unreviewed:
            findings.append(_finding("GEOMETRY_REPAIRS_UNREVIEWED", "warning", "geometry_health", "geometry has {} unreviewed repair record(s)".format(len(unreviewed)), object_id))
    return tuple(findings)


def _use_definition_validation(value):
    if isinstance(value, Mapping):
        return any(key in value for key in ("properties", "definition", "meta"))
    return hasattr(value, "properties")


def _property_value(value, name):
    raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    if raw is None and isinstance(value, Mapping) and isinstance(value.get("properties"), Mapping):
        raw = value["properties"].get(name)
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        if "value_si" in raw:
            return float(raw["value_si"])
        return float(raw["value"]) if "value" in raw else None
    if hasattr(raw, "value_si"):
        return float(raw.value_si)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _basic_material_errors(value):
    errors = []
    density = _property_value(value, "density")
    if density is not None and (not math.isfinite(density) or density <= 0):
        errors.append("density must be positive and finite")
    modulus = _property_value(value, "young_modulus")
    if modulus is not None and (not math.isfinite(modulus) or modulus <= 0):
        errors.append("young_modulus must be positive and finite")
    ratio = _property_value(value, "poissons_ratio")
    if ratio is not None and (not math.isfinite(ratio) or not (-1.0 < ratio < 0.5)):
        errors.append("poissons_ratio must be finite and between -1 and 0.5")
    return tuple(errors)


def _provenance_confidence(material):
    if isinstance(material, Mapping):
        provenance = material.get("provenance")
        return provenance.get("confidence") if isinstance(provenance, Mapping) else None
    provenance = getattr(material, "provenance", None)
    return getattr(provenance, "confidence", None) if provenance is not None else None


def _approval_state(material):
    raw = material.get("approval_state") if isinstance(material, Mapping) else getattr(material, "approval_state", None)
    if raw is None:
        return "draft"
    return raw.value if hasattr(raw, "value") else str(raw)


def check_material(material_or_properties, object_id):
    """Validate definitions via material_validation_errors; raw records via basic checks."""

    findings = []
    if material_or_properties is None:
        return (_finding("MATERIAL_INVALID", "error", "material", "material is missing", object_id),)
    if _use_definition_validation(material_or_properties):
        try:
            errors = material_validation_errors(material_or_properties)
        except (TypeError, ValueError):
            errors = ()
    else:
        errors = _basic_material_errors(material_or_properties)
    if errors:
        findings.append(_finding("MATERIAL_INVALID", "error", "material", "; ".join(errors), object_id))
    confidence = _provenance_confidence(material_or_properties)
    if confidence not in _VALID_CONFIDENCE:
        findings.append(_finding("MAT_PROVENANCE_CONFIDENCE", "warning", "material", "provenance confidence is {!r}; expected low, medium, or high".format(confidence), object_id))
    approval = _approval_state(material_or_properties)
    if approval != "approved":
        findings.append(_finding("MAT_UNAPPROVED_PROVENANCE", "warning", "material", "material approval_state is {!r}; properties are not approved for qualification".format(approval), object_id))
    return tuple(findings)


def check_classification(classification_dict, object_id):
    """Warn on unresolved components; require structural behavior."""

    findings = []
    if isinstance(classification_dict, Mapping):
        component_type = classification_dict.get("component_type")
        unresolved = bool(classification_dict.get("unresolved", False))
        if "unresolved" not in classification_dict and component_type in (None, "", "unresolved", "surface"):
            unresolved = True
        behavior = classification_dict.get("structural_behavior")
    else:
        component_type = getattr(classification_dict, "component_type", None)
        flag = getattr(classification_dict, "unresolved", None)
        unresolved = bool(flag) if flag is not None else component_type in (None, "", "unresolved", "surface")
        behavior = getattr(classification_dict, "structural_behavior", None)
    if unresolved:
        findings.append(_finding("CLASSIFICATION_UNRESOLVED", "warning", "classification", "component classification is unresolved", object_id))
    if behavior is None or str(behavior).strip() == "":
        findings.append(_finding("CLASSIFICATION_MISSING_BEHAVIOR", "error", "classification", "structural behavior is missing", object_id))
    return tuple(findings)


def check_wall_thickness(geometry, object_id, min_thickness_m, max_thickness_m):
    """Exact thickness for boxes and explicit wall_thickness_m dicts; else THICKNESS_UNKNOWN."""

    if min_thickness_m > max_thickness_m:
        return (_finding("THICKNESS_LIMITS_INVALID", "error", "wall_thickness", "min_thickness_m {:.6g} exceeds max_thickness_m {:.6g}".format(min_thickness_m, max_thickness_m), object_id),)
    thickness = None
    if isinstance(geometry, Box):
        thickness = min(geometry.size)
    elif isinstance(geometry, Mapping):
        raw = geometry.get("wall_thickness_m")
        if raw is None and isinstance(geometry.get("geometry"), Mapping):
            raw = geometry["geometry"].get("wall_thickness_m")
        if raw is not None:
            try:
                thickness = float(raw)
            except (TypeError, ValueError):
                thickness = None
            if thickness is not None and not math.isfinite(thickness):
                thickness = None
    if thickness is None:
        return (_finding("THICKNESS_UNKNOWN", "warning", "wall_thickness", "no exact thickness available for this representation", object_id),)
    findings = []
    if thickness < min_thickness_m:
        findings.append(_finding("WALL_THICKNESS_TOO_THIN", "error", "wall_thickness", "wall thickness {:.6g} m is below minimum {:.6g} m".format(thickness, min_thickness_m), object_id))
    if thickness > max_thickness_m:
        findings.append(_finding("WALL_THICKNESS_TOO_THICK", "error", "wall_thickness", "wall thickness {:.6g} m exceeds maximum {:.6g} m".format(thickness, max_thickness_m), object_id))
    return tuple(findings)


def _geometry_bounds(geometry):
    if isinstance(geometry, Geometry):
        return geometry.bounds()
    if isinstance(geometry, Mapping):
        try:
            candidate = geometry_from_dict(geometry)
        except (TypeError, ValueError):
            return None
        if isinstance(candidate, Geometry):
            return candidate.bounds()
    return None


def _aabb_distance(first, second):
    squared = 0.0
    for index in range(3):
        if first.max_point[index] < second.min_point[index]:
            gap = second.min_point[index] - first.max_point[index]
        elif second.max_point[index] < first.min_point[index]:
            gap = first.min_point[index] - second.max_point[index]
        else:
            gap = 0.0
        squared += gap * gap
    return math.sqrt(squared)


def check_pcb_clearance(pcb_geometry, shell_geometry, min_clearance_m, tolerance_m=0.0, pcb_object_id="pcb", shell_object_id="shell"):
    """AABB clearance between PCB and shell; failure is a blocking finding."""

    pcb_bounds = _geometry_bounds(pcb_geometry)
    shell_bounds = _geometry_bounds(shell_geometry)
    if pcb_bounds is None or shell_bounds is None:
        return (_finding("PCB_CLEARANCE_UNKNOWN", "warning", "pcb_clearance", "clearance cannot be computed: missing axis-aligned bounds", pcb_object_id),)
    distance = _aabb_distance(pcb_bounds, shell_bounds)
    affected = (str(pcb_object_id), str(shell_object_id))
    if distance < min_clearance_m:
        return (ValidationFinding(code="PCB_CLEARANCE_FAIL", severity="blocker", category="pcb_clearance", message="PCB clearance {:.6g} m is below required {:.6g} m".format(distance, min_clearance_m), affected_ids=affected, evidence_blocking=True),)
    margin = distance - min_clearance_m
    if margin < tolerance_m:
        return (ValidationFinding(code="PCB_CLEARANCE_MARGIN_THIN", severity="warning", category="pcb_clearance", message="PCB clearance margin {:.6g} m is thinner than tolerance {:.6g} m".format(margin, tolerance_m), affected_ids=affected),)
    return ()


def _promote_warnings(findings):
    return tuple(
        replace(item, severity="error", evidence_blocking=True) if item.severity == "warning" else item
        for item in findings
    )


# Optical tracking lens stack: the lens height above the PCB is a design
# parameter (gaming-mouse class 1.2-1.5 mm); the lens z-displacement budget
# under shock is 0.15 mm — beyond it the tracking surface distance drifts
# out of the sensor's depth of field (OPTICAL_TRACKING_LOD_SHIFT).
LENS_HEIGHT_MIN_M = 1.2e-3
LENS_HEIGHT_MAX_M = 1.5e-3
LENS_DEFOCUS_BUDGET_M = 0.15e-3


def check_optical_defocus(
    pcb_geometry,
    sensor_geometry,
    object_id,
    drop_peak_accel_g,
    lens_height_m=None,
    pcb_young_modulus_pa=22e9,
    pcb_poissons_ratio=0.14,
    pcb_density_kg_m3=1850.0,
    pcb_thickness_m=None,
    sensor_mass_kg=0.0015,
    lens_defocus_budget_m=LENS_DEFOCUS_BUDGET_M,
):
    """Optical sensor lens z-defocus under drop shock (PCB plate bending).

    The sensor lens mounts on the PCB; the shock-induced PCB deflection
    displaces the lens out of the tracking surface's depth of field.  The
    PCB is modeled as a simply supported rectangular plate (Roark 11.4 case
    1, uniform load) PLUS a central point load from the sensor package
    inertia (Roark 11.4 case 7); the lens z-displacement is the combined
    plate deflection at the sensor location.  A displacement beyond the
    0.15 mm defocus budget emits ``OPTICAL_TRACKING_LOD_SHIFT`` (warning —
    non-blocking, the tracking degrades but the sensor survives).

    Returns a tuple of :class:`ValidationFinding`; never raises.  Missing
    data emits ``OPTICAL_TRACKING_LOD_UNKNOWN`` (warning) instead of
    crashing, mirroring ``PCB_CLEARANCE_UNKNOWN``.
    """

    def unknown(reason):
        return (
            _finding(
                "OPTICAL_TRACKING_LOD_UNKNOWN",
                "warning",
                "optics",
                "optical lens defocus cannot be assessed: {}".format(reason),
                object_id,
            ),
        )

    pcb_bounds = _geometry_bounds(pcb_geometry)
    sensor_bounds = _geometry_bounds(sensor_geometry)
    if pcb_bounds is None or sensor_bounds is None:
        return unknown("missing PCB or sensor bounds")
    if drop_peak_accel_g is None or not math.isfinite(float(drop_peak_accel_g)) or float(drop_peak_accel_g) <= 0.0:
        return unknown("no positive drop shock data")
    if pcb_young_modulus_pa is None or pcb_young_modulus_pa <= 0.0:
        return unknown("PCB modulus is not positive")
    if pcb_thickness_m is None or pcb_thickness_m <= 0.0:
        pcb_thickness_m = min(
            pcb_bounds.max_point[i] - pcb_bounds.min_point[i] for i in range(3)
        )
        if pcb_thickness_m <= 0.0:
            return unknown("PCB thickness unavailable")
    # Plate in-plane dimensions (largest two spans of the PCB AABB).
    spans = sorted(
        (pcb_bounds.max_point[i] - pcb_bounds.min_point[i] for i in range(3)),
        reverse=True,
    )
    length, width = spans[0], spans[1]
    if length <= 0.0 or width <= 0.0:
        return unknown("PCB spans unavailable")
    a = min(length, width)
    b = max(length, width)
    aspect = b / a if a > 0.0 else 1.0
    if pcb_poissons_ratio is None or not (-0.99 <= pcb_poissons_ratio <= 0.49):
        pcb_poissons_ratio = 0.35  # standard FR4 screening default
    if pcb_density_kg_m3 is None or pcb_density_kg_m3 <= 0.0:
        pcb_density_kg_m3 = 1850.0  # standard FR4 density
    # Simply-supported plate coefficients (Roark 11.4 case 1 uniform load
    # alpha_u, case 7 central point load alpha_p; Navier series for SS plate).
    # The standoff-mounted board sits between the clamped and simply-supported
    # bounds; the simply-supported case is the screening choice because the
    # sensor cutout and mounting-hole pattern soften the panel.
    if aspect <= 1.0:
        alpha_u = 0.00406
        alpha_p = 0.01160
    elif aspect <= 1.2:
        alpha_u = 0.00564
        alpha_p = 0.00750
    elif aspect <= 1.4:
        alpha_u = 0.00705
        alpha_p = 0.00510
    elif aspect <= 1.6:
        alpha_u = 0.00773
        alpha_p = 0.00365
    elif aspect <= 1.8:
        alpha_u = 0.00832
        alpha_p = 0.00270
    elif aspect <= 2.0:
        alpha_u = 0.01013
        alpha_p = 0.00207
    else:
        alpha_u = 0.01013
        alpha_p = 0.00207
    # Combined shock load: uniform load from the whole accelerated board
    # mass PLUS a central point load from the sensor package inertia.
    pcb_mass = pcb_density_kg_m3 * a * b * pcb_thickness_m
    sensor_mass = max(0.0, float(sensor_mass_kg or 0.0))
    q = (pcb_mass + sensor_mass) * float(drop_peak_accel_g) * 9.80665 / (a * b)
    point_load = sensor_mass * float(drop_peak_accel_g) * 9.80665
    denom = max(1e-12, 12.0 * (1.0 - pcb_poissons_ratio * pcb_poissons_ratio))
    d = max(1e-12, pcb_young_modulus_pa * (pcb_thickness_m ** 3) / denom)
    deflection = alpha_u * q * (a ** 4) / d + alpha_p * point_load * (a ** 2) / d
    # Sensor z-offset from the PCB mid-plane (lens height class 1.2-1.5 mm)
    # amplifies the plate slope; the lever contribution is bounded by the
    # local slope at the sensor x a half-span.  Screening-level estimate.
    sensor_center = (
        (sensor_bounds.min_point[0] + sensor_bounds.max_point[0]) / 2.0,
        (sensor_bounds.min_point[1] + sensor_bounds.max_point[1]) / 2.0,
        (sensor_bounds.min_point[2] + sensor_bounds.max_point[2]) / 2.0,
    )
    pcb_center = (
        (pcb_bounds.min_point[0] + pcb_bounds.max_point[0]) / 2.0,
        (pcb_bounds.min_point[1] + pcb_bounds.max_point[1]) / 2.0,
        (pcb_bounds.min_point[2] + pcb_bounds.max_point[2]) / 2.0,
    )
    radial = math.sqrt(
        (sensor_center[0] - pcb_center[0]) ** 2
        + (sensor_center[1] - pcb_center[1]) ** 2
    )
    slope = 4.0 * deflection / a if a > 0.0 else 0.0
    lens_height = LENS_HEIGHT_MIN_M if lens_height_m is None else float(lens_height_m)
    if not math.isfinite(lens_height) or lens_height <= 0.0:
        lens_height = LENS_HEIGHT_MIN_M
    delta_z = deflection + slope * radial * (lens_height / a)
    if delta_z > lens_defocus_budget_m:
        return (
            _finding(
                "OPTICAL_TRACKING_LOD_SHIFT",
                "warning",
                "optics",
                "optical sensor lens z-displacement {:.3f} mm exceeds the {:.3f} mm defocus budget under {:.0f} g shock (PCB deflection {:.3f} mm); tracking surface distance drift risk".format(
                    delta_z * 1e3,
                    lens_defocus_budget_m * 1e3,
                    float(drop_peak_accel_g),
                    deflection * 1e3,
                ),
                object_id,
            ),
        )
    return ()


def _lens_height_from_classification(classification):
    """Best-effort lens height (m) from a classification record; None if absent."""
    if isinstance(classification, Mapping):
        raw = classification.get("lens_height_m")
        if raw is None:
            raw = classification.get("tracking_height_m")
    else:
        raw = getattr(classification, "lens_height_m", None)
        if raw is None:
            raw = getattr(classification, "tracking_height_m", None)
    if raw is None:
        return None
    try:
        height = float(raw)
    except (TypeError, ValueError):
        return None
    return height if math.isfinite(height) and height > 0.0 else None


def run_validation(geometry_objs, material_map, classifications, options):
    """Orchestrate DFM-lite checks into a sorted :class:`ValidationReport`.

    options: min_thickness_m, max_thickness_m, min_clearance_m,
    clearance_tolerance_m (or tolerance_m), pcb_object_id, shell_object_id,
    sensor_object_id, drop_peak_accel_g, pcb_young_modulus_pa,
    pcb_poissons_ratio, pcb_density_kg_m3, pcb_thickness_m, sensor_mass_kg,
    lens_height_m, repair_records (mapping object id -> records, or one list
    for all objects).  strict (bool): when true, promote warning findings to
    evidence-blocking errors so the report status is fail.
    """

    geometry_objs = dict(geometry_objs or {})
    material_map = dict(material_map or {})
    classifications = dict(classifications or {})
    options = dict(options or {})
    findings = []
    min_thickness = options.get("min_thickness_m")
    max_thickness = options.get("max_thickness_m")
    min_clearance = options.get("min_clearance_m")
    tolerance = options.get("clearance_tolerance_m", options.get("tolerance_m", 0.0))
    pcb_id = options.get("pcb_object_id")
    shell_id = options.get("shell_object_id")
    sensor_id = options.get("sensor_object_id")
    repair_records = options.get("repair_records", {})
    if isinstance(repair_records, (list, tuple)):
        repair_records = {object_id: repair_records for object_id in geometry_objs}
    display_tessellation = bool(options.get("display_tessellation", False))
    for object_id, geometry in geometry_objs.items():
        findings.extend(
            check_geometry_health(
                geometry,
                object_id,
                repair_records=repair_records.get(object_id),
                display_tessellation=display_tessellation,
            )
        )
        if object_id in material_map:
            findings.extend(check_material(material_map[object_id], object_id))
        if object_id in classifications:
            findings.extend(check_classification(classifications[object_id], object_id))
        if min_thickness is not None and max_thickness is not None:
            findings.extend(check_wall_thickness(geometry, object_id, min_thickness, max_thickness))
    if pcb_id and shell_id and min_clearance is not None and pcb_id in geometry_objs and shell_id in geometry_objs:
        findings.extend(check_pcb_clearance(geometry_objs[pcb_id], geometry_objs[shell_id], min_clearance, tolerance, pcb_object_id=pcb_id, shell_object_id=shell_id))
    if pcb_id and sensor_id and pcb_id in geometry_objs and sensor_id in geometry_objs:
        drop_g = options.get("drop_peak_accel_g")
        findings.extend(
            check_optical_defocus(
                geometry_objs[pcb_id],
                geometry_objs[sensor_id],
                sensor_id,
                drop_g,
                lens_height_m=options.get("lens_height_m"),
                pcb_young_modulus_pa=options.get("pcb_young_modulus_pa", 22e9),
                pcb_poissons_ratio=options.get("pcb_poissons_ratio", 0.14),
                pcb_density_kg_m3=options.get("pcb_density_kg_m3", 1850.0),
                pcb_thickness_m=options.get("pcb_thickness_m"),
                sensor_mass_kg=options.get("sensor_mass_kg", 0.0015),
            )
        )
    if options.get("strict"):
        findings = _promote_warnings(findings)
    return ValidationReport.build(findings)


__all__ = [
    "ValidationFinding",
    "ValidationReport",
    "check_geometry_health",
    "check_material",
    "check_classification",
    "check_wall_thickness",
    "check_pcb_clearance",
    "check_optical_defocus",
    "run_validation",
]
