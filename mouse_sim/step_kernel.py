"""Normal-Python adapter for the optional FreeCAD/OCCT STEP worker."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .geometry import TriangleMesh, geometry_from_dict


FREECADCMD_ENV = "MOUSE_SIM_FREECADCMD"
DEFAULT_FREECADCMD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
DEFAULT_TIMEOUT = 300.0
DEFAULT_MESH_DEFLECTION_MM = 0.5
DEFAULT_GLB_DEFLECTION_MM = 0.10
DEFAULT_STEP_SCALE = 0.001
BACKEND_NAME = "freecad-occt"
# Format marker embedded in the asset id.  Bumped whenever the worker output
# layout changes so previously cached assets rebuild (parts export added).
ASSET_FORMAT_VERSION = "parts-v6"

# Per-user asset directory; never a shared world-writable path.
_PROCESS_ASSET_DIR = Path(tempfile.gettempdir()) / (
    "mouse-sim-step-assets-{}".format(os.getuid())
)
_STEP_MARKERS = (
    b"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION",
    b"SHAPE_REPRESENTATION_RELATIONSHIP",
    b"BREP_WITH_VOIDS",
)
_STEP_UNIT_NAMES = {
    "METRE": "m",
    "METER": "m",
    "MILLIMETRE": "mm",
    "MILLIMETER": "mm",
    "CENTIMETRE": "cm",
    "CENTIMETER": "cm",
    "MICROMETRE": "um",
    "MICROMETER": "um",
    "KILOMETRE": "km",
    "KILOMETER": "km",
    "INCH": "in",
    "FOOT": "ft",
}
_UNIT_SCALE_TO_M = {
    "m": 1.0,
    "mm": 1e-3,
    "cm": 1e-2,
    "um": 1e-6,
    "km": 1e3,
    "in": 0.0254,
    "ft": 0.3048,
}


class StepKernelUnavailable(RuntimeError):
    """Raised when no usable FreeCADCmd executable is available."""


class StepKernelFailure(RuntimeError):
    """Raised when FreeCADCmd cannot complete a kernel operation."""


def freecadcmd_path():
    """Return the first usable FreeCADCmd path, or ``None``."""
    configured = os.environ.get(FREECADCMD_ENV)
    candidates = [configured, DEFAULT_FREECADCMD, shutil.which("freecadcmd")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(str(path), os.X_OK):
            return path.resolve()
    return None


def kernel_available():
    return freecadcmd_path() is not None


def requires_kernel(data, backend="auto"):
    """Return whether a STEP payload must use the FreeCAD/OCCT backend."""
    mode = str(backend or "auto").strip().lower()
    if mode == "kernel":
        return True
    if mode == "stdlib":
        return False
    if mode != "auto":
        raise ValueError("unsupported STEP backend: {!r}".format(backend))
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif not isinstance(data, bytes):
        data = bytes(data)
    upper = data.upper()
    if any(marker in upper for marker in _STEP_MARKERS):
        return True
    return b"ADVANCED_BREP_SHAPE_REPRESENTATION" in upper and b"MANIFOLD_SOLID_BREP" in upper


def step_unit_hint(data):
    """Return a declared STEP length unit, without guessing from coordinates."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif not isinstance(data, bytes):
        data = bytes(data)
    upper = data.upper()
    patterns = (
        (rb"\.MILLI\.\s*,\s*\.METRE\.", "mm"),
        (rb"\.CENTI\.\s*,\s*\.METRE\.", "cm"),
        (rb"\.MICRO\.\s*,\s*\.METRE\.", "um"),
        (rb"\.KILO\.\s*,\s*\.METRE\.", "km"),
        (rb"\$\s*,\s*\.METRE\.", "m"),
    )
    for pattern, unit in patterns:
        if re.search(rb"SI_UNIT\s*\(\s*" + pattern, upper):
            return unit
    for raw_name in re.findall(rb"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", upper):
        unit = _STEP_UNIT_NAMES.get(raw_name.decode("ascii", errors="ignore").strip())
        if unit is not None:
            return unit
    return None


def detect_step_units(data):
    """Return ``(unit, declared)`` for adapter callers and diagnostics."""
    unit = step_unit_hint(data)
    return unit or "mm", unit is not None


def default_asset_dir():
    """Return the persistent per-user asset directory (private, 0o700)."""
    _PROCESS_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    return _PROCESS_ASSET_DIR


def _secure_asset_dir(root):
    """Ensure the asset directory is private and owned by the current user.

    A shared world-writable path would let other local users read uploaded
    CAD files or substitute cache content, so ownership and mode are enforced.
    """
    try:
        stat_result = root.stat()
    except OSError:
        raise StepKernelFailure("STEP asset directory is not accessible: {}".format(root))
    if stat_result.st_uid != os.geteuid() or not stat_result.st_mode & 0o040000:
        raise StepKernelFailure("STEP asset directory is not owned by this user: {}".format(root))
    try:
        os.chmod(str(root), 0o700)
    except OSError:
        pass
    return root


def _asset_dir(asset_dir):
    root = Path(asset_dir).expanduser() if asset_dir is not None else default_asset_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StepKernelFailure("cannot create STEP asset directory: {}".format(exc))
    root = root.resolve()
    return _secure_asset_dir(root)


def _positive_setting(name, default):
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise StepKernelFailure("{} must be a finite positive number".format(name))
    if not math.isfinite(value) or value <= 0.0:
        raise StepKernelFailure("{} must be a finite positive number".format(name))
    return value


def _source_scale(source_units):
    try:
        return _UNIT_SCALE_TO_M[str(source_units).strip()]
    except KeyError:
        raise StepKernelFailure("unsupported STEP source length unit: {!r}".format(source_units))


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path, max_bytes=1024 * 1024 * 1024):
    """Read JSON with a size guard so hostile outputs cannot exhaust memory."""
    try:
        size = path.stat().st_size
    except OSError:
        raise StepKernelFailure("asset file is missing: {}".format(path))
    if size > max_bytes:
        raise StepKernelFailure("asset file exceeds size cap: {} ({} bytes)".format(path, size))
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _worker_script_hash():
    """Hash of the worker script; asset ids change when the worker changes."""
    try:
        path = os.path.join(os.path.dirname(__file__), "freecad_step_worker.py")
        with open(path, "rb") as stream:
            return hashlib.sha256(stream.read()).hexdigest()[:16]
    except OSError:
        return "unknown"


def _settings(source_units):
    return {
        "backend": BACKEND_NAME,
        "source_units": source_units,
        # FreeCAD/OCCT store STEP geometry in internal millimetres, so the
        # scale to metres is always 0.001 regardless of the declared units.
        "scale_to_m": 0.001,
        "worker_script_sha256": _worker_script_hash(),
        "mesh_deflection_mm": _positive_setting(
            "MOUSE_SIM_STEP_MESH_DEFLECTION_MM", DEFAULT_MESH_DEFLECTION_MM
        ),
        "glb_deflection_mm": _positive_setting(
            "MOUSE_SIM_STEP_GLB_DEFLECTION_MM", DEFAULT_GLB_DEFLECTION_MM
        ),
    }


def _asset_id(source_sha256, settings):
    payload = {
        "format_version": ASSET_FORMAT_VERSION,
        "source_sha256": source_sha256,
        "settings": settings,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _diagnostics(metadata, assumed_units, cached):
    from .importers import ImportDiagnostic

    values = []
    if assumed_units:
        values.append(
            ImportDiagnostic(
                "step_units_assumed_mm",
                "warning",
                "STEP file declares no length unit; assuming millimetres for kernel tessellation",
                (("source_units", "mm"),),
            )
        )
    details = {
        "backend": BACKEND_NAME,
        "mesh_deflection_mm": str(metadata.get("mesh_deflection_mm", "")),
        "glb_deflection_mm": str(metadata.get("glb_deflection_mm", "")),
        "object_count": str(metadata.get("object_count", 0)),
        "triangle_count": str(metadata.get("triangle_count", 0)),
        "cached": "true" if cached else "false",
    }
    values.append(
        ImportDiagnostic(
            "step_kernel_tessellated",
            "info",
            "FreeCAD/OCCT produced a tessellated display mesh; it is not CAD-exact geometry",
            tuple((key, str(details[key])) for key in sorted(details)),
        )
    )
    return tuple(values)


def _cached_result(asset_id, root, settings, source_sha256, assumed_units, cached=True):
    mesh_path = root / (asset_id + ".mesh.json")
    glb_path = root / (asset_id + ".glb")
    parts_path = root / (asset_id + ".parts.json")
    manifest_path = root / (asset_id + ".manifest.json")
    try:
        complete = (
            mesh_path.is_file()
            and glb_path.is_file()
            and parts_path.is_file()
            and glb_path.stat().st_size > 0
        )
    except OSError:
        complete = False
    if not complete:
        return None
    try:
        payload = _read_json(mesh_path)
        if not isinstance(payload, dict):
            raise ValueError("worker mesh payload is not an object")
        geometry_payload = payload.get("geometry")
        geometry = geometry_from_dict(geometry_payload, units="m")
        if not isinstance(geometry, TriangleMesh):
            raise ValueError("worker geometry is not a mesh")
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
        if isinstance(manifest, dict):
            manifest_mesh = manifest.get("mesh")
            if isinstance(manifest_mesh, dict):
                for key, value in manifest_mesh.items():
                    metadata.setdefault(key, value)
            manifest_glb = manifest.get("glb")
            if isinstance(manifest_glb, dict):
                metadata.setdefault("glb_deflection_mm", manifest_glb.get("deflection_mm"))
        metadata.setdefault("mesh_deflection_mm", settings["mesh_deflection_mm"])
        metadata.setdefault("glb_deflection_mm", settings["glb_deflection_mm"])
        metadata.setdefault("object_count", 0)
        metadata.setdefault("triangle_count", len(geometry.triangles))
        # parts.json is the canonical per-part source; validate it so a torn
        # or hostile file is treated as a cache miss instead of being served.
        parts_payload = _read_json(parts_path) if parts_path.is_file() else None
        if not isinstance(parts_payload, dict) or not isinstance(parts_payload.get("parts"), list):
            return None
        raw_parts = parts_payload["parts"]
        parts = []
        for entry in raw_parts:
            if not (isinstance(entry, dict) and str(entry.get("id", ""))):
                return None
            part = {"id": str(entry["id"]), "name": entry.get("name")}
            color = entry.get("color")
            if (
                isinstance(color, (list, tuple))
                and len(color) == 3
                and all(isinstance(c, (int, float)) and math.isfinite(float(c)) for c in color)
            ):
                part["color"] = [float(c) for c in color]
            parts.append(part)
        parts_available = True
        asset = {
            "asset_id": asset_id,
            "path": str(glb_path.resolve()),
            "format": "glb",
            "sha256": _sha256_file(glb_path),
            "source_sha256": source_sha256,
            "bytes": glb_path.stat().st_size,
            "object_count": int(metadata.get("object_count", 0)),
            "triangle_count": int(metadata.get("triangle_count", len(geometry.triangles))),
            "backend": BACKEND_NAME,
            "tessellation_deflection_mm": float(metadata["glb_deflection_mm"]),
            "parts": parts,
            "parts_path": str(parts_path.resolve()) if parts_available else None,
        }
        return geometry, _diagnostics(metadata, assumed_units, cached=cached), asset
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, StepKernelFailure):
        # A corrupt or oversized cached asset is a cache miss: rebuild it.
        return None


def _failure_output(completed):
    stderr = completed.stderr.decode("utf-8", errors="replace").strip() if completed.stderr else ""
    stdout = completed.stdout.decode("utf-8", errors="replace").strip() if completed.stdout else ""
    detail = stderr or stdout or "no diagnostic output"
    return detail[-2000:]


def tessellate_step(data, source_name, source_units, asset_dir, timeout=DEFAULT_TIMEOUT):
    """Run the FreeCAD worker and return ``(mesh, diagnostics, asset)``."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif not isinstance(data, bytes):
        data = bytes(data)
    detected_units = step_unit_hint(data)
    effective_units = str(detected_units or source_units or "mm").strip().lower()
    assumed_units = detected_units is None
    settings = _settings(effective_units)
    source_sha256 = _sha256_bytes(data)
    asset_id = _asset_id(source_sha256, settings)
    root = _asset_dir(asset_dir)
    mesh_path = root / (asset_id + ".mesh.json")
    glb_path = root / (asset_id + ".glb")
    parts_path = root / (asset_id + ".parts.json")
    input_path = root / (asset_id + ".stp")
    cached = _cached_result(asset_id, root, settings, source_sha256, assumed_units)
    if cached is not None:
        return cached
    command_path = freecadcmd_path()
    if command_path is None:
        raise StepKernelUnavailable(
            "FreeCADCmd is unavailable; set {} or install FreeCAD at {}".format(
                FREECADCMD_ENV, DEFAULT_FREECADCMD
            )
        )
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise StepKernelFailure("STEP kernel timeout must be a finite positive number")
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise StepKernelFailure("STEP kernel timeout must be a finite positive number")

    try:
        # Write the STEP input privately and atomically; never follow a
        # pre-planted symlink in the asset directory.
        temp_input = input_path.with_suffix(".stp.tmp")
        descriptor = os.open(
            str(temp_input), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        except OSError:
            try:
                os.remove(temp_input)
            except OSError:
                pass
            raise
        os.replace(temp_input, input_path)
    except OSError as exc:
        raise StepKernelFailure("cannot write temporary STEP input: {}".format(exc))

    worker_script = Path(__file__).with_name("freecad_step_worker.py").resolve()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
        "MOUSE_SIM_STEP_INPUT": str(input_path),
        "MOUSE_SIM_STEP_MESH_OUTPUT": str(mesh_path),
        "MOUSE_SIM_STEP_GLB_OUTPUT": str(glb_path),
        "MOUSE_SIM_STEP_PARTS_OUTPUT": str(parts_path),
        "MOUSE_SIM_STEP_MESH_DEFLECTION_MM": str(settings["mesh_deflection_mm"]),
        "MOUSE_SIM_STEP_GLB_DEFLECTION_MM": str(settings["glb_deflection_mm"]),
        "MOUSE_SIM_STEP_SCALE": str(settings["scale_to_m"]),
        "MOUSE_SIM_STEP_SOURCE_UNITS": str(effective_units),
        "MOUSE_SIM_STEP_SOURCE_SHA256": source_sha256,
    }
    expression = "exec(open({}).read())".format(repr(str(worker_script)))
    command = [str(command_path), "-c", expression]

    def _limit_worker():
        # macOS rejects lowering RLIMIT_AS from an inherited unlimited limit,
        # so CPU/NOFILE are set first, each guarded separately; failures are
        # reported instead of silently dropping every limit.
        try:
            import resource

            try:
                resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
            except Exception as exc:
                sys.stderr.write("mouse_sim step worker: RLIMIT_CPU failed: {}\n".format(exc))
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
            except Exception as exc:
                sys.stderr.write("mouse_sim step worker: RLIMIT_NOFILE failed: {}\n".format(exc))
            try:
                resource.setrlimit(
                    resource.RLIMIT_AS, (6 * 1024 * 1024 * 1024, 6 * 1024 * 1024 * 1024)
                )
            except Exception as exc:
                sys.stderr.write("mouse_sim step worker: RLIMIT_AS failed: {}\n".format(exc))
        except Exception as exc:
            sys.stderr.write("mouse_sim step worker: resource module unavailable: {}\n".format(exc))

    try:
        completed = subprocess.run(
            command,
            env=env,
            preexec_fn=_limit_worker,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise StepKernelFailure("FreeCAD STEP tessellation timed out after {} seconds".format(timeout))
    except OSError as exc:
        raise StepKernelUnavailable("FreeCADCmd could not be started: {}".format(exc))
    if completed.returncode != 0:
        raise StepKernelFailure(
            "FreeCAD STEP worker exited with status {}: {}".format(
                completed.returncode, _failure_output(completed)
            )
        )
    if not (mesh_path.is_file() and parts_path.is_file() and glb_path.is_file() and glb_path.stat().st_size > 0):
        raise StepKernelFailure("FreeCAD STEP worker completed without mesh, parts, and GLB outputs")
    result = _cached_result(asset_id, root, settings, source_sha256, assumed_units, cached=False)
    if result is None:
        raise StepKernelFailure("FreeCAD STEP worker produced invalid mesh or GLB metadata")
    return result


__all__ = [
    "BACKEND_NAME",
    "DEFAULT_TIMEOUT",
    "ASSET_FORMAT_VERSION",
    "StepKernelUnavailable",
    "StepKernelFailure",
    "freecadcmd_path",
    "kernel_available",
    "requires_kernel",
    "step_unit_hint",
    "detect_step_units",
    "default_asset_dir",
    "tessellate_step",
]
