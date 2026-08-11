"""Directory-based content-addressed artifact cache with digest verification."""

import json
import os
import tempfile
from pathlib import Path

from mouse_sim.canonical import canonical_bytes, sha256_bytes

CACHE_KEY_DOMAIN = "gms-analysis-cache-v1"


class ArtifactCache:
    """A content-addressed, JSON artifact store rooted at ``root_dir``.

    Each artifact is one JSON file whose name is derived from the cache key.
    Every stored payload carries a ``_digest`` field equal to the sha256 of
    the canonical bytes of the payload excluding ``_digest`` itself; loads
    reject corrupted or tampered files by returning ``None``.
    """

    def __init__(self, root_dir, engine_version="0.1.0"):
        self.root = Path(root_dir)
        self.engine_version = str(engine_version)

    def key_for(self, inputs):
        """Derive the cache key for an input snapshot at this engine version."""
        return cache_key_for_inputs(inputs, self.engine_version)

    def path_for(self, key):
        """Return the on-disk path for a cache key."""
        return self.root / "{}.json".format(key)

    def contains(self, key):
        """Return whether an artifact exists for ``key``."""
        return self.path_for(key).is_file()

    @staticmethod
    def _without_digest(payload):
        data = dict(payload)
        data.pop("_digest", None)
        return data

    @classmethod
    def _digest_of(cls, payload):
        return sha256_bytes(canonical_bytes(cls._without_digest(payload)))

    def store(self, key, payload):
        """Atomically store ``payload`` under ``key`` and return its path."""
        if not isinstance(payload, dict):
            raise TypeError("ArtifactCache.store expects a dict payload")
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = dict(payload)
        data["_digest"] = self._digest_of(data)
        descriptor, temporary = tempfile.mkstemp(
            prefix="{}-".format(key)[:40], suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            os.replace(temporary, str(target))
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return target

    def load(self, key):
        """Load the artifact for ``key``, or ``None`` on any corruption.

        The stored ``_digest`` field is verified against the sha256 of the
        canonical bytes of the payload excluding ``_digest`` itself.  The
        returned payload never includes the ``_digest`` field.
        """
        target = self.path_for(key)
        try:
            with target.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        stored_digest = data.get("_digest")
        if not isinstance(stored_digest, str):
            return None
        try:
            digest_matches = self._digest_of(data) == stored_digest
        except Exception:
            # A tampered payload with non-finite values raises inside the
            # digest computation (audit finding: it previously escaped the
            # guard and hard-failed the run as PIPELINE_INTERNAL).  Any
            # corruption — including non-finite JSON — is a cache miss.
            return None
        if not digest_matches:
            return None
        data.pop("_digest", None)
        return data


def cache_key_for_inputs(inputs, engine_version="0.1.0"):
    """Build a deterministic cache key for an input snapshot.

    The key is the sha256 of the ``gms-analysis-cache-v1`` domain prefix and
    a NUL byte over the canonical bytes of ``{"engine_version": ...,
    "inputs": ...}`` so keys never collide across engine releases.
    """
    payload = {"engine_version": str(engine_version), "inputs": inputs}
    return sha256_bytes(CACHE_KEY_DOMAIN.encode("ascii") + b"\0" + canonical_bytes(payload))


__all__ = ["ArtifactCache", "cache_key_for_inputs"]
