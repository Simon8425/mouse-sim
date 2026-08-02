"""Standard-library geometry importers and provenance-bearing load results."""

from dataclasses import dataclass
import json
from pathlib import Path
import struct
from typing import Any, Mapping, Optional, Tuple

from .errors import UnitError
from .geometry import TriangleMesh, geometry_from_dict
from .units import normalize_unit, unit_dimension


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


def _result(geometry, format_name, source_units, source_name, payload=None, diagnostics=(), unsupported=False):
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
            # while retaining deterministic first-seen ordering.
            for point in current:
                try:
                    index = vertices.index(point)
                except ValueError:
                    index = len(vertices)
                    vertices.append(point)
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
    for index in range(count):
        offset = 84 + index * 50
        values = struct.unpack_from("<12f", data, offset)
        triangle = []
        for point in (values[3:6], values[6:9], values[9:12]):
            point = tuple(float(item) for item in point)
            try:
                vertex_index = vertices.index(point)
            except ValueError:
                vertex_index = len(vertices)
                vertices.append(point)
            triangle.append(vertex_index)
        triangles.append(tuple(triangle))
    return TriangleMesh(vertices, triangles, units=units)


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


def load_geometry(path_or_bytes, fmt="auto", units=None):
    """Load analytic JSON, OBJ, or ASCII/binary STL geometry.

    OBJ and STL have no portable unit declaration, so ``units`` is mandatory
    for those formats.  STEP is intentionally rejected with a structured
    diagnostic and ``geometry=None``; this function never substitutes a mesh
    or bounding box for unsupported CAD geometry.
    """

    data, source_name = _source_bytes(path_or_bytes)
    format_name = _format_name(fmt)
    if format_name == "auto":
        format_name = _detect_format(data, source_name)
    aliases = {"stp": "step", "stl-ascii": "stl", "stl-binary": "stl", "trianglemesh": "mesh"}
    format_name = aliases.get(format_name, format_name)
    if format_name in ("step", "cad"):
        diagnostic = _diagnostic(
            "unsupported_format",
            "blocker",
            "STEP geometry is unsupported by the standard-library importer; no geometry was fabricated",
            format=format_name,
        )
        return _result(None, "step", units, source_name, diagnostics=(diagnostic,), unsupported=True)
    if format_name in ("obj", "stl"):
        source_units = _require_import_units(units, format_name)
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
        return _result(geometry, "obj", source_units, source_name, diagnostics=tuple(diagnostics) + _mesh_diagnostics(geometry))
    if format_name == "stl":
        if _looks_binary_stl(data):
            geometry = _parse_binary_stl(data, source_units)
        else:
            geometry = _parse_ascii_stl(data, source_units)
        return _result(geometry, "stl", source_units, source_name, diagnostics=_mesh_diagnostics(geometry))
    if format_name == "mesh":
        payload = json.loads(data.decode("utf-8"))
        geometry = geometry_from_dict(payload, units=units)
        return _result(geometry, "mesh", source_units, source_name, payload=payload)
    raise ValueError("unsupported geometry format: {!r}".format(format_name))


__all__ = ["ImportDiagnostic", "GeometryLoadResult", "load_geometry", "geometry_from_dict"]
