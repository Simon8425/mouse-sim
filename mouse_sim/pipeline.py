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

ENGINE_VERSION = "0.1.0"
RESULT_SCHEMA_ID = "gms.pipeline-result/1"
MANIFEST_SCHEMA_ID = "gms.run-manifest/1"

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
        "manifest": None,
        "errors": [],
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
        return materials.builtin_materials()
    try:
        return materials.load_material_catalog(raw_materials)
    except Exception as exc:
        message = str(exc) or "invalid material catalog"
        result["issues"].append(
            _issue("MATERIAL_CATALOG_INVALID", "error", "material", message, evidence_blocking=True)
        )
        result["errors"].append({"code": "MATERIAL_CATALOG_INVALID", "message": message})
        return materials.builtin_materials()


def _parse_objects(request, catalog, result, units):
    geometry_objs = {}
    materials_by_object = {}
    density_by_object = {}
    overrides = {}
    behaviors = {}
    if "objects" in request and not isinstance(request.get("objects"), (Mapping, list, tuple)):
        _pipeline_error(result, "INVALID_OBJECTS", "objects must be an object or array")
        return geometry_objs, materials_by_object, density_by_object, overrides, behaviors
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
        material = catalog.get(material_key) if material_key is not None else None
        if material_key is not None and material is None:
            result["issues"].append(
                _issue(
                    "MATERIAL_NOT_FOUND",
                    "warning",
                    "material",
                    "material {!r} for object {!r} is not in the catalog".format(material_key, object_id),
                )
            )
        if material is not None:
            materials_by_object[object_id] = material
            density_by_object[object_id] = (
                material.properties if hasattr(material, "properties") else material
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
                "material": material_key,
            }
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
        assumptions.extend(response.get("assumptions") or [])
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
            assumptions.extend(impact_result.get("assumptions") or [])
            _add_state(states, impact_result.get("validity"))
            _add_flag_states(states, impact_result.get("flags"))
            _collect_unsupported(impact_result, unsupported)
        _collect_unsupported(impact_section, unsupported)
        _add_flag_states(states, impact_section.get("flags"))
        if impact_section.get("reason"):
            states.append("inconclusive")
            reasons.append(str(impact_section["reason"]))
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
    confidence = "high" if state == "valid" else "low"
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
    if load_case is not None and structure is not None:
        try:
            if not isinstance(structure, Mapping):
                raise ValueError("structure must be an object")
            normalized_load = _normalize_load(load_case)
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
            )
            peak = simulation["peak"]
            peak_force = None
            if peak is not None:
                estimate = impact.estimate_impact(
                    mass_kg,
                    velocity_m_s=peak["impact_speed_m_s"],
                    restitution=surface_restitution,
                    contact_stiffness_n_per_m=stiffness,
                )
                peak_force = estimate.to_dict().get("peak_force_n")
            result["drop_simulation"] = {
                "config": simulation["config"],
                "model": simulation["model"],
                "drops": simulation["drops"],
                "impacts": simulation["impacts"],
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
                    velocity_m_s=peak["impact_speed_m_s"],
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
    expected = _build_manifest(mode, inputs, input_hashes)
    expected["manifest_hash"] = canonical.manifest_hash(expected)
    return cached.get("manifest") == expected


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
