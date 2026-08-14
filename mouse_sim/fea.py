"""Per-vertex FEA display post-processor (display-only, deterministic).

This module turns the AUTHORITATIVE shell result into a per-vertex
visualization field for the frontend: a REAL simply-supported plate
bending stress/damage distribution over the whole shell (Navier double
series, the structural solver's closed form), a local plastic dent
displacement field along the contact normal (impact Gaussian), and
analytic ``procedural`` records for analytic primitives so the shader can
evaluate the dent Gaussian without a mesh.

DISPLAY-ONLY GUARANTEE: :func:`compute_fea` never modifies the sections
it reads (``result["shell"]``, ``result["drop_simulation"]``, ...) and is
wired into the pipeline AFTER ``_assemble_shell_result`` completes.  It
cannot raise: any internal failure degrades to a fail-open payload with
``computed: False`` and a ``FEA_COMPUTE_FAILED`` flag.

MODEL (screening quality, disclosed — never an FEA claim):

- Hotspot/impact center: the peak drop impact's ``contact_location``
  (world frame) from ``result["drop_simulation"]`` — the impact with the
  maximum ``impact_speed_m_s`` (the same selection the simulator uses).
  Without a drop simulation the center falls back to the shell critical
  region and ``impact_window_s = 0.0`` (no dent animation).
- Peak stress sigma_peak = ``result["shell"]["peak_stress_pa"]`` (the
  authoritative closed-form value).  Yield stress sigma_yield =
  sigma_peak * min_safety_factor when both are finite and the factor is
  positive, so the peak damage D_peak = min(1, 1/SF) is consistent with
  the shell verdict; ``damage_basis`` records which reference was used
  (``"derated_allowable"`` — the derated tensile allowable behind the
  shell safety factor).  Without a safety factor the yield stress is read
  from the RESOLVED shell material's trace properties (a persisted
  ``derated_tensile_allowable_pa``, then ``yield_strength``, then
  ``tensile_allowable``; first finite positive value) with
  ``damage_basis`` ``"material_allowable"`` / ``"material_yield"`` /
  ``"material_allowable_underated"`` and the material-yield assumption is
  disclosed.  Without either reference no
  damage field can be emitted: the payload fails open
  (``FEA_YIELD_REFERENCE_UNAVAILABLE``, ``computed: False``) and the
  stress field is never shown with an invented scale.
- Per-vertex STRESS/DAMAGE field (meshed objects): the closed-form
  simply-supported rectangular plate bending solution for the structural
  section's uniform-pressure load — Navier double-sine series with odd
  terms m,n = 1,3,...,15:
      w(x,y) = sum_m sum_n Wmn * sin(m*pi*x/a) * sin(n*pi*y/b)
      Wmn    = 16*p / (pi^6 * m * n * den),
      den    = D11*(m/a)^4 + 2*(D12+2*D66)*(m/a)^2*(n/b)^2 + D22*(n/b)^4
  with the SAME plate constants the structural solver uses
  (physics.solve_load_case / shell_panel_response): the panel dimensions
  a_m/b_m/t_m from ``result["structural"]["structure"]``, the pressure
  from ``load_case`` (magnitude_pa, or a distributed force p = F/(a*b)),
  and D11/D12/D22/D66 from the resolved material — isotropic
  D = E*t^3/(12*(1-nu^2)) or the orthotropic lamination constants, both
  including the linear temperature derating.  Per-vertex stress uses the
  second derivatives of w (the same extraction as physics._shell_fields):
      sigma_v(x,y) = sqrt(sx^2 + sy^2 - sx*sy + 3*txy^2)
  with sx = 6*Mx/t^2, sy = 6*My/t^2, txy = 6*Txy/t^2 from the moment
  resultants Mx = D11*w,xx + D12*w,yy, My = D12*w,xx + D22*w,yy,
  Txy = -2*D66*w,xy.
- Mapping: each mesh's bounding box is stretched onto the panel domain
  (x in [-a/2, a/2], y in [-b/2, b/2] with a, b from the structural
  panel geometry; the bbox aspect a = max extent, b = min extent when the
  structural section carries no panel geometry — disclosed).  Vertex z is
  ignored: the field is projected onto the shell mid-plane (disclosed).
- Normalization: the field is scaled so its max — at the plate center,
  like real plate bending — equals the authoritative sigma_peak:
      sigma_v(i) = min(sigma_peak, raw(i) * sigma_peak / raw(a/2, b/2))
      D_i        = min(1.0, sigma_v(i) / sigma_yield)
  When the structural response is not a uniform-pressure shell panel
  (missing structure/load/material data, a beam case, a point load, or a
  degenerate mesh bbox), the per-vertex field falls back to the impact
  Gaussian and the fallback is disclosed
  (``FEA_PLATE_FIELD_UNAVAILABLE`` / ``FEA_PLATE_FIELD_MESH_UNAVAILABLE``).
- Dent displacement (only when a drop ran): the impact Gaussian is
  retained for the dent layer — the dent stays LOCAL at the impact zone
  while the damage/stress field is the plate distribution:
      Delta_i = -n_hat * delta_max * exp(-(d_i/lambda)^2)
                * (1 + 2*max(0, (D_gauss_i - 0.7)/0.3))
  where D_gauss_i = min(1, sigma_peak/sigma_yield * exp(-(d_i/lambda)^2))
  is the Gaussian-local damage used ONLY for the plastic dent
  amplification; the magnitude is capped at 1.5*delta_max; n_hat is the
  contact normal in the object's model frame.  Without a drop simulation
  every displacement vector is zero.
- Falloff radius lambda = max(0.001, min(0.05, 4*delta_max)) meters,
  where delta_max is the contact compression of the drop-derived impact
  estimate.  The estimate consumes the pipeline's STORED impact inputs
  (``drop_simulation.peak_force_estimate``: effective mass, energy-CAPPED
  kinetic energy, degraded restitution, resolved contact kwargs), so the
  numbers are identical to the drop section's internal estimate — including
  when ``DROP_SIMULATION_ENERGY_CAPPED`` fired.  Only when the stored
  inputs are absent is the estimate re-derived from the raw peak record,
  disclosed with ``FEA_DERIVED_ESTIMATE_FALLBACK``.  Without a usable
  compression, lambda = 0.01 m and ``FEA_FALLOFF_DEFAULTED`` is set.
- Analytic primitives (box/sphere/cylinder/cone/frustum/compound) carry
  no vertex arrays; they get a ``procedural`` record with the Gaussian
  parameters so the shader can evaluate the dent field analytically.

DETERMINISM: every output path iterates in a fixed order (object dict
insertion order, vertex index order, first-maximum tie breaking, the
Navier series summed m outer then n inner).  No set iteration, no
randomness, no wall-clock inputs; every emitted number is rounded and
clamped, and NaN/Inf can never reach the payload.
"""

import math
from typing import Any, List, Mapping, Optional, Sequence, Tuple

# Damage thresholds shared with the frontend shader.
DENT_THRESHOLD = 0.7
TEAR_THRESHOLD = 0.92
# Plastic amplification: factor 1 below DENT_THRESHOLD, linearly reaching
# PLASTIC_AMPLIFICATION_MAX at damage 1.0.
PLASTIC_AMPLIFICATION_RANGE = 0.3
PLASTIC_AMPLIFICATION_MAX = 3.0
# Dent depth cap as a multiple of the contact compression.
DENT_DEPTH_CAP_FACTOR = 1.5
# Falloff radius lambda bounds and the default when no drop-derived
# compression is available (documented engineering fallback, disclosed).
FALLOFF_MIN_M = 0.001
FALLOFF_MAX_M = 0.05
FALLOFF_DEFAULT_M = 0.01
FALLOFF_COMPRESSION_FACTOR = 4.0
# Sanity bounds for the dent animation window (seconds).  The window is the
# drop-derived contact duration, but a sub-millisecond compression phase
# would make the dent pop in on a single frame — the animation is floored
# at IMPACT_WINDOW_MIN_S (display smoothing, disclosed) and capped at
# IMPACT_WINDOW_MAX_S.
IMPACT_WINDOW_MIN_S = 0.05
IMPACT_WINDOW_MAX_S = 1.0
# Navier double-sine series truncation for the plate display field: odd
# terms m,n = 1,3,...,15 (8x8 terms, finer than the structural solver's
# screening order 9 — the display field is separately normalized so the
# truncation never shifts the field max).
NAVIER_SERIES_MAX = 15
NAVIER_TERMS = tuple(range(1, NAVIER_SERIES_MAX + 2, 2))

FEA_COMPUTE_FAILED = "FEA_COMPUTE_FAILED"
FEA_PEAK_STRESS_UNAVAILABLE = "FEA_PEAK_STRESS_UNAVAILABLE"
FEA_IMPACT_CENTER_UNAVAILABLE = "FEA_IMPACT_CENTER_UNAVAILABLE"
FEA_IMPACT_CENTER_DEFAULTED = "FEA_IMPACT_CENTER_DEFAULTED"
FEA_YIELD_REFERENCE_UNAVAILABLE = "FEA_YIELD_REFERENCE_UNAVAILABLE"
FEA_FALLOFF_DEFAULTED = "FEA_FALLOFF_DEFAULTED"
FEA_DROP_ESTIMATE_UNAVAILABLE = "FEA_DROP_ESTIMATE_UNAVAILABLE"
FEA_DERIVED_ESTIMATE_FALLBACK = "FEA_DERIVED_ESTIMATE_FALLBACK"
FEA_TRANSFORM_ASSUMED_IDENTITY = "FEA_TRANSFORM_ASSUMED_IDENTITY"
FEA_NO_MESHED_OBJECTS = "FEA_NO_MESHED_OBJECTS"
FEA_NON_FINITE_VERTEX = "FEA_NON_FINITE_VERTEX"
FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE = "FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE"
# The plate display field could not be derived from the structural section
# (no uniform-pressure shell panel: missing panel structure/load/material
# data, a beam case, or a point load) — the impact Gaussian is used for the
# per-vertex stress/damage field and the fallback is disclosed.
FEA_PLATE_FIELD_UNAVAILABLE = "FEA_PLATE_FIELD_UNAVAILABLE"
# A specific mesh could not be projected onto the panel domain (degenerate
# bounding box) — that object's field falls back to the impact Gaussian.
FEA_PLATE_FIELD_MESH_UNAVAILABLE = "FEA_PLATE_FIELD_MESH_UNAVAILABLE"
# The structural section carries no panel geometry: the mesh bounding-box
# aspect (a = max extent, b = min extent) defines the plate domain.
FEA_PLATE_FIELD_BBOX_ASPECT = "FEA_PLATE_FIELD_BBOX_ASPECT"
# The plate field is evaluated on the shell mid-plane: vertex z is ignored.
FEA_PLATE_FIELD_MIDPLANE = "FEA_PLATE_FIELD_MIDPLANE"

_MODEL_DESCRIPTION_ASSUMPTION = (
    "per-vertex stress = simply-supported plate bending solution (Navier "
    "series, m,n<=15) mapped onto the mesh bounding box, normalized so the "
    "field max equals the shell peak stress; impact Gaussian retained for "
    "the dent layer only"
)
_GAUSSIAN_MODEL_DESCRIPTION_ASSUMPTION = (
    "per-vertex display field: sigma_v = sigma_peak*exp(-(d/lambda)^2), "
    "D = min(1, sigma_v/sigma_yield), lambda = max(0.001, min(0.05, "
    "4*delta_max)) m; display-only visualization, not an FEA result"
)

Vector3 = Tuple[float, float, float]


def _finite(value, label="value"):
    """Return a finite float or raise ValueError (internal guard)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be numeric".format(label))
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(label))
    return number


def _finite_vector3(value, label="vector"):
    """Validate a 3-component vector of finite floats (internal guard)."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("{} must be a 3-component vector".format(label))
    return tuple(_finite(item, label) for item in value)


def _failed_payload(flag, assumptions=()):
    """Fail-open payload: display data with ``computed: False``."""
    return {
        "computed": False,
        "peak": None,
        "yield_stress_pa": None,
        "damage_basis": None,
        "safety_factor": None,
        "impact_window_s": 0.0,
        "dent_threshold": DENT_THRESHOLD,
        "tear_threshold": TEAR_THRESHOLD,
        "center_frame": None,
        "objects": [],
        "procedural": [],
        "assumptions": list(assumptions),
        "flags": [flag],
    }


def _shell_peak_stress_pa(result):
    """Authoritative peak stress from the shell result (or None)."""
    shell = result.get("shell")
    if not isinstance(shell, Mapping):
        return None
    value = shell.get("peak_stress_pa")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def _shell_safety_factor(result):
    """Min safety factor from the shell result (float or None)."""
    shell = result.get("shell")
    if not isinstance(shell, Mapping):
        return None
    value = shell.get("min_safety_factor")
    if value in (None, "not_available"):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _material_yield_pa(result):
    """First finite positive yield reference of the resolved shell material.

    Reads ``result["shell"]["inputs_trace"]["material"]["properties"]``
    (the trace built by ``shell_validation.build_shell_trace``): the
    catalog property names are ``derated_tensile_allowable_pa``,
    ``yield_strength`` and ``tensile_allowable`` as SI Pa.  Values may be
    plain floats or quantity dicts (``{"value_si": ..., "unit": ...}``).
    Returns ``(value, basis)``: the first finite positive value in that
    fixed key order and the damage basis it came from
    (``"material_allowable"`` for a persisted derated allowable,
    ``"material_yield"`` for yield strength, ``"material_allowable_underated"``
    for the plain catalog allowable), or ``(None, None)`` when absent.
    """
    shell = result.get("shell")
    if not isinstance(shell, Mapping):
        return None, None
    trace = shell.get("inputs_trace")
    if not isinstance(trace, Mapping):
        return None, None
    material = trace.get("material")
    if not isinstance(material, Mapping):
        return None, None
    properties = material.get("properties")
    if not isinstance(properties, Mapping):
        return None, None
    for key, basis in (
        ("derated_tensile_allowable_pa", "material_allowable"),
        ("yield_strength", "material_yield"),
        ("tensile_allowable", "material_allowable_underated"),
    ):
        value = properties.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = value.get("value_si", value.get("value"))
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            return value, basis
    return None, None


def _peak_impact(result):
    """The peak drop impact record (first maximum of impact_speed_m_s).

    Mirrors the simulator's selection: ``max(all_impacts, key=...)`` picks
    the FIRST maximum in list order, so a strict ``>`` comparison keeps the
    same record.  Returns the impact dict or None.
    """
    drop = result.get("drop_simulation")
    if not isinstance(drop, Mapping):
        return None
    impacts = drop.get("impacts")
    if not isinstance(impacts, (list, tuple)) or not impacts:
        return None
    best = None
    best_speed = None
    for impact in impacts:
        if not isinstance(impact, Mapping):
            continue
        speed = impact.get("impact_speed_m_s")
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(speed):
            continue
        if best is None or speed > best_speed:
            best = impact
            best_speed = speed
    return best


def _drop_derived_estimate(result):
    """Re-derive the drop-derived impact estimate from the STORED inputs.

    Prefers the pipeline-stored ``drop_simulation.peak_force_estimate``
    inputs (``mass_kg``, the energy-CAPPED ``impact_speed_m_s`` /
    ``energy_j``, the degraded ``restitution``, and the resolved contact
    kwargs echoed by the pipeline's drop section), feeding them back to
    ``impact.estimate_impact`` exactly as the pipeline did — so the
    returned contact compression and duration are identical to the values
    the pipeline computed internally, including when
    ``DROP_SIMULATION_ENERGY_CAPPED`` fired (the raw peak record's
    kinetic energy can be lever-amplified and must not be re-derived).
    Falls back to re-deriving from the raw peak record only when the
    stored inputs are absent (legacy/partial results).  Returns
    ``(estimate, fallback)``: ``fallback`` is True when the stored inputs
    were unavailable and the raw-record re-derivation was used, and the
    ``ImpactResult`` is None when any input is missing.
    """
    from . import impact as impact_module

    drop = result.get("drop_simulation")
    if not isinstance(drop, Mapping):
        return None, False
    stored = drop.get("peak_force_estimate")
    if isinstance(stored, Mapping):
        try:
            mass_kg = float(stored.get("mass_kg"))
            speed = float(stored.get("impact_speed_m_s"))
            restitution = float(stored.get("restitution") or 0.0)
        except (TypeError, ValueError):
            mass_kg = speed = restitution = None
        if (
            mass_kg is not None
            and math.isfinite(mass_kg)
            and mass_kg > 0.0
            and speed is not None
            and math.isfinite(speed)
            and speed > 0.0
            and restitution is not None
            and math.isfinite(restitution)
        ):
            kwargs = {}
            stiffness = stored.get("contact_stiffness_n_per_m")
            modulus = stored.get("effective_modulus_pa")
            radius = stored.get("contact_radius_m")
            if stiffness is not None:
                try:
                    kwargs["contact_stiffness_n_per_m"] = float(stiffness)
                except (TypeError, ValueError):
                    kwargs = {}
            elif modulus is not None and radius is not None:
                try:
                    kwargs["effective_modulus_pa"] = float(modulus)
                    kwargs["contact_radius_m"] = float(radius)
                except (TypeError, ValueError):
                    kwargs = {}
            if kwargs:
                try:
                    estimate = impact_module.estimate_impact(
                        mass_kg, velocity_m_s=speed, restitution=restitution, **kwargs
                    )
                except Exception:
                    estimate = None
                if estimate is not None and estimate.validity not in ("failed", "no_impact"):
                    return estimate, False

    # Fallback: re-derive from the raw peak record (legacy results without
    # the stored estimate inputs).  The raw record may carry lever-amplified
    # kinetic energy that the pipeline capped; the fallback is disclosed by
    # the caller with FEA_DERIVED_ESTIMATE_FALLBACK.
    model = drop.get("model")
    peak = drop.get("peak")
    if not isinstance(model, Mapping) or not isinstance(peak, Mapping):
        return None, True
    try:
        mass_kg = float(model.get("mass_kg"))
        energy_j = float(peak.get("kinetic_energy_j"))
        restitution = float(model.get("restitution") or 0.0)
    except (TypeError, ValueError):
        return None, True
    if (
        not math.isfinite(mass_kg)
        or mass_kg <= 0.0
        or not math.isfinite(energy_j)
        or energy_j <= 0.0
        or not math.isfinite(restitution)
    ):
        return None, True
    speed = math.sqrt(2.0 * energy_j / mass_kg)
    kwargs = {}
    stiffness = drop.get("contact_stiffness_n_per_m")
    modulus = drop.get("effective_modulus_pa")
    radius = drop.get("contact_radius_m")
    if stiffness is not None:
        try:
            kwargs["contact_stiffness_n_per_m"] = float(stiffness)
        except (TypeError, ValueError):
            return None, True
    elif modulus is not None and radius is not None:
        try:
            kwargs["effective_modulus_pa"] = float(modulus)
            kwargs["contact_radius_m"] = float(radius)
        except (TypeError, ValueError):
            return None, True
    else:
        return None, True
    try:
        estimate = impact_module.estimate_impact(
            mass_kg, velocity_m_s=speed, restitution=restitution, **kwargs
        )
    except Exception:
        return None, True
    if estimate.validity in ("failed", "no_impact"):
        return None, True
    return estimate, True


def _fallback_center(result):
    """Shell critical region (or structural filtered location) as center."""
    shell = result.get("shell")
    if isinstance(shell, Mapping):
        critical = shell.get("critical_region")
        if isinstance(critical, (list, tuple)) and len(critical) == 3:
            try:
                return tuple(float(component) for component in critical)
            except (TypeError, ValueError):
                pass
    structural = result.get("structural")
    if isinstance(structural, Mapping):
        response = structural.get("response")
        if isinstance(response, Mapping):
            for key in ("filtered_location", "max_displacement_location"):
                location = response.get(key)
                if isinstance(location, (list, tuple)) and len(location) == 3:
                    try:
                        return tuple(float(component) for component in location)
                    except (TypeError, ValueError):
                        continue
    return None


def _object_frame(geometry):
    """Return ``(rotation, translation, identity_assumed)`` for an object.

    The object's transform maps world = R*local + t (row-major rotation).
    A missing or malformed transform assumes identity and is disclosed:
    the rotation must be a 3x3 matrix of finite floats that is
    orthonormal (R*R^T ~= I within a relative 1e-6 tolerance) — a
    non-orthonormal rotation is not a valid frame map and must never be
    applied (a malformed rotation can otherwise kill the whole payload).
    """
    transform = getattr(geometry, "transform", None)
    rotation = getattr(transform, "rotation", None)
    translation = getattr(transform, "translation", None)
    if (
        rotation is None
        or translation is None
        or not isinstance(rotation, (list, tuple))
        or len(rotation) != 3
        or not all(isinstance(row, (list, tuple)) and len(row) == 3 for row in rotation)
        or not isinstance(translation, (list, tuple))
        or len(translation) != 3
    ):
        return None, None, True
    try:
        rows = tuple(tuple(float(item) for item in row) for row in rotation)
        offset = tuple(float(item) for item in translation)
    except (TypeError, ValueError):
        return None, None, True
    if not all(math.isfinite(item) for row in rows for item in row) or not all(
        math.isfinite(item) for item in offset
    ):
        return None, None, True
    for i in range(3):
        for j in range(3):
            dot = sum(rows[i][k] * rows[j][k] for k in range(3))
            expected = 1.0 if i == j else 0.0
            if abs(dot - expected) > 1e-6 * max(1.0, abs(dot)):
                return None, None, True
    return rows, offset, False


def _point_in_model(rotation, translation, world_point):
    """Map a world point into the object's model frame: R^T*(p - t).

    ``translation=None`` treats the input as a direction vector and only
    applies the inverse rotation (used for the contact normal).
    """
    if translation is None:
        delta = world_point
    else:
        delta = (
            world_point[0] - translation[0],
            world_point[1] - translation[1],
            world_point[2] - translation[2],
        )
    if rotation is None:
        return delta
    return (
        rotation[0][0] * delta[0] + rotation[1][0] * delta[1] + rotation[2][0] * delta[2],
        rotation[0][1] * delta[0] + rotation[1][1] * delta[1] + rotation[2][1] * delta[2],
        rotation[0][2] * delta[0] + rotation[1][2] * delta[1] + rotation[2][2] * delta[2],
    )


def _finite_vertex(vertex):
    """Normalize a vertex to a finite 3-vector, or None when unusable."""
    if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
        return None
    try:
        values = tuple(float(component) for component in vertex)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in values):
        return None
    return values


def _vertex_field(vertex, impact_model, inverse_lambda_squared, sigma_peak, sigma_yield, delta_max, normal_model):
    """Per-vertex Gaussian damage, stress, and dent displacement (raw).

    ``impact_model`` and ``normal_model`` are the impact point and contact
    normal in the object's MODEL frame.  Returns
    ``(damage, stress_pa, displacement)`` with displacement along
    ``-normal_model``, magnitude ``delta_max*exp(...)*amplification``
    capped at ``1.5*delta_max``.  A non-positive or non-finite
    ``sigma_yield`` yields zero damage/stress/displacement (a raw
    ZeroDivisionError can never escape the vertex loop).
    """
    if not math.isfinite(sigma_yield) or sigma_yield <= 0.0:
        return 0.0, 0.0, (0.0, 0.0, 0.0)
    offset = (
        vertex[0] - impact_model[0],
        vertex[1] - impact_model[1],
        vertex[2] - impact_model[2],
    )
    distance_squared = offset[0] * offset[0] + offset[1] * offset[1] + offset[2] * offset[2]
    gaussian = math.exp(-distance_squared * inverse_lambda_squared)
    stress = sigma_peak * gaussian
    damage = stress / sigma_yield
    if damage > 1.0:
        damage = 1.0
    amplification = 1.0
    if damage > DENT_THRESHOLD:
        amplification = 1.0 + 2.0 * (damage - DENT_THRESHOLD) / PLASTIC_AMPLIFICATION_RANGE
        if amplification > PLASTIC_AMPLIFICATION_MAX:
            amplification = PLASTIC_AMPLIFICATION_MAX
    depth = delta_max * gaussian * amplification
    cap = DENT_DEPTH_CAP_FACTOR * delta_max
    if depth > cap:
        depth = cap
    displacement = (
        -normal_model[0] * depth,
        -normal_model[1] * depth,
        -normal_model[2] * depth,
    )
    return damage, stress, displacement


def _number(value):
    """Finite float or None (internal guard)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _plate_constants(result):
    """Uniform-pressure shell-panel data for the plate display field.

    Mirrors the structural solver's shell_panel branch (physics.py
    ``solve_load_case`` / ``shell_panel_response``): the same panel
    dimensions (a_m/length_m, b_m/width_m, t_m/thickness_m — the solver's
    ``_shell_dims``), the same uniform-pressure load (kind ``pressure``
    with ``magnitude_pa``, or a distributed force p = F/(a*b)), the same
    plate stiffnesses from the resolved material (``physics._material_props``
    including the linear temperature derating, ``_orthotropic_shell_stiffness``
    falling back to ``_isotropic_plate_stiffness``), and the same stress
    extraction (von Mises of the 6/t^2-scaled moment resultants,
    ``physics._shell_fields``).

    Returns a constants dict or None when the structural response is not a
    uniform-pressure shell panel (missing structure/load/material data, a
    beam case, a point load, or non-physical constants): the caller then
    falls back to the impact Gaussian with a disclosure.  Never raises:
    any malformed input degrades to None.
    """
    try:
        structural = result.get("structural")
        if not isinstance(structural, Mapping):
            return None
        structure = structural.get("structure")
        load_case = structural.get("load_case")
        if not isinstance(structure, Mapping) or not isinstance(load_case, Mapping):
            return None
        if str(structure.get("type") or "") != "shell_panel":
            return None
        a = _number(structure.get("a_m", structure.get("length_m")))
        b = _number(structure.get("b_m", structure.get("width_m")))
        t = _number(structure.get("t_m", structure.get("thickness_m")))
        if t is None or t <= 0.0:
            return None
        kind = str(load_case.get("kind") or "")
        if kind == "pressure":
            p = _number(load_case.get("magnitude_pa"))
        elif kind == "force" and not load_case.get("point_load"):
            if a is None or b is None or a <= 0.0 or b <= 0.0:
                return None
            force = _number(load_case.get("force_n"))
            p = None if force is None else force / (a * b)
        else:
            return None
        if p is None or p <= 0.0:
            return None
        from . import physics as physics_module

        material_payload = structural.get("resolved_material")
        if not isinstance(
            material_payload,
            (dict, physics_module.MaterialDefinition, physics_module.MaterialProperties),
        ):
            material_payload = {}
        temperature_k = _number(load_case.get("temperature_k"))
        E, nu, _allowable, info = physics_module._material_props(
            material_payload, temperature_k
        )
        if E is None or nu is None:
            return None
        D11, D12, D22, D66 = physics_module._orthotropic_shell_stiffness(E, info, t)
        if D11 is None:
            D11, D12, D22, D66 = physics_module._isotropic_plate_stiffness(E, nu, t)
        # The Navier denominator den = D11*(m/a)^4 + 2*(D12+2*D66)*(m/a)^2*(n/b)^2
        # + D22*(n/b)^4 must be positive for every term; a non-positive
        # D12+2*D66 (pathological orthotropic data) would make the series
        # unbounded — refuse the plate field rather than emit garbage.
        if any(
            not math.isfinite(value) or value <= 0.0
            for value in (D11, D12, D22, D66, D12 + 2.0 * D66)
        ):
            return None
        return {
            "a": a,
            "b": b,
            "t": t,
            "p": p,
            "D11": D11,
            "D12": D12,
            "D22": D22,
            "D66": D66,
        }
    except Exception:
        return None


def _plate_vertex_stress(x_panel, y_panel, plate, a=None, b=None):
    """Raw von Mises bending stress at a panel coordinate (Pa).

    Same plate constants and extraction as the structural solver's
    ``physics._shell_fields``: accumulates the negated curvatures of the
    Navier series (mxx = -w,xx, myy = -w,yy, mxy = -w,xy), forms the
    moment resultants Mx = D11*w,xx + D12*w,yy, My = D12*w,xx + D22*w,yy,
    Txy = -2*D66*w,xy, scales each by 6/t^2 and takes von Mises
    (sqrt(sx^2 + sy^2 - sx*sy + 3*txy^2)).  Returns None when the series
    produces a non-finite value.  ``a``/``b`` override the panel
    dimensions (used when the plate domain came from the mesh
    bounding-box aspect instead of the structural geometry).
    """
    if a is None:
        a = plate["a"]
    if b is None:
        b = plate["b"]
    t = plate["t"]
    p = plate["p"]
    D11 = plate["D11"]
    D12 = plate["D12"]
    D22 = plate["D22"]
    D66 = plate["D66"]
    mxx = 0.0
    myy = 0.0
    mxy = 0.0
    # Deterministic summation order: m outer, then n (same as the solver).
    for m in NAVIER_TERMS:
        alpha = math.pi * m / a
        alpha2 = alpha * alpha
        sinx = math.sin(alpha * x_panel)
        cosx = math.cos(alpha * x_panel)
        m_over_a = m / a
        for n in NAVIER_TERMS:
            beta = math.pi * n / b
            beta2 = beta * beta
            n_over_b = n / b
            den = (
                D11 * (m_over_a ** 4)
                + 2.0 * (D12 + 2.0 * D66) * (m_over_a ** 2) * (n_over_b ** 2)
                + D22 * (n_over_b ** 4)
            )
            coeff = 16.0 * p / (math.pi ** 6 * m * n * den)
            s = sinx * math.sin(beta * y_panel)
            mxx += coeff * alpha2 * s
            myy += coeff * beta2 * s
            mxy += coeff * alpha * beta * cosx * math.cos(beta * y_panel)
    factor = 6.0 / (t * t)
    mx = -(D11 * mxx + D12 * myy)
    my = -(D12 * mxx + D22 * myy)
    txy = 2.0 * D66 * mxy
    sx = mx * factor
    sy = my * factor
    stxy = txy * factor
    value = math.sqrt(max(0.0, sx * sx + sy * sy - sx * sy + 3.0 * stxy * stxy))
    if not math.isfinite(value):
        return None
    return value


def _panel_mapping(finite_vertices, plate):
    """Map the mesh bounding box onto the plate domain.

    Returns ``(cx, cy, cz, a, b, x_extent, y_extent, aspect_fallback)``:
    cx/cy/cz is the bounding-box center (model frame), ``a``/``b`` are the
    panel dimensions from the structural geometry (the bounding-box aspect
    a = max extent, b = min extent when the structural section carries no
    panel geometry, with ``aspect_fallback=True``).  Returns None when the
    bounding box is degenerate (zero x or y extent: the mesh cannot be
    projected onto the plate plane).
    """
    xmin = min(vertex[0] for vertex in finite_vertices)
    xmax = max(vertex[0] for vertex in finite_vertices)
    ymin = min(vertex[1] for vertex in finite_vertices)
    ymax = max(vertex[1] for vertex in finite_vertices)
    zmin = min(vertex[2] for vertex in finite_vertices)
    zmax = max(vertex[2] for vertex in finite_vertices)
    x_extent = xmax - xmin
    y_extent = ymax - ymin
    if x_extent <= 0.0 or y_extent <= 0.0:
        return None
    a = plate.get("a")
    b = plate.get("b")
    aspect_fallback = False
    if a is None or b is None:
        aspect_fallback = True
        if x_extent >= y_extent:
            a, b = x_extent, y_extent
        else:
            a, b = y_extent, x_extent
    return (
        (xmin + xmax) / 2.0,
        (ymin + ymax) / 2.0,
        (zmin + zmax) / 2.0,
        a,
        b,
        x_extent,
        y_extent,
        aspect_fallback,
    )


def _is_mesh_geometry(geometry):
    """True when the object carries indexed mesh vertices and triangles."""
    vertices = getattr(geometry, "vertices", None)
    triangles = getattr(geometry, "triangles", None)
    return (
        isinstance(vertices, (list, tuple))
        and isinstance(triangles, (list, tuple))
        and len(vertices) > 0
        and len(triangles) > 0
    )


def _compute_fea(result, geometry_objs):
    """Compute the FEA display payload (internal; exceptions are contained
    by :func:`compute_fea`)."""
    assumptions = [_GAUSSIAN_MODEL_DESCRIPTION_ASSUMPTION]
    flags = []

    sigma_peak = _shell_peak_stress_pa(result)
    if sigma_peak is None or sigma_peak <= 0.0:
        return _failed_payload(FEA_PEAK_STRESS_UNAVAILABLE, assumptions)

    # Honesty: the stress field inherits the structural solve's validity.  An
    # inconclusive solve (e.g. THIN_SHELL_OUT_OF_RANGE) is still visualized,
    # but the payload must say so instead of presenting bare numbers.
    structural_response = ((result.get("structural") or {}).get("response") or {})
    response_validity = str(structural_response.get("validity") or "")
    if response_validity and response_validity not in ("valid", "approximate"):
        flags.append(FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE)
        assumptions.append(
            "structural response validity {!r}: the stress field is displayed "
            "but the underlying solve is not valid/approximate".format(
                response_validity
            )
        )

    safety_factor = _shell_safety_factor(result)
    if safety_factor is not None and safety_factor > 0.0:
        # The shell safety factor is computed against the DERATED tensile
        # allowable (physics._material_props derates the catalog allowable
        # at temperature), so sigma_yield = sigma_peak*SF is that derated
        # allowable — recorded as the damage basis, never silently.
        sigma_yield = sigma_peak * safety_factor
        damage_basis = "derated_allowable"
        assumptions.append(
            "yield stress is the derated tensile allowable behind the shell "
            "safety factor (sigma_yield = sigma_peak * min_safety_factor; "
            "damage_basis 'derated_allowable')"
        )
    else:
        # No structural safety factor: use the resolved shell material's
        # yield reference (trace properties, SI Pa).  Honest scaling — the
        # peak damage is sigma_peak/material_yield, never 1.0 by convention.
        material_yield, material_basis = _material_yield_pa(result)
        if material_yield is None:
            assumptions.append(
                "no yield reference: no structural safety factor and no "
                "resolved shell material yield strength/allowable; the "
                "damage field cannot be emitted (stress has no honest scale)"
            )
            return _failed_payload(FEA_YIELD_REFERENCE_UNAVAILABLE, assumptions)
        sigma_yield = material_yield
        damage_basis = material_basis
        assumptions.append(
            "yield stress from the resolved shell material (no structural "
            "safety factor); damage_basis {!r}".format(damage_basis)
        )

    # Plate display field: the closed-form simply-supported plate bending
    # solution of the structural section's uniform-pressure shell panel.
    # None when the structural response is not a uniform-pressure shell
    # panel (missing panel structure/load/material data, a beam case, or a
    # point load) — the per-vertex stress/damage field then falls back to
    # the impact Gaussian, disclosed (never silently).
    plate = _plate_constants(result)
    plate_used = False
    if plate is None and any(
        _is_mesh_geometry(geometry) for geometry in geometry_objs.values()
    ):
        flags.append(FEA_PLATE_FIELD_UNAVAILABLE)
        assumptions.append(
            "structural response is not a uniform-pressure shell panel "
            "(missing panel structure/load/material data, a beam case, or a "
            "point load): the per-vertex stress/damage field falls back to "
            "the impact Gaussian (display only)"
        )

    # World-frame hotspot center and contact normal.
    center = None
    center_frame = "world"
    normal_world = (0.0, 0.0, 1.0)
    impact_window = 0.0
    drop_available = False
    impact = _peak_impact(result)
    if impact is not None:
        location = impact.get("contact_location")
        normal = impact.get("contact_normal")
        if isinstance(location, (list, tuple)) and len(location) == 3:
            try:
                candidate = tuple(float(component) for component in location)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and all(math.isfinite(item) for item in candidate):
                center = candidate
                drop_available = True
        if isinstance(normal, (list, tuple)) and len(normal) == 3:
            try:
                candidate = tuple(float(component) for component in normal)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and all(math.isfinite(item) for item in candidate):
                magnitude = math.sqrt(sum(item * item for item in candidate))
                if magnitude > 0.0:
                    normal_world = tuple(item / magnitude for item in candidate)
    if center is None:
        center = _fallback_center(result)
        if center is None or not all(math.isfinite(item) for item in center):
            return _failed_payload(FEA_IMPACT_CENTER_UNAVAILABLE, assumptions)
        flags.append(FEA_IMPACT_CENTER_DEFAULTED)
        # The shell critical region is a PANEL-LOCAL Navier plate coordinate
        # (no drop ran, so there is no world-frame contact point): it is kept
        # as the hotspot anchor as-is, never mapped through object transforms.
        center_frame = "panel_local"
        assumptions.append(
            "no drop simulation: hotspot centered at the shell critical "
            "region; dent animation disabled (impact_window_s = 0)"
        )
        assumptions.append(
            "hotspot center is a panel-local stand-in plate coordinate (no "
            "drop simulation ran): kept as-is for display, not mapped through "
            "object transforms (center_frame 'panel_local')"
        )
        drop_available = False
    normal_world = _finite_vector3(normal_world, "contact_normal")

    # Drop-derived contact compression (dent depth scale) and window.
    delta_max = 0.0
    estimate = None
    if drop_available:
        estimate, estimate_fallback = _drop_derived_estimate(result)
        if estimate is not None:
            if estimate_fallback:
                flags.append(FEA_DERIVED_ESTIMATE_FALLBACK)
                assumptions.append(
                    "pipeline-stored impact estimate inputs unavailable: the "
                    "drop-derived estimate was re-derived from the raw peak "
                    "record (capped-energy results may differ); dent depth "
                    "scale is fallback-only"
                )
            compression = estimate.contact_compression_m
            if math.isfinite(compression) and compression > 0.0:
                delta_max = compression
                duration = estimate.contact_duration_s
                if not math.isfinite(duration) or duration < 0.0:
                    duration = 0.0
                if duration > IMPACT_WINDOW_MAX_S:
                    duration = IMPACT_WINDOW_MAX_S
                if duration < IMPACT_WINDOW_MIN_S:
                    duration = IMPACT_WINDOW_MIN_S
                    assumptions.append(
                        "impact_window_s floored at {:.3g} s: the drop-derived "
                        "contact duration is shorter than a display frame".format(
                            IMPACT_WINDOW_MIN_S
                        )
                    )
                impact_window = round(duration, 6)
                assumptions.append(
                    "impact_window_s = the compression-phase contact "
                    "duration of the drop-derived impact estimate"
                )
            else:
                flags.append(FEA_DROP_ESTIMATE_UNAVAILABLE)
                assumptions.append(
                    "drop-derived impact estimate produced no usable contact "
                    "compression; dent depth set to zero"
                )
        else:
            flags.append(FEA_DROP_ESTIMATE_UNAVAILABLE)
            assumptions.append(
                "drop-derived impact estimate unavailable; dent depth set "
                "to zero"
            )
    if delta_max <= 0.0:
        falloff_radius = FALLOFF_DEFAULT_M
        flags.append(FEA_FALLOFF_DEFAULTED)
        assumptions.append(
            "falloff radius defaulted to 0.01 m: no drop-derived contact "
            "compression available"
        )
    else:
        falloff_radius = FALLOFF_COMPRESSION_FACTOR * delta_max
        if falloff_radius < FALLOFF_MIN_M:
            falloff_radius = FALLOFF_MIN_M
        elif falloff_radius > FALLOFF_MAX_M:
            falloff_radius = FALLOFF_MAX_M
    inverse_lambda_squared = 1.0 / (falloff_radius * falloff_radius)

    objects_out = []
    procedural_out = []
    # Nearest mesh vertex to the impact point (distance_squared, object_id,
    # index, vertex): anchors the peak record when the Gaussian field is zero
    # at every vertex (sparse mesh, impact point between vertices).
    nearest = None
    # Nearest mesh vertex to the PLATE center (the mesh bounding-box center,
    # distance_squared, object_id, index, vertex): anchors the peak record at
    # the field-max location when the plate field is computed.
    center_nearest = None
    transform_disclosed = False
    nonfinite_disclosed = False
    mesh_fallback_disclosed = False
    midplane_disclosed = False
    aspect_disclosed = False
    for object_id, geometry in geometry_objs.items():
        rotation, translation, identity_assumed = _object_frame(geometry)
        if identity_assumed and not transform_disclosed:
            transform_disclosed = True
            flags.append(FEA_TRANSFORM_ASSUMED_IDENTITY)
            assumptions.append(
                "object transform missing: the world-frame impact point was "
                "used as-is in the object's model frame"
            )
        impact_model = (
            tuple(center)
            if center_frame == "panel_local"
            else _point_in_model(rotation, translation, center)
        )
        normal_model = _point_in_model(rotation, None, normal_world)
        # Procedural records are emitted for EVERY object (meshes included):
        # the frontend shader evaluates the dent Gaussian continuously in
        # fragment space, so the dent stays visible even on sparse meshes
        # whose vertices miss the impact zone.
        procedural_out.append(
            {
                "object_id": str(object_id),
                "impact_point_model_m": [round(value, 9) for value in impact_model],
                "falloff_radius_m": round(falloff_radius, 9),
                "contact_normal_model": [round(value, 9) for value in normal_model],
                "peak_stress_pa": round(sigma_peak, 6),
                "yield_stress_pa": round(sigma_yield, 6),
                "max_compression_m": round(delta_max, 9),
            }
        )
        if not _is_mesh_geometry(geometry):
            continue
        # First pass: normalize the vertex array (finite triples or None).
        entries = []
        for vertex in geometry.vertices:
            entries.append(_finite_vertex(vertex))

        # Plate field for this object: stretch the mesh bounding box onto the
        # panel domain and normalize the field so its max — the plate center
        # value, where the bending peak sits — equals sigma_peak.  A mesh
        # that cannot be mapped (degenerate bbox) or a non-finite center
        # value falls back to the impact Gaussian for this object, disclosed.
        object_plate = None
        scale_x = 0.0
        scale_y = 0.0
        plate_scale = 0.0
        center_x = center_y = center_z = 0.0
        panel_a = 0.0
        panel_b = 0.0
        if plate is not None:
            finite = [values for values in entries if values is not None]
            mapping = _panel_mapping(finite, plate) if finite else None
            if mapping is None:
                if not mesh_fallback_disclosed:
                    mesh_fallback_disclosed = True
                    flags.append(FEA_PLATE_FIELD_MESH_UNAVAILABLE)
                    assumptions.append(
                        "object {!r}: the mesh cannot be projected onto the "
                        "panel domain (degenerate bounding box); the impact "
                        "Gaussian is used for this object".format(str(object_id))
                    )
            else:
                (
                    center_x,
                    center_y,
                    center_z,
                    panel_a,
                    panel_b,
                    x_extent,
                    y_extent,
                    aspect_fallback,
                ) = mapping
                center_raw = _plate_vertex_stress(
                    panel_a / 2.0, panel_b / 2.0, plate, a=panel_a, b=panel_b
                )
                if center_raw is not None and center_raw > 0.0:
                    object_plate = plate
                    plate_used = True
                    scale_x = panel_a / x_extent
                    scale_y = panel_b / y_extent
                    plate_scale = sigma_peak / center_raw
                    if aspect_fallback and not aspect_disclosed:
                        aspect_disclosed = True
                        flags.append(FEA_PLATE_FIELD_BBOX_ASPECT)
                        assumptions.append(
                            "structural panel geometry carries no a_m/b_m: "
                            "the mesh bounding-box aspect ratio (a = max "
                            "extent, b = min extent) defines the plate domain"
                        )
                    if not midplane_disclosed:
                        midplane_disclosed = True
                        flags.append(FEA_PLATE_FIELD_MIDPLANE)
                        assumptions.append(
                            "plate field projected onto the shell mid-plane "
                            "(vertex z ignored); the mesh bounding box is "
                            "stretched onto x in [-a/2, a/2], y in [-b/2, b/2]"
                        )
                else:
                    if not mesh_fallback_disclosed:
                        mesh_fallback_disclosed = True
                        flags.append(FEA_PLATE_FIELD_MESH_UNAVAILABLE)
                        assumptions.append(
                            "object {!r}: the plate field could not be "
                            "evaluated (non-positive center stress); the "
                            "impact Gaussian is used for this object".format(
                                str(object_id)
                            )
                        )

        damage_list = []
        displacement_list = []
        stress_list = []
        for index, values in enumerate(entries):
            if values is None:
                if not nonfinite_disclosed:
                    nonfinite_disclosed = True
                    flags.append(FEA_NON_FINITE_VERTEX)
                    assumptions.append(
                        "non-finite mesh vertex zeroed in the display field"
                    )
                damage_list.append(0.0)
                displacement_list.append([0.0, 0.0, 0.0])
                stress_list.append(0.0)
                continue
            # The dent displacement ALWAYS comes from the impact Gaussian:
            # the dent stays local at the impact zone even though the
            # damage/stress field is the whole-shell plate distribution.
            damage, stress, displacement = _vertex_field(
                values,
                impact_model,
                inverse_lambda_squared,
                sigma_peak,
                sigma_yield,
                delta_max,
                normal_model,
            )
            if object_plate is not None:
                # Replace the Gaussian damage/stress with the plate bending
                # field, normalized so the field max (at the plate center)
                # equals sigma_peak; the Gaussian dent displacement stands.
                x_panel = panel_a / 2.0 + (values[0] - center_x) * scale_x
                y_panel = panel_b / 2.0 + (values[1] - center_y) * scale_y
                raw = _plate_vertex_stress(
                    x_panel, y_panel, object_plate, a=panel_a, b=panel_b
                )
                if raw is None or not math.isfinite(raw):
                    if not nonfinite_disclosed:
                        nonfinite_disclosed = True
                        flags.append(FEA_NON_FINITE_VERTEX)
                        assumptions.append(
                            "non-finite mesh vertex zeroed in the display field"
                        )
                    damage_list.append(0.0)
                    displacement_list.append([0.0, 0.0, 0.0])
                    stress_list.append(0.0)
                    continue
                stress = raw * plate_scale
                if stress > sigma_peak:
                    stress = sigma_peak
                damage = stress / sigma_yield
                if damage > 1.0:
                    damage = 1.0
            if not (
                math.isfinite(damage)
                and math.isfinite(stress)
                and all(math.isfinite(component) for component in displacement)
            ):
                if not nonfinite_disclosed:
                    nonfinite_disclosed = True
                    flags.append(FEA_NON_FINITE_VERTEX)
                    assumptions.append(
                        "non-finite mesh vertex zeroed in the display field"
                    )
                damage_list.append(0.0)
                displacement_list.append([0.0, 0.0, 0.0])
                stress_list.append(0.0)
                continue
            if object_plate is not None:
                offset = (
                    values[0] - center_x,
                    values[1] - center_y,
                    values[2] - center_z,
                )
                distance_squared = (
                    offset[0] * offset[0] + offset[1] * offset[1] + offset[2] * offset[2]
                )
                if center_nearest is None or distance_squared < center_nearest[0]:
                    center_nearest = (distance_squared, str(object_id), index, values)
            else:
                offset = (
                    values[0] - impact_model[0],
                    values[1] - impact_model[1],
                    values[2] - impact_model[2],
                )
                distance_squared = (
                    offset[0] * offset[0] + offset[1] * offset[1] + offset[2] * offset[2]
                )
                if nearest is None or distance_squared < nearest[0]:
                    nearest = (distance_squared, str(object_id), index, values)
            damage_list.append(round(damage, 6))
            stress_list.append(round(stress, 6))
            displacement_list.append([round(component, 9) for component in displacement])
        objects_out.append(
            {
                "object_id": str(object_id),
                "vertex_count": len(damage_list),
                "damage": damage_list,
                "displacement": displacement_list,
                "stress_pa": stress_list,
            }
        )
    if not objects_out and not procedural_out:
        flags.append(FEA_NO_MESHED_OBJECTS)

    peak_record = None
    if plate_used and center_nearest is not None:
        # The plate field's continuous maximum sits AT the plate center (the
        # mesh bounding-box center — the normalized field max equals the
        # shell peak stress), so the peak record anchors at the nearest
        # vertex to the plate center, NOT at the impact point.
        assumptions.append(
            "peak stress is the plate field's continuous maximum at the "
            "plate center (the mesh bounding-box center), anchored at the "
            "nearest vertex — not at the impact point"
        )
        if sigma_yield > 0.0:
            peak_damage = sigma_peak / sigma_yield
            if peak_damage > 1.0:
                peak_damage = 1.0
        else:
            peak_damage = 1.0
        peak_record = {
            "object_id": center_nearest[1],
            "vertex_index": center_nearest[2],
            "location_model_m": [round(value, 9) for value in center_nearest[3]],
            "damage": round(peak_damage, 6),
            "stress_pa": round(sigma_peak, 6),
            "stress_mpa": round(sigma_peak / 1e6, 6),
        }
    elif nearest is not None or procedural_out:
        # The Gaussian field's continuous maximum sits AT the impact point
        # (d = 0), so the peak is the shell peak stress at the impact
        # location in the nearest object's model frame — not the argmax over
        # a sparse mesh, which can be zero everywhere even though a real
        # stress field exists.
        assumptions.append(
            "peak stress is the continuous-field maximum at the impact point, "
            "not the nearest-vertex value"
        )
        if sigma_yield > 0.0:
            peak_damage = sigma_peak / sigma_yield
            if peak_damage > 1.0:
                peak_damage = 1.0
        else:
            peak_damage = 1.0
        if nearest is not None:
            peak_object_id = nearest[1]
            peak_index = nearest[2]
            peak_location = [round(value, 9) for value in nearest[3]]
        else:
            peak_object_id = procedural_out[0]["object_id"]
            peak_index = 0
            peak_location = list(procedural_out[0]["impact_point_model_m"])
        peak_record = {
            "object_id": peak_object_id,
            "vertex_index": peak_index,
            "location_model_m": peak_location,
            "damage": round(peak_damage, 6),
            "stress_pa": round(sigma_peak, 6),
            "stress_mpa": round(sigma_peak / 1e6, 6),
        }

    if plate_used:
        # The plate model is the base description of the emitted field (the
        # seeded Gaussian base is replaced; per-object fallbacks disclose
        # themselves individually above).
        assumptions[0] = _MODEL_DESCRIPTION_ASSUMPTION

    return {
        "computed": True,
        "peak": peak_record,
        "yield_stress_pa": round(sigma_yield, 6),
        "damage_basis": damage_basis,
        "safety_factor": (
            round(safety_factor, 6) if safety_factor is not None else None
        ),
        "impact_window_s": impact_window,
        "dent_threshold": DENT_THRESHOLD,
        "tear_threshold": TEAR_THRESHOLD,
        "center_frame": center_frame,
        "objects": objects_out,
        "procedural": procedural_out,
        "assumptions": assumptions,
        "flags": flags,
    }


def compute_fea(result, geometry_objs, request=None):
    """Compute the display-only per-vertex FEA payload for a pipeline result.

    ``result`` is the assembled pipeline result (``result["shell"]`` and
    ``result["drop_simulation"]`` are the authoritative inputs) and
    ``geometry_objs`` maps object ids to the parsed geometry objects
    (``mouse_sim.geometry``).  ``request`` is accepted for signature
    stability but NOT used: every input is read from the assembled result,
    so the visualization can never be affected by a raw request field the
    pipeline did not execute.

    NEVER raises: failures return a fail-open payload with
    ``computed: False`` and a disclosure flag.  Never modifies ``result``.
    """
    if result is None or not isinstance(result, Mapping):
        return _failed_payload(FEA_COMPUTE_FAILED)
    if geometry_objs is None:
        geometry_objs = {}
    if not isinstance(geometry_objs, Mapping):
        return _failed_payload(FEA_COMPUTE_FAILED)
    try:
        return _compute_fea(result, geometry_objs)
    except Exception:
        return _failed_payload(FEA_COMPUTE_FAILED)


__all__ = [
    "DENT_THRESHOLD",
    "TEAR_THRESHOLD",
    "NAVIER_SERIES_MAX",
    "FEA_COMPUTE_FAILED",
    "FEA_PEAK_STRESS_UNAVAILABLE",
    "FEA_IMPACT_CENTER_UNAVAILABLE",
    "FEA_IMPACT_CENTER_DEFAULTED",
    "FEA_YIELD_REFERENCE_UNAVAILABLE",
    "FEA_FALLOFF_DEFAULTED",
    "FEA_DROP_ESTIMATE_UNAVAILABLE",
    "FEA_DERIVED_ESTIMATE_FALLBACK",
    "FEA_TRANSFORM_ASSUMED_IDENTITY",
    "FEA_NO_MESHED_OBJECTS",
    "FEA_NON_FINITE_VERTEX",
    "FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE",
    "FEA_PLATE_FIELD_UNAVAILABLE",
    "FEA_PLATE_FIELD_MESH_UNAVAILABLE",
    "FEA_PLATE_FIELD_BBOX_ASPECT",
    "FEA_PLATE_FIELD_MIDPLANE",
    "compute_fea",
]
