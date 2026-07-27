"""Deterministic local content identities without authentication semantics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def canonical_json(value: object) -> str:
    """Return the established byte-compatible canonical JSON representation."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_fingerprint(value: object) -> str:
    """Return a local SHA-256 content identity for canonical JSON bytes."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def ordered_geometry_fingerprint(geometries: Iterable[object]) -> str:
    """Match the Scenario Area identity: ordered current geometry WKB bytes."""

    try:
        payload = b"".join(bytes(item.wkb) for item in geometries)
    except (AttributeError, TypeError) as error:
        raise ValueError("Area identity requires ordered current geometry WKB") from error
    return hashlib.sha256(payload).hexdigest()
