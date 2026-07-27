"""Explicit fail-closed identity for code that can change a compiled network.

The compiler identity deliberately has two small, reviewable inputs:

* SATN package files whose execution can change ``CompiledNetwork``; and
* installed runtime distributions that those files execute through.

Publication, deployment and Pages code intentionally do not participate in
this identity.  They validate, present or package a compiled network.  Their
current validation still runs before a publication may be reused, so excluding
them here is not an exclusion from safety checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Final

MANIFEST_SCHEMA_VERSION: Final = "satn-compilation-dependency-manifest/v2"
DEPENDENCY_SET_VERSION: Final = "satn-compiled-network/v2"
PACKAGE_LABEL: Final = "satn"

# The tuple is (kind, reason).  Keep this list intentionally flat and explicit:
# it is the review surface for a change that can alter CompiledNetwork output.
# Labels are relative to the installed ``satn`` package, never a checkout's
# ``src/satn`` path, so an installed wheel and an editable installation agree.
COMPILATION_COMPONENTS: Final[dict[str, tuple[str, str]]] = {
    "satn/__init__.py": ("module", "executed SATN package import boundary"),
    "satn/agents.py": ("module", "bounded decision selection and replay"),
    "satn/atm.py": ("module", "authoritative ATM comparison"),
    "satn/backbone.py": ("module", "Backbone-and-Access assembly"),
    "satn/compilation_dependencies.py": (
        "module",
        "compilation dependency manifest and fail-closed registry",
    ),
    "satn/compiler.py": ("module", "CompiledNetwork derivation"),
    "satn/constants.py": ("module", "compiler schema and source constants"),
    "satn/cross_spine.py": ("module", "Cross-Spine Connector assembly"),
    "satn/ea_elevation.py": ("module", "governed elevation contract and sampling"),
    "satn/education_access.py": (
        "module",
        "School Access Obligation and destination evidence assessment",
    ),
    "satn/evidence.py": ("module", "network evidence derivation"),
    "satn/existing_alignment.py": (
        "module",
        "Existing-Alignment Advantage evidence derivation and tie-break records",
    ),
    "satn/identifiers.py": ("module", "stable compiled feature identifiers"),
    "satn/models.py": ("module", "Area Definition and compiled model semantics"),
    "satn/network_selection.py": (
        "module",
        "frozen Network Selection Profile validation and fingerprinting",
    ),
    "satn/alignment_selection.py": (
        "module",
        "deterministic Preferred Strategic Alignment selection contract",
    ),
    "satn/pipeline.py": ("module", "compilation orchestration and reuse binding"),
    "satn/population_reach.py": ("module", "governed Population Reach evidence assessment"),
    "satn/psa_evidence_loaders.py": (
        "module",
        "strict governed Preferred Strategic Alignment evidence loading",
    ),
    "satn/spine_access_candidate_preparation.py": (
        "module",
        "bounded Spine Access candidate-preparation adapter; no strategic selection",
    ),
    "satn/strategic_corridors.py": (
        "module",
        "compiler-derived sibling strategic-corridor candidate preparation",
    ),
    "satn/routing.py": ("module", "routable network and route selection"),
    "satn/school_street.py": ("module", "school-street assessment"),
    "satn/settlement.py": ("module", "settlement and urban eligibility"),
    "satn/sources.py": ("module", "governed snapshot and source loading"),
    "satn/tags.py": ("module", "OSM tag interpretation"),
    "satn/topography.py": ("module", "topography profile derivation"),
    "satn/topography_alternatives.py": (
        "module",
        "topography alternative comparison",
    ),
    "satn/urban.py": ("module", "urban structure derivation"),
    "satn/urban_community.py": ("module", "urban community access derivation"),
    "satn/urban_school.py": ("module", "urban school access derivation"),
}

# These paths are controlled SATN package files, but are intentionally excluded
# because they cannot alter CompiledNetwork output.  Reasons are part of the
# reviewable registry; their bytes are deliberately not compiler digest inputs.
# Current publication validation remains mandatory before reuse.
EXCLUDED_COMPONENTS: Final[dict[str, str]] = {
    "satn/assets/MAPLIBRE-LICENSE.txt": "review-map vendor licence text",
    "satn/assets/__init__.py": "review-map resource package marker",
    "satn/assets/maplibre-gl.css": "review-map presentation asset",
    "satn/assets/maplibre-gl.js": "review-map presentation asset",
    "satn/assets/review-map.css": "review-map presentation asset",
    "satn/assets/review-map.html": "review-map presentation asset",
    "satn/assets/review-map.js": "review-map presentation asset",
    "satn/cli.py": "command-line adapter",
    "satn/deployment.py": "isolated deployment assembly",
    "satn/deployment_catalogue.py": "deployment catalogue assembly",
    "satn/deployment_provenance.py": "deployment lock validation",
    "satn/heartbeat.py": "operational progress reporting",
    "satn/pages_packaging.py": "Pages release packaging",
    "satn/psa_criteria_assembly.py": (
        "post-compile governed criteria assembly without CompiledNetwork mutation"
    ),
    "satn/publisher.py": "publication, PDF and review-map serialization",
    "satn/reference_application.py": (
        "post-adoption Reference replay planning without CompiledNetwork mutation"
    ),
    "satn/runtime_governance.py": "current publication runtime-governance validation",
    "satn/runtime_governance_contract.py": "current publication governance contract",
    "satn/scenario_compilation.py": (
        "post-compile Scenario Compilation bridge without CompiledNetwork mutation"
    ),
    "satn/strategic_criteria_scenario.py": (
        "post-compile strategic criteria and Scenario bridge without CompiledNetwork mutation"
    ),
}

# These are the distributions whose executed runtime can change a compiled
# network.  The list intentionally includes the direct compiler imports and the
# geospatial/model-validation engines they invoke, but excludes CLI, test,
# build, PDF and publication-only tooling.  Each version is captured from the
# installed distribution rather than from pyproject.toml or uv.lock.
COMPILER_RUNTIME_DISTRIBUTIONS: Final[dict[str, str]] = {
    "geopandas": "spatial frame operations and vector source I/O",
    "httpx": "supported PydanticAI OpenAI direct-runtime HTTP transport",
    "networkx": "routable graph traversal and path selection",
    "numpy": "numeric engine used by spatial and tabular compiler operations",
    "openai": "supported PydanticAI OpenAI direct-runtime client",
    "osmnx": "governed OSM acquisition and graph normalisation",
    "pandas": "tabular ordering, filtering and network evidence derivation",
    "pydantic": "Area Definition and agent decision validation",
    "pydantic-ai-slim": "configured bounded direct-runtime decisions",
    "pydantic-core": "Pydantic validation engine",
    "pyogrio": "GeoPandas vector reader/writer engine",
    "pyproj": "coordinate-reference-system transformations",
    "PyYAML": "Area Definition YAML parsing",
    "shapely": "geometry construction and spatial operations",
}

_TEXT_SUFFIXES: Final = frozenset({".css", ".html", ".js", ".py", ".txt"})
_NON_CONTROLLED_FILENAMES: Final = frozenset({".DS_Store"})


def _package_root() -> Path:
    """Return the installed SATN package directory, not a source checkout root."""
    return Path(__file__).resolve().parent


def _canonical_distribution_name(distribution: str) -> str:
    """Return the stable PEP 503-style label used in manifest component paths."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


def _is_non_controlled_package_metadata(relative: Path) -> bool:
    """Ignore interpreter/cache metadata without weakening source-file classification."""
    if any(part == "__pycache__" for part in relative.parts):
        return True
    if any(part.endswith((".dist-info", ".egg-info")) for part in relative.parts):
        return True
    return relative.name in _NON_CONTROLLED_FILENAMES or relative.suffix in {".pyc", ".pyo"}


def _component_label(relative: Path) -> str:
    return f"{PACKAGE_LABEL}/{relative.as_posix()}"


def _controlled_satn_paths(package_root: Path) -> set[str]:
    """Return every controlled installed-package file requiring classification.

    Package labels are invariant across source checkouts, editable installs and
    installed wheels.  Python cache and distribution metadata are never hashed;
    every remaining regular non-symlink package file must be registered.
    """
    root = package_root.resolve()
    if package_root.is_symlink() or not root.is_dir():
        raise ValueError("compilation dependency package root is missing or unsafe")
    paths: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _is_non_controlled_package_metadata(relative):
            continue
        if path.is_symlink():
            raise ValueError(f"compilation dependency package file is a symlink: {relative}")
        if path.is_file():
            paths.add(_component_label(relative))
    return paths


def _registry_error(
    *,
    controlled: set[str],
    compilation: Mapping[str, tuple[str, str]],
    excluded: Mapping[str, str],
) -> None:
    """Reject incomplete, overlapping, missing or unclassified registrations."""
    compilation_paths = set(compilation)
    excluded_paths = set(excluded)
    errors: list[str] = []
    overlap = sorted(compilation_paths & excluded_paths)
    if overlap:
        errors.append("both compilation and excluded: " + ", ".join(overlap))
    missing = sorted((compilation_paths | excluded_paths) - controlled)
    if missing:
        errors.append("missing registered component: " + ", ".join(missing))
    unclassified = sorted(controlled - compilation_paths - excluded_paths)
    if unclassified:
        errors.append("unclassified controlled component: " + ", ".join(unclassified))
    malformed = sorted(
        path
        for path, value in compilation.items()
        if not isinstance(value, tuple)
        or len(value) != 2
        or not all(isinstance(part, str) and part for part in value)
    )
    if malformed:
        errors.append("malformed compilation component: " + ", ".join(malformed))
    malformed_exclusions = sorted(
        path for path, reason in excluded.items() if not isinstance(reason, str) or not reason
    )
    if malformed_exclusions:
        errors.append("malformed excluded component: " + ", ".join(malformed_exclusions))
    if errors:
        raise ValueError("invalid compilation dependency manifest: " + "; ".join(errors))


def _runtime_distribution_version(distribution: str) -> str:
    """Read one required installed compiler runtime version, fail closed if absent."""
    try:
        version = metadata.version(distribution)
    except metadata.PackageNotFoundError as error:
        raise ValueError(
            f"required compiler runtime distribution is unavailable: {distribution}"
        ) from error
    if not isinstance(version, str) or not version.strip():
        raise ValueError(
            f"required compiler runtime distribution has invalid version: {distribution}"
        )
    return version.strip()


def _normalized_component_bytes(path: Path) -> bytes:
    """Return bytes with only text line-ending normalisation applied.

    CRLF/LF differences are checkout transport differences for the text assets
    and Python modules in this package.  No whitespace, encoding or binary
    normalisation is performed.
    """
    contents = path.read_bytes()
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return contents
    try:
        contents.decode("utf-8")
    except UnicodeDecodeError:
        return contents
    return contents.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _component_path(package_root: Path, label: str) -> Path:
    prefix = f"{PACKAGE_LABEL}/"
    if not label.startswith(prefix):
        raise ValueError(f"invalid SATN package component label: {label}")
    relative = Path(label.removeprefix(prefix))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"invalid SATN package component label: {label}")
    path = package_root / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"registered compilation component is unreadable: {label}")
    return path


def _runtime_component_records() -> list[dict[str, str]]:
    """Return the canonical installed-runtime projection used in the manifest."""
    labels: set[str] = set()
    records: list[dict[str, str]] = []
    for distribution, reason in COMPILER_RUNTIME_DISTRIBUTIONS.items():
        canonical_name = _canonical_distribution_name(distribution)
        label = f"runtime-distribution/{canonical_name}"
        if label in labels:
            raise ValueError(f"duplicate compiler runtime distribution label: {label}")
        labels.add(label)
        version = _runtime_distribution_version(distribution)
        records.append(
            {
                "path": label,
                "kind": "runtime-distribution",
                "reason": reason,
                "version": version,
                "sha256": hashlib.sha256(
                    f"{canonical_name}\0{version}".encode()
                ).hexdigest(),
            }
        )
    return records


def compilation_dependency_manifest(
    *,
    package_root: Path | None = None,
    components: Mapping[str, tuple[str, str]] | None = None,
    excluded: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return the complete, explicit semantic dependency manifest.

    ``package_root`` and registry arguments exist for deterministic tests.  The
    normal compiler always discovers files beside this installed module and
    versions from installed distribution metadata.
    """
    root = (package_root or _package_root()).resolve()
    compilation = dict(COMPILATION_COMPONENTS if components is None else components)
    exclusions = dict(EXCLUDED_COMPONENTS if excluded is None else excluded)
    controlled = _controlled_satn_paths(root)
    _registry_error(
        controlled=controlled,
        compilation=compilation,
        excluded=exclusions,
    )
    component_records = _runtime_component_records()
    component_records.extend(
        {
            "path": label,
            "kind": compilation[label][0],
            "reason": compilation[label][1],
            "sha256": hashlib.sha256(
                _normalized_component_bytes(_component_path(root, label))
            ).hexdigest(),
        }
        for label in sorted(compilation)
    )
    component_records.sort(key=lambda component: component["path"])
    digest_payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dependency_set_version": DEPENDENCY_SET_VERSION,
        "components": component_records,
    }
    return {
        **digest_payload,
        "sha256": hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "excluded_components": [
            {"path": path, "reason": exclusions[path]} for path in sorted(exclusions)
        ],
    }
