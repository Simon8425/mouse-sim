"""Energy-based MVP impact estimate (exploration only).

Quasi-static screening: closing velocity, impulse, peak force, peak
acceleration, contact duration, and load-path stress from energy and
momentum balance.  Output is exploration-only; impact qualification is
blocked unless the analysis method is approved and validated evidence
exists.  Failure modes that need detailed simulation (battery crush,
PCB shock, fracture, delamination, screw pull-out) are always reported
as unsupported.

The result metadata identifies this as the ``screening_surrogate_v1``
model: a coarse screening surrogate, not a validated dynamics solver.
Contact stiffness may be linear (``linear`` / ``linear_calibrated``) or
the nonlinear Hertz point-contact law (``hertz_nonlinear``,
F = (4/3)*E_eff*sqrt(r)*delta^(3/2)).
"""

from dataclasses import dataclass, field, replace
import math
from typing import Any, Iterable, Mapping, Optional, Tuple

from .model import EvidenceDisposition

Vector3 = Tuple[float, float, float]

UNSUPPORTED_BATTERY_CRUSH = "UNSUPPORTED_BATTERY_CRUSH"
UNSUPPORTED_PCB_SHOCK = "UNSUPPORTED_PCB_SHOCK"
UNSUPPORTED_FRACTURE = "UNSUPPORTED_FRACTURE"
UNSUPPORTED_DELAMINATION = "UNSUPPORTED_DELAMINATION"
UNSUPPORTED_SCREW_PULLOUT = "UNSUPPORTED_SCREW_PULLOUT"
IMPACT_UNSUPPORTED_FAILURE_MODES = (
    UNSUPPORTED_BATTERY_CRUSH,
    UNSUPPORTED_PCB_SHOCK,
    UNSUPPORTED_FRACTURE,
    UNSUPPORTED_DELAMINATION,
    UNSUPPORTED_SCREW_PULLOUT,
)
CONTACT_PATCH_ASSUMPTION = "CONTACT_PATCH_ASSUMPTION"
DESK_EDGE_CONTACT_APPROXIMATION = "DESK_EDGE_CONTACT_APPROXIMATION"
FATIGUE_ESTIMATE_EXCEEDED = "FATIGUE_ESTIMATE_EXCEEDED"
FATIGUE_GENERIC_FALLBACK = "FATIGUE_GENERIC_FALLBACK"
# Generic polymer S-N fallback: fatigue strength 14 MPa at 1e6 cycles with a
# Basquin slope of 6, ABS-like (polymer fatigue compilations, R ~ 0.1).
GENERIC_FATIGUE_STRENGTH_AT_1E6_PA = 14e6
GENERIC_FATIGUE_EXPONENT_K = 6
FATIGUE_GENERIC_FALLBACK_ASSUMPTION = (
    "generic polymer fatigue law (14 MPa @ 10^6, slope 6) used; "
    "material-specific S-N data unavailable"
)
INVALID_MASS = "INVALID_MASS"
INVALID_KINEMATICS = "INVALID_KINEMATICS"
INVALID_RESTITUTION = "INVALID_RESTITUTION"
INVALID_TARGET_MASS = "INVALID_TARGET_MASS"
INVALID_STIFFNESS = "INVALID_STIFFNESS"
INVALID_STOPPING_DISTANCE = "INVALID_STOPPING_DISTANCE"
INVALID_CONTACT_DURATION = "INVALID_CONTACT_DURATION"
INVALID_LOAD_PATH = "INVALID_LOAD_PATH"
INVALID_CONTACT_NORMAL = "INVALID_CONTACT_NORMAL"
INVALID_CONTACT_RADIUS = "INVALID_CONTACT_RADIUS"
INVALID_INERTIA_TENSOR = "INVALID_INERTIA_TENSOR"
INVALID_CONTACT_OFFSET = "INVALID_CONTACT_OFFSET"
INSUFFICIENT_PARAMETERS = "INSUFFICIENT_PARAMETERS"
PEAK_FORCE_NOT_ESTIMATED = "PEAK_FORCE_NOT_ESTIMATED"
CONTACT_PATCH_ASSUMPTION_TEXT = (
    "contact patch approximated as point contact; local contact stresses not resolved"
)
METHOD_ID = "energy_quasi_static_v1"
SCREENING_SURROGATE_MODEL_ID = "screening_surrogate_v1"
CONTACT_MODEL_LINEAR = "linear"
CONTACT_MODEL_LINEAR_CALIBRATED = "linear_calibrated"
CONTACT_MODEL_HERTZ_NONLINEAR = "hertz_nonlinear"
CONTACT_MODEL_HALF_SINE = "half_sine"
CONTACT_MODEL_STOPPING_DISTANCE = "stopping_distance"
HERTZ_EFFECTIVE_MODULUS_DEFAULT_PA = 1e9
# Full elastic (e=1) Hertz contact duration factor: t = 2.94*delta_max/v_n.
# For restitution e the reported compression-phase duration is scaled by
# (1+e)/2, so plastic impact (e=0) ends at max compression (t ~ 1.47*delta/v)
# and perfectly elastic impact spans the full contact.
HERTZ_CONTACT_DURATION_FACTOR = 2.94
PEAK_FORCE_CONSERVATIVE_FACTOR = 2.0
IMPACT_ACCELERATION_IMPLAUSIBLE = "IMPACT_ACCELERATION_IMPLAUSIBLE"
IMPACT_STRESS_IMPLAUSIBLE = "IMPACT_STRESS_IMPLAUSIBLE"
_ACCELERATION_PLAUSIBILITY_LIMIT_M_S2 = 1e6
_STRESS_PLAUSIBILITY_LIMIT_PA = 1e11
_MAX_SCREENING_LIFE_CYCLES = 1e18
_IMPACT_SOLVER_METADATA = {
    "model_family": "energy_quasi_static_screening",
    "model_id": SCREENING_SURROGATE_MODEL_ID,
    "description": "quasi-static energy-balance screening surrogate; "
    "exploration only, not a validated dynamics solver",
    "load_path_stress_pa_limitation": "scalar screening proxy based on the "
    "reported force; not a component stress prediction",
}


@dataclass(frozen=True)
class ImpactResult:
    """Immutable result of an energy-based quasi-static impact estimate.

    ``validity`` is ``valid``, ``no_impact`` (no closing velocity), or
    ``failed`` (invalid inputs, flagged).  ``safety_factor`` is a float
    when computed and the marker string ``not_available`` otherwise.
    ``load_path_stress_pa`` is a scalar screening proxy based on the
    reported force, not a component-level stress prediction.
    ``solver_metadata`` identifies the model as ``screening_surrogate_v1``.
    ``contact_model`` records how the contact stiffness was modeled
    (``linear``, ``linear_calibrated``, ``hertz_nonlinear``,
    ``half_sine``, or ``stopping_distance``).
    """

    impact_energy_j: float
    closing_velocity_m_s: float
    effective_mass_kg: float
    impulse_n_s: float
    peak_force_n: float
    peak_acceleration_m_s2: float
    contact_duration_s: float
    contact_compression_m: float
    method_id: str = METHOD_ID
    flags: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    unsupported_failure_modes: Tuple[str, ...] = IMPACT_UNSUPPORTED_FAILURE_MODES
    validity: str = "valid"
    load_path_stress_pa: Optional[float] = None
    safety_factor: Any = "not_available"
    qualification_blocked: bool = True
    average_force_n: Optional[float] = None
    peak_force_estimate_n: Optional[float] = None
    contact_model: str = CONTACT_MODEL_LINEAR
    contact_normal: Vector3 = (0.0, 0.0, 1.0)
    impact_angle_deg: float = 0.0
    effective_normal_velocity_m_s: float = 0.0
    vertical_velocity_component_m_s: float = 0.0
    tangential_velocity_m_s: float = 0.0
    energy_partition: Optional[Mapping] = None
    solver_metadata: Mapping = field(default_factory=lambda: dict(_IMPACT_SOLVER_METADATA))

    def to_dict(self):
        return {
            "impact_energy_j": self.impact_energy_j,
            "closing_velocity_m_s": self.closing_velocity_m_s,
            "effective_mass_kg": self.effective_mass_kg,
            "impulse_n_s": self.impulse_n_s,
            "peak_force_n": self.peak_force_n,
            "peak_acceleration_m_s2": self.peak_acceleration_m_s2,
            "contact_duration_s": self.contact_duration_s,
            "contact_compression_m": self.contact_compression_m,
            "method_id": self.method_id,
            "flags": list(self.flags),
            "assumptions": list(self.assumptions),
            "unsupported_failure_modes": list(self.unsupported_failure_modes),
            "validity": self.validity,
            "load_path_stress_pa": self.load_path_stress_pa,
            "safety_factor": self.safety_factor,
            "qualification_blocked": self.qualification_blocked,
            "average_force_n": self.average_force_n,
            "peak_force_estimate_n": self.peak_force_estimate_n,
            "contact_model": self.contact_model,
            "contact_normal": list(self.contact_normal),
            "impact_angle_deg": self.impact_angle_deg,
            "effective_normal_velocity_m_s": self.effective_normal_velocity_m_s,
            "vertical_velocity_component_m_s": self.vertical_velocity_component_m_s,
            "tangential_velocity_m_s": self.tangential_velocity_m_s,
            "energy_partition": dict(self.energy_partition) if self.energy_partition is not None else None,
            "solver_metadata": dict(self.solver_metadata),
        }


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be numeric".format(label))
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(label))
    return number


def _failed(flag, reason):
    return ImpactResult(
        impact_energy_j=0.0,
        closing_velocity_m_s=0.0,
        effective_mass_kg=0.0,
        impulse_n_s=0.0,
        peak_force_n=0.0,
        peak_acceleration_m_s2=0.0,
        contact_duration_s=0.0,
        contact_compression_m=0.0,
        flags=(flag,),
        assumptions=(reason,),
        validity="failed",
    )


def _no_impact(mass_kg, reason, normal=(0.0, 0.0, 1.0), angle=0.0):
    return ImpactResult(
        impact_energy_j=0.0,
        closing_velocity_m_s=0.0,
        effective_mass_kg=mass_kg,
        impulse_n_s=0.0,
        peak_force_n=0.0,
        peak_acceleration_m_s2=0.0,
        contact_duration_s=0.0,
        contact_compression_m=0.0,
        contact_normal=normal,
        impact_angle_deg=angle,
        assumptions=(reason, "no impact event evaluated"),
        validity="no_impact",
    )


def _kinematics(velocity, height, gravity):
    if velocity is not None:
        return velocity, "closing velocity supplied directly: v = {:.6g} m/s".format(velocity)
    speed = math.sqrt(2.0 * gravity * max(height, 0.0)) if height > 0.0 else 0.0
    return speed, "closing velocity from free fall: v = sqrt(2*g*h) = {:.6g} m/s".format(speed)


def _normalized_contact_normal(contact_normal):
    """Validate and normalize a contact normal.

    Returns ``(unit_normal, angle_deg_from_vertical)`` or raises
    ``ValueError`` for a non-vector, non-numeric, non-finite, or zero
    input.
    """
    if not isinstance(contact_normal, (tuple, list)) or len(contact_normal) != 3:
        raise ValueError("contact_normal must be a 3-component vector")
    try:
        raw = tuple(float(item) for item in contact_normal)
    except (TypeError, ValueError):
        raise ValueError("contact_normal components must be numeric")
    if not all(math.isfinite(item) for item in raw) or all(item == 0.0 for item in raw):
        raise ValueError("contact_normal must be finite and nonzero")
    norm = math.sqrt(sum(item * item for item in raw))
    normal = tuple(item / norm for item in raw)
    n_z = min(1.0, abs(normal[2]))
    return normal, math.degrees(math.acos(n_z))


def _vector3(value, label):
    """Validate a 3-component vector of finite floats."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("{} must be a 3-component vector".format(label))
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise ValueError("{} components must be numeric".format(label))
    if not all(math.isfinite(item) for item in result):
        raise ValueError("{} components must be finite".format(label))
    return result


def _validate_inertia_tensor(value):
    """Validate a 3x3 inertia tensor of finite floats (kg*m^2)."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("inertia_tensor_kg_m2 must be a 3x3 matrix")
    rows = []
    for index in range(3):
        row = value[index]
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise ValueError("inertia_tensor_kg_m2 must be a 3x3 matrix")
        try:
            items = tuple(float(item) for item in row)
        except (TypeError, ValueError):
            raise ValueError("inertia_tensor_kg_m2 components must be numeric")
        if not all(math.isfinite(item) for item in items):
            raise ValueError("inertia_tensor_kg_m2 components must be finite")
        rows.append(items)
    # Audit finding (W2-05D): a negative-diagonal tensor passed validation
    # and was silently clamped inside _energy_partition, producing a
    # valid-looking result.  A physical inertia tensor is symmetric
    # positive-definite (Sylvester criterion).
    if any(
        abs(rows[i][j] - rows[j][i]) > 1e-9 for i in range(3) for j in range(3)
    ):
        raise ValueError("inertia_tensor_kg_m2 must be symmetric")
    det1 = rows[0][0]
    det2 = rows[0][0] * rows[1][1] - rows[0][1] * rows[1][0]
    det3 = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    if not (det1 > 0.0 and det2 > 0.0 and det3 > 0.0):
        raise ValueError("inertia_tensor_kg_m2 must be positive-definite")
    return tuple(rows)


def _invert_3x3(matrix):
    """Invert a 3x3 matrix, or return None when singular."""
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if det == 0.0 or not math.isfinite(det):
        return None
    return (
        ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det),
    )


def _energy_partition(total_mass, inertia, normal, impulse, offset, energy=None):
    """Estimate the translation vs rotation energy partition of the impact.

    Free-body rigid-impulse model (screening estimate only; rotation
    response is not resolved): a plastic impulse ``J`` applied along the
    contact normal ``n`` at offset ``r`` from the center of mass yields
    T_trans = J^2/(2*M) and T_rot = 0.5*J^2*(r x n)*I^-1*(r x n).  The
    reduced-mass effect of a target is not included in the partition.
    When ``energy`` is supplied both components are scaled so that
    T_trans + T_rot equals it exactly (the raw impulse partition does not
    conserve the reported impact energy in general).

    Returns ``(partition_dict, notes)``; ``partition_dict`` is None when
    the partition cannot be estimated (singular inertia tensor).
    """
    notes = []
    if inertia is None:
        return None, ["rotational energy partition not estimated: inertia tensor not provided"]
    symmetric = tuple(
        tuple(0.5 * (inertia[i][j] + inertia[j][i]) for j in range(3)) for i in range(3)
    )
    inverse = _invert_3x3(symmetric)
    if inverse is None:
        return None, ["rotational energy partition not estimated: inertia tensor not invertible"]
    r = offset if offset is not None else (0.0, 0.0, 0.0)
    if offset is None:
        notes.append("contact offset unknown; rotational share assumed zero (underestimates rotation)")
    cross = (
        r[1] * normal[2] - r[2] * normal[1],
        r[2] * normal[0] - r[0] * normal[2],
        r[0] * normal[1] - r[1] * normal[0],
    )
    temp = (
        sum(inverse[0][j] * cross[j] for j in range(3)),
        sum(inverse[1][j] * cross[j] for j in range(3)),
        sum(inverse[2][j] * cross[j] for j in range(3)),
    )
    k = sum(temp[i] * cross[i] for i in range(3))
    j_squared = impulse * impulse
    translational = j_squared / (2.0 * total_mass)
    rotational = 0.5 * j_squared * max(0.0, k)
    total = translational + rotational
    if not (
        math.isfinite(translational) and math.isfinite(rotational) and math.isfinite(total)
    ):
        return None, [
            "rotational energy partition not estimated: partition quantities overflowed "
            "for the screening surrogate"
        ]
    if energy is not None and total > 0.0:
        scale = energy / total
        translational *= scale
        rotational *= scale
        notes.append(
            "raw rigid-impulse partition scaled so T_trans + T_rot equals the "
            "reported impact energy (energy conservation)"
        )
    scaled_total = translational + rotational
    partition = {
        "model": "rigid_body_impulse_partition",
        "total_mass_kg": total_mass,
        "impulse_n_s": impulse,
        "translational_energy_j": translational,
        "rotational_energy_j": rotational,
        "translational_fraction": (translational / scaled_total) if scaled_total > 0.0 else 1.0,
        "rotational_fraction": (rotational / scaled_total) if scaled_total > 0.0 else 0.0,
        "contact_offset_m": list(r) if offset is not None else None,
        "notes": list(notes),
    }
    return partition, notes


def estimate_impact(
    mass_kg,
    velocity_m_s=None,
    fall_height_m=None,
    contact_normal=(0, 0, 1),
    restitution=0.0,
    contact_stiffness_n_per_m=None,
    stopping_distance_m=None,
    contact_duration_s=None,
    target_mass_kg=None,
    load_path_area_m2=None,
    load_path_lever_arm_m=None,
    section_modulus_m3=None,
    allowable_pa=None,
    orientation="face",
    g=9.80665,
    effective_modulus_pa=None,
    contact_radius_m=None,
    total_mass_kg=None,
    inertia_tensor_kg_m2=None,
    contact_location_m=None,
    center_of_mass_m=None,
):
    """Estimate impact response from energy and momentum balance.

    Closing velocity comes from ``velocity_m_s`` or, when absent, from
    ``fall_height_m`` via v = sqrt(2*g*h).  ``contact_normal`` is
    normalized and used to resolve the closing velocity into normal and
    tangential components: a free-fall velocity is assumed vertical, a
    directly supplied velocity is assumed along the contact normal.  The
    normal component drives energy, impulse, and peak force; the
    tangential component is reported as sliding-only and not resolved.

    Peak force uses the first available of contact stiffness (linear
    spring), the nonlinear Hertz point-contact law (``effective_modulus_pa``
    together with ``contact_radius_m``, F = (4/3)*E_eff*sqrt(r)*delta^(3/2)),
    stopping distance (average work-equivalent force F_avg = E/d with a
    documented conservative peak-force estimate), or contact duration
    (half-sine pulse F = pi*J/(2*t)).

    When ``inertia_tensor_kg_m2`` is supplied, an energy-partition estimate
    (translation vs rotation) is included in ``energy_partition`` using the
    total mass (``total_mass_kg``, defaulting to the effective mass) and,
    when ``contact_location_m`` (and optionally ``center_of_mass_m``) are
    given, the contact-location delta.  This is a screening estimate, not a
    rotation solver.

    Validation failures return a failed result carrying a flag.
    """
    try:
        mass = _finite(mass_kg, "mass_kg")
    except ValueError:
        return _failed(INVALID_MASS, "mass_kg must be numeric and finite")
    if mass <= 0.0:
        return _failed(INVALID_MASS, "mass_kg must be positive; got {!r}".format(mass_kg))
    try:
        restitution = _finite(restitution, "restitution")
    except ValueError:
        return _failed(INVALID_RESTITUTION, "restitution must be numeric and finite")
    if not 0.0 <= restitution <= 1.0:
        return _failed(INVALID_RESTITUTION, "restitution must be within [0, 1]; got {!r}".format(restitution))
    try:
        gravity = _finite(g, "g")
    except ValueError:
        return _failed(INVALID_KINEMATICS, "g must be numeric and finite")
    if gravity <= 0.0:
        return _failed(INVALID_KINEMATICS, "g must be positive")
    velocity = None
    if velocity_m_s is not None:
        try:
            velocity = _finite(velocity_m_s, "velocity_m_s")
        except ValueError:
            return _failed(INVALID_KINEMATICS, "velocity_m_s must be numeric and finite")
        if velocity < 0.0:
            return _failed(INVALID_KINEMATICS, "velocity_m_s must be non-negative")
    height = None
    if fall_height_m is not None:
        try:
            height = _finite(fall_height_m, "fall_height_m")
        except ValueError:
            return _failed(INVALID_KINEMATICS, "fall_height_m must be numeric and finite")
        if height < 0.0:
            return _failed(INVALID_KINEMATICS, "fall_height_m must be non-negative")
    if velocity is None and height is None:
        return _failed(INVALID_KINEMATICS, "no closing velocity: supply velocity_m_s or fall_height_m")
    from_fall = height is not None and velocity_m_s is None
    velocity, kinematics_text = _kinematics(velocity, height, gravity)
    if velocity <= 0.0:
        return _no_impact(mass, kinematics_text)
    try:
        normal, impact_angle_deg = _normalized_contact_normal(contact_normal)
    except ValueError as exc:
        return _failed(INVALID_CONTACT_NORMAL, str(exc))
    n_z = abs(normal[2])
    if from_fall:
        normal_velocity = velocity * n_z
        vertical_velocity = velocity
    else:
        normal_velocity = velocity
        vertical_velocity = velocity * n_z
    tangential_velocity = math.sqrt(max(0.0, velocity * velocity - normal_velocity * normal_velocity))
    if normal_velocity <= 0.0:
        return _no_impact(
            mass,
            "no normal closing velocity: free-fall velocity is perpendicular to the contact normal",
            normal=normal,
            angle=impact_angle_deg,
        )
    if from_fall:
        decomposition_text = (
            "contact normal normalized to n = ({:.4g}, {:.4g}, {:.4g}); angle {:.3g} deg from vertical;"
            " free-fall velocity resolved against the normal: v_n = {:.6g} m/s drives energy and"
            " impulse; tangential component v_t = {:.6g} m/s is sliding-only and not resolved".format(
                normal[0], normal[1], normal[2], impact_angle_deg, normal_velocity, tangential_velocity
            )
        )
    else:
        decomposition_text = (
            "contact normal normalized to n = ({:.4g}, {:.4g}, {:.4g}); angle {:.3g} deg from vertical;"
            " supplied closing velocity assumed along the normal: v_n = {:.6g} m/s drives energy and"
            " impulse; vertical component {:.6g} m/s; tangential component v_t = {:.6g} m/s is"
            " sliding-only and not resolved".format(
                normal[0], normal[1], normal[2], impact_angle_deg, normal_velocity,
                vertical_velocity, tangential_velocity,
            )
        )
    total_mass = None
    if total_mass_kg is not None:
        try:
            total_mass = _finite(total_mass_kg, "total_mass_kg")
        except ValueError:
            return _failed(INVALID_MASS, "total_mass_kg must be numeric and finite")
        if total_mass <= 0.0:
            return _failed(INVALID_MASS, "total_mass_kg must be positive; got {!r}".format(total_mass_kg))
    inertia = None
    if inertia_tensor_kg_m2 is not None:
        try:
            inertia = _validate_inertia_tensor(inertia_tensor_kg_m2)
        except ValueError as exc:
            return _failed(INVALID_INERTIA_TENSOR, str(exc))
    offset = None
    offset_notes = []
    if contact_location_m is not None or center_of_mass_m is not None:
        try:
            location = _vector3(contact_location_m, "contact_location_m") if contact_location_m is not None else None
            com = _vector3(center_of_mass_m, "center_of_mass_m") if center_of_mass_m is not None else None
        except ValueError as exc:
            return _failed(INVALID_CONTACT_OFFSET, str(exc))
        if location is None:
            return _failed(INVALID_CONTACT_OFFSET, "contact_location_m required when center_of_mass_m is supplied")
        if com is None:
            com = (0.0, 0.0, 0.0)
            offset_notes.append("center_of_mass_m not supplied; contact offset computed from the origin")
        offset = tuple(location[i] - com[i] for i in range(3))
    target = None
    if target_mass_kg is not None:
        try:
            target = _finite(target_mass_kg, "target_mass_kg")
        except ValueError:
            return _failed(INVALID_TARGET_MASS, "target_mass_kg must be numeric and finite")
        if target <= 0.0:
            return _failed(INVALID_TARGET_MASS, "target_mass_kg must be positive; got {!r}".format(target_mass_kg))
    if target is None:
        effective_mass = mass
        target_text = "target modeled as rigid with infinite mass; effective mass equals falling mass"
    else:
        effective_mass = mass * target / (mass + target)
        target_text = "effective mass is reduced mass: m_eff = m*m_t/(m+m_t) = {:.6g} kg".format(effective_mass)
    partition_mass = total_mass if total_mass is not None else effective_mass
    energy = 0.5 * effective_mass * normal_velocity * normal_velocity
    impulse = effective_mass * (1.0 + restitution) * normal_velocity
    if (
        not math.isfinite(energy)
        or not math.isfinite(impulse)
        or (normal_velocity > 0.0 and energy <= 0.0)
    ):
        return _failed(
            INVALID_KINEMATICS,
            "impact energy or impulse is not finite; input magnitudes out of range "
            "for the screening surrogate",
        )
    values = {}
    for name, value, flag in (
        ("contact_stiffness_n_per_m", contact_stiffness_n_per_m, INVALID_STIFFNESS),
        ("stopping_distance_m", stopping_distance_m, INVALID_STOPPING_DISTANCE),
        ("contact_duration_s", contact_duration_s, INVALID_CONTACT_DURATION),
    ):
        if value is None:
            continue
        try:
            value = _finite(value, name)
        except ValueError:
            return _failed(flag, "{} must be numeric and finite".format(name))
        if value <= 0.0:
            return _failed(flag, "{} must be positive; got {!r}".format(name, value))
        values[name] = value
    stiffness = values.get("contact_stiffness_n_per_m")
    stopping = values.get("stopping_distance_m")
    duration = values.get("contact_duration_s")
    hertz = None
    if stiffness is None and (effective_modulus_pa is not None or contact_radius_m is not None):
        if effective_modulus_pa is None or contact_radius_m is None:
            return _failed(
                INVALID_STIFFNESS,
                "effective_modulus_pa and contact_radius_m must be supplied together "
                "for the Hertz nonlinear contact model",
            )
        try:
            modulus = _finite(effective_modulus_pa, "effective_modulus_pa")
        except ValueError:
            return _failed(INVALID_STIFFNESS, "effective_modulus_pa must be numeric and finite")
        if modulus <= 0.0:
            return _failed(INVALID_STIFFNESS, "effective_modulus_pa must be positive; got {!r}".format(effective_modulus_pa))
        try:
            radius = _finite(contact_radius_m, "contact_radius_m")
        except ValueError:
            return _failed(INVALID_CONTACT_RADIUS, "contact_radius_m must be numeric and finite")
        if radius <= 0.0:
            return _failed(INVALID_CONTACT_RADIUS, "contact_radius_m must be positive; got {!r}".format(contact_radius_m))
        hertz = (modulus, radius)
    force_estimated = True
    average_force = None
    peak_force_estimate = None
    contact_model = CONTACT_MODEL_LINEAR
    if stiffness is not None:
        peak_force = normal_velocity * math.sqrt(effective_mass * stiffness)
        contact_duration = math.pi * math.sqrt(effective_mass / stiffness) / 2.0
        compression = normal_velocity * math.sqrt(effective_mass / stiffness)
        force_text = (
            "peak force from linear spring: F = v_n*sqrt(m_eff*k);"
            " t = pi*sqrt(m_eff/k)/2; contact duration covers the compression"
            " phase; full contact ~ (1+e)*t"
        )
        contact_model = CONTACT_MODEL_LINEAR
        peak_force_estimate = peak_force
    elif hertz is not None:
        modulus, radius = hertz
        k_h = (4.0 / 3.0) * modulus * math.sqrt(radius)
        compression = (
            (5.0 / 4.0) * effective_mass * normal_velocity * normal_velocity / k_h
        ) ** (2.0 / 5.0)
        peak_force = k_h * compression ** 1.5
        contact_duration = (
            HERTZ_CONTACT_DURATION_FACTOR
            * (1.0 + restitution)
            / 2.0
            * compression
            / normal_velocity
        )
        force_text = (
            "peak force from Hertz nonlinear point contact: F = (4/3)*E_eff*sqrt(r)*delta^(3/2)"
            " with E_eff = {:.6g} Pa, r = {:.6g} m; delta_max = ((5/4)*m_eff*v_n^2/k_H)^(2/5)"
            " = {:.6g} m; compression-phase contact duration t = {:.3g}*(1+e)/2*delta_max/v_n"
            " (full elastic contact t = {:.3g}*delta_max/v_n)".format(
                modulus, radius, compression, HERTZ_CONTACT_DURATION_FACTOR,
                HERTZ_CONTACT_DURATION_FACTOR,
            )
        )
        contact_model = CONTACT_MODEL_HERTZ_NONLINEAR
        peak_force_estimate = peak_force
    elif stopping is not None:
        average_force = energy / stopping
        peak_force = average_force
        peak_force_estimate = PEAK_FORCE_CONSERVATIVE_FACTOR * average_force
        contact_duration = 2.0 * stopping / normal_velocity
        compression = stopping
        force_text = (
            "average work-equivalent force from energy over stopping distance:"
            " F_avg = E/d = {:.6g} N; conservative peak-force estimate"
            " F_peak ~ {:.6g} N = {:.6g}*F_avg (triangular force-pulse assumption)".format(
                average_force, peak_force_estimate, PEAK_FORCE_CONSERVATIVE_FACTOR
            )
        )
        contact_model = CONTACT_MODEL_STOPPING_DISTANCE
    elif duration is not None:
        peak_force = math.pi * impulse / (2.0 * duration)
        contact_duration = duration
        compression = energy / peak_force
        force_text = (
            "peak force from half-sine pulse: F = pi*J/(2*t); contact duration"
            " covers the compression phase; full contact ~ (1+e)*t"
        )
        contact_model = CONTACT_MODEL_HALF_SINE
        peak_force_estimate = peak_force
    else:
        peak_force = 0.0
        contact_duration = 0.0
        compression = 0.0
        force_estimated = False
        force_text = "peak force not estimated: supply contact_stiffness_n_per_m, stopping_distance_m, or contact_duration_s (PEAK_FORCE_NOT_ESTIMATED)"
    response_force = peak_force_estimate if peak_force_estimate is not None else peak_force
    peak_acceleration = response_force / mass
    computed = (energy, impulse, peak_force, peak_acceleration, contact_duration, compression)
    if not all(math.isfinite(value) for value in computed):
        return _failed(
            INVALID_KINEMATICS,
            "impact estimate overflowed; input magnitudes out of range for the screening surrogate",
        )
    stress_label = "conservative peak-force estimate" if stopping is not None else "peak force"
    if force_estimated:
        stress, stress_texts = _load_path_stress(
            response_force, load_path_area_m2, load_path_lever_arm_m, section_modulus_m3, stress_label
        )
        if isinstance(stress, ImpactResult):
            return stress
    else:
        stress, stress_texts = None, ["load-path stress not computed: peak force not estimated"]
    safety_factor = "not_available"
    if allowable_pa is not None:
        try:
            allowable = _finite(allowable_pa, "allowable_pa")
        except ValueError:
            return _failed(INVALID_LOAD_PATH, "allowable_pa must be numeric and finite")
        if allowable <= 0.0:
            return _failed(INVALID_LOAD_PATH, "allowable_pa must be positive; got {!r}".format(allowable_pa))
        if stress is not None and stress > 0.0:
            safety_factor = allowable / stress
        else:
            stress_texts.append("safety_factor not_available: load-path stress not computed")
    elif stress is not None:
        stress_texts.append("safety_factor not_available: allowable_pa not supplied")
    partition = None
    partition_notes = []
    if inertia is not None:
        partition, partition_notes = _energy_partition(
            partition_mass, inertia, normal, effective_mass * normal_velocity, offset,
            energy=energy,
        )
        if partition is not None:
            partition_notes.append(
                "energy partition (translation vs rotation) estimated with a free-body"
                " rigid-impulse model: J at the contact offset along the contact normal"
                " splits kinetic energy into T_trans = J^2/(2*M) and"
                " T_rot = 0.5*(r x J)*I^-1*(r x J); both components scaled so"
                " T_trans + T_rot equals the reported impact energy; screening"
                " estimate only, rotation response not resolved"
            )
    assumptions = [
        "method {}: quasi-static energy balance, exploration only".format(METHOD_ID),
        "model {}: screening surrogate, not a validated dynamics solver".format(SCREENING_SURROGATE_MODEL_ID),
        "{} ({})".format(CONTACT_PATCH_ASSUMPTION_TEXT, CONTACT_PATCH_ASSUMPTION),
        kinematics_text,
        decomposition_text,
        target_text,
        force_text,
        "impact orientation: {!r}; restitution {:.4g} applied to impulse only".format(orientation, restitution),
        "unsupported failure modes: {}".format(", ".join(IMPACT_UNSUPPORTED_FAILURE_MODES)),
    ] + stress_texts + offset_notes + partition_notes
    if stiffness is not None and (effective_modulus_pa is not None or contact_radius_m is not None):
        assumptions.append(
            "Hertz nonlinear contact parameters (effective_modulus_pa, contact_radius_m) ignored:"
            " explicit contact_stiffness_n_per_m takes precedence"
        )
    result_flags = [CONTACT_PATCH_ASSUMPTION]
    validity = "valid"
    if not force_estimated:
        result_flags.extend((PEAK_FORCE_NOT_ESTIMATED, INSUFFICIENT_PARAMETERS))
        validity = "inconclusive"
    if force_estimated and peak_acceleration > _ACCELERATION_PLAUSIBILITY_LIMIT_M_S2:
        result_flags.append(IMPACT_ACCELERATION_IMPLAUSIBLE)
        assumptions.append(
            "peak deceleration {:.6g} m/s^2 exceeds the {:.6g} m/s^2 plausibility"
            " limit; contact stiffness and/or closing speed are implausibly high"
            " for the reported body mass ({}; evidence_blocking)".format(
                peak_acceleration, _ACCELERATION_PLAUSIBILITY_LIMIT_M_S2,
                IMPACT_ACCELERATION_IMPLAUSIBLE,
            )
        )
        validity = "inconclusive"
    if stress is not None and stress > _STRESS_PLAUSIBILITY_LIMIT_PA:
        result_flags.append(IMPACT_STRESS_IMPLAUSIBLE)
        assumptions.append(
            "load-path stress {:.6g} Pa exceeds the {:.6g} Pa plausibility limit;"
            " the screening proxy is not physically meaningful at this magnitude"
            " ({}; evidence_blocking)".format(
                stress, _STRESS_PLAUSIBILITY_LIMIT_PA, IMPACT_STRESS_IMPLAUSIBLE
            )
        )
        validity = "inconclusive"
    return ImpactResult(
        impact_energy_j=energy,
        closing_velocity_m_s=velocity,
        effective_mass_kg=effective_mass,
        impulse_n_s=impulse,
        peak_force_n=peak_force,
        peak_acceleration_m_s2=peak_acceleration,
        contact_duration_s=contact_duration,
        contact_compression_m=compression,
        flags=tuple(result_flags),
        assumptions=tuple(assumptions),
        validity=validity,
        load_path_stress_pa=stress,
        safety_factor=safety_factor,
        average_force_n=average_force,
        peak_force_estimate_n=peak_force_estimate,
        contact_model=contact_model,
        contact_normal=normal,
        impact_angle_deg=impact_angle_deg,
        effective_normal_velocity_m_s=normal_velocity,
        vertical_velocity_component_m_s=vertical_velocity,
        tangential_velocity_m_s=tangential_velocity,
        energy_partition=partition,
    )


def _load_path_stress(force, area, lever, modulus, force_label="peak force"):
    """Compute a scalar load-path stress screening proxy (axial plus bending).

    The result is a scalar screening proxy based on the reported force
    (``force_label``), not a component-level stress prediction: it does not
    resolve local geometry, stress concentrations, or failure modes.  On
    invalid inputs it returns a failed result and an empty text list.
    """
    values = {}
    for name, value in (
        ("load_path_area_m2", area),
        ("load_path_lever_arm_m", lever),
        ("section_modulus_m3", modulus),
    ):
        if value is None:
            continue
        try:
            value = _finite(value, name)
        except ValueError:
            return _failed(INVALID_LOAD_PATH, "{} must be numeric and finite".format(name)), ()
        if value < 0.0 or (name != "load_path_lever_arm_m" and value == 0.0):
            return _failed(INVALID_LOAD_PATH, "{} must be positive; got {!r}".format(name, value)), ()
        values[name] = value
    area = values.get("load_path_area_m2")
    lever = values.get("load_path_lever_arm_m")
    modulus = values.get("section_modulus_m3")
    stress = None
    texts = []
    if area is not None:
        stress = force / area
        texts.append(
            "scalar load-path stress screening proxy (axial, {}): sigma = F/A = {:.6g} Pa".format(
                force_label, stress
            )
        )
    if lever is not None and modulus is not None:
        bending = force * lever / modulus
        stress = (stress or 0.0) + bending
        texts.append(
            "scalar load-path stress screening proxy (bending, {}): sigma = F*L/Z = {:.6g} Pa".format(
                force_label, bending
            )
        )
    elif (lever is None) != (modulus is None):
        texts.append("bending omitted: load_path_lever_arm_m and section_modulus_m3 must both be supplied")
    if stress is None:
        texts.append("load-path stress not computed: load_path_area_m2 required")
    if stress is not None and not math.isfinite(stress):
        return _failed(
            INVALID_LOAD_PATH,
            "load-path stress overflowed; input magnitudes out of range for the screening surrogate",
        ), ()
    return stress, texts


def _cycles_to_failure(stress, curve, fatigue_strength_at_1e6_pa=None, fatigue_exponent_k=None):
    """Cycles to failure: exact S-N point, conservative curve bound, or
    per-material Basquin power law N = 1e6*(sigma_ref/sigma)^k.

    Returns ``(life, used_generic_fallback)``.  Without an explicit curve,
    the Basquin law is evaluated with the supplied material fatigue
    strength at 1e6 cycles and slope k; when either is missing the
    conservative generic polymer law (14 MPa @ 1e6, slope 6) is used and
    ``used_generic_fallback`` is True so the caller can disclose it.  The
    life is floored at 1 cycle and capped so astronomically low stresses
    cannot overflow into inf and corrupt JSON output.
    """
    if curve:
        if stress in curve:
            return max(1.0, float(curve[stress])), False
        candidates = [float(life) for key, life in curve.items() if float(key) >= stress]
        if candidates:
            return min(candidates), False
        return min(max(1.0, float(life)) for life in curve.values()), False
    sigma_ref = fatigue_strength_at_1e6_pa
    exponent = fatigue_exponent_k
    if sigma_ref is None or exponent is None:
        sigma_ref = GENERIC_FATIGUE_STRENGTH_AT_1E6_PA
        exponent = GENERIC_FATIGUE_EXPONENT_K
        used_generic = True
    else:
        used_generic = False
    ratio = sigma_ref / stress
    try:
        life = 1e6 * ratio ** exponent
    except OverflowError:
        life = float("inf")
    if not math.isfinite(life) or life > _MAX_SCREENING_LIFE_CYCLES:
        life = _MAX_SCREENING_LIFE_CYCLES
    return max(1.0, life), used_generic


def _cycle_levels(cycles_n, stress_amplitude_pa):
    """Normalize cycles input to ``[(count, stress_pa), ...]`` levels."""

    def _count(value):
        number = _finite(value, "cycle count")
        if number < 0.0:
            raise ValueError("cycle counts must be non-negative")
        return number

    if isinstance(cycles_n, (int, float)) and not isinstance(cycles_n, bool):
        if stress_amplitude_pa is None:
            raise ValueError("stress_amplitude_pa required for single cycle level")
        return [(_count(cycles_n), _finite(stress_amplitude_pa, "stress_amplitude_pa"))]
    levels = []
    for item in cycles_n:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            levels.append((_count(item), _finite(stress_amplitude_pa, "stress_amplitude_pa")))
            continue
        try:
            count, stress = item
        except (TypeError, ValueError):
            raise ValueError("cycle levels must be (count, stress_pa) pairs")
        levels.append((_count(count), _finite(stress, "stress_pa")))
    return levels


def repeat_impact_cycles(
    cycles_n,
    stress_amplitude_pa=None,
    s_n_curve=None,
    allowable_pa=None,
    fatigue_strength_at_1e6_pa=None,
    fatigue_exponent_k=None,
):
    """Miner's-rule cumulative damage over repeated impact stress cycles.

    ``cycles_n`` is either a single count for ``stress_amplitude_pa`` or
    an iterable of ``(count, stress_pa)`` levels.  Cycles-to-failure comes
    from an exact S-N dict point, the most conservative curve bound, or a
    per-material Basquin power law N = 1e6*(sigma_ref/sigma)^k with
    ``fatigue_strength_at_1e6_pa`` and ``fatigue_exponent_k``; when either
    is missing the generic polymer law is used and the result carries the
    ``FATIGUE_GENERIC_FALLBACK`` flag and a disclosure assumption.  The
    label is explicit: this is a coarse screening estimate, not a fatigue
    prediction.
    """
    levels = _cycle_levels(cycles_n, stress_amplitude_pa)
    entries = []
    total_cycles = 0.0
    damage = 0.0
    generic_fallback_used = False
    for count, stress in levels:
        if stress <= 0.0:
            continue
        life, used_generic = _cycles_to_failure(
            stress, s_n_curve or {}, fatigue_strength_at_1e6_pa, fatigue_exponent_k
        )
        generic_fallback_used = generic_fallback_used or used_generic
        total_cycles += count
        damage += count / life
        entries.append({"cycles": count, "stress_pa": stress, "cycles_to_failure": life})
    # Epsilon: exactly one lifetime of exposure (damage_sum == 1.0) is
    # exhaustion; a 1e-9 tolerance keeps float rounding from hiding it.
    exceeded = damage >= 1.0 - 1e-9
    allowable_exceeded = False
    if allowable_pa is not None:
        allowable = _finite(allowable_pa, "allowable_pa")
        allowable_exceeded = any(stress > allowable for _, stress in levels if stress > 0.0)
    flags = [FATIGUE_ESTIMATE_EXCEEDED] if exceeded else []
    assumptions = []
    if generic_fallback_used:
        flags.append(FATIGUE_GENERIC_FALLBACK)
        assumptions.append(FATIGUE_GENERIC_FALLBACK_ASSUMPTION)
    return {
        "damage_sum": damage,
        "miner_exceeded": exceeded,
        "cycles_evaluated": total_cycles,
        "flags": flags,
        "label": "coarse screening estimate, not fatigue prediction",
        "levels": entries,
        "allowable_exceeded": allowable_exceeded,
        "assumptions": assumptions,
    }


def _method_approved(method):
    if isinstance(method, Mapping):
        approved = bool(method.get("approved_for_qualification", False))
        state = str(method.get("approval_state", "") or "").casefold()
    else:
        approved = bool(getattr(method, "approved_for_qualification", False))
        state = getattr(method, "approval_state", "")
        if not isinstance(state, str):
            state = getattr(state, "value", "")
        state = str(state).casefold()
    return approved and state == "approved"


def impact_qualification_status(method=None, validated=False):
    """Impact estimates are blocked unless validated and method approved.

    ``method=None`` is allowed when validated; a supplied method must be
    approved for qualification with an approved approval state.
    """
    validated = bool(validated)
    approved = True if method is None else _method_approved(method)
    qualified = validated and approved
    if qualified:
        disposition = EvidenceDisposition.QUALIFICATION_PENDING_REVIEW.value
        reason = "impact/energy estimate is validated and method is approved; qualification pending review"
    else:
        disposition = EvidenceDisposition.QUALIFICATION_BLOCKED.value
        reasons = []
        if not validated:
            reasons.append("no validated evidence")
        if method is not None and not approved:
            reasons.append("method not approved for qualification")
        reason = "impact/energy qualification blocked: " + "; ".join(reasons)
    return {"qualified": qualified, "disposition": disposition, "reason": reason}


def desk_edge_impact(mass_kg, velocity_m_s, contact_radius_m, shell_stiffness_n_per_m=None, **kwargs):
    """Desk-edge impact helper with an explicit contact-radius assumption.

    Changing contact geometry enters through the contact-radius stiffness
    assumption.  When ``shell_stiffness_n_per_m`` is absent, contact is
    modeled with the nonlinear Hertz point-contact law
    F = (4/3)*E_eff*sqrt(r)*delta^(3/2) with an assumed effective modulus
    E_eff = 1e9 Pa and the peak force follows from energy balance
    (``contact_model`` = ``hertz_nonlinear``).  When an explicit
    ``shell_stiffness_n_per_m`` is supplied it is used as a calibrated
    linear stiffness (``contact_model`` = ``linear_calibrated``).
    """
    if "contact_stiffness_n_per_m" in kwargs:
        raise ValueError("desk_edge_impact: use shell_stiffness_n_per_m instead of contact_stiffness_n_per_m")
    try:
        radius = _finite(contact_radius_m, "contact_radius_m")
    except ValueError:
        return _failed(INVALID_CONTACT_RADIUS, "contact_radius_m must be numeric and finite")
    if radius <= 0.0:
        return _failed(INVALID_CONTACT_RADIUS, "contact_radius_m must be positive; got {!r}".format(contact_radius_m))
    if shell_stiffness_n_per_m is None:
        result = estimate_impact(
            mass_kg,
            velocity_m_s,
            effective_modulus_pa=HERTZ_EFFECTIVE_MODULUS_DEFAULT_PA,
            contact_radius_m=radius,
            **kwargs,
        )
        contact_model = CONTACT_MODEL_HERTZ_NONLINEAR
        stiffness_text = (
            " contact stiffness modeled with the nonlinear Hertz point-contact law"
            " F = (4/3)*E_eff*sqrt(r)*delta^(3/2) with E_eff = 1e9 Pa (crude assumption);"
            " peak force from energy balance"
        )
    else:
        try:
            stiffness = _finite(shell_stiffness_n_per_m, "shell_stiffness_n_per_m")
        except ValueError:
            return _failed(INVALID_STIFFNESS, "shell_stiffness_n_per_m must be numeric and finite")
        if stiffness <= 0.0:
            return _failed(INVALID_STIFFNESS, "shell_stiffness_n_per_m must be positive; got {!r}".format(shell_stiffness_n_per_m))
        result = estimate_impact(mass_kg, velocity_m_s, contact_stiffness_n_per_m=stiffness, **kwargs)
        contact_model = CONTACT_MODEL_LINEAR_CALIBRATED
        stiffness_text = (
            " contact stiffness supplied explicitly as a calibrated linear stiffness"
            " k = {:.6g} N/m".format(stiffness)
        )
    assumption = (
        "desk edge approximates contact geometry as sphere-on-edge with radius r = {:.6g} m;"
        " changing contact geometry enters through the contact-radius stiffness assumption".format(radius)
        + stiffness_text
    )
    return replace(
        result,
        contact_model=contact_model,
        flags=(DESK_EDGE_CONTACT_APPROXIMATION,) + result.flags,
        assumptions=result.assumptions + (assumption,),
    )


__all__ = [
    "CONTACT_MODEL_HERTZ_NONLINEAR",
    "CONTACT_MODEL_LINEAR",
    "CONTACT_MODEL_LINEAR_CALIBRATED",
    "CONTACT_PATCH_ASSUMPTION",
    "DESK_EDGE_CONTACT_APPROXIMATION",
    "FATIGUE_ESTIMATE_EXCEEDED",
    "FATIGUE_GENERIC_FALLBACK",
    "HERTZ_CONTACT_DURATION_FACTOR",
    "IMPACT_ACCELERATION_IMPLAUSIBLE",
    "IMPACT_STRESS_IMPLAUSIBLE",
    "IMPACT_UNSUPPORTED_FAILURE_MODES",
    "INVALID_CONTACT_OFFSET",
    "INVALID_INERTIA_TENSOR",
    "ImpactResult",
    "PEAK_FORCE_NOT_ESTIMATED",
    "SCREENING_SURROGATE_MODEL_ID",
    "UNSUPPORTED_BATTERY_CRUSH",
    "UNSUPPORTED_DELAMINATION",
    "UNSUPPORTED_FRACTURE",
    "UNSUPPORTED_PCB_SHOCK",
    "UNSUPPORTED_SCREW_PULLOUT",
    "desk_edge_impact",
    "estimate_impact",
    "impact_qualification_status",
    "repeat_impact_cycles",
]
