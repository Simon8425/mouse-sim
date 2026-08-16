"""HTTP web API adapter for the mouse simulation package.

The adapter exposes the deterministic analysis engine over HTTP: health,
baseline project, material catalog, geometry normalization, and analysis
endpoints.  All responses are JSON; failures use the ``gms.web-error/1``
envelope and pipeline failures are surfaced as web errors instead of a
successful ``200`` pass-through.
"""

import json
import math
import os
import re
import sys
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .canonical import canonical_json
from .errors import UnitError

API_VERSION = "1"

WEB_ERROR_SCHEMA_ID = "gms.web-error/1"
WEB_HEALTH_SCHEMA_ID = "gms.web-health/1"
WEB_BASELINE_SCHEMA_ID = "gms.web-baseline/1"
WEB_MATERIAL_CATALOG_SCHEMA_ID = "gms.web-material-catalog/1"
GEOMETRY_PREVIEW_SCHEMA_ID = "gms.geometry-preview/1"
WEB_ANALYSIS_REQUEST_SCHEMA_ID = "gms.web-analysis-request/1"
WEB_ANALYSIS_RESPONSE_SCHEMA_ID = "gms.web-analysis-response/1"

BASELINE_SOURCE = "examples/mouse_baseline.json"

# Normalized STEP meshes expand substantially when represented as JSON. Keep
# this larger than the raw geometry limit so a validated upload can be sent
# through the analysis endpoint without an avoidable 413 response.
DEFAULT_MAX_JSON_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_GEOMETRY_BYTES = 64 * 1024 * 1024

_ACCEPTED_FORMATS = frozenset(
    ("auto", "json", "obj", "stl", "ascii", "step", "stp", "stl-ascii", "stl-binary")
)
_FORMAT_ALIASES = {"ascii": "stl", "stp": "step", "stl-ascii": "stl", "stl-binary": "stl"}
_GEOMETRY_MEDIA_TYPES = frozenset(
    ("", "application/octet-stream", "application/json", "text/plain")
)
_ANALYZE_MEDIA_TYPES = frozenset(("", "application/json"))

_ENVELOPE_KEYS = frozenset(("schema_id", "request", "options"))
_OPTION_KEYS = frozenset(("strict", "use_cache"))
_ALLOWED_REQUEST_SCHEMA_IDS = (None, "", "gms.project/1")

# Geometry normalization is CPU and memory heavy (large STEP models parse for
# tens of seconds).  Serialize normalize requests so duplicate or concurrent
# uploads cannot stack several full parses in memory at once.  The lock is
# scoped to the STEP kernel path only (see handle_normalize).
# Removed _NORMALIZE_LOCK for parser concurrency
# Analysis is also memory heavy; bounded concurrency prevents several large
# pipelines from running at once.
_ANALYZE_SEMAPHORE = threading.BoundedSemaphore(2)
_NORMALIZE_SEMAPHORE = threading.BoundedSemaphore(4)
_STEP_ASSET_REGISTRY = {}
_STEP_ASSET_PARTS_REGISTRY = {}
_STEP_ASSET_REGISTRY_LOCK = threading.Lock()
_STEP_ASSET_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_STEP_ASSET_ROUTE_RE = re.compile(r"^/api/geometry/assets/([0-9a-f]{64})\.glb$")
_STEP_ASSET_PARTS_ROUTE_RE = re.compile(r"^/api/geometry/assets/([0-9a-f]{64})\.parts\.json$")

# AI classification job registry (see ai_classify_jobs below).
_CLASSIFY_JOBS = {}
_CLASSIFY_JOBS_LOCK = threading.Lock()
_CLASSIFY_MAX_JOBS = 64
_CLASSIFY_JOB_ID_RE = re.compile(r"^cj-[0-9a-f]{16}$")
# Bound the in-memory asset registry; the files remain on disk and re-upload
# re-registers.  Eviction only drops the registry entry.
_STEP_ASSET_REGISTRY_CAP = 256

# Background geometry warm-up: the first analyze of a freshly-uploaded model
# spends most of its time certifying the geometry (parse + weld + exact
# self-intersection sweep, ~30 s on a 46-part STEP assembly).  When an asset
# is registered we kick off a SILENT daemon thread that pre-runs that
# certification through the shared ``importers.parse_and_repair_geometry``
# cache, so a later Run reuses the cached (certified) meshes and the request
# completes in seconds.  No response payload or log changes: the warm-up
# produces nothing observable.  If the user presses Run before the warm-up
# finishes, the foreground pipeline simply computes normally (the request
# waits, as before) and both paths share the same deterministic cache.
_WARMUP_REGISTRY = {}
_WARMUP_LOCK = threading.Lock()


def _warmup_asset_geometry(asset_id, parts_path):
    """Certify an asset's part geometry into the shared parse cache.

    Runs in a daemon thread; never raises into the request path.  Only the
    deterministic parse/repair/diagnostics work is performed — the result is
    purely the shared geometry cache warming, so a later analyze request
    reuses it transparently.
    """
    try:
        from .importers import parse_and_repair_geometry

        with parts_path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        raw_parts = payload.get("parts") if isinstance(payload, dict) else None
        if not isinstance(raw_parts, list):
            return
        for part in raw_parts:
            if not isinstance(part, dict):
                continue
            geometry = part.get("geometry")
            if isinstance(geometry, dict):
                parse_and_repair_geometry(geometry, units=geometry.get("units", "m"))
    except Exception:
        # Warm-up is best-effort: a failure simply leaves the cache cold and
        # the next analyze request computes normally (silent, no user impact).
        return


def _request_geometry_warmup(asset_id, parts_path):
    """Start (or join) the background certification for one asset.

    Deduplicated per asset_id: repeated registrations of the same model do
    not stack warm-up threads.  Returns without waiting — the caller never
    blocks on the warm-up.
    """
    with _WARMUP_LOCK:
        if _WARMUP_REGISTRY.get(asset_id) is True:
            return
        _WARMUP_REGISTRY[asset_id] = True
        # Bound the warm-up bookkeeping to the asset registry cap; evicted
        # ids simply re-warm on the next registration.
        while len(_WARMUP_REGISTRY) > _STEP_ASSET_REGISTRY_CAP:
            _WARMUP_REGISTRY.pop(next(iter(_WARMUP_REGISTRY)), None)
    thread = threading.Thread(
        target=_warmup_asset_geometry,
        args=(asset_id, parts_path),
        name="geometry-warmup-{}".format(asset_id[:8]),
        daemon=True,
    )
    thread.start()

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".txt": "text/plain",
}


def _env_limit(name, default):
    """Read a positive integer limit from the environment, falling back to default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class WebConfig:
    """Immutable configuration for the web API server."""

    host: str = "127.0.0.1"
    port: int = 8000
    web_dist: Optional[Path] = None
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    cache_dir: Optional[Path] = None
    cors_origins: Tuple[str, ...] = ()
    max_json_bytes: int = field(
        default_factory=lambda: _env_limit("MOUSE_SIM_MAX_JSON_BYTES", DEFAULT_MAX_JSON_BYTES)
    )
    max_geometry_bytes: int = field(
        default_factory=lambda: _env_limit(
            "MOUSE_SIM_MAX_GEOMETRY_BYTES", DEFAULT_MAX_GEOMETRY_BYTES
        )
    )
    log_requests: bool = True


def sanitize_display_name(name, max_length=120):
    """Return a display-only basename with control characters stripped."""
    if name is None:
        return None
    text = str(name)
    basename = text.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in basename if ord(char) >= 32 and char != "\x7f")
    return cleaned[:max_length]


def register_step_asset(asset):
    """Register a generated GLB (and optional per-part JSON) and return its
    public, path-free metadata.

    The parts JSON is served as ``/api/geometry/assets/<id>.parts.json`` only
    when the asset carries a validated ``parts_path``.  ``parts`` lists
    ``{"id", "name"}`` summaries; full part geometry stays on disk.
    """
    if not isinstance(asset, dict):
        return None
    asset_id = str(asset.get("asset_id", ""))
    if not _STEP_ASSET_ID_RE.fullmatch(asset_id):
        return None
    raw_path = asset.get("path")
    if not raw_path:
        return None
    try:
        path = Path(raw_path).expanduser().resolve()
        if path.name != asset_id + ".glb" or path.suffix.lower() != ".glb" or not path.is_file():
            return None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    parts_path = None
    raw_parts_path = asset.get("parts_path")
    if raw_parts_path:
        try:
            candidate = Path(raw_parts_path).expanduser().resolve()
            if (
                candidate.name == asset_id + ".parts.json"
                and candidate.suffix.lower() == ".json"
                and candidate.is_file()
            ):
                parts_path = candidate
        except (OSError, RuntimeError, TypeError, ValueError):
            parts_path = None
    with _STEP_ASSET_REGISTRY_LOCK:
        # Re-registering refreshes the LRU position.
        _STEP_ASSET_REGISTRY.pop(asset_id, None)
        _STEP_ASSET_PARTS_REGISTRY.pop(asset_id, None)
        _STEP_ASSET_REGISTRY[asset_id] = path
        if parts_path is not None:
            _STEP_ASSET_PARTS_REGISTRY[asset_id] = parts_path
        # Bound the registry: evict the oldest entry (files stay on disk).
        while len(_STEP_ASSET_REGISTRY) > _STEP_ASSET_REGISTRY_CAP:
            oldest = next(iter(_STEP_ASSET_REGISTRY))
            _STEP_ASSET_REGISTRY.pop(oldest, None)
            _STEP_ASSET_PARTS_REGISTRY.pop(oldest, None)
    if parts_path is not None:
        # Silent background certification: pre-parse + pre-certify the part
        # geometry so the first analyze of this model is fast.  Never blocks
        # the upload response and never notifies the user.
        _request_geometry_warmup(asset_id, parts_path)
    public = {
        "asset_id": asset_id,
        "url": "/api/geometry/assets/{}.glb".format(asset_id),
        "format": "glb",
    }
    for key in (
        "sha256",
        "bytes",
        "object_count",
        "triangle_count",
        "backend",
        "tessellation_deflection_mm",
    ):
        if key in asset:
            public[key] = asset[key]
    if parts_path is not None:
        parts = asset.get("parts")
        if isinstance(parts, list):
            summaries = []
            for entry in parts:
                if not (isinstance(entry, dict) and str(entry.get("id", ""))):
                    continue
                summary = {"id": str(entry["id"]), "name": entry.get("name")}
                color = entry.get("color")
                if (
                    isinstance(color, (list, tuple))
                    and len(color) == 3
                    and all(isinstance(c, (int, float)) and math.isfinite(float(c)) for c in color)
                ):
                    summary["color"] = [float(c) for c in color]
                summaries.append(summary)
            public["parts"] = summaries
        public["parts_url"] = "/api/geometry/assets/{}.parts.json".format(asset_id)
    return public


def _load_registered_asset_objects(asset_id):
    """Load normalized STEP part geometry for a server-side analysis reference."""
    with _STEP_ASSET_REGISTRY_LOCK:
        parts_path = _STEP_ASSET_PARTS_REGISTRY.get(asset_id)
    if parts_path is None or not parts_path.is_file():
        candidates = [
            Path.cwd() / ".web-cache" / "step-assets" / "{}.parts.json".format(asset_id),
            Path.cwd() / ".web-cache" / "{}.parts.json".format(asset_id),
        ]
        for cand in candidates:
            if cand.is_file():
                parts_path = cand
                with _STEP_ASSET_REGISTRY_LOCK:
                    _STEP_ASSET_PARTS_REGISTRY[asset_id] = cand
                break
    if parts_path is None:
        return None
    try:
        with parts_path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    raw_parts = payload.get("parts") if isinstance(payload, dict) else None
    if not isinstance(raw_parts, list):
        return None
    objects = []
    for part in raw_parts:
        if not isinstance(part, dict):
            continue
        object_id = str(part.get("id", "")).strip()
        geometry = part.get("geometry")
        name = part.get("name")
        if object_id and isinstance(geometry, dict):
            objects.append({"id": object_id, "name": name, "geometry": geometry})
    return objects or None


_CLASSIFY_SCHEMA_ID = "gms.ai-classify-request/1"


def _classify_job_snapshot(job_id):
    with _CLASSIFY_JOBS_LOCK:
        job = _CLASSIFY_JOBS.get(job_id)
        if job is None:
            return None
        return dict(job)


def _classify_job_worker(job_id, asset_id, part_ids=None, api_key=None, model=None, provider=None, endpoint=None):
    from . import ai_classify
    try:
        asset_objects = _load_registered_asset_objects(asset_id)
        if asset_objects is None:
            raise ValueError("Asset geometry is unavailable on disk")
        if part_ids:
            by_id = {str(item["id"]): item for item in asset_objects}
            selected = []
            for part_id in part_ids:
                item = by_id.get(str(part_id))
                if item is not None:
                    selected.append(item)
        else:
            selected = asset_objects
        cache = ai_classify.ClassificationCache()
        parts_payload = []
        for item in selected:
            parts_payload.append(
                {
                    "object_id": str(item.get("id")),
                    "name": item.get("name"),
                    "geometry": item.get("geometry") or {},
                    "rule": {"component_type": "unresolved", "confidence": 0.0},
                }
            )
        total = len(parts_payload)
        with _CLASSIFY_JOBS_LOCK:
            job = _CLASSIFY_JOBS.get(job_id)
            if job is not None:
                job["total"] = total
                job["done"] = 0
                job["status"] = "running"

        def _on_progress(done_count, total_count):
            with _CLASSIFY_JOBS_LOCK:
                j = _CLASSIFY_JOBS.get(job_id)
                if j is not None:
                    j["done"] = done_count
                    j["total"] = total_count

        results = ai_classify.classify_parts(
            parts_payload,
            use_cache=True,
            cache=cache,
            api_key_value=api_key,
            model=model,
            provider=provider,
            endpoint=endpoint,
            on_progress=_on_progress,
        )
        with _CLASSIFY_JOBS_LOCK:
            job = _CLASSIFY_JOBS.get(job_id)
            if job is None:
                return
            job["done"] = len(results)
            job["status"] = "done"
            job["results"] = results
    except Exception as exc:  # noqa: BLE001 - report job failure to the client
        with _CLASSIFY_JOBS_LOCK:
            job = _CLASSIFY_JOBS.get(job_id)
            if job is None:
                return
            job["status"] = "error"
            job["error"] = str(exc)


def handle_classify_start(config, payload):
    """Start an AI classification job for a registered STEP asset."""
    if not isinstance(payload, dict):
        return make_web_error(422, "E_INVALID_ENVELOPE", "classify payload must be an object")
    asset_id = payload.get("asset_id")
    if not isinstance(asset_id, str) or not _STEP_ASSET_ID_RE.fullmatch(asset_id):
        return make_web_error(422, "E_INVALID_ASSET", "asset_id is not a registered STEP asset")
    with _STEP_ASSET_REGISTRY_LOCK:
        parts_path = _STEP_ASSET_PARTS_REGISTRY.get(asset_id)
    if parts_path is None:
        return make_web_error(422, "E_ASSET_NOT_FOUND", "registered STEP geometry is unavailable")
    raw_ids = payload.get("part_ids")
    part_ids = None
    if raw_ids is not None:
        if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
            return make_web_error(422, "E_INVALID_PART_IDS", "part_ids must be a list of strings")
        part_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    
    api_key_val = str(payload.get("api_key")).strip() if payload.get("api_key") else None
    model_val = str(payload.get("model")).strip() if payload.get("model") else None
    provider_val = str(payload.get("provider")).strip() if payload.get("provider") else None
    endpoint_val = str(payload.get("endpoint")).strip() if payload.get("endpoint") else None

    job_id = "cj-" + os.urandom(8).hex()
    with _CLASSIFY_JOBS_LOCK:
        while len(_CLASSIFY_JOBS) >= _CLASSIFY_MAX_JOBS:
            oldest = next(iter(_CLASSIFY_JOBS))
            _CLASSIFY_JOBS.pop(oldest, None)
        _CLASSIFY_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "asset_id": asset_id,
            "total": 0,
            "done": 0,
            "results": [],
            "error": None,
            "created": time.time(),
        }
    thread = threading.Thread(
        target=_classify_job_worker,
        args=(job_id, asset_id, part_ids, api_key_val, model_val, provider_val, endpoint_val),
        name="ai-classify-{}".format(job_id),
        daemon=True,
    )
    thread.start()
    return (202, {"schema_id": _CLASSIFY_SCHEMA_ID, "job_id": job_id, "status": "queued"})


def handle_classify_status(job_id):
    if not _CLASSIFY_JOB_ID_RE.fullmatch(str(job_id or "")):
        return make_web_error(404, "E_JOB_NOT_FOUND", "classify job not found")
    snapshot = _classify_job_snapshot(str(job_id))
    if snapshot is None:
        return make_web_error(404, "E_JOB_NOT_FOUND", "classify job not found")
    body = {
        "schema_id": _CLASSIFY_SCHEMA_ID,
        "job_id": snapshot["job_id"],
        "status": snapshot.get("status", "queued"),
        "total": snapshot.get("total", 0),
        "done": snapshot.get("done", 0),
        "results": snapshot.get("results", []),
        "error": snapshot.get("error"),
    }
    return (200, body)


def _quantity_si(properties, field_name):
    quantity = getattr(properties, field_name, None) if properties is not None else None
    return quantity.value_si if quantity is not None else None


def material_catalog_projection(catalog):
    """Project a material catalog into compact web material entries."""
    entries = []
    for key in sorted(catalog.keys(), key=lambda key: str(key).casefold()):
        definition = catalog[key]
        properties = getattr(definition, "properties", None)
        provenance = getattr(definition, "provenance", None)
        approval = getattr(definition, "approval_state", None)
        entries.append(
            {
                "key": str(key),
                "name": getattr(definition, "name", None),
                "family": getattr(definition, "family", None) or None,
                "density_kg_m3": _quantity_si(properties, "density"),
                "young_modulus_pa": _quantity_si(properties, "young_modulus"),
                "approval_state": (
                    str(getattr(approval, "value", approval)) if approval is not None else None
                ),
                "confidence": getattr(provenance, "confidence", None),
                "source_type": getattr(provenance, "source_type", None),
            }
        )
    return entries


def make_web_error(status, code, message, severity="error", details=None):
    """Build an ``(http_status, gms.web-error/1 envelope)`` pair."""
    error = {"code": code, "severity": severity, "phase": "web", "message": message}
    if details is not None:
        error["details"] = details
    status = int(status)
    return (status, {"schema_id": WEB_ERROR_SCHEMA_ID, "status": status, "error": error})


class WebRequestError(Exception):
    """An HTTP-level error raised while handling a web request."""

    def __init__(self, status, code, message, severity="error", details=None):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message
        self.severity = severity
        self.details = details

    def envelope(self):
        """Return the ``gms.web-error/1`` envelope for this error."""
        return make_web_error(
            self.status, self.code, self.message, severity=self.severity, details=self.details
        )[1]


class _NonFiniteError(ValueError):
    """Raised by the JSON parse_constant hook for NaN/Infinity/-Infinity."""


def _reject_non_finite(value):
    raise _NonFiniteError("non-finite JSON constant {!r} is not allowed".format(value))


def parse_json_object(data):
    """Parse UTF-8 JSON bytes into an object, rejecting non-finite numbers."""
    try:
        payload = json.loads(data.decode("utf-8"), parse_constant=_reject_non_finite)
    except _NonFiniteError as exc:
        raise WebRequestError(422, "E_NON_FINITE", str(exc))
    except (UnicodeDecodeError, ValueError):
        raise WebRequestError(400, "E_PARSE", "malformed JSON")
    if not isinstance(payload, dict):
        raise WebRequestError(400, "E_INVALID_ENVELOPE", "request body must be a JSON object")
    return payload


def handle_health(config):
    """Return the health envelope for the web API."""
    from .pipeline import ENGINE_VERSION
    from .physics import SOLVER_CAPABILITIES
    from .step_kernel import kernel_available

    try:
        step_kernel_available = bool(kernel_available())
    except Exception:
        # The optional FreeCAD/OCCT kernel is not required for a healthy
        # service: report availability instead of failing the whole probe.
        step_kernel_available = False
    return (
        200,
        {
            "schema_id": WEB_HEALTH_SCHEMA_ID,
            "engine_version": ENGINE_VERSION,
            "api_version": API_VERSION,
            "supported_formats": ["json", "obj", "stl", "step"],
            "solver_capabilities": list(SOLVER_CAPABILITIES.to_dict()["capability_keys"]),
            "cache_active": config.cache_dir is not None,
            "max_json_bytes": config.max_json_bytes,
            "max_geometry_bytes": config.max_geometry_bytes,
            "deterministic": True,
            "step_backend": "auto",
            "step_kernel_backend": "freecad-occt",
            "step_kernel_available": step_kernel_available,
            "advanced_step_backend": "kernel",
            "advanced_step_uses_kernel": True,
        },
    )


_BUILTIN_BASELINE_PROJECT = {
    "schema_id": "gms.project/1",
    "mode": "exploration",
    "units": "mm",
    "objects": [
        {"id": "shell_top", "geometry": {"type": "box", "size": [110, 65, 2.5]}, "material": "ABS", "structural_behavior": "shell", "classification": {"component_type": "shell_top", "source": "cad", "confidence": "high"}},
        {"id": "shell_bottom", "geometry": {"type": "box", "size": [110, 65, 1.2]}, "material": "PC/ABS", "structural_behavior": "shell", "classification": {"component_type": "shell_bottom", "source": "cad", "confidence": "high"}},
        {"id": "pcb", "geometry": {"type": "box", "size": [60, 40, 1.6]}, "material": "FR4", "structural_behavior": "rigid", "classification": {"component_type": "pcb", "source": "cad", "confidence": "high"}},
        {"id": "battery", "geometry": {"type": "box", "size": [50, 30, 8]}, "material": "LiPo", "structural_behavior": "rigid", "classification": {"component_type": "battery", "source": "supplier", "confidence": "medium"}},
        {"id": "wheel", "geometry": {"type": "cylinder", "radius": 6, "height": 20}, "material": "POM", "structural_behavior": "rigid", "classification": {"component_type": "wheel", "source": "cad", "confidence": "high"}},
        {"id": "skate_left", "geometry": {"type": "box", "size": [12, 6, 1]}, "material": "PTFE", "structural_behavior": "rigid", "classification": {"component_type": "skate", "source": "supplier", "confidence": "medium"}},
        {"id": "skate_right", "geometry": {"type": "box", "size": [12, 6, 1]}, "material": "PTFE", "structural_behavior": "rigid", "classification": {"component_type": "skate", "source": "supplier", "confidence": "medium"}},
        {"id": "screw_front", "geometry": {"type": "cylinder", "radius": 1.5, "height": 6}, "material": "steel", "mass_override": 0.0002, "structural_behavior": "rigid", "classification": {"component_type": "screw", "source": "supplier", "confidence": "medium"}},
        {"id": "screw_rear", "geometry": {"type": "cylinder", "radius": 1.5, "height": 6}, "material": "steel", "mass_override": 0.0002, "structural_behavior": "rigid", "classification": {"component_type": "screw", "source": "supplier", "confidence": "medium"}}
    ]
}


def handle_baseline(config):
    """Return the baseline project document from the configured project root."""
    root = getattr(config, "project_root", None)
    if root is not None:
        path = Path(root) / "examples" / "mouse_baseline.json"
    else:
        path = Path.cwd() / "examples" / "mouse_baseline.json"
        if not path.exists():
            path = Path(__file__).resolve().parent.parent / "examples" / "mouse_baseline.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            project = json.load(stream)
    except (OSError, ValueError):
        return make_web_error(404, "E_NOT_FOUND", "baseline project is unavailable")
    return (
        200,
        {"schema_id": WEB_BASELINE_SCHEMA_ID, "source": BASELINE_SOURCE, "project": project},
    )


def handle_materials(config):
    """Return the compact built-in material catalog projection."""
    from .materials import builtin_materials

    return (
        200,
        {
            "schema_id": WEB_MATERIAL_CATALOG_SCHEMA_ID,
            "catalog_source": "builtin",
            "materials": material_catalog_projection(builtin_materials()),
        },
    )


def _normalized_format(raw):
    """Normalize a format query value or return None when unsupported."""
    if raw is None:
        raw = "auto"
    value = str(raw).strip().lower()
    value = _FORMAT_ALIASES.get(value, value)
    if value in ("auto", "json", "obj", "stl", "step"):
        return value
    return None


def _preview_failure(fmt, units, diagnostics, source_name):
    """Build the 422 geometry-preview failure envelope."""
    return (
        422,
        {
            "schema_id": GEOMETRY_PREVIEW_SCHEMA_ID,
            "supported": False,
            "format": fmt,
            "source_units": units,
            "geometry": None,
            "diagnostics": diagnostics,
            "source_name": source_name,
        },
    )


def handle_normalize(config, query, body):
    """Normalize raw geometry bytes into the geometry-preview envelope."""
    raw_format = (query.get("format") or ["auto"])[0]
    fmt = _normalized_format(raw_format)
    if fmt is None:
        return make_web_error(
            422, "E_INVALID_FORMAT", "unsupported geometry format {!r}".format(raw_format)
        )
    acquired = _NORMALIZE_SEMAPHORE.acquire(timeout=60.0)
    if not acquired:
        return make_web_error(
            503, "E_BUSY", "server is busy processing geometry normalization; try again"
        )
    try:
        units = (query.get("units") or [None])[0]
        name = sanitize_display_name((query.get("name") or [None])[0])
        asset_dir = Path(config.cache_dir) / "step-assets" if config.cache_dir is not None else None
        try:
            from .importers import load_geometry
            from .step_kernel import StepKernelFailure, StepKernelUnavailable
        except Exception as exc:
            return make_web_error(500, "E_INTERNAL", "geometry importer is unavailable: {}".format(exc))
        try:
            result = load_geometry(
                body,
                fmt=fmt,
                units=units,
                step_backend="auto",
                step_asset_dir=asset_dir,
            )
        except UnitError as exc:
            diagnostic = {
                "code": "invalid_units",
                "severity": "blocker",
                "message": str(exc),
                "details": {},
            }
            return _preview_failure(fmt, units, [diagnostic], name)
        except StepKernelUnavailable as exc:
            diagnostic = {
                "code": "step_kernel_unavailable",
                "severity": "blocker",
                "message": str(exc),
                "details": {},
            }
            return _preview_failure(fmt, units, [diagnostic], name)
        except StepKernelFailure as exc:
            diagnostic = {
                "code": "step_kernel_failed",
                "severity": "blocker",
                "message": str(exc),
                "details": {},
            }
            return _preview_failure(fmt, units, [diagnostic], name)
        except ValueError as exc:
            diagnostic = {
                "code": "parse_failed",
                "severity": "error",
                "message": str(exc),
                "details": {},
            }
            return _preview_failure(fmt, units, [diagnostic], name)
        if result is None or not result.is_supported:
            diagnostics = [
                item.to_dict() for item in (result.diagnostics if result is not None else ())
            ]
            return _preview_failure(fmt, units, diagnostics, name)
        raw_display_asset = getattr(result, "display_asset", None)
        display_asset = register_step_asset(raw_display_asset) if raw_display_asset is not None else None
        response = {
            "schema_id": GEOMETRY_PREVIEW_SCHEMA_ID,
            "supported": True,
            "format": result.format,
            "source_units": result.source_units,
            "geometry": result.geometry.to_dict(),
            "diagnostics": [item.to_dict() for item in result.diagnostics],
            "source_name": result.source_name or name,
        }
        if display_asset is not None:
            response["display_asset"] = display_asset
        return (
            200,
            response,
        )
    finally:
        _NORMALIZE_SEMAPHORE.release()


def _validate_analysis_envelope(payload):
    """Validate an analysis envelope, returning None or an error pair."""
    unknown = set(payload) - _ENVELOPE_KEYS
    if unknown:
        return make_web_error(
            422,
            "E_INVALID_ENVELOPE",
            "unknown envelope fields: {}".format(", ".join(sorted(unknown))),
        )
    if payload.get("schema_id") != WEB_ANALYSIS_REQUEST_SCHEMA_ID:
        found = payload.get("schema_id")
        return make_web_error(
            422,
            "E_INVALID_ENVELOPE",
            "unexpected schema_id {!r}; expected {!r}".format(found, WEB_ANALYSIS_REQUEST_SCHEMA_ID),
        )
    request = payload.get("request")
    if not isinstance(request, dict):
        return make_web_error(422, "E_INVALID_ENVELOPE", "envelope 'request' must be an object")
    options = payload.get("options")
    if options is not None:
        if not isinstance(options, dict):
            return make_web_error(422, "E_INVALID_ENVELOPE", "envelope 'options' must be an object")
        unknown_options = set(options) - _OPTION_KEYS
        if unknown_options:
            return make_web_error(
                422,
                "E_INVALID_ENVELOPE",
                "unknown options: {}".format(", ".join(sorted(unknown_options))),
            )
        for key in sorted(set(options) & _OPTION_KEYS):
            if not isinstance(options[key], bool):
                return make_web_error(
                    422, "E_INVALID_ENVELOPE", "option {!r} must be a boolean".format(key)
                )
    request_schema = request.get("schema_id")
    if request_schema not in _ALLOWED_REQUEST_SCHEMA_IDS:
        if request_schema == "gms.project-document":
            return make_web_error(
                422, "UNSUPPORTED_ARTIFACT", "a full project document cannot be executed"
            )
        return make_web_error(
            422, "E_INVALID_ENVELOPE", "unsupported request schema_id {!r}".format(request_schema)
        )
    objects = request.get("objects")
    has_objects = isinstance(objects, (list, tuple, dict)) and len(objects) > 0
    has_geometry = "geometry" in request
    has_geometry_asset = isinstance(request.get("geometry_asset_id"), str) and bool(
        request.get("geometry_asset_id").strip()
    )
    if not has_objects and not has_geometry and not has_geometry_asset:
        return make_web_error(
            422,
            "UNSUPPORTED_ARTIFACT",
            "request carries no inline geometry/object payload; "
            "reference-only payloads and full project documents cannot be executed",
        )
    return None


def _result_error_response(result):
    """Return a web-error pair for a failed pipeline result, or None when successful."""
    errors = result.get("errors") or []
    internal = any(
        isinstance(item, dict) and str(item.get("code", "")) == "PIPELINE_INTERNAL"
        for item in errors
    )
    if internal:
        message = "pipeline encountered an internal error"
        for item in errors:
            if isinstance(item, dict) and str(item.get("code", "")) == "PIPELINE_INTERNAL":
                message = str(item.get("message", "")) or message
        return make_web_error(500, "E_INTERNAL", message)
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str(first.get("code", "E_INVALID_INPUT"))
        message = str(first.get("message", "")) or "pipeline reported input errors"
        return make_web_error(422, code, message)
    validation = result.get("validation")
    if isinstance(validation, dict) and validation.get("status") == "fail":
        return make_web_error(
            422,
            "E_VALIDATION",
            "validation failed for the submitted geometry and material inputs",
        )
    return None


def slim_result_for_web(result):
    """Return a response copy with geometry-heavy manifest inputs replaced.

    W5-04 follow-up: the manifest was previously slimmed by replacing
    ``manifest.inputs.objects`` with a count/sha256 summary while keeping the
    original ``manifest_hash`` — the served document no longer matched its
    recorded hash, so ``reproduce_from_manifest`` REJECTED every web-served
    manifest (replay impossible on the web path).  The manifest is the
    certification document: it must stay byte-identical to the pipeline
    result.  Geometry-heavy echoes elsewhere in the result payload are
    trimmed, never the manifest.
    """
    slim = dict(result)
    return slim


def handle_analyze(config, cache, payload):
    """Validate the analysis envelope and execute the pipeline."""
    from .pipeline import ENGINE_VERSION, run_pipeline
    from .materials import builtin_materials, load_material_catalog

    invalid = _validate_analysis_envelope(payload)
    if invalid is not None:
        return invalid
    request = payload["request"]
    options = payload.get("options")
    pipeline_request = dict(request)
    asset_id = pipeline_request.get("geometry_asset_id")
    if asset_id is not None:
        if not isinstance(asset_id, str) or not _STEP_ASSET_ID_RE.fullmatch(asset_id):
            return make_web_error(422, "E_INVALID_ASSET", "geometry_asset_id is not a registered STEP asset")
        asset_objects = _load_registered_asset_objects(asset_id)
        if asset_objects is None:
            return make_web_error(422, "E_ASSET_NOT_FOUND", "registered STEP geometry is unavailable")
        asset_by_id = {str(item["id"]): item for item in asset_objects}
        requested_objects = pipeline_request.get("objects")
        if isinstance(requested_objects, list):
            resolved_objects = []
            for raw_object in requested_objects:
                if not isinstance(raw_object, dict):
                    continue
                object_id = str(raw_object.get("id", raw_object.get("name", ""))).strip()
                asset_object = asset_by_id.get(object_id)
                if asset_object is None:
                    continue
                resolved = dict(asset_object)
                resolved.update(raw_object)
                resolved["geometry"] = asset_object["geometry"]
                resolved_objects.append(resolved)
            pipeline_request["objects"] = resolved_objects or asset_objects
        elif isinstance(requested_objects, dict):
            resolved_objects = []
            for object_id, raw_object in requested_objects.items():
                raw = dict(raw_object) if isinstance(raw_object, dict) else {}
                asset_object = asset_by_id.get(str(object_id))
                if asset_object is None:
                    continue
                resolved = dict(asset_object)
                resolved.update(raw)
                resolved["id"] = str(object_id)
                resolved["geometry"] = asset_object["geometry"]
                resolved_objects.append(resolved)
            pipeline_request["objects"] = resolved_objects or asset_objects
        else:
            pipeline_request["objects"] = asset_objects
    use_cache = True
    if options is not None:
        use_cache = bool(options.get("use_cache", True))
        request_options = pipeline_request.get("options")
        pipeline_options = dict(request_options) if isinstance(request_options, dict) else {}
        pipeline_options["strict"] = bool(options.get("strict", False))
        pipeline_request["options"] = pipeline_options
    if cache is None and config.cache_dir is not None:
        from .cache import ArtifactCache

        cache = ArtifactCache(config.cache_dir)
    # Bounded analysis concurrency prevents several large pipelines from
    # exhausting memory simultaneously; excess requests queue.
    with _ANALYZE_SEMAPHORE:
        result = run_pipeline(pipeline_request, cache=cache, use_cache=use_cache)
    error_response = _result_error_response(result)
    if error_response is not None:
        return error_response
    catalog = builtin_materials()
    raw_materials = request.get("materials")
    if raw_materials is not None:
        try:
            catalog = load_material_catalog(raw_materials)
        except Exception:
            catalog = builtin_materials()
    return (
        200,
        {
            "schema_id": WEB_ANALYSIS_RESPONSE_SCHEMA_ID,
            "run_id": result["run_id"],
            "engine_version": ENGINE_VERSION,
            "result": slim_result_for_web(result),
            "materials": material_catalog_projection(catalog),
        },
    )


def _cache_for(config):
    if config.cache_dir is None:
        return None
    from .cache import ArtifactCache

    return ArtifactCache(config.cache_dir)


class WebApiHandler(BaseHTTPRequestHandler):
    """HTTP request handler bound to a :class:`WebConfig` instance."""

    config = None

    def version_string(self):
        return "mouse-sim-web-api/1"

    def log_message(self, format_string, *args):
        if self.config is not None and self.config.log_requests:
            super().log_message(format_string, *args)

    def _apply_cors(self):
        config = self.config
        if config is None or not config.cors_origins:
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin in config.cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")

    def _read_body(self, limit):
        length_header = self.headers.get("Content-Length")
        if length_header is not None:
            try:
                length = int(length_header)
            except (TypeError, ValueError):
                raise WebRequestError(400, "E_INVALID_HEADER", "invalid Content-Length header")
            if length < 0:
                raise WebRequestError(400, "E_INVALID_HEADER", "negative Content-Length")
            if length > limit:
                raise WebRequestError(
                    413, "E_BODY_TOO_LARGE", "request body exceeds the {} byte limit".format(limit)
                )
            body = self.rfile.read(length)
            if len(body) != length:
                raise WebRequestError(
                    400, "E_BAD_REQUEST", "client closed the connection before the body was complete"
                )
            return body
        chunks = []
        total = 0
        while True:
            chunk = self.rfile.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise WebRequestError(
                    413, "E_BODY_TOO_LARGE", "request body exceeds the {} byte limit".format(limit)
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _send_api(self, status, payload, extra_headers=None):
        body = canonical_json(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self._apply_cors()
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The client (e.g. the web console aborting a long geometry
            # upload) went away before the response could be delivered.
            self.close_connection = True

    def _route_api_get(self, path):
        if path == "/api/health":
            return handle_health(self.config)
        if path == "/api/projects/baseline":
            return handle_baseline(self.config)
        if path == "/api/materials":
            return handle_materials(self.config)
        prefix = "/api/classify/jobs/"
        if path.startswith(prefix):
            job_id = path[len(prefix):]
            return handle_classify_status(job_id)
        return make_web_error(404, "E_NOT_FOUND", "unknown API path {!r}".format(path))

    def _serve_registered_asset(self, path, head_only=False):
        """Serve only a registered asset whose generated id is in the registry."""
        decoded = unquote(path)
        parts_match = _STEP_ASSET_PARTS_ROUTE_RE.fullmatch(decoded)
        glb_match = _STEP_ASSET_ROUTE_RE.fullmatch(decoded)
        if parts_match is None and glb_match is None:
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "asset not found"))
            return
        if parts_match is not None:
            asset_id = parts_match.group(1)
            expected_name = asset_id + ".parts.json"
            content_type = "application/json; charset=utf-8"
            with _STEP_ASSET_REGISTRY_LOCK:
                registered = _STEP_ASSET_PARTS_REGISTRY.get(asset_id)
        else:
            asset_id = glb_match.group(1)
            expected_name = asset_id + ".glb"
            content_type = "model/gltf-binary"
            with _STEP_ASSET_REGISTRY_LOCK:
                registered = _STEP_ASSET_REGISTRY.get(asset_id)
        if registered is None:
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "asset not found"))
            return
        try:
            path = registered.resolve()
            if path.name != expected_name or not path.is_file():
                raise OSError("registered asset is unavailable")
            stream = path.open("rb")
            length = stream.seek(0, os.SEEK_END)
            stream.seek(0)
        except (OSError, RuntimeError, ValueError):
            if "stream" in locals():
                stream.close()
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "asset not found"))
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._apply_cors()
        self.end_headers()
        if head_only:
            stream.close()
            return
        try:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        finally:
            stream.close()

    def _post_normalize(self, config):
        content_type = self.headers.get("Content-Type") or ""
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in _GEOMETRY_MEDIA_TYPES:
            return make_web_error(
                415, "E_UNSUPPORTED_MEDIA_TYPE", "unsupported Content-Type {!r}".format(content_type)
            )
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        body = self._read_body(config.max_geometry_bytes)
        return handle_normalize(config, query, body)

    def _post_analyze(self, config):
        content_type = self.headers.get("Content-Type") or ""
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in _ANALYZE_MEDIA_TYPES:
            return make_web_error(
                415, "E_UNSUPPORTED_MEDIA_TYPE", "unsupported Content-Type {!r}".format(content_type)
            )
        body = self._read_body(config.max_json_bytes)
        payload = parse_json_object(body)
        return handle_analyze(config, _cache_for(config), payload)

    def _send_static_file(self, path, root, head_only=False):
        try:
            with path.open("rb") as stream:
                body = stream.read()
        except OSError:
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "file not found"))
            return
        content_type = _STATIC_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        relative = path.relative_to(root)
        if path.name == "index.html":
            cache_control = "no-cache"
        elif relative.parts and relative.parts[0] == "assets":
            cache_control = "public, max-age=31536000, immutable"
        else:
            cache_control = "no-cache"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self._apply_cors()
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve_static(self, head_only=False):
        config = self.config
        if config is None or config.web_dist is None:
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "static content is not configured"))
            return
        root = config.web_dist.resolve()
        decoded = unquote(urlparse(self.path).path)
        if "\x00" in decoded:
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "path not found"))
            return
        candidate = (root / decoded.lstrip("/")).resolve()
        if not candidate.is_relative_to(root):
            self._send_api(*make_web_error(404, "E_NOT_FOUND", "path not found"))
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            accepts_html = "text/html" in (self.headers.get("Accept") or "")
            fallback = root / "index.html"
            if accepts_html and fallback.is_file():
                candidate = fallback
            else:
                self._send_api(*make_web_error(404, "E_NOT_FOUND", "file not found"))
                return
        self._send_static_file(candidate, root, head_only=head_only)

    def _handle_get(self, head_only=False):
        try:
            path = urlparse(self.path).path
            if path.startswith("/api/geometry/assets/"):
                self._serve_registered_asset(path, head_only=head_only)
            elif path.startswith("/api/"):
                status, payload = self._route_api_get(path)
                self._send_api(status, payload)
            else:
                self._serve_static(head_only=head_only)
        except WebRequestError as exc:
            # 400/413 may leave the request body unread on the socket; keeping
            # the connection open would let leftover bytes be parsed as the
            # next request line (keep-alive poisoning).
            close = exc.status in (400, 413)
            if close:
                self.close_connection = True
            extra = {"Connection": "close"} if close else None
            self._send_api(exc.status, exc.envelope(), extra_headers=extra)
        except Exception as exc:
            self.log_message("internal error: %r", exc)
            self._send_api(*make_web_error(500, "E_INTERNAL", str(exc)))

    def _handle_post(self):
        try:
            config = self.config
            path = urlparse(self.path).path
            if path == "/api/geometry/normalize":
                status, payload = self._post_normalize(config)
            elif path == "/api/analyze":
                status, payload = self._post_analyze(config)
            elif path == "/api/classify":
                content_type = self.headers.get("Content-Type") or ""
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type not in _ANALYZE_MEDIA_TYPES:
                    status, payload = make_web_error(
                        415, "E_UNSUPPORTED_MEDIA_TYPE", "unsupported Content-Type {!r}".format(content_type)
                    )
                else:
                    body = self._read_body(config.max_json_bytes)
                    data = parse_json_object(body)
                    status, payload = handle_classify_start(config, data)
            else:
                status, payload = make_web_error(
                    404, "E_NOT_FOUND", "unknown API path {!r}".format(path)
                )
            if status in (400, 404, 413, 415):
                self.close_connection = True
                self._send_api(status, payload, extra_headers={"Connection": "close"})
            else:
                self._send_api(status, payload)
        except WebRequestError as exc:
            # 400/413 may leave the request body unread on the socket; keeping
            # the connection open would let leftover bytes be parsed as the
            # next request line (keep-alive poisoning).
            close = exc.status in (400, 413)
            if close:
                self.close_connection = True
            extra = {"Connection": "close"} if close else None
            self._send_api(exc.status, exc.envelope(), extra_headers=extra)
        except Exception as exc:
            self.log_message("internal error: %r", exc)
            self._send_api(*make_web_error(500, "E_INTERNAL", str(exc)))

    def do_GET(self):
        self._handle_get(head_only=False)

    def do_HEAD(self):
        self._handle_get(head_only=True)

    def do_POST(self):
        self._handle_post()

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._apply_cors()
            self.end_headers()
        except Exception as exc:
            try:
                self._send_api(*make_web_error(500, "E_INTERNAL", str(exc)))
            except Exception:
                pass


def build_server(config):
    """Build a threaded HTTP server whose handler is bound to ``config``."""
    handler_class = type("WebApiBoundHandler", (WebApiHandler,), {"config": config})
    server = ThreadingHTTPServer((config.host, config.port), handler_class)
    server.daemon_threads = True
    return server


def register_existing_assets(asset_dir):
    """Re-register cached STEP assets after a server restart.

    The asset registry is process-local; files persist on disk, so scan the
    asset directory for complete triples (GLB + parts JSON + manifest) and
    re-register them.  Only strictly validated, hex-id-named files are served.
    """
    if asset_dir is None:
        return 0
    try:
        root = Path(asset_dir).expanduser().resolve()
        if not root.is_dir():
            return 0
    except (OSError, RuntimeError):
        return 0
    registered = 0
    for glb_path in sorted(root.glob("*.glb")):
        asset_id = glb_path.name[:-4]
        if not _STEP_ASSET_ID_RE.fullmatch(asset_id):
            continue
        parts_path = root / (asset_id + ".parts.json")
        if not parts_path.is_file():
            continue
        # Restore part summaries from the manifest (small) rather than the
        # full parts JSON (tens of megabytes).
        parts = []
        manifest_path = root / (asset_id + ".manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_parts = manifest.get("parts")
            if isinstance(raw_parts, list):
                for entry in raw_parts:
                    if not (isinstance(entry, dict) and str(entry.get("id", ""))):
                        continue
                    summary = {"id": str(entry["id"]), "name": entry.get("name")}
                    color = entry.get("color")
                    if (
                        isinstance(color, (list, tuple))
                        and len(color) == 3
                        and all(isinstance(c, (int, float)) and math.isfinite(float(c)) for c in color)
                    ):
                        summary["color"] = [float(c) for c in color]
                    parts.append(summary)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            parts = []
        asset = {
            "asset_id": asset_id,
            "path": str(glb_path),
            "parts_path": str(parts_path),
            "parts": parts,
        }
        if register_step_asset(asset) is not None:
            registered += 1
    return registered


def serve(config, server=None):
    """Serve the web API until interrupted, printing the listening line."""
    if server is None:
        server = build_server(config)
    try:
        from .step_kernel import default_asset_dir

        if config.cache_dir is not None:
            asset_dir = Path(config.cache_dir) / "step-assets"
        else:
            asset_dir = default_asset_dir()
        register_existing_assets(asset_dir)
    except Exception:
        pass
    sys.stdout.write(
        "mouse-sim web API listening on http://{}:{}\n".format(config.host, config.port)
    )
    sys.stdout.flush()
    server.serve_forever()
    return 0


__all__ = [
    "API_VERSION",
    "WEB_ERROR_SCHEMA_ID",
    "WEB_HEALTH_SCHEMA_ID",
    "WEB_BASELINE_SCHEMA_ID",
    "WEB_MATERIAL_CATALOG_SCHEMA_ID",
    "GEOMETRY_PREVIEW_SCHEMA_ID",
    "WEB_ANALYSIS_REQUEST_SCHEMA_ID",
    "WEB_ANALYSIS_RESPONSE_SCHEMA_ID",
    "DEFAULT_MAX_JSON_BYTES",
    "DEFAULT_MAX_GEOMETRY_BYTES",
    "WebConfig",
    "WebRequestError",
    "sanitize_display_name",
    "register_step_asset",
    "material_catalog_projection",
    "make_web_error",
    "parse_json_object",
    "handle_health",
    "handle_baseline",
    "handle_materials",
    "handle_normalize",
    "handle_analyze",
    "slim_result_for_web",
    "build_server",
    "serve",
]
