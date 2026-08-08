"""Automated worst-case population engine for manufacturing-tolerance screening.

Simulates thousands of virtual manufactured units (default 10,000), each a
deterministic combination of manufacturing-tolerance draws, profile-driven
usage, a drop test, and component failure analysis, and aggregates
per-component failure probabilities, weakest components, unit failure rate
with a 95% Wilson confidence interval, sensitivity of failures to each
tolerance draw, and a usage-fraction survival curve.

Determinism contract: identical config + context produce a byte-identical
result regardless of worker count.  Units are processed in fixed chunks of
``CHUNK_SIZE`` and merged in unit order; per-unit output is a pure function
of (unit_seed, config, context).  Parallel workers are used only when the
platform can spawn them; otherwise the run executes serially in-process
with identical results.
"""

import importlib
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

from . import drop_sim
from . import impact
from . import lifecycle

GRAVITY_M_S2 = 9.80665
CHUNK_SIZE = 64
DEFAULT_SAMPLE_COUNT = 10000
MIN_SAMPLE_COUNT = 100
MAX_SAMPLE_COUNT = 100000
PER_UNIT_FAILURES_CAP = 100
WILSON_Z = 1.96
# Screening linear-spring contact stiffness for the component load chain.
# SCREENING VALUE, NOT CALIBRATED: measured effective contact stiffness for
# plastic enclosures on rigid floors spans ~2e5-1e6 N/m, so this convention
# under-predicts peak acceleration by up to ~3x; component shock verdicts
# (battery/PCB/screw) are screening-only and should not be read as field
# failure rates.  Overridable via config.contact_stiffness_n_per_m.
CONTACT_STIFFNESS_N_PER_M = 1e5
LCG_MULTIPLIER = 2654435761
LCG_INCREMENT = 0x9E3779B9
LCG_STREAM_MULTIPLIER = 1664525
LCG_STREAM_INCREMENT = 1013904223
LCG_MODULUS = 4294967296.0

MANUFACTURING_TOLERANCES = (
    ("mass_scale", "+/-3%"),
    ("inertia_scale_x", "+/-5%"),
    ("inertia_scale_y", "+/-5%"),
    ("inertia_scale_z", "+/-5%"),
    ("com_offset_x_m", "+/-2% of support extent"),
    ("com_offset_y_m", "+/-2% of support extent"),
    ("com_offset_z_m", "+/-2% of support extent"),
    ("screw_preload_scale", "+/-10%"),
    ("clip_thickness_scale", "+/-5%"),
    ("pcb_thickness_scale", "+/-5%"),
    ("adhesive_area_scale", "+/-10%"),
    ("battery_offset_x_m", "+/-0.001 m"),
    ("battery_offset_y_m", "+/-0.001 m"),
    ("switch_force_scale", "+/-15%"),
    ("wall_thickness_scale", "+/-5%"),
    ("shell_modulus_scale", "+/-5%"),
    ("shell_strength_scale", "+/-5%"),
    ("shell_density_scale", "+/-3%"),
)
PARAMETER_ORDER = tuple(name for name, _ in MANUFACTURING_TOLERANCES)

FALLBACK_PROFILES = {
    "esports_fps": {
        "actuations_per_day": 6000,
        "scroll_rotations_per_day": 30,
        "slide_km_per_day": 0.8,
        "switch_type": "mechanical",
        "pad_surface": "cloth",
    },
    "general": {
        "actuations_per_day": 2500,
        "scroll_rotations_per_day": 25,
        "slide_km_per_day": 0.5,
        "switch_type": "mechanical",
        "pad_surface": "cloth",
    },
    "office": {
        "actuations_per_day": 1500,
        "scroll_rotations_per_day": 40,
        "slide_km_per_day": 0.3,
        "switch_type": "mechanical",
        "pad_surface": "cloth",
    },
    "creative": {
        "actuations_per_day": 2000,
        "scroll_rotations_per_day": 80,
        "slide_km_per_day": 0.6,
        "switch_type": "mechanical",
        "pad_surface": "cloth",
    },
    "casual": {
        "actuations_per_day": 800,
        "scroll_rotations_per_day": 10,
        "slide_km_per_day": 0.2,
        "switch_type": "mechanical",
        "pad_surface": "cloth",
    },
}

FALLBACK_COMPONENT_DEFAULTS = {
    "battery": {"shock_rating_g": 450.0},
    "switch": {"actuation_force_n": 0.6, "switch_type": "mechanical"},
    "encoder": {"rated_rotations": 25000},
    "pcb": {"shock_rating_g": 500.0, "thickness_m": 0.0016},
    "adhesive": {"area_m2": 2.0e-4, "strength_pa": 5.0e6},
    "screw": {"preload_n": 2.0, "retention_force_n": 400.0},
    "clip": {"beam_thickness_m": 0.0012, "beam_width_m": 0.008, "strength_pa": 45.0e6},
    "mount": {"column_diameter_m": 0.0025, "supported_mass_kg": 0.02},
}
DEFAULT_COMPONENT_SPECS = [
    # Reference design values: tolerance draws scale these fields per unit,
    # so manufacturing variation drives the failure statistics.  Values match
    # the platform component module design defaults.
    {"component_id": "switch_primary", "type": "switch", "actuation_force_n": 0.7},
    {"component_id": "scroll_encoder", "type": "encoder"},
    {"component_id": "battery_pack", "type": "battery"},
    {"component_id": "pcb_main", "type": "pcb", "thickness_m": 0.0016},
    {"component_id": "adhesive_plate", "type": "adhesive", "area_m2": 4e-4},
    {"component_id": "screw_boss_m1_6", "type": "screw", "preload_n": 15.0},
    {"component_id": "clip_side_button", "type": "clip", "beam_thickness_m": 0.001},
    {"component_id": "mount_battery", "type": "mount"},
]
ELEC_TYPES = ("pcb", "battery", "switch", "encoder")
MECH_TYPES = ("screw", "clip", "mount", "adhesive")

_DEFAULT_SUPPORT = None


def _r(value):
    # Screening-model rates are honest to ~4 decimals at n=10,000 (the
    # Wilson CI width is ~0.6% at a 10% rate); 6 decimals would overstate
    # precision the unvalidated model constants do not support.
    return round(float(value), 4)


def _sig(value, digits=3):
    """Round an ABSOLUTE quantity to ``digits`` significant figures
    (rates use ``_r``; absolute stresses/displacements must not be rounded
    to a fixed number of decimal places, which would zero real values)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number == 0.0:
        return number
    exponent = math.floor(math.log10(abs(number)))
    factor = 10.0 ** (digits - 1 - exponent)
    return round(number * factor) / factor


def clamp_sample_count(value):
    """Clamp a sample count into [MIN_SAMPLE_COUNT, MAX_SAMPLE_COUNT]."""
    return max(MIN_SAMPLE_COUNT, min(MAX_SAMPLE_COUNT, int(value)))


def _support_or_default(support):
    global _DEFAULT_SUPPORT
    if support is not None:
        return support
    if _DEFAULT_SUPPORT is None:
        _DEFAULT_SUPPORT = drop_sim.support_points(
            [
                (x, y, z)
                for x in (-0.03, 0.03)
                for y in (-0.019, 0.019)
                for z in (-0.015, 0.015)
            ]
        )
    return _DEFAULT_SUPPORT


def _mix_seed(seed):
    """SplitMix64-style integer mixing of the unit seed.

    The raw LCG stream ``u_k(s) = (a^k * A * s + K) mod 2^32 / 2^32`` is a
    linear function of the seed; for small seeds the stream shows
    short-range serial correlation in some draws, which
    quantizes small-N failure rates to residue classes.  Mixing the seed
    through SplitMix64 decorrelates consecutive units before the stream.
    """
    z = (int(seed) + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return (z ^ (z >> 31)) & 0xFFFFFFFFFFFFFFFF


def draw_unit_parameters(seed, tolerance_scale=1.0, support=None):
    """Deterministic per-unit manufacturing-tolerance draws for a seed.

    Draws come from a fixed-order LCG stream (state initialized as
    ``(seed * 2654435761 + 0x9E3779B9) & 0xFFFFFFFF``, one draw per step
    ``u = state / 2^32``) that reproduces the mass/inertia/CoM draws of
    ``drop_sim._unit_variation(seed, support)`` for the drop unit seed, then
    continues with the remaining tolerance bands.  ``tolerance_scale``
    multiplies every band width (0 yields the nominal unit: all scales 1.0,
    offsets 0.0).  ``support`` only sets the CoM offset extents.
    """
    support = _support_or_default(support)
    extent = [0.0, 0.0, 0.0]
    for point in support:
        for axis in range(3):
            extent[axis] = max(extent[axis], abs(float(point[axis])))
    scale = max(0.0, min(2.0, float(tolerance_scale or 0.0)))
    state = (_mix_seed(seed) & 0xFFFFFFFF)

    def next_unit():
        nonlocal state
        state = (state * LCG_STREAM_MULTIPLIER + LCG_STREAM_INCREMENT) & 0xFFFFFFFF
        return state / LCG_MODULUS

    def band(half_width):
        return 1.0 + half_width * scale * (2.0 * next_unit() - 1.0)

    def offset(half_width):
        return half_width * scale * (2.0 * next_unit() - 1.0)

    mass_scale = band(0.03)
    inertia_scale = (band(0.05), band(0.05), band(0.05))
    com_offset_m = (
        offset(0.02 * extent[0]),
        offset(0.02 * extent[1]),
        offset(0.02 * extent[2]),
    )
    screw_preload_scale = band(0.10)
    clip_thickness_scale = band(0.05)
    pcb_thickness_scale = band(0.05)
    adhesive_area_scale = band(0.10)
    battery_offset_m = (offset(0.001), offset(0.001))
    switch_force_scale = band(0.15)
    # SHELL-focused manufacturing variation: wall thickness, material
    # modulus/strength batch scatter, and density.  These are the primary
    # shell-robustness tolerances (the population's core question).
    wall_thickness_scale = band(0.05)
    shell_modulus_scale = band(0.05)
    shell_strength_scale = band(0.05)
    shell_density_scale = band(0.03)
    return {
        "mass_scale": mass_scale,
        "inertia_scale": inertia_scale,
        "com_offset_m": com_offset_m,
        "screw_preload_scale": screw_preload_scale,
        "clip_thickness_scale": clip_thickness_scale,
        "pcb_thickness_scale": pcb_thickness_scale,
        "adhesive_area_scale": adhesive_area_scale,
        "battery_offset_m": battery_offset_m,
        "switch_force_scale": switch_force_scale,
        "wall_thickness_scale": wall_thickness_scale,
        "shell_modulus_scale": shell_modulus_scale,
        "shell_strength_scale": shell_strength_scale,
        "shell_density_scale": shell_density_scale,
    }


def _try_import_module(name):
    for candidate in (name, "mouse_sim." + name):
        try:
            return importlib.import_module(candidate)
        except ImportError:
            continue
    return None


_PROFILES_MODULE = None
_PROFILES_MODULE_TRIED = False
_ELEC_MODULE = None
_ELEC_MODULE_TRIED = False
_MECH_MODULE = None
_MECH_MODULE_TRIED = False


def _profiles_module():
    global _PROFILES_MODULE, _PROFILES_MODULE_TRIED
    if not _PROFILES_MODULE_TRIED:
        _PROFILES_MODULE_TRIED = True
        _PROFILES_MODULE = _try_import_module("profiles")
    return _PROFILES_MODULE


def _elec_module():
    global _ELEC_MODULE, _ELEC_MODULE_TRIED
    if not _ELEC_MODULE_TRIED:
        _ELEC_MODULE_TRIED = True
        _ELEC_MODULE = _try_import_module("components_elec")
    return _ELEC_MODULE


def _mech_module():
    global _MECH_MODULE, _MECH_MODULE_TRIED
    if not _MECH_MODULE_TRIED:
        _MECH_MODULE_TRIED = True
        _MECH_MODULE = _try_import_module("components_mech")
    return _MECH_MODULE


def _fallback_profile_usage(profile, lifespan_days):
    name = str(profile).lower()
    if name not in FALLBACK_PROFILES:
        raise ValueError(
            "unknown usage profile {!r}; supported: {}".format(
                profile, ", ".join(sorted(FALLBACK_PROFILES))
            )
        )
    table = FALLBACK_PROFILES[name]
    days = max(1, int(lifespan_days))
    return {
        "prior_drops": 0,
        "prior_impact_energy_j": 0.0,
        "actuation_cycles": int(table["actuations_per_day"]) * days,
        "slide_distance_km": round(float(table["slide_km_per_day"]) * days, 4),
        "age_days": float(days),
        "switch_type": table["switch_type"],
        "pad_surface": table["pad_surface"],
        "scroll_encoder_rotations": int(table["scroll_rotations_per_day"]) * days,
    }


def profile_usage(profile, lifespan_days):
    """Flat usage snapshot for a profile over a lifespan; raises ValueError
    for unknown profiles.  Uses the platform ``profiles`` module when
    available (unwrapping its nested ``usage`` schema), otherwise a built-in
    deterministic usage table."""
    module = _profiles_module()
    if module is not None:
        usage = module.profile_usage(profile, lifespan_days)
        if isinstance(usage, dict) and isinstance(usage.get("usage"), dict):
            return dict(usage["usage"])
        if usage is not None:
            return dict(usage)
    return _fallback_profile_usage(profile, lifespan_days)


def _default_component_specs():
    # The default suite passes through unchanged: the platform component
    # modules apply their own design defaults per type.
    return [dict(spec) for spec in DEFAULT_COMPONENT_SPECS]


def _normalize_components(components):
    if components is None:
        return _default_component_specs()
    if not isinstance(components, (list, tuple)):
        raise ValueError("config components must be a list of component specs")
    specs = []
    seen = set()
    for spec in components:
        if not isinstance(spec, dict) or "component_id" not in spec or "type" not in spec:
            raise ValueError("each component spec needs component_id and type")
        component_id = str(spec["component_id"])
        ctype = str(spec["type"])
        if component_id in seen:
            raise ValueError("duplicate component_id {!r}".format(component_id))
        seen.add(component_id)
        # Specs pass through unchanged: the platform component modules apply
        # their own design defaults, and the fallback analyzers read their
        # own defaults.  Merging the fallback defaults here would silently
        # override the reference design (e.g. a 2e-4 m^2 adhesive patch).
        specs.append(dict(spec))
    if not specs:
        raise ValueError("config components must not be empty")
    return specs


def _normalize_config(config, context):
    source = dict(config or {})
    allowed = {
        "sample_count",
        "profile",
        "lifespan_days",
        "base_seed",
        "workers",
        "tolerance_scale",
        "drop_height_m",
        "drop_surface",
        "drop_orientation",
        "components",
        "contact_stiffness_n_per_m",
        "worst_case",
    }
    unknown = sorted(set(source) - allowed)
    if unknown:
        raise ValueError(
            "unknown population config key(s): {}".format(", ".join(unknown))
        )
    worst_case = source.get("worst_case")
    if worst_case is not None:
        worst_case = _normalize_worst_case(worst_case)
    try:
        sample_count = clamp_sample_count(source.get("sample_count", DEFAULT_SAMPLE_COUNT))
    except (TypeError, ValueError):
        raise ValueError("config sample_count must be an integer")
    profile = str(source.get("profile", "esports_fps"))
    try:
        lifespan_days = int(source.get("lifespan_days", 730))
    except (TypeError, ValueError):
        raise ValueError("config lifespan_days must be an integer")
    lifespan_days = max(1, lifespan_days)
    try:
        base_seed = int(source.get("base_seed", 0))
    except (TypeError, ValueError):
        raise ValueError("config base_seed must be an integer")
    try:
        workers = int(source.get("workers", min(8, os.cpu_count() or 1)))
    except (TypeError, ValueError):
        raise ValueError("config workers must be an integer")
    workers = max(1, workers)
    try:
        tolerance_scale = float(source.get("tolerance_scale", 1.0))
    except (TypeError, ValueError):
        raise ValueError("config tolerance_scale must be numeric")
    tolerance_scale = max(0.0, min(2.0, tolerance_scale))
    try:
        drop_height_m = float(source.get("drop_height_m", 0.75))
    except (TypeError, ValueError):
        raise ValueError("config drop_height_m must be numeric")
    if not math.isfinite(drop_height_m) or drop_height_m < 0.02 or drop_height_m > 2.0:
        raise ValueError("config drop_height_m must be between 0.02 and 2.0 m")
    drop_surface = str(source.get("drop_surface", "concrete")).lower()
    if drop_surface not in drop_sim.SURFACES:
        raise ValueError(
            "config drop_surface must be one of {}".format(", ".join(sorted(drop_sim.SURFACES)))
        )
    drop_orientation = str(source.get("drop_orientation", "flat")).lower()
    if drop_orientation not in drop_sim.ORIENTATIONS:
        raise ValueError(
            "config drop_orientation must be one of {}".format(", ".join(drop_sim.ORIENTATIONS))
        )
    return {
        "sample_count": sample_count,
        "profile": profile,
        "lifespan_days": lifespan_days,
        "base_seed": base_seed,
        "workers": workers,
        "tolerance_scale": tolerance_scale,
        "drop_height_m": drop_height_m,
        "drop_surface": drop_surface,
        "drop_orientation": drop_orientation,
        "contact_stiffness_n_per_m": _finite_float(
            source.get("contact_stiffness_n_per_m"), CONTACT_STIFFNESS_N_PER_M
        ),
        "components": _normalize_components(source.get("components")),
        "worst_case": worst_case,
    }


_WORST_CASE_KEYS = (
    "wall_thickness",
    "shell_modulus",
    "shell_strength",
    "shell_density",
    "com_offset",
)


def _normalize_worst_case(raw):
    if not isinstance(raw, dict):
        raise ValueError("config worst_case must be an object")
    unknown = sorted(set(raw) - set(_WORST_CASE_KEYS) - {"drop_height", "orientation", "restitution", "friction"})
    if unknown:
        raise ValueError(
            "unknown worst_case key(s): {}".format(", ".join(unknown))
        )
    for key in _WORST_CASE_KEYS:
        value = raw.get(key, "min")
        if value not in ("min", "max"):
            raise ValueError("worst_case.{} must be 'min' or 'max'".format(key))
    drop_height = raw.get("drop_height", 2.0)
    try:
        drop_height = float(drop_height)
    except (TypeError, ValueError):
        raise ValueError("worst_case.drop_height must be numeric")
    if not math.isfinite(drop_height) or drop_height < 0.02 or drop_height > 2.0:
        raise ValueError("worst_case.drop_height must be between 0.02 and 2.0 m")
    orientation = str(raw.get("orientation", "corner")).lower()
    if orientation not in drop_sim.ORIENTATIONS:
        raise ValueError(
            "worst_case.orientation must be one of {}".format(", ".join(drop_sim.ORIENTATIONS))
        )
    return {
        "wall_thickness": raw.get("wall_thickness", "min"),
        "shell_modulus": raw.get("shell_modulus", "min"),
        "shell_strength": raw.get("shell_strength", "min"),
        "shell_density": raw.get("shell_density", "max"),
        "com_offset": raw.get("com_offset", "max"),
        "drop_height": drop_height,
        "orientation": orientation,
        "restitution": raw.get("restitution", "max"),
        "friction": raw.get("friction", "max"),
    }


def _finite_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _validate_context(context):
    if not isinstance(context, dict):
        raise ValueError("population context must be a dict with mass_kg, inertia_kg_m2, support")
    mass_kg = context.get("mass_kg")
    if mass_kg is None:
        raise ValueError("context mass_kg is required")
    try:
        mass_kg = float(mass_kg)
    except (TypeError, ValueError):
        raise ValueError("context mass_kg must be numeric")
    if not math.isfinite(mass_kg) or mass_kg <= 0.0:
        raise ValueError("context mass_kg must be a positive finite number")
    if "inertia_kg_m2" not in context:
        raise ValueError("context inertia_kg_m2 is required")
    if not context.get("support"):
        raise ValueError("context support must be a non-empty list of 3D points")
    return context


def _apply_unit_dimensions(spec, params):
    applied = dict(spec)
    ctype = applied["type"]
    # Tolerance scaling applies to spec-provided design values only; absent
    # fields keep the component module's design defaults (the population
    # must not silently override the platform reference design).
    if ctype == "screw" and "preload_n" in applied:
        applied["preload_n"] = float(applied["preload_n"]) * params["screw_preload_scale"]
    elif ctype == "clip" and "beam_thickness_m" in applied:
        applied["beam_thickness_m"] = (
            float(applied["beam_thickness_m"]) * params["clip_thickness_scale"]
        )
    elif ctype == "pcb" and "thickness_m" in applied:
        applied["thickness_m"] = (
            float(applied["thickness_m"]) * params["pcb_thickness_scale"]
        )
    elif ctype == "adhesive" and "area_m2" in applied:
        applied["area_m2"] = float(applied["area_m2"]) * params["adhesive_area_scale"]
    elif ctype == "battery":
        applied["battery_offset_m"] = params["battery_offset_m"]
    elif ctype == "switch" and "actuation_force_n" in applied:
        applied["actuation_force_n"] = (
            float(applied["actuation_force_n"]) * params["switch_force_scale"]
        )
    return applied


def _drop_summary(drop, height_m, effective_mass, surface, orientation, stiffness=None):
    peak = drop.get("peak") or {}
    first = (drop.get("drops") or [{}])[0]
    peak_speed = float(peak.get("impact_speed_m_s") or 0.0)
    capped_j = float(peak.get("kinetic_energy_j") or 0.0)
    raw_j = float(peak.get("raw_kinetic_energy_j") or 0.0)
    impact_count = int(first.get("impact_count") or 0)
    settled_s = float(first.get("settled_s") or 0.0)
    if capped_j > 0.0 and effective_mass > 0.0:
        v_capped = math.sqrt(2.0 * capped_j / effective_mass)
    else:
        v_capped = math.sqrt(2.0 * GRAVITY_M_S2 * max(0.0, float(height_m)))
    estimate = impact.estimate_impact(
        effective_mass,
        velocity_m_s=v_capped,
        restitution=drop_sim.SURFACES[surface]["restitution"],
        contact_stiffness_n_per_m=stiffness or CONTACT_STIFFNESS_N_PER_M,
    )
    accel_m_s2 = float(getattr(estimate, "peak_acceleration_m_s2", 0.0) or 0.0)
    return {
        "peak_impact_speed_m_s": _r(peak_speed),
        "impact_energy_j": _r(capped_j),
        "raw_impact_energy_j": _r(raw_j),
        "impact_count": impact_count,
        "settled_s": _r(settled_s),
        "peak_acceleration_m_s2": _r(accel_m_s2),
        "peak_acceleration_g": _r(accel_m_s2 / GRAVITY_M_S2),
        "energy_capped_speed_m_s": _r(v_capped),
        "drop_height_m": float(height_m),
        "surface": surface,
        "orientation": orientation,
    }


def _run_worst_case(config, context, usage, diagnostics):
    """Deterministic worst-case analysis: every tolerance at its band edge.

    This is NOT a Monte Carlo tail observation — it is a single unit at the
    mathematically worst corner of the tolerance box (thinness walls, weakest
    material, heaviest density, worst CoM offset, highest drop, corner
    orientation, maximum restitution/friction).  No sampling, no CI.
    """
    wc = config["worst_case"]
    ts = config["tolerance_scale"]

    def edge(half_width, key):
        band = half_width * ts
        return 1.0 + band if wc[key] == "max" else 1.0 - band

    t_scale = edge(0.05, "wall_thickness")
    e_scale = edge(0.05, "shell_modulus")
    s_scale = edge(0.05, "shell_strength")
    d_scale = edge(0.03, "shell_density")
    params = {
        "mass_scale": 1.0,
        "inertia_scale": (1.0, 1.0, 1.0),
        "com_offset_m": (0.0, 0.0, 0.0),
        "screw_preload_scale": 1.0 - 0.10 * ts,
        "clip_thickness_scale": t_scale,
        "pcb_thickness_scale": 1.0 - 0.05 * ts,
        "adhesive_area_scale": 1.0 - 0.10 * ts,
        "battery_offset_m": (0.0, 0.0),
        "switch_force_scale": 1.0 + 0.15 * ts,
        "wall_thickness_scale": t_scale,
        "shell_modulus_scale": e_scale,
        "shell_strength_scale": s_scale,
        "shell_density_scale": d_scale,
    }
    shell = context.get("shell")
    shell_block = None
    shell_failed = False
    if shell is not None:
        nominal = shell.get("nominal") or {}
        sf_nominal = nominal.get("safety_factor")
        sf_unit = None
        if sf_nominal is not None:
            try:
                sf_unit = float(sf_nominal) * s_scale * t_scale * t_scale
            except (TypeError, ValueError):
                sf_unit = None
        shell_failed = sf_unit is not None and sf_unit < 1.0
        shell_block = {
            "safety_factor": _sig(sf_unit) if sf_unit is not None else None,
            "peak_stress_pa": (
                _sig(float(nominal.get("peak_stress_pa")) / (t_scale * t_scale))
                if nominal.get("peak_stress_pa") is not None
                else None
            ),
            "max_displacement_m": (
                _sig(float(nominal.get("max_displacement_m")) / (e_scale * t_scale ** 3))
                if nominal.get("max_displacement_m") is not None
                else None
            ),
            "verdict": "fail" if shell_failed else "pass",
        }
    # Worst-case drop: highest specified height, corner orientation, maximum
    # friction/restitution — the drop mass carries the heaviest shell factors.
    com_offset = context.get("com_offset_m") or (0.0, 0.0, 0.0)
    worst_com = tuple(
        (0.02 * max(0.001, abs(value))) * ts + value
        for value in com_offset
    )
    effective_mass = (
        context["mass_kg"] * params["mass_scale"] * d_scale * t_scale
    )
    restitution_scale = 1.0 + 0.10 * ts
    friction_scale = 1.0 + 0.10 * ts
    drop = drop_sim.simulate(
        context["mass_kg"],
        context["inertia_kg_m2"],
        context["support"],
        wc["drop_height"],
        config["drop_surface"],
        drop_count=1,
        test="drop",
        orientation=wc["orientation"],
        seed=0,
        unit_seed=None,
        com_offset_m=worst_com,
        mass_scale=d_scale * t_scale,
        restitution_scale=restitution_scale,
        friction_scale=friction_scale,
    )
    summary = _drop_summary(
        drop,
        wc["drop_height"],
        effective_mass,
        config["drop_surface"],
        wc["orientation"],
        stiffness=config["contact_stiffness_n_per_m"],
    )
    restitution_scale_l, friction_scale_l, damage, _ = lifecycle.degradation_factors(usage)
    component_context = _component_context(
        context, params, usage, damage, restitution_scale_l, friction_scale_l,
        summary, 0, effective_mass,
    )
    applied_specs = [_apply_unit_dimensions(spec, params) for spec in config["components"]]
    analysis = _analyze_components(applied_specs, component_context)
    component_rows = []
    any_component_fail = False
    for result in analysis:
        failed = result.get("status") == "fail"
        if failed:
            any_component_fail = True
        component_rows.append(
            {
                "component_id": result.get("component_id", "unknown"),
                "type": result.get("type", "unknown"),
                "status": "fail" if failed else "ok",
                "usage_ratio": _r(float(result.get("usage_ratio") or 0.0)),
            }
        )
    verdict = "fail" if (shell_failed or any_component_fail) else "pass"
    return {
        "mode": "deterministic_worst_case",
        "sample_count": 1,
        "profile": config["profile"],
        "lifespan_days": config["lifespan_days"],
        "base_seed": config["base_seed"],
        "workers": 1,
        "shell": shell_block,
        "drop": {
            "drop_height_m": wc["drop_height"],
            "surface": config["drop_surface"],
            "orientation": wc["orientation"],
            "peak_impact_speed_m_s": summary["peak_impact_speed_m_s"],
            "impact_energy_j": summary["impact_energy_j"],
            "peak_acceleration_g": summary["peak_acceleration_g"],
        },
        "components": component_rows,
        "verdict": verdict,
        "assumptions": [
            "every tolerance at its worst-case band edge (tolerance_scale = {})".format(ts),
            "deterministic — no sampling, no CI; replay requires only the same config",
            "worst-case drop at {:.2f} m, {} orientation, maximum restitution/friction".format(
                wc["drop_height"], wc["orientation"]
            ),
            "physical-model confidence: screening (uncalibrated closed-form laws)",
        ],
        "diagnostics": diagnostics + ["mode: deterministic worst-case (no Monte Carlo sampling)"],
        "model": _model(config, context),
    }


def _component_context(context, params, usage, damage, restitution_scale, friction_scale,
                       summary, seed, effective_mass):
    base = dict(context)
    base.pop("drop", None)
    base.pop("lifecycle", None)
    base["drop"] = summary
    base["lifecycle"] = {
        "usage": usage,
        "damage": damage,
        "restitution_scale": restitution_scale,
        "friction_scale": friction_scale,
    }
    base["unit"] = {
        "seed": seed,
        "parameters": params,
        "effective_mass_kg": effective_mass,
    }
    return base


def _fallback_ratio(spec, ctx, ctype):
    drop = ctx["drop"]
    accel_g = float(drop.get("peak_acceleration_g") or 0.0)
    accel_m_s2 = float(drop.get("peak_acceleration_m_s2") or 0.0)
    mass = float(ctx["unit"].get("effective_mass_kg") or 0.0)
    force = mass * accel_m_s2
    usage = ctx["lifecycle"]["usage"]
    if ctype == "battery":
        rating = float(spec.get("shock_rating_g", 450.0))
        ratio = accel_g / rating if rating > 0.0 else 0.0
    elif ctype == "switch":
        switch_type = spec.get("switch_type") or usage.get("switch_type") or "unknown"
        rated = lifecycle.RATED_SWITCH_ACTUATIONS.get(switch_type, 20_000_000)
        ratio = int(usage.get("actuation_cycles", 0)) / float(rated)
    elif ctype == "encoder":
        rated = float(spec.get("rated_rotations", lifecycle.SCROLL_ENCODER_RATED_ROTATIONS))
        steps = int(usage.get("scroll_encoder_rotations", 0))
        rotations = steps / lifecycle.SCROLL_ENCODER_DETENTS_PER_REVOLUTION if steps else 0.0
        ratio = rotations / rated if rated > 0.0 else 0.0
    elif ctype == "pcb":
        rating = float(spec.get("shock_rating_g", 500.0))
        thickness = float(spec.get("thickness_m", 0.0016))
        rating_eff = rating * math.sqrt(thickness / 0.0016)
        ratio = accel_g / rating_eff if rating_eff > 0.0 else 0.0
    elif ctype == "adhesive":
        area = float(spec.get("area_m2", 2.0e-4))
        strength = float(spec.get("strength_pa", 5.0e6))
        stress = force / area if area > 0.0 else 0.0
        ratio = stress / strength if strength > 0.0 else 0.0
    elif ctype == "screw":
        retention = float(spec.get("retention_force_n", 400.0)) * float(spec.get("preload_n", 2.0)) / 2.0
        ratio = force / retention if retention > 0.0 else 0.0
    elif ctype == "clip":
        thickness = float(spec.get("beam_thickness_m", 0.0012))
        width = float(spec.get("beam_width_m", 0.008))
        strength = float(spec.get("strength_pa", 45.0e6))
        area = thickness * width
        stress = force / area if area > 0.0 else 0.0
        ratio = stress / strength if strength > 0.0 else 0.0
    elif ctype == "skate":
        initial_m = float(spec.get("thickness_m", 0.0004))
        slide_km = float(usage.get("slide_distance_km", 0.0))
        pad = usage.get("pad_surface", "cloth")
        rate_mm = lifecycle.skate_wear_rate_mm_per_km(pad)
        initial_mm = initial_m * 1000.0
        remaining_mm = max(0.0, initial_mm - rate_mm * max(0.0, slide_km))
        ratio = 1.0 - remaining_mm / initial_mm if initial_mm > 0.0 else 0.0
    else:
        ratio = 0.0
    if not math.isfinite(ratio) or ratio < 0.0:
        ratio = 0.0
    return ratio, "fail" if ratio >= 1.0 else "ok"


def _fallback_analyze_one(spec, ctx):
    ctype = spec.get("type", "unknown")
    ratio, status = _fallback_ratio(spec, ctx, ctype)
    return {
        "component_id": spec.get("component_id", "unknown"),
        "type": ctype,
        "status": status,
        "usage_ratio": _r(ratio),
        "margin": _r(1.0 - ratio),
        "model": "fallback_screening_v1",
    }


def _fallback_analyze(specs, ctx):
    return [_fallback_analyze_one(spec, ctx) for spec in specs]


def _normalize_module_results(raw, specs):
    if raw is None:
        return None
    items = list(raw)
    if len(items) != len(specs):
        return None
    results = []
    for spec, item in zip(specs, items):
        if isinstance(item, dict):
            get = item.get
        else:
            get = lambda key, default=None: getattr(item, key, default)
        component_id = get("component_id", None) or spec.get("component_id", "unknown")
        ctype = get("type", None) or get("component_type", None) or spec.get("type", "unknown")
        status = get("status", None) or get("verdict", None) or get("result", None) or "ok"
        status = str(status).strip().lower()
        failed = status in (
            "fail", "failed", "critical", "failure", "exceeded", "over_limit", "unsupported",
        )
        usage_ratio = get("usage_ratio", None)
        if usage_ratio is None:
            usage_ratio = get("cycles_ratio", None)
        if usage_ratio is None:
            usage_ratio = get("damage", None)
        if usage_ratio is None:
            usage_ratio = get("ratio", None)
        try:
            usage_ratio = float(usage_ratio) if usage_ratio is not None else 0.0
        except (TypeError, ValueError):
            usage_ratio = 0.0
        if not math.isfinite(usage_ratio) or usage_ratio < 0.0:
            usage_ratio = 0.0
        results.append(
            {
                "component_id": component_id,
                "type": ctype,
                "status": "fail" if failed else "ok",
                "usage_ratio": _r(usage_ratio),
                "usage_ratio_raw": usage_ratio,
                "margin": None,
                "model": "platform",
            }
        )
    return results


def _analyze_components(component_specs, component_context):
    results = []
    elec_specs = [spec for spec in component_specs if spec["type"] in ELEC_TYPES]
    mech_specs = [spec for spec in component_specs if spec["type"] in MECH_TYPES]
    for module, specs in ((_elec_module(), elec_specs), (_mech_module(), mech_specs)):
        if not specs:
            continue
        if module is None:
            results.extend(_fallback_analyze(specs, component_context))
            continue
        try:
            normalized = _normalize_module_results(module.analyze_many(specs, component_context), specs)
        except Exception:
            normalized = None
        if normalized is None:
            results.extend(_fallback_analyze(specs, component_context))
        else:
            results.extend(normalized)
    return results


def _process_unit(index, config, context):
    base_seed = int(config["base_seed"])
    seed = (base_seed + index) & 0xFFFFFFFF
    tolerance_scale = config["tolerance_scale"]
    params = draw_unit_parameters(seed, tolerance_scale, context["support"])
    usage = profile_usage(config["profile"], config["lifespan_days"])
    restitution_scale, friction_scale, damage, _ = lifecycle.degradation_factors(usage)
    unit_seed = seed if tolerance_scale != 0.0 else None
    com_offset = context.get("com_offset_m") or (0.0, 0.0, 0.0)
    # The shell mass factors (density x thickness) enter the DROP itself, so
    # the simulated mass, energy, and acceleration are the same physical
    # quantities the component chain then consumes.
    drop = drop_sim.simulate(
        context["mass_kg"],
        context["inertia_kg_m2"],
        context["support"],
        config["drop_height_m"],
        config["drop_surface"],
        drop_count=1,
        test="drop",
        orientation=config["drop_orientation"],
        seed=0,
        unit_seed=unit_seed,
        unit_scale=config["tolerance_scale"],
        com_offset_m=com_offset,
        mass_scale=params["shell_density_scale"] * params["wall_thickness_scale"],
        friction_scale=friction_scale,
        restitution_scale=restitution_scale,
    )
    # The shell mass scales with wall thickness and density: a thicker or
    # denser shell unit is heavier, shifting the drop physics consistently.
    effective_mass = (
        context["mass_kg"]
        * params["mass_scale"]
        * params["shell_density_scale"]
        * params["wall_thickness_scale"]
    )
    summary = _drop_summary(
        drop, config["drop_height_m"], effective_mass,
        config["drop_surface"], config["drop_orientation"],
        stiffness=config.get("contact_stiffness_n_per_m"),
    )
    component_context = _component_context(
        context, params, usage, damage, restitution_scale, friction_scale,
        summary, seed, effective_mass,
    )
    applied_specs = [_apply_unit_dimensions(spec, params) for spec in config["components"]]
    analysis = _analyze_components(applied_specs, component_context)
    failed_components = []
    worst_ratio = 0.0
    worst_component_id = None
    for result in analysis:
        ratio = float(result.get("usage_ratio_raw") or result.get("usage_ratio") or 0.0)
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_component_id = result.get("component_id")
        if result.get("status") == "fail":
            failed_components.append(
                {
                    "component_id": result.get("component_id", "unknown"),
                    "type": result.get("type", "unknown"),
                }
            )
    # SHELL analysis: the primary engineering question.  The nominal
    # closed-form structural response (from the pipeline's load case) scales
    # with the per-unit shell variation: bending stress ~ 1/t^2, safety
    # factor ~ strength*t^2, deflection ~ 1/(E*t^3) (load-controlled plate
    # and beam laws).  The shell fails when its safety factor drops below 1.
    shell = context.get("shell")
    shell_record = None
    shell_failed = False
    if shell is not None:
        nominal = shell.get("nominal") or {}
        sf_nominal = nominal.get("safety_factor")
        sigma_nominal = nominal.get("peak_stress_pa")
        w_nominal = nominal.get("max_displacement_m")
        t_scale = params["wall_thickness_scale"]
        e_scale = params["shell_modulus_scale"]
        s_scale = params["shell_strength_scale"]
        sf_unit = None
        if sf_nominal is not None:
            try:
                sf_unit = float(sf_nominal) * s_scale * t_scale * t_scale
            except (TypeError, ValueError):
                sf_unit = None
        shell_failed = sf_unit is not None and sf_unit < 1.0
        shell_record = {
            "unit": index,
            "seed": seed,
            "failed": shell_failed,
            "safety_factor": _sig(sf_unit) if sf_unit is not None else None,
            "peak_stress_pa": (
                _sig(float(sigma_nominal) / (t_scale * t_scale))
                if sigma_nominal is not None
                else None
            ),
            "max_displacement_m": (
                _sig(float(w_nominal) / (e_scale * t_scale ** 3))
                if w_nominal is not None
                else None
            ),
        }
    record = {
        "unit": index,
        "seed": seed,
        "failed": bool(failed_components),
        "failed_components": failed_components,
        "worst_ratio": _r(worst_ratio),
        "worst_ratio_raw": worst_ratio,
        "worst_component_id": worst_component_id,
        "shell_failed": shell_failed,
        "shell": shell_record,
    }
    return record, params


def _parameter_values(params):
    return {
        "mass_scale": params["mass_scale"],
        "inertia_scale_x": params["inertia_scale"][0],
        "inertia_scale_y": params["inertia_scale"][1],
        "inertia_scale_z": params["inertia_scale"][2],
        "com_offset_x_m": params["com_offset_m"][0],
        "com_offset_y_m": params["com_offset_m"][1],
        "com_offset_z_m": params["com_offset_m"][2],
        "screw_preload_scale": params["screw_preload_scale"],
        "clip_thickness_scale": params["clip_thickness_scale"],
        "pcb_thickness_scale": params["pcb_thickness_scale"],
        "adhesive_area_scale": params["adhesive_area_scale"],
        "battery_offset_x_m": params["battery_offset_m"][0],
        "battery_offset_y_m": params["battery_offset_m"][1],
        "switch_force_scale": params["switch_force_scale"],
        "wall_thickness_scale": params["wall_thickness_scale"],
        "shell_modulus_scale": params["shell_modulus_scale"],
        "shell_strength_scale": params["shell_strength_scale"],
        "shell_density_scale": params["shell_density_scale"],
    }


def _empty_stats(component_ids):
    return {
        "unit_failures": 0,
        "component_failures": dict.fromkeys(component_ids, 0),
        "worst_component_counts": dict.fromkeys(component_ids, 0),
        "ratio_bins": [0] * 11,
        "survivors": 0,
        "params": {name: [0.0, 0.0, 0.0, 0.0, 0] for name in PARAMETER_ORDER},
        "shell_failures": 0,
        "shell_params": {name: [0.0, 0.0, 0.0, 0.0, 0] for name in PARAMETER_ORDER},
    }


def _fold_unit(stats, record, params):
    if record["failed"]:
        stats["unit_failures"] += 1
    for failed in record["failed_components"]:
        component_id = failed.get("component_id") or "unknown"
        stats["component_failures"][component_id] = stats["component_failures"].get(component_id, 0) + 1
    if record["worst_component_id"] is not None:
        worst_id = record["worst_component_id"]
        stats["worst_component_counts"][worst_id] = stats["worst_component_counts"].get(worst_id, 0) + 1
    ratio = record.get("worst_ratio_raw", record["worst_ratio"])
    if not math.isfinite(ratio) or ratio < 0.0:
        ratio = 0.0
    # Failure-time fraction u_f = 1/r: units whose worst ratio is below 1
    # outlive the horizon (survivors); the rest are histogrammed by their
    # failure time within the horizon.
    if ratio >= 1.0:
        failure_fraction = 1.0 / ratio
        stats["ratio_bins"][min(10, int(failure_fraction * 10.0))] += 1
    else:
        stats["survivors"] += 1
    failed = 1 if record["failed"] else 0
    shell_failed = 1 if record.get("shell_failed") else 0
    if shell_failed:
        stats["shell_failures"] += 1
    values = _parameter_values(params)
    for name in PARAMETER_ORDER:
        value = values[name]
        entry = stats["params"][name]
        entry[0] += value
        entry[1] += value * value
        entry[2] += value * failed
        entry[3] += value * value * failed
        entry[4] += failed
        shell_entry = stats["shell_params"][name]
        shell_entry[0] += value
        shell_entry[1] += value * value
        shell_entry[2] += value * shell_failed
        shell_entry[3] += value * value * shell_failed
        shell_entry[4] += shell_failed


def _process_chunk(indices, config, context):
    stats = _empty_stats([spec.get("component_id") or spec["type"] for spec in config["components"]])
    units = []
    for index in indices:
        record, params = _process_unit(index, config, context)
        units.append(record)
        _fold_unit(stats, record, params)
    return {"units": units, "stats": stats}


def _merge_stats(total, chunk_stats):
    total["unit_failures"] += chunk_stats["unit_failures"]
    for key, value in chunk_stats["component_failures"].items():
        total["component_failures"][key] += value
    for key, value in chunk_stats["worst_component_counts"].items():
        total["worst_component_counts"][key] += value
    for bin_index in range(11):
        total["ratio_bins"][bin_index] += chunk_stats["ratio_bins"][bin_index]
    total["survivors"] += chunk_stats["survivors"]
    total["shell_failures"] += chunk_stats["shell_failures"]
    for name in PARAMETER_ORDER:
        target = total["params"][name]
        chunk = chunk_stats["params"][name]
        for i in range(5):
            target[i] += chunk[i]
        shell_target = total["shell_params"][name]
        shell_chunk = chunk_stats["shell_params"][name]
        for i in range(5):
            shell_target[i] += shell_chunk[i]


def _process_pool_context():
    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return None


def _run_parallel(chunks, chunk_ranges, config, context, workers, diagnostics):
    pool_context = _process_pool_context()
    if pool_context is None:
        return False
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=pool_context) as executor:
            futures = [
                executor.submit(_process_chunk, indices, config, context)
                for indices in chunk_ranges
            ]
            for future in futures:
                chunks.append(future.result())
        return True
    except Exception as exc:
        del chunks[:]
        diagnostics.append(
            "parallel execution unavailable ({!r}); ran serially in-process".format(exc)
        )
        return False


def _wilson(failures, n, z=WILSON_Z):
    if n <= 0:
        return {"low": 0.0, "high": 0.0}
    p = failures / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denominator
    return {
        "low": round(max(0.0, center - margin), 4),
        "high": round(min(1.0, center + margin), 4),
    }


def _sensitivity(total, n, params_key="params", failure_count=None):
    entries = []
    for name in PARAMETER_ORDER:
        sum_x, sum_sq, sum_f, sum_sq_f, count_f = total[params_key][name]
        mean = sum_x / n
        std = 0.0
        if n > 1:
            variance = (sum_sq - sum_x * sum_x / n) / (n - 1)
            std = math.sqrt(variance) if variance > 0.0 else 0.0
        correlation = None
        if 0 < count_f < n and std > 0.0:
            p = count_f / n
            mean_failed = sum_f / count_f
            mean_passed = (sum_x - sum_f) / (n - count_f)
            correlation = (mean_failed - mean_passed) * math.sqrt(p * (1.0 - p)) / std
            correlation = max(-1.0, min(1.0, correlation))
        if correlation is None:
            level = "NOT_OBSERVED"
        elif abs(correlation) >= 0.3:
            level = "HIGH"
        elif abs(correlation) >= 0.1:
            level = "MEDIUM"
        else:
            level = "LOW"
        entries.append(
            {
                "parameter": name,
                "correlation": round(correlation, 3) if correlation is not None else None,
                "level": level,
                "mean_value": _r(mean),
                "std_value": _r(std),
            }
        )
    entries.sort(
        key=lambda item: (
            item["correlation"] is None,
            -(abs(item["correlation"]) if item["correlation"] is not None else 0.0),
            item["parameter"],
        )
    )
    return entries


def _survival(bins, survivors, n):
    """Usage-fraction survival curve.

    Each unit's worst usage ratio ``r`` (fraction of its screening life
    consumed over the full lifespan) gives a failure time fraction
    ``u_f = 1/r`` (units with ``r >= 1`` fail within the lifespan; units with
    ``r < 1`` outlive the horizon).  ``bins`` histogram ``min(1, u_f)`` for
    the units that fail within the horizon; ``survivors`` counts the rest.
    ``S(u) = P(u_f > u)`` — the fraction of units still alive at usage
    fraction ``u`` — so ``S(1.0) = P(r < 1) = 1 - failure_rate``.
    """
    curve = []
    for step in range(1, 11):
        fraction = (survivors + sum(bins[step:])) / n
        curve.append(
            {
                "usage_fraction": round(step / 10.0, 1),
                "survival_rate": _r(fraction),
            }
        )
    return curve


def _per_unit_failures(per_unit_records):
    records = []
    for record in per_unit_records:
        if not record["failed"]:
            continue
        for failed in record["failed_components"]:
            records.append(
                {
                    "unit": record["unit"],
                    "seed": record["seed"],
                    "component_id": failed["component_id"],
                    "type": failed["type"],
                }
            )
            if len(records) >= PER_UNIT_FAILURES_CAP:
                return records
    return records


def _model(config, context):
    return {
        "manufacturing_tolerances": [name for name, _ in MANUFACTURING_TOLERANCES],
        "drop": {
            "test": "drop",
            "contact_stiffness_n_per_m": config.get("contact_stiffness_n_per_m", CONTACT_STIFFNESS_N_PER_M),
            "drop_count": 1,
            "height_m": config["drop_height_m"],
            "surface": config["drop_surface"],
            "orientation": config["drop_orientation"],
            "impact_model": "estimate_impact linear spring, k = 1e5 N/m",
            "restitution_source": "drop_sim.SURFACES[surface]",
        },
        "assumptions": [
            "per-unit output is a pure function of (unit_seed, config, context); units are "
            "processed in fixed 64-unit chunks and merged in unit order, so worker count and "
            "scheduling cannot affect the result",
            "usage is profile-driven and identical for every unit; tolerance draws (including "
            "skate thickness) do not change usage",
            "drop mass/inertia/CoM variation uses the simulator unit-seed bands "
            "(drop_sim._unit_variation); reported draws mirror the same LCG stream at "
            "tolerance_scale = 1.0, and tolerance_scale = 0 runs the drop nominally",
            "component failure models are deterministic screening estimates (platform "
            "components_elec/components_mech analyzers when available, otherwise built-in "
            "fallback models); battery placement offset and switch actuation force are "
            "recorded but do not drive fallback verdicts",
            "95% Wilson confidence interval with z = 1.96, clamped to [0, 1]",
            "survival curve S(u) = fraction of units whose worst usage ratio (cycles/rating, "
            "damage) has not yet been reached at usage fraction u (worst ratio >= u); the "
            "curve is monotonic non-increasing",
            "per-unit failure records are capped at 100 entries",
        ],
    }


def _base_diagnostics(config, context, usage):
    lines = []
    if _profiles_module() is None:
        lines.append("profiles module unavailable; using built-in deterministic usage-profile table")
    if _elec_module() is None or _mech_module() is None:
        lines.append(
            "components_elec/components_mech modules unavailable; using built-in fallback "
            "component screening models"
        )
    else:
        lines.append("using platform components_elec/components_mech analyzers")
    lines.append(
        "profile {!r} over {} days: {:,} actuations, {:,} scroll rotations, {:.1f} km slide, "
        "{} pad".format(
            config["profile"],
            config["lifespan_days"],
            int(usage.get("actuation_cycles", 0)),
            int(usage.get("scroll_encoder_rotations", 0)),
            float(usage.get("slide_distance_km", 0.0)),
            usage.get("pad_surface", "cloth"),
        )
    )
    return lines


def _aggregate_diagnostics(config, context, total, per_unit_records):
    n = config["sample_count"]
    worst = total["worst_component_counts"]
    order = sorted(worst, key=lambda cid: (-worst[cid], cid))
    top = ", ".join(
        "{} {:.0%}".format(cid, worst[cid] / n) for cid in order[:4] if worst[cid] > 0
    )
    lines = [
        "population: {} units, {} failed ({:.2%}), {} component failures total".format(
            n,
            total["unit_failures"],
            total["unit_failures"] / n,
            sum(total["component_failures"].values()),
        ),
        "worst component by usage ratio: {}".format(top if top else "none"),
        "manufacturing tolerances: {}".format(
            ", ".join("{} {}".format(name, band) for name, band in MANUFACTURING_TOLERANCES)
        ),
    ]
    return lines


def _assemble_result(config, context, total, per_unit_records, diagnostics):
    n = config["sample_count"]
    units_failed = total["unit_failures"]
    component_ids = [spec.get("component_id") or spec["type"] for spec in config["components"]]
    component_types = {spec.get("component_id") or spec["type"]: spec["type"] for spec in config["components"]}
    component_rates = []
    for cid in component_ids:
        failures = total["component_failures"][cid]
        component_rates.append(
            {
                "component_id": cid,
                "type": component_types[cid],
                "failures": failures,
                "rate": _r(failures / n),
                "wilson_ci": _wilson(failures, n),
                "rank": 0,
            }
        )
    component_rates.sort(key=lambda item: (-item["rate"], item["component_id"]))
    for rank, item in enumerate(component_rates, start=1):
        item["rank"] = rank
    weakest = [
        {
            "component_id": item["component_id"],
            "type": item["type"],
            "rate": item["rate"],
            "rank": item["rank"],
        }
        for item in component_rates[:3]
    ]
    # SHELL result: the primary engineering answer of the population.  The
    # shell failure rate (safety factor below 1 across manufacturing
    # variation) is reported separately from the secondary component
    # screening; the shell sensitivity shows which shell tolerance drives
    # shell failures.
    shell_block = None
    shell = context.get("shell")
    if shell is not None:
        nominal = shell.get("nominal") or {}
        shell_failures = total["shell_failures"]
        shell_block = {
            "nominal": {
                "safety_factor": _sig(nominal.get("safety_factor")),
                "peak_stress_pa": _sig(nominal.get("peak_stress_pa")),
                "max_displacement_m": _sig(nominal.get("max_displacement_m")),
                "wall_thickness_m": _sig(nominal.get("t_m")),
            },
            "failures": shell_failures,
            "failure_rate": _r(shell_failures / n),
            "wilson_ci": _wilson(shell_failures, n),
            "sensitivity": _sensitivity(total, n, params_key="shell_params"),
            "assumptions": [
                "shell response scales with per-unit manufacturing variation: bending stress ~ 1/t^2, safety factor ~ strength*t^2, deflection ~ 1/(E*t^3) (load-controlled closed-form laws)",
                "shell failure is load-controlled: the shell modulus affects deflection only, NOT the safety factor or the shell failure rate",
                "shell failure = structural safety factor below 1 under the pinned load case; drop-derived loading is not part of the shell safety-factor screen",
                "the shell mass factors (density x thickness) enter the per-unit DROP mass, so the drop physics and the component load chain stay consistent",
                "population drops include the mass-model center-of-mass offset",
                "the Wilson CI covers SAMPLING error only; model/parameter uncertainty is unquantified",
                "sensitivity method: point-biserial correlation of per-unit parameter draws with shell failure (Monte Carlo) — correlation, not causation",
            ],
        }
    return {
        "sample_count": n,
        "profile": config["profile"],
        "lifespan_days": config["lifespan_days"],
        "base_seed": config["base_seed"],
        "workers": config["workers"],
        "units_failed": units_failed,
        "failure_rate": _r(units_failed / n),
        "wilson_ci": _wilson(units_failed, n),
        "component_failure_rates": component_rates,
        "weakest_components": weakest,
        "shell": shell_block,
        "sensitivity": _sensitivity(total, n),
        "survival": _survival(total["ratio_bins"], total["survivors"], n),
        "per_unit_failures": _per_unit_failures(per_unit_records),
        "total_component_failures": sum(total["component_failures"].values()),
        "model": _model(config, context),
        "diagnostics": diagnostics,
    }


def run_population(config, context):
    """Run the Monte-Carlo population simulation (or, with a ``worst_case``
    block, the deterministic worst-case analysis).

    ``config``: sample_count (clamped to [100, 100000]), profile, lifespan_days,
    base_seed, workers, drop_height_m, drop_surface, drop_orientation,
    tolerance_scale, components (list of specs; default one spec per component
    type), worst_case (deterministic corner analysis; see ``_run_worst_case``).
    ``context``: shared component context with mass_kg, inertia_kg_m2,
    support, materials, environment_temperature_k; drop/lifecycle are computed
    per unit.

    Returns a JSON-clean aggregate dict (see module docstring); raises
    ValueError on invalid configuration.
    """

    context = _validate_context(context)
    config = _normalize_config(config, context)
    usage = profile_usage(config["profile"], config["lifespan_days"])
    diagnostics = _base_diagnostics(config, context, usage)
    if config["worst_case"] is not None:
        return _run_worst_case(config, context, usage, diagnostics)
    chunk_ranges = [
        range(start, min(start + CHUNK_SIZE, config["sample_count"]))
        for start in range(0, config["sample_count"], CHUNK_SIZE)
    ]
    chunks = []
    if config["workers"] > 1:
        _run_parallel(chunks, chunk_ranges, config, context, config["workers"], diagnostics)
    if not chunks:
        for indices in chunk_ranges:
            chunks.append(_process_chunk(indices, config, context))
    total = _empty_stats([spec["component_id"] for spec in config["components"]])
    per_unit_records = []
    for chunk in chunks:
        _merge_stats(total, chunk["stats"])
        per_unit_records.extend(chunk["units"])
    diagnostics.extend(
        _aggregate_diagnostics(config, context, total, per_unit_records)
    )
    return _assemble_result(config, context, total, per_unit_records, diagnostics)


__all__ = [
    "CHUNK_SIZE",
    "DEFAULT_SAMPLE_COUNT",
    "PER_UNIT_FAILURES_CAP",
    "clamp_sample_count",
    "draw_unit_parameters",
    "profile_usage",
    "run_population",
]
