"""Energy-based MVP impact estimate (exploration only).

Quasi-static screening: closing velocity, impulse, peak force, peak
acceleration, contact duration, and load-path stress from energy and
momentum balance.  Output is exploration-only; impact qualification is
blocked unless the analysis method is approved and validated evidence
exists.  Failure modes that need detailed simulation (battery crush,
PCB shock, fracture, delamination, screw pull-out) are always reported
as unsupported.
"""

from dataclasses import dataclass, replace
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
INSUFFICIENT_PARAMETERS = "INSUFFICIENT_PARAMETERS"
PEAK_FORCE_NOT_ESTIMATED = "PEAK_FORCE_NOT_ESTIMATED"
CONTACT_PATCH_ASSUMPTION_TEXT = (
    "contact patch approximated as point contact; local contact stresses not resolved"
)
METHOD_ID = "energy_quasi_static_v1"


@dataclass(frozen=True)
class ImpactResult:
    """Immutable result of an energy-based quasi-static impact estimate.

    ``validity`` is ``valid``, ``no_impact`` (no closing velocity), or
    ``failed`` (invalid inputs, flagged).  ``safety_factor`` is a float
    when computed and the marker string ``not_available`` otherwise.
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


def _no_impact(mass_kg, reason):
    return ImpactResult(
        impact_energy_j=0.0,
        closing_velocity_m_s=0.0,
        effective_mass_kg=mass_kg,
        impulse_n_s=0.0,
        peak_force_n=0.0,
        peak_acceleration_m_s2=0.0,
        contact_duration_s=0.0,
        contact_compression_m=0.0,
        assumptions=(reason, "no impact event evaluated"),
        validity="no_impact",
    )


def _kinematics(velocity, height, gravity):
    if velocity is not None:
        return velocity, "closing velocity supplied directly: v = {:.6g} m/s".format(velocity)
    speed = math.sqrt(2.0 * gravity * max(height, 0.0)) if height > 0.0 else 0.0
    return speed, "closing velocity from free fall: v = sqrt(2*g*h) = {:.6g} m/s".format(speed)


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
):
    """Estimate impact response from energy and momentum balance.

    Closing velocity comes from ``velocity_m_s`` or, when absent, from
    ``fall_height_m`` via v = sqrt(2*g*h).  Peak force uses the first
    available of contact stiffness (linear spring), stopping distance
    (F = E/d), or contact duration (half-sine pulse F = pi*J/(2*t)).
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
    height = None
    if fall_height_m is not None:
        try:
            height = _finite(fall_height_m, "fall_height_m")
        except ValueError:
            return _failed(INVALID_KINEMATICS, "fall_height_m must be numeric and finite")
    if velocity is None and height is None:
        return _failed(INVALID_KINEMATICS, "no closing velocity: supply velocity_m_s or fall_height_m")
    velocity, kinematics_text = _kinematics(velocity, height, gravity)
    if velocity <= 0.0:
        return _no_impact(mass, kinematics_text)
    if not isinstance(contact_normal, (tuple, list)) or len(contact_normal) != 3:
        return _failed(INVALID_CONTACT_NORMAL, "contact_normal must be a 3-component vector")
    try:
        normal = tuple(float(item) for item in contact_normal)
    except (TypeError, ValueError):
        return _failed(INVALID_CONTACT_NORMAL, "contact_normal components must be numeric")
    if not all(math.isfinite(item) for item in normal) or all(item == 0.0 for item in normal):
        return _failed(INVALID_CONTACT_NORMAL, "contact_normal must be finite and nonzero")
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
    energy = 0.5 * effective_mass * velocity * velocity
    impulse = effective_mass * (1.0 + restitution) * velocity
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
    force_estimated = True
    if stiffness is not None:
        peak_force = velocity * math.sqrt(effective_mass * stiffness)
        contact_duration = math.pi * math.sqrt(effective_mass / stiffness) / 2.0
        compression = velocity * math.sqrt(effective_mass / stiffness)
        force_text = "peak force from linear spring: F = v*sqrt(m_eff*k); t = pi*sqrt(m_eff/k)/2"
        force_estimated = True
    elif stopping is not None:
        peak_force = energy / stopping
        contact_duration = 2.0 * stopping / velocity
        compression = stopping
        force_text = "peak force from energy over stopping distance: F = E/d; t = 2*d/v"
        force_estimated = True
    elif duration is not None:
        peak_force = math.pi * impulse / (2.0 * duration)
        contact_duration = duration
        compression = energy / peak_force
        force_text = "peak force from half-sine pulse: F = pi*J/(2*t)"
        force_estimated = True
    else:
        peak_force = 0.0
        contact_duration = 0.0
        compression = 0.0
        force_estimated = False
        force_text = "peak force not estimated: supply contact_stiffness_n_per_m, stopping_distance_m, or contact_duration_s (PEAK_FORCE_NOT_ESTIMATED)"
    peak_acceleration = peak_force / effective_mass
    if force_estimated:
        stress, stress_texts = _load_path_stress(
            peak_force, load_path_area_m2, load_path_lever_arm_m, section_modulus_m3
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
    assumptions = [
        "method {}: quasi-static energy balance, exploration only".format(METHOD_ID),
        "{} ({})".format(CONTACT_PATCH_ASSUMPTION_TEXT, CONTACT_PATCH_ASSUMPTION),
        kinematics_text,
        target_text,
        force_text,
        "impact orientation: {!r}; restitution {:.4g} applied to impulse only".format(orientation, restitution),
        "unsupported failure modes: {}".format(", ".join(IMPACT_UNSUPPORTED_FAILURE_MODES)),
    ] + stress_texts
    return ImpactResult(
        impact_energy_j=energy,
        closing_velocity_m_s=velocity,
        effective_mass_kg=effective_mass,
        impulse_n_s=impulse,
        peak_force_n=peak_force,
        peak_acceleration_m_s2=peak_acceleration,
        contact_duration_s=contact_duration,
        contact_compression_m=compression,
        flags=(CONTACT_PATCH_ASSUMPTION,) if force_estimated else (CONTACT_PATCH_ASSUMPTION, PEAK_FORCE_NOT_ESTIMATED),
        assumptions=tuple(assumptions),
        validity="valid",
        load_path_stress_pa=stress,
        safety_factor=safety_factor,
    )


def _load_path_stress(peak_force, area, lever, modulus):
    """Compute axial plus bending load-path stress, or a failed result."""
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
        stress = peak_force / area
        texts.append("axial load-path stress: sigma = F/A = {:.6g} Pa".format(stress))
    if lever is not None and modulus is not None:
        bending = peak_force * lever / modulus
        stress = (stress or 0.0) + bending
        texts.append("bending load-path stress: sigma = F*L/Z = {:.6g} Pa".format(bending))
    elif (lever is None) != (modulus is None):
        texts.append("bending omitted: load_path_lever_arm_m and section_modulus_m3 must both be supplied")
    if stress is None:
        texts.append("load-path stress not computed: load_path_area_m2 required")
    return stress, texts


def _cycles_to_failure(stress, curve):
    """Cycles to failure: exact S-N point, conservative curve bound, or power law."""
    if curve:
        if stress in curve:
            return max(1.0, float(curve[stress]))
        candidates = [float(life) for key, life in curve.items() if float(key) >= stress]
        if candidates:
            return min(candidates)
        return min(max(1.0, float(life)) for life in curve.values())
    # Conservative screening power law: 1e6 cycles at 1 MPa, exponent 3.
    return max(1.0, 1e6 * (1e6 / stress) ** 3)


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


def repeat_impact_cycles(cycles_n, stress_amplitude_pa=None, s_n_curve=None, allowable_pa=None):
    """Miner's-rule cumulative damage over repeated impact stress cycles.

    ``cycles_n`` is either a single count for ``stress_amplitude_pa`` or
    an iterable of ``(count, stress_pa)`` levels.  Cycles-to-failure comes
    from an exact S-N dict point, the most conservative curve bound, or a
    coarse power-law estimate.  The label is explicit: this is a coarse
    screening estimate, not a fatigue prediction.
    """
    levels = _cycle_levels(cycles_n, stress_amplitude_pa)
    entries = []
    total_cycles = 0.0
    damage = 0.0
    for count, stress in levels:
        if stress <= 0.0:
            continue
        life = _cycles_to_failure(stress, s_n_curve or {})
        total_cycles += count
        damage += count / life
        entries.append({"cycles": count, "stress_pa": stress, "cycles_to_failure": life})
    exceeded = damage >= 1.0
    allowable_exceeded = False
    if allowable_pa is not None:
        allowable = _finite(allowable_pa, "allowable_pa")
        allowable_exceeded = any(stress > allowable for _, stress in levels if stress > 0.0)
    return {
        "damage_sum": damage,
        "miner_exceeded": exceeded,
        "cycles_evaluated": total_cycles,
        "flags": [FATIGUE_ESTIMATE_EXCEEDED] if exceeded else [],
        "label": "coarse screening estimate, not fatigue prediction",
        "levels": entries,
        "allowable_exceeded": allowable_exceeded,
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
    assumption: when ``shell_stiffness_n_per_m`` is absent, stiffness is
    estimated by Hertz-like scaling k = (4/3)*E_eff*sqrt(r) with an
    assumed effective modulus E_eff = 1e9 Pa.
    """
    if "contact_stiffness_n_per_m" in kwargs:
        raise ValueError("desk_edge_impact: use shell_stiffness_n_per_m instead of contact_stiffness_n_per_m")
    try:
        radius = _finite(contact_radius_m, "contact_radius_m")
    except ValueError:
        return _failed(INVALID_CONTACT_RADIUS, "contact_radius_m must be numeric and finite")
    if radius <= 0.0:
        return _failed(INVALID_CONTACT_RADIUS, "contact_radius_m must be positive; got {!r}".format(contact_radius_m))
    stiffness = shell_stiffness_n_per_m
    stiffness_text = ""
    if stiffness is None:
        stiffness = (4.0 / 3.0) * 1e9 * math.sqrt(radius)
        stiffness_text = (
            " contact stiffness estimated from contact radius: k = (4/3)*E_eff*sqrt(r)"
            " with E_eff = 1e9 Pa (crude assumption)"
        )
    else:
        try:
            stiffness = _finite(stiffness, "shell_stiffness_n_per_m")
        except ValueError:
            return _failed(INVALID_STIFFNESS, "shell_stiffness_n_per_m must be numeric and finite")
        if stiffness <= 0.0:
            return _failed(INVALID_STIFFNESS, "shell_stiffness_n_per_m must be positive; got {!r}".format(shell_stiffness_n_per_m))
    result = estimate_impact(mass_kg, velocity_m_s, contact_stiffness_n_per_m=stiffness, **kwargs)
    assumption = (
        "desk edge approximates contact geometry as sphere-on-edge with radius r = {:.6g} m;"
        " changing contact geometry enters through the contact-radius stiffness assumption".format(radius)
        + stiffness_text
    )
    return replace(
        result,
        flags=(DESK_EDGE_CONTACT_APPROXIMATION,) + result.flags,
        assumptions=result.assumptions + (assumption,),
    )


__all__ = [
    "CONTACT_PATCH_ASSUMPTION",
    "DESK_EDGE_CONTACT_APPROXIMATION",
    "FATIGUE_ESTIMATE_EXCEEDED",
    "IMPACT_UNSUPPORTED_FAILURE_MODES",
    "ImpactResult",
    "PEAK_FORCE_NOT_ESTIMATED",
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
