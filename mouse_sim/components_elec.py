"""Closed-form electronics component failure models for a gaming mouse.

Each model is a deterministic SCREENING assessment of one component type
against the drop/impact summary, the usage snapshot, and the environment.
The models are intentionally simple closed-form screening laws (plate
bending, impulse shear, Miner-style damage accumulation, Coffin-Manson-class
thermal fatigue) with honest validity and assumption disclosure; they never
raise on bad input.

Screening status vocabulary: ``validity`` is always ``"approximate"`` for
these models (their constants are class-level engineering data, not
calibration measurements); ``status`` uses marginal bands so a knife-edge
rating (e.g. exactly at the rated cycle count) reports ``warn`` with a
``..._MARGINAL`` finding instead of a hard ``fail``.

Context contract (all keys optional):
    mass_kg, inertia_kg_m2, support, materials (dict key -> MaterialDefinition),
    drop: {peak_impact_speed_m_s, peak_accel_g | peak_acceleration_g,
           peak_force_n, impact_count, settled_s, peak_raw_energy_j} | None,
    lifecycle: the usage snapshot dict (prior_drops, actuation_cycles,
           scroll_encoder_rotations, ...) OR {"usage": {...}, ...} | None,
    environment_temperature_k | None.
"""

import math

from .impact import GENERIC_FATIGUE_EXPONENT_K, GENERIC_FATIGUE_STRENGTH_AT_1E6_PA

COMPONENT_TYPES = ("pcb", "battery", "switch", "encoder")

# Detents (wheel steps, as users perceive them) per full encoder revolution.
# ALPS EC11-class mechanical encoders use ~24 detents per revolution; encoder
# lifetime ratings are quoted in revolutions, so usage in wheel steps must be
# converted before comparison.
DETENTS_PER_REVOLUTION = 24


def defaults(component_type):
    if component_type == "pcb":
        return {
            "width_m": 0.04,
            "length_m": 0.06,
            "thickness_m": 0.0016,
            "mounting": "standoffs",
            "component_mass_kg": 0.002,
            "board_density_kg_m3": 1850.0,
            "material": "FR-4",
            "allowable_flex_stress_pa": 65e6,
            "solder_joint_area_m2": 2e-7,
            "solder_joint_count": 200,
            "solder_allowable_shear_pa": 20e6,
            "thermal_cycles_per_day": 1,
            "delta_temperature_k": 30,
        }
    if component_type == "battery":
        return {
            "mass_kg": 0.02,
            "crush_load_n": 130.0,
            "shock_limit_g": 500.0,
            "temperature_max_k": 333.15,
        }
    if component_type == "switch":
        return {
            "switch_type": "mechanical",
            "actuation_force_n": 0.7,
            "button_stalk_diameter_m": 0.0025,
            "button_stalk_length_m": 0.005,
            "material": "ABS",
        }
    if component_type == "encoder":
        return {
            "encoder_type": "mechanical",
            "detents_per_revolution": DETENTS_PER_REVOLUTION,
        }
    raise ValueError("unknown component type {!r}".format(component_type))


def _usage_snapshot(context):
    lifecycle = context.get("lifecycle")
    if isinstance(lifecycle, dict):
        nested = lifecycle.get("usage")
        if isinstance(nested, dict):
            return nested
        return lifecycle
    return {}


def _drop_summary(context):
    drop = context.get("drop")
    if not isinstance(drop, dict):
        drop = {}
    accel_g = drop.get("peak_accel_g")
    if accel_g is None:
        accel_g = drop.get("peak_acceleration_g")
    return {
        "accel_g": _finite_float(accel_g, 0.0),
        "peak_force_n": _finite_float(drop.get("peak_force_n"), 0.0),
        "peak_speed_m_s": _finite_float(drop.get("peak_impact_speed_m_s"), 0.0),
        "settled_s": _finite_float(drop.get("settled_s"), 0.0),
        "impact_count": int(drop.get("impact_count") or 0),
    }


def _finite_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _age_days(context):
    return _finite_float(_usage_snapshot(context).get("age_days"), 0.0)


def _result(component_id, ctype, status, metrics, findings, assumptions, usage_ratio, validity="approximate"):
    """Screening result: validity is ``approximate`` by default — the
    constants are class-level engineering data, not calibration
    measurements.  ``not_evaluated`` results carry their own validity."""
    return {
        "component_id": str(component_id or ctype),
        "type": ctype,
        "status": status,
        "validity": validity,
        "metrics": metrics,
        "findings": findings,
        "assumptions": assumptions,
        "flags": [],
        "usage_ratio": round(_finite_float(usage_ratio, 0.0), 6),
    }


def _not_evaluated(component_id, reason):
    return _result(
        component_id,
        str(component_id),
        "not_evaluated",
        {},
        [{"code": "NOT_EVALUATED", "severity": "info", "message": reason}],
        [reason],
        0.0,
        validity="not_evaluated",
    )


# Simply supported rectangular plate coefficients (Roark 11.4 case 1,
# uniform load, all edges simply supported; b/a aspect ratios).  The values
# are from the nu=0.3 tables; using them with nu=0.14 in D over-predicts the
# flexural stress by ~14% (conservative direction, disclosed in the
# assumption).  Beyond b/a = 2 the strip asymptote is used.
_PLATE_COEFFICIENTS = (
    (1.0, 0.00406, 0.2872),
    (1.2, 0.00564, 0.3660),
    (1.4, 0.00705, 0.4200),
    (1.5, 0.00773, 0.4600),
    (1.6, 0.00832, 0.4720),
    (1.8, 0.00921, 0.4870),
    (2.0, 0.01013, 0.4970),
)


def _plate_coefficients(aspect):
    """Interpolate (alpha_deflection, beta_stress) for a simply supported
    plate of aspect b/a >= 1 (Roark 11.4 case 1, nu = 0.3 tables)."""
    for index in range(len(_PLATE_COEFFICIENTS) - 1):
        ratio_a, alpha_a, beta_a = _PLATE_COEFFICIENTS[index]
        ratio_b, alpha_b, beta_b = _PLATE_COEFFICIENTS[index + 1]
        if ratio_a <= aspect <= ratio_b:
            fraction = (aspect - ratio_a) / (ratio_b - ratio_a)
            return alpha_a + fraction * (alpha_b - alpha_a), beta_a + fraction * (beta_b - beta_a)
    if aspect >= _PLATE_COEFFICIENTS[-1][0]:
        return _PLATE_COEFFICIENTS[-1][1], _PLATE_COEFFICIENTS[-1][2]
    return _PLATE_COEFFICIENTS[0][1], _PLATE_COEFFICIENTS[0][2]


def _analyze_pcb(spec, context):
    drop = _drop_summary(context)
    age_days = _age_days(context)
    width = _finite_float(spec.get("width_m"), 0.04)
    length = _finite_float(spec.get("length_m"), 0.06)
    thickness = _finite_float(spec.get("thickness_m"), 0.0016)
    component_mass = _finite_float(spec.get("component_mass_kg"), 0.002)
    board_density = _finite_float(spec.get("board_density_kg_m3"), 1850.0)
    allowable = _finite_float(spec.get("allowable_flex_stress_pa"), 65e6)
    joint_area = _finite_float(spec.get("solder_joint_area_m2"), 2e-7)
    joint_count = max(1, int(spec.get("solder_joint_count") or 200))
    solder_allowable = _finite_float(spec.get("solder_allowable_shear_pa"), 20e6)
    thermal_per_day = _finite_float(spec.get("thermal_cycles_per_day"), 1.0)
    delta_t = _finite_float(spec.get("delta_temperature_k"), 30.0)
    accel_g = drop["accel_g"]
    if accel_g <= 0.0 and age_days <= 0.0:
        return _not_evaluated("pcb", "no drop or age data to assess the PCB against")
    if thickness <= 0.0:
        return _not_evaluated("pcb", "board thickness must be positive")
    if allowable <= 0.0 or solder_allowable <= 0.0:
        return _not_evaluated("pcb", "board or solder allowable must be positive")
    # Plate bending under the drop shock of the FULL board inertia load: the
    # board carries its own mass plus the carried component mass (a bare
    # 40x60x1.6 mm FR-4 board is ~7 g — ignoring it under-predicts the
    # flexural stress several-fold).
    a = min(width, length)
    b = max(width, length)
    young = 22e9
    nu = 0.14
    flex = None
    deflection = None
    stress_ratio = 0.0
    if accel_g > 0.0 and a > 0.0 and b > 0.0:
        board_mass = board_density * a * b * thickness
        load = (board_mass + component_mass) * accel_g * 9.80665
        q = load / (a * b)
        d = young * thickness ** 3 / (12.0 * (1.0 - nu * nu))
        alpha, beta = _plate_coefficients(b / a)
        deflection = alpha * q * a ** 4 / d
        flex = beta * q * a ** 2 / (thickness ** 2)
        stress_ratio = flex / allowable if allowable > 0.0 else 0.0
    # Solder-joint mechanical shock shear (direct inertia; screening only —
    # the governing board-drop solder failure mode is board flexure pulling
    # the joints, which the flexural channel bounds indirectly).
    shock_shear = None
    shock_ratio = 0.0
    if accel_g > 0.0 and joint_area > 0.0:
        force = component_mass * accel_g * 9.80665
        shock_shear = force / (joint_count * joint_area)
        shock_ratio = shock_shear / solder_allowable if solder_allowable > 0.0 else 0.0
    # Solder-joint thermal fatigue (Coffin-Manson-class screening): the
    # reference is 20,000 cycles to failure at a 40 K daily thermal cycle
    # (published solder-joint thermal-fatigue data class), slope 2.5.
    thermal_cycles = thermal_per_day * age_days
    cycles_to_failure = 2e4 * (40.0 / max(delta_t, 1e-6)) ** 2.5
    thermal_damage = thermal_cycles / cycles_to_failure if cycles_to_failure > 0.0 else 0.0
    findings = []
    status = "pass"
    if (stress_ratio >= 1.0) or (shock_ratio >= 1.0) or (thermal_damage >= 1.2):
        status = "fail"
        if stress_ratio >= 1.0:
            findings.append(
                {"code": "PCB_FLEX_OVER_STRESS", "severity": "error", "message": "board flex stress exceeds the FR-4 flexural allowable"}
            )
        if shock_ratio >= 1.0:
            findings.append(
                {"code": "PCB_SOLDER_SHOCK_FAILURE", "severity": "error", "message": "solder-joint shock shear exceeds the joint allowable"}
            )
        if thermal_damage >= 1.2:
            findings.append(
                {"code": "PCB_SOLDER_THERMAL_FATIGUE", "severity": "error", "message": "thermal fatigue damage of the solder joints exceeds the screening life"}
            )
    elif (stress_ratio > 0.7) or (shock_ratio > 0.7) or (thermal_damage > 0.3):
        status = "warn"
        if thermal_damage > 0.3:
            findings.append(
                {"code": "PCB_SOLDER_THERMAL_WEAR", "severity": "warning", "message": "solder-joint thermal fatigue damage {:.2f} approaching the screening life".format(thermal_damage)}
            )
        if stress_ratio > 0.7:
            findings.append(
                {"code": "PCB_FLEX_MARGIN_LOW", "severity": "warning", "message": "board flex stress {:.0%} of the allowable".format(stress_ratio)}
            )
    elif thermal_damage >= 1.0:
        status = "warn"
        findings.append(
            {"code": "PCB_SOLDER_THERMAL_MARGINAL", "severity": "warning", "message": "thermal fatigue damage {:.2f} at the screening life boundary; within rating scatter".format(thermal_damage)}
        )
    usage_ratio = max(stress_ratio, shock_ratio, thermal_damage)
    metrics = {
        "max_deflection_m": round(deflection, 9) if deflection is not None else None,
        "flex_stress_pa": round(flex, 3) if flex is not None else None,
        "shock_shear_pa": round(shock_shear, 3) if shock_shear is not None else None,
        "thermal_damage": round(thermal_damage, 6),
        "solder_cycles_to_failure": round(cycles_to_failure, 3),
    }
    assumptions = [
        "board modeled as a simply supported plate under the FULL board inertia load (board + carried component mass) at peak drop acceleration (Roark/Timoshenko class formulas)",
        "plate coefficients interpolated by aspect ratio from the nu=0.3 tables; with nu=0.14 in D the stress is over-predicted ~14% (conservative)",
        "solder-joint thermal fatigue uses a Coffin-Manson-class power law (20,000 cycles at 40 K daily cycle, slope 2.5)",
        "solder shock channel is direct inertia shear only; board-flexure-driven joint strain is the governing board-drop mode and is bounded indirectly by the flexural channel",
    ]
    return _result("pcb", "pcb", status, metrics, findings, assumptions, usage_ratio)


def _analyze_battery(spec, context):
    drop = _drop_summary(context)
    mass = max(0.0, _finite_float(spec.get("mass_kg"), 0.02))
    crush = _finite_float(spec.get("crush_load_n"), 130.0)
    shock_limit = _finite_float(spec.get("shock_limit_g"), 500.0)
    temp_max = _finite_float(spec.get("temperature_max_k"), 333.15)
    accel_g = drop["accel_g"]
    env_temp = context.get("environment_temperature_k")
    env_temp = _finite_float(env_temp, None) if env_temp is not None else None
    if accel_g <= 0.0 and env_temp is None:
        return _not_evaluated("battery", "no drop or temperature data to assess the battery against")
    impact_force = mass * accel_g * 9.80665
    # The cell sits on the chassis, which attenuates the rigid-body shock;
    # a 0.5 transmission factor is a documented SCREENING assumption (the
    # rigid-mount bound would be ~1.0; the foam-mount bound lower).
    transmitted = 0.5 * impact_force
    crush_margin = transmitted / crush if crush > 0.0 else 1.0
    shock_margin = accel_g / shock_limit if shock_limit > 0.0 else 1.0
    findings = []
    status = "pass"
    if crush_margin >= 1.0 or shock_margin >= 1.0:
        status = "fail"
        if crush_margin >= 1.0:
            findings.append(
                {"code": "BATTERY_CRUSH_RISK", "severity": "error", "message": "transmitted drop force {:.1f} N exceeds the cell crush threshold {:.1f} N".format(transmitted, crush)}
            )
        if shock_margin >= 1.0:
            findings.append(
                {"code": "BATTERY_SHOCK_EXCEEDED", "severity": "error", "message": "peak drop acceleration {:.0f} g exceeds the cell shock limit {:.0f} g".format(accel_g, shock_limit)}
            )
    elif crush_margin > 0.8 or shock_margin > 0.8:
        status = "warn"
        findings.append(
            {"code": "BATTERY_SHOCK_MARGINAL", "severity": "warning", "message": "drop shock utilization {:.0%} of the screening limit; within class-data scatter".format(max(crush_margin, shock_margin))}
        )
    if env_temp is not None and env_temp > temp_max:
        findings.append(
            {"code": "BATTERY_TEMPERATURE_LIMIT", "severity": "warning", "message": "environment temperature {:.1f} K above the cell continuous limit {:.1f} K; thermal-runaway risk screening".format(env_temp, temp_max)}
        )
        if status == "pass":
            status = "warn"
    metrics = {
        "impact_force_n": round(impact_force, 3),
        "transmitted_force_n": round(transmitted, 3),
        "shock_g": round(accel_g, 3),
        "crush_margin": round(crush_margin, 6),
        "shock_margin": round(shock_margin, 6),
    }
    assumptions = [
        "chassis-to-cell force transmission factor 0.5 (screening; rigid-mount bound ~1.0, foam-mount lower)",
        "LiPo pouch cell crush threshold class ~130 N (published cell crush-test data class)",
        "shock limit 500 g class (published cell shock ratings ~50 g continuous, 500-1500 g shock); duration-blind screening",
        "crush channel loads the cell by its OWN inertia; the chassis-level force path is not separately modeled (screening)",
    ]
    return _result("battery", "battery", status, metrics, findings, assumptions, max(crush_margin, shock_margin))


def _analyze_switch(spec, context):
    usage = _usage_snapshot(context)
    actuation = int(_finite_float(usage.get("actuation_cycles"), 0.0))
    if actuation <= 0:
        return _not_evaluated("switch", "no actuation history to assess the switch against")
    switch_type = str(spec.get("switch_type") or "mechanical").lower()
    rated = _finite_float(
        spec.get("rated_cycles"),
        60e6 if switch_type == "optical" else 20e6,
    )
    usage_damage = actuation / rated if rated > 0.0 else 0.0
    # Button-stalk cantilever fatigue with stress concentration.
    force = _finite_float(spec.get("actuation_force_n"), 0.7)
    stalk_d = _finite_float(spec.get("button_stalk_diameter_m"), 0.0025)
    stalk_l = _finite_float(spec.get("button_stalk_length_m"), 0.005)
    k_f = 2.2
    stalk_stress = None
    stalk_cycles = None
    stalk_damage = 0.0
    if stalk_d > 0.0 and stalk_l > 0.0:
        stalk_stress = 32.0 * force * stalk_l / (math.pi * stalk_d ** 3) * k_f
        materials = context.get("materials") or {}
        sigma_ref = GENERIC_FATIGUE_STRENGTH_AT_1E6_PA
        exponent = GENERIC_FATIGUE_EXPONENT_K
        material_def = materials.get(spec.get("material")) or materials.get("ABS")
        props = getattr(material_def, "properties", None) if material_def is not None else None
        ref = getattr(props, "fatigue_strength_at_1e6_pa", None) if props is not None else None
        k = getattr(props, "fatigue_exponent_k", None) if props is not None else None
        fatigue_flagged = props is None or ref is None or k is None
        if ref is not None:
            sigma_ref = _finite_float(
                getattr(ref, "value_si", ref), GENERIC_FATIGUE_STRENGTH_AT_1E6_PA
            )
        if k is not None:
            exponent = _finite_float(
                getattr(k, "value_si", k), GENERIC_FATIGUE_EXPONENT_K
            )
        if stalk_stress > 0.0:
            stalk_cycles = 1e6 * (sigma_ref / stalk_stress) ** exponent
            stalk_damage = actuation / stalk_cycles if stalk_cycles > 0.0 else 0.0
    findings = []
    status = "pass"
    worst = max(usage_damage, stalk_damage)
    if worst >= 1.2:
        status = "fail"
        if usage_damage >= 1.2:
            findings.append(
                {"code": "SWITCH_RATED_LIFE_EXCEEDED", "severity": "error", "message": "actuation cycles exceed the switch class rating by more than rating scatter"}
            )
        if stalk_damage >= 1.2:
            findings.append(
                {"code": "SWITCH_STALK_FATIGUE", "severity": "error", "message": "button-stalk fatigue damage exceeds the screening life"}
            )
    elif worst >= 1.0:
        status = "warn"
        findings.append(
            {"code": "SWITCH_RATED_LIFE_MARGINAL", "severity": "warning", "message": "life consumption {:.0%} at the rating boundary; within rating scatter".format(worst)}
        )
    elif worst > 0.7:
        status = "warn"
        findings.append(
            {"code": "SWITCH_LIFE_MARGIN_LOW", "severity": "warning", "message": "switch life consumption {:.0%}".format(worst)}
        )
    metrics = {
        "usage_damage": round(usage_damage, 6),
        "stalk_stress_pa": round(stalk_stress, 3) if stalk_stress is not None else None,
        "stalk_cycles_to_failure": round(stalk_cycles, 3) if stalk_cycles is not None else None,
        "stalk_damage": round(stalk_damage, 6),
    }
    assumptions = [
        "switch class ratings: mechanical 20M (Omron D2FC class), optical 60M; ratings carry datasheet scatter so the fail band starts at 1.2x",
        "stalk root stress concentration K_f = 2.2 (Neuber relation 1 + q(K_t-1), K_t = 3 fillet class, notch sensitivity q = 0.6 for PC/ABS-class polymers)",
        "stalk fatigue law: ABS-class sigma_ref 14 MPa @ 1e6, slope 6 (polymer fatigue compilation class); generic fallback used and flagged when the material lacks fatigue data",
        "double-click reliability and debounce behavior are not modeled",
    ]
    if fatigue_flagged:
        findings.append(
            {"code": "SWITCH_FATIGUE_GENERIC_FALLBACK", "severity": "info", "message": "stalk fatigue uses the generic ABS-class law; material-specific fatigue data unavailable"}
        )
    return _result("switch", "switch", status, metrics, findings, assumptions, worst)


def _analyze_encoder(spec, context):
    usage = _usage_snapshot(context)
    steps = int(_finite_float(usage.get("scroll_encoder_rotations"), 0.0))
    if steps <= 0:
        return _not_evaluated("encoder", "no scroll history to assess the encoder against")
    encoder_type = str(spec.get("encoder_type") or "mechanical").lower()
    rated = _finite_float(
        spec.get("rated_rotations"),
        1e6 if encoder_type == "optical" else 25000.0,
    )
    detents = _finite_float(spec.get("detents_per_revolution"), DETENTS_PER_REVOLUTION)
    if detents <= 0.0:
        return _not_evaluated("encoder", "detents per revolution must be positive")
    rotations = steps / detents if detents > 0.0 else 0.0
    damage = rotations / rated if rated > 0.0 else 0.0
    findings = []
    status = "pass"
    if damage >= 1.2:
        status = "fail"
        findings.append(
            {"code": "ENCODER_RATED_LIFE_EXCEEDED", "severity": "error", "message": "scroll usage exceeds the encoder class rotation rating by more than rating scatter"}
        )
    elif damage >= 1.0:
        status = "warn"
        findings.append(
            {"code": "ENCODER_RATED_LIFE_MARGINAL", "severity": "warning", "message": "encoder life consumption {:.0%} at the rating boundary; within rating scatter".format(damage)}
        )
    elif damage > 0.7:
        status = "warn"
        findings.append(
            {"code": "ENCODER_LIFE_MARGIN_LOW", "severity": "warning", "message": "encoder life consumption {:.0%}".format(damage)}
        )
    metrics = {
        "usage_rotations": round(rotations, 3),
        "usage_damage": round(damage, 6),
        "remaining_rotations": round(max(0.0, rated - rotations), 3),
    }
    assumptions = [
        "scroll usage counts wheel steps (detents); encoder ratings are in revolutions ({} detents/revolution conversion)".format(int(detents)),
        "encoder class rotation ratings: mechanical 25,000 (ALPS EC11 class, 15k-30k band), optical 1e6; ratings carry scatter so the fail band starts at 1.2x",
        "detent wear, debris/scroll-jump, and wheel axle friction are not separately modeled (dominant field modes are not covered)",
        "free-spin / hyper-scroll wheel revolutions are not counted (undercount for free-scroll-heavy users)",
    ]
    return _result("encoder", "encoder", status, metrics, findings, assumptions, damage)


_ANALYZERS = {
    "pcb": _analyze_pcb,
    "battery": _analyze_battery,
    "switch": _analyze_switch,
    "encoder": _analyze_encoder,
}


def analyze(spec, context):
    """Assess one component spec against the context."""
    if not isinstance(spec, dict):
        spec = {}
    ctype = str(spec.get("type") or "unknown").strip().lower()
    analyzer = _ANALYZERS.get(ctype)
    component_id = spec.get("component_id") or ctype
    if analyzer is None:
        return _not_evaluated(component_id, "unknown component type {!r}".format(ctype))
    merged = dict(defaults(ctype))
    merged.update({key: value for key, value in spec.items() if key not in ("type", "component_id")})
    try:
        result = analyzer(merged, context if isinstance(context, dict) else {})
        result["component_id"] = str(component_id)
        return result
    except Exception as exc:
        return _not_evaluated(component_id, "component analysis failed: {}".format(exc))


def analyze_many(specs, context):
    """Assess a list of component specs; never raises."""
    results = []
    for spec in specs or ():
        try:
            results.append(analyze(spec, context))
        except Exception as exc:
            results.append(
                _not_evaluated(
                    (spec or {}).get("component_id", "unknown"),
                    "component analysis failed: {}".format(exc),
                )
            )
    return results
