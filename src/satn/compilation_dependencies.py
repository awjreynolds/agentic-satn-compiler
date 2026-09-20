"""Explicit cache identity for the local POC compiler.

The POC deliberately uses a developer-managed revision for compiler reuse.
Changing compiler semantics requires incrementing ``COMPILER_CACHE_REVISION``.
The compiler does not scan package files or inspect installed distributions to
derive this value; input and network identities remain derived from the actual
Area Definition and governed source data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from satn.models import AreaConfig

CompilerPath = Literal[
    "network",
    "reference",
    "strategic-reference",
    "ea-recovery",
]

MANIFEST_SCHEMA_VERSION: Final = "satn-compilation-dependency-manifest/v4"
# Preserve genuine closed required routes through mesh admission.
# Rebind canonical urban city/town anchors within the governed urban scope.
# Preserve governed A-road and typed cycle-corridor obligations beside urban journeys.
# Retain raw NCN membership and geometry-only reciprocal edge identities.
COMPILER_CACHE_REVISION: Final = "poc-v11"
_REVISION = re.compile(r"^[a-z0-9]+(?:[-._/][a-z0-9]+)*$")
_COMPILER_PATHS = frozenset({"network", "reference", "strategic-reference", "ea-recovery"})


def _package_root() -> Path:
    """Return the installed SATN package for presentation asset lookups."""

    return Path(__file__).resolve().parent


def _validate_compiler_path(value: object) -> str:
    if value not in _COMPILER_PATHS:
        raise ValueError(f"unsupported compiler dependency path: {value}")
    return str(value)


def _validate_revision(value: object) -> str:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise ValueError("compiler cache revision is invalid")
    return value


def compilation_dependency_manifest(
    config: AreaConfig | None = None,
    *,
    compiler_path: CompilerPath = "network",
) -> dict[str, object]:
    """Return the local compiler's explicit cache revision record."""

    del config
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "compiler_cache_revision": COMPILER_CACHE_REVISION,
        "compiler_path": _validate_compiler_path(compiler_path),
    }


def validate_compilation_dependency_manifest(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Validate the small persisted compiler revision record."""

    if not isinstance(manifest, Mapping):
        raise ValueError("compilation dependency manifest must be an object")
    expected = {"schema_version", "compiler_cache_revision", "compiler_path"}
    if set(manifest) != expected or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("compilation dependency manifest schema is unsupported")
    _validate_revision(manifest.get("compiler_cache_revision"))
    _validate_compiler_path(manifest.get("compiler_path"))
    return dict(manifest)


def compiler_cache_revision(manifest: Mapping[str, object]) -> str:
    """Return the validated named revision used by stage and publication keys."""

    return _validate_revision(manifest.get("compiler_cache_revision"))


def is_compiler_cache_revision(value: object) -> bool:
    """Return whether a value is an explicit named compiler revision."""

    return isinstance(value, str) and _REVISION.fullmatch(value) is not None


__all__ = [
    "COMPILER_CACHE_REVISION",
    "MANIFEST_SCHEMA_VERSION",
    "CompilerPath",
    "_package_root",
    "compilation_dependency_manifest",
    "compiler_cache_revision",
    "is_compiler_cache_revision",
    "validate_compilation_dependency_manifest",
]
