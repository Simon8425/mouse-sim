"""HTTP web API adapter for the mouse simulation package.

The adapter exposes the deterministic analysis engine over HTTP: health,
baseline project, material catalog, geometry normalization, and analysis
endpoints.  All responses are JSON; failures use the ``gms.web-error/1``
envelope and pipeline failures are surfaced as web errors instead of a
successful ``200`` pass-through.
"""

import json
import os
import sys
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

DEFAULT_MAX_JSON_BYTES = 8 * 1024 * 1024
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

    return (
        200,
        {
            "schema_id": WEB_HEALTH_SCHEMA_ID,
            "engine_version": ENGINE_VERSION,
            "api_version": API_VERSION,
            "supported_formats": ["json", "obj", "stl"],
            "solver_capabilities": list(SOLVER_CAPABILITIES.to_dict()["capability_keys"]),
            "cache_active": config.cache_dir is not None,
            "max_json_bytes": config.max_json_bytes,
            "max_geometry_bytes": config.max_geometry_bytes,
            "deterministic": True,
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
    path = config.project_root / "examples" / "mouse_baseline.json"
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
    units = (query.get("units") or [None])[0]
    name = sanitize_display_name((query.get("name") or [None])[0])
    try:
        from .importers import load_geometry
    except Exception as exc:
        return make_web_error(500, "E_INTERNAL", "geometry importer is unavailable: {}".format(exc))
    try:
        result = load_geometry(body, fmt=fmt, units=units)
    except UnitError as exc:
        diagnostic = {
            "code": "invalid_units",
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
    return (
        200,
        {
            "schema_id": GEOMETRY_PREVIEW_SCHEMA_ID,
            "supported": True,
            "format": result.format,
            "source_units": result.source_units,
            "geometry": result.geometry.to_dict(),
            "diagnostics": [item.to_dict() for item in result.diagnostics],
            "source_name": result.source_name or name,
        },
    )


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
    if not has_objects and not has_geometry:
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
            "result": result,
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
        self.wfile.write(body)

    def _route_api_get(self, path):
        if path == "/api/health":
            return handle_health(self.config)
        if path == "/api/projects/baseline":
            return handle_baseline(self.config)
        if path == "/api/materials":
            return handle_materials(self.config)
        return make_web_error(404, "E_NOT_FOUND", "unknown API path {!r}".format(path))

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
            if path.startswith("/api/"):
                status, payload = self._route_api_get(path)
                self._send_api(status, payload)
            else:
                self._serve_static(head_only=head_only)
        except WebRequestError as exc:
            extra = {"Connection": "close"} if exc.status == 413 else None
            self._send_api(exc.status, exc.envelope(), extra_headers=extra)
        except Exception as exc:
            self._send_api(*make_web_error(500, "E_INTERNAL", str(exc)))

    def _handle_post(self):
        try:
            config = self.config
            path = urlparse(self.path).path
            if path == "/api/geometry/normalize":
                status, payload = self._post_normalize(config)
            elif path == "/api/analyze":
                status, payload = self._post_analyze(config)
            else:
                status, payload = make_web_error(
                    404, "E_NOT_FOUND", "unknown API path {!r}".format(path)
                )
            self._send_api(status, payload)
        except WebRequestError as exc:
            if exc.status == 413:
                self.close_connection = True
            extra = {"Connection": "close"} if exc.status == 413 else None
            self._send_api(exc.status, exc.envelope(), extra_headers=extra)
        except Exception as exc:
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


def serve(config, server=None):
    """Serve the web API until interrupted, printing the listening line."""
    if server is None:
        server = build_server(config)
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
    "material_catalog_projection",
    "make_web_error",
    "parse_json_object",
    "handle_health",
    "handle_baseline",
    "handle_materials",
    "handle_normalize",
    "handle_analyze",
    "build_server",
    "serve",
]
