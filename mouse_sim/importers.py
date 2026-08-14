"""Standard-library geometry importers and provenance-bearing load results."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
from typing import Any, Mapping, Optional, Tuple

from .errors import UnitError
from .geometry import TriangleMesh, edge_topology, geometry_from_dict
from .units import normalize_unit, unit_dimension

# Content-addressed cache of parsed + repaired mesh geometry.  The pipeline
# re-parses and re-certifies the same uploaded geometry on every analyze
# request (a 46-part, 257k-triangle STEP assembly takes ~34 s of
# self-intersection certification per run); the geometry dict is immutable
# and ``geometry_from_dict`` + ``repair_open_mesh`` are pure, so caching the
# repaired mesh (whose diagnostics caches are warm) is deterministic and
# safe.  Bounded so server memory stays flat; eviction only costs a re-parse.
from collections import OrderedDict
from threading import RLock

# Sized for several full multi-part assemblies at once (a 46-part model
# needs 46 entries); each entry holds one part's parsed mesh.
_GEOMETRY_CACHE_MAX_ENTRIES = 512
_geometry_cache = OrderedDict()
_geometry_cache_lock = RLock()


def _geometry_cache_get(key):
    with _geometry_cache_lock:
        if key not in _geometry_cache:
            return None
        _geometry_cache.move_to_end(key)
        return _geometry_cache[key]


def _geometry_cache_put(key, value):
    with _geometry_cache_lock:
        _geometry_cache[key] = value
        _geometry_cache.move_to_end(key)
        while len(_geometry_cache) > _GEOMETRY_CACHE_MAX_ENTRIES:
            _geometry_cache.popitem(last=False)


def parse_and_repair_geometry(geometry_data, units=None):
    """Parse a geometry dict and weld-repair it, caching the result.

    Returns ``(geometry, repair_diagnostics)`` like
    :func:`repair_open_mesh` (``repair_diagnostics`` is empty when no repair
    was applied).  The result is cached by the geometry dict's canonical
    content hash, so repeat analyzes of the same uploaded model skip the
    expensive parse + weld + self-intersection certification entirely.  The
    cache is bounded and process-local; eviction only re-runs the (pure,
    deterministic) computation.
    """
    from .canonical import sha256_content

    key = sha256_content(geometry_data)
    cached = _geometry_cache_get(key)
    if cached is not None:
        geometry, repair_diagnostics = cached
        return geometry, tuple(repair_diagnostics)
    geometry = geometry_from_dict(geometry_data, units=units)
    repair_diagnostics = ()
    if isinstance(geometry, TriangleMesh):
        geometry, repair_diagnostics = repair_open_mesh(geometry)
    _geometry_cache_put(key, (geometry, tuple(repair_diagnostics)))
    return geometry, tuple(repair_diagnostics)


@dataclass(frozen=True)
class ImportDiagnostic:
    """A stable, serializable import or repair diagnostic."""

    code: str
    severity: str
    message: str
    details: Tuple[Tuple[str, str], ...] = ()

    @property
    def structured(self):
        return self.to_dict()

    def to_dict(self):
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": {key: value for key, value in self.details},
        }


@dataclass(frozen=True)
class GeometryLoadResult:
    """Imported geometry plus source, derivation, review, and diagnostics."""

    geometry: Any
    format: str
    source_units: Optional[str]
    source_status: str = "source"
    derived_status: str = "not_derived"
    derived_from: Optional[str] = None
    review_status: str = "unreviewed"
    diagnostics: Tuple[ImportDiagnostic, ...] = ()
    repair_diagnostics: Tuple[ImportDiagnostic, ...] = ()
    unsupported: bool = False
    source_name: Optional[str] = None
    metadata: Tuple[Tuple[str, str], ...] = ()
    display_asset: Optional[Mapping[str, Any]] = None

    @property
    def asset_status(self):
        return "derived" if self.derived_status == "derived" else self.source_status

    @property
    def is_supported(self):
        return not self.unsupported and self.geometry is not None

    @property
    def errors(self):
        return tuple(item for item in self.diagnostics if item.severity in ("error", "blocker"))

    @property
    def diagnostic(self):
        return self.diagnostics[0] if self.diagnostics else None

    def __getattr__(self, name):
        # A load result remains convenient in exploratory use without losing
        # the provenance envelope required by review and qualification flows.
        geometry = object.__getattribute__(self, "geometry")
        if geometry is not None:
            return getattr(geometry, name)
        raise AttributeError(name)

    def to_dict(self):
        return {
            "geometry": self.geometry.to_dict() if self.geometry is not None else None,
            "format": self.format,
            "source_units": self.source_units,
            "source_status": self.source_status,
            "derived_status": self.derived_status,
            "derived_from": self.derived_from,
            "review_status": self.review_status,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "repair_diagnostics": [item.to_dict() for item in self.repair_diagnostics],
            "unsupported": self.unsupported,
            "source_name": self.source_name,
            "metadata": {key: value for key, value in self.metadata},
            "display_asset": dict(self.display_asset) if self.display_asset is not None else None,
        }


def _diagnostic(code, severity, message, **details):
    return ImportDiagnostic(code, severity, message, tuple((str(key), str(value)) for key, value in sorted(details.items())))


def _format_name(fmt):
    return str(fmt or "auto").strip().lower().lstrip(".")


def _source_bytes(path_or_bytes):
    source_name = None
    if isinstance(path_or_bytes, (bytes, bytearray, memoryview)):
        return bytes(path_or_bytes), source_name
    if hasattr(path_or_bytes, "read"):
        return path_or_bytes.read(), getattr(path_or_bytes, "name", None)
    path = Path(path_or_bytes)
    source_name = str(path)
    return path.read_bytes(), source_name


def _detect_format(data, source_name):
    if source_name:
        suffix = Path(source_name).suffix.lower().lstrip(".")
        if suffix in ("json", "obj", "stl", "step", "stp"):
            return suffix
    stripped = data.lstrip()
    if stripped.upper().startswith(b"ISO-10303-21"):
        return "step"
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return "json"
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "stl"
    lowered = text.lower()
    if "facet" in lowered and "vertex" in lowered:
        return "stl"
    if any(line.lstrip().startswith(("v ", "f ", "o ", "g ")) for line in text.splitlines()):
        return "obj"
    return "stl"


def _require_import_units(units, format_name):
    if units is None:
        raise UnitError("{} import requires explicit units".format(format_name.upper()))
    canonical = normalize_unit(units)
    if unit_dimension(canonical) != "length":
        raise UnitError("{} units must be a length unit".format(format_name.upper()))
    return canonical


def _metadata(payload):
    values = payload.get("metadata", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(values, Mapping):
        values = {}
    combined = dict(values)
    for key in ("asset_kind", "source_status", "status", "derived_status", "derived_from", "review_status", "source_id", "repair_diagnostics"):
        if isinstance(payload, Mapping) and key in payload:
            combined[key] = payload[key]
    source_status = str(combined.get("source_status", combined.get("status", "source")))
    derived_from = combined.get("derived_from")
    derived_status = str(combined.get("derived_status", "derived" if derived_from else "not_derived"))
    if combined.get("asset_kind") == "derived" or combined.get("status") == "derived":
        derived_status = "derived"
    review_status = str(combined.get("review_status", "unreviewed"))
    repair_values = combined.get("repair_diagnostics", ())
    repairs = []
    for item in repair_values if isinstance(repair_values, (list, tuple)) else ():
        if isinstance(item, Mapping):
            repairs.append(
                ImportDiagnostic(
                    str(item.get("code", "repair")),
                    str(item.get("severity", "warning")),
                    str(item.get("message", "geometry repair diagnostic")),
                    tuple((str(k), str(v)) for k, v in sorted(item.get("details", {}).items())) if isinstance(item.get("details", {}), Mapping) else (),
                )
            )
        else:
            repairs.append(_diagnostic("repair", "warning", str(item)))
    serializable = tuple((str(key), str(value)) for key, value in sorted(combined.items()) if key != "repair_diagnostics")
    return source_status, derived_status, None if derived_from is None else str(derived_from), review_status, tuple(repairs), serializable


def _result(
    geometry,
    format_name,
    source_units,
    source_name,
    payload=None,
    diagnostics=(),
    unsupported=False,
    display_asset=None,
):
    source_status, derived_status, derived_from, review_status, repairs, metadata = _metadata(payload or {})
    return GeometryLoadResult(
        geometry,
        format_name,
        source_units,
        source_status,
        derived_status,
        derived_from,
        review_status,
        tuple(diagnostics),
        repairs,
        unsupported,
        source_name,
        metadata,
        display_asset,
    )


def _parse_obj(data, units):
    text = data.decode("utf-8-sig")
    vertices = []
    triangles = []
    diagnostics = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        command = fields[0].lower()
        if command == "v":
            if len(fields) < 4:
                diagnostics.append(_diagnostic("invalid_vertex", "error", "OBJ vertex requires three coordinates", line=line_number))
                continue
            try:
                vertices.append(tuple(float(item) for item in fields[1:4]))
            except ValueError:
                diagnostics.append(_diagnostic("invalid_vertex", "error", "OBJ vertex contains a non-numeric coordinate", line=line_number))
        elif command == "f":
            if len(fields) < 4:
                diagnostics.append(_diagnostic("invalid_face", "error", "OBJ face requires at least three vertices", line=line_number))
                continue
            indices = []
            for token in fields[1:]:
                try:
                    raw_index = int(token.split("/", 1)[0])
                    index = raw_index - 1 if raw_index > 0 else len(vertices) + raw_index
                    if index < 0 or index >= len(vertices):
                        raise IndexError
                    indices.append(index)
                except (ValueError, IndexError):
                    diagnostics.append(_diagnostic("invalid_face", "error", "OBJ face references an invalid vertex", line=line_number))
                    indices = []
                    break
            for index in range(1, len(indices) - 1):
                triangles.append((indices[0], indices[index], indices[index + 1]))
    if not vertices or not triangles:
        raise ValueError("OBJ contains no usable vertices and faces")
    return TriangleMesh(vertices, triangles, units=units), diagnostics


def _parse_ascii_stl(data, units):
    text = data.decode("utf-8-sig")
    vertices = []
    triangles = []
    index_by_point = {}
    current = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        fields = raw_line.strip().split()
        if not fields or fields[0].lower() != "vertex":
            continue
        if len(fields) < 4:
            raise ValueError("ASCII STL vertex on line {} is incomplete".format(line_number))
        current.append(tuple(float(item) for item in fields[1:4]))
        if len(current) == 3:
            indices = []
            # STL commonly repeats vertices. Deduplicate exact source values
            # with a point -> index dict, preserving deterministic
            # first-seen ordering (list.index would be quadratic).
            for point in current:
                index = index_by_point.get(point)
                if index is None:
                    index = len(vertices)
                    vertices.append(point)
                    index_by_point[point] = index
                indices.append(index)
            triangles.append(tuple(indices))
            current = []
    if current:
        raise ValueError("ASCII STL has an incomplete facet")
    if not triangles:
        raise ValueError("ASCII STL contains no facets")
    return TriangleMesh(vertices, triangles, units=units)


def _looks_binary_stl(data):
    if len(data) < 84:
        return False
    try:
        count = struct.unpack_from("<I", data, 80)[0]
    except struct.error:
        return False
    return count > 0 and 84 + 50 * count == len(data)


def _parse_binary_stl(data, units):
    if len(data) < 84:
        raise ValueError("binary STL is shorter than its header")
    count = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + 50 * count
    if expected != len(data):
        raise ValueError("binary STL length does not match its triangle count")
    vertices = []
    triangles = []
    index_by_point = {}
    for index in range(count):
        offset = 84 + index * 50
        values = struct.unpack_from("<12f", data, offset)
        triangle = []
        for point in (values[3:6], values[6:9], values[9:12]):
            point = tuple(float(item) for item in point)
            vertex_index = index_by_point.get(point)
            if vertex_index is None:
                vertex_index = len(vertices)
                vertices.append(point)
                index_by_point[point] = vertex_index
            triangle.append(vertex_index)
        triangles.append(tuple(triangle))
    return TriangleMesh(vertices, triangles, units=units)


_ADVANCED_STEP_ENTITIES = (
    "ADVANCED_BREP_SHAPE_REPRESENTATION",
    "ADVANCED_FACE",
    "B_SPLINE_SURFACE",
    "B_SPLINE_CURVE",
    "CYLINDRICAL_SURFACE",
    "CONICAL_SURFACE",
    "SPHERICAL_SURFACE",
    "TOROIDAL_SURFACE",
    "EDGE_CURVE",
    "VERTEX_LOOP",
    "TRIMMED_CURVE",
    "CIRCLE",
    "ELLIPSE",
    "LINE",
)

_STEP_SI_PREFIXES = {"$": "m", ".MILLI.": "mm", ".CENTI.": "cm", ".KILO.": "km", ".MICRO.": "um"}

_STEP_UNIT_NAMES = {
    "METRE": "m",
    "MILLIMETRE": "mm",
    "CENTIMETRE": "cm",
    "MICROMETRE": "um",
    "KILOMETRE": "km",
    "INCH": "in",
    "FOOT": "ft",
}

_STEP_UNITS_WARNING = "STEP file declares no length unit; assuming millimetres"

# Face entity names accepted by the faceted parser.  ADVANCED_FACE is common
# in faceted exports even though the face carries no edges: what distinguishes
# advanced NURBS B-rep is the loop type (EDGE_LOOP with curved edges), which
# is checked separately when collecting bounds.
_STEP_FACE_ENTITIES = ("FACE", "FACE_SURFACE", "ADVANCED_FACE")

_STEP_ROOT_ENTITIES = (
    "MANIFOLD_SOLID_BREP",
    "BREP_WITH_VOIDS",
    "SHELL_BASED_SURFACE_MODEL",
    "TRIANGULATED_FACE_SET",
)

_STEP_LOOP_ENTITIES = ("POLY_LOOP", "EDGE_LOOP")


def _data_section(text):
    """Return the DATA section text of an ISO 10303-21 file, or None."""
    lines = []
    in_data = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if in_data:
            if stripped.startswith("ENDSEC") or stripped.startswith("END-ISO"):
                break
            lines.append(raw)
        elif stripped == "DATA" or stripped == "DATA;":
            in_data = True
    return "\n".join(lines) if in_data else None


def _matching_close(text, open_index):
    """Return the index of the paren matching ``text[open_index]``."""
    depth = 0
    in_quote = False
    index = open_index
    while index < len(text):
        char = text[index]
        if in_quote:
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                in_quote = False
        else:
            if char == "'":
                in_quote = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    raise ValueError("STEP entity parameters are unbalanced")


def _scan_step_entities(text):
    """Scan ``#<id>=<body>;`` entities from DATA text in file order."""
    entities = []
    in_quote = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_quote:
            if char == "'":
                if index + 1 < length and text[index + 1] == "'":
                    index += 2
                    continue
                in_quote = False
            index += 1
            continue
        if char == "'":
            in_quote = True
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            index = length if end == -1 else end + 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                raise ValueError("STEP file contains an unterminated comment")
            index = end + 2
            continue
        if char != "#":
            index += 1
            continue
        cursor = index + 1
        id_start = cursor
        while cursor < length and text[cursor].isdigit():
            cursor += 1
        if cursor == id_start or cursor >= length or text[cursor] != "=":
            index = cursor
            continue
        entity_id = int(text[id_start:cursor])
        cursor += 1
        body_start = cursor
        depth = 0
        in_quote = False
        while cursor < length:
            char = text[cursor]
            if in_quote:
                if char == "'":
                    if cursor + 1 < length and text[cursor + 1] == "'":
                        cursor += 2
                        continue
                    in_quote = False
            else:
                if char == "'":
                    in_quote = True
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                elif char == ";" and depth == 0:
                    break
            cursor += 1
        if in_quote:
            raise ValueError("STEP entity #{} contains an unterminated string".format(entity_id))
        if cursor >= length or depth != 0:
            raise ValueError("STEP entity #{} is missing a terminating semicolon".format(entity_id))
        entities.append((entity_id, text[body_start:cursor]))
        index = cursor + 1
    return entities


def _parse_entity_body(body):
    """Split an entity body into ``(name, params)`` clauses."""
    text = body.strip()
    if text.startswith("("):
        close = _matching_close(text, 0)
        if close == len(text) - 1:
            text = text[1:close]
    clauses = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
            continue
        start = index
        while index < length and (text[index].isalnum() or text[index] == "_"):
            index += 1
        name = text[start:index].upper()
        if not name:
            raise ValueError("STEP entity body is malformed")
        while index < length and text[index] in " \t\r\n":
            index += 1
        if index >= length or text[index] != "(":
            raise ValueError("STEP keyword {} is missing parameters".format(name))
        close = _matching_close(text, index)
        clauses.append((name, text[index:close + 1]))
        index = close + 1
    if not clauses:
        raise ValueError("STEP entity body is empty")
    return clauses


def _split_parameters(params):
    """Split a parenthesized parameter list at the top level."""
    text = params.strip()
    if not text.startswith("(") or not text.endswith(")"):
        raise ValueError("STEP parameters are not parenthesized")
    inner = text[1:-1]
    values = []
    current = []
    depth = 0
    in_quote = False
    index = 0
    while index < len(inner):
        char = inner[index]
        if in_quote:
            current.append(char)
            if char == "'":
                if index + 1 < len(inner) and inner[index + 1] == "'":
                    current.append("'")
                    index += 1
                else:
                    in_quote = False
            index += 1
            continue
        if char == "'":
            in_quote = True
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    if in_quote:
        raise ValueError("STEP parameters contain an unterminated string")
    if depth != 0:
        raise ValueError("STEP parameters are unbalanced")
    tail = "".join(current).strip()
    if tail:
        values.append(tail)
    return values


def _entity_refs(params):
    """Return entity ids referenced by a bare ref or parenthesized list."""
    text = params.strip()
    if text.startswith("(") and text.endswith(")"):
        tokens = _split_parameters(text)
    else:
        tokens = [text]
    refs = []
    for token in tokens:
        token = token.strip()
        if token.startswith("#") and token[1:].isdigit():
            refs.append(int(token[1:]))
        elif token in ("$", "*"):
            continue
        else:
            raise ValueError("STEP reference list contains an unexpected token {!r}".format(token))
    return refs


def _entity_ref_param(params, index=1):
    """Return the indexed top-level parameter of an entity clause."""
    parts = _split_parameters(params)
    if len(parts) <= index:
        raise ValueError("STEP entity has too few parameters")
    return parts[index]


def _entity_numbers(params):
    """Return float values from a parenthesized coordinate list."""
    values = []
    for token in _split_parameters(params):
        token = token.strip()
        try:
            values.append(float(token))
        except ValueError:
            raise ValueError("STEP coordinate is not numeric: {!r}".format(token))
    return values


def _step_units(entities, parsed):
    """Detect the STEP length unit, or None when none is declared."""
    for entity_id, _ in entities:
        for name, _ in parsed[entity_id]:
            if name != "LENGTH_UNIT":
                continue
            for other_name, other_params in parsed[entity_id]:
                if other_name == "SI_UNIT":
                    parts = _split_parameters(other_params)
                    prefix = parts[0].strip() if parts else "$"
                    return _STEP_SI_PREFIXES.get(prefix)
                if other_name == "CONVERSION_BASED_UNIT":
                    parts = _split_parameters(other_params)
                    if parts:
                        unit_name = parts[0].strip().strip("'").upper()
                        return _STEP_UNIT_NAMES.get(unit_name)
    return None


def _entity_names(parsed, entity_id):
    return tuple(name for name, _ in parsed[entity_id])


def _v_add(first, second):
    return tuple(first[index] + second[index] for index in range(3))


def _v_sub(first, second):
    return tuple(first[index] - second[index] for index in range(3))


def _v_scale(value, factor):
    return tuple(factor * item for item in value)


def _v_dot(first, second):
    return sum(first[index] * second[index] for index in range(3))


def _v_cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _v_normalize(vector):
    norm = math.sqrt(_v_dot(vector, vector))
    if norm <= 0.0:
        return None
    return _v_scale(vector, 1.0 / norm)


def _point_coordinates(parsed, point_ref, context):
    """Return CARTESIAN_POINT coordinates, unwrapping VERTEX_POINT wrappers."""
    if point_ref not in parsed:
        raise ValueError("STEP reference #{} is missing".format(point_ref))
    names = _entity_names(parsed, point_ref)
    if "VERTEX_POINT" in names:
        for name, params in parsed[point_ref]:
            if name == "VERTEX_POINT":
                refs = _entity_refs(_entity_ref_param(params))
                if not refs:
                    raise ValueError("STEP VERTEX_POINT #{} has no point".format(point_ref))
                return _point_coordinates(parsed, refs[0], context)
    if "CARTESIAN_POINT" in names:
        for name, params in parsed[point_ref]:
            if name == "CARTESIAN_POINT":
                parts = _split_parameters(params)
                if len(parts) < 2:
                    raise ValueError("STEP CARTESIAN_POINT #{} has no coordinates".format(point_ref))
                values = _entity_numbers(parts[1])
                if len(values) != 3:
                    raise ValueError(
                        "STEP CARTESIAN_POINT #{} must have three coordinates".format(point_ref)
                    )
                return tuple(values)
    raise ValueError("STEP entity #{} is not a point".format(point_ref))


def _poly_loop_points(parsed, loop_id):
    """Return the ordered coordinate list of a POLY_LOOP, or ``[]`` when degenerate."""
    points = []
    for name, params in parsed[loop_id]:
        if name != "POLY_LOOP":
            continue
        for point_ref in _entity_refs(_entity_ref_param(params)):
            points.append(_point_coordinates(parsed, point_ref, loop_id))
    if len(points) < 3:
        return []
    return points


def _unwrap_curve_name(parsed, curve_ref):
    """Return the effective curve type name, unwrapping TRIMMED_CURVE masters."""
    names = _entity_names(parsed, curve_ref)
    if "TRIMMED_CURVE" not in names:
        return names[0] if names else None
    for name, params in parsed[curve_ref]:
        if name == "TRIMMED_CURVE":
            parts = _split_parameters(params)
            if len(parts) < 2:
                return "TRIMMED_CURVE"
            refs = _entity_refs(parts[1])
            if not refs or refs[0] not in parsed:
                return "TRIMMED_CURVE"
            return _unwrap_curve_name(parsed, refs[0])
    return "TRIMMED_CURVE"


def _direction_vector(parsed, ref):
    """Return a DIRECTION vector, or None when the entity is missing."""
    if ref not in parsed:
        return None
    for name, params in parsed[ref]:
        if name == "DIRECTION":
            parts = _split_parameters(params)
            if len(parts) >= 2:
                values = _entity_numbers(parts[1])
                if len(values) == 3:
                    return tuple(values)
    return None


def _circle_arc_points(parsed, curve_ref, start, end):
    """Sample the shortest arc of a CIRCLE edge between its vertex points.

    Returns ``(points, mismatch)``; on mismatch the edge carries endpoints
    that do not lie on the declared circle and the caller treats it as a
    straight segment while emitting a warning.
    """
    radius = None
    axis_ref = None
    for name, params in parsed[curve_ref]:
        if name == "CIRCLE":
            parts = _split_parameters(params)
            if len(parts) < 3:
                raise ValueError("STEP CIRCLE #{} is malformed".format(curve_ref))
            refs = _entity_refs(parts[1])
            axis_ref = refs[0] if refs else None
            try:
                radius = float(parts[2].strip())
            except ValueError:
                raise ValueError("STEP CIRCLE #{} radius is not numeric".format(curve_ref))
    if axis_ref is None or radius is None or radius <= 0.0:
        raise ValueError("STEP CIRCLE #{} is malformed".format(curve_ref))
    center = None
    axis_dir_ref = None
    if axis_ref in parsed:
        for name, params in parsed[axis_ref]:
            if name == "AXIS2_PLACEMENT_3D":
                parts = _split_parameters(params)
                if len(parts) >= 2:
                    refs = _entity_refs(parts[1])
                    if refs:
                        center = _point_coordinates(parsed, refs[0], curve_ref)
                if len(parts) >= 3:
                    refs = _entity_refs(parts[2])
                    if refs:
                        axis_dir_ref = refs[0]
    if center is None:
        raise ValueError("STEP CIRCLE #{} has no placement location".format(curve_ref))
    start_offset = _v_sub(start, center)
    end_offset = _v_sub(end, center)
    start_distance = math.sqrt(_v_dot(start_offset, start_offset))
    end_distance = math.sqrt(_v_dot(end_offset, end_offset))
    tolerance = radius * 0.02
    if abs(start_distance - radius) > tolerance or abs(end_distance - radius) > tolerance:
        return (start, end), True
    u = _v_scale(start_offset, 1.0 / start_distance)
    w = _v_scale(end_offset, 1.0 / end_distance)
    normal = _direction_vector(parsed, axis_dir_ref) if axis_dir_ref is not None else None
    if normal is None:
        normal = (0.0, 0.0, 1.0)
    n = _v_normalize(normal)
    perp = _v_normalize(_v_cross(n, u))
    if perp is None:
        return (start, end), True
    angle = math.atan2(_v_dot(n, _v_cross(u, w)), _v_dot(u, w))
    if abs(angle) < 1e-9:
        return (start, end), False
    segments = max(4, min(32, int(math.ceil(abs(angle) / (math.pi / 12.0)))))
    sampled = [start]
    for step in range(1, segments):
        t = angle * (step / segments)
        direction = _v_add(_v_scale(u, math.cos(t)), _v_scale(perp, math.sin(t)))
        sampled.append(_v_add(center, _v_scale(direction, radius)))
    sampled.append(end)
    return sampled, False


_BSPLINE_EDGE_SAMPLES = 32


def _bspline_curve(parsed, curve_ref):
    """Parse a B-spline curve into ``(degree, control, knots, weights, closed)``."""
    degree = None
    control_refs = []
    multiplicities = []
    knots = []
    closed = False
    weights = None
    rational = False
    for name, params in parsed[curve_ref]:
        if name not in ("B_SPLINE_CURVE_WITH_KNOTS", "B_SPLINE_CURVE_WITH_KNOTS_AND_RATIONAL"):
            continue
        parts = _split_parameters(params)
        if len(parts) < 8:
            raise ValueError("STEP {} #{} is malformed".format(name, curve_ref))
        degree = int(float(parts[1].strip()))
        control_refs = []
        for token in _split_parameters(parts[2]):
            token = token.strip()
            if token.startswith("#"):
                control_refs.append(int(token[1:]))
            else:
                raise ValueError("STEP {} #{} control point list is malformed".format(name, curve_ref))
        closed = parts[4].strip() == ".T."
        multiplicities = [int(float(value)) for value in _entity_numbers(parts[6])]
        knots = list(_entity_numbers(parts[7]))
        if name.endswith("_AND_RATIONAL"):
            rational = True
            if len(parts) < 10:
                raise ValueError("STEP {} #{} has no weights".format(name, curve_ref))
            weights = list(_entity_numbers(parts[9]))
    if degree is None or degree < 1 or not control_refs or not knots:
        raise ValueError("STEP curve #{} is not a B-spline".format(curve_ref))
    control = [_point_coordinates(parsed, ref, curve_ref) for ref in control_refs]
    if len(multiplicities) != len(knots):
        raise ValueError("STEP B-spline #{} knot data is inconsistent".format(curve_ref))
    knot_vector = []
    for multiplicity, value in zip(multiplicities, knots):
        knot_vector.extend([value] * multiplicity)
    if len(knot_vector) != len(control) + degree + 1:
        raise ValueError("STEP B-spline #{} knot vector has the wrong length".format(curve_ref))
    if not rational:
        weights = [1.0] * len(control)
    elif weights is None or len(weights) != len(control):
        raise ValueError("STEP rational B-spline #{} has the wrong weight count".format(curve_ref))
    return degree, control, knot_vector, weights, closed


def _de_boor(control, weights, knots, degree, u):
    """Evaluate a (possibly rational) B-spline curve at parameter ``u``.

    Rational curves are evaluated in homogeneous coordinates (``w * point``
    with weight ``w``), matching Algorithm A3.1 of the NURBS Book.
    """
    count = len(control)
    if u <= knots[degree]:
        span = degree
    elif u >= knots[count]:
        span = count - 1
    else:
        span = degree
        while span < count - 1 and u >= knots[span + 1]:
            span += 1
    points = [
        _v_scale(control[span - degree + i], weights[span - degree + i])
        for i in range(degree + 1)
    ]
    point_weights = [weights[span - degree + i] for i in range(degree + 1)]
    for r in range(1, degree + 1):
        for i in range(degree, r - 1, -1):
            j = span - degree + i
            denominator = knots[j + degree - r + 1] - knots[j]
            alpha = 0.0 if denominator == 0.0 else (u - knots[j]) / denominator
            points[i] = _v_add(
                _v_scale(points[i - 1], 1.0 - alpha), _v_scale(points[i], alpha)
            )
            point_weights[i] = (1.0 - alpha) * point_weights[i - 1] + alpha * point_weights[i]
    weight = point_weights[degree]
    if abs(weight) < 1e-12:
        return points[degree]
    return _v_scale(points[degree], 1.0 / weight)


def _sample_bbox_diagonal(samples):
    minima = [min(point[index] for point in samples) for index in range(3)]
    maxima = [max(point[index] for point in samples) for index in range(3)]
    offset = _v_sub(maxima, minima)
    return math.sqrt(_v_dot(offset, offset))


def _bspline_edge_points(parsed, curve_ref, start, end):
    """Sample the B-spline edge between its vertex points.

    The edge's own parameter range is located by refining the closest-sample
    parameters of both endpoints, then resampled at fixed resolution.  Returns
    ``(points, mismatch)``; on mismatch the endpoints do not lie on the curve
    and the caller treats the edge as straight.
    """
    degree, control, knots, weights, closed = _bspline_curve(parsed, curve_ref)
    domain_start = knots[degree]
    domain_end = knots[len(control)]
    u_values = [
        domain_start + (domain_end - domain_start) * (step / _BSPLINE_EDGE_SAMPLES)
        for step in range(_BSPLINE_EDGE_SAMPLES + 1)
    ]
    samples = [_de_boor(control, weights, knots, degree, u) for u in u_values]
    tolerance = max(1e-9, _sample_bbox_diagonal(samples) * 1e-4)

    def refined_parameter(target):
        best = 0
        best_squared = None
        for index, point in enumerate(samples):
            offset = _v_sub(point, target)
            squared = offset[0] * offset[0] + offset[1] * offset[1] + offset[2] * offset[2]
            if best_squared is None or squared < best_squared:
                best_squared = squared
                best = index
        low = u_values[max(0, best - 1)]
        high = u_values[min(len(u_values) - 1, best + 1)]

        def distance2(value):
            point = _de_boor(control, weights, knots, degree, value)
            offset = _v_sub(point, target)
            return offset[0] * offset[0] + offset[1] * offset[1] + offset[2] * offset[2]

        golden = (math.sqrt(5.0) - 1.0) / 2.0
        m1 = high - golden * (high - low)
        m2 = low + golden * (high - low)
        f1 = distance2(m1)
        f2 = distance2(m2)
        for _ in range(18):
            if f1 < f2:
                high = m2
                m2 = m1
                f2 = f1
                m1 = high - golden * (high - low)
                f1 = distance2(m1)
            else:
                low = m1
                m1 = m2
                f1 = f2
                m2 = low + golden * (high - low)
                f2 = distance2(m2)
        u = (low + high) / 2.0
        point = _de_boor(control, weights, knots, degree, u)
        distance = math.sqrt(distance2(u))
        return u, distance

    u_start, start_error = refined_parameter(start)
    u_end, end_error = refined_parameter(end)
    if start_error > tolerance or end_error > tolerance:
        return (start, end), True

    def arc_samples(first, second):
        return [
            _de_boor(
                control,
                weights,
                knots,
                degree,
                first + (second - first) * (step / _BSPLINE_EDGE_SAMPLES),
            )
            for step in range(_BSPLINE_EDGE_SAMPLES + 1)
        ]

    if u_start <= u_end:
        arc = arc_samples(u_start, u_end)
    elif closed:
        arc = arc_samples(u_start, domain_end) + arc_samples(domain_start, u_end)[1:]
    else:
        arc = list(reversed(arc_samples(u_end, u_start)))
    return [start] + arc[1:-1] + [end], False


def _cross_2d(first, second, third):
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _triangulate_polygon(points):
    """Triangulate a simple (possibly concave or non-planar) polygon by ear clipping.

    Points are projected onto their dominant plane; collinear vertices are
    dropped first. Returns a list of index triples into ``points`` preserving
    the polygon orientation. A self-intersecting or degenerate face returns an
    empty list; it is safer to omit one face than fabricate a large sheet.
    """
    count = len(points)
    if count < 3:
        return []
    normal = (0.0, 0.0, 0.0)
    for index in range(1, count - 1):
        edge_a = _v_sub(points[index], points[0])
        edge_b = _v_sub(points[index + 1], points[0])
        normal = _v_add(normal, _v_cross(edge_a, edge_b))
    # Drop the dominant normal component so the polygon is projected onto its
    # actual face plane. Dropping the smallest component collapses planar faces
    # into lines and causes the fallback triangulator to create long spikes.
    axis = max(range(3), key=lambda item: abs(normal[item]))
    axes = [item for item in range(3) if item != axis]
    flat = [(point[axes[0]], point[axes[1]]) for point in points]
    scale = max(1.0, max(max(abs(x), abs(y)) for x, y in flat))
    epsilon = 1e-12 * scale * scale
    area = 0.0
    for index in range(count):
        x1, y1 = flat[index]
        x2, y2 = flat[(index + 1) % count]
        area += x1 * y2 - x2 * y1
    orientation = 1.0 if area >= 0.0 else -1.0

    cleaned = []
    original = []
    for index in range(count):
        previous = flat[(index - 1) % count]
        current = flat[index]
        following = flat[(index + 1) % count]
        if abs(_cross_2d(previous, current, following)) <= epsilon:
            continue
        cleaned.append(current)
        original.append(index)
    if len(cleaned) < 3:
        return []

    size = len(cleaned)
    turns = [
        _cross_2d(cleaned[index - 1], cleaned[index], cleaned[(index + 1) % size])
        for index in range(size)
    ]
    if all(turn * orientation > -epsilon for turn in turns):
        return [
            (original[0], original[index], original[index + 1])
            for index in range(1, size - 1)
        ]

    def inside(point, first, second, third):
        first_sign = _cross_2d(first, second, point)
        second_sign = _cross_2d(second, third, point)
        third_sign = _cross_2d(third, first, point)
        has_negative = first_sign < -epsilon or second_sign < -epsilon or third_sign < -epsilon
        has_positive = first_sign > epsilon or second_sign > epsilon or third_sign > epsilon
        return not (has_negative and has_positive)

    remaining = list(range(size))
    reflex = [index for index in range(size) if turns[index] * orientation <= epsilon]
    triangles = []
    limit = size * size
    while len(remaining) > 3 and len(triangles) < limit:
        clipped = False
        for position, i1 in enumerate(remaining):
            i0 = remaining[position - 1]
            i2 = remaining[(position + 1) % len(remaining)]
            if turns[i1] * orientation <= epsilon:
                continue
            if any(
                inside(cleaned[vertex], cleaned[i0], cleaned[i1], cleaned[i2])
                for vertex in reflex
                if vertex != i0 and vertex != i1 and vertex != i2
            ):
                continue
            triangles.append((original[i0], original[i1], original[i2]))
            remaining.pop(position)
            for neighbor in (i0, i2):
                neighbor_position = remaining.index(neighbor)
                previous = remaining[neighbor_position - 1]
                following = remaining[(neighbor_position + 1) % len(remaining)]
                updated = _cross_2d(cleaned[previous], cleaned[neighbor], cleaned[following])
                turns[neighbor] = updated
                if updated * orientation <= epsilon:
                    if neighbor not in reflex:
                        reflex.append(neighbor)
                elif neighbor in reflex:
                    reflex.remove(neighbor)
            clipped = True
            break
        if not clipped:
            break
    if len(remaining) == 3:
        triangles.append((original[remaining[0]], original[remaining[1]], original[remaining[2]]))
    if len(triangles) != size - 2:
        return []
    return triangles


def _edge_loop_points(parsed, loop_id):
    """Return an ordered polygon for an EDGE_LOOP, unwrapping oriented edges.

    Returns ``(points, unsupported_names, arc_count, mismatch_count)``.
    Straight edges (LINE, TRIMMED_CURVE over LINE, or no curve) are exact;
    CIRCLE edges are sampled as their shortest arc; other curve types are
    reported as unsupported so the caller can reject the file honestly.
    """
    points = []
    unsupported = []
    arcs = 0
    mismatches = 0
    for name, params in parsed[loop_id]:
        if name != "EDGE_LOOP":
            continue
        for oriented_ref in _entity_refs(_entity_ref_param(params)):
            if oriented_ref not in parsed:
                raise ValueError(
                    "STEP EDGE_LOOP #{} references missing edge #{}".format(loop_id, oriented_ref)
                )
            edge_ref = oriented_ref
            orientation = ".T."
            if "ORIENTED_EDGE" in _entity_names(parsed, oriented_ref):
                parts = _split_parameters(parsed[oriented_ref][0][1])
                if len(parts) > 3:
                    edge_refs = _entity_refs(parts[3])
                    if not edge_refs:
                        raise ValueError(
                            "STEP ORIENTED_EDGE #{} has no edge element".format(oriented_ref)
                        )
                    edge_ref = edge_refs[0]
                if len(parts) > 4:
                    orientation = parts[4].strip()
            if edge_ref not in parsed:
                raise ValueError(
                    "STEP EDGE_LOOP #{} references missing edge #{}".format(loop_id, edge_ref)
                )
            edge_names = _entity_names(parsed, edge_ref)
            if "EDGE_CURVE" not in edge_names:
                unsupported.append(edge_names[0])
                continue
            start_ref = None
            end_ref = None
            curve_ref = None
            for edge_name, edge_params in parsed[edge_ref]:
                if edge_name != "EDGE_CURVE":
                    continue
                parts = _split_parameters(edge_params)
                if len(parts) < 4:
                    raise ValueError("STEP EDGE_CURVE #{} is malformed".format(edge_ref))
                start_refs = _entity_refs(parts[1])
                end_refs = _entity_refs(parts[2])
                if not start_refs or not end_refs:
                    raise ValueError("STEP EDGE_CURVE #{} has no vertex points".format(edge_ref))
                start_ref = start_refs[0]
                end_ref = end_refs[0]
                if parts[3].strip() != "$":
                    curve_refs = _entity_refs(parts[3])
                    curve_ref = curve_refs[0] if curve_refs else None
            if start_ref is None or end_ref is None:
                raise ValueError("STEP EDGE_CURVE #{} is malformed".format(edge_ref))
            if orientation == ".F.":
                start_ref, end_ref = end_ref, start_ref
            start = _point_coordinates(parsed, start_ref, loop_id)
            end = _point_coordinates(parsed, end_ref, loop_id)
            if start == end:
                continue
            curve_name = None
            if curve_ref is not None:
                if curve_ref not in parsed:
                    raise ValueError(
                        "STEP EDGE_CURVE #{} references missing curve #{}".format(
                            edge_ref, curve_ref
                        )
                    )
                curve_name = _unwrap_curve_name(parsed, curve_ref)
            if curve_name == "CIRCLE":
                arc, mismatch = _circle_arc_points(parsed, curve_ref, start, end)
                if mismatch:
                    mismatches += 1
                    points.append(start)
                    points.append(end)
                else:
                    arcs += 1
                    points.extend(arc)
            elif curve_name in ("B_SPLINE_CURVE_WITH_KNOTS", "B_SPLINE_CURVE_WITH_KNOTS_AND_RATIONAL"):
                arc, mismatch = _bspline_edge_points(parsed, curve_ref, start, end)
                if mismatch:
                    mismatches += 1
                    points.append(start)
                    points.append(end)
                else:
                    arcs += 1
                    points.extend(arc)
            elif curve_name is None or curve_name == "LINE":
                points.append(start)
                points.append(end)
            else:
                unsupported.append(curve_name)
                continue
    if unsupported or len(points) < 3:
        return points, unsupported, arcs, mismatches
    return points, unsupported, arcs, mismatches


def _point_list_coordinates(parsed, list_ref, context):
    """Return coordinates from a CARTESIAN_POINT_LIST_3D."""
    if list_ref not in parsed:
        raise ValueError("STEP point list reference #{} is missing".format(list_ref))
    points = []
    for name, params in parsed[list_ref]:
        if name == "CARTESIAN_POINT_LIST_3D":
            parts = _split_parameters(params)
            if len(parts) < 2:
                raise ValueError(
                    "STEP CARTESIAN_POINT_LIST_3D #{} is malformed".format(list_ref)
                )
            for token in _split_parameters(parts[1]):
                token = token.strip()
                if token.startswith("#"):
                    points.append(_point_coordinates(parsed, int(token[1:]), context))
                else:
                    values = _entity_numbers(token)
                    if len(values) != 3:
                        raise ValueError(
                            "STEP CARTESIAN_POINT_LIST_3D #{} contains an invalid coordinate".format(
                                list_ref
                            )
                        )
                    points.append(tuple(values))
    if not points:
        raise ValueError("STEP point list reference #{} is empty".format(list_ref))
    return points


def _tessellated_triangles(parsed, root_id):
    """Return coordinate triples from an AP242 TRIANGULATED_FACE_SET."""
    point_list_ref = None
    triangle_tokens = None
    for name, params in parsed[root_id]:
        if name == "TRIANGULATED_FACE_SET":
            parts = _split_parameters(params)
            if len(parts) < 3:
                raise ValueError("STEP TRIANGULATED_FACE_SET #{} is malformed".format(root_id))
            refs = _entity_refs(parts[1])
            if refs:
                point_list_ref = refs[0]
            triangle_tokens = _split_parameters(parts[2])
    if point_list_ref is None or triangle_tokens is None:
        raise ValueError("STEP TRIANGULATED_FACE_SET #{} is malformed".format(root_id))
    points = _point_list_coordinates(parsed, point_list_ref, root_id)
    triangles = []
    for token in triangle_tokens:
        token = token.strip()
        if token.startswith("#"):
            tri_ref = int(token[1:])
            if tri_ref not in parsed:
                raise ValueError(
                    "STEP TRIANGULATED_FACE_SET #{} references missing face #{}".format(
                        root_id, tri_ref
                    )
                )
            values = None
            for name, params in parsed[tri_ref]:
                if name == "TRIANGULATED_FACE":
                    parts = _split_parameters(params)
                    if len(parts) >= 2:
                        values = _entity_numbers(parts[1])
            if values is None or len(values) != 3:
                raise ValueError(
                    "STEP TRIANGULATED_FACE #{} must reference three indices".format(tri_ref)
                )
            indices = [int(value) for value in values]
        else:
            values = _entity_numbers(token)
            if len(values) != 3:
                raise ValueError(
                    "STEP TRIANGULATED_FACE_SET #{} contains an invalid triangle".format(root_id)
                )
            indices = [int(value) for value in values]
        for value in indices:
            if value < 1 or value > len(points):
                raise ValueError("STEP triangle index {} is out of range".format(value))
        triangles.append(
            (points[indices[0] - 1], points[indices[1] - 1], points[indices[2] - 1])
        )
    return triangles


def _step_identity_transform():
    return (
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        (0.0, 0.0, 0.0),
    )


def _step_transform_apply(transform, point):
    rotation, translation = transform
    return tuple(_v_dot(rotation[row], point) + translation[row] for row in range(3))


def _step_transform_compose(outer, inner):
    """Return the transform that applies ``inner`` and then ``outer``."""
    outer_rotation, outer_translation = outer
    inner_rotation, inner_translation = inner
    rotation = tuple(
        tuple(
            sum(outer_rotation[row][index] * inner_rotation[index][column] for index in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    translation = tuple(
        _v_dot(outer_rotation[row], inner_translation) + outer_translation[row]
        for row in range(3)
    )
    return rotation, translation


def _step_transform_inverse(transform):
    rotation, translation = transform
    inverse_rotation = tuple(
        tuple(rotation[column][row] for column in range(3)) for row in range(3)
    )
    inverse_translation = tuple(-_v_dot(inverse_rotation[row], translation) for row in range(3))
    return inverse_rotation, inverse_translation


def _step_axis_frame(parsed, placement_id):
    """Return the local-to-placement transform for an AXIS2_PLACEMENT_3D."""
    clauses = parsed.get(placement_id, ())
    params = next((value for name, value in clauses if name == "AXIS2_PLACEMENT_3D"), None)
    if params is None:
        raise ValueError("STEP placement #{} is not an AXIS2_PLACEMENT_3D".format(placement_id))
    parts = _split_parameters(params)
    if len(parts) < 4:
        raise ValueError("STEP placement #{} is malformed".format(placement_id))
    origin_ref = int(parts[1].strip().lstrip("#"))
    axis_ref = int(parts[2].strip().lstrip("#"))
    reference_ref = int(parts[3].strip().lstrip("#"))
    origin = _point_coordinates(parsed, origin_ref, placement_id)
    z_axis = _v_normalize(_direction_vector(parsed, axis_ref) or (0.0, 0.0, 1.0))
    reference = _direction_vector(parsed, reference_ref) or (1.0, 0.0, 0.0)
    x_axis = _v_normalize(_v_sub(reference, _v_scale(z_axis, _v_dot(reference, z_axis))))
    if x_axis is None:
        raise ValueError("STEP placement #{} has a degenerate reference direction".format(placement_id))
    y_axis = _v_normalize(_v_cross(z_axis, x_axis))
    if y_axis is None:
        raise ValueError("STEP placement #{} has a degenerate axis frame".format(placement_id))
    rotation = (
        (x_axis[0], y_axis[0], z_axis[0]),
        (x_axis[1], y_axis[1], z_axis[1]),
        (x_axis[2], y_axis[2], z_axis[2]),
    )
    return rotation, origin


def _step_representation_roots(parsed, entities):
    """Map B-rep roots to their defining representation and local placement."""
    roots = {}
    for entity_id, _ in entities:
        params = next(
            (
                value
                for name, value in parsed[entity_id]
                if name == "ADVANCED_BREP_SHAPE_REPRESENTATION"
            ),
            None,
        )
        if params is None:
            continue
        refs = _entity_refs(_entity_ref_param(params))
        placement_refs = [ref for ref in refs if "AXIS2_PLACEMENT_3D" in _entity_names(parsed, ref)]
        placement = placement_refs[0] if placement_refs else None
        for root_id in refs:
            if any(name in _STEP_ROOT_ENTITIES for name in _entity_names(parsed, root_id)):
                roots.setdefault(root_id, []).append((entity_id, placement))
    return roots


def _step_representation_edges(parsed, entities):
    """Build source-representation to target-representation transform edges."""
    edges = {}
    for entity_id, _ in entities:
        relationship_params = next(
            (
                value
                for name, value in parsed[entity_id]
                if name == "REPRESENTATION_RELATIONSHIP"
            ),
            None,
        )
        transformation_params = next(
            (
                value
                for name, value in parsed[entity_id]
                if name == "REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION"
            ),
            None,
        )
        if relationship_params is None or transformation_params is None:
            continue
        relationship_parts = _split_parameters(relationship_params)
        transformation_refs = _entity_refs(transformation_params)
        if len(relationship_parts) < 4 or not transformation_refs:
            continue
        source_rep = int(relationship_parts[2].strip().lstrip("#"))
        target_rep = int(relationship_parts[3].strip().lstrip("#"))
        transformation_id = transformation_refs[0]
        transformation_params = next(
            (
                value
                for name, value in parsed.get(transformation_id, ())
                if name == "ITEM_DEFINED_TRANSFORMATION"
            ),
            None,
        )
        if transformation_params is None:
            continue
        transformation_parts = _split_parameters(transformation_params)
        if len(transformation_parts) < 4:
            continue
        source_placement = int(transformation_parts[2].strip().lstrip("#"))
        target_placement = int(transformation_parts[3].strip().lstrip("#"))
        source_frame = _step_axis_frame(parsed, source_placement)
        target_frame = _step_axis_frame(parsed, target_placement)
        mapping = _step_transform_compose(
            target_frame,
            _step_transform_inverse(source_frame),
        )
        edges.setdefault(source_rep, []).append((target_rep, mapping))
    return edges


def _step_root_instances(parsed, entities, root_ids):
    """Return ``(root_id, transform)`` occurrences, including assembly transforms."""
    root_representations = _step_representation_roots(parsed, entities)
    representation_edges = _step_representation_edges(parsed, entities)
    instances = []
    identity = _step_identity_transform()
    for root_id in root_ids:
        occurrences = root_representations.get(root_id, ())
        if not occurrences:
            instances.append((root_id, identity))
            continue
        for representation_id, local_placement in occurrences:
            paths = []

            def visit(current_representation, transform, visited):
                outgoing = representation_edges.get(current_representation, ())
                available = [
                    (target, edge)
                    for target, edge in outgoing
                    if target not in visited
                ]
                if not available:
                    paths.append(transform)
                    return
                for target, edge in available:
                    visit(
                        target,
                        _step_transform_compose(edge, transform),
                        visited | {target},
                    )

            if representation_edges.get(representation_id):
                visit(representation_id, identity, {representation_id})
            elif local_placement is not None:
                paths.append(_step_axis_frame(parsed, local_placement))
            else:
                paths.append(identity)
            for transform in paths:
                instances.append((root_id, transform))
    return instances


def _parse_step(data):
    """Parse the faceted B-REP subset of ISO 10303-21 into a triangle mesh.

    Supports POLY_LOOP and EDGE_LOOP face bounds (the latter with straight
    LINE, circular CIRCLE, or sampled B-spline edges), assembly occurrence
    transforms, and AP242 TRIANGULATED_FACE_SET tessellated solids. Returns
    ``(geometry, source_units, diagnostics)``.  Unsupported advanced (NURBS)
    B-reps return ``(None, None, (blocker_diagnostic,))``; malformed input
    raises ``ValueError``.

    Topology that removes material (face inner bounds / holes and
    BREP_WITH_VOIDS void shells) cannot be represented by the faceted
    importer.  Silently dropping it would turn a shell with holes or voids
    into a closed solid whose reported volume includes the missing material,
    so any such file returns ``(None, None, (blocker_diagnostic,))`` and the
    geometry is marked unsupported instead of certifying a wrong volume.
    """
    text = data.decode("utf-8-sig")
    data_section = _data_section(text)
    if data_section is None:
        raise ValueError("STEP file has no DATA section")
    entities = _scan_step_entities(data_section)
    if not entities:
        raise ValueError("STEP file contains no entities")
    parsed = {}
    for entity_id, body in entities:
        parsed[entity_id] = _parse_entity_body(body)

    diagnostics = []
    source_units = _step_units(entities, parsed)
    if source_units is None:
        source_units = "mm"
        diagnostics.append(_diagnostic("step_units_assumed_mm", "warning", _STEP_UNITS_WARNING))
    else:
        try:
            source_units = normalize_unit(source_units)
        except UnitError:
            source_units = "mm"
            diagnostics.append(_diagnostic("step_units_assumed_mm", "warning", _STEP_UNITS_WARNING))

    def names(entity_id):
        return tuple(name for name, _ in parsed[entity_id])

    root_ids = [
        entity_id
        for entity_id, _ in entities
        if any(name in _STEP_ROOT_ENTITIES for name in names(entity_id))
    ]
    faceted_loop_ids = [
        entity_id for entity_id, _ in entities if any(name in _STEP_LOOP_ENTITIES for name in names(entity_id))
    ]
    if not root_ids and not faceted_loop_ids:
        advanced = []
        for entity_id, _ in entities:
            for name in names(entity_id):
                if name in _ADVANCED_STEP_ENTITIES and name not in advanced:
                    advanced.append(name)
        if advanced:
            diagnostic = _diagnostic(
                "unsupported_format",
                "blocker",
                "STEP advanced/NURBS B-rep is unsupported by the standard-library importer; no geometry was fabricated",
                format="step",
                entities=",".join(advanced[:5]),
            )
            return None, None, (diagnostic,)
        raise ValueError("STEP file contains no faceted geometry")
    if not root_ids:
        raise ValueError("STEP file contains no supported root entity")

    polygons = []
    arc_edges = 0
    arc_mismatches = 0
    degenerate_loops = 0
    untriangulated_faces = 0
    unsupported = []
    unsupported_entities = []
    skipped_holes = 0
    skipped_void_shells = 0
    tessellated = []
    root_instances = _step_root_instances(parsed, entities, root_ids)
    for root_id, root_transform in root_instances:
        shell_ids = []
        tessellated_ids = []
        for name, params in parsed[root_id]:
            if name == "MANIFOLD_SOLID_BREP":
                shell_ids.extend(_entity_refs(_entity_ref_param(params)))
            elif name == "BREP_WITH_VOIDS":
                parts = _split_parameters(params)
                shell_ids.extend(_entity_refs(parts[1]))
                if len(parts) > 2:
                    skipped_void_shells += len(_entity_refs(parts[2]))
            elif name == "SHELL_BASED_SURFACE_MODEL":
                shell_ids.extend(_entity_refs(_entity_ref_param(params)))
            elif name in ("CLOSED_SHELL", "OPEN_SHELL"):
                shell_ids.append(root_id)
            elif name == "TRIANGULATED_FACE_SET":
                tessellated_ids.append(root_id)

        face_ids = []
        for shell_id in shell_ids:
            if shell_id not in parsed:
                raise ValueError("STEP shell reference #{} is missing".format(shell_id))
            if not any(name in ("CLOSED_SHELL", "OPEN_SHELL") for name in names(shell_id)):
                unsupported_entities.append(names(shell_id)[0])
                continue
            for name, params in parsed[shell_id]:
                if name in ("CLOSED_SHELL", "OPEN_SHELL"):
                    face_ids.extend(_entity_refs(_entity_ref_param(params)))

        loop_specs = []
        for face_id in face_ids:
            if face_id not in parsed:
                raise ValueError("STEP face reference #{} is missing".format(face_id))
            if not any(name in _STEP_FACE_ENTITIES for name in names(face_id)):
                unsupported_entities.append(names(face_id)[0])
                continue
            bounds = []
            for name, params in parsed[face_id]:
                if name in _STEP_FACE_ENTITIES:
                    bounds.extend(_entity_refs(_entity_ref_param(params)))
            outer_bounds = []
            inner_bounds = []
            for bound_id in bounds:
                if bound_id not in parsed:
                    raise ValueError("STEP bound reference #{} is missing".format(bound_id))
                bound_name = next(
                    (name for name, _ in parsed[bound_id] if name in ("FACE_OUTER_BOUND", "FACE_BOUND")),
                    None,
                )
                if bound_name is None:
                    unsupported_entities.append(names(bound_id)[0])
                    continue
                bound_params = next(
                    params
                    for name, params in parsed[bound_id]
                    if name == bound_name
                )
                bound_parts = _split_parameters(bound_params)
                reverse = len(bound_parts) > 2 and bound_parts[2].strip() == ".F."
                target = outer_bounds if bound_name == "FACE_OUTER_BOUND" else inner_bounds
                target.append((bound_id, reverse))
            if not outer_bounds:
                outer_bounds = inner_bounds
            else:
                skipped_holes += len(inner_bounds)
            for bound_id, reverse in outer_bounds:
                bound_params = next(
                    params
                    for name, params in parsed[bound_id]
                    if name in ("FACE_OUTER_BOUND", "FACE_BOUND")
                )
                for loop_ref in _entity_refs(_entity_ref_param(bound_params)):
                    if loop_ref not in parsed:
                        raise ValueError("STEP loop reference #{} is missing".format(loop_ref))
                    if any(name in _STEP_LOOP_ENTITIES for name in names(loop_ref)):
                        loop_specs.append((loop_ref, reverse))
                    else:
                        unsupported_entities.append(names(loop_ref)[0])

        unique_loop_specs = []
        for loop_spec in loop_specs:
            if loop_spec not in unique_loop_specs:
                unique_loop_specs.append(loop_spec)
        for loop_id, reverse in unique_loop_specs:
            if "POLY_LOOP" in names(loop_id):
                points = _poly_loop_points(parsed, loop_id)
            else:
                points, loop_unsupported, arcs, mismatches = _edge_loop_points(parsed, loop_id)
                unsupported.extend(loop_unsupported)
                arc_edges += arcs
                arc_mismatches += mismatches
            if not points:
                degenerate_loops += 1
                continue
            if reverse:
                points = list(reversed(points))
            polygons.append([_step_transform_apply(root_transform, point) for point in points])
        for tessellated_id in tessellated_ids:
            for first, second, third in _tessellated_triangles(parsed, tessellated_id):
                tessellated.append(
                    (
                        _step_transform_apply(root_transform, first),
                        _step_transform_apply(root_transform, second),
                        _step_transform_apply(root_transform, third),
                    )
                )
    if unsupported_entities:
        unique_names = []
        for name in unsupported_entities:
            if name not in unique_names:
                unique_names.append(name)
        diagnostic = _diagnostic(
            "unsupported_step_entities",
            "blocker",
            "STEP faceted B-rep references unsupported entity types: {}".format(", ".join(unique_names)),
            entities=",".join(unique_names),
        )
        return None, None, (diagnostic,)
    if unsupported:
        unique_names = []
        for name in unsupported:
            if name not in unique_names:
                unique_names.append(name)
        diagnostic = _diagnostic(
            "unsupported_step_entities",
            "blocker",
            "STEP faceted B-rep references unsupported entity types: {}".format(", ".join(unique_names)),
            entities=",".join(unique_names),
        )
        return None, None, (diagnostic,)
    if skipped_holes:
        return None, None, (
            _diagnostic(
                "step_topology_unsupported",
                "blocker",
                "STEP face with inner bounds (holes) is not supported by the faceted importer; imported volume may be overestimated; mass is not certified",
                kind="holes",
                count=str(skipped_holes),
            ),
        )
    if skipped_void_shells:
        return None, None, (
            _diagnostic(
                "step_topology_unsupported",
                "blocker",
                "STEP BREP_WITH_VOIDS void shells are not supported by the faceted importer; imported volume may be overestimated; mass is not certified",
                kind="voids",
                count=str(skipped_void_shells),
            ),
        )
    if arc_edges:
        diagnostics.append(
            _diagnostic(
                "step_curved_edges_approximated",
                "warning",
                "STEP curved (circular or B-spline) edges were approximated as polyline segments",
                count=str(arc_edges),
            )
        )
    if arc_mismatches:
        diagnostics.append(
            _diagnostic(
                "step_arc_endpoints_mismatch",
                "warning",
                "STEP curved edges whose endpoints do not lie on the curve were treated as straight segments",
                count=str(arc_mismatches),
            )
        )
    if not polygons and not tessellated:
        raise ValueError("STEP file produced no triangles")

    vertices = []
    index_by_point = {}

    def vertex_index(point):
        index = index_by_point.get(point)
        if index is None:
            index = len(vertices)
            vertices.append(point)
            index_by_point[point] = index
        return index

    triangles = []
    for points in polygons:
        collapsed = []
        for point in points:
            if not collapsed or point != collapsed[-1]:
                collapsed.append(point)
        if len(collapsed) > 1 and collapsed[0] == collapsed[-1]:
            collapsed.pop()
        if len(collapsed) < 3:
            degenerate_loops += 1
            continue
        local_triangles = _triangulate_polygon(collapsed)
        if not local_triangles:
            untriangulated_faces += 1
            continue
        for first, second, third in local_triangles:
            triangles.append(
                (
                    vertex_index(collapsed[first]),
                    vertex_index(collapsed[second]),
                    vertex_index(collapsed[third]),
                )
            )
    for first, second, third in tessellated:
        triangles.append((vertex_index(first), vertex_index(second), vertex_index(third)))
    filtered = []
    for first, second, third in triangles:
        edge_a = _v_sub(vertices[second], vertices[first])
        edge_b = _v_sub(vertices[third], vertices[first])
        cross = _v_cross(edge_a, edge_b)
        if _v_dot(cross, cross) <= 1e-24:
            continue
        filtered.append((first, second, third))
    triangles = filtered
    if degenerate_loops:
        diagnostics.append(
            _diagnostic(
                "step_degenerate_loop_skipped",
                "warning",
                "STEP degenerate loops with fewer than three points were skipped",
                count=str(degenerate_loops),
            )
        )
    if untriangulated_faces:
        diagnostics.append(
            _diagnostic(
                "step_faces_skipped_untriangulatable",
                "warning",
                "STEP faces with invalid or non-planar projected boundaries were skipped",
                count=str(untriangulated_faces),
            )
        )
    if not triangles:
        raise ValueError("STEP file produced no triangles")
    geometry = TriangleMesh(vertices, triangles, units=source_units)
    return geometry, source_units, tuple(diagnostics)


def _mesh_diagnostics(mesh):
    diagnostics = mesh.diagnostics()
    values = []
    if not diagnostics.safe_for_mass_properties:
        values.append(
            _diagnostic(
                "mesh_not_safe_for_mass_properties",
                "warning",
                "mesh imported without repair; topology diagnostics block safe solid mass properties",
                issues=", ".join(diagnostics.issues),
            )
        )
    return tuple(values)


# Seam-stitching weld tolerance as a fraction of the mesh's own diagonal.
# STEP/STL tessellation often duplicates the vertices along an intended seam
# by a small numerical difference; welding anything closer than this
# tolerance closes the seam without risking distinct geometry (typically
# 1 um on a 100 mm part).
MESH_WELD_TOLERANCE_FRACTION = 1e-5

# Meshes above this triangle count skip the weld-repair attempt: the edge
# passes and candidate diagnostics would cost seconds on multi-hundred-
# thousand-triangle flattened assemblies for (usually) no seam-stitching
# gain.  The envelope mass fallback still applies to them.
MESH_REPAIR_TRIANGLE_LIMIT = 250000


def _weld_vertices(vertices, triangles, tolerance):
    """Merge vertices closer than ``tolerance`` (union-find over a hash grid)."""
    if not vertices or tolerance <= 0.0:
        return list(vertices), list(triangles)
    origin = (min(vertex[0] for vertex in vertices), min(vertex[1] for vertex in vertices), min(vertex[2] for vertex in vertices))
    parent = list(range(len(vertices)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        root_first, root_second = find(first), find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    def cell_key(vertex):
        return (
            int(math.floor((vertex[0] - origin[0]) / tolerance)),
            int(math.floor((vertex[1] - origin[1]) / tolerance)),
            int(math.floor((vertex[2] - origin[2]) / tolerance)),
        )

    grid = {}
    for index, vertex in enumerate(vertices):
        grid.setdefault(cell_key(vertex), []).append(index)
    for index, vertex in enumerate(vertices):
        cx, cy, cz = cell_key(vertex)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for candidate in grid.get((cx + dx, cy + dy, cz + dz), ()):
                        if candidate <= index:
                            continue
                        other = vertices[candidate]
                        if all(abs(vertex[axis] - other[axis]) <= tolerance for axis in range(3)):
                            union(index, candidate)
    remap = {}
    welded = []
    for index in range(len(vertices)):
        root = find(index)
        if root not in remap:
            remap[root] = len(welded)
            welded.append(vertices[index])
    rebuilt = []
    for triangle in triangles:
        mapped = tuple(remap[find(index)] for index in triangle)
        if len(set(mapped)) == 3:
            rebuilt.append(mapped)
    return welded, rebuilt


def repair_open_mesh(mesh):
    """Attempt a conservative seam-stitch weld on an open triangle mesh.

    Welding only merges vertices already coincident within a small tolerance
    (a fraction of the mesh diagonal) — it never fabricates geometry.  The
    repaired mesh is accepted ONLY when its topology then certifies safe
    solid mass properties; otherwise the original mesh is returned untouched
    (with no repair claim).  Returns ``(geometry, repair_diagnostics)`` where
    ``geometry`` is the repaired mesh or the original, and diagnostics are
    empty unless a repair was actually applied.
    """
    if not isinstance(mesh, TriangleMesh):
        return mesh, ()
    # Edge-level precheck only: a mesh that is clearly open at the edge
    # level needs no full diagnostic pass on the ORIGINAL — the welded
    # candidate's diagnostics below are the certification, and the original's
    # self-intersection sweep is wasted work when the mesh is open (real
    # tessellated shells are open; the sweep is the dominant cost of the
    # drop-test pipeline on a 46-part STEP assembly).  A mesh that is closed
    # at the edge level is checked as-is first (the historical fast path).
    boundary, nonmanifold, degenerate, inconsistent = edge_topology(mesh.vertices, mesh.triangles)
    if not (boundary or nonmanifold or degenerate or inconsistent):
        if mesh.diagnostics().safe_for_mass_properties:
            return mesh, ()
        # Edge-closed but unsafe (e.g. degenerate, inconsistent winding, or
        # self-intersecting): no weld can fix those, so there is nothing to
        # repair.
        return mesh, ()
    # Bounded cost: oversized flattened assemblies are never weld-candidates.
    if len(mesh.triangles) > MESH_REPAIR_TRIANGLE_LIMIT:
        return mesh, ()
    bounds = mesh.bounds()
    size = bounds.size
    diagonal = math.sqrt(sum(item * item for item in size)) if all(item > 0.0 for item in size) else 0.0
    if diagonal <= 0.0:
        return mesh, ()
    tolerance = max(1e-12, diagonal * MESH_WELD_TOLERANCE_FRACTION)
    welded, rebuilt = _weld_vertices(mesh.vertices, mesh.triangles, tolerance)
    if len(welded) == len(mesh.vertices) and len(rebuilt) == len(mesh.triangles):
        return mesh, ()
    # Cheap edge-level precheck on the raw welded arrays (no components,
    # nesting, or self-intersection sweeps): most genuinely open shells stay
    # open after welding, and only a candidate that is closed at the edge
    # level is worth a full diagnostic pass for certification.
    boundary, nonmanifold, degenerate, inconsistent = edge_topology(welded, rebuilt)
    if boundary or nonmanifold or degenerate or inconsistent:
        return mesh, ()
    repaired = TriangleMesh(welded, rebuilt, units=mesh.units, transform=mesh.transform)
    diagnostics = repaired.diagnostics()
    if not diagnostics.safe_for_mass_properties:
        return mesh, ()
    details = {
        "merged_vertices": str(len(mesh.vertices) - len(welded)),
        "removed_degenerate_triangles": str(len(mesh.triangles) - len(rebuilt)),
        "tolerance_m": "{:.6g}".format(tolerance),
        "boundary_edges_after": str(diagnostics.boundary_edges),
    }
    return repaired, (_diagnostic(
        "mesh_weld_repair",
        "info",
        "open mesh seams were stitched by welding coincident vertices; mass properties are now certified",
        **details,
    ),)


def load_geometry(
    path_or_bytes,
    fmt="auto",
    units=None,
    step_backend="auto",
    step_asset_dir=None,
    step_timeout=None,
):
    """Load analytic JSON, OBJ, ASCII/binary STL, or STEP geometry.

    OBJ and STL have no portable unit declaration, so ``units`` is mandatory
    for those formats.  STEP declares its own length units in the file, so an
    explicit ``units`` argument is ignored for STEP. Unsupported curve types
    or malformed standard-library B-rep structures return structured
    diagnostics; advanced STEP uses the optional kernel backend and never
    substitutes a mesh or bounding box for an unavailable CAD operation.
    """

    data, source_name = _source_bytes(path_or_bytes)
    format_name = _format_name(fmt)
    if format_name == "auto":
        format_name = _detect_format(data, source_name)
    aliases = {"stp": "step", "stl-ascii": "stl", "stl-binary": "stl", "trianglemesh": "mesh"}
    format_name = aliases.get(format_name, format_name)
    if format_name == "cad":
        diagnostic = _diagnostic(
            "unsupported_format",
            "blocker",
            "CAD geometry is unsupported by the standard-library importer; no geometry was fabricated",
            format=format_name,
        )
        return _result(None, format_name, units, source_name, diagnostics=(diagnostic,), unsupported=True)
    if format_name in ("obj", "stl"):
        source_units = _require_import_units(units, format_name)
    elif format_name == "step":
        source_units = None
    else:
        source_units = normalize_unit(units) if units is not None else None
    if format_name == "json":
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("geometry JSON must contain an object")
        geometry = geometry_from_dict(payload, units=units)
        payload_units = payload.get("units", source_units)
        nested = payload.get("geometry")
        if payload_units is None and isinstance(nested, Mapping):
            payload_units = nested.get("units")
        return _result(geometry, "json", payload_units, source_name, payload=payload, diagnostics=_mesh_diagnostics(geometry) if isinstance(geometry, TriangleMesh) else ())
    if format_name == "obj":
        geometry, diagnostics = _parse_obj(data, source_units)
        geometry, repair_diagnostics = repair_open_mesh(geometry)
        return _result(geometry, "obj", source_units, source_name, diagnostics=tuple(diagnostics) + _mesh_diagnostics(geometry) + repair_diagnostics)
    if format_name == "stl":
        if _looks_binary_stl(data):
            geometry = _parse_binary_stl(data, source_units)
        else:
            geometry = _parse_ascii_stl(data, source_units)
        geometry, repair_diagnostics = repair_open_mesh(geometry)
        return _result(geometry, "stl", source_units, source_name, diagnostics=_mesh_diagnostics(geometry) + repair_diagnostics)
    if format_name == "step":
        from .step_kernel import (
            StepKernelFailure,
            StepKernelUnavailable,
            requires_kernel,
            step_unit_hint,
            tessellate_step,
        )

        if requires_kernel(data, step_backend):
            step_units = step_unit_hint(data) or "mm"
            try:
                geometry, kernel_diagnostics, display_asset = tessellate_step(
                    data,
                    source_name,
                    step_units,
                    step_asset_dir,
                    timeout=step_timeout,
                )
            except StepKernelUnavailable as exc:
                diagnostic = _diagnostic(
                    "step_kernel_unavailable",
                    "blocker",
                    str(exc),
                    backend=str(step_backend),
                )
                failure_diagnostics = [diagnostic]
                if step_unit_hint(data) is None:
                    failure_diagnostics.append(
                        _diagnostic(
                            "step_units_assumed_mm",
                            "warning",
                            "STEP file declares no length unit; assuming millimetres for kernel tessellation",
                            source_units="mm",
                        )
                    )
                return _result(
                    None,
                    "step",
                    step_units,
                    source_name,
                    diagnostics=tuple(failure_diagnostics),
                    unsupported=True,
                )
            except StepKernelFailure as exc:
                diagnostic = _diagnostic(
                    "step_kernel_failed",
                    "blocker",
                    str(exc),
                    backend=str(step_backend),
                )
                failure_diagnostics = [diagnostic]
                if step_unit_hint(data) is None:
                    failure_diagnostics.append(
                        _diagnostic(
                            "step_units_assumed_mm",
                            "warning",
                            "STEP file declares no length unit; assuming millimetres for kernel tessellation",
                            source_units="mm",
                        )
                    )
                return _result(
                    None,
                    "step",
                    step_units,
                    source_name,
                    diagnostics=tuple(failure_diagnostics),
                    unsupported=True,
                )
            geometry, repair_diagnostics = repair_open_mesh(geometry) if isinstance(geometry, TriangleMesh) else (geometry, ())
            mesh_diagnostics = _mesh_diagnostics(geometry) if isinstance(geometry, TriangleMesh) else ()
            return _result(
                geometry,
                "step",
                step_units,
                source_name,
                diagnostics=tuple(kernel_diagnostics) + mesh_diagnostics + repair_diagnostics,
                display_asset=display_asset,
            )

        geometry, step_units, diagnostics = _parse_step(data)
        unsupported = geometry is None
        geometry, repair_diagnostics = repair_open_mesh(geometry) if isinstance(geometry, TriangleMesh) else (geometry, ())
        mesh_diagnostics = _mesh_diagnostics(geometry) if isinstance(geometry, TriangleMesh) else ()
        return _result(
            geometry,
            "step",
            step_units,
            source_name,
            diagnostics=tuple(diagnostics) + mesh_diagnostics + repair_diagnostics,
            unsupported=unsupported,
        )
    if format_name == "mesh":
        payload = json.loads(data.decode("utf-8"))
        geometry = geometry_from_dict(payload, units=units)
        return _result(geometry, "mesh", source_units, source_name, payload=payload)
    raise ValueError("unsupported geometry format: {!r}".format(format_name))


__all__ = ["ImportDiagnostic", "GeometryLoadResult", "load_geometry", "geometry_from_dict", "repair_open_mesh"]
