"""Mass properties and aggregation over geometry-bearing objects."""

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional, Tuple

from .geometry import Geometry
from .model import MaterialProperties, Quantity
from .units import normalize_unit, to_si, unit_dimension


Vector3 = Tuple[float, float, float]
Tensor3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def _zero_tensor():
    return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def _add(first, second):
    return tuple(first[index] + second[index] for index in range(3))


def _sub(first, second):
    return tuple(first[index] - second[index] for index in range(3))


def _scale(value, factor):
    return tuple(factor * item for item in value)


def _dot(first, second):
    return sum(first[index] * second[index] for index in range(3))


def _tensor_add(first, second):
    return tuple(tuple(first[i][j] + second[i][j] for j in range(3)) for i in range(3))


def _tensor_scale(tensor, factor):
    return tuple(tuple(factor * tensor[i][j] for j in range(3)) for i in range(3))


def _parallel_axis(mass, offset):
    squared = _dot(offset, offset)
    return tuple(
        tuple(mass * (squared if row == column else 0.0) - mass * offset[row] * offset[column] for column in range(3))
        for row in range(3)
    )


def _finite(value, label):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(label))
    return number


@dataclass(frozen=True)
class ObjectMassProperties:
    """Mass result for one object.

    ``mass_status`` is one of ``calculated``, ``measured``, or ``unknown``.
    Geometry-derived inertia is about ``center_of_mass_m``.  A missing
    geometry or density never produces a guessed value.
    """

    object_id: str
    mass_kg: Optional[float]
    mass_status: str
    volume_m3: Optional[float] = None
    center_of_mass_m: Optional[Vector3] = None
    inertia_tensor_kg_m2: Optional[Tensor3] = None
    uncertainty_kg: Optional[float] = None
    completeness: float = 0.0
    diagnostics: Tuple[str, ...] = ()
    source_status: str = "source"
    derived_status: str = "derived"
    review_status: str = "unreviewed"

    @property
    def centroid_m(self):
        return self.center_of_mass_m

    @property
    def mass_source(self):
        return self.mass_status

    @property
    def is_complete(self):
        return self.completeness >= 1.0

    def to_dict(self):
        return {
            "object_id": self.object_id,
            "mass_kg": self.mass_kg,
            "mass_status": self.mass_status,
            "volume_m3": self.volume_m3,
            "center_of_mass_m": list(self.center_of_mass_m) if self.center_of_mass_m is not None else None,
            "inertia_tensor_kg_m2": [list(row) for row in self.inertia_tensor_kg_m2] if self.inertia_tensor_kg_m2 is not None else None,
            "uncertainty_kg": self.uncertainty_kg,
            "completeness": self.completeness,
            "diagnostics": list(self.diagnostics),
            "source_status": self.source_status,
            "derived_status": self.derived_status,
            "review_status": self.review_status,
        }


@dataclass(frozen=True)
class MassPropertiesResult:
    """Aggregate mass result, including partial-data state and provenance."""

    mass_kg: Optional[float]
    mass_status: str
    center_of_mass_m: Optional[Vector3]
    inertia_tensor_kg_m2: Optional[Tensor3]
    uncertainty_kg: Optional[float]
    completeness: float
    objects: Tuple[ObjectMassProperties, ...] = ()
    diagnostics: Tuple[str, ...] = ()
    source_status: str = "source"
    derived_status: str = "derived"
    review_status: str = "unreviewed"

    @property
    def per_object(self):
        return self.objects

    @property
    def centroid_m(self):
        return self.center_of_mass_m

    @property
    def mass_source(self):
        return self.mass_status

    @property
    def is_complete(self):
        return self.completeness >= 1.0

    def to_dict(self):
        return {
            "mass_kg": self.mass_kg,
            "mass_status": self.mass_status,
            "center_of_mass_m": list(self.center_of_mass_m) if self.center_of_mass_m is not None else None,
            "inertia_tensor_kg_m2": [list(row) for row in self.inertia_tensor_kg_m2] if self.inertia_tensor_kg_m2 is not None else None,
            "uncertainty_kg": self.uncertainty_kg,
            "completeness": self.completeness,
            "objects": [item.to_dict() for item in self.objects],
            "diagnostics": list(self.diagnostics),
            "source_status": self.source_status,
            "derived_status": self.derived_status,
            "review_status": self.review_status,
        }


def _unwrap_geometry(value):
    if value is None:
        return None
    geometry = getattr(value, "geometry", None)
    if geometry is not None and not isinstance(value, Geometry):
        return geometry
    return value


def _object_entries(document):
    """Yield ``(id, geometry, record)`` from common document shapes."""

    if isinstance(document, Geometry) or getattr(document, "geometry", None) is not None:
        return (("object-0", _unwrap_geometry(document), document),)
    if isinstance(document, Mapping):
        if "objects" in document:
            source = document["objects"]
            if isinstance(source, Mapping):
                return tuple((str(key), _unwrap_geometry(value), {"id": key, "geometry": value}) for key, value in source.items())
            return tuple(_entry_from_record(item, index) for index, item in enumerate(source))
        if "geometry" in document:
            return ((str(document.get("id", "object-0")), _unwrap_geometry(document["geometry"]), document),)
        # A direct id -> geometry mapping is a useful small API for scripts.
        return tuple((str(key), _unwrap_geometry(value), {"id": key, "geometry": value}) for key, value in document.items())
    if isinstance(document, (list, tuple)):
        return tuple(_entry_from_record(item, index) for index, item in enumerate(document))
    components = getattr(document, "components", None)
    if components is not None:
        return tuple(_entry_from_record(item, index) for index, item in enumerate(components))
    raise TypeError("document must contain geometry-bearing objects")


def _entry_from_record(record, index):
    if isinstance(record, Mapping):
        identifier = record.get("id", record.get("object_id", record.get("name", "object-{}".format(index))))
        geometry = record.get("geometry", record.get("shape", record.get("solid")))
        return str(identifier), _unwrap_geometry(geometry), record
    if isinstance(record, tuple) and len(record) == 2:
        return str(record[0]), _unwrap_geometry(record[1]), {"id": record[0], "geometry": record[1]}
    return "object-{}".format(index), _unwrap_geometry(record), {"id": "object-{}".format(index), "geometry": record}


def _mapping_lookup(mapping, key):
    if not isinstance(mapping, Mapping):
        return None
    return mapping.get(key, mapping.get(str(key)))


def _mass_quantity(value, label="mass"):
    if isinstance(value, Quantity):
        if unit_dimension(value.unit) != "mass":
            raise ValueError("{} must use a mass unit".format(label))
        return _finite(value.value_si, label)
    if isinstance(value, Mapping):
        if "mass" in value and not ("value" in value or "value_si" in value):
            return _mass_quantity(value["mass"], label)
        if "value_si" in value:
            return _finite(value["value_si"], label)
        if "value" in value:
            unit = value.get("unit")
            if unit is None:
                raise ValueError("{} requires an explicit unit".format(label))
            if unit_dimension(unit) != "mass":
                raise ValueError("{} unit must be a mass unit".format(label))
            return _finite(to_si(value["value"], unit, expected_dimension="mass"), label)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Numeric material/mass maps are documented as SI values.  Persisted
        # quantities should use Quantity or {value, unit} when crossing APIs.
        return _finite(value, label)
    raise ValueError("{} is not a mass quantity".format(label))


def _density_quantity(value):
    if isinstance(value, MaterialProperties):
        value = value.density
    elif hasattr(value, "density") and not isinstance(value, (int, float, Mapping, Quantity)):
        value = value.density
    if isinstance(value, Quantity):
        if unit_dimension(value.unit) != "density":
            raise ValueError("density quantity must use a density unit")
        density = value.value_si
    elif isinstance(value, Mapping):
        if "density" in value and not ("value" in value or "value_si" in value):
            return _density_quantity(value["density"])
        if "value_si" in value:
            density = _finite(value["value_si"], "density")
        elif "value" in value and value.get("unit") is not None:
            if unit_dimension(value["unit"]) != "density":
                raise ValueError("density unit must be a density unit")
            density = to_si(value["value"], value["unit"], expected_dimension="density")
        else:
            raise ValueError("density requires an explicit unit")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        density = _finite(value, "density")
    else:
        raise ValueError("material has no usable density")
    if density <= 0.0:
        raise ValueError("density must be positive")
    return density


def _uncertainty(value):
    if value is None:
        return None
    try:
        result = _mass_quantity(value, "mass uncertainty")
    except ValueError:
        return _finite(value, "mass uncertainty")
    if result < 0.0:
        raise ValueError("mass uncertainty must be non-negative")
    return result


def _override_value(value):
    if value is None:
        return None, None
    uncertainty = None
    if hasattr(value, "measured_mass"):
        uncertainty = getattr(value, "uncertainty", None)
        value = getattr(value, "measured_mass")
    elif isinstance(value, Mapping):
        uncertainty = value.get("uncertainty")
        value = value.get("mass", value.get("measured_mass", value))
    mass = _mass_quantity(value, "measured mass")
    if mass < 0.0:
        raise ValueError("measured mass must be non-negative")
    return mass, _uncertainty(uncertainty)


def _geometry_properties(geometry):
    if geometry is None:
        return None, ("geometry_missing",)
    try:
        value = geometry.geometric_properties()
    except (AttributeError, ValueError) as exc:
        return None, ("geometry_mass_properties_unavailable: {}".format(exc),)
    diagnostics = tuple(getattr(value, "diagnostics", ()))
    if not value.closed or value.centroid_m is None or value.inertia_tensor_unit_density is None:
        return value, diagnostics + ("geometry_not_safe_for_mass_properties",)
    return value, diagnostics


def _provenance(record):
    envelope = record.get("geometry") if isinstance(record, Mapping) else record
    source_status = str(getattr(envelope, "source_status", "source"))
    derived_status = str(getattr(envelope, "derived_status", "derived"))
    review_status = str(getattr(envelope, "review_status", "unreviewed"))
    diagnostic_values = getattr(envelope, "diagnostics", ())
    if callable(diagnostic_values):
        diagnostic_values = ()
    imported_diagnostics = tuple(
        "import_diagnostic:{}".format(getattr(item, "code", str(item)))
        for item in diagnostic_values
    )
    repair_values = getattr(envelope, "repair_diagnostics", ())
    if repair_values is None or callable(repair_values):
        repair_values = ()
    repairs = tuple("repair_diagnostic:{}".format(getattr(item, "code", str(item))) for item in repair_values)
    return source_status, derived_status, review_status, imported_diagnostics + repairs


def mass_properties(document, material_by_object, mass_overrides=None):
    """Calculate and aggregate object mass properties.

    Material densities are interpreted as kg/m3 when numeric, or require an
    explicit density unit in ``Quantity``/mapping form.  Overrides are
    measured masses and may be ``Quantity``, ``MassOverride``, numeric SI kg,
    or ``{"value": ..., "unit": ...}``.  Unknown objects remain explicit in
    the result and reduce ``completeness``.
    """

    entries = _object_entries(document)
    overrides = mass_overrides or {}
    results = []
    aggregate_diagnostics = []
    for identifier, geometry, record in entries:
        diagnostics = []
        source_status, derived_status, review_status, import_diagnostics = _provenance(record)
        diagnostics.extend(import_diagnostics)
        geometry_value, geometry_diagnostics = _geometry_properties(geometry)
        diagnostics.extend(geometry_diagnostics)
        volume = getattr(geometry_value, "volume_m3", None)
        center = getattr(geometry_value, "centroid_m", None)
        tensor_unit = getattr(geometry_value, "inertia_tensor_unit_density", None)
        try:
            override = _mapping_lookup(overrides, identifier)
            if override is None and isinstance(record, Mapping):
                override = record.get("mass_override", record.get("measured_mass"))
            measured_mass, uncertainty = _override_value(override) if override is not None else (None, None)
        except ValueError as exc:
            diagnostics.append("invalid_mass_override: {}".format(exc))
            measured_mass, uncertainty = None, None
        if measured_mass is not None:
            mass = measured_mass
            status = "measured"
            tensor = _tensor_scale(tensor_unit, mass / volume) if tensor_unit is not None and volume and volume > 0 else None
            if center is None:
                diagnostics.append("measured_mass_without_center_of_mass")
        else:
            material = _mapping_lookup(material_by_object, identifier)
            if material is None and isinstance(record, Mapping):
                material = record.get("material", record.get("density"))
            try:
                density = _density_quantity(material)
            except (TypeError, ValueError) as exc:
                density = None
                diagnostics.append("density_unknown: {}".format(exc))
            if density is not None and geometry_value is not None and geometry_value.closed and tensor_unit is not None and center is not None:
                mass = density * volume
                status = "calculated"
                tensor = _tensor_scale(tensor_unit, density)
            else:
                mass = None
                status = "unknown"
                tensor = None
                if density is not None and geometry_value is not None and not geometry_value.closed:
                    diagnostics.append("closed_geometry_required_for_calculated_mass")
                uncertainty = None
        completeness = 1.0 if mass is not None and center is not None and tensor is not None else 0.5 if mass is not None else 0.0
        object_result = ObjectMassProperties(
            identifier,
            mass,
            status,
            volume,
            center,
            tensor,
            uncertainty,
            completeness,
            tuple(diagnostics),
            source_status,
            derived_status,
            review_status,
        )
        results.append(object_result)
        aggregate_diagnostics.extend("{}: {}".format(identifier, item) for item in diagnostics)
    known = tuple(item for item in results if item.mass_kg is not None)
    spatially_known = tuple(item for item in known if item.center_of_mass_m is not None and item.inertia_tensor_kg_m2 is not None)
    total_mass = sum(item.mass_kg for item in known) if known else None
    complete_count = sum(1 for item in results if item.completeness >= 1.0)
    completeness = float(complete_count) / len(results) if results else 0.0
    if not known:
        status = "unknown"
    elif len(known) != len(results):
        status = "partial"
    elif all(item.mass_status == "measured" for item in known):
        status = "measured"
    elif all(item.mass_status == "calculated" for item in known):
        status = "calculated"
    else:
        status = "mixed"
    center_of_mass = None
    inertia = None
    if total_mass is not None and spatially_known and len(spatially_known) == len(known):
        center_of_mass = _scale(
            tuple(sum(item.mass_kg * item.center_of_mass_m[index] for item in known) for index in range(3)),
            1.0 / total_mass,
        )
        inertia = _zero_tensor()
        for item in known:
            inertia = _tensor_add(inertia, item.inertia_tensor_kg_m2)
            inertia = _tensor_add(inertia, _parallel_axis(item.mass_kg, _sub(item.center_of_mass_m, center_of_mass)))
    elif known:
        aggregate_diagnostics.append("center_of_mass_or_inertia_incomplete")
    uncertainties = [item.uncertainty_kg for item in known if item.uncertainty_kg is not None]
    uncertainty = math.sqrt(sum(value * value for value in uncertainties)) if uncertainties else 0.0 if known else None
    if len(known) != len(results):
        aggregate_diagnostics.append("one_or_more_object_masses_unknown")
    source_statuses = set(item.source_status for item in results)
    derived_statuses = set(item.derived_status for item in results)
    review_statuses = set(item.review_status for item in results)
    return MassPropertiesResult(
        total_mass,
        status,
        center_of_mass,
        inertia,
        uncertainty,
        completeness,
        tuple(results),
        tuple(aggregate_diagnostics),
        next(iter(source_statuses)) if len(source_statuses) == 1 else "mixed",
        next(iter(derived_statuses)) if len(derived_statuses) == 1 else "mixed",
        next(iter(review_statuses)) if len(review_statuses) == 1 else "mixed",
    )


__all__ = ["ObjectMassProperties", "MassPropertiesResult", "mass_properties"]
