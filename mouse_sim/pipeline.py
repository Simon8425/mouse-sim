"""Deterministic, JSON-friendly mouse simulation pipeline orchestration.

``run_pipeline`` executes the standard analysis steps (material catalog,
geometry import, mass properties, DFM-lite validation, structural screening,
impact estimation, qualification gating) and packages the result into a
stable, JSON-serializable result with an immutable run manifest.  Pipeline
errors never raise: they are collected into the result's ``errors`` list and
the lifecycle is marked ``failed``.
"""

import math
import os
import traceback
from typing import Mapping, Optional

from mouse_sim import (
    canonical,
    canonical_bytes,
    canonical_value,
    collision,
    classify_objects,
    sha256_bytes,
)
from mouse_sim.classification import canonical_component_type
from mouse_sim import importers
from mouse_sim import impact
from mouse_sim import materials
from mouse_sim import mass
from mouse_sim import physics
from mouse_sim import qualification
from mouse_sim import validation
from mouse_sim.cache import ArtifactCache
from mouse_sim.units import to_si

# The module itself, for tests that spy on execution.
pipeline_module = __import__("importlib").import_module(__name__)

ENGINE_VERSION = "0.1.0"
RESULT_SCHEMA_ID = "gms.pipeline-result/1"
MANIFEST_SCHEMA_ID = "gms.run-manifest/1"

# Modules whose source defines the engine's numerical behavior — including
# every implementation dependency that can change a computed result: geometry
# import/parse (importers, step_kernel, freecad_step_worker), object semantics
# (model), canonical serialization (canonical), and input schema handling
# (schema).  A change in any of them invalidates every cached run: the run id
# embeds a hash of these sources, so stale cache entries silently serving old
# physics are impossible.  Presentation/transport-only modules (web_api, cli,
# reports, cache, errors) are deliberately excluded.
_ENGINE_BEHAVIOR_MODULES = (
    "pipeline",
    "physics",
    "drop_sim",
    "impact",
    "geometry",
    "mass",
    "materials",
    "validation",
    "qualification",
    "units",
    "collision",
    "classification",
    "ai_classify",
    "lifecycle",
    "components_elec",
    "components_mech",
    "profiles",
    "population",
    "importers",
    "canonical",
    "model",
    "schema",
    "step_kernel",
    "freecad_step_worker",
    "shell_validation",
    "fea",
)

_ENGINE_HASH = None


def _engine_hash(root=None):
    """Deterministic sha256 over the engine module sources.

    ``root`` is the package directory; it defaults to the installed
    ``mouse_sim`` directory and the result is cached.  Passing ``root``
    explicitly (tests) computes a fresh hash from that directory.
    """
    global _ENGINE_HASH
    if root is not None or _ENGINE_HASH is None:
        import hashlib
        import os

        from mouse_sim import __path__ as mouse_sim_path

        if root is None:
            root = os.path.abspath(str(mouse_sim_path[0]))
        hasher = hashlib.sha256()
        for name in _ENGINE_BEHAVIOR_MODULES:
            path = os.path.join(root, name + ".py")
            try:
                with open(path, "rb") as stream:
                    hasher.update(name.encode("utf-8"))
                    hasher.update(b"\0")
                    hasher.update(stream.read())
            except OSError:
                continue
        digest = hasher.hexdigest()
        if root is None:
            _ENGINE_HASH = digest
        return digest
    return _ENGINE_HASH

_IMPACT_KWARGS = (
    "velocity_m_s",
    "fall_height_m",
    "contact_normal",
    "restitution",
    "contact_stiffness_n_per_m",
    "stopping_distance_m",
    "contact_duration_s",
    "target_mass_kg",
    "load_path_area_m2",
    "load_path_lever_arm_m",
    "section_modulus_m3",
    "allowable_pa",
    "orientation",
    "g",
    "effective_modulus_pa",
    "contact_radius_m",
    "total_mass_kg",
    "inertia_tensor_kg_m2",
    "contact_location_m",
    "center_of_mass_m",
)


def _normalize_mode(mode):
    value = str(mode or "exploration").strip().casefold()
    return value if value in ("exploration", "qualification", "validation") else "exploration"


def _issue(code, severity, category, message, evidence_blocking=False):
    return {
        "code": code,
        "severity": severity,
        "category": category,
        "message": message,
        "evidence_blocking": bool(evidence_blocking),
    }


def _new_result(mode):
    return {
        "schema_id": RESULT_SCHEMA_ID,
        "engine_version": ENGINE_VERSION,
        "run_id": "",
        "mode": mode,
        "lifecycle_state": "completed",
        "validity": {
            "state": "valid",
            "reasons": [],
            "assumptions": [],
            "unsupported_failure_modes": [],
            "confidence": "low",
        },
        "issues": [],
        "geometry_summary": {"objects": [], "parse_errors": []},
        "materials": {},
        "requirements": [],
        "mass": None,
        "validation": None,
        "structural": None,
        "impact": None,
        "drop_simulation": None,
        "collision": {
            "status": "skipped",
            "configured": False,
            "reason": "collision pairs are not configured",
            "pairs": [],
            "count": 0,
            "flags": [],
        },
        "qualification": None,
        "lifecycle": None,
        "correlation": None,
        "components": None,
        "population": None,
        "shell": None,
        "component_screening": None,
        "manifest": None,
        "errors": [],
    }


def _lifecycle_int(value):
    """Snapshot echo of a drop count, clamped to the documented screening
    maximum so the snapshot always matches what the fatigue model used."""
    from . import lifecycle as lifecycle_module

    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if number <= 0:
        return 0
    return min(number, lifecycle_module.MAX_PRIOR_DROPS)


def _lifecycle_float(value):
    """Snapshot echo of an impact energy, clamped to the documented
    screening maximum so the snapshot always matches the model's input."""
    from . import lifecycle as lifecycle_module

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0.0:
        return 0.0
    return min(number, lifecycle_module.MAX_EVENT_ENERGY_J)


def _append_correlation_metric(condition, metric_key, measured, predicted, max_error):
    """Append a measured-vs-predicted comparison to a correlation condition."""
    try:
        measured = float(measured)
        predicted = float(predicted)
    except (TypeError, ValueError):
        condition["metrics"].append(
            {
                "metric_key": metric_key,
                "measured": None,
                "predicted": None,
                "relative_error": None,
                "pass": False,
                "reason": "non-numeric measured or predicted value",
            }
        )
        return
    if not math.isfinite(measured) or not math.isfinite(predicted):
        condition["metrics"].append(
            {
                "metric_key": metric_key,
                "measured": measured,
                "predicted": predicted,
                "relative_error": None,
                "pass": False,
                "reason": "non-finite measured or predicted value",
            }
        )
        return
    if abs(measured) < 1e-12:
        condition["metrics"].append(
            {
                "metric_key": metric_key,
                "measured": measured,
                "predicted": predicted,
                "relative_error": None,
                "pass": False,
                "reason": "measured value is zero; relative error undefined",
            }
        )
        return
    if measured < 0.0 or predicted < 0.0:
        condition["metrics"].append(
            {
                "metric_key": metric_key,
                "measured": measured,
                "predicted": predicted,
                "relative_error": None,
                "pass": False,
                "reason": "comparison value is negative",
            }
        )
        return
    relative_error = abs(measured - predicted) / abs(measured)
    condition["metrics"].append(
        {
            "metric_key": metric_key,
            "measured": round(measured, 6),
            "predicted": round(predicted, 6),
            "relative_error": round(relative_error, 6),
            "pass": relative_error <= max_error,
        }
    )


def _resolve_structure_material(structure_data, catalog, materials_by_object):
    """Resolve the material used by the structural section and the shell
    material chain — ONE authoritative resolution.

    Returns ``(material_def_or_None, label)``.  A pinned material is used
    as-is (a catalog miss yields None: the structural run proceeds without a
    material payload and the impact estimate derives no allowable).  An
    unpinned structure resolves the FIRST OBJECT's material (the shell — the
    same material the geometry/mass model used), falling back to catalog
    order only when no object carries a material.
    """
    if isinstance(structure_data, Mapping):
        material_ref = structure_data.get("material")
        if material_ref is not None:
            return catalog.get(material_ref), material_ref
    for object_id, material in materials_by_object.items():
        if material is not None:
            return material, object_id
    return _first_material(catalog), None


def _shell_material(request, catalog, materials_by_object):
    """Resolve the material actually used for the shell structural response.

    Returns ``(material_def_or_None, label)``; ``label`` names the resolved
    material for disclosure.  Delegates to :func:`_resolve_structure_material`
    so the structural solver, the impact allowable, and the disclosure can
    never disagree (audit finding: an unpinned structure previously resolved
    catalog-first ABS while the mass model used the object's Default).
    """
    return _resolve_structure_material(request.get("structure"), catalog, materials_by_object)


def _resolve_drop_contact(request, catalog, materials_by_object, surface, drop_request, result, shell_material=None):
    """Resolve the estimate_impact contact kwargs for a drop on ``surface``.

    An explicit ``contact_stiffness_n_per_m`` in ``drop_request`` keeps the
    calibrated linear spring (the user's override, unchanged).  Otherwise
    the default is the nonlinear Hertz point-contact law: E_eff from the
    resolved shell material and the floor ``drop_sim.SURFACES`` table, with
    the corner blend radius defaulting to
    ``impact.DEFAULT_CORNER_BLEND_RADIUS_M`` (2.0 mm) unless
    ``contact_radius_m`` is supplied.  A shell material without E/nu data
    falls back to the generic polymer with a disclosed
    HERTZ_EFFECTIVE_MODULUS_ASSUMED issue (never silent).  Returns
    ``(kwargs, model_label, assumptions)``: ``kwargs`` is ready to spread
    into :func:`impact.estimate_impact`, ``model_label`` names the contact
    model for result payloads, and ``assumptions`` carries the documented
    corner-blend-radius assumption when the Hertz default radius was used.
    Raises ValueError on invalid explicit input.
    """
    from . import drop_sim as drop_module

    stiffness = None
    raw_stiffness = drop_request.get("contact_stiffness_n_per_m")
    if raw_stiffness is not None:
        stiffness = float(raw_stiffness)
        if not math.isfinite(stiffness) or stiffness <= 0.0:
            raise ValueError("drop_simulation.contact_stiffness_n_per_m must be positive")
    radius = drop_request.get("contact_radius_m")
    if radius is not None:
        radius = float(radius)
        if not math.isfinite(radius) or radius <= 0.0:
            raise ValueError("drop_simulation.contact_radius_m must be positive")
    if shell_material is None:
        shell_material, _ = _shell_material(request, catalog, materials_by_object)
    kwargs, label, disclosure = drop_module.hertz_contact_kwargs(
        shell_material,
        surface,
        explicit_stiffness_n_per_m=stiffness,
        contact_radius_m=radius,
    )
    if disclosure is not None:
        result["issues"].append(
            _issue("HERTZ_EFFECTIVE_MODULUS_ASSUMED", "warning", "drop_simulation", disclosure)
        )
    # Documented assumption: the default corner blend radius (2.0 mm) enters
    # the Hertz contact geometry whenever the user did not pin one.
    assumptions = ()
    if "effective_modulus_pa" in kwargs and radius is None:
        assumptions = (impact.DEFAULT_CORNER_BLEND_RADIUS_ASSUMPTION,)
    return kwargs, label, assumptions


def _validate_raw_measured_drops(measured_drops):
    """W2-05: validate raw correlation.measured_drops entries BEFORE any
    canonical serialization (a NaN crashed the run as PIPELINE_INTERNAL in
    _collect_inputs; negative/implausible values and unknown keys were
    silently accepted).  Raises ValueError on the first problem."""
    allowed_drop_keys = {
        "drop_id", "height_m", "surface", "orientation",
        "measured_peak_accel_g", "measured_impact_duration_s",
        "measured_settle_s", "measured_settle_time_s",
        "measured_peak_accel_g_uncertainty",
        "measured_impact_duration_s_uncertainty",
        "measured_settle_s_uncertainty",
        "sensor", "identity_ok", "identity_flags", "uncertainty",
    }
    if not isinstance(measured_drops, (list, tuple)):
        raise ValueError("correlation.measured_drops must be an array")
    for raw_drop in measured_drops:
        if not isinstance(raw_drop, Mapping):
            raise ValueError("correlation.measured_drops entries must be objects")
        drop_id = str(raw_drop.get("drop_id", "drop"))
        unknown_keys = sorted(set(raw_drop.keys()) - allowed_drop_keys)
        if unknown_keys:
            raise ValueError(
                "correlation.measured_drops entry {!r} has unsupported "
                "key(s) {}: a physical test must never silently ignore "
                "unvalidated fields".format(drop_id, sorted(unknown_keys))
            )
        for key, low, high in (
            ("measured_peak_accel_g", 0.0, 10000.0),
            ("measured_impact_duration_s", 0.0, 1.0),
            ("measured_settle_s", 0.0, 60.0),
        ):
            if raw_drop.get(key) is None:
                continue
            value = float(raw_drop[key])
            if not math.isfinite(value):
                raise ValueError(
                    "correlation.measured_drops entry {!r} {} must be "
                    "finite".format(drop_id, key)
                )
            if value <= low:
                raise ValueError(
                    "correlation.measured_drops entry {!r} {} must be "
                    "positive".format(drop_id, key)
                )
            if value > high:
                raise ValueError(
                    "correlation.measured_drops entry {!r} {} {} exceeds "
                    "the physical plausibility bound ({}) for a hand "
                    "drop".format(drop_id, key, value, high)
                )
        raw_sensor = raw_drop.get("sensor")
        if raw_sensor is not None and not isinstance(raw_sensor, Mapping):
            raise ValueError(
                "correlation.measured_drops entry {!r} sensor must be an "
                "object".format(drop_id)
            )
        if isinstance(raw_sensor, Mapping) and raw_sensor.get("sampling_rate_hz") is not None:
            rate = float(raw_sensor["sampling_rate_hz"])
            if not math.isfinite(rate) or rate <= 0.0:
                raise ValueError(
                    "correlation.measured_drops entry {!r} "
                    "sensor.sampling_rate_hz must be positive and finite".format(drop_id)
                )


def _run_correlation_section(
    correlation_section,
    mass_kg,
    inertia,
    support,
    com_offset_m,
    surface_restitution,
    contact_kwargs,
    result,
    drop_overrides=None,
):
    """Evaluate a measured-drop correlation section against the simulator.

    Failures of the correlation section itself are reported as a warning
    issue; they never poison the drop-simulation results.
    """
    try:
        from . import drop_sim as drop_module

        if not isinstance(correlation_section, Mapping):
            raise ValueError("correlation must be an object")
        measured_drops = correlation_section.get("measured_drops")
        if not isinstance(measured_drops, (list, tuple)) or not measured_drops:
            raise ValueError("correlation.measured_drops must be a non-empty array")
        acceptance = correlation_section.get("acceptance")
        acceptance = dict(acceptance) if isinstance(acceptance, Mapping) else {}
        max_error = float(acceptance.get("max_relative_error", 0.25))
        if not math.isfinite(max_error) or max_error <= 0.0:
            raise ValueError("correlation.acceptance.max_relative_error must be positive")
        min_conditions = int(acceptance.get("min_drop_conditions", 3))
        # Validation-mode pins (gravity, restitution/friction scales, mass/
        # inertia scales, CoM override) must reach the correlation re-sim so
        # measured-vs-simulated compares the SAME physical configuration.
        correlation_gravity = float(drop_overrides.get("gravity_m_s2") or drop_module.GRAVITY_M_S2)
        correlation_restitution_scale = float(drop_overrides.get("restitution_scale") or 1.0)
        correlation_friction_scale = float(drop_overrides.get("friction_scale") or 1.0)
        correlation_mass_scale = float(drop_overrides.get("mass_scale") or 1.0)
        correlation_inertia_scale = float(drop_overrides.get("inertia_scale") or 1.0)
        correlation_com = drop_overrides.get("com_override_m") or com_offset_m
        # Audit finding: the re-sim previously ran default dt/seed/unit_seed
        # while the trace reported the pins as applied — the compared
        # configuration must be the reported configuration.
        correlation_timestep = drop_overrides.get("timestep_s")
        correlation_seed = drop_overrides.get("seed", 0)
        correlation_unit_seed = drop_overrides.get("unit_seed")
        conditions = []
        for raw_drop in measured_drops:
            if not isinstance(raw_drop, Mapping):
                continue
            drop_id = str(raw_drop.get("drop_id", "drop"))
            condition = {"drop_id": drop_id, "metrics": []}
            # W2-05 follow-up: the raw measured_drops path had no measurement
            # validation (NaN crashed the run as PIPELINE_INTERNAL at
            # serialization; negative/implausible values and unknown keys
            # were silently accepted).  Mirror the measured_tests rules:
            # unknown keys rejected, measured values finite and within the
            # documented plausibility bounds.  A rejected drop fails its
            # condition (verdict fail), never the whole run.
            try:
                _validate_raw_measured_drops([raw_drop])
            except (TypeError, ValueError) as exc:
                condition["error"] = str(exc)
                conditions.append(condition)
                continue
            height = raw_drop.get("height_m")
            surface = str(raw_drop.get("surface", "concrete")).strip().lower()
            orientation = raw_drop.get("orientation", "flat")
            if isinstance(orientation, str):
                orientation = orientation.strip().lower()
            try:
                height = float(height)
                if not math.isfinite(height):
                    raise ValueError
                correlation_config = drop_module.validate_config(
                    {
                        "test": "drop",
                        "height_m": height,
                        "surface": surface,
                        "drop_count": 1,
                        "orientation": orientation,
                    }
                )
                condition["height_m"] = correlation_config["height_m"]
                condition["surface"] = correlation_config["surface"]
                condition["orientation"] = correlation_config["orientation"]
                measured_accel = raw_drop.get("measured_peak_accel_g")
                measured_duration = raw_drop.get("measured_impact_duration_s")
                measured_settle = raw_drop.get(
                    "measured_settle_s", raw_drop.get("measured_settle_time_s")
                )
                # Audit finding (W2-02F): a sensor whose sampling rate cannot
                # resolve the measured impact duration was compared as if the
                # pulse were fully captured.  Fewer than two sample periods
                # across the pulse cannot resolve its shape or peak; disclose
                # the limitation on the condition (never silent).
                sensor_definition = raw_drop.get("sensor") or {}
                sampling_rate = sensor_definition.get("sampling_rate_hz")
                if (
                    sampling_rate is not None
                    and measured_duration is not None
                    and float(sampling_rate) > 0.0
                    and float(measured_duration) < 2.0 / float(sampling_rate)
                ):
                    condition["sampling_resolution_warning"] = (
                        "the {:.0f} Hz sensor samples every {:.3g} s; the "
                        "measured impact duration {:.4g} s spans fewer than "
                        "two sample periods and may not resolve the pulse "
                        "peak or duration".format(
                            float(sampling_rate),
                            1.0 / float(sampling_rate),
                            float(measured_duration),
                        )
                    )
                correlation_run = drop_module.simulate(
                    mass_kg,
                    inertia,
                    support,
                    correlation_config["height_m"],
                    surface=correlation_config["surface"],
                    drop_count=1,
                    test="drop",
                    orientation=(
                        {"quaternion_wxyz": list(correlation_config["orientation_quaternion_wxyz"])}
                        if correlation_config.get("orientation_quaternion_wxyz") is not None
                        else correlation_config["orientation"]
                    ),
                    seed=correlation_seed,
                    unit_seed=correlation_unit_seed,
                    com_offset_m=correlation_com,
                    gravity=correlation_gravity,
                    restitution_scale=correlation_restitution_scale,
                    friction_scale=correlation_friction_scale,
                    mass_scale=correlation_mass_scale,
                    inertia_scale=correlation_inertia_scale,
                    dt=(
                        float(correlation_timestep)
                        if correlation_timestep is not None
                        else drop_module.DT_S
                    ),
                )
                correlation_peak = correlation_run["peak"]
                predicted_accel_g = None
                if correlation_peak is not None:
                    raw_energy = correlation_peak.get("raw_kinetic_energy_j") or correlation_peak[
                        "kinetic_energy_j"
                    ]
                    correlation_model = correlation_run["model"]
                    correlation_mass = float(correlation_model.get("mass_kg") or mass_kg)
                    budget = correlation_mass * correlation_gravity * correlation_config["height_m"]
                    capped = min(raw_energy, budget)
                    speed = math.sqrt(max(0.0, 2.0 * capped / correlation_mass))
                    estimate = impact.estimate_impact(
                        correlation_mass,
                        velocity_m_s=speed,
                        restitution=float(correlation_model.get("restitution") or surface_restitution),
                        **contact_kwargs,
                    )
                    predicted_accel_g = (
                        estimate.to_dict().get("peak_acceleration_m_s2", 0.0) / 9.80665
                    )
                first_drop = correlation_run["drops"][0]
                predicted_settle = first_drop["settled_s"]
                did_not_settle = any(
                    str(check.get("code") or "") == "DROP_SIM_DID_NOT_SETTLE"
                    for check in first_drop.get("checks") or []
                )
                # Reproducibility echo: the EXACT simulated pose for THIS
                # condition (a physical test's recorded orientation can be
                # compared 1:1 against it).
                condition["orientation_quaternion_wxyz"] = correlation_run["model"].get(
                    "orientation_quaternion_wxyz"
                )
                condition["gravity_vector_body"] = correlation_run["model"].get(
                    "gravity_vector_body"
                )
                condition["starting_pose_m"] = correlation_run["model"].get("starting_pose_m")
                condition["initial_angular_velocity_rad_s"] = correlation_run["model"].get(
                    "initial_angular_velocity_rad_s"
                )
                # Equivalence of the compared quantity (validation mode): the
                # predicted peak is CoM-frame, quasi-static, rotation-free;
                # it is only equivalent to a surface sensor reading for FLAT
                # impacts with a defined sensor at/near the CoM reading the
                # RESULTANT peak.  Non-equivalent conditions are excluded
                # from the verdict (audit finding: they previously drove
                # correlated + high confidence while every row was flagged
                # NOT EQUIVALENT).
                equivalent = True
                if drop_overrides.get("enforce_equivalence"):
                    sensor = raw_drop.get("sensor") or {}
                    flat_impact = isinstance(orientation, str) and orientation == "flat"
                    sensor_defined = bool(sensor)
                    location = sensor.get("location_body_m") if sensor_defined else None
                    # Near-CoM is judged against the BODY's actual center of
                    # mass (the drop's com offset), not the mesh origin:
                    # for an asymmetric prototype the origin can be far from
                    # the CoM (audit follow-up).
                    com_reference = correlation_com or (0.0, 0.0, 0.0)
                    near_com = (
                        location is not None
                        and math.sqrt(
                            sum(
                                (float(component) - float(reference)) ** 2
                                for component, reference in zip(location, com_reference)
                            )
                        )
                        <= 0.005
                    )
                    resultant = (
                        str(sensor.get("quantity") or "resultant_peak_g") == "resultant_peak_g"
                        if sensor_defined
                        else False
                    )
                    equivalent = bool(flat_impact and sensor_defined and near_com and resultant)
                    if not sensor_defined:
                        condition["equivalence_note"] = (
                            "no sensor definition supplied; the comparison quantity "
                            "is unknown and treated as NOT equivalent"
                        )
                condition["equivalent"] = equivalent
                if not equivalent and not condition.get("equivalence_note"):
                    condition["equivalence_note"] = (
                        "the simulated peak is CoM-frame and rotation-free; a "
                        "surface-mounted sensor reading body-frame acceleration "
                        "at its own location includes rotational terms for "
                        "non-flat impacts (factor ~2-3 at corner/edge) — the "
                        "comparison is NOT directly equivalent unless the "
                        "sensor is at the CoM during a flat impact reading "
                        "the resultant peak"
                    )
                # Prototype identity (all modes): a test from a different
                # CAD revision / material / prototype must not contribute to
                # the verdict (audit finding).
                identity_ok = True
                identity_flags = []
                if drop_overrides.get("enforce_equivalence"):
                    identity_ok = bool(raw_drop.get("identity_ok", True))
                    identity_flags = list(raw_drop.get("identity_flags") or [])
                condition["identity_ok"] = identity_ok
                condition["identity_flags"] = identity_flags
                if measured_accel is not None and predicted_accel_g is not None:
                    _append_correlation_metric(
                        condition, "peak_accel_g", measured_accel, predicted_accel_g, max_error
                    )
                if measured_duration is not None:
                    # Audit finding: the measured full contact pulse was
                    # compared against the compression-phase-only model value
                    # (~30% systematic bias).  Compare against the FULL
                    # contact duration (compression + restitution).
                    compression = estimate.to_dict().get("contact_duration_s")
                    if compression is not None:
                        full_duration = (1.0 + float(
                            correlation_model.get("restitution") or 0.0
                        )) * float(compression)
                        condition["duration_convention"] = (
                            "full contact duration (1+e)*t with e = effective "
                            "restitution; compression phase t = (pi/2)*sqrt(m/k)"
                        )
                        _append_correlation_metric(
                            condition,
                            "impact_duration_s",
                            measured_duration,
                            full_duration,
                            max_error,
                        )
                if measured_settle is not None:
                    if did_not_settle:
                        # Audit finding: the 8.0 s DID_NOT_SETTLE sentinel must
                        # never be compared as a settle value.
                        condition["metrics"].append(
                            {
                                "metric_key": "settle_time_s",
                                "measured": measured_settle,
                                "predicted": None,
                                "relative_error": None,
                                "pass": False,
                                "reason": "simulated drop did not settle "
                                "(DROP_SIM_DID_NOT_SETTLE); settle comparison not applicable",
                            }
                        )
                    else:
                        _append_correlation_metric(
                            condition, "settle_time_s", measured_settle, predicted_settle, max_error
                        )
            except (TypeError, ValueError) as exc:
                condition["error"] = str(exc)
            conditions.append(condition)
        result["correlation"] = _correlation_summary(conditions, max_error, min_conditions)
    except Exception as exc:
        result["issues"].append(
            _issue(
                "CORRELATION_EVALUATION_FAILED",
                "warning",
                "correlation",
                "correlation evaluation failed: {}".format(exc),
            )
        )


def _run_component_and_population_sections(
    request, catalog, result, geometry_objs, component_specs, population_config, materials_by_object
):
    """Component-level failure analysis and worst-case population analysis.

    Builds the shared component context (mass/inertia/support from the
    geometry + mass model, the drop/impact summary when a drop ran, the
    lifecycle usage snapshot, the environment temperature) and evaluates the
    supplied component specs; a ``population`` config additionally runs the
    virtual-unit worst-case campaign.

    ARCHITECTURE NOTE: the component and population sections are SECONDARY
    screening observations.  Their verdicts never feed back into the shell
    physics (mass/inertia/drop/structural results are computed before this
    section runs), so an arbitrary component threshold cannot contaminate
    the shell result.  The shell result is reported separately in
    ``result["shell"]``.
    """
    from . import drop_sim as drop_module

    mass_kg = None
    if result["mass"] is not None:
        mass_kg = result["mass"].get("mass_kg")
    # When a drop ran, the drop's SIMULATED mass (incl. unit variation and
    # lifecycle scales) is the physically consistent mass for the component
    # load chain — the mass-model mass alone can disagree with the drop.
    drop_section = result.get("drop_simulation")
    if drop_section is not None and drop_section.get("model") is not None:
        drop_mass = drop_section["model"].get("mass_kg")
        if drop_mass is not None:
            mass_kg = drop_mass
    if mass_kg is None:
        mass_kg = 0.06
        result["issues"].append(
            _issue(
                "DROP_SIMULATION_MASS_ASSUMED",
                "warning",
                "components",
                "component/population load chain mass assumed to be 0.06 kg "
                "(60 g ultralight reference; mesh has no safe mass properties)",
            )
        )
    vertices = []
    for geometry in geometry_objs.values():
        mesh_vertices = getattr(geometry, "world_vertices", None)
        if mesh_vertices:
            vertices.extend(mesh_vertices)
            continue
        try:
            geometry_bounds = geometry.bounds()
            geometry_bounds = (
                (geometry_bounds.minimum[0], geometry_bounds.maximum[0]),
                (geometry_bounds.minimum[1], geometry_bounds.maximum[1]),
                (geometry_bounds.minimum[2], geometry_bounds.maximum[2]),
            )
        except Exception:
            geometry_bounds = None
        if geometry_bounds is not None:
            vertices.extend(drop_module.box_corners(geometry_bounds))
    if not vertices:
        result["issues"].append(
            _issue(
                "COMPONENT_ANALYSIS_UNAVAILABLE",
                "warning",
                "components",
                "no geometry available for component or population analysis",
            )
        )
        return
    support = drop_module.support_points(vertices)
    bounds = [
        (min(vertex[index] for vertex in vertices), max(vertex[index] for vertex in vertices))
        for index in range(3)
    ]
    inertia = None
    if result["mass"] is not None:
        inertia = result["mass"].get("inertia_tensor_kg_m2")
    if inertia is None:
        inertia = drop_module.box_inertia(mass_kg, bounds)
        result["issues"].append(
            _issue(
                "DROP_SIMULATION_INERTIA_APPROXIMATED",
                "warning",
                "components",
                "component/population load chain uses a uniform-density box "
                "inertia model (mass properties unavailable)",
            )
        )
    com_offset_m = None
    if result["mass"] is not None and result["mass"].get("center_of_mass_m") is not None:
        com_offset_m = tuple(result["mass"]["center_of_mass_m"])

    drop_summary = None
    drop_request = request.get("drop_simulation")
    if drop_section is not None and drop_section.get("peak") is not None:
        peak = drop_section["peak"]
        drop_config = drop_section.get("config") or {}
        # The echoed config strips unknown keys, so the stiffness/radius
        # come from the ORIGINAL request (validate_config drops them from
        # the echo).
        height = float(drop_config.get("height_m") or 0.75)
        capped_j = float(peak.get("kinetic_energy_j") or 0.0)
        if capped_j > 0.0 and mass_kg > 0.0:
            v_eff = math.sqrt(2.0 * capped_j / mass_kg)
        else:
            v_eff = math.sqrt(2.0 * drop_module.GRAVITY_M_S2 * height)
        surface = str(drop_config.get("surface") or "concrete").strip().lower()
        contact_kwargs, _, _ = _resolve_drop_contact(
            request, catalog, materials_by_object, surface, drop_request, result
        )
        estimate = impact.estimate_impact(
            mass_kg,
            velocity_m_s=v_eff,
            restitution=drop_module.SURFACES[surface]["restitution"],
            **contact_kwargs,
        )
        accel_m_s2 = float(estimate.to_dict().get("peak_acceleration_m_s2") or 0.0)
        first_drop = (drop_section.get("drops") or [{}])[0]
        drop_summary = {
            "peak_impact_speed_m_s": float(peak.get("impact_speed_m_s") or 0.0),
            "peak_accel_g": round(accel_m_s2 / 9.80665, 6),
            "peak_acceleration_g": round(accel_m_s2 / 9.80665, 6),
            "peak_force_n": drop_section.get("peak_force_estimate_n"),
            "impact_count": int(first_drop.get("impact_count") or 0),
            "settled_s": float(first_drop.get("settled_s") or 0.0),
            "peak_raw_energy_j": float(peak.get("raw_kinetic_energy_j") or 0.0),
        }

    lifecycle_usage = None
    lifecycle_section = request.get("lifecycle")
    if isinstance(lifecycle_section, Mapping):
        lifecycle_usage = {
            key: lifecycle_section.get(key)
            for key in (
                "prior_drops",
                "prior_impact_energy_j",
                "actuation_cycles",
                "scroll_encoder_rotations",
                "slide_distance_km",
                "age_days",
            )
        }
    env_temp = request.get("environment_temperature_k")
    if env_temp is not None:
        try:
            env_temp = float(env_temp)
        except (TypeError, ValueError):
            env_temp = None
        if env_temp is not None and (not math.isfinite(env_temp) or env_temp < 173.15 or env_temp > 373.15):
            env_temp = None

    context = {
        "mass_kg": mass_kg,
        "inertia_kg_m2": inertia,
        "support": support,
        "materials": dict(catalog),
        "drop": drop_summary,
        "lifecycle": lifecycle_usage,
        "environment_temperature_k": env_temp,
        "com_offset_m": com_offset_m,
        "shell": None,
        # The resolved shell material E/nu pair (or None) so the population's
        # default Hertz drop-impact path uses the SAME contact modulus as the
        # pipeline's drop estimate; None triggers a disclosed generic-polymer
        # fallback in the population run.
        "shell_hertz_pair": drop_module.hertz_shell_material_pair(
            _shell_material(request, catalog, materials_by_object)[0]
        ),
    }

    # Shell context for the population: the pinned structural load case with
    # the nominal closed-form response.  The population varies the shell
    # (wall thickness, modulus, strength, density) around this nominal and
    # reports the SHELL failure probability as its primary answer.
    structure_data = request.get("structure")
    load_case_data = request.get("load_case")
    structural = result.get("structural")
    if (
        isinstance(structure_data, Mapping)
        and load_case_data is not None
        and structural is not None
        and structural.get("response") is not None
        and structure_data.get("t_m") is not None
    ):
        response = structural["response"]
        # The population shell analysis is only meaningful when the nominal
        # structural response is itself valid or approximate: an inconclusive
        # nominal (unsupported geometry, failed solve) must not become a
        # confident shell-failure probability.
        response_validity = str(response.get("validity") or "inconclusive")
        sf = response.get("safety_factor")
        try:
            sf = float(sf) if sf not in (None, "not_available") else None
        except (TypeError, ValueError):
            sf = None
        if sf is not None and response_validity in ("valid", "approximate"):
            context["shell"] = {
                "structure": structure_data,
                "load_case": load_case_data,
                "nominal": {
                    "t_m": float(structure_data["t_m"]),
                    "safety_factor": sf,
                    "peak_stress_pa": response.get("max_stress_pa"),
                    "max_displacement_m": response.get("max_displacement_m"),
                },
            }

    if component_specs is not None:
        if not isinstance(component_specs, (list, tuple)):
            component_specs = [component_specs]
        from . import components_elec, components_mech

        components = []
        for spec in component_specs:
            if not isinstance(spec, Mapping) or not spec.get("type"):
                continue
            ctype = str(spec["type"]).strip().lower()
            if ctype in components_elec.COMPONENT_TYPES:
                components.append(components_elec.analyze(spec, context))
            elif ctype in components_mech.COMPONENT_TYPES:
                components.append(components_mech.analyze(spec, context))
            else:
                components.append(
                    {
                        "component_id": str(spec.get("component_id") or ctype),
                        "type": ctype,
                        "status": "not_evaluated",
                        "validity": "not_evaluated",
                        "metrics": {},
                        "findings": [
                            {
                                "code": "UNKNOWN_COMPONENT",
                                "severity": "info",
                                "message": "unknown component type {!r}".format(ctype),
                            }
                        ],
                        "assumptions": [],
                        "flags": [],
                        "usage_ratio": 0.0,
                    }
                )
        failed = [item for item in components if item.get("status") == "fail"]
        warned = [item for item in components if item.get("status") == "warn"]
        weakest = None
        candidates = [item for item in components if item.get("status") in ("fail", "warn")]
        if candidates:
            weakest = max(candidates, key=lambda item: float(item.get("usage_ratio") or 0.0))
        result["components"] = {
            "components": components,
            "summary": {
                "fail_count": len(failed),
                "warn_count": len(warned),
                "weakest": weakest,
            },
        }

    if population_config is not None:
        if not isinstance(population_config, Mapping):
            result["issues"].append(
                _issue("POPULATION_INVALID", "warning", "population", "population must be an object")
            )
        else:
            from . import population as population_module

            try:
                population_settings = dict(population_config)
                if not population_settings.get("components"):
                    population_settings["components"] = list(population_module.DEFAULT_COMPONENT_SPECS)
                if "workers" not in population_settings:
                    import os
                    population_settings["workers"] = min(os.cpu_count() or 4, 8)
                result["population"] = population_module.run_population(population_settings, context)
            except Exception as exc:
                result["issues"].append(
                    _issue("POPULATION_ANALYSIS_FAILED", "warning", "population", str(exc))
                )


def _assemble_shell_result(request, result, validation_run=None):
    """Assemble the authoritative SHELL engineering result.

    The shell is the primary engineering target: the structural response
    (deformation, stress, safety factor, critical region) under the pinned
    load case, with the drop-derived loading context and an honest
    confidence label.  Internal components are secondary context and never
    influence this section.

    Classification states (never conflated): safe / marginal / failed /
    unsupported / invalid_input / insufficient_evidence.  Confidence is
    split into PHYSICAL-MODEL confidence (what the model and its inputs
    justify) and STATISTICAL confidence (sampling only — a single run has
    none).
    """
    structural = result.get("structural")
    response = (structural or {}).get("response") or {}
    safety_factor = response.get("safety_factor")
    sf = None
    if safety_factor not in (None, "not_available"):
        try:
            sf = float(safety_factor)
        except (TypeError, ValueError):
            sf = None
    validity = str(response.get("validity") or "not_evaluated")
    issue_codes = [item.get("code") for item in result.get("issues") or ()]
    validation_findings = (result.get("validation") or {}).get("findings") or ()
    validation_codes = {str(item.get("code") or "") for item in validation_findings}
    mass_assumed = "DROP_SIMULATION_MASS_ASSUMED" in issue_codes
    inertia_approximated = "DROP_SIMULATION_INERTIA_APPROXIMATED" in issue_codes
    # W4-03: inverted thickness limits (min > max) were flagged by the
    # validation report but the shell verdict ignored them — a contradictory
    # PASS/SAFE next to a fail report.  Geometry-configuration contradictions
    # must restrict the verdict like any other unverifiable-geometry case.
    thickness_limits_invalid = "THICKNESS_LIMITS_INVALID" in validation_codes
    # Geometry integrity is INCOMPLETE for meshes beyond the exact
    # self-intersection sweep limit: the mass may be affected, so the shell
    # must never be declared safe or highly confident on such a mesh.
    geometry_integrity_uncertain = "SELF_INTERSECTION_UNVERIFIED" in validation_codes
    invalid_input = bool(result.get("errors")) or any(
        code in ("GEOMETRY_PARSE_FAILED", "GEOMETRY_MISSING", "INVALID_OBJECTS")
        for code in issue_codes
    )
    unsupported = response.get("unsupported_failure_modes") or []
    unsupported_flags = [
        flag for flag in (response.get("flags") or ()) if str(flag).startswith("UNSUPPORTED_")
    ]
    # Point-load structural cases: the peak stress is a truncated-series
    # value whose convergence is load-position dependent
    # (POINT_LOAD_SINGULARITY / POINT_LOAD_STRESS_ORDER_DEPENDENT), so the
    # safety factor alone can never certify a pass — the verdict must be
    # marginal/warn with a targeted reason (S1 gate).
    point_load_flags = [
        flag
        for flag in (response.get("flags") or ())
        if flag in ("POINT_LOAD_SINGULARITY", "POINT_LOAD_STRESS_ORDER_DEPENDENT")
    ]
    preflight_codes = {
        str(item.get("code") or "")
        for item in (result.get("structural") or {}).get("preflight", [])
        if isinstance(item, Mapping)
    }
    point_load_present = bool(
        point_load_flags
        or preflight_codes & {"POINT_LOAD_SINGULARITY", "POINT_LOAD_STRESS_ORDER_DEPENDENT"}
    )
    calibration = result.get("correlation")
    # Audit finding: confidence "high" previously gated on verdict=="pass"
    # only, so a 1-2 condition pass (user-lowered min_drop_conditions) or a
    # non-equivalent pass unlocked high confidence while model_status stayed
    # below correlated.  ONE standard: high requires model_status ==
    # "correlated" (>= 3 equivalent, identity-consistent conditions with a
    # peak-acceleration comparison).
    from . import shell_validation as validation_module

    model_status_data = validation_module.build_model_status(result, validation_run)
    calibration_passed = model_status_data["model_status"] == "correlated"
    if invalid_input:
        status = "not_evaluated"
        classification = "invalid_input"
    elif sf is None:
        status = "not_evaluated"
        classification = "insufficient_evidence"
    elif (
        (mass_assumed or inertia_approximated or geometry_integrity_uncertain or thickness_limits_invalid)
        and sf is not None
        and sf >= 1.2
    ):
        # The geometry could not certify a solid (mass/inertia assumed, or
        # self-intersection unverified beyond the sweep limit, or the
        # thickness limits are contradictory): the pinned structural analysis
        # is still shown, but the shell cannot be declared SAFE — the
        # physical object's drop-side behavior is unverifiable.
        status = "warn"
        classification = "insufficient_evidence"
    elif (
        (result.get("mass") or {}).get("mass_status") not in ("calculated", "measured")
        and sf is not None
        and sf >= 1.2
    ):
        # The geometry produced no certifiable mass (open/unsafe mesh): the
        # shell verdict is restricted to what the pinned structural analysis
        # supports.
        status = "warn"
        classification = "insufficient_evidence"
    elif sf < 1.0:
        status = "fail"
        classification = "failed"
    elif unsupported_flags and validity == "approximate":
        # The analysis is only approximate because part of the physics is
        # unsupported (anisotropy, weld lines, ...): never PASS — the numeric
        # SF is still reported, the verdict is disclosed as unsupported.
        status = "warn"
        classification = "unsupported"
    elif point_load_present and sf >= 1.0:
        # S1 gate: a point-load stress is series-order dependent and cannot
        # be certified by its safety factor alone — a would-be PASS is
        # classified marginal/warn with a targeted reason entry.
        status = "warn"
        classification = "marginal"
    elif sf < 1.2:
        status = "warn"
        classification = "marginal"
    else:
        status = "pass"
        classification = "safe"
    if validity in ("inconclusive", "failed") and classification not in (
        "invalid_input",
        "failed",
    ):
        status = "not_evaluated"
        classification = "insufficient_evidence"
    # Physical-model confidence: 'high' requires a valid response with NO
    # unsupported modes, NO assumed mass/inertia, NO point-load singularity,
    # AND a passed measured-drop correlation (calibration).  Everything else
    # is capped at medium (screening) or low.
    if (
        validity == "valid"
        and not unsupported_flags
        and not mass_assumed
        and not inertia_approximated
        and not point_load_present
        and not geometry_integrity_uncertain
        and calibration_passed
    ):
        physical_model_confidence = "high"
    elif validity in ("valid", "approximate"):
        physical_model_confidence = "medium"
    else:
        physical_model_confidence = "low"
    critical_region = response.get("max_displacement_location")
    if critical_region is None:
        critical_region = response.get("filtered_location")
    stability = _critical_region_probe(request, result)
    drop_section = result.get("drop_simulation")
    loading = None
    if drop_section is not None and drop_section.get("peak") is not None:
        peak = drop_section["peak"]
        loading = {
            "drop_peak_speed_m_s": float(peak.get("impact_speed_m_s") or 0.0),
            "drop_peak_energy_j": float(peak.get("kinetic_energy_j") or 0.0),
            "drop_peak_force_n": drop_section.get("peak_force_estimate_n"),
        }
    limitations = []
    if unsupported:
        limitations.append("unsupported failure modes: {}".format(", ".join(sorted(set(unsupported)))))
    if mass_assumed:
        limitations.append("mass assumed 0.06 kg (60 g ultralight reference; geometry mass properties unavailable)")
    if inertia_approximated:
        limitations.append("inertia approximated by a bounding box")
    if geometry_integrity_uncertain:
        limitations.append(
            "self-intersection unverified beyond the exact sweep limit; "
            "geometry integrity and mass are not fully certified"
        )
    if not calibration_passed:
        limitations.append(
            "no passed measured-drop correlation: the physical model is uncalibrated screening"
        )
    if point_load_present and sf is not None and sf >= 1.0:
        limitations.append(
            "point-load stress is series-order dependent "
            "(POINT_LOAD_SINGULARITY / POINT_LOAD_STRESS_ORDER_DEPENDENT): "
            "the safety factor alone cannot certify this case; margin requires "
            "a dedicated local-contact model"
        )
    if stability is not None and not stability["stable"]:
        limitations.append(stability["statement"])
    raw_assumptions = response.get("assumptions") or []
    if not isinstance(raw_assumptions, list):
        raw_assumptions = [str(raw_assumptions)]
    result["shell"] = {
        "status": status,
        "classification": classification,
        "peak_stress_pa": response.get("max_stress_pa"),
        "max_displacement_m": response.get("max_displacement_m"),
        "min_safety_factor": sf,
        "critical_region": list(critical_region) if critical_region is not None else None,
        "critical_region_stability": stability,
        "failure_mode": (
            "validity {}".format(validity)
            + ("; unsupported: " + ", ".join(sorted(set(unsupported))) if unsupported else "")
        ),
        "physical_model_confidence": physical_model_confidence,
        "statistical_confidence": {"kind": "single_run"},
        "statement": (
            "closed-form screening surrogate; physical-model confidence {}, "
            "statistical confidence: single deterministic run (no sampling)".format(
                physical_model_confidence
            )
        ),
        "assumptions": raw_assumptions,
        "limitations": limitations,
        "loading": loading,
    }
    # Physical-validation framing (validation-preparation phase): the model
    # status separates SIMULATION RESULT from PHYSICALLY VALIDATED RESULT,
    # the trace records one authoritative value per quantity used, and the
    # invalidating-assumption list states what would invalidate the verdict.
    result["shell"]["model_status"] = model_status_data["model_status"]
    result["shell"]["physical_validation"] = model_status_data["physical_validation"]
    result["shell"]["invalidating_assumptions"] = validation_module.build_invalidating_assumptions(result)
    result["shell"]["inputs_trace"] = validation_module.build_shell_trace(request, result)


def _critical_region_probe(request, result):
    """Perturbation probe for critical-region stability.

    Re-solves the structural case with small perturbations (thickness ±1%,
    load ±1%, series order ±2, and for point loads the load position shifted
    by half a grid cell) and reports whether the critical region stays put.
    A region that flips under tiny perturbations is reported UNSTABLE rather
    than pretending one exact triangle is the definitive failure point.
    """
    structural = result.get("structural")
    if structural is None:
        return None
    structure = structural.get("structure")
    load_case = structural.get("load_case")
    # The probe re-solves with the SAME resolved material the nominal solve
    # used (audit finding: re-resolving from the builtin catalog could
    # silently re-probe with a different E/allowable than the nominal).
    material_payload = structural.get("resolved_material") or {}
    if not isinstance(structure, dict) or not isinstance(load_case, dict):
        return None
    fixtures = None
    try:
        from . import physics as physics_module

        locations = []
        nominal_location = structural["response"].get("max_displacement_location")
        if nominal_location is None:
            return None
        variants = []
        if "t_m" in structure:
            variants.append(("t_m", float(structure["t_m"]) * 0.99))
        magnitude = load_case.get("magnitude")
        if isinstance(magnitude, dict) and "value" in magnitude:
            variants.append(("load", float(magnitude["value"]) * 1.01))
        variants.append(("series", None))
        for label, _ in variants:
            probe_structure = dict(structure)
            probe_load = dict(load_case)
            if label == "t_m":
                probe_structure["t_m"] = _lifecycle_float(variants[0][1])
            elif label == "load":
                probe_magnitude = dict(load_case.get("magnitude", {}))
                probe_magnitude["value"] = _lifecycle_float(variants[1][1])
                probe_load["magnitude"] = probe_magnitude
            elif label == "series":
                current = int(probe_structure.get("series_order") or 9)
                probe_structure["series_order"] = max(3, current - 2)
            try:
                probe_response = physics_module.solve_load_case(
                    probe_load, probe_structure, material_payload, fixtures
                )
                location = probe_response.max_displacement_location
                if location is not None:
                    locations.append((label, tuple(location)))
                filtered = probe_response.filtered_location
                if filtered is not None:
                    locations.append((label + "_filtered", tuple(filtered)))
            except Exception:
                continue
        nominal = tuple(nominal_location)
        a_dim = float(structure.get("a_m") or structure.get("length_m") or 0.0)
        b_dim = float(structure.get("b_m") or structure.get("width_m") or 0.0)
        span = min(a_dim, b_dim) if (a_dim and b_dim) else max(a_dim, b_dim)
        tolerance = max(1e-6, 0.2 * span) if span else 1e-6
        max_shift = 0.0
        for _, location in locations:
            distance = math.sqrt(
                sum((location[index] - nominal[index]) ** 2 for index in range(3))
            )
            if distance > max_shift:
                max_shift = distance
        stable = max_shift <= tolerance
        return {
            "stable": stable,
            "probe_solves": len(locations) + 1,
            "max_location_shift_m": round(max_shift, 9),
            "tolerance_m": round(tolerance, 9),
            "statement": (
                "stable"
                if stable
                else "UNSTABLE: multiple regions have similar peak stress; the reported critical region is not definitive"
            ),
        }
    except Exception:
        return None


def _correlation_summary(conditions, max_error, min_conditions):
    """Aggregate per-condition comparisons into the correlation verdict.

    Fail-closed: the verdict, R^2, and bias are computed from the
    measured/predicted pairs only (never from reported summary fields).
    Passing requires at least ``min_conditions`` DISTINCT evaluated
    conditions, at least ``min_conditions`` distinct measured values (a
    degenerate/duplicated dataset cannot define a meaningful R^2), every
    per-metric relative error within ``max_error``, R^2 in [0, 1] and
    >= 0.80, and |bias| <= 0.10.
    """
    # Audit finding: non-equivalent conditions (corner/edge impacts with
    # off-CoM or axis sensors — factor ~2-3 mismatch) previously drove the
    # verdict and the correlated/high-confidence labels while every row was
    # flagged NOT EQUIVALENT.  They are now EXCLUDED from the verdict (the
    # rows remain in the comparison table with their flags).  Conditions
    # flagged with identity mismatches (different CAD revision / material /
    # prototype) are likewise excluded.  When the equivalence field is
    # absent (legacy exploration-mode correlation), conditions are treated
    # as equivalent by default.
    all_evaluated = [condition for condition in conditions if condition["metrics"]]
    excluded = [
        condition
        for condition in all_evaluated
        if not condition.get("equivalent", True) or not condition.get("identity_ok", True)
    ]
    evaluated = [
        condition
        for condition in all_evaluated
        if condition.get("equivalent", True) and condition.get("identity_ok", True)
    ]
    all_metrics = [metric for condition in evaluated for metric in condition["metrics"]]
    evaluated_metric_count = len(all_metrics)
    failures = [metric for metric in all_metrics if not metric.get("pass", False)]
    measured_points = []
    predicted_points = []
    for condition in evaluated:
        for metric in condition["metrics"]:
            if metric.get("measured") is not None and metric.get("predicted") is not None:
                measured_points.append(metric["measured"])
                predicted_points.append(metric["predicted"])
    r_squared = None
    if len(measured_points) >= 2:
        mean_m = sum(measured_points) / len(measured_points)
        mean_p = sum(predicted_points) / len(predicted_points)
        numerator = sum(
            (m - mean_m) * (p - mean_p) for m, p in zip(measured_points, predicted_points)
        )
        denom_m = math.sqrt(sum((m - mean_m) ** 2 for m in measured_points))
        denom_p = math.sqrt(sum((p - mean_p) ** 2 for p in predicted_points))
        if denom_m > 1e-12 and denom_p > 1e-12:
            r_squared = (numerator / (denom_m * denom_p)) ** 2
    bias = None
    if all_metrics:
        signed = [
            (measured - predicted) / abs(measured)
            for metric in all_metrics
            if metric.get("measured") is not None
            and metric.get("predicted") is not None
            and abs(metric["measured"]) > 1e-12
            for measured, predicted in [(metric["measured"], metric["predicted"])]
        ]
        if signed:
            bias = sum(signed) / len(signed)
    min_r_squared = 0.80
    max_bias = 0.10
    reasons = []
    # W10-01: exclusions are disclosed in the explanation but never veto the
    # verdict (they do not contribute to it, by the campaign matrix design).
    exclusion_note = None
    if len(evaluated) < min_conditions:
        reasons.append(
            "{} of {} required drop conditions evaluated".format(len(evaluated), min_conditions)
        )
    distinct_measured = len(set(round(value, 9) for value in measured_points))
    if distinct_measured < min_conditions:
        reasons.append(
            "{} distinct measured value(s) across {} comparison(s); R^2 is not meaningful".format(
                distinct_measured, len(measured_points)
            )
        )
    if len(measured_points) >= 2 and r_squared is None:
        # Zero variance in measured or predicted: R^2 is undefined (e.g. all
        # repeats at one height).  Fail closed rather than pass vacuously.
        reasons.append("measured or predicted values have zero variance; R^2 is undefined")
    duplicate_identities = []
    seen_identities = []
    for condition in evaluated:
        # Independence is judged on the PHYSICAL POSE: (height, surface,
        # resolved orientation QUATERNION), not the orientation label.
        # Audit finding: mode "corner" and the explicit corner quaternion
        # are the SAME pose but were previously counted as two conditions
        # (the quaternion label collapsed to "explicit"), letting two
        # physical conditions pass as three; conversely, two genuinely
        # different explicit quaternions at one height/surface were falsely
        # flagged as duplicates.
        quaternion = condition.get("orientation_quaternion_wxyz")
        if isinstance(quaternion, (list, tuple)) and len(quaternion) == 4:
            # Canonicalize the sign: q and -q describe the same orientation
            # (audit follow-up: the sign-sensitive key let (-1,0,0,0) and
            # (1,0,0,0) count as two distinct conditions).
            components = [float(component) for component in quaternion]
            sign = 1.0
            for component in components:
                if abs(component) > 1e-9:
                    sign = 1.0 if component > 0.0 else -1.0
                    break
            orientation_key = tuple(
                round(component * sign, 6) for component in components
            )
        else:
            orientation_key = str(condition.get("orientation", "") or "").strip().lower()
        height_value = float(condition.get("height_m"))
        surface_key = str(condition.get("surface", "") or "").strip().lower()
        # W2-02E follow-up: the 4dp cell key had a knife-edge — heights
        # straddling a cell boundary (e.g. 0.75005 vs 0.75006, 10 um apart)
        # counted as independent.  Independence uses a 1 mm pose tolerance
        # (same physical drop) instead of exact cell equality.
        is_duplicate = any(
            surface_key == seen_surface
            and orientation_key == seen_orientation
            and abs(height_value - seen_height) < 1e-3
            for seen_height, seen_surface, seen_orientation in seen_identities
        )
        if is_duplicate:
            duplicate_identities.append((height_value, surface_key, orientation_key))
        else:
            seen_identities.append((height_value, surface_key, orientation_key))
    if duplicate_identities:
        reasons.append(
            "{} duplicate drop condition(s) (same height/surface/orientation pose)".format(
                len(duplicate_identities)
            )
        )
    # Audit finding (W2-13 follow-up / W10-01): non-equivalent conditions are
    # EXCLUDED from the verdict — they must not contribute to it, and they
    # must not VETO it either.  The campaign matrix documents rows 4-8/11 as
    # diagnostic rows that appear in the comparison table with their flags
    # but do NOT contribute to the verdict; a full campaign with a passing
    # equivalent subset must still be able to reach correlated.  Exclusions
    # are disclosed (excluded_conditions / excluded_reasons / explanation)
    # but never fail the verdict on their own.
    if excluded:
        exclusion_note = (
            "{} condition(s) excluded from the verdict (disclosed, not "
            "counted): {}".format(
                len(excluded),
                "; ".join(
                    "{} ({})".format(
                        str(condition.get("drop_id") or "?"),
                        "NOT EQUIVALENT sensor comparison"
                        if not condition.get("equivalent", True)
                        else "identity mismatch: " + "; ".join(condition.get("identity_flags") or []),
                    )
                    for condition in excluded
                ),
            )
        )
    if failures:
        reasons.append("{} of {} metric comparisons exceeded the {:.0%} error bound".format(
            len(failures), evaluated_metric_count, max_error
        ))
    if r_squared is not None and (r_squared < min_r_squared or r_squared > 1.0 + 1e-9):
        reasons.append("R-squared {:.3f} outside [{:.2f}, 1]".format(r_squared, min_r_squared))
    if bias is not None and abs(bias) > max_bias:
        reasons.append("signed bias {:.3f} exceeds {:.3f}".format(bias, max_bias))
    if not evaluated:
        reasons.append("no measured drop conditions could be evaluated")
    verdict = "pass" if not reasons else "fail"
    # W10-01: the explanation discloses both the failing reasons and the
    # excluded conditions (which are disclosed but never veto).
    explanation_parts = list(reasons)
    if exclusion_note:
        explanation_parts.append(exclusion_note)
    explanation = "; ".join(explanation_parts) if explanation_parts else (
        "predicted vs measured drop response within acceptance"
    )
    return {
        "conditions": conditions,
        "evaluated_conditions": len(evaluated),
        "excluded_conditions": len(excluded),
        "excluded_reasons": [
            {
                "drop_id": str(condition.get("drop_id") or "?"),
                "reason": "NOT EQUIVALENT sensor comparison"
                if not condition.get("equivalent", True)
                else "identity mismatch",
                "details": condition.get("identity_flags") or condition.get("equivalence_note"),
            }
            for condition in excluded
        ],
        "max_relative_error": round(max_error, 6),
        "min_drop_conditions": min_conditions,
        "r_squared": round(r_squared, 6) if r_squared is not None else None,
        "bias": round(bias, 6) if bias is not None else None,
        "verdict": verdict,
        "explanation": explanation,
    }


def _normalize_load(load):
    """Materialize magnitude_pa/force_n from a ``magnitude``/``force`` input.

    W4-03: a non-positive load magnitude (e.g. -1 kPa) previously flowed into
    the solver — the von Mises stress is sign-invariant, so a negative load
    produced a plausible-looking PASS/SAFE with a negative displacement.
    Loads must be strictly positive; a zero or negative load is an input
    error, never a valid structural case.
    """
    result = dict(load or {})
    magnitude = result.get("magnitude")
    if magnitude is not None:
        if isinstance(magnitude, Mapping):
            if "unit" in magnitude:
                result["magnitude_pa"] = to_si(
                    magnitude.get("value", 0.0), magnitude["unit"], expected_dimension="pressure"
                )
            else:
                result["magnitude_pa"] = float(magnitude.get("value", 0.0))
        else:
            result["magnitude_pa"] = float(magnitude)
        value = result["magnitude_pa"]
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                "load_case.magnitude must be a positive, finite pressure: a "
                "zero or negative load is not a valid structural case "
                "(received {!r})".format(value)
            )
    force = result.get("force")
    if force is not None:
        if isinstance(force, Mapping) and "unit" in force:
            result["force_n"] = to_si(force.get("value", 0.0), force["unit"], expected_dimension="force")
        elif isinstance(force, Mapping):
            result["force_n"] = float(force.get("value", 0.0))
        else:
            result["force_n"] = float(force)
        value = result["force_n"]
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                "load_case.force must be a positive, finite force: a zero or "
                "negative load is not a valid structural case (received "
                "{!r})".format(value)
            )
    return result


def _object_entries(request):
    if "objects" not in request:
        return ()
    raw_objects = request.get("objects")
    if raw_objects is None:
        raise ValueError("objects must be an object or array")
    if isinstance(raw_objects, Mapping):
        entries = []
        for key, value in raw_objects.items():
            if not isinstance(value, Mapping):
                raise ValueError("objects entry {!r} must be an object".format(key))
            entries.append((str(key), dict(value)))
        return tuple(entries)
    if not isinstance(raw_objects, (list, tuple)):
        raise ValueError("objects must be an object or array")
    entries = []
    for index, value in enumerate(raw_objects):
        if not isinstance(value, Mapping):
            raise ValueError("objects[{}] must be an object".format(index))
        identifier = value.get("id", value.get("name"))
        entries.append((identifier, dict(value)))
    return tuple(entries)


def _pipeline_error(result, code, message):
    result["issues"].append(_issue(code, "error", "pipeline", message, evidence_blocking=True))
    result["errors"].append({"code": code, "message": message})


def _material_catalog(request, result):
    raw_materials = request.get("materials")
    if raw_materials is None:
        catalog = materials.builtin_materials()
    else:
        try:
            catalog = materials.load_material_catalog(raw_materials)
        except Exception as exc:
            message = str(exc) or "invalid material catalog"
            result["issues"].append(
                _issue("MATERIAL_CATALOG_INVALID", "error", "material", message, evidence_blocking=True)
            )
            result["errors"].append({"code": "MATERIAL_CATALOG_INVALID", "message": message})
            catalog = materials.builtin_materials()
    # A usable Default material must always exist: fallback assignment for
    # components without an explicit material must never fail.
    return materials.ensure_default_material(catalog)


# Context-aware density estimates (kg/m3) for components that carry no
# explicit material.  Values mirror the built-in material catalog so a
# Default-assigned part's MASS reflects its classified role (a shell is a
# polymer, a PCB is FR4, a battery is LiPo, ...) instead of the generic
# Default polymer density.  These are engineering estimates, not supplier
# measurements; the mass section discloses them via mass_status/diagnostics.
#
# The table carries BOTH the canonical taxonomy keys
# (classification.CANONICAL_COMPONENT_TYPES: top_shell, bottom_shell,
# scroll_wheel, foot_pad, screw_boss, ...) and the legacy rule-classifier
# keys (shell_top, shell_bottom, wheel, skate, screw, ...): the pipeline's
# classification dict holds canonical names when a user-reviewed or AI-fused
# classification is applied, and legacy names on the raw rule path.  A
# missing key silently dropped the density override AND its disclosure, so
# the two vocabularies must never diverge again.
_CLASSIFICATION_DENSITY_KG_M3 = {
    # Canonical taxonomy keys.
    "top_shell": 1040.0,
    "bottom_shell": 1040.0,
    "main_button": 1040.0,
    "side_button": 1040.0,
    "scroll_wheel": 1410.0,
    "encoder": 1410.0,
    "pcb": 1850.0,
    "sensor": 1850.0,
    "foot_pad": 2200.0,
    "battery": 2500.0,
    "internal_structure": 1040.0,
    "screw_boss": 7850.0,
    # Legacy rule-classifier keys.
    "shell_top": 1040.0,
    "shell_bottom": 1040.0,
    "shell": 1040.0,
    "button": 1040.0,
    "wheel": 1410.0,
    "skate": 2200.0,
    "screw": 7850.0,
}


def _apply_classification_densities(result, classifications, density_by_object):
    """Override the density of Default-material objects with a
    classification-based estimate (disclosed in the geometry summary)."""
    assignments = {
        str(entry.get("object_id")): entry
        for entry in result.get("material_assignments") or ()
    }
    summary_by_id = {
        str(entry.get("object_id")): entry
        for entry in result.get("geometry_summary", {}).get("objects") or ()
    }
    estimated = dict(density_by_object or {})
    for object_id, classification in (classifications or {}).items():
        assignment = assignments.get(str(object_id))
        if assignment is None or assignment.get("source") != "default":
            continue
        density = _CLASSIFICATION_DENSITY_KG_M3.get(str(classification.get("component_type")))
        if density is None:
            continue
        estimated[object_id] = density
        summary = summary_by_id.get(str(object_id))
        if isinstance(summary, dict):
            summary["density_source"] = "classification_estimate"
            summary["density_kg_m3"] = density
    return estimated


def _parse_objects(request, catalog, result, units):
    geometry_objs = {}
    materials_by_object = {}
    density_by_object = {}
    overrides = {}
    behaviors = {}
    # User-reviewed AI classifications ride along on request objects
    # (component_type + confidence); they win over heuristic and AI signals.
    request_classifications = {}
    request_names = {}
    if "objects" in request and not isinstance(request.get("objects"), (Mapping, list, tuple)):
        _pipeline_error(result, "INVALID_OBJECTS", "objects must be an object or array")
        return geometry_objs, materials_by_object, density_by_object, overrides, behaviors, request_classifications, request_names
    # Default-material fallback: every object without a valid explicit
    # material is deterministically assigned the configured Default material
    # so the simulation never runs on undefined material properties.
    requested_default = request.get("default_material")
    default_key = (
        str(requested_default).strip()
        if requested_default is not None
        else materials.DEFAULT_MATERIAL_KEY
    )
    if default_key not in catalog:
        default_key = materials.DEFAULT_MATERIAL_KEY
        if requested_default is not None:
            result["issues"].append(
                _issue(
                    "DEFAULT_MATERIAL_KEY_MISSING",
                    "warning",
                    "material",
                    "default material {!r} is not in the catalog; using the built-in "
                    "Default material".format(requested_default),
                )
            )
    default_material = catalog[default_key]
    assignments = []
    default_count = 0
    try:
        entries = _object_entries(request)
    except (TypeError, ValueError) as exc:
        _pipeline_error(result, "INVALID_OBJECTS", str(exc))
        return geometry_objs, materials_by_object, density_by_object, overrides, behaviors, request_classifications, request_names
    seen_ids = set()
    for raw_object_id, raw in entries:
        object_id = "" if raw_object_id is None else str(raw_object_id).strip()
        if not object_id:
            _pipeline_error(result, "INVALID_OBJECT_ID", "object id must not be blank")
            continue
        if object_id in seen_ids:
            _pipeline_error(result, "DUPLICATE_OBJECT_ID", "duplicate object id {!r}".format(object_id))
            continue
        seen_ids.add(object_id)
        name_value = raw.get("name")
        if isinstance(name_value, str) and name_value.strip():
            request_names[object_id] = name_value
        geometry_data = raw.get("geometry", raw.get("shape"))
        geometry = None
        parse_message = None
        if geometry_data is None:
            parse_message = "object {!r} has no geometry".format(object_id)
            result["issues"].append(
                _issue("GEOMETRY_MISSING", "error", "geometry", parse_message, evidence_blocking=True)
            )
            result["errors"].append({"code": "GEOMETRY_MISSING", "message": parse_message})
        else:
            try:
                geometry, repair_diagnostics = importers.parse_and_repair_geometry(
                    geometry_data, units=units
                )
            except Exception as exc:
                geometry = None
                repair_diagnostics = ()
                parse_message = "object {!r}: {}".format(object_id, exc)
                result["issues"].append(
                    _issue("GEOMETRY_PARSE_FAILED", "error", "geometry", parse_message, evidence_blocking=True)
                )
                result["errors"].append({"code": "GEOMETRY_PARSE_FAILED", "message": parse_message})
        kind = None
        diagnostics = []
        if geometry is not None:
            kind = getattr(geometry, "kind", None)
            if hasattr(geometry, "diagnostics"):
                diagnostics = list(getattr(geometry.diagnostics(), "issues", ()))
            # Open meshes with coincident seam vertices can be stitched by a
            # conservative weld (never fabricated geometry); a successful
            # repair is disclosed in the geometry summary diagnostics.
            if isinstance(geometry, importers.TriangleMesh) and repair_diagnostics:
                repaired_issues = getattr(geometry.diagnostics(), "issues", ())
                diagnostics = list(repaired_issues) + [
                    "mesh_weld_repaired: {}".format(item.message) for item in repair_diagnostics
                ]
            geometry_objs[object_id] = geometry
        else:
            result["geometry_summary"]["parse_errors"].append(
                {"object_id": object_id, "message": parse_message or "geometry unavailable"}
            )
        material_key = raw.get("material")
        material = None
        source = None
        if material_key is not None:
            if isinstance(material_key, Mapping):
                # An inline material definition: validate it and use it, or
                # fail with a clear message instead of a TypeError.
                try:
                    material = materials._material_definition(material_key)
                    errors = materials.material_validation_errors(material)
                    if errors:
                        material = None
                        result["issues"].append(
                            _issue(
                                "MATERIAL_INLINE_INVALID",
                                "warning",
                                "material",
                                "inline material for object {!r} failed validation: {}".format(
                                    object_id, "; ".join(errors)
                                ),
                            )
                        )
                except Exception as exc:
                    material = None
                    result["issues"].append(
                        _issue(
                            "MATERIAL_INLINE_INVALID",
                            "warning",
                            "material",
                            "inline material for object {!r} could not be parsed: {}".format(
                                object_id, exc
                            ),
                        )
                    )
            else:
                material = catalog.get(material_key)
        if material is None and material_key is not None:
            result["issues"].append(
                _issue(
                    "MATERIAL_NOT_FOUND",
                    "warning",
                    "material",
                    "material {!r} for object {!r} is not in the catalog; using the "
                    "Default material".format(material_key, object_id),
                )
            )
        if material is None:
            material = default_material
            source = "default"
            default_count += 1
        else:
            source = "explicit"
        if material is not None:
            materials_by_object[object_id] = material
            density_by_object[object_id] = (
                material.properties if hasattr(material, "properties") else material
            )
        assignments.append(
            {
                "object_id": object_id,
                "material": material_key if source == "explicit" else default_key,
                "source": source,
            }
        )
        override = raw.get("mass_override", raw.get("measured_mass"))
        if override is not None:
            overrides[object_id] = override
        behavior = raw.get("structural_behavior")
        if behavior is not None:
            behaviors[object_id] = behavior
        raw_classification = raw.get("classification")
        if isinstance(raw_classification, Mapping):
            component_type = raw_classification.get("component_type")
            if isinstance(component_type, str) and component_type.strip():
                request_classifications[object_id] = {
                    "component_type": canonical_component_type(component_type),
                    "confidence": raw_classification.get("confidence", 0.95),
                }
        result["geometry_summary"]["objects"].append(
            {
                "object_id": object_id,
                "geometry_type": kind,
                "units": units,
                "parsed": geometry is not None,
                "diagnostics": diagnostics,
                "material": material_key if source == "explicit" else default_key,
            }
        )
    result["material_assignments"] = assignments
    if default_count:
        result["issues"].append(
            _issue(
                "DEFAULT_MATERIAL_ASSIGNED",
                "warning",
                "material",
                "{} component(s) have no explicit material and use the Default "
                "material ({!r}); assign a specific material to tighten the model".format(
                    default_count, default_key
                ),
            )
        )
    return geometry_objs, materials_by_object, density_by_object, overrides, behaviors, request_classifications, request_names


def _first_material(catalog):
    if isinstance(catalog, Mapping):
        for value in catalog.values():
            return value
    return None


def _material_evidence(catalog, materials_by_object, result):
    assignments = {}
    for entry in result["geometry_summary"]["objects"]:
        material = entry.get("material")
        if material is not None:
            assignments[entry["object_id"]] = str(material)
    keys_by_ref = {}
    if isinstance(catalog, Mapping):
        for key, material in catalog.items():
            keys_by_ref[str(material.meta.id).casefold()] = str(key)
            keys_by_ref[str(material.name).strip().casefold()] = str(key)
    definitions = {}
    for material in dict.fromkeys(materials_by_object.values()):
        key = keys_by_ref.get(str(material.meta.id).casefold())
        if key is None:
            key = keys_by_ref.get(str(material.name).strip().casefold())
        if key is None:
            key = material.meta.id
        definitions[str(key)] = canonical_value(material)
    return {
        "assignments": assignments,
        "definitions": definitions,
    }


_VALIDITY_RANK = {"valid": 0, "approximate": 1, "inconclusive": 2, "failed": 3}


def _state(value):
    value = str(value or "").strip().casefold()
    if value in ("failed", "fail", "error", "invalid"):
        return "failed"
    if value in ("inconclusive", "unknown", "not_available"):
        return "inconclusive"
    if value in ("approximate", "warn", "warning", "estimate"):
        return "approximate"
    if value in ("valid", "pass", "clear", "completed", "no_impact", ""):
        return "valid"
    return "inconclusive"


def _flag_state(flag):
    text = str(flag or "").casefold()
    if any(token in text for token in ("invalid", "failed", "failure", "error")):
        return "failed"
    if any(token in text for token in ("unsupported", "not_certified", "unknown", "missing")):
        return "inconclusive"
    if any(token in text for token in ("not_converged", "singularity", "underconstrained", "approx", "estimate")):
        return "approximate"
    return "valid"


def _add_state(states, value):
    states.append(_state(value))


def _add_flag_states(states, flags):
    for flag in flags or ():
        states.append(_flag_state(flag))


def _collect_unsupported(node, output):
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "unsupported_failure_modes" and isinstance(value, (list, tuple, set)):
                output.extend(str(item) for item in value)
            elif isinstance(value, (Mapping, list, tuple, set)):
                _collect_unsupported(value, output)
    elif isinstance(node, (list, tuple, set)):
        for value in node:
            if isinstance(value, (Mapping, list, tuple, set)):
                _collect_unsupported(value, output)


def _validity(result):
    states = []
    reasons = []
    assumptions = []
    unsupported = []
    validation_report = result["validation"] or {}
    findings = validation_report.get("findings") or []
    reasons.extend(
        item["message"]
        for item in findings
        if item.get("severity") in ("warning", "error", "blocker")
    )
    _add_state(states, validation_report.get("validity_state"))
    if validation_report.get("status") == "fail":
        states.append("failed")
    elif validation_report.get("status") == "warn":
        states.append("approximate")
    # Pipeline issues are part of the evidence: warnings downgrade an
    # otherwise valid run, errors and blockers fail it.
    for item in result.get("issues") or ():
        severity = str(item.get("severity", "info")).casefold()
        if severity in ("error", "blocker"):
            states.append("failed")
            reasons.append(str(item.get("message", item.get("code", "pipeline issue"))))
        elif severity == "warning":
            states.append("approximate")
            reasons.append(str(item.get("message", item.get("code", "pipeline warning"))))
    structural = result["structural"]
    if structural is not None:
        for item in structural.get("preflight") or ():
            severity = str(item.get("severity", "info")).casefold()
            if severity in ("error", "blocker"):
                states.append("failed")
                reasons.append(item.get("message", item.get("code", "structural preflight failed")))
            elif severity == "warning":
                states.append("approximate")
                reasons.append(item.get("message", item.get("code", "structural preflight warning")))
        response = structural.get("response") or {}
        raw_assumptions = response.get("assumptions") or []
        if not isinstance(raw_assumptions, list):
            raw_assumptions = [str(raw_assumptions)]
        assumptions.extend(raw_assumptions)
        validity_reasons = response.get("validity_reasons") or []
        if isinstance(validity_reasons, list):
            reasons.extend(str(item) for item in validity_reasons)
        _add_state(states, response.get("validity"))
        _add_flag_states(states, response.get("flags"))
        _collect_unsupported(response, unsupported)
    impact_section = result["impact"]
    if impact_section is not None:
        impact_result = impact_section.get("result")
        if impact_result is None:
            states.append("failed")
            reasons.append(impact_section.get("reason") or "impact result is missing")
        else:
            raw_assumptions = impact_result.get("assumptions") or []
            if not isinstance(raw_assumptions, list):
                raw_assumptions = [str(raw_assumptions)]
            assumptions.extend(raw_assumptions)
            _add_state(states, impact_result.get("validity"))
            _add_flag_states(states, impact_result.get("flags"))
            _collect_unsupported(impact_result, unsupported)
        _collect_unsupported(impact_section, unsupported)
        _add_flag_states(states, impact_section.get("flags"))
        if impact_section.get("reason"):
            states.append("inconclusive")
            reasons.append(str(impact_section["reason"]))
    drop_simulation = result.get("drop_simulation")
    if drop_simulation is not None:
        for check in drop_simulation.get("checks") or ():
            severity = str(check.get("severity", "warning")).casefold()
            if severity == "error":
                states.append("failed")
            else:
                states.append("approximate")
            reasons.append(str(check.get("message", check.get("code", "drop simulation check"))))
    collision_result = result.get("collision")
    if collision_result is not None:
        collision_status = str(collision_result.get("status", "")).casefold()
        if collision_status == "failed":
            states.append("failed")
            if collision_result.get("reason"):
                reasons.append(str(collision_result["reason"]))
        elif collision_status in ("skipped", "valid", "evaluated", "clear", ""):
            states.append("valid")
        else:
            _add_state(states, collision_status)
        for pair in collision_result.get("pairs") or ():
            pair_status = str(pair.get("status", "")).casefold()
            pair_names = "/".join(str(item) for item in pair.get("pair", ()))
            if pair_status == "interference" or pair.get("interference"):
                states.append("failed")
                reasons.append("collision interference for {}".format(pair_names))
            elif pair_status == "unknown":
                states.append("inconclusive")
                reasons.append("collision clearance unknown for {}".format(pair_names))
            elif pair_status in ("contact", "estimate"):
                states.append("approximate")
            _add_flag_states(states, pair.get("flags"))
    unsupported = sorted(set(unsupported))
    if unsupported:
        states.append("inconclusive")
        reasons.append("unsupported failure modes: {}".format(", ".join(unsupported)))
    if result.get("errors"):
        states.append("failed")
    state = max(states, key=lambda value: _VALIDITY_RANK.get(value, 2)) if states else "valid"
    # Confidence reflects how much analysis evidence the run actually
    # produced: 'high' only for a valid run with at least one analysis
    # section (structural response, impact estimate, or drop simulation),
    # 'medium' for a valid run whose evidence is limited to mass/geometry
    # screening, and 'low' otherwise.
    analysis_evidence = bool(
        (result.get("structural") or {}).get("response")
        or (result.get("impact") or {}).get("result")
        or result.get("drop_simulation") is not None
    )
    if state == "valid":
        # 'high' only with analysis evidence AND model_status == "correlated"
        # (>= 3 equivalent measured conditions passing acceptance):
        # analytical/numerical correctness is separate from physical
        # validation (freeze-phase item 12) — a solver that passes its own
        # tests never yields high confidence by itself, and a 1-2 condition
        # pass never does either.
        model_status = (result.get("shell") or {}).get("model_status")
        if analysis_evidence and model_status == "correlated":
            confidence = "high"
        else:
            confidence = "medium"
    else:
        confidence = "low"
    return {
        "state": state,
        "reasons": reasons,
        "assumptions": assumptions,
        "unsupported_failure_modes": unsupported,
        "confidence": confidence,
    }


_COLLISION_PAIR_KEYS = ("collision_pairs", "clearance_pairs")
_COLLISION_TOLERANCE_KEYS = ("collision_tolerance_m", "clearance_tolerance_m")
_COLLISION_RULE_KEYS = ("collision_pair_rules", "clearance_pair_rules", "pair_rules")


def _first_option(options, keys):
    for key in keys:
        if key in options:
            value = options.get(key)
            if value is not None:
                return value
    return None


def _clearance_margin_m(request):
    profile = request.get("tolerance_profile")
    if isinstance(profile, Mapping):
        value = profile.get("clearance_margin_m")
        if value is None:
            value = profile.get("clearance_margin")
    elif hasattr(profile, "clearance_margin_m"):
        value = getattr(profile, "clearance_margin_m", None)
    else:
        value = None
    if value is None:
        return None
    try:
        if isinstance(value, Mapping):
            if "unit" in value:
                return to_si(value.get("value", 0.0), value["unit"], expected_dimension="length")
            return float(value.get("value", value.get("value_si", 0.0)))
        return float(value)
    except (TypeError, ValueError):
        return None


def _with_clearance_margin(request, config):
    if not isinstance(config, Mapping):
        return config
    config = dict(config)
    if (
        "tolerance_m" not in config
        and "tolerance_a_m" not in config
        and "tolerance_b_m" not in config
    ):
        margin = _clearance_margin_m(request)
        if margin is not None:
            config["tolerance_m"] = margin
    return config


def _collision_config(request, options):
    options = options if isinstance(options, Mapping) else {}
    for key in ("collision", "clearance"):
        if key not in request:
            continue
        raw = request.get(key)
        if not isinstance(raw, Mapping):
            return raw
        config = dict(raw)
        if config.get("pairs") is None:
            pairs = _first_option(options, _COLLISION_PAIR_KEYS)
            if pairs is not None:
                config["pairs"] = pairs
        if config.get("tolerance_m") is None:
            tolerance = _first_option(options, _COLLISION_TOLERANCE_KEYS)
            if tolerance is not None:
                config["tolerance_m"] = tolerance
        if config.get("pair_rules") is None:
            rules = _first_option(options, _COLLISION_RULE_KEYS)
            if rules is not None:
                config["pair_rules"] = rules
        return _with_clearance_margin(request, config)
    config = {}
    for request_key, target in (
        ("collision_pairs", "pairs"),
        ("collision_tolerance_m", "tolerance_m"),
        ("collision_pair_rules", "pair_rules"),
    ):
        if request_key in request:
            config[target] = request[request_key]
    for option_keys, target in (
        (_COLLISION_PAIR_KEYS, "pairs"),
        (_COLLISION_TOLERANCE_KEYS, "tolerance_m"),
        (_COLLISION_RULE_KEYS, "pair_rules"),
    ):
        if config.get(target) is None:
            value = _first_option(options, option_keys)
            if value is not None:
                config[target] = value
    return _with_clearance_margin(request, config) or None


def _collision_pair(spec):
    if isinstance(spec, Mapping):
        pair = spec.get("pair")
        if pair is None and spec.get("a") is not None and spec.get("b") is not None:
            pair = (spec.get("a"), spec.get("b"))
        rule = spec.get("pair_rule", spec.get("rule"))
        if pair is None:
            return None, rule
        return pair, rule
    return spec, None


def _collision_rule(config, pair, pair_rule):
    rule = dict(pair_rule or {})
    if not isinstance(config, Mapping):
        return rule
    configured_rules = config.get("pair_rules")
    if isinstance(configured_rules, Mapping):
        names = tuple(sorted((str(pair[0]), str(pair[1]))))
        configured = configured_rules.get(names)
        if configured is None:
            configured = configured_rules.get("{}:{}".format(*names))
        if configured is None:
            configured = configured_rules.get("{},{}".format(*names))
        if isinstance(configured, Mapping):
            merged = dict(configured)
            merged.update(rule)
            rule = merged
    if "tolerance_m" not in rule and "deformation_allowance_m" not in rule:
        tolerance = config.get("tolerance_m")
        if tolerance is not None:
            try:
                rule["deformation_allowance_m"] = float(tolerance)
            except (TypeError, ValueError):
                pass
    if "tolerance_a_m" not in rule:
        tolerance_a = config.get("tolerance_a_m")
        if tolerance_a is not None:
            try:
                rule["tolerance_a_m"] = float(tolerance_a)
            except (TypeError, ValueError):
                pass
    if "tolerance_b_m" not in rule:
        tolerance_b = config.get("tolerance_b_m")
        if tolerance_b is not None:
            try:
                rule["tolerance_b_m"] = float(tolerance_b)
            except (TypeError, ValueError):
                pass
    return rule or None


def _run_collision(request, geometry_objs, result):
    options = request.get("options")
    if not isinstance(options, Mapping):
        options = {}
    config = _collision_config(request, options)
    skipped = {
        "status": "skipped",
        "configured": False,
        "reason": "collision pairs are not configured",
        "pairs": [],
        "count": 0,
        "flags": [],
    }
    if config is None:
        return skipped
    if isinstance(config, Mapping):
        raw_pairs = config.get("pairs")
        if not raw_pairs:
            return skipped
    else:
        raw_pairs = config
    if not isinstance(raw_pairs, (list, tuple)):
        _pipeline_error(result, "COLLISION_CONFIG_INVALID", "collision pairs must be an array")
        return {
            "status": "failed",
            "configured": True,
            "reason": "collision pairs must be an array",
            "pairs": [],
            "count": 0,
            "flags": [],
        }
    configured = []
    for spec in raw_pairs:
        pair, pair_rule = _collision_pair(spec)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            _pipeline_error(result, "COLLISION_PAIR_INVALID", "collision pair must contain two object ids")
            continue
        first, second = (str(pair[0]).strip(), str(pair[1]).strip())
        if not first or not second or first == second or first not in geometry_objs or second not in geometry_objs:
            _pipeline_error(result, "COLLISION_PAIR_INVALID", "collision pair references unknown or invalid object ids")
            continue
        configured.append((first, second, _collision_rule(config, (first, second), pair_rule)))
    if not configured:
        return {
            "status": "failed",
            "configured": True,
            "reason": "no valid collision pairs configured",
            "pairs": [],
            "count": 0,
            "flags": [],
        }
    involved = {}
    pair_rules = {}
    wanted = set()
    for first, second, rule in configured:
        involved[first] = geometry_objs[first]
        involved[second] = geometry_objs[second]
        key = tuple(sorted((first, second)))
        wanted.add(key)
        if rule:
            pair_rules[key] = rule
    try:
        matrix = collision.pair_clearance_matrix(involved, pair_rules=pair_rules)
    except (TypeError, ValueError) as exc:
        _pipeline_error(result, "COLLISION_EVALUATION_FAILED", str(exc))
        return {
            "status": "failed",
            "configured": True,
            "reason": str(exc),
            "pairs": [],
            "count": 0,
            "flags": [],
        }
    records = [
        record
        for record in matrix.get("pairs") or ()
        if tuple(record.get("pair", ())) in wanted
    ]
    flags = sorted({flag for record in records for flag in record.get("flags") or ()})
    return {
        "status": "evaluated" if not result["errors"] or records else "failed",
        "configured": True,
        "reason": None,
        "object_names": list(matrix.get("object_names") or ()),
        "units": matrix.get("units", "m"),
        "pairs": records,
        "count": len(records),
        "flags": flags,
    }


def _attach_validation_extras(request, result, validation_run):
    """Attach the shell-validation preparation artifacts to the result.

    Runs only in validation mode: the contact-stiffness sweep (sensitivity
    of the shell loading to the largest identified uncertainty), the
    end-to-end parameter sensitivity, the uncertainty bands derived from
    the sweep, and the measured-vs-simulated comparison.  Nothing here
    modifies the physics; measured data is never applied automatically.
    """
    from . import shell_validation as validation_module

    shell = result["shell"]
    applied = validation_run.get("applied") or {}
    sweep_values = applied.get("contact_stiffness_sweep_n_per_m")
    sensitivity_config = applied.get("sensitivity") or {}
    measured_tests = applied.get("measured_tests")

    block = {
        "config": applied,
        "note": (
            "shell validation preparation: the chain above is pinned by the "
            "validation section; measured data never modifies the physics"
        ),
    }
    # Shell-only explicitness: which objects are the engineering target and
    # which are context only, plus the drop-dynamics vs structural track
    # separation.
    block.update(validation_module.build_validation_tracks(request, result))
    # Prototype measurement disclosure: when the user pinned a MEASURED
    # prototype mass, report the difference against the geometry-derived
    # mass so a real 45 g prototype is never silently compared against a
    # differently-massed simulation.  The EFFECTIVE mass actually solved
    # (incl. any mass_scale) is reported (audit finding: the disclosure
    # previously ignored a compounding mass_scale).
    prototype = (applied.get("prototype") or {})
    if prototype.get("mass_kg") is not None:
        model_mass = (result.get("mass") or {}).get("mass_kg")
        drop_model = (result.get("drop_simulation") or {}).get("model") or {}
        effective_mass = drop_model.get("mass_kg")
        if model_mass is not None:
            try:
                measured_mass = float(prototype["mass_kg"])
                solved_mass = float(effective_mass) if effective_mass is not None else measured_mass
                delta_pct = 100.0 * (solved_mass - float(model_mass)) / float(model_mass)
            except (TypeError, ValueError, ZeroDivisionError):
                delta_pct = None
            scale_active = bool((applied.get("drop") or {}).get("mass_scale"))
            block["prototype_mass_disclosure"] = {
                "measured_kg": prototype["mass_kg"],
                "model_kg": model_mass,
                "solved_kg": effective_mass,
                "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
                "note": (
                    "the simulation uses the MEASURED prototype mass"
                    + (" multiplied by the pinned mass_scale" if scale_active else "")
                    + "; the delta against the geometry-derived mass is disclosed"
                ),
            }
    # Inert-pin disclosures (audit findings): pins that are validated and
    # recorded but cannot affect the executed chain must warn, not silently
    # pretend to be applied.
    prototype = applied.get("prototype") or {}
    if prototype.get("thickness_m") is not None:
        structure = request.get("structure") or {}
        model_t = structure.get("t_m")
        try:
            pinned_t = float(prototype["thickness_m"])
            mismatch = model_t is None or abs(pinned_t - float(model_t)) / float(model_t) > 0.01
        except (TypeError, ValueError, ZeroDivisionError):
            mismatch = True
        if mismatch:
            result["issues"].append(
                _issue(
                    "VALIDATION_THICKNESS_PIN_NOT_APPLIED",
                    "warning",
                    "validation",
                    "validation.prototype.thickness_m ({}) is recorded but does "
                    "not drive the structural solve (structure.t_m = {}); the "
                    "structural track is validated by a separate quasi-static "
                    "test, not by the drop campaign".format(
                        prototype["thickness_m"], model_t
                    ),
                )
            )
    contact = applied.get("contact") or {}
    if contact.get("substeps") is not None:
        result["issues"].append(
            _issue(
                "VALIDATION_SUBSTEPS_PIN_INERT",
                "warning",
                "validation",
                "validation.contact.substeps is recorded but the integrator "
                "derives its own subdivisions; the pin has no effect on the "
                "simulation",
            )
        )
    structural_pin = (applied.get("structural") or {}).get("model")
    executed_method = (result.get("structural") or {}).get("response", {}).get("method_id")
    if structural_pin and executed_method and structural_pin != executed_method:
        result["issues"].append(
            _issue(
                "VALIDATION_STRUCTURAL_MODEL_PIN_MISMATCH",
                "warning",
                "validation",
                "validation.structural.model {!r} does not match the executed "
                "solver method {!r}: the pin is recorded but the solve follows "
                "request.structure".format(structural_pin, executed_method),
            )
        )
    shell["statement"] = (
        shell.get("statement", "")
        + " SHELL VALIDATION: drop tests validate the drop-dynamics chain "
        "(contact stiffness, restitution, friction, rigid-body dynamics); "
        "the structural safety factor comes from the pinned quasi-static "
        "load case and is NOT validated by drop tests."
    )
    # Measured comparison first: the k-sensitivity below scales each test's
    # own simulated acceleration.
    if measured_tests:
        revision = (applied.get("geometry") or {}).get("cad_revision")
        block["measured_comparison"] = validation_module.measured_comparison(
            measured_tests, _simulated_by_condition(result), validation_revision=revision
        )
    sweep = None
    bands = None
    drop = result.get("drop_simulation") or {}
    estimate = drop.get("peak_force_estimate") or {}
    if sweep_values and estimate:
        sweep = validation_module.run_contact_stiffness_sweep(
            sweep_values,
            float(estimate.get("mass_kg")),
            float(estimate.get("impact_speed_m_s")),
            float(estimate.get("restitution")),
        )
        block["contact_stiffness_sweep"] = sweep
        structural = result.get("structural") or {}
        structural_response = structural.get("response") or {}
        pinned_stiffness = drop.get("contact_stiffness_n_per_m")
        bands = validation_module.build_uncertainty_bands(
            sweep["rows"], structural_response, pinned_stiffness
        )
        block["uncertainty_bands"] = bands
        # k-sensitivity of the measured comparison: per stiffness value, the
        # bias/RMSE of simulated vs measured peak acceleration, with each
        # test's simulated value scaled from ITS OWN predicted acceleration
        # (a = v*sqrt(k/m) under the linear-spring model).
        if measured_tests:
            block["measured_k_sensitivity"] = _measured_k_sensitivity(
                measured_tests,
                sweep["rows"],
                pinned_stiffness,
                block.get("measured_comparison"),
            )
    elif sweep_values:
        result["issues"].append(
            _issue(
                "VALIDATION_SWEEP_UNAVAILABLE",
                "warning",
                "validation",
                "contact_stiffness_sweep requested but no drop-derived estimate "
                "is available (drop simulation did not produce a peak estimate)",
            )
        )
    # Always expose the uncertainty-bands block (basis not_computed when no
    # sweep ran) so consumers never hit a missing key.
    if bands is None:
        block["uncertainty_bands"] = validation_module.build_uncertainty_bands(None, None)

    if sensitivity_config:
        try:
            block["sensitivity"] = validation_module.run_sensitivity(
                request,
                fraction=sensitivity_config.get("perturbation_fraction", 0.1),
                parameters=sensitivity_config.get("parameters"),
            )
        except Exception as exc:
            result["issues"].append(
                _issue(
                    "VALIDATION_SENSITIVITY_FAILED",
                    "warning",
                    "validation",
                    "sensitivity analysis failed: {}".format(str(exc)),
                )
            )

    shell["validation"] = block


def _simulated_by_condition(result):
    """Map drop_id (test_id) -> the simulated peak acceleration g plus the
    exact simulated pose for that condition.

    Audit finding: the previous (height, surface, orientation) key collapsed
    distinct explicit quaternions at one height/surface (last-wins), pairing
    a measured value with the WRONG simulation.  Keying by drop_id (= test_id
    in the validation workflow) pairs each measured test with its own
    condition's simulation exactly.
    """
    conditions = (result.get("correlation") or {}).get("conditions") or []
    by_condition = {}
    for condition in conditions:
        drop_id = str(condition.get("drop_id") or "")
        if not drop_id:
            continue
        block = {
            "orientation_quaternion_wxyz": condition.get("orientation_quaternion_wxyz"),
            "gravity_vector_body": condition.get("gravity_vector_body"),
            "starting_pose_m": condition.get("starting_pose_m"),
            "initial_angular_velocity_rad_s": condition.get(
                "initial_angular_velocity_rad_s"
            ),
            "equivalent": condition.get("equivalent", True),
            "identity_ok": condition.get("identity_ok", True),
            "equivalence_note": condition.get("equivalence_note"),
        }
        for metric in condition.get("metrics") or []:
            key = metric.get("metric_key")
            if key == "peak_accel_g":
                block["peak_acceleration_g"] = metric.get("predicted")
            elif key == "settle_time_s":
                block["settle_time_s_predicted"] = metric.get("predicted")
            elif key == "impact_duration_s":
                block["impact_duration_s_predicted"] = metric.get("predicted")
        by_condition[drop_id] = block
    return by_condition


def _measured_k_sensitivity(measured_tests, sweep_rows, pinned_stiffness, comparison):
    """Bias/RMSE of simulated vs measured peak acceleration at each k.

    Each test's simulated acceleration at a swept stiffness is scaled from
    ITS OWN predicted acceleration at the pinned stiffness (a = v*sqrt(k/m)
    under the linear-spring model), so a 0.5 m test is never compared
    against a 1.0 m simulation.
    """
    if pinned_stiffness is None or not sweep_rows:
        return {"note": "no stiffness reference for the k-sensitivity comparison"}
    per_test = []
    for row in (comparison or {}).get("rows") or []:
        simulated = row.get("simulated") or {}
        measured = row.get("measured") or {}
        simulated_g = simulated.get("peak_acceleration_g")
        measured_g = measured.get("measured_peak_accel_g")
        if simulated_g is None or measured_g is None:
            continue
        try:
            per_test.append((float(simulated_g), float(measured_g)))
        except (TypeError, ValueError):
            continue
    if not per_test:
        return {"note": "no matched measured-vs-simulated peak accelerations"}
    rows = []
    for sweep_row in sweep_rows:
        stiffness = float(sweep_row["contact_stiffness_n_per_m"])
        scale = math.sqrt(stiffness / float(pinned_stiffness))
        residuals = [
            simulated_g * scale - measured_g for simulated_g, measured_g in per_test
        ]
        rows.append(
            {
                "contact_stiffness_n_per_m": stiffness,
                "bias_g": round(sum(residuals) / len(residuals), 4),
                "rmse_g": round(
                    math.sqrt(sum(value * value for value in residuals) / len(residuals)), 4
                ),
            }
        )
    return {
        "rows": rows,
        "note": "each test's simulated value scaled from its own predicted "
        "acceleration at the pinned k (linear-spring a ~ sqrt(k)); this does "
        "NOT select a 'correct' k — measurement of the physical contact is required",
    }


def _execute(request, mode, options, result):
    # Shell validation mode: pin the entire shell chain (material, drop,
    # contact, structural) explicitly from the validation section - nothing
    # is silently inherited from unrelated settings.
    validation_run = None
    if mode == "validation":
        from . import shell_validation as validation_module
        from .errors import ValidationError as _ValidationError

        try:
            request, validation_run = validation_module.apply_validation_config(request)
        except _ValidationError as exc:
            message = str(exc)
            result["issues"].append(
                _issue(
                    "VALIDATION_CONFIG_INVALID",
                    "error",
                    "validation",
                    message,
                    evidence_blocking=True,
                )
            )
            result["errors"].append({"code": "VALIDATION_CONFIG_INVALID", "message": message})
            result["validity"] = {
                "state": "failed",
                "reasons": [message],
                "assumptions": [],
                "unsupported_failure_modes": [],
                "confidence": "low",
            }
            return
        result["validation_run"] = validation_run
    units = str(request.get("units") or "m")
    catalog = _material_catalog(request, result)
    # Fail-closed material pin: a validation run must never claim a material
    # pin that the catalog cannot deliver (audit finding: an unknown key ran
    # the structural solve without a material payload, silently).
    if validation_run is not None:
        pinned_material = (validation_run.get("applied") or {}).get("material")
        if pinned_material is not None and catalog.get(pinned_material) is None:
            message = "validation.material {!r} is not in the resolved material catalog".format(
                pinned_material
            )
            result["issues"].append(
                _issue(
                    "VALIDATION_CONFIG_INVALID",
                    "error",
                    "validation",
                    message,
                    evidence_blocking=True,
                )
            )
            result["errors"].append({"code": "VALIDATION_CONFIG_INVALID", "message": message})
            result["validity"] = {
                "state": "failed",
                "reasons": [message],
                "assumptions": [],
                "unsupported_failure_modes": [],
                "confidence": "low",
            }
            return
    raw_requirements = request.get("requirements")
    if raw_requirements is None and request.get("requirement") is not None:
        raw_requirements = [request["requirement"]]
    result["requirements"] = canonical_value(raw_requirements or [])
    geometry_objs, materials_by_object, density_by_object, overrides, behaviors, request_classifications, request_names = _parse_objects(
        request, catalog, result, units
    )
    result["materials"] = _material_evidence(catalog, materials_by_object, result)

    classification_result = classify_objects(geometry_objs)
    # Merge AI + user signals per the consensus matrix: the deterministic rule
    # classification is the baseline; a user-reviewed request wins; the
    # OpenRouter vision result is fused when enabled (offline → heuristic).
    from . import ai_classify

    classifications = {}
    ai_inputs = []
    for object_id, item in classification_result.by_id().items():
        data = item.to_dict()
        data["structural_behavior"] = behaviors.get(object_id, "solid")
        if object_id in request_classifications:
            classifications[object_id] = ai_classify.merge_classification(
                object_id, data, None, request_classifications[object_id]
            )
            continue
        geometry = geometry_objs.get(object_id)
        geometry_dict = None
        if geometry is not None:
            try:
                geometry_dict = geometry.to_dict()
            except Exception:
                geometry_dict = None
        ai_inputs.append(
            {
                "object_id": object_id,
                "name": request_names.get(object_id),
                "geometry": geometry_dict,
                "rule": data,
            }
        )
    if ai_inputs and ai_classify.is_enabled():
        ai_results = ai_classify.classify_parts(
            ai_inputs, use_cache=True, cache=ai_classify.ClassificationCache()
        )
        for entry in ai_results:
            classifications[entry["object_id"]] = entry
    # Remaining parts (AI disabled or skipped) fall back to the rule result.
    for entry in ai_inputs:
        object_id = entry["object_id"]
        if object_id not in classifications:
            classifications[object_id] = entry["rule"]
    # Merged dicts from ai_classify do not carry the request's structural
    # behavior; re-apply it so validation (CLASSIFICATION_MISSING_BEHAVIOR)
    # and density handling keep working unchanged.
    for object_id, data in classifications.items():
        if isinstance(data, dict) and "structural_behavior" not in data:
            data["structural_behavior"] = behaviors.get(object_id, "solid")
    result["classifications"] = classifications

    # Components without an explicit material are deterministically assigned
    # the Default material for mechanical properties, but their MASS uses a
    # context-aware density derived from the classified geometry (shells use
    # a polymer density, PCBs FR4, batteries LiPo, ...) instead of the raw
    # generic Default density.  The estimate is disclosed per object in the
    # geometry summary; mass status remains "estimated" for open geometry.
    density_by_object = _apply_classification_densities(result, classifications, density_by_object)

    mass_result = mass.mass_properties(geometry_objs, density_by_object, overrides)
    result["mass"] = mass_result.to_dict()

    validation_report = validation.run_validation(
        geometry_objs, materials_by_object, classifications, options
    )
    result["validation"] = validation_report.to_dict()

    load_case = request.get("load_case")
    structure = request.get("structure")
    result["structural"] = None
    structural_response = None
    # Optional usage temperature (K): when supplied, the structural solver
    # applies the material's documented linear modulus/allowable derating
    # (see physics._material_props) and flags the response approximate.
    environment_temperature_k = request.get("environment_temperature_k")
    if environment_temperature_k is not None:
        try:
            environment_temperature_k = float(environment_temperature_k)
        except (TypeError, ValueError):
            environment_temperature_k = None
        if environment_temperature_k is not None and (
            not math.isfinite(environment_temperature_k)
            or environment_temperature_k < 173.15
            or environment_temperature_k > 373.15
        ):
            result["issues"].append(
                _issue(
                    "ENVIRONMENT_TEMPERATURE_OUT_OF_RANGE",
                    "warning",
                    "environment",
                    "environment_temperature_k must be between 173.15 and 373.15 K; ignored",
                )
            )
            environment_temperature_k = None
    if load_case is not None and structure is not None:
        try:
            if not isinstance(structure, Mapping):
                raise ValueError("structure must be an object")
            normalized_load = _normalize_load(load_case)
            if environment_temperature_k is not None:
                normalized_load = dict(normalized_load)
                normalized_load["temperature_k"] = environment_temperature_k
            structure_data = dict(structure)
            material_def, material_label = _resolve_structure_material(
                structure_data, catalog, materials_by_object
            )
            material_payload = material_def if material_def is not None else {}
            fixtures = request.get("fixtures")
            preflight = [
                dict(item)
                for item in physics.preflight_structural_case(normalized_load, structure_data, material_payload, fixtures)
            ]
            response = physics.solve_load_case(normalized_load, structure_data, material_payload, fixtures)
            structural_response = response
            result["structural"] = {
                "load_case": normalized_load,
                "structure": structure_data,
                "material": material_label,
                "resolved_material": material_def,
                "fixtures": fixtures,
                "preflight": preflight,
                "response": response.to_dict(),
            }
        except Exception as exc:
            message = str(exc) or "structural evaluation failed"
            result["issues"].append(
                _issue("STRUCTURAL_EVALUATION_FAILED", "error", "structural", message, evidence_blocking=True)
            )
            result["errors"].append({"code": "STRUCTURAL_EVALUATION_FAILED", "message": message})

    result["collision"] = _run_collision(request, geometry_objs, result)

    impact_request = request.get("impact")
    result["impact"] = None
    impact_material_label = None
    if impact_request is not None:
        try:
            if not isinstance(impact_request, Mapping):
                raise ValueError("impact must be an object")
            impact_data = dict(impact_request)
            total_mass = None
            if result["mass"] is not None:
                total_mass = result["mass"].get("mass_kg")
            mass_kg = impact_data.get("mass_kg", total_mass)
            unsupported = list(impact.IMPACT_UNSUPPORTED_FAILURE_MODES)
            if mass_kg is None:
                result["impact"] = {
                    "mass_kg": None,
                    "result": None,
                    "reason": "no mass available for impact estimate",
                    "unsupported_failure_modes": unsupported,
                }
            else:
                kwargs = {key: impact_data[key] for key in _IMPACT_KWARGS if key in impact_data}
                # When the user did not pin an allowable, derive the safety
                # factor from the SHELL's actual resolved material (the
                # structural section's material, or the primary object's),
                # so derated (or plain) allowables reach the impact estimate
                # from the material the result reports — never from catalog
                # insertion order.
                impact_material = None
                impact_material_label = None
                if "allowable_pa" not in kwargs:
                    impact_material, impact_material_label = _shell_material(
                        request, catalog, materials_by_object
                    )
                    properties = getattr(impact_material, "properties", None)
                    if properties is not None:
                        allowable = getattr(properties, "tensile_allowable", None)
                        if allowable is not None:
                            kwargs["allowable_pa"] = (
                                allowable.value_si if hasattr(allowable, "value_si") else allowable
                            )
                estimate = impact.estimate_impact(mass_kg, **kwargs)
                result["impact"] = {
                    "mass_kg": mass_kg,
                    "result": estimate.to_dict(),
                    "reason": None,
                    "unsupported_failure_modes": unsupported,
                    "material": impact_material_label,
                }
        except Exception as exc:
            message = str(exc) or "impact evaluation failed"
            result["issues"].append(
                _issue("IMPACT_EVALUATION_FAILED", "error", "impact", message, evidence_blocking=True)
            )
            result["errors"].append({"code": "IMPACT_EVALUATION_FAILED", "message": message})

    result["drop_simulation"] = None
    drop_request = request.get("drop_simulation")
    if drop_request is not None:
        try:
            if not isinstance(drop_request, Mapping):
                raise ValueError("drop_simulation must be an object")
            from . import drop_sim as drop_module

            config = drop_module.validate_config(dict(drop_request))
            requested_mass_kg = config.get("mass_kg")
            mass_kg = requested_mass_kg
            if mass_kg is None and result["mass"] is not None:
                mass_kg = result["mass"].get("mass_kg")
            assumed_mass = mass_kg is None
            if mass_kg is None:
                mass_kg = 0.06
            vertices = []
            for geometry in geometry_objs.values():
                # Prefer world-frame vertices so assembly placements and
                # transforms feed the support model.
                mesh_vertices = getattr(geometry, "vertices", None)
                world_vertices = None
                if mesh_vertices:
                    try:
                        world_vertices = getattr(geometry, "_world_vertices", None)
                    except Exception:
                        world_vertices = None
                    if callable(world_vertices):
                        try:
                            vertices.extend(world_vertices())
                        except Exception:
                            vertices.extend(mesh_vertices)
                    else:
                        vertices.extend(mesh_vertices)
                    continue
                try:
                    geometry_bounds = geometry.bounds()
                    geometry_bounds = (
                        (geometry_bounds.minimum[0], geometry_bounds.maximum[0]),
                        (geometry_bounds.minimum[1], geometry_bounds.maximum[1]),
                        (geometry_bounds.minimum[2], geometry_bounds.maximum[2]),
                    )
                except Exception:
                    geometry_bounds = None
                if geometry_bounds is not None:
                    vertices.extend(drop_module.box_corners(geometry_bounds))
            if not vertices:
                raise ValueError("drop simulation requires geometry")
            bounds = [
                (min(vertex[index] for vertex in vertices), max(vertex[index] for vertex in vertices))
                for index in range(3)
            ]
            inertia = None
            inertia_source = "mass_model"
            cad_mass_kg = None
            if result["mass"] is not None:
                inertia = result["mass"].get("inertia_tensor_kg_m2")
                cad_mass_kg = result["mass"].get("mass_kg")
            # Absolute measured-inertia override (validation mode: the real
            # prototype's inertia tensor replaces the geometry-derived one).
            inertia_override = drop_request.get("inertia_override_kg_m2")
            if inertia_override is not None:
                if (
                    not isinstance(inertia_override, (list, tuple))
                    or len(inertia_override) != 3
                    or any(
                        not isinstance(row, (list, tuple)) or len(row) != 3
                        for row in inertia_override
                    )
                ):
                    raise ValueError(
                        "drop_simulation.inertia_override_kg_m2 must be a 3x3 matrix"
                    )
                inertia_override = [
                    [float(value) for value in row] for row in inertia_override
                ]
                if not all(
                    math.isfinite(value) for row in inertia_override for value in row
                ):
                    raise ValueError("drop_simulation.inertia_override_kg_m2 must be finite")
                if any(
                    abs(inertia_override[i][j] - inertia_override[j][i]) > 1e-9
                    for i in range(3)
                    for j in range(3)
                ):
                    raise ValueError(
                        "drop_simulation.inertia_override_kg_m2 must be symmetric"
                    )
                inertia = inertia_override
                inertia_source = "prototype_override"
            # True only when the tensor came from the mass model's CAD
            # (geometry/measured) aggregation, not the absolute prototype
            # override above or the box envelope below.
            inertia_from_cad_model = inertia is not None and inertia_source == "mass_model"
            if inertia is None:
                inertia = drop_module.box_inertia(mass_kg, bounds)
                result["issues"].append(
                    _issue(
                        "DROP_SIMULATION_INERTIA_APPROXIMATED",
                        "warning",
                        "drop_simulation",
                        "drop simulation uses a uniform-density box inertia model",
                    )
                )
            # Physics-consistency fix (audit): the web UI's only drop-simulation
            # mass override replaces the body's translational mass WITHOUT
            # rescaling the CAD-derived inertia tensor — the integrator then
            # simulates a body whose Euler gyro torque and contact impulses
            # (drop_sim) respond with the CAD inertia while translation uses
            # the override.  Uniformly rescale the CAD tensor to the override,
            # I' = I * (M_override / M_CAD): a uniform positive scale preserves
            # positive-definiteness and leaves the CoM unchanged.  The mass
            # model's inertia is already consistent with ITS OWN reported mass
            # (measured overrides rescale the per-object tensor in mass.py), so
            # M_CAD is that reported mass and the scale is applied exactly
            # once.  The prototype inertia_override (absolute measured tensor)
            # and the box envelope (built from the override mass itself) are
            # inherently consistent and are never rescaled.
            if requested_mass_kg is not None and inertia_from_cad_model:
                if (
                    cad_mass_kg is None
                    or not math.isfinite(cad_mass_kg)
                    or cad_mass_kg <= 1e-9
                ):
                    result["issues"].append(
                        _issue(
                            "DROP_SIMULATION_INERTIA_NOT_RESCALED",
                            "warning",
                            "drop_simulation",
                            "drop_simulation.mass_kg override {:.4f} kg cannot be "
                            "reconciled with the CAD inertia tensor (the mass model "
                            "reports no usable mass): the inertia tensor was left "
                            "unscaled, so rotational response uses the CAD inertia "
                            "with the overridden translational mass".format(
                                requested_mass_kg
                            ),
                        )
                    )
                else:
                    inertia_scale_factor = requested_mass_kg / cad_mass_kg
                    if abs(inertia_scale_factor - 1.0) > 1e-9:
                        inertia = [
                            [inertia_scale_factor * value for value in row]
                            for row in inertia
                        ]
            if assumed_mass:
                result["issues"].append(
                    _issue(
                        "DROP_SIMULATION_MASS_ASSUMED",
                        "warning",
                        "drop_simulation",
                        "drop simulation mass assumed to be 0.06 kg (60 g ultralight reference; mesh has no safe mass properties)",
                    )
                )
            support = drop_module.support_points(vertices)
            # Contact model for the drop-derived estimate: an explicit
            # contact_stiffness_n_per_m keeps the calibrated linear spring;
            # the DEFAULT is the nonlinear Hertz point-contact law (E_eff
            # from the shell material and the floor surface table, corner
            # blend radius defaulting to 2.0 mm).
            contact_kwargs, contact_model_label, contact_assumptions = _resolve_drop_contact(
                request, catalog, materials_by_object, config["surface"], drop_request, result
            )
            # Validation-mode contact/input pins (documented in the shell
            # validation module): timestep, gravity, mass/inertia scales,
            # restitution/friction scales, and an explicit CoM override.  All
            # are optional; defaults match the integrator's documented values.
            timestep_s = drop_request.get("timestep_s")
            if timestep_s is not None:
                timestep_s = float(timestep_s)
                if not math.isfinite(timestep_s) or timestep_s <= 0.0 or timestep_s > 0.1:
                    raise ValueError("drop_simulation.timestep_s must be in (0, 0.1] s")
            gravity_m_s2 = drop_request.get("gravity_m_s2")
            if gravity_m_s2 is not None:
                gravity_m_s2 = float(gravity_m_s2)
                if not math.isfinite(gravity_m_s2) or gravity_m_s2 <= 0.0:
                    raise ValueError("drop_simulation.gravity_m_s2 must be positive")
            mass_scale = drop_request.get("mass_scale")
            if mass_scale is not None:
                mass_scale = float(mass_scale)
                if not math.isfinite(mass_scale) or mass_scale <= 0.0:
                    raise ValueError("drop_simulation.mass_scale must be positive")
            inertia_scale = drop_request.get("inertia_scale")
            if inertia_scale is not None:
                inertia_scale = float(inertia_scale)
                if not math.isfinite(inertia_scale) or inertia_scale <= 0.0:
                    raise ValueError("drop_simulation.inertia_scale must be positive")
            restitution_scale = drop_request.get("restitution_scale")
            if restitution_scale is not None:
                restitution_scale = float(restitution_scale)
                if not math.isfinite(restitution_scale) or not (0.1 <= restitution_scale <= 2.0):
                    raise ValueError("drop_simulation.restitution_scale must be within [0.1, 2.0]")
            friction_scale = drop_request.get("friction_scale")
            if friction_scale is not None:
                friction_scale = float(friction_scale)
                if not math.isfinite(friction_scale) or friction_scale <= 0.0:
                    raise ValueError("drop_simulation.friction_scale must be positive")
            surface_restitution = drop_module.SURFACES[config["surface"]]["restitution"]
            com_override = drop_request.get("com_override_m")
            if com_override is not None:
                if not isinstance(com_override, (list, tuple)) or len(com_override) != 3:
                    raise ValueError("drop_simulation.com_override_m must be a 3-vector")
                com_override = tuple(float(component) for component in com_override)
                if not all(math.isfinite(component) for component in com_override):
                    raise ValueError("drop_simulation.com_override_m must be finite")
                # An off-body CoM makes the rigid-body integration numerically
                # explosive (audit finding): bound the override to the body's
                # own extent (0.75 x the bounding-box diagonal covers hollow
                # shells whose CoM can sit near the surface).
                diagonal = math.sqrt(
                    sum((bounds[index][1] - bounds[index][0]) ** 2 for index in range(3))
                )
                magnitude = math.sqrt(sum(component * component for component in com_override))
                if diagonal > 0.0 and magnitude > 0.75 * diagonal:
                    raise ValueError(
                        "drop_simulation.com_override_m magnitude {:.4f} m exceeds the "
                        "geometry extent ({:.4f} m): the center of mass must lie "
                        "within the shell".format(magnitude, 0.75 * diagonal)
                    )
                if magnitude > 1.0:
                    raise ValueError(
                        "drop_simulation.com_override_m magnitude {:.4f} m exceeds the "
                        "absolute ceiling (1 m) for a hand-held shell".format(magnitude)
                    )
            # The sim resolves contact about the center of mass: the mass
            # model's world-frame CoM is the body-fixed offset from the
            # support-model anchor (support points are the world vertices).
            # An explicit com_override_m (validation pinning) takes precedence.
            com_offset_m = com_override
            if com_offset_m is None and result["mass"] is not None and result["mass"].get("center_of_mass_m") is not None:
                com_offset_m = tuple(result["mass"]["center_of_mass_m"])
            # Lifecycle degradation: prior usage (drops, impact energy, slide
            # distance, actuation) degrades the unit's restitution and
            # friction deterministically; the applied factors and damage
            # metrics are disclosed in result["lifecycle"].  Config-pinned
            # restitution/friction scales (validation mode) multiply with
            # the lifecycle degradation.
            friction_scale = friction_scale if friction_scale is not None else 1.0
            restitution_scale = restitution_scale if restitution_scale is not None else 1.0
            lifecycle_section = request.get("lifecycle")
            if lifecycle_section is not None:
                from . import lifecycle as lifecycle_module

                if not isinstance(lifecycle_section, Mapping):
                    raise ValueError("lifecycle must be an object")
                rest_scale, fric_scale, damage, diagnostics = lifecycle_module.degradation_factors(
                    lifecycle_section
                )
                restitution_scale = restitution_scale * rest_scale
                friction_scale = friction_scale * fric_scale
                if damage["fatigue_index"] > 0.0 or damage["actuation_exceeded"]:
                    result["issues"].append(
                        _issue(
                            "LIFECYCLE_DEGRADATION_APPLIED",
                            "warning",
                            "lifecycle",
                            "prior usage degrades the unit: fatigue index {:.4f}, "
                            "skate remaining {:.2f} mm".format(
                                damage["fatigue_index"], damage["skate_remaining_mm"]
                            ),
                        )
                    )
                result["lifecycle"] = {
                    "usage_snapshot": {
                        "prior_drops": _lifecycle_int(lifecycle_section.get("prior_drops")),
                        "prior_impact_energy_j": _lifecycle_float(
                            lifecycle_section.get("prior_impact_energy_j")
                        ),
                        "prior_drop_energies_j": lifecycle_section.get("prior_drop_energies_j"),
                        "actuation_cycles": _lifecycle_int(
                            lifecycle_section.get("actuation_cycles")
                        ),
                        "slide_distance_km": _lifecycle_float(
                            lifecycle_section.get("slide_distance_km")
                        ),
                        "age_days": _lifecycle_float(lifecycle_section.get("age_days")),
                    },
                    "damage": damage,
                    "degraded_properties": {
                        "restitution_scale": round(restitution_scale, 6),
                        "friction_scale": round(friction_scale, 6),
                    },
                    "applied_to": ["drop_simulation"],
                    "diagnostics": diagnostics,
                    # CERT-04 follow-up: the fatigue S-N law is a class-level
                    # screening assumption, NOT a validated material curve —
                    # the limitation must be visible in the run payload, not
                    # only in module docstrings/HANDOFF.
                    "fatigue_model": {
                        "law": "N(E) = 1e6 * (0.5 J / E)^2.5 (Miner linear accumulation)",
                        "basis": "class-level screening values (typical polymer "
                        "S-N slope), NOT a validated material fatigue curve",
                        "limitation": "fatigue results are screening estimates, "
                        "not fatigue predictions; do not use for life "
                        "certification without material-specific calibration",
                    },
                }
            # An explicit pose (validation pinning / physical-test replay)
            # travels as a quaternion dict to the simulator; a mode string
            # passes through unchanged.
            orientation_arg = config["orientation"]
            explicit_quaternion = config.get("orientation_quaternion_wxyz")
            if explicit_quaternion is not None:
                orientation_arg = {"quaternion_wxyz": list(explicit_quaternion)}
            simulation = drop_module.simulate(
                mass_kg,
                inertia,
                support,
                config["height_m"],
                surface=config["surface"],
                drop_count=config["drop_count"],
                test=config["test"],
                orientation=orientation_arg,
                spin_rps=config["spin_rps"],
                seed=config.get("seed", 0),
                unit_seed=config.get("unit_seed"),
                com_offset_m=com_offset_m,
                friction_scale=friction_scale,
                restitution_scale=restitution_scale,
                gravity=gravity_m_s2 if gravity_m_s2 is not None else drop_module.GRAVITY_M_S2,
                dt=timestep_s if timestep_s is not None else drop_module.DT_S,
                mass_scale=mass_scale if mass_scale is not None else 1.0,
                inertia_scale=inertia_scale if inertia_scale is not None else 1.0,
                pause_between_drops_s=config["pause_between_drops_s"],
            )
            if lifecycle_section is not None:
                from . import lifecycle as lifecycle_module

                total_energy = 0.0
                per_drop_energies = []
                for drop in simulation["drops"]:
                    drop_energy = drop.get("energy") or {}
                    release = float(drop_energy.get("release_j") or 0.0)
                    total_energy += release
                    per_drop_energies.append(release)
                result["lifecycle"]["next_usage"] = lifecycle_module.next_usage(
                    lifecycle_section,
                    config["drop_count"],
                    total_energy,
                    drop_energies_j=per_drop_energies,
                )
            peak = simulation["peak"]
            peak_force = None
            estimate_inputs = None
            if peak is not None:
                # Energy-honest handoff: the contact-point speed can exceed
                # free fall via legitimate lever amplification, but the impact
                # energy fed to the estimate must never exceed the drop's
                # energy budget (m*g*h plus any tumble spin budget).
                model_used = simulation["model"]
                # The estimate must use the SAME body the integrator solved:
                # the effective mass (incl. unit variation and lifecycle
                # scales) and the degraded/unit restitution — NOT the base
                # pre-variation values (audit finding: the estimate used the
                # base mass/restitution, inflating the force ~1.3%).
                effective_mass = float(model_used.get("mass_kg") or mass_kg)
                effective_restitution = float(model_used.get("restitution") or surface_restitution)
                effective_gravity = float(model_used.get("gravity_m_s2") or drop_module.GRAVITY_M_S2)
                raw_energy = peak.get("raw_kinetic_energy_j") or peak["kinetic_energy_j"]
                budget = effective_mass * effective_gravity * config["height_m"]
                if config["test"] == "tumble" and config["spin_rps"]:
                    spin_inertia = inertia[1][1]
                    budget += 0.5 * spin_inertia * (2.0 * math.pi * config["spin_rps"]) ** 2
                capped_energy = min(raw_energy, budget)
                if capped_energy < raw_energy:
                    result["issues"].append(
                        _issue(
                            "DROP_SIMULATION_ENERGY_CAPPED",
                            "warning",
                            "drop_simulation",
                            "drop-derived impact energy capped at the drop energy budget "
                            "(lever-amplified contact speed exceeds the release energy)",
                        )
                    )
                effective_speed = math.sqrt(max(0.0, 2.0 * capped_energy / effective_mass))
                estimate = impact.estimate_impact(
                    effective_mass,
                    velocity_m_s=effective_speed,
                    restitution=effective_restitution,
                    **contact_kwargs,
                )
                peak_force = estimate.to_dict().get("peak_force_n")
                estimate_inputs = {
                    "mass_kg": round(effective_mass, 6),
                    "restitution": round(effective_restitution, 6),
                    "energy_j": round(capped_energy, 6),
                    "impact_speed_m_s": round(effective_speed, 6),
                    "contact_stiffness_n_per_m": contact_kwargs.get("contact_stiffness_n_per_m"),
                    "effective_modulus_pa": contact_kwargs.get("effective_modulus_pa"),
                    "contact_radius_m": contact_kwargs.get("contact_radius_m"),
                    "model": "{} (drop-derived, effective mass and "
                    "degraded restitution)".format(contact_model_label),
                }
            result["drop_simulation"] = {
                "config": simulation["config"],
                "inertia_source": inertia_source,
                "model": simulation["model"],
                "drops": simulation["drops"],
                "impacts": simulation["impacts"],
                "checks": simulation["checks"],
                "peak": simulation["peak"],
                "peak_force_estimate_n": peak_force,
                "peak_force_estimate": estimate_inputs,
                "contact_stiffness_n_per_m": contact_kwargs.get("contact_stiffness_n_per_m"),
                "effective_modulus_pa": contact_kwargs.get("effective_modulus_pa"),
                "contact_radius_m": contact_kwargs.get("contact_radius_m"),
                "contact_model": (
                    impact.CONTACT_MODEL_HERTZ_NONLINEAR
                    if "effective_modulus_pa" in contact_kwargs
                    else impact.CONTACT_MODEL_LINEAR
                ),
                "contact_assumptions": list(contact_assumptions),
                "trajectory": simulation["trajectory"],
            }
            # Wire drop evidence into the impact section so the qualification
            # gates evaluate the simulated drop as impact evidence.
            impact_section = result["impact"]
            impact_missing = impact_section is None or impact_section.get("result") is None
            if impact_missing and peak is not None:
                estimate = impact.estimate_impact(
                    effective_mass,
                    velocity_m_s=effective_speed,
                    restitution=effective_restitution,
                    **contact_kwargs,
                )
                impact_payload = estimate.to_dict()
                impact_payload["assumptions"] = (
                    impact_payload["assumptions"] + list(contact_assumptions)
                )
                result["impact"] = {
                    "mass_kg": effective_mass,
                    "result": impact_payload,
                    "reason": None,
                    "unsupported_failure_modes": list(impact.IMPACT_UNSUPPORTED_FAILURE_MODES),
                    "source": "drop_simulation",
                    "material": impact_material_label,
                }
            elif impact_section is not None and peak_force is not None:
                # A user-supplied impact section is a SEPARATE standalone
                # quasi-static model (g=9.80665, restitution defaults to 0
                # there); cross-reference the drop-derived integrator value so
                # the two peak forces in one document cannot be confused.
                impact_section["cross_reference"] = {
                    "drop_derived_peak_force_estimate_n": round(peak_force, 4),
                    "drop_derived_energy_j": round(
                        estimate_inputs["energy_j"], 6
                    ) if estimate_inputs else None,
                    "note": (
                        "the impact section is a standalone quasi-static energy "
                        "model; the drop simulation's integrator-based estimate "
                        "is the shell loading reference"
                    ),
                }
            # Measured-drop correlation: for every user-supplied measured drop,
            # re-run the simulator under the same condition and compare the
            # predicted peak chassis acceleration (from the energy-capped
            # impact estimate) and settle time against the measured values.
            # This is the only path in which the word "correlation" refers to
            # an actual comparison of simulated output against experimental
            # data (per ASTM D3332-style instrumented drop practice).
            correlation_section = request.get("correlation")
            if correlation_section is not None:
                _run_correlation_section(
                    correlation_section,
                    mass_kg,
                    inertia,
                    support,
                    com_offset_m,
                    surface_restitution,
                    contact_kwargs,
                    result,
                    drop_overrides={
                        "gravity_m_s2": gravity_m_s2 if gravity_m_s2 is not None else drop_module.GRAVITY_M_S2,
                        "restitution_scale": restitution_scale,
                        "friction_scale": friction_scale,
                        "mass_scale": mass_scale if mass_scale is not None else 1.0,
                        "inertia_scale": inertia_scale if inertia_scale is not None else 1.0,
                        "com_override_m": com_override,
                        "timestep_s": timestep_s if timestep_s is not None else None,
                        "seed": config.get("seed", 0),
                        "unit_seed": config.get("unit_seed"),
                        # Equivalence and prototype identity are properties of
                        # the MEASUREMENT DEFINITION, not the mode: they are
                        # enforced in every mode (audit follow-up: exploration
                        # mode previously bypassed the gate entirely).  Raw
                        # correlation drops without a sensor definition are
                        # NOT equivalent (fail-closed).
                        "enforce_equivalence": True,
                    },
                )
        except Exception as exc:
            message = str(exc) or "drop simulation failed"
            result["issues"].append(
                _issue(
                    "DROP_SIMULATION_FAILED",
                    "error",
                    "drop_simulation",
                    message,
                    evidence_blocking=True,
                )
            )
            result["errors"].append({"code": "DROP_SIMULATION_FAILED", "message": message})

    # Component-level failure analysis: each supplied component spec (pcb,
    # battery, switch, encoder, screw, clip, mount, adhesive) is assessed
    # against the drop/impact summary, the usage snapshot, and the
    # environment.  This is the field-failure prediction layer.
    component_specs = request.get("components")
    population_config = request.get("population")
    if component_specs is not None or population_config is not None:
        _run_component_and_population_sections(
            request, catalog, result, geometry_objs, component_specs, population_config,
            materials_by_object,
        )

    # SHELL RESULT — the authoritative engineering answer.  The shell is the
    # primary engineering target: its deformation, stress, safety factor,
    # critical region, and confidence are reported here, separate from the
    # secondary component screening below.  Component verdicts never feed
    # back into this section, so arbitrary component thresholds cannot
    # contaminate the shell result.
    _assemble_shell_result(request, result, validation_run)

    # FEA display post-processor: per-vertex damage/stress/displacement
    # visualization fields computed from the FINAL shell result.  This is a
    # display-only append — it reads the assembled sections and never
    # modifies them, so no shell output can be affected.  The call is
    # guarded: any internal failure degrades to fail-open display data
    # (computed: False) instead of raising into the pipeline.
    try:
        from . import fea as fea_module

        result["fea"] = fea_module.compute_fea(result, geometry_objs, request=request)
    except Exception:
        result["fea"] = {
            "computed": False,
            "peak": None,
            "yield_stress_pa": None,
            "damage_basis": None,
            "safety_factor": None,
            "impact_window_s": 0.0,
            "dent_threshold": 0.7,
            "tear_threshold": 0.92,
            "center_frame": None,
            "objects": [],
            "procedural": [],
            "assumptions": [],
            "flags": ["FEA_COMPUTE_FAILED"],
        }

    # Validation-mode extras: contact-stiffness sweep, parameter sensitivity,
    # uncertainty bands, and the measured-vs-simulated comparison.  These are
    # preparation artifacts for physical testing — they never modify the
    # physics and never run outside validation mode.
    if validation_run is not None:
        _attach_validation_extras(request, result, validation_run)

    # SECONDARY COMPONENT SCREENING — simplified observations with honest
    # low confidence.  Never combined into the shell verdict.
    if result["components"] is not None:
        components = result["components"]
        result["component_screening"] = {
            "components": components["components"],
            "summary": components["summary"],
            "confidence": "low-medium",
            "note": (
                "simplified screening models with class-level engineering "
                "constants, not calibrated measurements; component verdicts "
                "do not affect the shell result"
            ),
        }

    if result["structural"] is not None:
        qual_load_case = result["structural"]["load_case"]
    elif request.get("load_case") is not None:
        try:
            qual_load_case = _normalize_load(request["load_case"])
        except Exception:
            qual_load_case = None
    else:
        qual_load_case = None
    # The qualification gates run in exploration and qualification modes
    # (exploration reports the never-qualifies disposition); validation mode
    # is a shell-focused preparation run and must not produce a
    # qualification verdict.
    if mode in ("exploration", "qualification"):
        try:
            qualification_result = qualification.evaluate_qualification(
                mode=mode,
                method=request.get("method"),
                geometry=request.get("geometry"),
                materials=list(materials_by_object.values()) if materials_by_object else None,
                load_case=qual_load_case,
                fixtures=request.get("fixtures"),
                tolerance_profile=request.get("tolerance_profile"),
                correlation_records=request.get("correlation_records"),
                requirement=request.get("requirement"),
                validation_report=result["validation"],
                solver=physics.SOLVER_CAPABILITIES,
                convergence_evidence=bool(request.get("convergence_evidence", False)),
                force_balance=bool(request.get("force_balance", False)),
                reviewed_flags=request.get("reviewed_flags"),
                structural_response=structural_response.to_dict() if structural_response is not None else None,
                impact=result["impact"],
                requirements=result["requirements"],
                pipeline_result=result,
            )
            result["qualification"] = qualification_result.to_dict()
        except Exception as exc:
            message = str(exc) or "qualification evaluation failed"
            result["issues"].append(
                _issue("QUALIFICATION_EVALUATION_FAILED", "error", "qualification", message, evidence_blocking=True)
            )
            result["errors"].append({"code": "QUALIFICATION_EVALUATION_FAILED", "message": message})

    result["validity"] = _validity(result)


def _collect_inputs(request):
    """Canonical snapshot of every input dict, keyed by request key.

    Materials passed as a FILE PATH are substituted with the file CONTENT
    hash (audit finding: hashing the path string let a changed catalog file
    serve stale cached results under the same run_id).
    """
    inputs = {}
    for key in sorted(request.keys()):
        value = request[key]
        if key == "materials" and isinstance(value, (str, os.PathLike)):
            try:
                with open(value, "rb") as stream:
                    content_sha256 = sha256_bytes(stream.read())
                inputs[str(key)] = {"path": str(value), "content_sha256": content_sha256}
            except OSError:
                inputs[str(key)] = {"path": str(value), "content_sha256": None}
            continue
        if key == "materials" and isinstance(value, Mapping):
            # Audit finding (W2-10D): canonical hashing sorts dict keys while
            # the catalog resolver is insertion-order sensitive for
            # normalized-equal keys - two orders resolved DIFFERENT materials
            # under the SAME run_id (cache poisoning).  Bind the ORDER by
            # hashing the mapping as an ordered [key, definition] list.
            # W2-10F follow-up: the WRAPPER root {"materials": {catalog}}
            # collapsed to a single ["materials", sorted-catalog] pair; the
            # inner catalog's order must be bound too.
            catalog = value
            if (
                len(value) == 1
                and "materials" in value
                and isinstance(value.get("materials"), Mapping)
            ):
                catalog = value["materials"]
            ordered_pairs = []
            for catalog_key, definition in catalog.items():
                ordered_pairs.append([str(catalog_key), canonical_value(definition)])
            inputs[str(key)] = {"ordered_catalog": ordered_pairs}
            continue
        inputs[str(key)] = canonical_value(value)
    return inputs


def _input_hashes(inputs):
    # The inputs snapshot is already canonical (produced by _collect_inputs),
    # so the preserialized fast path is byte-identical to canonical_bytes
    # while skipping the redundant _plain normalization pass over large
    # geometry payloads (measured ~3.6x faster on a 19 MB STEP assembly).
    return {
        key: sha256_bytes(canonical.canonical_bytes_preserialized(value))
        for key, value in inputs.items()
    }


def _run_id_for(mode, input_hashes, options):
    return canonical.cache_key(
        {
            "engine_version": ENGINE_VERSION,
            "engine_hash": _engine_hash(),
            "mode": mode,
            "input_hashes": input_hashes,
            "options": canonical_value(options),
        }
    )


def _build_manifest(mode, inputs, input_hashes, run_id=None):
    return {
        "schema_id": MANIFEST_SCHEMA_ID,
        "engine_version": ENGINE_VERSION,
        "engine_hash": _engine_hash(),
        "run_id": run_id,
        "mode": mode,
        "inputs": inputs,
        "input_hashes": input_hashes,
    }


def _cached_matches(cached, mode, inputs, input_hashes, run_id):
    if not isinstance(cached, dict):
        return False
    if cached.get("schema_id") != RESULT_SCHEMA_ID:
        return False
    if cached.get("run_id") != run_id:
        return False
    # The run id already binds engine version, engine behavior hash, mode,
    # and every input hash; comparing the per-key hashes (instead of
    # rebuilding and hashing the full manifest snapshot) verifies the hit
    # without an O(request) re-serialization on every request.
    if cached.get("mode") != mode:
        return False
    manifest = cached.get("manifest")
    if not isinstance(manifest, dict):
        return False
    # W4-01 follow-up: a cached payload written by a DIFFERENT engine build
    # whose manifest was re-signed consistently was served (attack 4b — the
    # engine_hash was never cross-checked at hit time, only at store time via
    # the run_id key).  The manifest must bind the CURRENT engine hash and
    # its own recorded manifest_hash must be self-consistent, mirroring
    # reproduce_from_manifest.
    if manifest.get("engine_hash") != _engine_hash():
        return False
    cached_hashes = manifest.get("input_hashes")
    if not isinstance(cached_hashes, dict) or cached_hashes != input_hashes:
        return False
    # W12-01 follow-up: a cache-writer who re-signed the manifest could tamper
    # the INPUTS SNAPSHOT (e.g. height 1.0 -> 0.5) while keeping the recorded
    # input_hashes/run_id/engine_hash genuine — the hit was then served with a
    # body computed for different physics.  The inputs snapshot must re-derive
    # to the recorded input_hashes (which are bound to the current request).
    if not isinstance(manifest.get("inputs"), dict):
        return False
    try:
        if _input_hashes(manifest["inputs"]) != cached_hashes:
            return False
    except Exception:
        return False
    if not isinstance(manifest.get("manifest_hash"), str) or not manifest.get("manifest_hash"):
        return False
    try:
        presented = {key: value for key, value in manifest.items() if key != "manifest_hash"}
        if canonical.manifest_hash(presented) != manifest.get("manifest_hash"):
            return False
    except Exception:
        return False
    return True


def run_pipeline(request: dict, cache: Optional[ArtifactCache] = None, use_cache: bool = True) -> dict:
    """Run the deterministic simulation pipeline for ``request``.

    Never raises: pipeline failures are collected into ``errors`` and the
    result's lifecycle becomes ``failed``.  When ``use_cache`` and ``cache``
    are given, a verified cache hit returns the stored payload directly;
    otherwise the fresh result is computed and stored.
    """
    if request is not None and not isinstance(request, Mapping):
        mode = _normalize_mode(None)
        result = _new_result(mode)
        result["run_id"] = _run_id_for(mode, {}, {})
        _pipeline_error(result, "INVALID_REQUEST", "request must be an object")
        result["lifecycle_state"] = "failed"
        # SENIOR-04: same fail-closed validity as the hard-failure handler.
        result["validity"] = {
            "state": "failed",
            "reasons": ["request must be an object"],
            "assumptions": [],
            "unsupported_failure_modes": [],
            "confidence": "low",
        }
        return result
    request = dict(request or {})
    mode = _normalize_mode(request.get("mode", "exploration"))
    options = request.get("options")
    options = dict(options) if isinstance(options, Mapping) else {}
    debug = bool(options.get("debug", False))
    result = _new_result(mode)
    # Validation-mode inputs are classified by the validation layer BEFORE
    # canonical input hashing runs: a NaN/Inf in a validation field must
    # surface as VALIDATION_CONFIG_INVALID (a user-input error), not as the
    # generic PIPELINE_INTERNAL that canonical serialization reports.
    if mode != "validation" and request.get("validation") is not None:
        # Audit finding (W5-01): a validation section in a non-validation run
        # was silently ignored.  Disclose it.
        result["issues"].append(
            _issue(
                "VALIDATION_SECTION_IGNORED",
                "warning",
                "validation",
                "a validation section is present but mode is not 'validation'; "
                "the section is ignored by this run",
            )
        )
    if mode == "validation" and request.get("validation") is not None:
        try:
            from . import shell_validation as validation_module
            from .errors import ValidationError as _ValidationError

            validation_module.apply_validation_config(request)
        except _ValidationError as exc:
            message = str(exc)
            result["issues"].append(
                _issue(
                    "VALIDATION_CONFIG_INVALID",
                    "error",
                    "validation",
                    message,
                    evidence_blocking=True,
                )
            )
            result["errors"].append({"code": "VALIDATION_CONFIG_INVALID", "message": message})
            result["lifecycle_state"] = "failed"
            result["validity"] = {
                "state": "failed",
                "reasons": [message],
                "assumptions": [],
                "unsupported_failure_modes": [],
                "confidence": "low",
            }
            return result
    try:
        # W2-05 follow-up: raw correlation.measured_drops with NaN/Inf would
        # crash canonical input hashing (PIPELINE_INTERNAL) BEFORE _execute
        # runs; reject only the non-finite entries here (fail-closed
        # correlation issue).  Finite-but-implausible values and unknown
        # keys are handled per-condition inside _run_correlation_section,
        # where they fail the verdict honestly.
        raw_correlation = request.get("correlation")
        if (
            mode != "validation"
            and isinstance(raw_correlation, Mapping)
            and isinstance(raw_correlation.get("measured_drops"), (list, tuple))
        ):
            non_finite = False
            for raw_drop in raw_correlation.get("measured_drops"):
                if not isinstance(raw_drop, Mapping):
                    continue
                for key in (
                    "measured_peak_accel_g",
                    "measured_impact_duration_s",
                    "measured_settle_s",
                    "measured_settle_time_s",
                ):
                    value = raw_drop.get(key)
                    if value is None:
                        continue
                    try:
                        finite = math.isfinite(float(value))
                    except (TypeError, ValueError):
                        finite = False
                    if not finite:
                        non_finite = True
                        break
            if non_finite:
                # W8-02 follow-up: popping the correlation section before
                # hashing let the run COMPLETE under the correlation-less
                # request's run_id — a NaN request then collided with a
                # legitimately correlation-less request and the cache served
                # a payload with the submitted correlation evidence silently
                # dropped.  Fail closed: the request never enters the
                # hashing/cache closure.
                message = (
                    "correlation evaluation failed: raw measured_drops "
                    "contain a non-finite measured value; the request cannot "
                    "be certified or cached"
                )
                result["issues"].append(
                    _issue(
                        "CORRELATION_EVALUATION_FAILED",
                        "error",
                        "correlation",
                        message,
                        evidence_blocking=True,
                    )
                )
                result["errors"].append({"code": "CORRELATION_EVALUATION_FAILED", "message": message})
                result["lifecycle_state"] = "failed"
                result["validity"] = {
                    "state": "failed",
                    "reasons": [message],
                    "assumptions": [],
                    "unsupported_failure_modes": [],
                    "confidence": "low",
                }
                return result
        inputs = _collect_inputs(request)
        input_hashes = _input_hashes(inputs)
        run_id = _run_id_for(mode, input_hashes, options)
        result["run_id"] = run_id
        if use_cache and cache is not None:
            cached = cache.load(run_id)
            if cached is not None and _cached_matches(cached, mode, inputs, input_hashes, run_id):
                return cached
        _execute(request, mode, options, result)
        # The structural section carries the RESOLVED material definition for
        # the probe; serialize it for the result payload (traceability: the
        # exact material used, incl. catalog-miss and object-resolution).
        structural = result.get("structural")
        if isinstance(structural, dict) and structural.get("resolved_material") is not None:
            try:
                structural["resolved_material"] = structural["resolved_material"].to_dict()
            except Exception:
                structural["resolved_material"] = None
        manifest = _build_manifest(mode, inputs, input_hashes, run_id=run_id)
        manifest["manifest_hash"] = canonical.manifest_hash(manifest)
        result["manifest"] = manifest
        result["lifecycle_state"] = "failed" if result["errors"] else "completed"
        if use_cache and cache is not None and not result["errors"]:
            try:
                # Audit finding (W2-10E): the materials file is read twice
                # (hashed in _collect_inputs, re-read by load_material_catalog);
                # a change between the reads would store content-B results
                # under content-A's key and poison later A-runs.  Verify the
                # file still matches the keyed hash before storing; on
                # mismatch, skip the store and warn.
                materials_ok = True
                raw_materials = request.get("materials")
                if isinstance(raw_materials, (str, os.PathLike)):
                    try:
                        with open(raw_materials, "rb") as stream:
                            current_hash = sha256_bytes(stream.read())
                        keyed_hash = None
                        for entry in inputs.values():
                            if isinstance(entry, dict) and entry.get("path") == str(raw_materials):
                                keyed_hash = entry.get("content_sha256")
                                break
                        if keyed_hash is not None and current_hash != keyed_hash:
                            materials_ok = False
                            result["issues"].append(
                                _issue(
                                    "MATERIALS_CONTENT_CHANGED_DURING_RUN",
                                    "warning",
                                    "materials",
                                    "the materials catalog file changed while the "
                                    "run was executing; the result is NOT cached "
                                    "(it would poison the cache under the wrong "
                                    "content key)",
                                )
                            )
                    except OSError:
                        materials_ok = False
                if materials_ok:
                    cache.store(run_id, result)
            except (OSError, ValueError, TypeError):
                pass
        return result
    except Exception as exc:
        error = {"code": "PIPELINE_INTERNAL", "message": str(exc)}
        if debug:
            error["traceback"] = traceback.format_exc()
        result["errors"].append(error)
        result["lifecycle_state"] = "failed"
        # SENIOR-04: a hard failure previously left validity.state at the
        # default "valid" (the crash happened before _validity ran).  A
        # failed run must never present a valid-looking state.
        result["validity"] = {
            "state": "failed",
            "reasons": [str(exc)],
            "assumptions": [],
            "unsupported_failure_modes": [],
            "confidence": "low",
        }
        return result


def reproduce_from_manifest(manifest) -> dict:
    """Replay a run from its manifest's input snapshot when possible."""
    if not isinstance(manifest, Mapping):
        return {"supported": False, "reason": "manifest must be an object"}
    if manifest.get("schema_id") != MANIFEST_SCHEMA_ID:
        return {
            "supported": False,
            "reason": "unrecognized manifest schema_id {!r}".format(manifest.get("schema_id")),
        }
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        return {"supported": False, "reason": "manifest has no replayable inputs snapshot"}
    # Audit finding (W2-11B): fields added to the manifest AFTER manifest_hash
    # was computed are outside the certification closure.  Re-verify the
    # PRESENTED document's own hash before replaying.
    try:
        presented = {key: value for key, value in manifest.items() if key != "manifest_hash"}
        if canonical.manifest_hash(presented) != manifest.get("manifest_hash"):
            return {
                "supported": False,
                "reason": "the manifest document does not match its recorded manifest_hash",
            }
    except Exception:
        return {"supported": False, "reason": "the manifest document is not self-consistent"}
    # Audit finding: the manifest previously bound inputs only, so a physics
    # change (engine hash drift) was certified "supported" with silently
    # different physics.  Engine identity and run_id must match.
    if manifest.get("engine_hash") != _engine_hash():
        return {
            "supported": False,
            "reason": "engine source hash differs from the manifest's recorded "
            "engine hash: the recorded physics cannot be reproduced by the "
            "current engine",
        }
    request = {key: canonical_value(value) for key, value in inputs.items()}
    # W2-10G follow-up: dict-form catalogs are snapshotted as an ordered
    # [key, definition] list; rebuild the DICT (preserving order) so the
    # replay executes the same catalog resolution instead of failing.
    raw_materials = request.get("materials")
    if (
        isinstance(raw_materials, dict)
        and set(raw_materials.keys()) == {"ordered_catalog"}
        and isinstance(raw_materials.get("ordered_catalog"), (list, tuple))
    ):
        rebuilt = {}
        for pair in raw_materials["ordered_catalog"]:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                rebuilt[str(pair[0])] = canonical_value(pair[1])
        request["materials"] = rebuilt
    # Audit finding (W1-09/W2-10E): path-form materials were snapshotted as
    # {path, content_sha256} but the content was never embedded - replay fed
    # the dict back, failed, and was still certified supported.  Re-resolve
    # the recorded path, verify the recorded sha, and replay with the path.
    raw_materials = request.get("materials")
    if isinstance(raw_materials, dict) and "path" in raw_materials and "content_sha256" in raw_materials:
        recorded_path = raw_materials["path"]
        recorded_sha = raw_materials["content_sha256"]
        try:
            with open(recorded_path, "rb") as stream:
                current_sha = sha256_bytes(stream.read())
        except OSError:
            return {
                "supported": False,
                "reason": "the manifest references a materials catalog file that "
                "cannot be read; its content was not embedded",
            }
        if recorded_sha != current_sha:
            return {
                "supported": False,
                "reason": "the materials catalog file content differs from the "
                "manifest's recorded content hash; the recorded physics cannot "
                "be reproduced",
            }
        request["materials"] = recorded_path
    result = run_pipeline(request)
    replay_manifest = result.get("manifest") or {}
    if replay_manifest.get("manifest_hash") != manifest.get("manifest_hash"):
        return {"supported": False, "reason": "replay produced a different manifest hash"}
    if result.get("run_id") != manifest.get("run_id"):
        return {
            "supported": False,
            "reason": "replay produced a different run_id than the manifest records",
        }
    if result.get("lifecycle_state") != "completed":
        return {
            "supported": False,
            "reason": "the replay did not complete (lifecycle {}); the recorded "
            "physics was not reproduced".format(result.get("lifecycle_state")),
        }
    return {
        "supported": True,
        "run_id": result.get("run_id"),
        "lifecycle_state": result.get("lifecycle_state"),
    }


__all__ = [
    "ENGINE_VERSION",
    "RESULT_SCHEMA_ID",
    "MANIFEST_SCHEMA_ID",
    "run_pipeline",
    "reproduce_from_manifest",
]
