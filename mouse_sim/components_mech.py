"""Closed-form mechanical component failure models for a gaming mouse.

Screws (boss pull-out, vibration loosening), snap-fit clips (engagement,
retention, creep), internal mounts (drop compression, Euler buckling), and
adhesive joints (thermal-mismatch + impact shear with aging).  Deterministic
SCREENING laws with honest validity/assumption disclosure; never raise on
bad input.

Screening status vocabulary: ``validity`` is always ``"approximate"`` (the
constants are class-level engineering data, not calibration measurements);
``status`` uses marginal bands so knife-edge margins report ``warn`` with a
``..._MARGINAL`` finding instead of a hard ``fail``.

Context contract (all keys optional):
    mass_kg, inertia_kg_m2, support, materials,
    drop: {peak_accel_g | peak_acceleration_g, peak_force_n, ...} | None,
    lifecycle: usage snapshot dict OR {"usage": {...}, ...} | None,
    environment_temperature_k | None.
"""

import math

COMPONENT_TYPES = ("screw", "clip", "mount", "adhesive")

# Material constants used by the screening models.  These are hardcoded
# ABS-class values (the reference shell material); the models do NOT read
# the component material field — the assumption is disclosed in each model.
_ABS_YIELD_PA = 40e6
_ABS_COMPRESSIVE_PA = 60e6
_ABS_MODULUS_PA = 2.0e9


def defaults(component_type):
    if component_type == "screw":
        return {
            "thread_diameter_m": 0.002,
            "engagement_length_m": 0.003,
            "boss_material": "ABS",
            "preload_n": 15.0,
            "screw_count": 4,
            "supported_mass_kg": 0.05,
            "transport_vibration_g_rms": 3.0,
        }
    if component_type == "clip":
        return {
            "beam_length_m": 0.008,
            "beam_width_m": 0.003,
            "beam_thickness_m": 0.001,
            "engagement_depth_m": 0.0006,
            "material": "ABS",
            "friction": 0.6,
            # Release ramp 50 deg: typical side-button clip class; retention is
            # highly sensitive to this angle near the self-locking pole.
            "release_angle_deg": 50.0,
            "disassembly_force_n": 5.0,
        }
    if component_type == "mount":
        return {
            "column_diameter_m": 0.0025,
            "column_height_m": 0.004,
            "column_count": 4,
            "supported_mass_kg": 0.02,
            "material": "ABS",
        }
    if component_type == "adhesive":
        return {
            "joint_type": "lap",
            "area_m2": 4e-4,
            "adhesive": "acrylic_foam",
            "thickness_m": 0.0005,
            "adhered_mass_kg": 0.02,
            "alpha_part_per_k": 20e-6,
            "alpha_substrate_per_k": 80e-6,
            "delta_temperature_k": 40,
            "exposed": False,
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


def _accel_g(context):
    drop = context.get("drop")
    if not isinstance(drop, dict):
        return 0.0
    value = drop.get("peak_accel_g")
    if value is None:
        value = drop.get("peak_acceleration_g")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0.0 else 0.0


def _finite(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


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
        "usage_ratio": round(_finite(usage_ratio, 0.0), 6),
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


def _analyze_screw(spec, context):
    d = _finite(spec.get("thread_diameter_m"), 0.002)
    engagement = _finite(spec.get("engagement_length_m"), 0.003)
    preload = max(0.0, _finite(spec.get("preload_n"), 15.0))
    raw_count = spec.get("screw_count")
    count = max(1, int(raw_count) if raw_count else 4)
    supported = max(0.0, _finite(spec.get("supported_mass_kg"), 0.05))
    vibration_g = max(0.0, _finite(spec.get("transport_vibration_g_rms"), 3.0))
    accel_g = _accel_g(context)
    if d <= 0.0 or engagement <= 0.0:
        return _not_evaluated("screw", "screw geometry must be positive")
    # Plastic boss shear-out screening: tau = 0.2 * S_y (ABS S_y = 40 MPa,
    # hardcoded ABS-class value; the boss material field is not read).
    tau = 0.2 * _ABS_YIELD_PA
    pullout = math.pi * d * engagement * tau
    added = supported * max(accel_g, vibration_g) * 9.80665 / count
    total_load = preload + added
    findings = []
    status = "pass"
    margin = None
    if total_load <= 0.0:
        # Unloaded joint: nothing to fail; report margin as None.
        findings.append(
            {"code": "SCREW_UNLOADED", "severity": "info", "message": "screw joint carries no load"}
        )
    else:
        margin = pullout / total_load
        if margin < 1.0:
            status = "fail"
            findings.append(
                {"code": "SCREW_PULLOUT_RISK", "severity": "error", "message": "applied load exceeds the boss pull-out force (margin {:.2f})".format(margin)}
            )
        elif margin < 1.2:
            status = "warn"
            findings.append(
                {"code": "SCREW_PULLOUT_MARGINAL", "severity": "warning", "message": "pull-out margin {:.2f} within 20% of the screening threshold".format(margin)}
            )
    preload_fraction = preload / pullout if pullout > 0.0 else 1.0
    # Junker-class self-loosening screening: transverse vibration load above
    # the friction capacity (mu_s * preload) of the joint risks self-loosening.
    transverse = supported * vibration_g * 9.80665 / count
    loosening_preload = transverse / 0.15
    if preload < loosening_preload:
        findings.append(
            {"code": "SCREW_LOOSENING_RISK", "severity": "warning", "message": "preload {:.1f} N below the {:.1f} N friction capacity threshold; vibration self-loosening risk (Junker class)".format(preload, loosening_preload)}
        )
        if status == "pass":
            status = "warn"
    metrics = {
        "pull_out_force_n": round(pullout, 3),
        "added_load_n": round(added, 4),
        "margin": round(margin, 4) if margin is not None else None,
        "preload_fraction": round(preload_fraction, 4),
    }
    assumptions = [
        "boss pull-out tau = 0.2 * S_y with ABS-class S_y = 40 MPa hardcoded (plastic thread shear-out screening; the boss material field is not read)",
        "bolt load = preload + applied load (screening superposition; for a stiff screw in a soft plastic boss the load-sharing factor is ~0.9, so the error is <= 25% and conservative)",
        "preload relaxation from boss creep over life is NOT modeled (unconservative direction; 15 N default is 6x the loosening threshold)",
        "vibration loosening screening: preload below the friction capacity (mu_s = 0.15) of the transverse load risks Junker-class self-loosening",
        "3 g rms transport vibration is a severe screening value (rough-transport class), not a named ISTA class",
    ]
    return _result("screw", "screw", status, metrics, findings, assumptions, 1.0 / margin if margin and margin > 0.0 else 0.0)


def _creep_modulus_factor(age_days):
    """ABS creep-modulus retention (ISO 899-1 class data at ~5-10 MPa test
    stress): 0.62 @ 1000 h, 0.55 @ 10000 h, 0.5 @ 5 years; log-time
    interpolation.  The screening beam runs at ~70% of yield, where real
    retention is LOWER, so the factor is optimistic, not conservative."""
    if age_days <= 0.0:
        return 1.0
    anchors = ((41.7, 0.62), (417.0, 0.55), (1825.0, 0.50))
    if age_days >= anchors[-1][0]:
        return 0.50
    if age_days <= anchors[0][0]:
        return 1.0 - (1.0 - 0.62) * (math.log10(age_days + 1.0) / math.log10(42.7))
    for (t1, f1), (t2, f2) in zip(anchors, anchors[1:]):
        if t1 <= age_days < t2:
            frac = (math.log10(age_days) - math.log10(t1)) / (math.log10(t2) - math.log10(t1))
            return f1 + frac * (f2 - f1)
    return 0.50


def _analyze_clip(spec, context):
    usage = _usage_snapshot(context)
    age_days = _finite(usage.get("age_days"), 0.0)
    length = _finite(spec.get("beam_length_m"), 0.008)
    width = _finite(spec.get("beam_width_m"), 0.003)
    thickness = _finite(spec.get("beam_thickness_m"), 0.001)
    depth = _finite(spec.get("engagement_depth_m"), 0.0006)
    friction = _finite(spec.get("friction"), 0.6)
    release_angle = math.radians(_finite(spec.get("release_angle_deg"), 50.0))
    disassembly = _finite(spec.get("disassembly_force_n"), 5.0)
    if length <= 0.0 or thickness <= 0.0:
        return _not_evaluated("clip", "clip beam geometry must be positive")
    if disassembly <= 0.0:
        return _not_evaluated("clip", "disassembly force must be positive")
    young = _ABS_MODULUS_PA
    yield_pa = _ABS_YIELD_PA
    stiffness = young * width * thickness ** 3 / (4.0 * length ** 3)
    engagement_force = stiffness * depth
    bend_stress = 3.0 * young * thickness * depth / (2.0 * length ** 2)
    # Snap-fit retention over the release ramp: F_ret = F_engage * (mu + tan a)
    # / (1 - mu * tan a) with the release angle a (standard snap-fit formula).
    tan_angle = math.tan(release_angle)
    denominator = 1.0 - friction * tan_angle
    if denominator > 1e-6:
        retention = engagement_force * (friction + tan_angle) / denominator
    else:
        # Self-locking ramp (mu * tan(a) >= 1): not releasable by pulling.
        # The retention is material-limited, not geometry-limited; use a
        # documented material-limit proxy instead of the divergent formula.
        retention = engagement_force * 10.0
    creep = _creep_modulus_factor(age_days)
    derated_retention = retention * creep
    findings = []
    status = "pass"
    if bend_stress > 0.8 * yield_pa:
        status = "fail"
        findings.append(
            {"code": "CLIP_OVER_STRESS", "severity": "error", "message": "snap-fit bend stress exceeds 80% of the yield strength (assembly strain limit)"}
        )
    if derated_retention < disassembly:
        status = "fail"
        findings.append(
            {"code": "CLIP_RETENTION_LOST", "severity": "error", "message": "creep-derated retention {:.1f} N below the disassembly force {:.1f} N".format(derated_retention, disassembly)}
        )
    elif derated_retention < 1.5 * disassembly:
        status = "warn"
        findings.append(
            {"code": "CLIP_RETENTION_MARGIN_LOW", "severity": "warning", "message": "creep-derated retention {:.1f} N below 1.5x the disassembly force {:.1f} N".format(derated_retention, disassembly)}
        )
    if denominator <= 1e-6:
        findings.append(
            {"code": "CLIP_SELF_LOCKING", "severity": "info", "message": "release ramp is self-locking (mu*tan(a) >= 1); retention is material-limited, not geometry-limited"}
        )
    metrics = {
        "stiffness_n_per_m": round(stiffness, 3),
        "engagement_force_n": round(engagement_force, 3),
        "bend_stress_pa": round(bend_stress, 3),
        "retention_force_n": round(retention, 3),
        "creep_modulus_factor": round(creep, 4),
        "derated_retention_force_n": round(derated_retention, 3),
    }
    assumptions = [
        "cantilever snap-fit engagement (constant-section beam)",
        "retention over the release ramp: F_ret = F_engage * (mu + tan a) / (1 - mu * tan a)",
        "ABS-class modulus 2.0 GPa and yield 40 MPa hardcoded; the material field is not read",
        "ABS creep-modulus retention class data (ISO 899-1, ~5-10 MPa test stress): 0.62 @ 1000 h, 0.55 @ 10000 h, 0.5 @ 5 years; the screening beam runs near yield where real retention is lower (optimistic)",
        "friction mu = 0.6 (dry ABS-on-ABS class 0.4-0.6); retention is highly sensitive to mu near the self-locking pole at arctan(1/mu)",
        "self-locking ramps (mu*tan(a) >= 1) use a material-limit retention proxy (10x engagement force)",
    ]
    return _result("clip", "clip", status, metrics, findings, assumptions, disassembly / derated_retention if derated_retention > 0.0 else 1.0)


def _analyze_mount(spec, context):
    accel_g = _accel_g(context)
    d = _finite(spec.get("column_diameter_m"), 0.0025)
    height = _finite(spec.get("column_height_m"), 0.004)
    raw_count = spec.get("column_count")
    count = max(1, int(raw_count) if raw_count else 4)
    supported = max(0.0, _finite(spec.get("supported_mass_kg"), 0.02))
    if accel_g <= 0.0:
        return _not_evaluated("mount", "no drop data to assess the mount against")
    if d <= 0.0 or height <= 0.0:
        return _not_evaluated("mount", "mount column geometry must be positive")
    young = _ABS_MODULUS_PA
    compressive_allowable = _ABS_COMPRESSIVE_PA
    force = supported * accel_g * 9.80665 / count
    area = math.pi * d ** 2 / 4.0
    stress = force / area
    # Eccentric-loading derate: published boss/eccentric-column data fail at
    # 50-70% of pure compression; 0.6 is the mid-band screening value.
    eccentric_allowable = 0.6 * compressive_allowable
    findings = []
    status = "pass"
    stress_ratio = stress / eccentric_allowable if eccentric_allowable > 0.0 else 0.0
    if stress_ratio >= 1.0:
        status = "fail"
        findings.append(
            {"code": "MOUNT_CRUSH", "severity": "error", "message": "drop compression stress {:.1f} MPa exceeds the eccentric-compression allowable {:.1f} MPa".format(stress / 1e6, eccentric_allowable / 1e6)}
        )
    elif stress_ratio > 0.8:
        status = "warn"
        findings.append(
            {"code": "MOUNT_CRUSH_MARGINAL", "severity": "warning", "message": "compression utilization {:.0%} within 20% of the screening threshold".format(stress_ratio)}
        )
    # Euler buckling is only valid above the transition slenderness; a short
    # stub column (L/d < ~2.3, lambda_eff < pi*sqrt(E/sigma_cr)) crushes
    # before it can buckle, so the Euler check is skipped there.
    radius_of_gyration = d / 4.0
    slenderness = 2.0 * height / radius_of_gyration if radius_of_gyration > 0.0 else 0.0
    transition = math.pi * math.sqrt(young / max(eccentric_allowable, 1e-6))
    buckling_margin = None
    buckling = None
    if slenderness >= transition:
        inertia = math.pi * d ** 4 / 64.0
        buckling = math.pi ** 2 * young * inertia / (4.0 * height ** 2)
        buckling_margin = buckling / force if force > 0.0 else 0.0
        if buckling_margin < 2.0 and status == "pass":
            status = "warn"
            findings.append(
                {"code": "MOUNT_BUCKLING_MARGIN_LOW", "severity": "warning", "message": "Euler buckling margin {:.2f} below 2".format(buckling_margin)}
            )
    metrics = {
        "impact_load_n": round(force, 3),
        "compression_stress_pa": round(stress, 3),
        "eccentric_allowable_pa": round(eccentric_allowable, 3),
        "buckling_load_n": round(buckling, 3) if buckling is not None else None,
        "buckling_margin": round(buckling_margin, 4) if buckling_margin is not None else None,
    }
    assumptions = [
        "compression load split evenly across the columns; drop accel applied at full chassis level (no transmission derate)",
        "eccentric-compression derate 0.6 of the ABS-class 60 MPa compressive allowable (published 50-70% band for offset-loaded bosses)",
        "Euler buckling checked only above the transition slenderness (short stub columns crush before buckling)",
        "ABS-class modulus 2.0 GPa hardcoded; the material field is not read",
    ]
    return _result("mount", "mount", status, metrics, findings, assumptions, stress_ratio)


_ADHESIVE_DATA = {
    # Acrylic foam tape (VHB-class): static shear ~0.3-0.4 MPa (3M VHB data).
    # Static allowables are conservative for millisecond-scale drops (foam
    # tapes rate-stiffen); the governing failure mode (peel/cleavage) is NOT
    # modeled and can fail a joint below the shear criterion.
    "acrylic_foam": {"young_pa": 0.5e6, "allowable_pa": 0.3e6},
    "cyanoacrylate": {"young_pa": 1e9, "allowable_pa": 10e6},
    "silicone": {"young_pa": 1e6, "allowable_pa": 0.5e6},
}


def _analyze_adhesive(spec, context):
    usage = _usage_snapshot(context)
    age_days = _finite(usage.get("age_days"), 0.0)
    area = _finite(spec.get("area_m2"), 4e-4)
    adhesive = str(spec.get("adhesive") or "acrylic_foam").lower()
    data = _ADHESIVE_DATA.get(adhesive, _ADHESIVE_DATA["acrylic_foam"])
    young = data["young_pa"]
    allowable = data["allowable_pa"]
    mass = max(0.0, _finite(spec.get("adhered_mass_kg"), 0.02))
    alpha_part = _finite(spec.get("alpha_part_per_k"), 20e-6)
    alpha_sub = _finite(spec.get("alpha_substrate_per_k"), 80e-6)
    delta_t = _finite(spec.get("delta_temperature_k"), 40.0)
    accel_g = _accel_g(context)
    env_temp = context.get("environment_temperature_k")
    if accel_g <= 0.0 and env_temp is None:
        return _not_evaluated("adhesive", "no drop or temperature data to assess the adhesive joint against")
    if area <= 0.0:
        return _not_evaluated("adhesive", "joint area must be positive")
    thermal_shear = young * abs(alpha_part - alpha_sub) * delta_t / 2.0
    impact_shear = mass * accel_g * 9.80665 / area if area > 0.0 else 0.0
    # UV/humidity aging derate applies to EXPOSED joints only; internal
    # joints (battery-to-chassis) are not UV-aged.  The derate is a step at
    # 365 days (screening simplification).
    exposed = bool(spec.get("exposed", False))
    aging = 0.5 if exposed and age_days > 365.0 else 1.0
    effective_allowable = allowable * aging
    utilization = math.sqrt(thermal_shear ** 2 + impact_shear ** 2) / effective_allowable if effective_allowable > 0.0 else 0.0
    findings = []
    status = "pass"
    if utilization >= 1.0:
        status = "fail"
        findings.append(
            {"code": "ADHESIVE_OVER_UTILIZATION", "severity": "error", "message": "combined joint utilization {:.2f} exceeds the (aged) allowable".format(utilization)}
        )
    elif utilization > 0.7:
        status = "warn"
        findings.append(
            {"code": "ADHESIVE_UTILIZATION_HIGH", "severity": "warning", "message": "combined joint utilization {:.2f} above 0.7".format(utilization)}
        )
    metrics = {
        "thermal_shear_pa": round(thermal_shear, 3),
        "impact_shear_pa": round(impact_shear, 3),
        "allowable_pa": round(effective_allowable, 3),
        "aging_factor": aging,
        "utilization": round(utilization, 4),
    }
    assumptions = [
        "thermal-mismatch shear = E * d(alpha) * dT / 2 (screening simplification; foam-tape thermal term is negligible by design)",
        "default d(alpha) = 60 ppm/K (battery-on-ABS class); the thermal term matters only for stiff adhesives (cyanoacrylate)",
        "static shear allowables used for drop loading: conservative for rate-stiffening foam tapes; PEEL/CLEAVAGE failure mode is NOT modeled and can fail a joint below the shear criterion",
        "aging derate 0.5 beyond 365 days applies to exposed joints only (UV/humidity class); internal joints are not UV-aged",
    ]
    return _result("adhesive", "adhesive", status, metrics, findings, assumptions, utilization)


_ANALYZERS = {
    "screw": _analyze_screw,
    "clip": _analyze_clip,
    "mount": _analyze_mount,
    "adhesive": _analyze_adhesive,
}


def analyze(spec, context):
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
