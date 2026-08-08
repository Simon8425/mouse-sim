"""Material catalog, assignment, and qualification services.

The core model owns the persisted material dataclasses.  This module owns the
operational concerns around them: catalog construction, tolerant JSON input,
strict validation, assignment resolution, and qualification gating.  Values
are kept as SI-normalized :class:`~mouse_sim.model.Quantity` instances and no
external material database or dependency is required.
"""

from dataclasses import dataclass, fields, replace
import json
import math
import re
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from .errors import ValidationError
from .model import (
    ApprovalState,
    Component,
    EntityMeta,
    EntityRef,
    EvidenceDisposition,
    GateCheck,
    GateResult,
    MassOverride,
    MaterialAssignment,
    MaterialDefinition,
    MaterialProperties,
    ModelBase,
    ProjectDocument,
    Provenance,
    Quantity,
    RegionRef,
    new_meta,
    with_content_hash,
)
from .units import normalize_unit, si_unit_for_dimension, unit_dimension


STRUCTURAL_BEHAVIORS = frozenset(
    ("shell", "solid", "rigid", "connector", "excluded_mass", "envelope")
)
MATERIAL_REQUIRED_BEHAVIORS = frozenset(("shell", "solid", "connector"))
TRACEABLE_SOURCE_TYPES = frozenset(
    ("catalog", "certificate", "manufacturer", "measured", "supplier", "test")
)
VALID_CONFIDENCE = frozenset(("low", "medium", "high"))


class MaterialCatalog(dict):
    """A mapping of stable display keys to immutable material definitions.

    Lookup is case-insensitive and accepts common punctuation variants while
    iteration preserves the catalog's canonical keys.
    """

    @staticmethod
    def _lookup_key(key):
        text = str(key).strip().casefold()
        text = text.replace("\\", "/")
        text = re.sub(r"[ _-]+", "", text)
        text = text.replace("/", "")
        return text

    def _key_for(self, key):
        wanted = self._lookup_key(key)
        for candidate in dict.__iter__(self):
            if self._lookup_key(candidate) == wanted:
                return candidate
        wanted_text = str(key).strip().casefold()
        for candidate, material in dict.items(self):
            if (
                material.meta.id.casefold() == wanted_text
                or material.name.strip().casefold() == wanted_text
            ):
                return candidate
        raise KeyError(key)

    def __getitem__(self, key):
        return dict.__getitem__(self, self._key_for(key))

    def __contains__(self, key):
        try:
            self._key_for(key)
        except (KeyError, TypeError):
            return False
        return True

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def resolve(self, key):
        """Resolve a catalog key, material ID, or material name."""

        if isinstance(key, MaterialDefinition):
            return key
        if isinstance(key, EntityRef):
            wanted = key.id
        elif isinstance(key, Mapping):
            wanted = key.get("id") or key.get("key") or key.get("name", "")
        else:
            wanted = key
        try:
            return self[wanted]
        except KeyError:
            wanted_text = str(wanted).strip().casefold()
            for material in dict.values(self):
                if (
                    material.meta.id.casefold() == wanted_text
                    or material.name.casefold() == wanted_text
                ):
                    return material
            raise KeyError(key)


@dataclass(frozen=True)
class ResolvedAssignment(ModelBase):
    """An assignment joined to its material and, when available, component."""

    assignment: MaterialAssignment
    material: Optional[MaterialDefinition] = None
    component: Optional[Component] = None
    effective_properties: Optional[MaterialProperties] = None
    structural_behavior: str = "solid"
    errors: Tuple[str, ...] = ()


AssignmentResolution = ResolvedAssignment


def _quantity(value, dimension, field_name):
    if value is None:
        return None
    expected_unit = si_unit_for_dimension(dimension)
    if isinstance(value, Quantity):
        try:
            unit = normalize_unit(value.unit)
            if unit_dimension(unit) != dimension:
                raise ValidationError("{} must have dimension {}".format(field_name, dimension))
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                raise
            raise ValidationError("{} has an invalid unit: {}".format(field_name, exc))
        if unit == expected_unit:
            quantity = Quantity(value.value_si, expected_unit)
        else:
            quantity = Quantity.from_value(value.value_si, unit)
    elif isinstance(value, Mapping):
        try:
            if "value_si" in value:
                unit = normalize_unit(value.get("unit", expected_unit))
                if unit_dimension(unit) != dimension:
                    raise ValidationError("{} must have dimension {}".format(field_name, dimension))
                quantity = Quantity(
                    value["value_si"], expected_unit
                ) if unit == expected_unit else Quantity.from_value(value["value_si"], unit)
            elif "value" in value:
                quantity = Quantity.from_value(value["value"], value.get("unit", expected_unit))
            else:
                raise ValidationError("{} must contain value or value_si".format(field_name))
        except ValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ValidationError("{} is invalid: {}".format(field_name, exc))
    else:
        try:
            if isinstance(value, bool):
                raise TypeError("boolean is not a numeric quantity")
            quantity = Quantity(float(value), expected_unit)
        except (TypeError, ValueError) as exc:
            raise ValidationError("{} must be numeric: {}".format(field_name, exc))
    return quantity


def _properties(
    density,
    young_modulus,
    poissons_ratio,
    yield_strength,
    ultimate_strength,
    tensile_allowable,
    compressive_allowable,
    shear_allowable,
    friction_coefficient,
    temperature_min_k,
    temperature_max_k,
    fatigue_strength_at_1e6_pa=None,
    fatigue_exponent_k=None,
    young_modulus_transverse_pa=None,
    young_modulus_thickness_pa=None,
    shear_modulus_xy_pa=None,
    shear_modulus_thickness_pa=None,
    poissons_ratio_xy=None,
    poissons_ratio_xz=None,
    weld_line_factor=None,
    continuous_use_temperature_min_k=None,
    continuous_use_temperature_max_k=None,
):
    return MaterialProperties(
        density=_quantity(density, "density", "density"),
        young_modulus=_quantity(young_modulus, "pressure", "young_modulus"),
        poissons_ratio=poissons_ratio,
        yield_strength=_quantity(yield_strength, "pressure", "yield_strength"),
        ultimate_strength=_quantity(ultimate_strength, "pressure", "ultimate_strength"),
        tensile_allowable=_quantity(tensile_allowable, "pressure", "tensile_allowable"),
        compressive_allowable=_quantity(compressive_allowable, "pressure", "compressive_allowable"),
        shear_allowable=_quantity(shear_allowable, "pressure", "shear_allowable"),
        friction_coefficient=friction_coefficient,
        temperature_min_k=_temperature(temperature_min_k, "temperature_min_k"),
        temperature_max_k=_temperature(temperature_max_k, "temperature_max_k"),
        fatigue_strength_at_1e6_pa=_quantity(
            fatigue_strength_at_1e6_pa, "pressure", "fatigue_strength_at_1e6_pa"
        ),
        fatigue_exponent_k=fatigue_exponent_k,
        young_modulus_transverse_pa=_quantity(
            young_modulus_transverse_pa, "pressure", "young_modulus_transverse_pa"
        ),
        young_modulus_thickness_pa=_quantity(
            young_modulus_thickness_pa, "pressure", "young_modulus_thickness_pa"
        ),
        shear_modulus_xy_pa=_quantity(shear_modulus_xy_pa, "pressure", "shear_modulus_xy_pa"),
        shear_modulus_thickness_pa=_quantity(
            shear_modulus_thickness_pa, "pressure", "shear_modulus_thickness_pa"
        ),
        poissons_ratio_xy=poissons_ratio_xy,
        poissons_ratio_xz=poissons_ratio_xz,
        weld_line_factor=weld_line_factor,
        continuous_use_temperature_min_k=_temperature(
            continuous_use_temperature_min_k, "continuous_use_temperature_min_k"
        ),
        continuous_use_temperature_max_k=_temperature(
            continuous_use_temperature_max_k, "continuous_use_temperature_max_k"
        ),
    )


def _temperature(value, field_name):
    if value is None:
        return None
    if isinstance(value, Quantity):
        if unit_dimension(value.unit) != "temperature":
            raise ValidationError("{} must be a temperature".format(field_name))
        return value.to_si()
    if isinstance(value, Mapping):
        return _quantity(value, "temperature", field_name).to_si()
    return float(value)


DEFAULT_MATERIAL_KEY = "default"


def _builtin_definition(
    key,
    name,
    family,
    density,
    young_modulus,
    poissons_ratio,
    yield_strength,
    ultimate_strength,
    tensile_allowable,
    compressive_allowable,
    shear_allowable,
    friction_coefficient,
    condition="nominal room temperature, dry",
    fatigue_strength_at_1e6_pa=None,
    fatigue_exponent_k=None,
    young_modulus_transverse_pa=None,
    young_modulus_thickness_pa=None,
    shear_modulus_xy_pa=None,
    shear_modulus_thickness_pa=None,
    poissons_ratio_xy=None,
    poissons_ratio_xz=None,
    weld_line_factor=None,
    continuous_use_temperature_min_k=293.15,
    continuous_use_temperature_max_k=293.15,
    anisotropy_supported=False,
):
    properties = _properties(
        density,
        young_modulus,
        poissons_ratio,
        yield_strength,
        ultimate_strength,
        tensile_allowable,
        compressive_allowable,
        shear_allowable,
        friction_coefficient,
        293.15,
        293.15,
        fatigue_strength_at_1e6_pa=fatigue_strength_at_1e6_pa,
        fatigue_exponent_k=fatigue_exponent_k,
        young_modulus_transverse_pa=young_modulus_transverse_pa,
        young_modulus_thickness_pa=young_modulus_thickness_pa,
        shear_modulus_xy_pa=shear_modulus_xy_pa,
        shear_modulus_thickness_pa=shear_modulus_thickness_pa,
        poissons_ratio_xy=poissons_ratio_xy,
        poissons_ratio_xz=poissons_ratio_xz,
        weld_line_factor=weld_line_factor,
        continuous_use_temperature_min_k=continuous_use_temperature_min_k,
        continuous_use_temperature_max_k=continuous_use_temperature_max_k,
    )
    provenance = Provenance(
        source_type="catalog",
        source_id="mouse_sim_builtin_v1",
        citation="mouse_sim engineering reference catalog v1",
        condition=condition,
        temperature_k=293.15,
        confidence="low",
    )
    material_id = "mat_{}_v1".format(re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_"))
    definition = MaterialDefinition(
        meta=new_meta("MaterialDefinition", material_id, revision=1),
        name=name,
        family=family,
        properties=properties,
        provenance=provenance,
        approval_state=ApprovalState.DRAFT,
        anisotropy_supported=anisotropy_supported,
    )
    return with_content_hash(definition)


def builtin_materials():
    """Return the built-in exploratory material catalog.

    Built-ins intentionally use ``approval_state=draft`` and low confidence.
    They are useful for exploration and mass estimates but cannot pass the
    qualification material gate without a separately approved record.

    Fatigue endurance data (stress amplitude at 1e6 cycles, R~0.1) and
    continuous-use temperature ranges are drawn from the polymer supplier
    datasheet class (e.g., SABIC/Covestro/BASF modulus-vs-temperature and
    fatigue curves, MIL-HDBK-5 class values for steel) and are intentionally
    conservative screening values, not certified data.
    """

    entries = (
        {
            "key": "ABS", "name": "ABS", "family": "thermoplastic",
            "density": 1040, "young_modulus": 2.3e9, "poissons_ratio": 0.35,
            "yield_strength": 40e6, "ultimate_strength": 45e6,
            "tensile_allowable": 20e6, "compressive_allowable": 60e6,
            "shear_allowable": 25e6, "friction_coefficient": 0.35,
            "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 363.15,
        },
        {
            "key": "PC", "name": "Polycarbonate", "family": "thermoplastic",
            "density": 1200, "young_modulus": 2.35e9, "poissons_ratio": 0.37,
            "yield_strength": 65e6, "ultimate_strength": 70e6,
            "tensile_allowable": 35e6, "compressive_allowable": 80e6,
            "shear_allowable": 40e6, "friction_coefficient": 0.30,
            "fatigue_strength_at_1e6_pa": 25e6, "fatigue_exponent_k": 6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 398.15,
        },
        {
            "key": "PC/ABS", "name": "PC/ABS blend", "family": "thermoplastic",
            "density": 1160, "young_modulus": 2.2e9, "poissons_ratio": 0.36,
            "yield_strength": 45e6, "ultimate_strength": 55e6,
            "tensile_allowable": 25e6, "compressive_allowable": 65e6,
            "shear_allowable": 30e6, "friction_coefficient": 0.32,
            "fatigue_strength_at_1e6_pa": 20e6, "fatigue_exponent_k": 6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 373.15,
        },
        {
            "key": "POM", "name": "Polyoxymethylene", "family": "thermoplastic",
            "density": 1410, "young_modulus": 3.0e9, "poissons_ratio": 0.35,
            "yield_strength": 65e6, "ultimate_strength": 70e6,
            "tensile_allowable": 35e6, "compressive_allowable": 85e6,
            "shear_allowable": 40e6, "friction_coefficient": 0.25,
            "fatigue_strength_at_1e6_pa": 30e6, "fatigue_exponent_k": 6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 363.15,
        },
        {
            "key": "nylon", "name": "Nylon 6/6", "family": "thermoplastic",
            "density": 1140, "young_modulus": 2.7e9, "poissons_ratio": 0.39,
            "yield_strength": 50e6, "ultimate_strength": 75e6,
            "tensile_allowable": 25e6, "compressive_allowable": 70e6,
            "shear_allowable": 30e6, "friction_coefficient": 0.30,
            "fatigue_strength_at_1e6_pa": 20e6, "fatigue_exponent_k": 6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 373.15,
        },
        {
            "key": "TPU", "name": "Thermoplastic polyurethane", "family": "elastomer",
            "density": 1200, "young_modulus": 25e6, "poissons_ratio": 0.49,
            "yield_strength": 10e6, "ultimate_strength": 25e6,
            "tensile_allowable": 5e6, "compressive_allowable": 20e6,
            "shear_allowable": 8e6, "friction_coefficient": 0.50,
            "continuous_use_temperature_min_k": 243.15,
            "continuous_use_temperature_max_k": 343.15,
        },
        {
            "key": "FR4", "name": "FR-4 glass epoxy", "family": "laminate",
            "density": 1850, "young_modulus": 22e9, "poissons_ratio": 0.14,
            "yield_strength": 250e6, "ultimate_strength": 350e6,
            "tensile_allowable": 200e6, "compressive_allowable": 300e6,
            "shear_allowable": 100e6, "friction_coefficient": 0.40,
            "fatigue_strength_at_1e6_pa": 65e6, "fatigue_exponent_k": 10,
            "young_modulus_transverse_pa": 22e9, "young_modulus_thickness_pa": 9e9,
            "shear_modulus_xy_pa": 7e9, "shear_modulus_thickness_pa": 3.5e9,
            "poissons_ratio_xy": 0.14, "poissons_ratio_xz": 0.34,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 403.15,
            "anisotropy_supported": True,
        },
        {
            "key": "LiPo", "name": "Lithium-polymer battery pack", "family": "battery",
            "density": 2500, "young_modulus": 100e6, "poissons_ratio": 0.30,
            "yield_strength": 5e6, "ultimate_strength": 10e6,
            "tensile_allowable": 3e6, "compressive_allowable": 5e6,
            "shear_allowable": 2e6, "friction_coefficient": 0.40,
            "continuous_use_temperature_min_k": 253.15,
            "continuous_use_temperature_max_k": 333.15,
        },
        {
            "key": "steel", "name": "Low-carbon steel", "family": "metal",
            "density": 7850, "young_modulus": 200e9, "poissons_ratio": 0.30,
            "yield_strength": 250e6, "ultimate_strength": 400e6,
            "tensile_allowable": 150e6, "compressive_allowable": 250e6,
            "shear_allowable": 100e6, "friction_coefficient": 0.45,
            "fatigue_strength_at_1e6_pa": 220e6, "fatigue_exponent_k": 5,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 473.15,
        },
        {
            "key": "PTFE", "name": "Polytetrafluoroethylene", "family": "polymer",
            "density": 2200, "young_modulus": 0.5e9, "poissons_ratio": 0.46,
            "yield_strength": 20e6, "ultimate_strength": 30e6,
            "tensile_allowable": 10e6, "compressive_allowable": 25e6,
            "shear_allowable": 12e6, "friction_coefficient": 0.10,
            "continuous_use_temperature_min_k": 73.15,
            "continuous_use_temperature_max_k": 533.15,
        },
        {
            "key": "magnesium/aluminum", "name": "Magnesium/Aluminum alloy", "family": "metal",
            "density": 2235, "young_modulus": 57e9, "poissons_ratio": 0.31,
            "yield_strength": 155e6, "ultimate_strength": 265e6,
            "tensile_allowable": 90e6, "compressive_allowable": 150e6,
            "shear_allowable": 58e6, "friction_coefficient": 0.35,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 423.15,
        },
        {
            "key": DEFAULT_MATERIAL_KEY,
            "name": "Default (generic polymer)",
            "family": "generic_polymer",
            "density": 1160, "young_modulus": 2.0e9, "poissons_ratio": 0.36,
            "yield_strength": 40e6, "ultimate_strength": 50e6,
            "tensile_allowable": 25e6, "compressive_allowable": 60e6,
            "shear_allowable": 30e6, "friction_coefficient": 0.35,
            "fatigue_strength_at_1e6_pa": 14e6, "fatigue_exponent_k": 6,
            "continuous_use_temperature_min_k": 233.15,
            "continuous_use_temperature_max_k": 363.15,
        },
    )
    catalog = MaterialCatalog()
    for entry in entries:
        definition = _builtin_definition(**entry)
        catalog[entry["key"]] = definition
    return catalog


def default_material_definition():
    """Return the built-in Default material definition.

    The Default material is the deterministic fallback for any object
    without an explicit material: it carries full physical properties
    (density, modulus, Poisson ratio, strength allowables, friction) so a
    simulation never fails or produces undefined behavior because a
    component has no assigned material.  It is a conservative generic
    engineering polymer, intentionally low-confidence and DRAFT.
    """
    return _builtin_definition(
        DEFAULT_MATERIAL_KEY,
        "Default (generic polymer)",
        "generic_polymer",
        1160,
        2.0e9,
        0.36,
        40e6,
        50e6,
        25e6,
        60e6,
        30e6,
        0.35,
        fatigue_strength_at_1e6_pa=14e6,
        fatigue_exponent_k=6,
        continuous_use_temperature_min_k=233.15,
        continuous_use_temperature_max_k=363.15,
    )


def ensure_default_material(catalog):
    """Ensure a catalog carries a usable Default material entry.

    Returns a catalog of the SAME type (a ``MaterialCatalog`` is preserved so
    case-insensitive and alias lookup keep working — a plain dict rebuild
    silently disabled them).  User-supplied catalogs that define their own
    'default' key keep it; otherwise the built-in Default material is added
    so fallback assignment is always possible.
    """
    if DEFAULT_MATERIAL_KEY in catalog:
        return catalog
    if isinstance(catalog, MaterialCatalog):
        rebuilt = MaterialCatalog()
        for key, value in catalog.items():
            rebuilt[key] = value
    else:
        rebuilt = dict(catalog)
    rebuilt[DEFAULT_MATERIAL_KEY] = default_material_definition()
    return rebuilt


def _provenance(value):
    if value is None:
        return Provenance()
    if isinstance(value, Provenance):
        return value
    if not isinstance(value, Mapping):
        raise ValidationError("provenance must be an object")
    data = dict(value)
    if "conditioning" in data and "condition" not in data:
        data["condition"] = data.pop("conditioning")
    return Provenance.from_dict(data)


def _definition_from_mapping(payload, default_key=None):
    if isinstance(payload, MaterialDefinition):
        return payload
    if not isinstance(payload, Mapping):
        raise ValidationError("material definition must be an object")
    data = dict(payload.get("definition", payload))
    key = data.get("key") or data.get("id") or default_key or data.get("name", "material")
    meta_data = data.get("meta")
    if meta_data is None:
        material_id = "mat_{}".format(re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_"))
        meta = new_meta("MaterialDefinition", material_id, revision=1)
    else:
        meta = data["meta"] if isinstance(data["meta"], EntityMeta) else EntityMeta.from_dict(data["meta"])

    raw_properties = data.get("properties", {})
    if isinstance(raw_properties, MaterialProperties):
        properties = raw_properties
    else:
        if not isinstance(raw_properties, Mapping):
            raise ValidationError("properties must be an object")
        properties = _properties(
            raw_properties.get("density"),
            raw_properties.get("young_modulus", raw_properties.get("youngs_modulus")),
            raw_properties.get("poissons_ratio", raw_properties.get("poisson_ratio")),
            raw_properties.get("yield_strength"),
            raw_properties.get("ultimate_strength"),
            raw_properties.get("tensile_allowable"),
            raw_properties.get("compressive_allowable"),
            raw_properties.get("shear_allowable"),
            raw_properties.get("friction_coefficient", raw_properties.get("friction")),
            raw_properties.get("temperature_min_k", raw_properties.get("temperature_min")),
            raw_properties.get("temperature_max_k", raw_properties.get("temperature_max")),
            fatigue_strength_at_1e6_pa=raw_properties.get(
                "fatigue_strength_at_1e6_pa", raw_properties.get("fatigue_strength_pa")
            ),
            fatigue_exponent_k=raw_properties.get("fatigue_exponent_k"),
            young_modulus_transverse_pa=raw_properties.get(
                "young_modulus_transverse_pa",
                raw_properties.get("young_modulus_e2_pa", raw_properties.get("e2_pa")),
            ),
            young_modulus_thickness_pa=raw_properties.get(
                "young_modulus_thickness_pa",
                raw_properties.get("young_modulus_e3_pa", raw_properties.get("e3_pa")),
            ),
            shear_modulus_xy_pa=raw_properties.get(
                "shear_modulus_xy_pa",
                raw_properties.get("shear_modulus_g12_pa", raw_properties.get("g12_pa")),
            ),
            shear_modulus_thickness_pa=raw_properties.get(
                "shear_modulus_thickness_pa",
                raw_properties.get("shear_modulus_g13_pa", raw_properties.get("g13_pa")),
            ),
            poissons_ratio_xy=raw_properties.get(
                "poissons_ratio_xy", raw_properties.get("poissons_ratio_12")
            ),
            poissons_ratio_xz=raw_properties.get(
                "poissons_ratio_xz", raw_properties.get("poissons_ratio_13")
            ),
            weld_line_factor=raw_properties.get("weld_line_factor"),
            continuous_use_temperature_min_k=raw_properties.get(
                "continuous_use_temperature_min_k", raw_properties.get("continuous_use_temp_min_k")
            ),
            continuous_use_temperature_max_k=raw_properties.get(
                "continuous_use_temperature_max_k", raw_properties.get("continuous_use_temp_max_k")
            ),
        )
    approval = data.get("approval_state", ApprovalState.DRAFT)
    if not isinstance(approval, ApprovalState):
        approval = ApprovalState(approval)
    definition = MaterialDefinition(
        meta=meta,
        name=data.get("name", str(key)),
        family=data.get("family", ""),
        properties=properties,
        provenance=_provenance(data.get("provenance")),
        approval_state=approval,
        material_lot=data.get("material_lot"),
        anisotropy_supported=bool(data.get("anisotropy_supported", False)),
    )
    return definition


def _catalog_payload(source):
    if isinstance(source, (str, Path)):
        text = str(source)
        if text.lstrip().startswith(("{", "[")):
            try:
                return json.loads(text)
            except (TypeError, ValueError) as exc:
                raise ValidationError("invalid material catalog JSON: {}".format(exc))
        try:
            with Path(source).open("r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, TypeError, ValueError) as exc:
            raise ValidationError("unable to load material catalog: {}".format(exc))
    if hasattr(source, "read"):
        try:
            return json.load(source)
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid material catalog JSON: {}".format(exc))
    return source


def load_material_catalog(source=None, include_builtins=False, validate=True):
    """Load material definitions from JSON, a path, or a mapping.

    Accepted JSON roots are ``{"materials": [...]}``, a list of definitions,
    or a mapping from catalog key to definition.  Numeric properties are
    interpreted using their documented SI units unless an explicit unit is
    supplied.
    """

    if source is None:
        payload = {"materials": []}
    else:
        payload = _catalog_payload(source)
    if isinstance(payload, Mapping) and "materials" in payload:
        raw_materials = payload["materials"]
    elif isinstance(payload, Mapping):
        raw_materials = payload
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        raw_materials = payload
    else:
        raise ValidationError("material catalog must be an object or array")

    if not isinstance(raw_materials, Mapping) and (
        not isinstance(raw_materials, Sequence)
        or isinstance(raw_materials, (str, bytes, bytearray))
    ):
        raise ValidationError("materials must be an object or array")

    catalog = builtin_materials() if include_builtins else MaterialCatalog()
    if isinstance(raw_materials, Mapping):
        items = raw_materials.items()
    else:
        items = ((None, item) for item in raw_materials)
    for key, payload_item in items:
        label = "materials[{}]".format(key) if key is not None else "materials[{}]".format(len(catalog))
        try:
            definition = _definition_from_mapping(payload_item, default_key=key)
            if validate:
                validate_material(definition)
        except (TypeError, ValueError) as exc:
            details = getattr(exc, "errors", ()) or (str(exc),)
            raise ValidationError("invalid material catalog entry {}".format(label), details)
        catalog[key if key is not None else definition.name] = definition
    return catalog


def _material_definition(material):
    if isinstance(material, MaterialDefinition):
        return material
    if isinstance(material, Mapping):
        return _definition_from_mapping(material)
    raise ValidationError("expected a MaterialDefinition")


def _quantity_errors(quantity, field_name, dimension, positive=False):
    errors = []
    if quantity is None:
        return errors
    if not isinstance(quantity, Quantity):
        return ["{} must be a Quantity".format(field_name)]
    try:
        actual = unit_dimension(quantity.unit)
        expected_unit = si_unit_for_dimension(dimension)
    except (TypeError, ValueError) as exc:
        return ["{} has an invalid unit: {}".format(field_name, exc)]
    if actual != dimension:
        errors.append("{} must have dimension {}".format(field_name, dimension))
    if quantity.unit != expected_unit:
        errors.append("{} must be SI-normalized as {}".format(field_name, expected_unit))
    try:
        value = float(quantity.value_si)
    except (TypeError, ValueError):
        errors.append("{} must be numeric".format(field_name))
        return errors
    if value != value or value in (float("inf"), float("-inf")):
        errors.append("{} must be finite".format(field_name))
    if positive and value <= 0:
        errors.append("{} must be positive".format(field_name))
    return errors


def material_validation_errors(material, require_structural=True, required_fields=()):
    """Return validation messages without raising an exception."""

    definition = _material_definition(material)
    properties = definition.properties
    errors = []
    if require_structural and properties.density is None:
        errors.append("density is required")
    if require_structural and properties.young_modulus is None:
        errors.append("young_modulus is required")
    errors.extend(_quantity_errors(properties.density, "density", "density", positive=True))
    errors.extend(_quantity_errors(properties.young_modulus, "young_modulus", "pressure", positive=True))
    for field_name in (
        "yield_strength",
        "ultimate_strength",
        "tensile_allowable",
        "compressive_allowable",
        "shear_allowable",
        "fatigue_strength_at_1e6_pa",
        "young_modulus_transverse_pa",
        "young_modulus_thickness_pa",
        "shear_modulus_xy_pa",
        "shear_modulus_thickness_pa",
    ):
        errors.extend(_quantity_errors(getattr(properties, field_name), field_name, "pressure", positive=True))
    # Plausibility bounds: absurd-but-finite values (density 1e12 kg/m^3,
    # modulus 1e15 Pa) were silently used and produced confident results.
    # The bounds are far outside any engineering polymer/metal/ceramic data
    # and act as a sanity floor, not a tight specification.
    def quantity_value(quantity):
        try:
            return float(quantity.value_si)
        except (TypeError, ValueError, AttributeError):
            return None

    if properties.density is not None:
        density_value = quantity_value(properties.density)
        if density_value is not None and density_value > 3e4:
            errors.append("density {} kg/m^3 is implausible (max screening bound 3e4)".format(density_value))
    if properties.young_modulus is not None:
        modulus_value = quantity_value(properties.young_modulus)
        if modulus_value is not None and modulus_value > 1e13:
            errors.append("young_modulus {} Pa is implausible (max screening bound 1e13)".format(modulus_value))
    e1 = quantity_value(properties.young_modulus)
    e2 = quantity_value(properties.young_modulus_transverse_pa)
    e3 = quantity_value(properties.young_modulus_thickness_pa)
    if e1 is not None and e1 > 0.0:
        for label, value in (("E2", e2), ("E3", e3)):
            if value is not None and (value > 100.0 * e1 or value < e1 / 100.0):
                errors.append(
                    "{} {:g} Pa is inconsistent with E1 {:g} Pa (screening bound: within 100x)".format(
                        label, value, e1
                    )
                )
    if (properties.fatigue_strength_at_1e6_pa is None) != (properties.fatigue_exponent_k is None):
        errors.append(
            "fatigue_strength_at_1e6_pa and fatigue_exponent_k must be set together"
        )
    if definition.anisotropy_supported:
        for field_name in (
            "young_modulus_transverse_pa",
            "young_modulus_thickness_pa",
            "shear_modulus_xy_pa",
            "poissons_ratio_xy",
        ):
            if getattr(properties, field_name) is None:
                errors.append(
                    "anisotropy_supported requires {} (E2, E3, G12, nu12 quartet)".format(field_name)
                )
    ratio = properties.poissons_ratio
    if ratio is not None:
        if not _finite_number(ratio) or not (-1.0 < float(ratio) < 0.5):
            errors.append("poissons_ratio must be finite and between -1 and 0.5")
    for field_name in ("poissons_ratio_xy", "poissons_ratio_xz"):
        ratio = getattr(properties, field_name)
        if ratio is not None and (not _finite_number(ratio) or not (-1.0 < float(ratio) < 0.5)):
            errors.append("{} must be finite and between -1 and 0.5".format(field_name))
    friction = properties.friction_coefficient
    if friction is not None and (not _finite_number(friction) or float(friction) < 0):
        errors.append("friction_coefficient must be finite and non-negative")
    weld = properties.weld_line_factor
    if weld is not None and (not _finite_number(weld) or not (0.4 <= float(weld) <= 0.8)):
        errors.append("weld_line_factor must be finite and between 0.4 and 0.8")
    for field_name in ("temperature_min_k", "temperature_max_k"):
        value = getattr(properties, field_name)
        if value is not None and (not _finite_number(value) or float(value) <= 0):
            errors.append("{} must be finite and positive".format(field_name))
    for field_name in ("continuous_use_temperature_min_k", "continuous_use_temperature_max_k"):
        value = getattr(properties, field_name)
        if value is not None and (not _finite_number(value) or float(value) <= 0):
            errors.append("{} must be finite and positive".format(field_name))
    if properties.temperature_min_k is not None and properties.temperature_max_k is not None:
        if properties.temperature_min_k > properties.temperature_max_k:
            errors.append("temperature_min_k must not exceed temperature_max_k")
    if (
        properties.continuous_use_temperature_min_k is not None
        and properties.continuous_use_temperature_max_k is not None
        and properties.continuous_use_temperature_min_k > properties.continuous_use_temperature_max_k
    ):
        errors.append(
            "continuous_use_temperature_min_k must not exceed continuous_use_temperature_max_k"
        )
    provenance = definition.provenance
    if provenance.confidence not in VALID_CONFIDENCE:
        errors.append("provenance.confidence must be low, medium, or high")
    for field_name in required_fields:
        if getattr(properties, field_name) is None:
            errors.append("{} is required".format(field_name))
    return tuple(errors)


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in (float("inf"), float("-inf"))


def validate_material(material, require_structural=True):
    """Validate and return a material definition."""

    definition = _material_definition(material)
    errors = material_validation_errors(definition, require_structural=require_structural)
    if errors:
        raise ValidationError("invalid material definition", errors)
    return definition


def mass_override_validation_errors(override, qualification=False):
    """Return validation messages for a measured mass override."""

    if override is None:
        return ("mass override is required",)
    if not isinstance(override, MassOverride):
        try:
            override = MassOverride.from_dict(override)
        except (TypeError, ValueError) as exc:
            return ("invalid mass override: {}".format(exc),)
    errors = _quantity_errors(override.measured_mass, "measured_mass", "mass", positive=True)
    if override.measured_mass is None:
        errors.append("measured_mass is required")
    if override.uncertainty is not None:
        errors.extend(_quantity_errors(override.uncertainty, "uncertainty", "mass", positive=False))
        if override.uncertainty.value_si < 0:
            errors.append("uncertainty must be non-negative")
        if override.measured_mass is not None and override.uncertainty.value_si > override.measured_mass.value_si:
            errors.append("uncertainty must not exceed measured_mass")
    if qualification:
        if not override.reviewed:
            errors.append("mass override must be reviewed for qualification")
        errors.extend(_provenance_gate_errors(override.provenance))
    return tuple(errors)


def validate_mass_override(override, qualification=False):
    errors = mass_override_validation_errors(override, qualification=qualification)
    if errors:
        raise ValidationError("invalid measured mass override", errors)
    return override


def _material_catalog(materials):
    if materials is None:
        return builtin_materials()
    if isinstance(materials, MaterialCatalog):
        return materials
    if isinstance(materials, ProjectDocument):
        materials = materials.material_definitions
    if isinstance(materials, Mapping):
        catalog = MaterialCatalog()
        for key, item in materials.items():
            definition = _material_definition(item)
            catalog[key] = definition
        return catalog
    catalog = MaterialCatalog()
    for item in materials:
        definition = _material_definition(item)
        catalog[definition.name] = definition
    return catalog


def _components_by_id(components):
    if components is None:
        return {}
    if isinstance(components, ProjectDocument):
        components = components.components
    if isinstance(components, Component):
        components = (components,)
    if isinstance(components, Mapping):
        values = components.values()
    else:
        values = components
    result = {}
    for component in values:
        if isinstance(component, Mapping):
            component = Component.from_dict(component)
        result[component.meta.id] = component
    return result


def _assignments(values):
    if isinstance(values, MaterialAssignment):
        return (values,)
    if isinstance(values, Mapping):
        if "meta" in values or "material_ref" in values:
            return (MaterialAssignment.from_dict(values),)
        values = values.values()
    return tuple(value if isinstance(value, MaterialAssignment) else MaterialAssignment.from_dict(value) for value in values)


def _ref_key(ref):
    if ref is None:
        return ""
    if isinstance(ref, EntityRef):
        return ref.id
    if isinstance(ref, Mapping):
        return ref.get("id", "")
    return str(ref)


def _properties_with_overrides(base, override):
    if base is None or override is None:
        return base
    values = {
        item.name: getattr(override, item.name)
        for item in fields(MaterialProperties)
        if getattr(override, item.name) is not None
    }
    return replace(base, **values)


def resolve_assignments(
    assignments,
    materials=None,
    components=None,
    strict=True,
    component=None,
    component_id=None,
):
    """Resolve material assignments by material and component references.

    The first argument may be a sequence of assignments, a single assignment,
    a component, or a complete ``ProjectDocument``.  The return value is a
    tuple of :class:`ResolvedAssignment` records in input order.
    """

    document = assignments if isinstance(assignments, ProjectDocument) else None
    if isinstance(assignments, Component):
        component = assignments
        assignments = components if components is not None else ()
        components = (component,)
    if isinstance(component, RegionRef):
        component_id = _ref_key(component.component_ref)
        component = None
    if component is not None:
        component_id = component.meta.id
    if document is not None:
        if materials is None:
            materials = document.material_definitions
        if components is None:
            components = document.components
        assignments = document.material_assignments
    catalog = _material_catalog(materials)
    component_map = _components_by_id(components)
    assignment_values = _assignments(assignments)
    if component_id:
        assignment_values = tuple(
            item
            for item in assignment_values
            if item.component_ref is None or _ref_key(item.component_ref) == component_id
        )
    resolved = []
    errors = []
    for index, assignment in enumerate(assignment_values):
        prefix = "assignment[{}]".format(index)
        assignment_errors = []
        component = component_map.get(_ref_key(assignment.component_ref)) if assignment.component_ref else None
        if assignment.component_ref is not None and component is None:
            assignment_errors.append("{} references an unknown component".format(prefix))
        behavior = assignment.structural_behavior
        if component is not None and behavior == "solid" and component.structural_behavior != "solid":
            behavior = component.structural_behavior
        if behavior not in STRUCTURAL_BEHAVIORS:
            assignment_errors.append("{} has unsupported structural_behavior {!r}".format(prefix, behavior))
        material = None
        if assignment.material_ref is not None:
            try:
                material = catalog.resolve(assignment.material_ref)
            except KeyError:
                assignment_errors.append("{} references an unknown material".format(prefix))
        elif behavior in MATERIAL_REQUIRED_BEHAVIORS:
            assignment_errors.append("{} requires a material reference".format(prefix))
        if behavior == "excluded_mass":
            if component is None or component.mass_override is None:
                assignment_errors.append("{} excluded_mass behavior requires a measured mass override".format(prefix))
        elif component is not None and component.mass_override is not None:
            assignment_errors.extend(
                "{}: {}".format(prefix, error)
                for error in mass_override_validation_errors(component.mass_override)
            )
        errors.extend(assignment_errors)
        properties = material.properties if material is not None else None
        properties = _properties_with_overrides(properties, assignment.property_overrides)
        resolved.append(
            ResolvedAssignment(
                assignment=assignment,
                material=material,
                component=component,
                effective_properties=properties,
                structural_behavior=behavior,
                errors=tuple(assignment_errors),
            )
        )
    if strict and errors:
        raise ValidationError("unable to resolve material assignments", tuple(errors))
    return tuple(resolved)


def _provenance_gate_errors(provenance):
    errors = []
    if provenance.source_type not in TRACEABLE_SOURCE_TYPES:
        errors.append("provenance source_type is not traceable")
    if not provenance.source_id:
        errors.append("traceable provenance requires source_id")
    if not provenance.condition:
        errors.append("traceable material data requires conditioning information")
    if provenance.confidence not in ("medium", "high"):
        errors.append("qualification requires medium or high confidence")
    return errors


def qualification_material_gates(materials, assignments=None, components=None):
    """Return a :class:`GateResult` for material qualification readiness."""

    if isinstance(materials, ProjectDocument):
        document = materials
        materials = document.material_definitions
        if assignments is None:
            assignments = document.material_assignments
        if components is None:
            components = document.components
    catalog = _material_catalog(materials)
    checks = []
    definitions = tuple(dict.values(catalog))
    required_fields = (
        (
            "density",
            "young_modulus",
            "poissons_ratio",
            "tensile_allowable",
            "compressive_allowable",
            "shear_allowable",
        )
        if any(
            assignment.structural_behavior in MATERIAL_REQUIRED_BEHAVIORS
            for assignment in _assignments(assignments or ())
        )
        else ()
    )
    for material in definitions:
        errors = list(material_validation_errors(material, required_fields=required_fields))
        if material.approval_state != ApprovalState.APPROVED:
            errors.append("material approval_state must be approved")
        errors.extend(_provenance_gate_errors(material.provenance))
        checks.append(
            GateCheck(
                check_key="material_{}".format(material.meta.id),
                passed=not errors,
                blocker=True,
                explanation="; ".join(errors) if errors else "approved, traceable, conditioned material data",
            )
        )

    if assignments is not None:
        try:
            resolutions = resolve_assignments(assignments, catalog, components, strict=False)
            resolution_errors = []
            for result in resolutions:
                resolution_errors.extend(result.errors)
                if result.structural_behavior in MATERIAL_REQUIRED_BEHAVIORS and result.material is None:
                    resolution_errors.append("required structural assignment has no material")
                if result.material is not None and result.material.approval_state != ApprovalState.APPROVED:
                    resolution_errors.append("assigned material {} is not approved".format(result.material.name))
            checks.append(
                GateCheck(
                    check_key="material_assignment_resolution",
                    passed=not resolution_errors,
                    blocker=True,
                    explanation="; ".join(resolution_errors) if resolution_errors else "all material assignments resolve",
                )
            )
        except (TypeError, ValueError) as exc:
            checks.append(GateCheck("material_assignment_resolution", False, True, str(exc)))

    component_map = _components_by_id(components)
    if component_map:
        try:
            component_resolutions = resolve_assignments(assignments or (), catalog, components, strict=False)
        except (TypeError, ValueError):
            component_resolutions = ()
        assigned_components = {
            _ref_key(item.assignment.component_ref)
            for item in component_resolutions
            if item.assignment.component_ref is not None
            and item.material is not None
            and not item.errors
        }
        behavior_errors = [
            "{} has unsupported structural_behavior {!r}".format(component.meta.id, component.structural_behavior)
            for component in component_map.values()
            if component.structural_behavior not in STRUCTURAL_BEHAVIORS
        ]
        missing = [
            component.meta.id
            for component in component_map.values()
            if component.structural_behavior in MATERIAL_REQUIRED_BEHAVIORS
            and component.meta.id not in assigned_components
        ]
        checks.append(
            GateCheck(
                check_key="material_assignment_coverage",
                passed=not missing and not behavior_errors,
                blocker=True,
                explanation=(
                    "; ".join(behavior_errors + ["missing assignments for: {}".format(", ".join(missing))])
                    if behavior_errors or missing
                    else "all structural components have material assignments"
                ),
            )
        )
        mass_errors = []
        for component in component_map.values():
            if component.structural_behavior == "excluded_mass":
                mass_errors.extend(
                    "{}: {}".format(component.meta.id, error)
                    for error in mass_override_validation_errors(component.mass_override, qualification=True)
                )
        checks.append(
            GateCheck(
                check_key="measured_mass_overrides",
                passed=not mass_errors,
                blocker=True,
                explanation="; ".join(mass_errors) if mass_errors else "measured mass overrides are valid",
            )
        )

    eligible = all(check.passed or not check.blocker for check in checks)
    return GateResult(
        eligible=eligible,
        evidence_disposition=(
            EvidenceDisposition.QUALIFICATION_ACCEPTED
            if eligible
            else EvidenceDisposition.QUALIFICATION_BLOCKED
        ),
        checks=tuple(checks),
        blocking_issue_refs=(),
    )


def material_gate_errors(result):
    """Return concise blocker explanations from a qualification gate result."""

    return tuple(check.explanation for check in result.checks if check.blocker and not check.passed)


__all__ = [
    "STRUCTURAL_BEHAVIORS",
    "MATERIAL_REQUIRED_BEHAVIORS",
    "TRACEABLE_SOURCE_TYPES",
    "MaterialCatalog",
    "ResolvedAssignment",
    "AssignmentResolution",
    "builtin_materials",
    "load_material_catalog",
    "material_validation_errors",
    "validate_material",
    "mass_override_validation_errors",
    "validate_mass_override",
    "resolve_assignments",
    "qualification_material_gates",
    "material_gate_errors",
]
