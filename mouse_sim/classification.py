"""Conservative geometric object classification.

Classification is topology-aware, not semantic inference.  In particular, a
single fused or connected solid is never reported as semantically separated
components merely because it has multiple faces, shells, or imported object
names.
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Tuple

from .geometry import Box, Compound, Cone, Cylinder, Frustum, Geometry, Sphere, TriangleMesh


# Name-based synonym mapping for common mouse components.  A matching name
# assigns the component type with a documented confidence and reason; unknown
# names keep the conservative topology-based classification.  A name never
# claims fused-solid separation.
NAME_SYNONYMS = {
    "wheel": (
        "wheel", "scroll", "scrollwheel", "scroll_wheel", "scrollring", "scroll_ring",
        "scroller", "wheel_assembly", "encoder",
    ),
    "pcb": (
        "pcb", "board", "mainboard", "main_board", "circuit_board", "logic_board",
        "electronics", "pwa",
    ),
    "battery": (
        "battery", "battery_pack", "batterypack", "lipo", "li_po", "lipoly",
        "cell", "power_cell", "accumulator",
    ),
    "shell_top": (
        "shell_top", "topshell", "top_shell", "top_cover", "topcover",
        "upper_shell", "shell_upper", "cover_top", "upper_cover",
    ),
    "shell_bottom": (
        "shell_bottom", "bottomshell", "bottom_shell", "bottom_cover", "bottomcover",
        "lower_shell", "shell_lower", "base", "base_plate", "baseplate",
        "chassis", "cover_bottom", "lower_cover",
    ),
    "skate": (
        "skate", "skates", "skatefoot", "skate_foot", "glide", "glide_pad",
        "glidepad", "mouse_foot", "mousefeet", "foot", "feet", "pad", "pads",
    ),
    "screw": (
        "screw", "screws", "fastener", "fasteners", "bolt", "bolts",
        "screw_insert", "screwinsert", "insert",
    ),
    "button": (
        "button", "buttons", "switch", "switches", "click", "clicker",
        "key", "keys", "microswitch", "micro_switch", "mouse_button",
    ),
}

_SYNONYM_TO_TYPE = {
    synonym: component_type
    for component_type, synonyms in NAME_SYNONYMS.items()
    for synonym in synonyms
}


def _synonym_key(text):
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().casefold()).strip("_")


def _synonym_type(identifier, record):
    """Resolve an object name to a semantic component type.

    Trailing numeric suffixes (``wheel_2``) are tolerated.  Returns
    ``(component_type, matched_synonym)`` or ``(None, None)``.
    """
    candidates = [identifier]
    if isinstance(record, Mapping):
        name = record.get("name", record.get("label"))
        if name is not None:
            candidates.append(name)
    for candidate in candidates:
        key = _synonym_key(candidate)
        while key:
            if key in _SYNONYM_TO_TYPE:
                return _SYNONYM_TO_TYPE[key], key
            base, _, suffix = key.rpartition("_")
            if suffix.isdigit() and base:
                key = base
                continue
            break
    return None, None


@dataclass(frozen=True)
class ObjectClassification:
    object_id: str
    component_type: str
    unresolved: bool
    confidence: float
    reasons: Tuple[str, ...] = ()
    fused: bool = False
    semantic_separation_claimed: bool = False
    source_status: str = "source"
    derived_status: str = "not_derived"
    review_status: str = "unreviewed"
    diagnostics: Tuple[str, ...] = ()

    @property
    def classification(self):
        return self.component_type

    @property
    def confidence_reasons(self):
        return self.reasons

    @property
    def reason(self):
        return "; ".join(self.reasons)

    def to_dict(self):
        return {
            "object_id": self.object_id,
            "component_type": self.component_type,
            "classification": self.component_type,
            "unresolved": self.unresolved,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "fused": self.fused,
            "semantic_separation_claimed": self.semantic_separation_claimed,
            "source_status": self.source_status,
            "derived_status": self.derived_status,
            "review_status": self.review_status,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class ClassificationResult:
    objects: Tuple[ObjectClassification, ...]

    def __iter__(self):
        return iter(self.objects)

    def __len__(self):
        return len(self.objects)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.objects[key]
        for value in self.objects:
            if value.object_id == str(key):
                return value
        raise KeyError(key)

    def by_id(self):
        return {value.object_id: value for value in self.objects}

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return tuple(value.object_id for value in self.objects)

    def values(self):
        return self.objects

    def items(self):
        return tuple((value.object_id, value) for value in self.objects)

    def __contains__(self, key):
        return any(value.object_id == str(key) for value in self.objects)

    def to_dict(self):
        return {value.object_id: value.to_dict() for value in self.objects}


def _unwrap(value):
    geometry = getattr(value, "geometry", None)
    if geometry is not None and not isinstance(value, Geometry):
        return geometry, value
    return value, value


def _entries(objects):
    if isinstance(objects, Mapping):
        if "objects" in objects:
            source = objects["objects"]
            if isinstance(source, Mapping):
                return tuple((str(key), value) for key, value in source.items())
            return tuple(_record_entry(value, index) for index, value in enumerate(source))
        if "geometry" in objects:
            return ((str(objects.get("id", "object-0")), objects),)
        return tuple((str(key), value) for key, value in objects.items())
    if isinstance(objects, Geometry) or getattr(objects, "geometry", None) is not None:
        return (("object-0", objects),)
    if hasattr(objects, "components"):
        return tuple((str(getattr(value.meta, "id", "component-{}".format(index))), value) for index, value in enumerate(objects.components))
    return tuple(_record_entry(value, index) for index, value in enumerate(objects))


def _record_entry(value, index):
    if isinstance(value, Mapping):
        identifier = value.get("id", value.get("object_id", value.get("name", "object-{}".format(index))))
        return str(identifier), value
    return "object-{}".format(index), value


def _metadata(envelope, record):
    source_status = getattr(envelope, "source_status", None)
    derived_status = getattr(envelope, "derived_status", None)
    review_status = getattr(envelope, "review_status", None)
    if isinstance(record, Mapping):
        source_status = record.get("source_status", record.get("status", source_status))
        derived_status = record.get("derived_status", derived_status)
        review_status = record.get("review_status", review_status)
    return str(source_status or "source"), str(derived_status or "not_derived"), str(review_status or "unreviewed")


def _classify_one(identifier, raw):
    record = raw if isinstance(raw, Mapping) else {}
    value, envelope = _unwrap(record.get("geometry", record.get("shape", raw)) if isinstance(raw, Mapping) else raw)
    source_status, derived_status, review_status = _metadata(envelope, record)
    explicit_fused = bool(record.get("fused", False)) if isinstance(record, Mapping) else False
    reasons = []
    diagnostics = []
    fused = explicit_fused
    semantic_type, matched_synonym = _synonym_type(identifier, record)
    if semantic_type is not None:
        component_type = semantic_type
        unresolved = False
        confidence = 0.95
        reasons.append(
            "name {!r} matches component synonym {!r} for type {!r}".format(
                identifier, matched_synonym, semantic_type
            )
        )
        if isinstance(value, TriangleMesh):
            mesh = value.diagnostics()
            diagnostics.extend(mesh.issues)
            if not mesh.safe_for_mass_properties:
                reasons.append("mesh topology diagnostics do not change the name-based component assignment")
    elif isinstance(value, (Box, Sphere, Cylinder, Cone, Frustum)):
        component_type = "solid"
        unresolved = False
        confidence = 0.99
        reasons.append("analytic primitive has an unambiguous closed solid definition")
    elif isinstance(value, TriangleMesh):
        mesh = value.diagnostics()
        diagnostics.extend(mesh.issues)
        if mesh.safe_for_mass_properties:
            component_type = "solid"
            unresolved = False
            confidence = 0.9
            reasons.append("closed, consistently wound triangle topology supports a solid classification")
        elif mesh.closed:
            component_type = "unresolved"
            unresolved = True
            confidence = 0.35
            reasons.append("mesh is topologically closed but has diagnostics that prevent a safe solid conclusion")
        else:
            component_type = "surface"
            unresolved = True
            confidence = 0.25
            reasons.append("open triangle topology describes a surface, not a certified solid")
        reasons.append("face connectivity does not establish semantic component separation")
    elif isinstance(value, Compound):
        component_type = "compound"
        unresolved = True
        confidence = 0.55
        reasons.append("compound children are geometric bodies; semantic component meaning is unresolved")
        reasons.append("no semantic separation is claimed for fused or touching bodies")
        fused = explicit_fused
    elif isinstance(value, Geometry):
        component_type = "geometry"
        unresolved = True
        confidence = 0.2
        reasons.append("geometry type is not recognized by the conservative classifier")
    else:
        component_type = "unresolved"
        unresolved = True
        confidence = 0.0
        reasons.append("no supported geometry representation was supplied")
    # Even high-confidence topology classification is not semantic parsing.
    semantic_separation_claimed = False
    if fused:
        unresolved = True
        confidence = min(confidence, 0.2)
        reasons.append("object is marked fused; semantic separation is intentionally unresolved")
    return ObjectClassification(
        str(identifier),
        component_type,
        unresolved,
        confidence,
        tuple(reasons),
        fused,
        semantic_separation_claimed,
        source_status,
        derived_status,
        review_status,
        tuple(diagnostics),
    )


def classify_objects(objects):
    """Classify objects conservatively and return an id-addressable result."""

    return ClassificationResult(tuple(_classify_one(identifier, value) for identifier, value in _entries(objects)))


__all__ = ["ObjectClassification", "ClassificationResult", "classify_objects"]
