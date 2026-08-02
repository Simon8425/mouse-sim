"""Exceptions raised by the :mod:`mouse_sim` foundation package."""


class MouseSimError(Exception):
    """Base class for package errors."""


class SerializationError(MouseSimError, ValueError):
    """A model could not be serialized or deserialized."""


class ValidationError(MouseSimError, ValueError):
    """A model or document failed a validation rule."""

    def __init__(self, message, errors=()):
        super().__init__(message)
        self.errors = tuple(errors)


class DocumentValidationError(ValidationError):
    """A project document is structurally or referentially invalid."""


class ReferenceError(ValidationError):
    """An entity reference cannot be resolved or has a wrong hash."""


class UnsupportedVersionError(ValidationError):
    """The document or entity uses a schema version this package cannot read."""


class UnitError(MouseSimError, ValueError):
    """An unknown unit or incompatible unit conversion was requested."""


class CanonicalizationError(MouseSimError, ValueError):
    """A value cannot be represented by deterministic JSON."""
