"""Canonical decoding for scalar and collection-valued source tags."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping


def tag_values(value: object) -> list[str]:
    """Return deterministic text values after GeoJSON and OSM round-trips."""
    if value is None:
        return []
    if isinstance(value, str) and value.startswith(("[", "(", "{")):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, (list, tuple, set)):
                value = parsed
        except (SyntaxError, ValueError):
            pass
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return [str(item) for item in value]
    text = str(value).strip()
    return [] if text.lower() in {"", "nan", "none", "<na>"} else [text]


def canonical_tag_values(value: object) -> tuple[str, ...]:
    """Return trimmed, non-missing values from scalar or collection tags."""

    return tuple(
        text.strip()
        for item in tag_values(value)
        if (text := str(item).strip()) and text.lower() not in {"nan", "none", "<na>"}
    )


def tag_identity(value: object) -> str | None:
    """Return collection-safe identity text, or ``None`` when absent."""

    values = canonical_tag_values(value)
    return ",".join(values) if values else None


def tag_text(value: object) -> str | None:
    """Return the first canonical text value, or ``None`` when absent."""

    values = canonical_tag_values(value)
    return values[0] if values else None


def source_identity(
    row: Mapping[str, object],
    fields: Iterable[str],
    fallback: object | None = None,
) -> str | None:
    """Choose a stable source identity using caller-owned field precedence."""

    for field in fields:
        values = canonical_tag_values(row.get(field))
        if values:
            return ",".join(values)
    return str(fallback) if fallback is not None else None
