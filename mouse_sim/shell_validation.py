"""Shell validation preparation: pinned validation configuration, contact
stiffness sweep, parameter sensitivity, result traceability, physical
validation status, uncertainty bands, and invalidating-assumption framing.

This module is the preparation layer for comparing the shell model against
real instrumented drop tests.  Its rules:

- The ``validation`` request section makes the shell chain SELF-CONTAINED:
  material, drop, contact and structural configuration are pinned
  explicitly; nothing is silently inherited from unrelated settings.
- Contact stiffness is a first-class validation parameter (the largest
  identified uncertainty): a fixed value, or a sweep, with the effect on
  peak force/acceleration/duration/compression reported per value.  NO value
  is claimed "correct" without measurements.
- Measured test data is accepted (test id, CAD revision, material,
  prototype id, surface, height, orientation, environment, sensor, values,
  measurement uncertainty) and compared against the same simulation
  configuration (absolute/relative error, bias, RMSE, correlation).
  Measured data NEVER modifies the physics automatically.
- Sensitivity analysis perturbs each shell parameter and reports the
  relative response of the shell outputs, so the top uncertainty drivers are
  identified BEFORE physical calibration.
"""

import math
from collections.abc import Mapping

from . import canonical
from .errors import ValidationError

# Documented screening domain for the validation drop.
VALIDATION_HEIGHT_RANGE_M = (0.02, 2.0)
VALIDATION_STIFFNESS_RANGE_N_PER_M = (1e3, 1e8)
# Default perturbation for sensitivity analysis (documented).
DEFAULT_PERTURBATION_FRACTION = 0.1
# Sensible perturbation for the discrete timestep parameter.
TIMESTEP_HALVING = 2
# Sensitivity output rows and their source keys (shell outputs).
SENSITIVITY_OUTPUTS = (
    "peak_acceleration_m_s2",
    "peak_force_n",
    "peak_stress_pa",
    "max_displacement_m",
    "safety_factor",
    "settle_s",
    "impact_speed_m_s",
)

# Outputs that drive the parameter ranking.  ``settle_s`` is EXCLUDED from
# the ranking aggregation: the settle time of a rocking contact is
# chaotically sensitive — microscopic input changes (relative 1e-6) flip
# the settle branch (measured 2-8 s over a +/-1e-6 mass band on the
# reference corner drop), so its per-parameter sensitivities are
# knife-edge artifacts of the discrete contact detection, not robust
# engineering measures.  It remains in the per-output rows so the
# behavior stays visible, and the exclusion is disclosed in the result.
SENSITIVITY_RANKING_OUTPUTS = tuple(
    key for key in SENSITIVITY_OUTPUTS if key != "settle_s"
)


def _require(record, key, message):
    if key not in record or record[key] is None:
        raise ValidationError(message)
    return record[key]


def _finite_float(value, name, low=None, high=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError("validation.{} must be numeric".format(name)) from None
    if not math.isfinite(number):
        raise ValidationError("validation.{} must be finite".format(name))
    if low is not None and number < low:
        raise ValidationError("validation.{} must be >= {}".format(name, low))
    if high is not None and number > high:
        raise ValidationError("validation.{} must be <= {}".format(name, high))
    return number


def _zero_vector(value, name):
    try:
        numbers = [float(component) for component in value]
    except (TypeError, ValueError):
        raise ValidationError("validation.{} must be a numeric vector".format(name)) from None
    if len(numbers) != 3 or any(not math.isfinite(number) for number in numbers):
        raise ValidationError("validation.{} must be a finite 3-vector".format(name))
    return numbers


def _reject_unknown_section_keys(mapping, allowed, section_name):
    """Audit finding (W2-09D/W2-14D): unknown keys in the validation section
    were silently ignored (e.g. a spin pin in validation.drop ran a zero-spin
    simulation).  Unknown keys are now rejected like measured_tests does."""
    if not isinstance(mapping, Mapping):
        return
    unknown = sorted(set(mapping.keys()) - allowed)
    if unknown:
        raise ValidationError(
            "{} contains unsupported key(s) {}: every field must be supported "
            "explicitly (silently ignored fields could produce an "
            "incompatible comparison)".format(section_name, unknown)
        )


def apply_validation_config(request):
    """Validate the ``validation`` section and produce the EFFECTIVE request.

    Returns ``(effective_request, record)``.  Raises ``ValidationError`` on
    an incomplete or invalid validation section.  The effective request
    pins the shell chain (material, drop, contact, structure) from the
    section; the record is stored in ``result["validation"]`` for
    traceability.  ``request`` is not mutated.
    """
    from . import drop_sim as drop_module

    section = request.get("validation")
    if not isinstance(section, Mapping):
        raise ValidationError(
            "mode=validation requires a validation section defining material, "
            "drop, contact and structural configuration"
        )
    effective = dict(request)
    record = {"section": dict(section), "applied": {}}

    # -- Geometry (CAD revision recorded; the mesh comes from request.objects).
    _reject_unknown_section_keys(
        section,
        {"geometry", "material", "prototype", "drop", "contact", "structural",
         "contact_stiffness_sweep_n_per_m", "sensitivity", "measured_tests"},
        "validation",
    )
    geometry = section.get("geometry") or {}
    if not isinstance(geometry, Mapping):
        raise ValidationError("validation.geometry must be an object")
    _reject_unknown_section_keys(geometry, {"revision", "units", "quality"}, "validation.geometry")
    revision = str(geometry.get("revision") or "")
    if not revision:
        raise ValidationError("validation.geometry.revision is required (CAD revision)")
    objects = request.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValidationError("validation requires request.objects (the shell CAD geometry)")
    record["applied"]["geometry"] = {
        "cad_revision": revision,
        "units": str(geometry.get("units") or "mm"),
        "quality": str(geometry.get("quality") or "not_specified"),
        "object_ids": [str(obj.get("id") or "") for obj in objects],
    }

    # -- Material: pinned for the shell (structure + primary object).  Only
    #    catalog keys are accepted — inline definitions are rejected so a
    #    validation run can never claim a material pin it cannot deliver.
    material = section.get("material")
    if material is None:
        raise ValidationError("validation.material is required")
    if not isinstance(material, str):
        raise ValidationError(
            "validation.material must be a catalog key; define inline "
            "materials via request.materials and pin the key here"
        )
    record["applied"]["material"] = material
    structure = request.get("structure")
    if not isinstance(structure, Mapping):
        raise ValidationError("validation requires request.structure (the shell structural model)")
    effective["structure"] = dict(structure)
    effective["structure"]["material"] = material
    if isinstance(objects[0], Mapping):
        first_object = dict(objects[0])
        first_object["material"] = material
        effective["objects"] = [first_object] + list(objects[1:])

    # -- Drop configuration (explicit; no inheritance from UI defaults).
    drop = section.get("drop")
    if not isinstance(drop, Mapping):
        raise ValidationError("validation.drop is required")
    _reject_unknown_section_keys(
        drop,
        {"height_m", "orientation", "surface", "gravity_m_s2", "mass_scale",
         "inertia_scale", "com_override_m", "initial_velocity_m_s",
         "initial_angular_velocity_rad_s"},
        "validation.drop",
    )
    height_m = _finite_float(
        _require(drop, "height_m", "validation.drop.height_m is required"),
        "drop.height_m",
        *VALIDATION_HEIGHT_RANGE_M,
    )
    surface = str(drop.get("surface") or "").strip().lower()
    if surface not in drop_module.SURFACES:
        raise ValidationError(
            "validation.drop.surface must be one of {}".format(
                ", ".join(sorted(drop_module.SURFACES))
            )
        )
    orientation = drop.get("orientation")
    if isinstance(orientation, str):
        orientation_mode = orientation.strip().lower()
        if orientation_mode not in drop_module.ORIENTATIONS:
            raise ValidationError(
                "validation.drop.orientation must be one of {} or an explicit "
                "quaternion object".format(", ".join(drop_module.ORIENTATIONS))
            )
    elif isinstance(orientation, Mapping):
        quaternion = orientation.get("quaternion_wxyz")
        if (
            not isinstance(quaternion, (list, tuple))
            or len(quaternion) != 4
            or any(not isinstance(component, (int, float)) for component in quaternion)
            or not all(math.isfinite(float(component)) for component in quaternion)
            or abs(sum(float(component) ** 2 for component in quaternion)) < 1e-12
        ):
            raise ValidationError(
                "validation.drop.orientation.quaternion_wxyz must be a "
                "non-zero quaternion [w, x, y, z] (normalized internally; "
                "only the zero quaternion is rejected)"
            )
        # Audit finding (W4-05): components of absurd magnitude (1e9) were
        # silently normalized to a different pose.  A unit quaternion has
        # components within [-1, 1].
        if any(abs(float(component)) > 1.0 + 1e-4 for component in quaternion):
            raise ValidationError(
                "validation.drop.orientation.quaternion_wxyz components "
                "must be within [-1, 1] (a unit quaternion); component "
                "values beyond that indicate an input error"
            )
        orientation_mode = orientation
    else:
        raise ValidationError("validation.drop.orientation is required")
    gravity_m_s2 = _finite_float(
        drop.get("gravity_m_s2", drop_module.GRAVITY_M_S2), "drop.gravity_m_s2", 1.0, 30.0
    )
    initial_velocity = _zero_vector(
        drop.get("initial_velocity_m_s", [0.0, 0.0, 0.0]), "drop.initial_velocity_m_s"
    )
    if any(abs(value) > 1e-12 for value in initial_velocity):
        raise ValidationError(
            "validation.drop.initial_velocity_m_s must be [0, 0, 0]: the "
            "integrator releases the shell from rest (a nonzero initial "
            "velocity is not supported for drop tests)"
        )
    initial_angular = _zero_vector(
        drop.get("initial_angular_velocity_rad_s", [0.0, 0.0, 0.0]),
        "drop.initial_angular_velocity_rad_s",
    )
    if any(abs(value) > 1e-12 for value in initial_angular):
        raise ValidationError(
            "validation.drop.initial_angular_velocity_rad_s must be [0, 0, 0]: "
            "the validation workflow does not support a spinning release "
            "(run tumble as an exploration drop_simulation instead)"
        )
    # -- Contact configuration (explicit; restitution/friction default to the
    #    surface table only when not pinned, and the pin is recorded).
    #    Parsed BEFORE the drop config so the stiffness pin can flow into it.
    contact = section.get("contact") or {}
    if not isinstance(contact, Mapping):
        raise ValidationError("validation.contact must be an object")
    _reject_unknown_section_keys(
        contact,
        {"stiffness_n_per_m", "restitution", "friction", "timestep_s", "substeps"},
        "validation.contact",
    )
    stiffness = _finite_float(
        _require(contact, "stiffness_n_per_m", "validation.contact.stiffness_n_per_m is required"),
        "contact.stiffness_n_per_m",
        *VALIDATION_STIFFNESS_RANGE_N_PER_M,
    )
    contact_record = {"stiffness_n_per_m": stiffness}
    if contact.get("restitution") is not None:
        restitution = _finite_float(
            contact.get("restitution"), "contact.restitution", 0.0, 1.0
        )
        contact_record["restitution"] = restitution
    if contact.get("friction") is not None:
        friction = _finite_float(contact.get("friction"), "contact.friction", 0.0, 2.0)
        contact_record["friction"] = friction
    timestep = contact.get("timestep_s")
    if timestep is not None:
        timestep = _finite_float(timestep, "contact.timestep_s", 1e-6, 0.1)
        contact_record["timestep_s"] = timestep
    if contact.get("substeps") is not None:
        try:
            substeps = int(contact["substeps"])
        except (TypeError, ValueError, OverflowError):
            raise ValidationError(
                "validation.contact.substeps must be a finite integer"
            ) from None
        if substeps < 1 or substeps > 100:
            raise ValidationError("validation.contact.substeps must be between 1 and 100")
        contact_record["substeps"] = substeps
    contact_record["surface_table"] = dict(drop_module.SURFACES[surface])
    effective["impact"] = {
        "fall_height_m": height_m,
        "contact_stiffness_n_per_m": stiffness,
    }
    if "restitution" in contact_record:
        effective["impact"]["restitution"] = contact_record["restitution"]
    if "friction" in contact_record:
        effective["impact"]["friction"] = contact_record["friction"]
    record["applied"]["contact"] = contact_record

    # Optional drop-body overrides (mass/inertia scales and an explicit CoM)
    # pin the body the integrator solves; they are recorded for replay.
    drop_overrides = {}
    if drop.get("mass_scale") is not None:
        drop_overrides["mass_scale"] = _finite_float(
            drop.get("mass_scale"), "drop.mass_scale", 0.01, 100.0
        )
    if drop.get("inertia_scale") is not None:
        drop_overrides["inertia_scale"] = _finite_float(
            drop.get("inertia_scale"), "drop.inertia_scale", 0.01, 100.0
        )
    if drop.get("com_override_m") is not None:
        com_override = _zero_vector(drop.get("com_override_m"), "drop.com_override_m")
        drop_overrides["com_override_m"] = com_override

    # -- Prototype measurement section: the REAL shell's measured values.
    #    Absolute overrides for mass/CoM/inertia; the simulation must use the
    #    actual prototype where available, and any difference between the
    #    measured mass and the geometry-derived mass is disclosed.
    prototype = section.get("prototype") or {}
    if prototype is not None and not isinstance(prototype, Mapping):
        raise ValidationError("validation.prototype must be an object")
    if prototype:
        _reject_unknown_section_keys(
            prototype,
            {"prototype_id", "mass_kg", "com_m", "inertia_kg_m2", "thickness_m",
             "material", "cad_revision"},
            "validation.prototype",
        )
    prototype_record = {}
    if prototype:
        if prototype.get("prototype_id") is not None:
            prototype_id_value = str(prototype.get("prototype_id") or "")
            if not prototype_id_value.strip():
                raise ValidationError(
                    "validation.prototype.prototype_id must be a non-empty "
                    "string when provided; an empty prototype id would "
                    "silently disable the prototype identity gate"
                )
            prototype_record["prototype_id"] = prototype_id_value
        if prototype.get("mass_kg") is not None:
            prototype_record["mass_kg"] = _finite_float(
                prototype.get("mass_kg"), "prototype.mass_kg", 1e-3, 10.0
            )
        if prototype.get("com_m") is not None:
            prototype_record["com_m"] = _zero_vector(prototype.get("com_m"), "prototype.com_m")
        if prototype.get("inertia_kg_m2") is not None:
            inertia_matrix = prototype.get("inertia_kg_m2")
            if (
                not isinstance(inertia_matrix, (list, tuple))
                or len(inertia_matrix) != 3
                or any(
                    not isinstance(row, (list, tuple)) or len(row) != 3
                    for row in inertia_matrix
                )
            ):
                raise ValidationError(
                    "validation.prototype.inertia_kg_m2 must be a 3x3 matrix"
                )
            prototype_record["inertia_kg_m2"] = [
                [_finite_float(value, "prototype.inertia_kg_m2") for value in row]
                for row in inertia_matrix
            ]
            if any(
                abs(prototype_record["inertia_kg_m2"][i][j] - prototype_record["inertia_kg_m2"][j][i]) > 1e-9
                for i in range(3)
                for j in range(3)
            ):
                raise ValidationError(
                    "validation.prototype.inertia_kg_m2 must be symmetric"
                )
            # Audit finding (W4-05): an absurd inertia cell (1e9 kg*m2) silently
            # rewrote the dynamics.  Documented plausibility domain for a
            # hand-held shell: elements within [0, 1e-1] kg*m2.
            if any(
                abs(value) > 0.1 for row in prototype_record["inertia_kg_m2"] for value in row
            ):
                raise ValidationError(
                    "validation.prototype.inertia_kg_m2 element exceeds the "
                    "documented plausibility bound (1e-1 kg*m2) for a shell"
                )
            if any(
                prototype_record["inertia_kg_m2"][i][i] <= 0.0 for i in range(3)
            ):
                raise ValidationError(
                    "validation.prototype.inertia_kg_m2 diagonal must be positive"
                )
            # W2-05 follow-up: a symmetric non-positive-definite tensor
            # (e.g. off-diagonal terms too large) passed the config layer and
            # only failed later as DROP_SIMULATION_FAILED.  A physical
            # inertia tensor is positive-definite (Sylvester criterion) —
            # reject it at the config layer, fail-closed.
            inertia_matrix = prototype_record["inertia_kg_m2"]
            det1 = inertia_matrix[0][0]
            det2 = (
                inertia_matrix[0][0] * inertia_matrix[1][1]
                - inertia_matrix[0][1] * inertia_matrix[1][0]
            )
            det3 = (
                inertia_matrix[0][0]
                * (inertia_matrix[1][1] * inertia_matrix[2][2] - inertia_matrix[1][2] * inertia_matrix[2][1])
                - inertia_matrix[0][1]
                * (inertia_matrix[1][0] * inertia_matrix[2][2] - inertia_matrix[1][2] * inertia_matrix[2][0])
                + inertia_matrix[0][2]
                * (inertia_matrix[1][0] * inertia_matrix[2][1] - inertia_matrix[1][1] * inertia_matrix[2][0])
            )
            if not (det1 > 0.0 and det2 > 0.0 and det3 > 0.0):
                raise ValidationError(
                    "validation.prototype.inertia_kg_m2 must be positive-definite "
                    "(a physical inertia tensor)"
                )
        if prototype.get("thickness_m") is not None:
            prototype_record["thickness_m"] = _finite_float(
                prototype.get("thickness_m"), "prototype.thickness_m", 1e-4, 0.1
            )
        if prototype.get("material") is not None:
            prototype_record["material"] = str(prototype.get("material") or "")
        if prototype.get("cad_revision") is not None:
            prototype_record["cad_revision"] = str(prototype.get("cad_revision") or "")
        record["applied"]["prototype"] = prototype_record
        # Absolute prototype overrides take precedence over the scales.
        if "mass_kg" in prototype_record:
            drop_overrides["mass_kg"] = prototype_record["mass_kg"]
        if "com_m" in prototype_record:
            drop_overrides["com_override_m"] = prototype_record["com_m"]
        if "inertia_kg_m2" in prototype_record:
            drop_overrides["inertia_override_kg_m2"] = prototype_record["inertia_kg_m2"]

    # The pinned drop config MERGES onto any user-supplied drop_simulation
    # (mass_kg/seed/unit_seed the user set explicitly are preserved; the
    # validation pins win on conflict) instead of silently discarding it.
    user_drop = request.get("drop_simulation")
    drop_config = dict(user_drop) if isinstance(user_drop, Mapping) else {}
    drop_config.update(
        {
            "test": "drop",
            "height_m": height_m,
            "surface": surface,
            "drop_count": 1,
            "orientation": orientation_mode,
            "gravity_m_s2": gravity_m_s2,
            "initial_velocity_m_s": initial_velocity,
            "initial_angular_velocity_rad_s": initial_angular,
            "contact_stiffness_n_per_m": stiffness,
            "timestep_s": timestep if timestep is not None else None,
        }
    )
    # Pinned restitution/friction travel as SCALES against the surface table
    # so the integrator (which consumes scales) actually applies them.
    if "restitution" in contact_record:
        table_restitution = drop_module.SURFACES[surface]["restitution"]
        if table_restitution <= 0.0:
            raise ValidationError(
                "validation.contact.restitution cannot be pinned for surface {} "
                "(table restitution is zero)".format(surface)
            )
        restitution_scale = contact_record["restitution"] / table_restitution
        # Audit finding (W2-05E): the documented pin range [0, 1] was never
        # achievable end-to-end (the integrator's scale domain is [0.1, 2.0]),
        # so pins inside the documented range hard-failed downstream as a
        # physics error.  Validate the achievable band at config time.
        if restitution_scale < 0.1 or restitution_scale > 2.0:
            raise ValidationError(
                "validation.contact.restitution {:.3f} yields scale {:.3f} on "
                "surface {}; the integrator supports scales in [0.1, 2.0] "
                "(achievable restitution [{:.3f}, {:.3f}])".format(
                    contact_record["restitution"], restitution_scale, surface,
                    0.1 * table_restitution, 2.0 * table_restitution,
                )
            )
        drop_config["restitution_scale"] = restitution_scale
    if "friction" in contact_record:
        table_friction = drop_module.SURFACES[surface]["friction"]
        if table_friction <= 0.0:
            raise ValidationError(
                "validation.contact.friction cannot be pinned for surface {} "
                "(table friction is zero)".format(surface)
            )
        friction_scale = contact_record["friction"] / table_friction
        if friction_scale <= 0.0:
            raise ValidationError(
                "validation.contact.friction must be positive (scale {:.3f} on "
                "surface {})".format(friction_scale, surface)
            )
        drop_config["friction_scale"] = friction_scale
    drop_config.update(drop_overrides)
    effective["drop_simulation"] = drop_config
    record["applied"]["drop"] = drop_config

    # -- Structural model record (the solve uses request.structure/load_case).
    structural = section.get("structural") or {}
    if not isinstance(structural, Mapping):
        raise ValidationError("validation.structural must be an object")
    _reject_unknown_section_keys(
        structural,
        {"model", "boundary_assumptions", "supported_validity"},
        "validation.structural",
    )
    model_name = str(structural.get("model") or "")
    if not model_name:
        raise ValidationError("validation.structural.model is required (e.g. shell_panel_navier_v1)")
    record["applied"]["structural"] = {
        "model": model_name,
        "boundary_assumptions": str(structural.get("boundary_assumptions") or ""),
        "supported_validity": str(structural.get("supported_validity") or ""),
        "material": str(effective["structure"].get("material") or ""),
    }
    if request.get("load_case") is None:
        raise ValidationError("validation requires request.load_case (the structural load)")

    # -- Contact stiffness sweep (optional; explicit list).
    sweep = section.get("contact_stiffness_sweep_n_per_m")
    if sweep is not None:
        if not isinstance(sweep, (list, tuple)) or not sweep:
            raise ValidationError(
                "validation.contact_stiffness_sweep_n_per_m must be a non-empty list"
            )
        sweep_values = []
        for value in sweep:
            sweep_values.append(
                _finite_float(value, "contact_stiffness_sweep_n_per_m", *VALIDATION_STIFFNESS_RANGE_N_PER_M)
            )
        record["applied"]["contact_stiffness_sweep_n_per_m"] = sweep_values

    # -- Sensitivity configuration (optional; runs ONLY when explicitly
    #    requested — the end-to-end sweep costs ~14 pipeline re-runs and is
    #    never silently forced on a validation run).
    if section.get("sensitivity") is not None:
        sensitivity = section.get("sensitivity")
        if not isinstance(sensitivity, Mapping):
            raise ValidationError("validation.sensitivity must be an object")
        fraction = _finite_float(
            sensitivity.get("perturbation_fraction", DEFAULT_PERTURBATION_FRACTION),
            "sensitivity.perturbation_fraction",
            0.001,
            0.5,
        )
        parameters = sensitivity.get("parameters")
        if parameters is not None:
            if not isinstance(parameters, (list, tuple)) or not parameters:
                raise ValidationError("validation.sensitivity.parameters must be a non-empty list")
            record["applied"]["sensitivity"] = {
                "perturbation_fraction": fraction,
                "parameters": [str(name) for name in parameters],
            }
        else:
            record["applied"]["sensitivity"] = {"perturbation_fraction": fraction}

    # -- Measured-test workflow (optional; metadata recorded, never applied
    #    to the physics).  The measured tests also feed the measured-drop
    #    correlation section so each test is re-simulated under its exact
    #    condition (the correlation machinery is the simulator's own).
    measured = section.get("measured_tests")
    correlation_from_measured = False
    if measured is not None:
        if not isinstance(measured, (list, tuple)):
            raise ValidationError("validation.measured_tests must be a list")
        normalized = _normalize_measured_tests(
            measured,
            pinned_material=material if isinstance(material, str) else None,
            validation_revision=revision,
            prototype_id=prototype_record.get("prototype_id"),
        )
        record["applied"]["measured_tests"] = normalized
        correlation_drops = []
        for test in normalized:
            drop_entry = {
                "drop_id": test["test_id"],
                "height_m": test["height_m"],
                "surface": test["surface"],
                "orientation": test["orientation"],
                "measured_peak_accel_g": test["measured"].get("measured_peak_accel_g"),
                "identity_ok": test["identity_ok"],
                "identity_flags": test["identity_flags"],
                "sensor": test["sensor"],
                "uncertainty": test["uncertainty"],
            }
            if test["measured"].get("measured_impact_duration_s") is not None:
                drop_entry["measured_impact_duration_s"] = test["measured"]["measured_impact_duration_s"]
            if test["measured"].get("measured_settle_s") is not None:
                drop_entry["measured_settle_s"] = test["measured"]["measured_settle_s"]
            correlation_drops.append(drop_entry)
        if correlation_drops:
            effective["correlation"] = {
                "acceptance": {},
                "measured_drops": correlation_drops,
            }
            correlation_from_measured = True
    if not correlation_from_measured:
        # W2-04 follow-up: in validation mode the measured comparisons
        # MUST be declared via validation.measured_tests (identity-
        # checked).  A top-level correlation.measured_drops section
        # bypasses the identity cross-check (foreign cad_revision/
        # material/prototype feeds the verdict undetected) — reject it
        # fail-closed.  Covers absent AND empty measured_tests.
        raw_correlation = request.get("correlation")
        if (
            isinstance(raw_correlation, Mapping)
            and raw_correlation.get("measured_drops")
        ):
            raise ValidationError(
                "validation-mode measured comparisons must be declared in "
                "validation.measured_tests; a top-level correlation section "
                "is not identity-checked in validation mode"
            )

    return effective, record


def _normalize_measured_tests(tests, pinned_material=None, validation_revision=None, prototype_id=None):
    """Validate measured-test entries; returns the normalized records.

    Each entry carries test metadata (test_id, cad_revision, material,
    prototype_id, surface + surface DEFINITION, height_m, orientation,
    environment, sensor DEFINITION with location/quantity/filtering) and
    measured values with uncertainty.  The values are compared against the
    simulation later; they NEVER modify the physics.  Duplicate test IDs,
    negative or implausible measurements, non-finite values, and UNKNOWN
    KEYS are rejected — a physical test must never silently compare
    incompatible data (an unsupported field such as spin must fail loudly,
    not be ignored).  Identity fields (cad_revision/material/prototype_id)
    are cross-checked against the run's pinned prototype; mismatches are
    flagged and excluded from the correlation verdict.
    """
    from . import drop_sim as drop_module

    allowed_keys = {
        "test_id", "cad_revision", "material", "prototype_id", "height_m",
        "surface", "orientation", "environment", "sensor",
        "measured_peak_accel_g", "measured_impact_duration_s", "measured_settle_s",
        "measured_peak_accel_g_uncertainty", "measured_impact_duration_s_uncertainty",
        "measured_settle_s_uncertainty",
    }
    normalized = []
    seen_ids = set()
    for entry in tests:
        if not isinstance(entry, Mapping):
            raise ValidationError("validation.measured_tests entries must be objects")
        unknown_keys = sorted(set(entry.keys()) - allowed_keys)
        if unknown_keys:
            raise ValidationError(
                "measured test entry contains unsupported key(s) {}: every field "
                "must be supported explicitly (silently ignored fields could "
                "produce an incompatible comparison)".format(unknown_keys)
            )
        test_id = str(entry.get("test_id") or "").strip()
        if not test_id:
            raise ValidationError("validation.measured_tests test_id is required")
        if test_id in seen_ids:
            raise ValidationError(
                "duplicate measured test_id {!r}: each physical test must "
                "have a unique identifier".format(test_id)
            )
        seen_ids.add(test_id)
        height_m = _finite_float(
            _require(entry, "height_m", "measured test {} height_m required".format(test_id)),
            "measured_tests.height_m",
            *VALIDATION_HEIGHT_RANGE_M,
        )
        raw_surface = entry.get("surface")
        if raw_surface is None:
            raise ValidationError(
                "measured test {} surface is required (a silent default would "
                "re-simulate the wrong surface)".format(test_id)
            )
        surface_definition = {}
        if isinstance(raw_surface, str):
            surface = raw_surface.strip().lower()
        elif isinstance(raw_surface, Mapping):
            surface = str(raw_surface.get("type") or "").strip().lower()
            definition = raw_surface.get("definition") or {}
            if isinstance(definition, Mapping):
                for key in ("thickness_m", "hardness", "mounting", "notes"):
                    if definition.get(key) is not None:
                        surface_definition[key] = definition[key]
        else:
            raise ValidationError(
                "measured test {} surface must be a table key or {{type, definition}}".format(
                    test_id
                )
            )
        if surface not in drop_module.SURFACES:
            raise ValidationError(
                "measured test {} surface must be one of {}".format(
                    test_id, ", ".join(sorted(drop_module.SURFACES))
                )
            )
        if surface_definition.get("thickness_m") is not None:
            try:
                thickness = float(surface_definition["thickness_m"])
            except (TypeError, ValueError):
                raise ValidationError(
                    "measured test {} surface definition thickness_m must be numeric".format(
                        test_id
                    )
                ) from None
            if not math.isfinite(thickness) or thickness < 0.0:
                raise ValidationError(
                    "measured test {} surface definition thickness_m must be "
                    "finite and non-negative".format(test_id)
                )
            surface_definition["thickness_m"] = thickness
        orientation = entry.get("orientation")
        if isinstance(orientation, str):
            orientation = orientation.strip().lower()
            if orientation not in drop_module.ORIENTATIONS:
                raise ValidationError(
                    "measured test {} orientation must be one of {} or a quaternion".format(
                        test_id, ", ".join(drop_module.ORIENTATIONS)
                    )
                )
        elif isinstance(orientation, Mapping) and "quaternion_wxyz" in orientation:
            quaternion = orientation["quaternion_wxyz"]
            if (
                not isinstance(quaternion, (list, tuple))
                or len(quaternion) != 4
                or any(
                    not isinstance(component, (int, float))
                    or not math.isfinite(float(component))
                    for component in quaternion
                )
                or abs(sum(float(component) ** 2 for component in quaternion)) < 1e-12
            ):
                raise ValidationError(
                    "measured test {} orientation quaternion must be a non-zero "
                    "finite quaternion [w, x, y, z]".format(test_id)
                )
            if any(abs(float(component)) > 1.0 + 1e-4 for component in quaternion):
                raise ValidationError(
                    "measured test {} orientation quaternion components must be "
                    "within [-1, 1] (a unit quaternion)".format(test_id)
                )
        else:
            raise ValidationError(
                "measured test {} orientation is required (mode or quaternion)".format(test_id)
            )
        measured = {}
        for key, low, high in (
            ("measured_peak_accel_g", 0.0, 10000.0),
            ("measured_impact_duration_s", 0.0, 1.0),
            ("measured_settle_s", 0.0, 60.0),
        ):
            if entry.get(key) is not None:
                value = _finite_float(entry.get(key), "measured_tests.{}".format(key))
                if value <= low:
                    raise ValidationError(
                        "measured test {} {} must be positive ({}): an impact "
                        "duration/settle cannot be zero or negative".format(
                            test_id, key, value
                        )
                    )
                if value > high:
                    raise ValidationError(
                        "measured test {} {} {} exceeds the physical "
                        "plausibility bound ({} s) for a hand drop".format(
                            test_id, key, value, high
                        )
                    )
                measured[key] = value
        if not measured:
            raise ValidationError(
                "measured test {} carries no measured values".format(test_id)
            )
        if "measured_peak_accel_g" in measured and measured["measured_peak_accel_g"] > 10000.0:
            raise ValidationError(
                "measured test {} measured_peak_accel_g {} g exceeds the "
                "physical plausibility bound (10000 g) for a hand drop".format(
                    test_id, measured["measured_peak_accel_g"]
                )
            )
        uncertainty = {}
        for key in (
            "measured_peak_accel_g_uncertainty",
            "measured_impact_duration_s_uncertainty",
            "measured_settle_s_uncertainty",
        ):
            if entry.get(key) is not None:
                value = _finite_float(entry.get(key), "measured_tests.{}".format(key), 0.0)
                uncertainty[key] = value
        # Structured sensor definition (item: exact measurement definition).
        # An ABSENT sensor definition means the comparison quantity is
        # UNKNOWN — the test is treated as non-equivalent (fail-closed), not
        # silently assumed to be a CoM/resultant reading.
        raw_sensor = entry.get("sensor")
        sensor = raw_sensor if isinstance(raw_sensor, Mapping) else {}
        if raw_sensor is not None and not isinstance(raw_sensor, Mapping):
            raise ValidationError(
                "measured test {} sensor must be an object".format(test_id)
            )
        sensor_record = {}
        if sensor:
            # A sensor object was actually supplied: record its definition.
            # An ABSENT sensor stays EMPTY so the equivalence logic can
            # distinguish "no definition" from a defined reading (audit
            # finding: the default quantity made an absent sensor look
            # defined and silently equivalent).
            _reject_unknown_section_keys(
                sensor,
                {"model", "filter", "sync", "notes", "quantity", "axis",
                 "location_body_m", "sampling_rate_hz"},
                "measured test {} sensor".format(test_id),
            )
            for key in ("model", "filter", "sync", "notes"):
                if sensor.get(key) is not None:
                    sensor_record[key] = str(sensor.get(key) or "")
            if sensor.get("location_body_m") is not None:
                sensor_record["location_body_m"] = _zero_vector(
                    sensor.get("location_body_m"), "measured_tests.sensor.location_body_m"
                )
            if sensor.get("sampling_rate_hz") is not None:
                sensor_record["sampling_rate_hz"] = _finite_float(
                    sensor.get("sampling_rate_hz"),
                    "measured_tests.sensor.sampling_rate_hz",
                    1.0,
                    1e7,
                )
            raw_quantity = sensor.get("quantity")
            if raw_quantity is None:
                raise ValidationError(
                    "measured test {} sensor.quantity is required when a sensor "
                    "is defined (resultant_peak_g or axis_peak_g)".format(test_id)
                )
            quantity = str(raw_quantity).strip().lower()
            if quantity not in ("resultant_peak_g", "axis_peak_g"):
                raise ValidationError(
                    "measured test {} sensor.quantity must be resultant_peak_g or "
                    "axis_peak_g".format(test_id)
                )
            sensor_record["quantity"] = quantity
            if sensor.get("axis") is not None:
                axis = str(sensor.get("axis") or "").strip().lower()
                if axis not in ("x", "y", "z", "-x", "-y", "-z"):
                    raise ValidationError(
                        "measured test {} sensor.axis must be one of x, y, z, "
                        "-x, -y, -z".format(test_id)
                    )
                if quantity != "axis_peak_g":
                    # Audit finding (W2-01D): an axis-bearing sensor with a
                    # resultant declaration is a contradiction and silently
                    # claimed the best-case measurement identity.
                    raise ValidationError(
                        "measured test {} sensor.axis is only valid with "
                        "quantity axis_peak_g (an axis reading is NOT a "
                        "resultant)".format(test_id)
                    )
                sensor_record["axis"] = axis
            elif quantity == "axis_peak_g":
                raise ValidationError(
                    "measured test {} sensor.quantity axis_peak_g requires "
                    "sensor.axis".format(test_id)
                )
        # Environment validation: recorded metadata, but NaN/out-of-range
        # values must be rejected (they previously destroyed the run as a
        # misleading PIPELINE_INTERNAL and could not be persisted).
        environment = entry.get("environment") or {}
        if not isinstance(environment, Mapping):
            raise ValidationError(
                "measured test {} environment must be an object".format(test_id)
            )
        _reject_unknown_section_keys(
            environment,
            {"temperature_k", "humidity_pct"},
            "measured test {} environment".format(test_id),
        )
        environment_record = dict(environment)
        if environment_record.get("temperature_k") is not None:
            temperature = _finite_float(
                environment_record.get("temperature_k"),
                "measured_tests.environment.temperature_k",
                173.15,
                373.15,
            )
            environment_record["temperature_k"] = temperature
        if environment_record.get("humidity_pct") is not None:
            humidity = _finite_float(
                environment_record.get("humidity_pct"),
                "measured_tests.environment.humidity_pct",
                0.0,
                100.0,
            )
            environment_record["humidity_pct"] = humidity
        # Identity cross-check against the run's pinned prototype (audit
        # finding: a test from a different prototype/material previously fed
        # the verdict undetected).
        identity_ok = True
        identity_flags = []
        test_revision = str(entry.get("cad_revision") or "").strip()
        test_material = str(entry.get("material") or "").strip()
        test_prototype = str(entry.get("prototype_id") or "").strip()
        if validation_revision and not test_revision:
            identity_ok = False
            identity_flags.append("cad_revision is missing (validation revision {!r})".format(
                validation_revision))
        elif validation_revision and test_revision != validation_revision:
            identity_ok = False
            identity_flags.append(
                "cad_revision {!r} differs from the validation revision {!r}".format(
                    test_revision, validation_revision
                )
            )
        if pinned_material and not test_material:
            identity_ok = False
            identity_flags.append("material is missing (pinned material {!r})".format(
                pinned_material))
        elif pinned_material and test_material != pinned_material:
            identity_ok = False
            identity_flags.append(
                "material {!r} differs from the pinned validation material {!r}".format(
                    test_material, pinned_material
                )
            )
        if prototype_id and not test_prototype:
            identity_ok = False
            identity_flags.append("prototype_id is missing (validation prototype {!r})".format(
                prototype_id))
        elif prototype_id and test_prototype != prototype_id:
            identity_ok = False
            identity_flags.append(
                "prototype_id {!r} differs from the validation prototype {!r}".format(
                    test_prototype, prototype_id
                )
            )
        normalized.append(
            {
                "test_id": test_id,
                "cad_revision": test_revision,
                "material": test_material,
                "prototype_id": test_prototype,
                "height_m": height_m,
                "surface": surface,
                "surface_definition": surface_definition,
                "orientation": orientation,
                "environment": environment_record,
                "sensor": sensor_record,
                "measured": measured,
                "uncertainty": uncertainty,
                "identity_ok": identity_ok,
                "identity_flags": identity_flags,
            }
        )
    return normalized


def run_contact_stiffness_sweep(stiffness_values, mass_kg, speed_m_s, restitution, load_path_area_m2=None, allowable_pa=None):
    """Evaluate the drop-derived estimate at each stiffness value.

    Returns a list of rows {contact_stiffness_n_per_m, peak_force_n,
    peak_acceleration_m_s2, impulse_n_s, contact_duration_s,
    contact_compression_m, load_path_stress_pa, safety_factor} plus a note
    that NO stiffness value is claimed correct without measurements.
    """
    from . import impact as impact_module

    rows = []
    for stiffness in stiffness_values:
        estimate = impact_module.estimate_impact(
            mass_kg,
            velocity_m_s=speed_m_s,
            restitution=restitution,
            contact_stiffness_n_per_m=stiffness,
            load_path_area_m2=load_path_area_m2,
            allowable_pa=allowable_pa,
        )
        data = estimate.to_dict()
        rows.append(
            {
                "contact_stiffness_n_per_m": stiffness,
                "peak_force_n": data.get("peak_force_n"),
                "peak_acceleration_m_s2": data.get("peak_acceleration_m_s2"),
                "impulse_n_s": data.get("impulse_n_s"),
                "contact_duration_s": data.get("contact_duration_s"),
                "contact_compression_m": data.get("contact_compression_m"),
                "load_path_stress_pa": data.get("load_path_stress_pa"),
                "safety_factor": data.get("safety_factor"),
            }
        )
    return {
        "rows": rows,
        "note": (
            "contact stiffness is the largest identified uncertainty; NO value "
            "is claimed correct without physical measurements. The sweep shows "
            "sensitivity only."
        ),
    }


def run_sensitivity(base_request, fraction=DEFAULT_PERTURBATION_FRACTION, parameters=None):
    """End-to-end parameter sensitivity of the shell chain.

    Perturbs each parameter by ``fraction`` (both directions where
    meaningful) by re-running the FULL pipeline on the perturbed request,
    and reports the relative response of the shell outputs.  Returns
    (rows, top_parameters) with rows ordered by mean relative response.
    """
    from . import pipeline as pipeline_module

    allowed_parameters = (
        "mass",
        "com",
        "inertia",
        "youngs_modulus",
        "thickness",
        "strength",
        "contact_stiffness",
        "restitution",
        "friction",
        "timestep",
    )
    if parameters is None:
        parameters = allowed_parameters
    unknown = [name for name in parameters if name not in allowed_parameters]
    if unknown:
        raise ValidationError(
            "validation.sensitivity.parameters contains unknown parameter(s): {}".format(
                ", ".join(unknown)
            )
        )

    def _plain(request):
        # The perturbed runs must NOT re-enter validation mode (the validation
        # extras — sensitivity included — would recurse).  The effective
        # request already carries the pinned config, so a plain exploration
        # run of the same request produces the same shell chain.
        stripped = dict(request)
        stripped["mode"] = "exploration"
        stripped.pop("validation", None)
        return stripped

    def run(request):
        result = pipeline_module.run_pipeline(_plain(request), use_cache=False)
        shell = result.get("shell") or {}
        structural = result.get("structural")
        drop = result.get("drop_simulation") or {}
        outputs = {}
        if structural is not None:
            response = structural.get("response") or {}
            outputs["peak_stress_pa"] = response.get("max_stress_pa")
            outputs["max_displacement_m"] = response.get("max_displacement_m")
            outputs["safety_factor"] = response.get("safety_factor")
        estimate = drop.get("peak_force_estimate") or {}
        peak_force = drop.get("peak_force_estimate_n")
        outputs["peak_force_n"] = peak_force
        if peak_force is not None and estimate.get("mass_kg"):
            try:
                outputs["peak_acceleration_m_s2"] = float(peak_force) / float(estimate["mass_kg"])
            except (TypeError, ValueError, ZeroDivisionError):
                outputs["peak_acceleration_m_s2"] = None
        else:
            outputs["peak_acceleration_m_s2"] = None
        peak = drop.get("peak") or {}
        outputs["impact_speed_m_s"] = peak.get("impact_speed_m_s")
        outputs["settle_s"] = None
        if drop.get("drops"):
            outputs["settle_s"] = drop["drops"][0].get("settled_s")
        return outputs, result

    baseline, baseline_result = run(base_request)
    baseline_mass = None
    baseline_com = None
    baseline_timestep = None
    drop_model = (baseline_result.get("drop_simulation") or {}).get("model") or {}
    baseline_mass = drop_model.get("mass_kg")
    baseline_timestep = drop_model.get("timestep_s")
    mass_section = baseline_result.get("mass") or {}
    baseline_com = mass_section.get("center_of_mass_m")
    if baseline_com is None:
        baseline_com = [0.0, 0.0, 0.0]

    def perturb(request, name, fraction_value):
        modified = dict(request)
        drop = dict(modified.get("drop_simulation") or {})
        if name == "mass" and baseline_mass:
            # A physically consistent body: uniform scaling of the mass
            # distribution scales mass AND inertia together (same geometry).
            drop["mass_kg"] = baseline_mass * (1.0 + fraction_value)
            drop["inertia_scale"] = 1.0 + fraction_value
        elif name == "com":
            drop["com_override_m"] = [
                value * (1.0 + fraction_value) for value in baseline_com
            ]
        elif name == "inertia":
            drop["inertia_scale"] = 1.0 + fraction_value
        elif name == "contact_stiffness":
            stiffness = drop.get("contact_stiffness_n_per_m")
            if stiffness is not None:
                drop["contact_stiffness_n_per_m"] = stiffness * (1.0 + fraction_value)
        elif name == "restitution":
            drop["restitution_scale"] = 1.0 + fraction_value
        elif name == "friction":
            drop["friction_scale"] = 1.0 + fraction_value
        elif name == "timestep":
            # Discrete parameter: the perturbation is a timestep HALVING
            # (finer resolution); the response is reported as the relative
            # change per halving.
            if baseline_timestep:
                drop["timestep_s"] = baseline_timestep / 2.0
        elif name in ("youngs_modulus", "thickness", "strength"):
            # The structural response is a pure closed-form function of the
            # pinned load case; re-solving it directly with the perturbed
            # property is exactly what the pipeline's structural section
            # does (same solver, same payload).  The drop side does not
            # depend on structural parameters.
            return _perturbed_structural_response(
                base_request, baseline_result, name, fraction_value, run
            )
        modified["drop_simulation"] = drop
        return modified

    rows = []
    for name in parameters:
        if name in ("youngs_modulus", "thickness", "strength"):
            structural_sensitivities = perturb(base_request, name, fraction)
            sensitivities = [
                item for item in structural_sensitivities
                if item["sensitivity_up"] is not None or item["sensitivity_down"] is not None
            ]
        else:
            up = perturb(base_request, name, fraction)
            down = perturb(base_request, name, -fraction)
            up_outputs, _ = run(up)
            down_outputs, _ = run(down)
            sensitivities = []
            for output_key in SENSITIVITY_OUTPUTS:
                baseline_value = baseline[output_key]
                up_value = up_outputs[output_key]
                down_value = down_outputs[output_key]
                if (
                    baseline_value is None
                    or not math.isfinite(float(baseline_value))
                    or abs(float(baseline_value)) < 1e-12
                ):
                    continue
                up_sensitivity = None
                if up_value is not None and math.isfinite(float(up_value)):
                    up_sensitivity = ((float(up_value) - float(baseline_value)) / float(baseline_value)) / fraction
                down_sensitivity = None
                if down_value is not None and math.isfinite(float(down_value)):
                    down_sensitivity = ((float(down_value) - float(baseline_value)) / float(baseline_value)) / (-fraction)
                sensitivities.append(
                    {
                        "output": output_key,
                        "sensitivity_up": round(up_sensitivity, 4) if up_sensitivity is not None else None,
                        "sensitivity_down": round(down_sensitivity, 4) if down_sensitivity is not None else None,
                    }
                )
        if not sensitivities:
            rows.append(
                {
                    "parameter": name,
                    "perturbation_fraction": fraction,
                    "mean_relative_response": 0.0,
                    "outputs": [],
                    "note": "no sensitive output measured (parameter has no "
                    "effect on the shell chain as configured)",
                }
            )
            continue
        ranking_sensitivities = [
            item for item in sensitivities
            if item["output"] in SENSITIVITY_RANKING_OUTPUTS
        ]
        # The mean and ranking use the stable outputs only (settle_s excluded
        # — see SENSITIVITY_RANKING_OUTPUTS); the per-output rows still carry
        # the full set (including settle_s) so the chaotic response stays
        # visible, ordered by response magnitude.
        sensitivities.sort(
            key=lambda item: -(abs(item["sensitivity_up"] or 0.0) + abs(item["sensitivity_down"] or 0.0)) / 2.0
        )
        mean_response = (
            sum(
                (abs(item["sensitivity_up"] or 0.0) + abs(item["sensitivity_down"] or 0.0)) / 2.0
                for item in ranking_sensitivities
            ) / len(ranking_sensitivities)
            if ranking_sensitivities else 0.0
        )
        row = {
            "parameter": name,
            "perturbation_fraction": fraction,
            "mean_relative_response": round(mean_response, 4),
            "outputs": sensitivities[:5],
        }
        if mean_response < 1e-6:
            row["note"] = (
                "no sensitive output measured: the parameter does not affect "
                "the shell chain outputs as configured (reported zero, not omitted)"
            )
        rows.append(row)
    rows.sort(key=lambda item: -item["mean_relative_response"])
    return {
        "rows": rows,
        "top_parameters": [item["parameter"] for item in rows if item["mean_relative_response"] > 0.01][:5],
        "note": (
            "sensitivity = relative output change per unit relative input change "
            "at +{:.0%} perturbation (end-to-end pipeline re-run; structural "
            "parameters re-solve the pinned closed-form case). It identifies "
            "WHAT NEEDS MEASUREMENT, not a parameter ranking for tuning. "
            "settle_s is reported per-output but EXCLUDED from the ranking: "
            "for rocking contacts the settle time is chaotically sensitive "
            "(sub-1e-6 relative input changes flip the settle branch), so its "
            "sensitivity values are not robust engineering measures.".format(fraction)
        ),
    }


def _perturbed_structural_response(base_request, baseline_result, name, fraction, run):
    """Re-solve the pinned structural case with a perturbed property.

    Mirrors the pipeline's structural section (same solver call, same
    payload shape) for the E / thickness / strength perturbations, and
    reports the relative response of the structural outputs.
    """
    from . import materials as materials_module
    from . import physics as physics_module

    structural = baseline_result.get("structural") or {}
    structure = dict(structural.get("structure") or {})
    load_case = structural.get("load_case")
    payload = structural.get("resolved_material")
    if payload is not None and not isinstance(payload, dict):
        try:
            payload = payload.to_dict()
        except Exception:
            payload = None
    if not isinstance(load_case, dict) or not structure:
        return []
    fixture = structural.get("fixtures")

    def solve_with(payload_dict):
        try:
            response = physics_module.solve_load_case(load_case, structure, payload_dict or {}, fixture)
            return response
        except Exception:
            return None

    baseline_response = structural.get("response") or {}
    baseline_stress = baseline_response.get("max_stress_pa")
    baseline_displacement = baseline_response.get("max_displacement_m")
    baseline_sf = baseline_response.get("safety_factor")

    def perturbed_payload(scale):
        if payload is None:
            return None
        modified = dict(payload)
        properties = dict(payload.get("properties") or {})
        if name == "youngs_modulus":
            for key in ("young_modulus", "young_modulus_x", "young_modulus_y", "young_modulus_z"):
                if key in properties:
                    properties[key] = _scale_quantity(properties[key], scale)
        elif name == "thickness":
            structure["t_m"] = float(structure["t_m"]) * scale
        elif name == "strength":
            for key in ("tensile_allowable", "yield_strength"):
                if key in properties:
                    properties[key] = _scale_quantity(properties[key], scale)
        modified["properties"] = properties
        try:
            return materials_module.MaterialDefinition.from_dict(modified)
        except Exception:
            return None

    def extract(response):
        data = response.to_dict() if hasattr(response, "to_dict") else {}
        return data.get("max_stress_pa"), data.get("max_displacement_m"), data.get("safety_factor")

    sensitivities = []
    for label, scale in (("up", 1.0 + fraction), ("down", 1.0 - fraction)):
        response = solve_with(perturbed_payload(scale))
        if response is None:
            continue
        stress, displacement, sf = extract(response)
        for key, baseline_value, value in (
            ("peak_stress_pa", baseline_stress, stress),
            ("max_displacement_m", baseline_displacement, displacement),
            ("safety_factor", baseline_sf, sf),
        ):
            if baseline_value is None or not math.isfinite(float(baseline_value)) or abs(float(baseline_value)) < 1e-12:
                continue
            if value is None:
                continue
            try:
                relative = ((float(value) - float(baseline_value)) / float(baseline_value)) / (
                    scale - 1.0
                )
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            entry = {"output": key, "sensitivity_up": None, "sensitivity_down": None}
            entry["sensitivity_up" if label == "up" else "sensitivity_down"] = round(relative, 4)
            sensitivities.append(entry)
    return sensitivities


def _scale_quantity(quantity, scale):
    """Scale a quantity that is a plain number, {value, unit}, or
    {value_si, unit} (the material to_dict representation)."""
    if isinstance(quantity, dict) and "value" in quantity:
        modified = dict(quantity)
        modified["value"] = float(quantity["value"]) * scale
        return modified
    if isinstance(quantity, dict) and "value_si" in quantity:
        modified = dict(quantity)
        modified["value_si"] = float(quantity["value_si"]) * scale
        return modified
    try:
        return float(quantity) * scale
    except (TypeError, ValueError):
        return quantity


def _trace_boundary_assumptions(request):
    """Describe the boundary conditions actually EXECUTED by the solver.

    The label must match the executed support (a cantilever beam is
    fixed-end, not simply-supported).  The structure section and the
    response metadata are the authorities.
    """
    structure = request.get("structure") or {}
    support = str(structure.get("support") or "").strip().lower()
    structure_type = str(structure.get("type") or "").strip().lower()
    if structure_type == "beam" and support == "cantilever":
        return (
            "fixed-end (cantilever) Euler-Bernoulli beam; see "
            "structural.response.metadata"
        )
    return (
        "simply-supported closed-form plate/beam (Navier / "
        "Euler-Bernoulli); see structural.response.metadata"
    )


def _derated_tensile_allowable_pa(material, temperature_k):
    """The derated tensile allowable the structural solve used, or None.

    Re-derives with the SAME physics material path the solver used
    (``physics._material_props`` applies the linear temperature derating to
    the catalog allowable at ``temperature_k``), so the persisted value is
    byte-identical to the allowable behind
    ``structural.response.safety_factor``.  Returns None when no
    temperature derating was applied, no allowable exists, or the material
    cannot be re-resolved.
    """
    if material is None or temperature_k is None:
        return None
    try:
        temperature_k = float(temperature_k)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(temperature_k):
        return None
    from . import materials as materials_module
    from . import physics as physics_module

    try:
        if isinstance(material, materials_module.MaterialDefinition):
            definition = material
        else:
            definition = materials_module.MaterialDefinition.from_dict(material)
    except Exception:
        definition = None
    if definition is None:
        return None
    try:
        E, nu, allowable, info = physics_module._material_props(definition, temperature_k)
    except Exception:
        return None
    if not info.get("derating_applied") or allowable is None:
        return None
    try:
        allowable = float(allowable)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(allowable) or allowable <= 0.0:
        return None
    return round(allowable, 6)


def build_shell_trace(request, result):
    """One authoritative trace of every quantity used by the shell result."""
    mass = result.get("mass") or {}
    drop = result.get("drop_simulation") or {}
    drop_model = drop.get("model") or {}
    structural = result.get("structural") or {}
    structural_response = structural.get("response") or {}
    material_used = structural.get("resolved_material")
    if material_used is not None and not isinstance(material_used, dict):
        try:
            material_used = material_used.to_dict()
        except Exception:
            material_used = None
    material_properties = {}
    if isinstance(material_used, dict):
        props = material_used.get("properties") or {}
        for key in ("density", "young_modulus", "poissons_ratio", "tensile_allowable", "yield_strength"):
            if key in props:
                material_properties[key] = props[key]
    # Persist the temperature-DERATED tensile allowable when the structural
    # solve applied linear temperature derating: the safety factor is
    # computed against this derated value (physics._material_props +
    # solve_load_case), and the FEA display fallback must prefer it over the
    # catalog (underated) allowable — one name must never mean two values.
    derated_tensile_allowable_pa = _derated_tensile_allowable_pa(
        material_used, (structural.get("load_case") or {}).get("temperature_k")
    )
    if derated_tensile_allowable_pa is not None:
        material_properties["derated_tensile_allowable_pa"] = derated_tensile_allowable_pa
    estimate = drop.get("peak_force_estimate") or {}
    return {
        "geometry": {
            "objects": [str(obj.get("id") or "") for obj in (request.get("objects") or [])],
            "geometry_digest": canonical.sha256_bytes(
                canonical.canonical_bytes(request.get("objects") or [])
            )[:16],
        },
        "material": {
            "label": structural.get("material"),
            "properties": material_properties,
        },
        "mass": {
            # The mass the SHELL CHAIN actually solved: the drop's effective
            # mass (prototype overrides / scales applied) when a drop ran;
            # the geometry-derived model mass is recorded separately (audit
            # finding: the trace previously reported the geometry mass while
            # the integrator solved a prototype-overridden body).
            "mass_kg": drop_model.get("mass_kg") if drop_model.get("mass_kg") is not None else mass.get("mass_kg"),
            "geometry_model_mass_kg": mass.get("mass_kg"),
            "mass_status": mass.get("mass_status"),
            "density_kg_m3": mass.get("density_kg_m3"),
        },
        "inertia": {
            # Audit finding (W2-12A/C): the trace reported the geometry-derived
            # tensor/CoM while the integrator solved an override/scaled body.
            # Record the SOLVED body with the geometry values alongside.
            "center_of_mass_m": (
                drop_model.get("com_offset_m")
                if drop_model.get("com_offset_m") is not None
                else mass.get("center_of_mass_m")
            ),
            "geometry_model_center_of_mass_m": mass.get("center_of_mass_m"),
            "inertia_tensor_kg_m2": (
                drop_model.get("inertia_kg_m2")
                if drop_model.get("inertia_kg_m2") is not None
                else mass.get("inertia_tensor_kg_m2")
            ),
            "geometry_model_inertia_tensor_kg_m2": mass.get("inertia_tensor_kg_m2"),
        },
        "drop": {
            "height_m": drop_model.get("height_m") if drop_model.get("height_m") is not None else (drop.get("config") or {}).get("height_m"),
            "gravity_m_s2": drop_model.get("gravity_m_s2"),
            "orientation": (drop.get("config") or {}).get("orientation"),
            "orientation_quaternion_wxyz": drop_model.get("orientation_quaternion_wxyz"),
            "initial_velocity_m_s": drop_model.get("initial_velocity_m_s"),
            "initial_angular_velocity_rad_s": drop_model.get("initial_angular_velocity_rad_s"),
            "surface": drop_model.get("surface"),
            "restitution": drop_model.get("restitution"),
            "friction": drop_model.get("friction"),
            "timestep_s": drop_model.get("timestep_s"),
            "integrator": drop_model.get("integrator"),
        },
        "contact": {
            "contact_stiffness_n_per_m": drop.get("contact_stiffness_n_per_m"),
            "peak_force_estimate": estimate or None,
        },
        "structural": {
            # The ACTUAL executed solver method (audit finding: the trace
            # previously hardcoded the Navier label even for beam cases).
            "model": structural_response.get("method_id"),
            "model_pin": (request.get("validation") or {}).get("structural", {}).get("model"),
            "load_case": structural.get("load_case"),
            # W2-12 follow-up: the boundary-assumptions label must match the
            # EXECUTED support (a cantilever beam is fixed-end, not
            # simply-supported).
            "boundary_assumptions": _trace_boundary_assumptions(request),
            "safety_factor_derivation": (
                "safety_factor = derated tensile_allowable / raw peak von Mises "
                "stress (physics._material_props + solve_load_case)"
            ),
            "safety_factor": structural_response.get("safety_factor"),
        },
        "engine": {
            "version": _engine_version(),
            "engine_hash": _engine_hash_value(),
            # run_id deliberately not included: it is request-identity, not a
            # physical quantity, and would break shell-output comparisons.
        },
        "seed": (drop.get("config") or {}).get("seed", 0),
    }


def _engine_version():
    from . import pipeline as pipeline_module

    return pipeline_module.ENGINE_VERSION


def _engine_hash_value():
    from . import pipeline as pipeline_module

    return pipeline_module._engine_hash()


def build_invalidating_assumptions(result):
    """The concise 'what would invalidate this result?' list."""
    assumptions = []
    validation = result.get("validation") or {}
    findings = validation.get("findings") or []
    finding_codes = {str(item.get("code") or "") for item in findings}
    if "SELF_INTERSECTION_UNVERIFIED" in finding_codes:
        assumptions.append(
            {"assumption": "geometry integrity", "status": "unverified",
             "impact": "mass and stress could change if the mesh self-intersects beyond the sweep limit"}
        )
    if any(item.get("code") == "GEOMETRY_ZERO_VOLUME" for item in findings) or any(
        item.get("code") == "OUTSIDE_SUPPORTED_PHYSICAL_SCALE" for item in findings
    ):
        assumptions.append(
            {"assumption": "geometry scale/validity", "status": "unsupported",
             "impact": "the geometry is outside the supported physical domain; mass is not certified"}
        )
    material = result.get("structural") or {}
    resolved = material.get("resolved_material")
    if resolved is not None and not isinstance(resolved, dict):
        try:
            resolved = resolved.to_dict()
        except Exception:
            resolved = None
    if not resolved:
        assumptions.append(
            {"assumption": "material properties", "status": "unresolved",
             "impact": "no material could be resolved; stress/SF are not computed"}
        )
    elif (resolved.get("provenance") or {}).get("confidence") != "high":
        assumptions.append(
            {"assumption": "material properties", "status": "screening",
             "impact": "material constants are class-level screening values; a wrong strength changes the SF directly"}
        )
    assumptions.append(
        {"assumption": "contact stiffness", "status": "uncalibrated",
         "impact": "k is the largest identified uncertainty; peak force/acceleration scale ~sqrt(k) (see the sweep)"}
    )
    assumptions.append(
        {"assumption": "restitution/friction", "status": "screening",
         "impact": "surface table values are engineering estimates; rebound and settle time depend on them"}
    )
    unsupported = []
    shell = result.get("shell") or {}
    limitations = shell.get("limitations") or []
    if limitations:
        unsupported = [str(item) for item in limitations if "unsupported" in str(item).lower()]
    if unsupported:
        assumptions.append(
            {"assumption": "unsupported failure modes", "status": "not_covered",
             "impact": "; ".join(unsupported)}
        )
    correlation = result.get("correlation")
    if correlation is None:
        assumptions.append(
            {"assumption": "physical correlation", "status": "no_measured_tests",
             "impact": "the model is UNVALIDATED against physical drops; internal consistency is not physical validation"}
        )
    elif correlation.get("verdict") != "pass":
        assumptions.append(
            {"assumption": "physical correlation", "status": "not_passed",
             "impact": "measured data does not yet confirm the model"}
        )
    else:
        # CERT-01 follow-up: the assumption label must key on the MODEL
        # STATUS, not the bare verdict — a 2-condition pass or a settle-only
        # pass has verdict "pass" but model_status unvalidated /
        # partially_validated.  Claiming "correlated" there contradicted the
        # four-state machine on the same disclosure card.
        model_status = (result.get("shell") or {}).get("model_status")
        if model_status == "correlated":
            # W11-02: the correlated count must follow the W2-13 discipline —
            # the VERDICT's evaluated conditions, never the broader
            # conditions list (which includes excluded diagnostic rows).
            conditions = int(correlation.get("evaluated_conditions") or 0)
            assumptions.append(
                {"assumption": "physical correlation", "status": "correlated",
                 "impact": "validated against {} measured conditions".format(conditions)}
            )
        else:
            assumptions.append(
                {"assumption": "physical correlation", "status": "not_correlated",
                 "impact": "the correlation verdict passed but the four-state "
                 "model did not reach CORRELATED (model_status {!r}: {}); "
                 "the model is not physically validated".format(
                     model_status,
                     ((result.get("shell") or {}).get("physical_validation") or {}).get("status"),
                 )}
            )
    return assumptions


def build_validation_tracks(request, result):
    """The two SEPARATE validation tracks (freeze-phase item 4).

    Drop tests validate the drop-dynamics chain (contact stiffness,
    restitution, friction, rigid-body dynamics, mass/CoM/inertia).  They do
    NOT validate the structural stress/SF model: the structural safety
    factor comes from the pinned quasi-static load case and the drop force
    never feeds it.  Structural validation requires a physical structural
    test with a known applied load.
    """
    objects = request.get("objects") or []
    shell_objects = [
        str(obj.get("id") or "")
        for obj in objects
        if str(obj.get("structural_behavior") or "solid") == "shell"
    ]
    context_objects = [
        str(obj.get("id") or "")
        for obj in objects
        if str(obj.get("structural_behavior") or "solid") != "shell"
    ]
    return {
        "physically_represented": shell_objects or [str(objects[0].get("id") or "")] if objects else [],
        "context_only": context_objects,
        "context_note": (
            "internal components (PCB, battery, switches, encoder, screws, "
            "clips, adhesives) exist ONLY as physical context for total mass, "
            "CoM and inertia; their physics is not validated by these tests "
            "and their screening results are not part of shell validation"
        ),
        "tracks": {
            "drop_dynamics": {
                "validated_quantities": [
                    "peak_acceleration_g",
                    "impact_duration_s",
                    "settle_time_s",
                    "rebound behavior",
                    "contact behavior",
                ],
                "validates": [
                    "contact stiffness",
                    "restitution",
                    "friction",
                    "rigid-body dynamics",
                    "mass/CoM/inertia",
                ],
                "note": "drop tests validate the drop-dynamics chain ONLY.",
            },
            "structural": {
                "validated_quantities": ["deformation", "stress", "strain", "safety_factor"],
                "requires": "a physical structural test with a known applied load",
                "note": (
                    "the structural safety factor comes from the pinned "
                    "quasi-static load case; the drop force does NOT feed it. "
                    "Matching accelerometer data does NOT validate the "
                    "structural solver."
                ),
            },
        },
    }


def build_model_status(result, validation_run=None):
    """Physical-validation model status — four states (freeze-phase item 12):

    - UNVALIDATED: no measured conditions compared yet.
    - PARTIALLY VALIDATED: some measured conditions were compared but the
      acceptance criteria are not fully satisfied.
    - CORRELATED: the predefined independent-condition criteria are
      satisfied (>= 3 distinct conditions, verdict pass).
    - OUTSIDE VALIDATED DOMAIN: correlated, but the CURRENT run's drop
      condition (height/surface/orientation) is not within the set of
      conditions the model was correlated against.

    Analytical correctness is separate from physical validation: model
    status NEVER rises from solver tests alone.
    """
    correlation = result.get("correlation")
    if correlation is None:
        return {
            "model_status": "unvalidated",
            "physical_validation": {
                "status": "no_measured_tests",
                "independent_conditions": 0,
                "note": "internal consistency is NOT physical validation; measured-drop "
                "correlation is required to raise model status to correlated",
            },
        }
    conditions = correlation.get("conditions") or []
    evaluated = [
        condition for condition in conditions if condition.get("metrics")
    ]
    verdict_conditions = int(correlation.get("evaluated_conditions") or 0)
    if verdict_conditions == 0:
        # Audit finding (W2-06A): an all-excluded or all-errored campaign
        # reported "partially validated, 0 conditions compared".  Nothing was
        # compared - that is UNVALIDATED.
        return {
            "model_status": "unvalidated",
            "physical_validation": {
                "status": "no_equivalent_conditions",
                "independent_conditions": 0,
                "compared_conditions": len(
                    [c for c in conditions if c.get("metrics")]
                ),
                "note": "no equivalent, identity-consistent measured condition "
                "could be evaluated; the model is unvalidated",
            },
        }
    if correlation.get("verdict") != "pass":
        return {
            "model_status": "partially_validated",
            "physical_validation": {
                "status": "compared_not_accepted",
                "independent_conditions": verdict_conditions,
                "compared_conditions": len(
                    [c for c in conditions if c.get("metrics")]
                ),
                "note": "{} measured condition(s) compared, but the acceptance "
                "criteria are not fully satisfied: {}".format(
                    verdict_conditions, correlation.get("explanation") or "see correlation"
                ),
            },
        }
    if verdict_conditions < 3:
        return {
            "model_status": "unvalidated",
            "physical_validation": {
                "status": "insufficient_conditions",
                "independent_conditions": verdict_conditions,
                "note": "fewer than 3 independent EQUIVALENT measured "
                "conditions: a passed comparison at this scale is not "
                "physical validation",
            },
        }
    # Audit finding: a settle-only (or duration-only) dataset previously
    # reached correlated without ever comparing the campaign's primary
    # observable.  Physical correlation of the drop model requires at least
    # one equivalent PEAK-ACCELERATION comparison.
    equivalent_evaluated = [
        condition
        for condition in evaluated
        if condition.get("equivalent", True) and condition.get("identity_ok", True)
    ]
    has_peak_accel = any(
        any(
            metric.get("metric_key") == "peak_accel_g"
            for metric in condition.get("metrics") or []
        )
        for condition in equivalent_evaluated
    )
    if not has_peak_accel:
        return {
            "model_status": "partially_validated",
            "physical_validation": {
                "status": "compared_not_accepted",
                "independent_conditions": verdict_conditions,
                "note": "{} condition(s) compared but none measured the peak "
                "acceleration — the drop model is not physically correlated "
                "on settle/duration data alone".format(verdict_conditions),
            },
        }
    # CORRELATED — record the validated domain and check the current run's
    # own drop condition against it.
    domain_conditions = set()
    for condition in equivalent_evaluated:
        height = condition.get("height_m")
        surface = str(condition.get("surface") or "").lower()
        quaternion = condition.get("orientation_quaternion_wxyz")
        if isinstance(quaternion, (list, tuple)) and len(quaternion) == 4:
            components = [float(component) for component in quaternion]
            sign = 1.0
            for component in components:
                if abs(component) > 1e-9:
                    sign = 1.0 if component > 0.0 else -1.0
                    break
            orientation_key = tuple(round(component * sign, 6) for component in components)
        else:
            orientation = condition.get("orientation")
            orientation_key = "explicit" if isinstance(orientation, dict) else str(orientation)
        if height is not None:
            domain_conditions.add((round(float(height), 4), surface, orientation_key))
    validated_domain = {
        "conditions": sorted(
            (height, surface, orientation)
            for height, surface, orientation in domain_conditions
        ),
        "note": "the model is correlated only for conditions in this set; "
        "any other drop condition is OUTSIDE the validated domain",
    }
    outside = False
    # W5-01 follow-up: the outside-domain check was gated on a validation
    # run, so exploration mode could NEVER emit outside_validated_domain —
    # the same campaign then reported correlated+high (exploration) vs
    # outside_validated_domain+medium (validation) for an extrapolated main
    # drop.  The current drop condition is available in BOTH modes (the
    # validation pins or the executed drop_simulation config); the check
    # applies whenever a domain exists.
    applied = (validation_run or {}).get("applied") or {}
    drop = applied.get("drop")
    if not drop:
        drop = (result.get("drop_simulation") or {}).get("config") or {}
    if drop:
        current_height = drop.get("height_m")
        current_surface = str(drop.get("surface") or "").lower()
        # The current pose is the ACTUALLY-SOLVED quaternion of the main drop
        # (canonicalized sign), so mode strings and explicit quaternions
        # compare consistently with the domain keys (audit follow-up: the
        # string "flat" never matched the identity-quaternion domain key).
        current_quaternion = (result.get("drop_simulation") or {}).get("model", {}).get(
            "orientation_quaternion_wxyz"
        )
        if isinstance(current_quaternion, (list, tuple)) and len(current_quaternion) == 4:
            components = [float(component) for component in current_quaternion]
            sign = 1.0
            for component in components:
                if abs(component) > 1e-9:
                    sign = 1.0 if component > 0.0 else -1.0
                    break
            current_key = tuple(round(component * sign, 6) for component in components)
        else:
            current_orientation = drop.get("orientation")
            current_key = (
                "explicit" if isinstance(current_orientation, dict) else str(current_orientation)
            )
        if current_height is not None:
            within = any(
                abs(current_height - height) < 0.01
                and current_surface == surface
                and current_key == orientation
                for height, surface, orientation in domain_conditions
            )
            outside = not within
    status = "outside_validated_domain" if outside else "validated"
    note = (
        "the current run's drop condition is OUTSIDE the correlated domain "
        "(height/surface/orientation not in the validated set); treat results "
        "as extrapolation"
        if outside
        else "predicted vs measured drop response within acceptance across "
        "{} independent conditions".format(verdict_conditions)
    )
    # W5-03 follow-up: exploration-mode raw correlation carries NO identity
    # declarations (cad_revision/material/prototype keys are rejected), so a
    # correlated exploration result has never been identity-cross-checked —
    # previously this was silent.  Disclose it on the result.
    identity_unchecked = validation_run is None
    if identity_unchecked:
        note = (
            note
            + " (identity cross-check not performed: exploration correlation "
            "carries no identity declarations; treat the correlated claim as "
            "unverified against CAD revision / material / prototype)"
        )
    return {
        "model_status": "outside_validated_domain" if outside else "correlated",
        "physical_validation": {
            "status": status,
            # W2-13 follow-up: the badge count must be the VERDICT's evaluated
            # conditions, never a broader metrics-bearing list.
            "independent_conditions": verdict_conditions,
            # W9-02/W10-01: correlated is now reachable with excluded
            # diagnostic rows present; disclose the full compared count so a
            # reader can see the exclusions were not silently dropped.
            "compared_conditions": len(
                [c for c in conditions if c.get("metrics")]
            ),
            "validated_domain": validated_domain,
            "identity_checked": not identity_unchecked,
            "note": note,
        },
    }


def build_uncertainty_bands(sweep_rows, structural_response, pinned_stiffness=None):
    """Uncertainty bands from the contact-stiffness sweep (never invented).

    When a sweep ran, the low/high of each quantity across the swept
    stiffness values is reported with its basis.  ``nominal`` is the swept
    row at the PINNED stiffness (the actual validation configuration), or
    the closest swept row when the pinned value is not in the sweep.  Without
    a sweep the bands are declared not_computed.
    """
    if not sweep_rows:
        return {
            "basis": "not_computed",
            "note": "run validation.contact_stiffness_sweep_n_per_m to quantify "
            "the stiffness-driven spread of the shell loading",
        }
    nominal_row = None
    nominal_is_fallback = False
    if pinned_stiffness is not None:
        for row in sweep_rows:
            if abs(float(row["contact_stiffness_n_per_m"]) - float(pinned_stiffness)) < 1e-9:
                nominal_row = row
                break
        if nominal_row is None and sweep_rows:
            # W12-02 follow-up: the fallback was the MIDDLE row of the
            # submission order (values[len//2]) with no disclosure — the
            # "nominal" then silently diverged from the reported headline at
            # the pinned stiffness, and reordering the sweep moved it.  The
            # fallback is the CLOSEST swept row, disclosed as such.
            nominal_row = min(
                sweep_rows,
                key=lambda row: abs(float(row["contact_stiffness_n_per_m"]) - float(pinned_stiffness)),
            )
            nominal_is_fallback = True
    bands = {}
    for key in (
        "peak_force_n",
        "peak_acceleration_m_s2",
        "contact_duration_s",
        "contact_compression_m",
        "load_path_stress_pa",
        "safety_factor",
    ):
        values = []
        for row in sweep_rows:
            value = row.get(key)
            if value is None:
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if not values:
            continue
        nominal = None
        if nominal_row is not None and nominal_row.get(key) is not None:
            try:
                nominal = float(nominal_row[key])
            except (TypeError, ValueError):
                nominal = None
        if nominal is None and values:
            nominal = values[len(values) // 2]
        if nominal is not None:
            bands[key] = {
                "low": round(min(values), 4),
                "high": round(max(values), 4),
                "nominal": round(nominal, 4),
            }
    note = (
        "spread across the swept contact stiffness values (uncalibrated k); "
        "NOT a statistical confidence interval"
    )
    if nominal_is_fallback:
        note = (
            "the pinned contact stiffness ({:.4g} N/m) is not in the swept "
            "set; band nominal is the closest swept row and differs from the "
            "headline value at the pinned stiffness. ".format(float(pinned_stiffness))
            + note
        )
    return {
        "basis": "contact_stiffness_sweep",
        "band": bands,
        "note": note,
    }


def measured_comparison(measured_tests, simulated_by_condition, validation_revision=None):
    """MEASURED vs SIMULATED comparison table.

    ``simulated_by_condition`` maps drop_id (= test_id) to the simulated peak
    acceleration (g) and the exact simulated pose — each measured test is
    paired with ITS OWN condition's simulation (audit finding: the previous
    physics-triple key cross-wired distinct explicit quaternions).  Computes
    per-test absolute/relative error, plus aggregate bias and RMSE over the
    EQUIVALENT, identity-consistent peak accelerations.  Measured data never
    modifies the physics.

    EQUIVALENCE DISCLOSURE: the simulated peak acceleration is the
    CoM-frame, quasi-static, single-point, rotation-free linear-spring peak
    deceleration (a = v*sqrt(k/m)); a surface-mounted accelerometer measures
    the body-frame acceleration at ITS location, which for corner/edge
    impacts includes rotational terms (alpha x r, omega x (omega x r)) of
    comparable magnitude.  The row is flagged ``equivalent`` only for flat
    impacts with a defined sensor at/near the CoM reading the resultant peak.
    """
    from . import drop_sim as drop_module

    rows = []
    for test in measured_tests:
        height = test["height_m"]
        surface = test["surface"]
        orientation = test["orientation"]
        simulated = simulated_by_condition.get(test["test_id"])
        measured_g = test["measured"].get("measured_peak_accel_g")
        sensor = test.get("sensor") or {}
        row = {
            "test_id": test["test_id"],
            "cad_revision": test["cad_revision"],
            "material": test["material"],
            "prototype_id": test["prototype_id"],
            "height_m": height,
            "surface": surface,
            "surface_definition": test.get("surface_definition") or {},
            "orientation": orientation,
            "environment": test["environment"],
            "sensor": sensor,
            "measured": test["measured"],
            "uncertainty": test["uncertainty"],
            "simulated": simulated,
            "simulated_quantity": (
                "CoM-frame quasi-static single-point linear-spring peak "
                "deceleration (a = v*sqrt(k/m)), rotation-free, continuous"
            ),
            "settle_criterion": (
                "simulated settle: sustained contact with |v| < 0.05 m/s and "
                "|omega| < 0.5 rad/s for a 0.4 s dwell"
            ),
            "surface_table_parameters": dict(drop_module.SURFACES[surface]),
        }
        if simulated is None:
            row["missing_simulation"] = True
        identity_ok = test.get("identity_ok", True)
        identity_flags = test.get("identity_flags") or []
        if not identity_ok:
            row["identity_mismatch"] = True
            row["identity_mismatch_note"] = "; ".join(identity_flags)
            if not simulated:
                row["identity_mismatch"] = True
                row["identity_mismatch_note"] = (
                    "test identity could not be verified against the run's "
                    "prototype; " + "; ".join(identity_flags)
                )
        if validation_revision and test["cad_revision"] and test["cad_revision"] != validation_revision:
            row["revision_mismatch"] = True
            row["revision_mismatch_note"] = (
                "measured test CAD revision {!r} differs from the validation "
                "revision {!r}: the test may belong to a different shell "
                "geometry".format(test["cad_revision"], validation_revision)
            )
        missing_uncertainty = []
        for measured_key, uncertainty_key in (
            ("measured_peak_accel_g", "measured_peak_accel_g_uncertainty"),
            ("measured_impact_duration_s", "measured_impact_duration_s_uncertainty"),
            ("measured_settle_s", "measured_settle_s_uncertainty"),
        ):
            if measured_key in test["measured"] and (
                uncertainty_key not in test["uncertainty"]
                or test["uncertainty"].get(uncertainty_key, 0.0) == 0.0
            ):
                missing_uncertainty.append(measured_key)
        if missing_uncertainty:
            row["uncertainty_missing"] = True
            row["uncertainty_missing_metrics"] = missing_uncertainty
        # Equivalence of the compared quantity: the authoritative value is the
        # condition's verdict-level flag (computed in the correlation section
        # from the sensor definition and the resolved orientation); absent
        # that, compute it here (flat + sensor at/near CoM + resultant).
        if simulated is not None and "equivalent" in simulated:
            row["equivalent"] = bool(simulated["equivalent"])
        else:
            location = sensor.get("location_body_m")
            # W2-16C follow-up: fail-closed — a sensor with NO location is
            # NOT near-CoM (the pipeline gate requires a defined location).
            near_com = (
                location is not None
                and math.sqrt(sum(float(component) ** 2 for component in location)) <= 0.005
            )
            flat_impact = (
                not isinstance(orientation, dict) and str(orientation) == "flat"
            )
            resultant = sensor.get("quantity", "resultant_peak_g") == "resultant_peak_g"
            sensor_defined = bool(sensor)
            row["equivalent"] = bool(flat_impact and sensor_defined and near_com and resultant)
        if not row["equivalent"]:
            condition_note = (simulated or {}).get("equivalence_note") if simulated else None
            row["equivalence_note"] = condition_note or (
                "the simulated peak is CoM-frame and rotation-free; a "
                "surface-mounted sensor reading body-frame acceleration at "
                "its own location includes rotational terms for non-flat "
                "impacts (factor ~2-3 at corner/edge) — the comparison is "
                "NOT directly equivalent unless the sensor is at the CoM "
                "during a flat impact reading the resultant peak"
            )
        if simulated is not None and simulated.get("peak_acceleration_g") is not None and measured_g is not None:
            simulated_g = simulated["peak_acceleration_g"]
            row["absolute_error_g"] = round(simulated_g - measured_g, 4)
            if abs(measured_g) > 1e-12:
                row["relative_error"] = round((simulated_g - measured_g) / abs(measured_g), 4)
            row["measured_minus_uncertainty_g"] = round(measured_g - test["uncertainty"].get("measured_peak_accel_g_uncertainty", 0.0), 4)
            row["measured_plus_uncertainty_g"] = round(measured_g + test["uncertainty"].get("measured_peak_accel_g_uncertainty", 0.0), 4)
        rows.append(row)
    # Aggregate over EQUIVALENT, identity-consistent rows only (audit
    # finding: the previous aggregate mixed non-equivalent rows whose
    # compared quantity differs by a factor ~2-3).
    peak_pairs = [
        (row["simulated"]["peak_acceleration_g"], row["measured"]["measured_peak_accel_g"])
        for row in rows
        if row.get("simulated") is not None
        and row["simulated"].get("peak_acceleration_g") is not None
        and row["measured"].get("measured_peak_accel_g") is not None
        and row.get("equivalent", True)
        and not row.get("identity_mismatch", False)
    ]
    aggregate = {}
    if peak_pairs:
        residuals = [simulated - measured for simulated, measured in peak_pairs]
        aggregate = {
            "count": len(peak_pairs),
            "bias_g": round(sum(residuals) / len(residuals), 4),
            "rmse_g": round(math.sqrt(sum(value * value for value in residuals) / len(residuals)), 4),
            "max_abs_error_g": round(max(abs(value) for value in residuals), 4),
        }
    excluded_from_aggregate = sum(
        1
        for row in rows
        if row.get("simulated") is not None
        and row["simulated"].get("peak_acceleration_g") is not None
        and row["measured"].get("measured_peak_accel_g") is not None
        and (not row.get("equivalent", True) or row.get("identity_mismatch", False))
    )
    return {
        "rows": rows,
        "aggregate": aggregate,
        "aggregate_excluded_non_equivalent": excluded_from_aggregate,
        "note": "comparison only; measured data never modifies the physics "
        "(calibration is explicit and reproducible). The compared quantity is "
        "the CoM-frame quasi-static linear-spring peak deceleration; the "
        "aggregate bias/RMSE covers EQUIVALENT, identity-consistent rows "
        "only — see the per-row equivalence and identity flags.",
    }


__all__ = [
    "apply_validation_config",
    "build_invalidating_assumptions",
    "build_model_status",
    "build_shell_trace",
    "build_uncertainty_bands",
    "measured_comparison",
    "run_contact_stiffness_sweep",
    "run_sensitivity",
]
