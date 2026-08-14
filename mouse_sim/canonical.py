"""Deterministic JSON and content-addressed hashing helpers."""

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
from pathlib import Path

from .errors import CanonicalizationError


_IDENTITY_FIELDS = frozenset(("id", "revision", "created_at", "timestamp", "content_hash"))


def _plain(value):
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalizationError("Decimal values must be finite")
        if value == 0:
            return 0
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("JSON numbers must be finite")
        if value == 0.0:
            return 0
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    raise CanonicalizationError("unsupported value type: {}".format(type(value).__name__))


def canonical_value(value, exclude_top_level=()):
    """Normalize a value into the deterministic JSON-compatible form."""

    plain = _plain(value)
    if exclude_top_level and isinstance(plain, dict):
        excluded = set(exclude_top_level)
        plain = {key: item for key, item in plain.items() if key not in excluded}
    return plain


def canonical_json(value, exclude_top_level=()):
    """Return compact, sorted-key, UTF-8-safe canonical JSON text."""

    try:
        return json.dumps(
            canonical_value(value, exclude_top_level),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(str(exc))


def canonical_bytes(value, exclude_top_level=()):
    return canonical_json(value, exclude_top_level).encode("utf-8")


def canonical_bytes_preserialized(value):
    """Canonical bytes of an ALREADY-canonical (plain JSON-compatible) value.

    ``canonical_bytes`` re-normalizes its input through ``canonical_value``
    (``_plain``), which is pure overhead when the caller already holds the
    canonical form (e.g. the pipeline's ``inputs`` snapshot produced by
    ``_collect_inputs``).  The output is byte-identical to
    ``canonical_bytes(value)`` for canonical input: both serialize with
    ``sort_keys``, compact separators, ``ensure_ascii=False`` and
    ``allow_nan=False``.  Non-plain input (dataclasses, enums, non-finite
    floats) raises the same :class:`CanonicalizationError` as the standard
    path — this helper is a fast path, never a silent behavior change.
    """
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(str(exc))


def sha256_bytes(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha256_bytes expects bytes")
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path):
    """Hash a file without loading the entire artifact into memory."""

    digest = hashlib.sha256()
    with open(str(path), "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _domain_hash(domain, payload):
    prefix = domain.encode("ascii") + b"\0"
    return sha256_bytes(prefix + canonical_bytes(payload))


def without_identity(value):
    """Remove identity/timestamp fields from an entity-like top-level value.

    Entity metadata is nested under ``meta``; plain payloads may put these
    fields at the top level.  No nested user data is removed.
    """

    plain = canonical_value(value)
    if not isinstance(plain, dict):
        return plain
    result = dict(plain)
    meta = result.get("meta")
    if isinstance(meta, dict):
        result["meta"] = {
            key: item for key, item in meta.items() if key not in _IDENTITY_FIELDS
        }
    else:
        result = {key: item for key, item in result.items() if key not in _IDENTITY_FIELDS}
    return result


def entity_content_hash(entity):
    """Hash an entity's semantic content, excluding metadata identity fields."""

    data = canonical_value(entity)
    if not isinstance(data, dict):
        raise CanonicalizationError("entity must serialize to an object")
    meta = data.pop("meta", None)
    if isinstance(meta, dict):
        entity_type = meta.get("entity_type", type(entity).__name__)
        schema_version = meta.get("schema_version", 1)
    else:
        entity_type = type(entity).__name__
        schema_version = 1
        data = {key: item for key, item in data.items() if key not in _IDENTITY_FIELDS}
    payload = {
        "entity_type": entity_type,
        "schema_version": schema_version,
        "data": data,
    }
    return _domain_hash("mouse-sim-entity-content-v1", payload)


def content_hash(entity):
    """Compatibility alias for :func:`entity_content_hash`."""

    return entity_content_hash(entity)


def hashed_entity(entity):
    """Return a copy of an entity with its metadata content hash populated."""

    from dataclasses import replace

    if not hasattr(entity, "meta"):
        raise CanonicalizationError("entity has no meta field")
    if not hasattr(entity.meta, "content_hash"):
        raise CanonicalizationError("entity metadata has no content_hash field")
    return replace(entity, meta=replace(entity.meta, content_hash=entity_content_hash(entity)))


def manifest_hash(manifest):
    """Return the stable content hash for an immutable run manifest."""

    data = canonical_value(manifest)
    if not isinstance(data, dict):
        raise CanonicalizationError("manifest must serialize to an object")
    meta = data.pop("meta", None)
    if isinstance(meta, dict):
        entity_type = meta.get("entity_type", "RunManifest")
        schema_version = meta.get("schema_version", 1)
    else:
        entity_type = "RunManifest"
        schema_version = 1
    return _domain_hash(
        "mouse-sim-run-manifest-v1",
        {"entity_type": entity_type, "schema_version": schema_version, "data": data},
    )


def cache_key(payload=None, **kwargs):
    """Create a deterministic analysis cache key.

    The preferred form is ``cache_key(payload)``.  Keyword arguments are
    intentionally accepted so callers can construct the execution input
    without first making an ad-hoc dictionary.
    """

    if payload is not None and kwargs:
        raise TypeError("use either payload or keyword arguments, not both")
    value = payload if payload is not None else kwargs
    return _domain_hash("mouse-sim-analysis-cache-v1", value)


def make_cache_key(
    engine_version,
    solver,
    method_hash="",
    input_content_hashes=(),
    source_artifact_hashes=(),
    mesh_settings=None,
    numerical_settings=None,
    tolerance_profile=None,
    deterministic_seed=None,
):
    """Build the standard cache key payload for an analysis execution."""

    payload = {
        "engine_version": engine_version,
        "solver": solver,
        "method_hash": method_hash,
        "input_content_hashes": sorted(input_content_hashes),
        "source_artifact_hashes": sorted(source_artifact_hashes),
        "mesh_settings": mesh_settings or {},
        "numerical_settings": numerical_settings or {},
        "tolerance_profile": tolerance_profile or {},
        "deterministic_seed": deterministic_seed,
    }
    return cache_key(payload)


def cache_key_for_manifest(manifest):
    """Build a cache key from manifest content without output timestamps."""

    return _domain_hash("mouse-sim-analysis-cache-v1", without_identity(manifest))


# Common spelling used by callers that hash canonical JSON directly.
sha256_content = lambda value: sha256_bytes(canonical_bytes(value))
