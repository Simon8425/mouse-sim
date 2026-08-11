"""AABB-based clearance analysis between geometries.

All clearances are conservative estimates computed from axis-aligned bounds
(``.bounds()``) rather than exact surface distances.  Stack-up of part
tolerances and deformation allowances is subtracted from the nominal gap; a
negative worst-case clearance is reported as interference.  Pairs involving
open or non-manifold triangle meshes cannot certify clearance, so they are
reported with status ``unknown`` and the CLEARANCE_NOT_CERTIFIED flag.
"""

import math
from dataclasses import dataclass
from typing import Mapping, Tuple

from .geometry import Geometry, TriangleMesh, geometry_from_dict

CLEARANCE_NOT_CERTIFIED = "clearance_not_certified"
TOLERANCE_APPLIED = "tolerance_applied"
PAIR_RULE_APPLIED = "pair_rule_applied"
STACKUP_CONSUMED_GAP = "stackup_consumed_gap"

STATUS_CLEAR = "clear"
STATUS_CONTACT = "contact"
STATUS_INTERFERENCE = "interference"
STATUS_UNKNOWN = "unknown"
STATUS_ESTIMATE = "estimate"

_EPSILON = 1e-9


def clamp(value, low, high):
    """Clamp ``value`` into the inclusive interval ``[low, high]``."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def sign(value):
    """Return -1.0, 0.0, or 1.0 for negative, zero, and positive values."""
    if value > 0.0:
        return 1.0
    if value < 0.0:
        return -1.0
    return 0.0


def _as_geometry(value):
    """Coerce a Geometry, an object wrapper, or a plain dict to Geometry."""
    if isinstance(value, Geometry):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(
            "expected a Geometry or geometry dict, got {!r}".format(type(value).__name__)
        )
    if "geometry" in value and isinstance(value["geometry"], (Geometry, Mapping)):
        return _as_geometry(value["geometry"])
    return geometry_from_dict(value)


def _sorted_pair(first, second):
    return tuple(sorted((first, second)))


def _aabb_signed_distance(first, second):
    """Signed gap between two bounds: positive separation, zero touch, negative penetration."""
    squared = 0.0
    separated = False
    penetration = None
    for axis in range(3):
        if first.max_point[axis] < second.min_point[axis]:
            gap = second.min_point[axis] - first.max_point[axis]
            squared += gap * gap
            separated = True
        elif second.max_point[axis] < first.min_point[axis]:
            gap = first.min_point[axis] - second.max_point[axis]
            squared += gap * gap
            separated = True
        else:
            overlap = min(first.max_point[axis], second.max_point[axis]) - max(
                first.min_point[axis], second.min_point[axis]
            )
            penetration = overlap if penetration is None else min(penetration, overlap)
    if separated:
        return math.sqrt(squared)
    if penetration is None:
        return 0.0
    return -penetration


def _mesh_uncertified(geometry):
    if not isinstance(geometry, TriangleMesh):
        return False, ()
    diagnostics = geometry.diagnostics()
    if diagnostics.closed and diagnostics.nonmanifold_edges == 0 and diagnostics.safe_for_mass_properties:
        return False, ()
    return True, diagnostics.issues


@dataclass(frozen=True)
class ClearanceResult:
    """Conservative AABB clearance between two geometries after stack-up."""

    nominal_clearance_m: float
    worst_case_clearance_m: float
    interference: bool
    touch: bool
    status: str
    method: str = "aabb_estimate"
    flags: Tuple[str, ...] = ()
    diagnostics: Tuple[str, ...] = ()

    def to_dict(self):
        return {
            "nominal_clearance_m": self.nominal_clearance_m,
            "worst_case_clearance_m": self.worst_case_clearance_m,
            "interference": self.interference,
            "touch": self.touch,
            "status": self.status,
            "method": self.method,
            "flags": list(self.flags),
            "diagnostics": list(self.diagnostics),
        }


def _rule_parameter(rule, key, default):
    if rule is None:
        return default
    return float(rule.get(key, default))


def clearance_between(a, b, tolerance_a_m=0.0, tolerance_b_m=0.0, deformation_allowance_m=0.0, pair_rule=None):
    """Return an AABB-estimate :class:`ClearanceResult` between ``a`` and ``b``.

    ``a`` and ``b`` may be :class:`Geometry` objects, object wrappers carrying
    a ``"geometry"`` key, or plain ``geometry_from_dict``-compatible mappings.
    ``pair_rule`` may be a mapping of overrides (``tolerance_a_m``,
    ``tolerance_b_m``, ``deformation_allowance_m``, ``label``) or a callable
    ``rule(a, b)`` returning such a mapping.
    """
    first = _as_geometry(a)
    second = _as_geometry(b)
    rule = pair_rule(first, second) if callable(pair_rule) else pair_rule
    if rule is not None and not isinstance(rule, Mapping):
        raise TypeError("pair_rule must be a mapping or a callable returning a mapping")
    tolerance_a_m = _rule_parameter(rule, "tolerance_a_m", tolerance_a_m)
    tolerance_b_m = _rule_parameter(rule, "tolerance_b_m", tolerance_b_m)
    deformation_allowance_m = _rule_parameter(rule, "deformation_allowance_m", deformation_allowance_m)
    nominal = _aabb_signed_distance(first.bounds(), second.bounds())
    worst = nominal - tolerance_a_m - tolerance_b_m - deformation_allowance_m
    flags = []
    diagnostics = []
    uncertified_a, issues_a = _mesh_uncertified(first)
    uncertified_b, issues_b = _mesh_uncertified(second)
    if uncertified_a or uncertified_b:
        flags.append(CLEARANCE_NOT_CERTIFIED)
        if uncertified_a:
            diagnostics.append(
                "open_or_nonmanifold_mesh_a: " + ", ".join(issues_a) if issues_a else "open_or_nonmanifold_mesh_a"
            )
        if uncertified_b:
            diagnostics.append(
                "open_or_nonmanifold_mesh_b: " + ", ".join(issues_b) if issues_b else "open_or_nonmanifold_mesh_b"
            )
    if tolerance_a_m or tolerance_b_m or deformation_allowance_m:
        flags.append(TOLERANCE_APPLIED)
    if rule is not None:
        flags.append(PAIR_RULE_APPLIED)
        if rule.get("label"):
            diagnostics.append("pair_rule: " + str(rule["label"]))
    if nominal > _EPSILON and worst <= _EPSILON:
        flags.append(STACKUP_CONSUMED_GAP)
    interference = worst <= -_EPSILON
    touch = abs(nominal) <= _EPSILON
    if uncertified_a or uncertified_b:
        status = STATUS_UNKNOWN
    elif interference:
        status = STATUS_INTERFERENCE
    elif touch:
        status = STATUS_CONTACT
    elif worst <= _EPSILON:
        status = STATUS_ESTIMATE
    else:
        status = STATUS_CLEAR
    return ClearanceResult(
        nominal, worst, interference, touch, status,
        flags=tuple(flags), diagnostics=tuple(diagnostics),
    )


def pair_clearance_matrix(objects, pair_rules=None):
    """Return sorted JSON-friendly clearance records for every object pair.

    ``objects`` maps names to Geometry objects or plain geometry dicts.
    ``pair_rules`` may map sorted name pairs (or a callable accepting the pair
    and geometries) to override mappings consumed by :func:`clearance_between`.
    """
    if not isinstance(objects, Mapping):
        raise TypeError("objects must be a mapping of names to geometries")
    names = sorted(str(name) for name in objects)
    geometries = {name: _as_geometry(objects[name]) for name in names}
    records = []
    for index, first_name in enumerate(names):
        for second_name in names[index + 1:]:
            rule = None
            if pair_rules is not None:
                if callable(pair_rules):
                    rule = pair_rules(first_name, second_name, geometries[first_name], geometries[second_name])
                elif isinstance(pair_rules, Mapping):
                    rule = pair_rules.get(
                        _sorted_pair(first_name, second_name),
                        pair_rules.get((first_name, second_name), pair_rules.get((second_name, first_name))),
                    )
            result = clearance_between(geometries[first_name], geometries[second_name], pair_rule=rule)
            record = {"pair": [first_name, second_name], "a": first_name, "b": second_name}
            record.update(result.to_dict())
            if rule is not None and rule.get("label"):
                record["label"] = str(rule["label"])
            records.append(record)
    return {"object_names": names, "count": len(records), "units": "m", "pairs": records}


__all__ = [
    "CLEARANCE_NOT_CERTIFIED",
    "TOLERANCE_APPLIED",
    "PAIR_RULE_APPLIED",
    "STACKUP_CONSUMED_GAP",
    "STATUS_CLEAR",
    "STATUS_CONTACT",
    "STATUS_INTERFERENCE",
    "STATUS_UNKNOWN",
    "STATUS_ESTIMATE",
    "ClearanceResult",
    "clamp",
    "sign",
    "clearance_between",
    "pair_clearance_matrix",
]
