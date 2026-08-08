"""Deterministic, JSON-friendly mouse simulation pipeline orchestration.

``run_pipeline`` executes the standard analysis steps (material catalog,
geometry import, mass properties, DFM-lite validation, structural screening,
impact estimation, qualification gating) and packages the result into a
stable, JSON-serializable result with an immutable run manifest.  Pipeline
errors never raise: they are collected into the result's ``errors`` list and
the lifecycle is marked ``failed``.
"""

import math
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

# Modules whose source defines the engine's numerical behavior.  A change in
# any of them invalidates every cached run: the run id embeds a hash of these
# sources, so stale cache entries silently serving old physics are impossible.
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
    "lifecycle",
    "components_elec",
    "components_mech",
    "profiles",
    "population",
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
    return value if value in ("exploration", "qualification") else "exploration"


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
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0


def _lifecycle_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0.0:
        return 0.0
    return number


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


def _run_correlation_section(
    correlation_section,
    mass_kg,
    inertia,
    support,
    com_offset_m,
    surface_restitution,
    stiffness,
    result,
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
        conditions = []
        for raw_drop in measured_drops:
            if not isinstance(raw_drop, Mapping):
                continue
            drop_id = str(raw_drop.get("drop_id", "drop"))
            condition = {"drop_id": drop_id, "metrics": []}
            height = raw_drop.get("height_m")
            surface = str(raw_drop.get("surface", "concrete")).strip().lower()
            orientation = str(raw_drop.get("orientation", "flat")).strip().lower()
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
                measured_accel = raw_drop.get("measured_peak_accel_g")
                measured_settle = raw_drop.get("measured_settle_time_s")
                correlation_run = drop_module.simulate(
                    mass_kg,
                    inertia,
                    support,
                    correlation_config["height_m"],
                    surface=correlation_config["surface"],
                    drop_count=1,
                    test="drop",
                    orientation=correlation_config["orientation"],
                    seed=0,
                    com_offset_m=com_offset_m,
                )
                correlation_peak = correlation_run["peak"]
                predicted_accel_g = None
                if correlation_peak is not None:
                    raw_energy = correlation_peak.get("raw_kinetic_energy_j") or correlation_peak[
                        "kinetic_energy_j"
                    ]
                    budget = mass_kg * drop_module.GRAVITY_M_S2 * correlation_config["height_m"]
                    capped = min(raw_energy, budget)
                    speed = math.sqrt(max(0.0, 2.0 * capped / mass_kg))
                    estimate = impact.estimate_impact(
                        mass_kg,
                        velocity_m_s=speed,
                        restitution=surface_restitution,
                        contact_stiffness_n_per_m=stiffness,
                    )
                    predicted_accel_g = (
                        estimate.to_dict().get("peak_acceleration_m_s2", 0.0) / 9.80665
                    )
                predicted_settle = correlation_run["drops"][0]["settled_s"]
                if measured_accel is not None and predicted_accel_g is not None:
                    _append_correlation_metric(
                        condition, "peak_accel_g", measured_accel, predicted_accel_g, max_error
                    )
                if measured_settle is not None:
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
    request, catalog, result, geometry_objs, component_specs, population_config
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
        mass_kg = 0.1
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
    com_offset_m = None
    if result["mass"] is not None and result["mass"].get("center_of_mass_m") is not None:
        com_offset_m = tuple(result["mass"]["center_of_mass_m"])

    drop_summary = None
    drop_stiffness = 1e5
    drop_request = request.get("drop_simulation")
    if isinstance(drop_request, Mapping):
        requested_stiffness = drop_request.get("contact_stiffness_n_per_m")
        if requested_stiffness is not None:
            try:
                requested_stiffness = float(requested_stiffness)
                if math.isfinite(requested_stiffness) and requested_stiffness > 0.0:
                    drop_stiffness = requested_stiffness
            except (TypeError, ValueError):
                pass
    if drop_section is not None and drop_section.get("peak") is not None:
        peak = drop_section["peak"]
        drop_config = drop_section.get("config") or {}
        # The echoed config strips unknown keys, so the stiffness comes from
        # the ORIGINAL request (validate_config drops it from the echo).
        height = float(drop_config.get("height_m") or 0.75)
        capped_j = float(peak.get("kinetic_energy_j") or 0.0)
        if capped_j > 0.0 and mass_kg > 0.0:
            v_eff = math.sqrt(2.0 * capped_j / mass_kg)
        else:
            v_eff = math.sqrt(2.0 * drop_module.GRAVITY_M_S2 * height)
        surface = str(drop_config.get("surface") or "concrete").strip().lower()
        estimate = impact.estimate_impact(
            mass_kg,
            velocity_m_s=v_eff,
            restitution=drop_module.SURFACES[surface]["restitution"],
            contact_stiffness_n_per_m=drop_stiffness,
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
                result["population"] = population_module.run_population(population_settings, context)
            except Exception as exc:
                result["issues"].append(
                    _issue("POPULATION_ANALYSIS_FAILED", "warning", "population", str(exc))
                )


def _assemble_shell_result(request, result):
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
    mass_assumed = "DROP_SIMULATION_MASS_ASSUMED" in issue_codes
    inertia_approximated = "DROP_SIMULATION_INERTIA_APPROXIMATED" in issue_codes
    invalid_input = bool(result.get("errors")) or any(
        code in ("GEOMETRY_PARSE_FAILED", "GEOMETRY_MISSING", "INVALID_OBJECTS")
        for code in issue_codes
    )
    unsupported = response.get("unsupported_failure_modes") or []
    unsupported_flags = [
        flag for flag in (response.get("flags") or ()) if str(flag).startswith("UNSUPPORTED_")
    ]
    calibration = result.get("correlation")
    calibration_passed = (
        isinstance(calibration, dict) and calibration.get("verdict") == "pass"
    )
    if invalid_input:
        status = "not_evaluated"
        classification = "invalid_input"
    elif sf is None:
        status = "not_evaluated"
        classification = "insufficient_evidence"
    elif (mass_assumed or inertia_approximated) and sf is not None and sf >= 1.2:
        # The geometry could not certify a solid (mass/inertia assumed): the
        # pinned structural analysis is still shown, but the shell cannot be
        # declared SAFE — the physical object's drop-side behavior is
        # unverifiable.
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
    point_load_present = bool(
        (result.get("structural") or {}).get("preflight")
        and any(
            isinstance(item, dict) and item.get("code") == "POINT_LOAD_SINGULARITY"
            for item in (result.get("structural") or {}).get("preflight", [])
        )
    )
    if (
        validity == "valid"
        and not unsupported_flags
        and not mass_assumed
        and not inertia_approximated
        and not point_load_present
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
        limitations.append("mass assumed 0.1 kg (geometry mass properties unavailable)")
    if inertia_approximated:
        limitations.append("inertia approximated by a bounding box")
    if not calibration_passed:
        limitations.append(
            "no passed measured-drop correlation: the physical model is uncalibrated screening"
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
    material_payload = {}
    material_ref = structural.get("material")
    if material_ref is not None:
        try:
            catalog = materials.builtin_materials()
            material_payload = catalog.get(material_ref) or {}
        except Exception:
            material_payload = {}
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
    """Aggregate per-condition comparisons into the correlation verdict."""
    evaluated = [condition for condition in conditions if condition["metrics"]]
    all_metrics = [metric for condition in evaluated for metric in condition["metrics"]]
    evaluated_metric_count = len(all_metrics)
    failures = [
        metric for metric in all_metrics if not metric.get("pass", False)
    ]
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
    if len(evaluated) < min_conditions:
        reasons.append(
            "{} of {} required drop conditions evaluated".format(len(evaluated), min_conditions)
        )
    if failures:
        reasons.append("{} of {} metric comparisons exceeded the {:.0%} error bound".format(
            len(failures), evaluated_metric_count, max_error
        ))
    if r_squared is not None and r_squared < min_r_squared:
        reasons.append("R-squared {:.3f} below {:.2f}".format(r_squared, min_r_squared))
    if bias is not None and abs(bias) > max_bias:
        reasons.append("signed bias {:.3f} exceeds {:.3f}".format(bias, max_bias))
    if not evaluated:
        reasons.append("no measured drop conditions could be evaluated")
    verdict = "pass" if not reasons else "fail"
    return {
        "conditions": conditions,
        "max_relative_error": round(max_error, 6),
        "min_drop_conditions": min_conditions,
        "r_squared": round(r_squared, 6) if r_squared is not None else None,
        "bias": round(bias, 6) if bias is not None else None,
        "verdict": verdict,
        "explanation": "; ".join(reasons) if reasons else (
            "predicted vs measured drop response within acceptance"
        ),
    }


def _normalize_load(load):
    """Materialize magnitude_pa/force_n from a ``magnitude``/``force`` input."""
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
    force = result.get("force")
    if force is not None:
        if isinstance(force, Mapping) and "unit" in force:
            result["force_n"] = to_si(force.get("value", 0.0), force["unit"], expected_dimension="force")
        elif isinstance(force, Mapping):
            result["force_n"] = float(force.get("value", 0.0))
        else:
            result["force_n"] = float(force)
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


def _parse_objects(request, catalog, result, units):
    geometry_objs = {}
    materials_by_object = {}
    density_by_object = {}
    overrides = {}
    behaviors = {}
    if "objects" in request and not isinstance(request.get("objects"), (Mapping, list, tuple)):
        _pipeline_error(result, "INVALID_OBJECTS", "objects must be an object or array")
        return geometry_objs, materials_by_object, density_by_object, overrides, behaviors
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
        return geometry_objs, materials_by_object, density_by_object, overrides, behaviors
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
                geometry = importers.geometry_from_dict(geometry_data, units=units)
            except Exception as exc:
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
    return geometry_objs, materials_by_object, density_by_object, overrides, behaviors


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
        confidence = "high" if analysis_evidence else "medium"
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


def _execute(request, mode, options, result):
    units = str(request.get("units") or "m")
    catalog = _material_catalog(request, result)
    raw_requirements = request.get("requirements")
    if raw_requirements is None and request.get("requirement") is not None:
        raw_requirements = [request["requirement"]]
    result["requirements"] = canonical_value(raw_requirements or [])
    geometry_objs, materials_by_object, density_by_object, overrides, behaviors = _parse_objects(
        request, catalog, result, units
    )
    result["materials"] = _material_evidence(catalog, materials_by_object, result)

    mass_result = mass.mass_properties(geometry_objs, density_by_object, overrides)
    result["mass"] = mass_result.to_dict()

    classification_result = classify_objects(geometry_objs)
    classifications = {}
    for object_id, item in classification_result.by_id().items():
        data = item.to_dict()
        data["structural_behavior"] = behaviors.get(object_id, "solid")
        classifications[object_id] = data

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
            material_ref = structure_data.get("material")
            material_def = catalog.get(material_ref) if material_ref is not None else _first_material(catalog)
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
                "material": material_ref,
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
                # factor from the assembly's primary material so derated (or
                # plain) allowables actually reach the impact estimate
                # instead of reporting not_available.
                if "allowable_pa" not in kwargs:
                    primary = _first_material(catalog)
                    properties = getattr(primary, "properties", None)
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
            mass_kg = config.get("mass_kg")
            if mass_kg is None and result["mass"] is not None:
                mass_kg = result["mass"].get("mass_kg")
            assumed_mass = mass_kg is None
            if mass_kg is None:
                mass_kg = 0.1
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
            if result["mass"] is not None:
                inertia = result["mass"].get("inertia_tensor_kg_m2")
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
            if assumed_mass:
                result["issues"].append(
                    _issue(
                        "DROP_SIMULATION_MASS_ASSUMED",
                        "warning",
                        "drop_simulation",
                        "drop simulation mass assumed to be 0.1 kg (mesh has no safe mass properties)",
                    )
                )
            support = drop_module.support_points(vertices)
            stiffness = float(drop_request.get("contact_stiffness_n_per_m", 1e5))
            if not math.isfinite(stiffness) or stiffness <= 0.0:
                raise ValueError("drop_simulation.contact_stiffness_n_per_m must be positive")
            surface_restitution = drop_module.SURFACES[config["surface"]]["restitution"]
            # The sim resolves contact about the center of mass: the mass
            # model's world-frame CoM is the body-fixed offset from the
            # support-model anchor (support points are the world vertices).
            com_offset_m = None
            if result["mass"] is not None and result["mass"].get("center_of_mass_m") is not None:
                com_offset_m = tuple(result["mass"]["center_of_mass_m"])
            # Lifecycle degradation: prior usage (drops, impact energy, slide
            # distance, actuation) degrades the unit's restitution and
            # friction deterministically; the applied factors and damage
            # metrics are disclosed in result["lifecycle"].
            friction_scale = 1.0
            restitution_scale = 1.0
            lifecycle_section = request.get("lifecycle")
            if lifecycle_section is not None:
                from . import lifecycle as lifecycle_module

                if not isinstance(lifecycle_section, Mapping):
                    raise ValueError("lifecycle must be an object")
                rest_scale, fric_scale, damage, diagnostics = lifecycle_module.degradation_factors(
                    lifecycle_section
                )
                restitution_scale = rest_scale
                friction_scale = fric_scale
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
                }
            simulation = drop_module.simulate(
                mass_kg,
                inertia,
                support,
                config["height_m"],
                surface=config["surface"],
                drop_count=config["drop_count"],
                test=config["test"],
                orientation=config["orientation"],
                spin_rps=config["spin_rps"],
                seed=config.get("seed", 0),
                unit_seed=config.get("unit_seed"),
                com_offset_m=com_offset_m,
                friction_scale=friction_scale,
                restitution_scale=restitution_scale,
            )
            if lifecycle_section is not None:
                from . import lifecycle as lifecycle_module

                total_energy = 0.0
                for drop in simulation["drops"]:
                    drop_energy = drop.get("energy") or {}
                    total_energy += float(drop_energy.get("release_j") or 0.0)
                result["lifecycle"]["next_usage"] = lifecycle_module.next_usage(
                    lifecycle_section,
                    config["drop_count"],
                    total_energy,
                )
            peak = simulation["peak"]
            peak_force = None
            if peak is not None:
                # Energy-honest handoff: the contact-point speed can exceed
                # free fall via legitimate lever amplification, but the impact
                # energy fed to the estimate must never exceed the drop's
                # energy budget (m*g*h plus any tumble spin budget).
                raw_energy = peak.get("raw_kinetic_energy_j") or peak["kinetic_energy_j"]
                budget = mass_kg * drop_module.GRAVITY_M_S2 * config["height_m"]
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
                effective_speed = math.sqrt(max(0.0, 2.0 * capped_energy / mass_kg))
                estimate = impact.estimate_impact(
                    mass_kg,
                    velocity_m_s=effective_speed,
                    restitution=surface_restitution,
                    contact_stiffness_n_per_m=stiffness,
                )
                peak_force = estimate.to_dict().get("peak_force_n")
            result["drop_simulation"] = {
                "config": simulation["config"],
                "model": simulation["model"],
                "drops": simulation["drops"],
                "impacts": simulation["impacts"],
                "checks": simulation["checks"],
                "peak": simulation["peak"],
                "peak_force_estimate_n": peak_force,
                "trajectory": simulation["trajectory"],
            }
            # Wire drop evidence into the impact section so the qualification
            # gates evaluate the simulated drop as impact evidence.
            impact_section = result["impact"]
            impact_missing = impact_section is None or impact_section.get("result") is None
            if impact_missing and peak is not None:
                estimate = impact.estimate_impact(
                    mass_kg,
                    velocity_m_s=effective_speed,
                    restitution=surface_restitution,
                    contact_stiffness_n_per_m=stiffness,
                )
                result["impact"] = {
                    "mass_kg": mass_kg,
                    "result": estimate.to_dict(),
                    "reason": None,
                    "unsupported_failure_modes": list(impact.IMPACT_UNSUPPORTED_FAILURE_MODES),
                    "source": "drop_simulation",
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
                    stiffness,
                    result,
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
            request, catalog, result, geometry_objs, component_specs, population_config
        )

    # SHELL RESULT — the authoritative engineering answer.  The shell is the
    # primary engineering target: its deformation, stress, safety factor,
    # critical region, and confidence are reported here, separate from the
    # secondary component screening below.  Component verdicts never feed
    # back into this section, so arbitrary component thresholds cannot
    # contaminate the shell result.
    _assemble_shell_result(request, result)

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
    """Canonical snapshot of every input dict, keyed by request key."""
    inputs = {}
    for key in sorted(request.keys()):
        inputs[str(key)] = canonical_value(request[key])
    return inputs


def _input_hashes(inputs):
    return {key: sha256_bytes(canonical_bytes(value)) for key, value in inputs.items()}


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


def _build_manifest(mode, inputs, input_hashes):
    return {
        "schema_id": MANIFEST_SCHEMA_ID,
        "engine_version": ENGINE_VERSION,
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
    cached_hashes = manifest.get("input_hashes")
    if not isinstance(cached_hashes, dict) or cached_hashes != input_hashes:
        return False
    return manifest.get("manifest_hash") is not None


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
        return result
    request = dict(request or {})
    mode = _normalize_mode(request.get("mode", "exploration"))
    options = request.get("options")
    options = dict(options) if isinstance(options, Mapping) else {}
    debug = bool(options.get("debug", False))
    result = _new_result(mode)
    try:
        inputs = _collect_inputs(request)
        input_hashes = _input_hashes(inputs)
        run_id = _run_id_for(mode, input_hashes, options)
        result["run_id"] = run_id
        if use_cache and cache is not None:
            cached = cache.load(run_id)
            if cached is not None and _cached_matches(cached, mode, inputs, input_hashes, run_id):
                return cached
        _execute(request, mode, options, result)
        manifest = _build_manifest(mode, inputs, input_hashes)
        manifest["manifest_hash"] = canonical.manifest_hash(manifest)
        result["manifest"] = manifest
        result["lifecycle_state"] = "failed" if result["errors"] else "completed"
        if use_cache and cache is not None and not result["errors"]:
            try:
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
    request = {key: canonical_value(value) for key, value in inputs.items()}
    result = run_pipeline(request)
    replay_manifest = result.get("manifest") or {}
    if replay_manifest.get("manifest_hash") != manifest.get("manifest_hash"):
        return {"supported": False, "reason": "replay produced a different manifest hash"}
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
