"""Schema loading and dependency-free project document validation.

The JSON schema is useful to tools that understand JSON Schema, while this
module provides the semantic checks needed by the standard-library-only core:
version checks, model deserialization, unique entity IDs, and reference/hash
integrity.  Validation raises the package's typed exceptions rather than
silently modifying a document.
"""

from collections.abc import Mapping
import json
import math
from pathlib import Path

from .canonical import entity_content_hash
from .errors import DocumentValidationError, SerializationError, UnsupportedVersionError
from .model import ProjectDocument, SCHEMA_ID, SCHEMA_VERSION


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mouse_sim.schema.json"

_ROOT_FIELDS = (
    "schema_id",
    "schema_version",
    "project",
    "geometry_assets",
    "components",
    "material_definitions",
    "material_assignments",
    "reference_frames",
    "load_cases",
    "fixtures",
    "requirements",
    "methods",
    "correlation_records",
    "analysis_runs",
    "validation_issues",
    "reports",
    "run_manifests",
    "review_records",
)


def schema_path():
    """Return the installed JSON schema path."""

    return SCHEMA_PATH


def load_schema():
    """Load and return the packaged JSON schema as a dictionary."""

    with SCHEMA_PATH.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def document_to_dict(document):
    """Serialize a :class:`ProjectDocument` to its JSON-compatible mapping."""

    if not isinstance(document, ProjectDocument):
        raise TypeError("document must be a ProjectDocument")
    return document.to_dict()


def document_from_dict(data):
    """Deserialize a project document and run its structural checks."""

    if isinstance(data, ProjectDocument):
        return validate_document(data)
    try:
        document = ProjectDocument.from_dict(data)
    except (SerializationError, TypeError, ValueError) as exc:
        raise DocumentValidationError("invalid project document: {}".format(exc), (str(exc),))
    return validate_document(document)


def _mapping_data(document):
    if isinstance(document, ProjectDocument):
        return document.to_dict(), document, None
    if not isinstance(document, Mapping):
        raise DocumentValidationError("project document must be an object", ("root is not an object",))
    try:
        decoded = ProjectDocument.from_dict(document)
    except (SerializationError, TypeError, ValueError) as exc:
        return dict(document), None, str(exc)
    return dict(document), decoded, None


def _walk_references(value, location=""):
    """Yield serialized EntityRef-shaped mappings with their locations."""

    if isinstance(value, Mapping):
        if set(value) == {"id", "content_hash"}:
            yield location or "$", value
            return
        for key, child in value.items():
            child_location = "{}.{}".format(location, key) if location else str(key)
            for item in _walk_references(child, child_location):
                yield item
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            child_location = "{}[{}]".format(location, index)
            for item in _walk_references(child, child_location):
                yield item


def _finite_errors(value, location="$", errors=None):
    if errors is None:
        errors = []
    if isinstance(value, bool) or value is None:
        return errors
    if isinstance(value, float) and not math.isfinite(value):
        errors.append("{} must be finite".format(location))
        return errors
    if isinstance(value, Mapping):
        for key, child in value.items():
            _finite_errors(child, "{}.{}".format(location, key), errors)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _finite_errors(child, "{}[{}]".format(location, index), errors)
    return errors


def _structural_errors(data):
    errors = []
    missing = [name for name in _ROOT_FIELDS if name not in data]
    if missing:
        errors.append("missing root fields: {}".format(", ".join(missing)))
    unknown = sorted(set(data) - set(_ROOT_FIELDS))
    if unknown:
        errors.append("unknown root fields: {}".format(", ".join(unknown)))
    if data.get("schema_id") != SCHEMA_ID:
        errors.append("schema_id must be {!r}".format(SCHEMA_ID))
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        errors.append("schema_version must be an integer")
    elif version != SCHEMA_VERSION:
        errors.append("unsupported schema_version: {!r}".format(version))
    if not isinstance(data.get("project"), Mapping):
        errors.append("project must be an object")
    for field_name in _ROOT_FIELDS[3:]:
        if field_name in data and not isinstance(data[field_name], (list, tuple)):
            errors.append("{} must be an array".format(field_name))
    return errors


def _entity_values(document):
    return (document.project,) + document.entities()


def _semantic_errors(data, document, verify_hashes=False):
    errors = []
    entities = _entity_values(document)
    by_id = {}
    for index, entity in enumerate(entities):
        meta = getattr(entity, "meta", None)
        location = "project" if index == 0 else "entities[{}]".format(index - 1)
        if meta is None:
            errors.append("{} has no meta field".format(location))
            continue
        entity_id = getattr(meta, "id", "")
        if entity_id:
            if entity_id in by_id:
                errors.append("duplicate entity id: {}".format(entity_id))
            by_id[entity_id] = entity
        if meta.schema_version != SCHEMA_VERSION:
            errors.append("{} has unsupported schema_version: {!r}".format(location, meta.schema_version))
        if not isinstance(meta.content_hash, str):
            errors.append("{}.meta.content_hash must be a string".format(location))
        elif verify_hashes and meta.content_hash:
            expected = entity_content_hash(entity)
            if meta.content_hash != expected:
                errors.append("{} has an invalid content_hash".format(location))

    for location, reference in _walk_references(data):
        reference_id = reference.get("id", "")
        if not reference_id:
            errors.append("{} has an empty entity reference id".format(location))
            continue
        target = by_id.get(reference_id)
        if target is None:
            errors.append("{} references unknown entity id {!r}".format(location, reference_id))
            continue
        target_hash = target.meta.content_hash
        reference_hash = reference.get("content_hash", "")
        if reference_hash and reference_hash != target_hash:
            errors.append("{} has a content_hash mismatch".format(location))

    errors.extend(_finite_errors(data))
    return errors


def document_validation_errors(document, verify_hashes=False):
    """Return validation messages for a project document.

    ``verify_hashes`` enables recalculation of every populated entity hash.
    Reference hashes are checked whenever a reference supplies one.
    Structural failures such as unknown root fields are returned as
    messages rather than raised; the caller decides how to act on them.
    """

    data, decoded, decode_error = _mapping_data(document)
    errors = _structural_errors(data)
    if decoded is None:
        if not errors and decode_error:
            errors.append(decode_error)
        return tuple(errors)
    if not errors:
        errors.extend(_semantic_errors(data, decoded, verify_hashes=verify_hashes))
    return tuple(errors)


def validate_document(document, verify_hashes=False):
    """Validate and return a project document, raising on invalid input."""

    data, decoded, decode_error = _mapping_data(document)
    errors = _structural_errors(data)
    if errors:
        if any("unsupported schema_version" in error for error in errors):
            raise UnsupportedVersionError("unsupported project document version", errors)
        raise DocumentValidationError("invalid project document", errors)
    if decoded is None:
        raise DocumentValidationError(
            "invalid project document",
            (decode_error or "project document could not be decoded",),
        )
    errors = _semantic_errors(data, decoded, verify_hashes=verify_hashes)
    if errors:
        raise DocumentValidationError("invalid project document", errors)
    return decoded


def validate_references(document, verify_hashes=False):
    """Validate entity IDs and reference hashes, returning the document."""

    return validate_document(document, verify_hashes=verify_hashes)


# Short aliases used by callers that treat schema validation as a parse step.
validate = validate_document
deserialize = document_from_dict
serialize = document_to_dict


__all__ = [
    "SCHEMA_PATH",
    "schema_path",
    "load_schema",
    "document_to_dict",
    "document_from_dict",
    "document_validation_errors",
    "validate_document",
    "validate_references",
    "validate",
    "serialize",
    "deserialize",
]
