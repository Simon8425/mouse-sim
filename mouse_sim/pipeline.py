"""Deterministic, JSON-friendly mouse simulation pipeline orchestration.

``run_pipeline`` executes the standard analysis steps (material catalog,
geometry import, mass properties, DFM-lite validation, structural screening,
impact estimation, qualification gating) and packages the result into a
stable, JSON-serializable result with an immutable run manifest.  Pipeline
errors never raise: they are collected into the result's ``errors`` list and
the lifecycle is marked ``failed``.
"""

import traceback
from typing import Mapping, Optional

from mouse_sim import (
    canonical,
    canonical_bytes,
    canonical_value,
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
        "mass": None,
        "validation": None,
        "structural": None,
        "impact": None,
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
    raw_objects = request.get("objects") or []
    if isinstance(raw_objects, Mapping):
        for key, value in raw_objects.items():
            if isinstance(value, Mapping):
                yield str(key), dict(value)
            else:
                yield str(key), {"id": str(key), "geometry": value}
    else:
        for index, value in enumerate(raw_objects):
            if isinstance(value, Mapping):
                identifier = value.get("id", value.get("name", "object-{}".format(index)))
                yield str(identifier), dict(value)
            else:
                yield "object-{}".format(index), {"geometry": value}


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
    for object_id, raw in _object_entries(request):
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
            except (TypeError, ValueError) as exc:
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


def _validity(result):
    validation_report = result["validation"] or {}
    findings = validation_report.get("findings") or []
    reasons = [
        item["message"]
        for item in findings
        if item.get("severity") in ("warning", "error", "blocker")
    ]
    assumptions = []
    unsupported = []
    structural = result["structural"]
    if structural is not None:
        response = structural.get("response") or {}
        assumptions.extend(response.get("assumptions") or [])
    impact_section = result["impact"]
    if impact_section is not None:
        impact_result = impact_section.get("result") or {}
        assumptions.extend(impact_result.get("assumptions") or [])
        unsupported.extend(impact_section.get("unsupported_failure_modes") or [])
    status = validation_report.get("status")
    if status == "pass":
        confidence = "high"
    elif status == "warn":
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "state": validation_report.get("validity_state") or "valid",
        "reasons": reasons,
        "assumptions": assumptions,
        "unsupported_failure_modes": sorted(set(unsupported)),
        "confidence": confidence,
    }


def _execute(request, mode, options, result):
    units = str(request.get("units") or "m")
    catalog = _material_catalog(request, result)
    geometry_objs, materials_by_object, density_by_object, overrides, behaviors = _parse_objects(
        request, catalog, result, units
    )

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

    impact_request = request.get("impact")
    result["impact"] = None
    if impact_request is not None:
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

    if result["structural"] is not None:
        qual_load_case = result["structural"]["load_case"]
    elif request.get("load_case") is not None:
        qual_load_case = _normalize_load(request["load_case"])
    else:
        qual_load_case = None
    qualification_result = qualification.evaluate_qualification(
        mode=mode,
        method=request.get("method"),
        geometry=request.get("geometry"),
        materials=list(catalog.values()) if isinstance(catalog, Mapping) else None,
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
    )
    result["qualification"] = qualification_result.to_dict()

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
    request = dict(request or {})
    mode = _normalize_mode(request.get("mode", "exploration"))
    options = dict(request.get("options") or {})
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
