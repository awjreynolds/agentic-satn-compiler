"""Explicit fail-closed identity for code that can change a compiled network.

The compiler identity deliberately has two small, reviewable inputs selected
for the configured execution:

* SATN package files whose active path can change ``CompiledNetwork``; and
* installed runtime distributions that active path executes through.

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
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from satn.models import AreaConfig

CompilerPath = Literal[
    "network",
    "reference",
    "strategic-reference",
    "ea-recovery",
]

MANIFEST_SCHEMA_VERSION: Final = "satn-compilation-dependency-manifest/v3"
DEPENDENCY_SET_VERSION: Final = "satn-compiled-network/v3"
PACKAGE_LABEL: Final = "satn"

# The tuple is (kind, reason). Keep this complete registry explicit: it is the
# review surface for a change that can alter CompiledNetwork output.
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
    "satn/content_identity.py": (
        "module",
        "deterministic local content and Area identities",
    ),
    "satn/constants.py": ("module", "compiler schema and source constants"),
    "satn/cross_spine.py": ("module", "Cross-Spine Connector assembly"),
    "satn/ea_elevation.py": ("module", "governed elevation contract and sampling"),
    "satn/ea_snapshot_recovery.py": (
        "module",
        "pinned snapshot recovery loader and exhaustive reconciliation proof",
    ),
    "satn/education_access.py": (
        "module",
        "School Access Obligation and destination evidence assessment",
    ),
    "satn/evidence.py": ("module", "network evidence derivation"),
    "satn/evidence_contracts.py": (
        "module",
        "immutable Local Evidence identity contracts",
    ),
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
    "satn/section_population.py": (
        "module",
        "governed local Section Population Capture evidence assessment",
    ),
    "satn/publisher.py": (
        "module",
        "EA recovery candidate fixed-point validation and immutable retention",
    ),
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
    "satn/strategic_reference_replay.py": (
        "module",
        "private deterministic strategic Reference replay materialisation",
    ),
    "satn/routing.py": ("module", "routable network and route selection"),
    "satn/route_controls.py": (
        "module",
        "governed route exclusions, preferences and retained-gap constraints",
    ),
    "satn/remote_endpoints.py": (
        "module",
        "configured remote snapshot endpoint validation",
    ),
    "satn/school_street.py": ("module", "school-street assessment"),
    "satn/settlement.py": ("module", "settlement and urban eligibility"),
    "satn/sources.py": ("module", "governed snapshot and source loading"),
    "satn/streaming_geojson.py": (
        "module",
        "strict bounded validation of governed GeoJSON snapshot inputs",
    ),
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

# Optional bundles remain classified and audited even when they cannot affect a
# configured compiler path. New adapters must be added here or to the core set;
# the complete package registry still rejects every unclassified file.
OPTIONAL_COMPONENT_GROUPS: Final[dict[str, frozenset[str]]] = {
    "atm-comparison": frozenset({"satn/atm.py"}),
    "elevation-source": frozenset({"satn/ea_elevation.py"}),
    "ea-recovery": frozenset(
        {
            "satn/ea_snapshot_recovery.py",
            "satn/publisher.py",
        }
    ),
    "network-selection": frozenset(
        {
            "satn/alignment_selection.py",
            "satn/existing_alignment.py",
            "satn/network_selection.py",
            "satn/population_reach.py",
            "satn/section_population.py",
            "satn/psa_evidence_loaders.py",
            "satn/spine_access_candidate_preparation.py",
            "satn/strategic_corridors.py",
        }
    ),
    "strategic-reference": frozenset({"satn/strategic_reference_replay.py"}),
}
_OPTIONAL_COMPONENT_PATHS: Final = frozenset().union(
    *OPTIONAL_COMPONENT_GROUPS.values()
)
CORE_COMPILATION_COMPONENTS: Final = frozenset(COMPILATION_COMPONENTS).difference(
    _OPTIONAL_COMPONENT_PATHS
)

# These paths are controlled SATN package files, but are intentionally excluded
# because they cannot alter CompiledNetwork output.  Reasons are part of the
# reviewable registry; their bytes are deliberately not compiler digest inputs.
# Current publication validation remains mandatory before reuse.
EXCLUDED_COMPONENTS: Final[dict[str, str]] = {
    "satn/assets/MAPLIBRE-LICENSE.txt": "review-map vendor licence text",
    "satn/assets/__init__.py": "review-map resource package marker",
    "satn/assets/duckdb-spatial-runtime-lock.json": (
        "pinned Local Evidence Store provisioning asset; "
        "not a compiler input before equivalence cutover"
    ),
    "satn/assets/maplibre-gl.css": "review-map presentation asset",
    "satn/assets/maplibre-gl.js": "review-map presentation asset",
    "satn/assets/osm-network-osmconf.ini": (
        "closed OpenStreetMap OGR tag mapping for the additive Local Evidence adapter; "
        "not a compiler input before equivalence cutover"
    ),
    "satn/assets/review-map.css": "review-map presentation asset",
    "satn/assets/review-map.html": "review-map presentation asset",
    "satn/assets/review-map.js": "review-map presentation asset",
    "satn/assets/strategic-reference.css": "strategic-only review-map presentation asset",
    "satn/assets/strategic-reference.js": "strategic-only review-map presentation asset",
    "satn/cli.py": "command-line adapter",
    "satn/acceptance_cutover.py": (
        "additive Local Evidence acceptance gate; not a compiler input before cutover"
    ),
    "satn/deployment.py": "isolated deployment assembly",
    "satn/deployment_catalogue.py": "deployment catalogue assembly",
    "satn/deployment_provenance.py": "deployment lock validation",
    "satn/deployment_scenario_cli.py": "post-compile officer-scenario command adapter",
    "satn/deployment_scenarios.py": (
        "post-compile clean-baseline and officer-scenario assembly"
    ),
    "satn/ea_raster_evidence.py": (
        "additive Environment Agency raster evidence sidecar; "
        "not a compiler input before equivalence cutover"
    ),
    "satn/edge_enrichments.py": (
        "additive Local Evidence sidecar; not a compiler input before cutover"
    ),
    "satn/evidence_replay.py": (
        "additive Local Evidence replay gate; not a compiler input before equivalence cutover"
    ),
    "satn/evidence_materialisations.py": (
        "additive Local Evidence logical records; not a compiler input before cutover"
    ),
    "satn/evidence_store_acceptance.py": (
        "additive Local Evidence acceptance evidence; not a compiler input before cutover"
    ),
    "satn/evidence_store_equivalence.py": (
        "additive Local Evidence equivalence gate; not a compiler input before cutover"
    ),
    "satn/_evidence_operations.py": (
        "private Local Evidence Store operations; "
        "not a compiler input before equivalence cutover"
    ),
    "satn/evidence_cli.py": (
        "additive Local Evidence Store command adapter; "
        "not a compiler input before equivalence cutover"
    ),
    "satn/filesystem_safety.py": "publication and deployment replacement guard",
    "satn/ea_fixed_point_convergence.py": (
        "bounded fixed-point orchestration without CompiledNetwork mutation"
    ),
    "satn/ea_fixed_point_operations.py": (
        "local fixed-point command operations without CompiledNetwork mutation"
    ),
    "satn/heartbeat.py": "operational progress reporting",
    "satn/local_evidence_store.py": (
        "additive Local Evidence Store sidecar; not a compiler input before equivalence cutover"
    ),
    "satn/open_roads_adapter.py": (
        "additive Local Evidence source adapter; not a compiler input before equivalence cutover"
    ),
    "satn/osm_network_adapter.py": (
        "additive OpenStreetMap Local Evidence source adapter; "
        "not a compiler input before equivalence cutover"
    ),
    "satn/officer_decisions.py": (
        "post-compile human decision ledger and scenario translation"
    ),
    "satn/pages_packaging.py": "Pages release packaging",
    "satn/psa_criteria_assembly.py": (
        "post-compile governed criteria assembly without CompiledNetwork mutation"
    ),
    "satn/reference_application.py": (
        "post-adoption Reference replay planning without CompiledNetwork mutation"
    ),
    "satn/runtime_governance.py": "current publication runtime-governance validation",
    "satn/runtime_governance_contract.py": "current publication governance contract",
    "satn/routing_materialisation.py": (
        "additive routing/assembly sidecar; not a compiler input before equivalence cutover"
    ),
    "satn/scenario_compilation.py": (
        "post-compile Scenario Compilation bridge without CompiledNetwork mutation"
    ),
    "satn/scenario_iteration.py": (
        "post-compile changed-configuration coordinator without CompiledNetwork mutation"
    ),
    "satn/strategic_criteria_scenario.py": (
        "post-compile strategic criteria and Scenario bridge without CompiledNetwork mutation"
    ),
    "satn/strategic_reference_application.py": (
        "post-adoption strategic Reference binding without CompiledNetwork mutation"
    ),
    "satn/strategic_reference_publication.py": (
        "publication-only strategic Reference provenance record; no compiler authority"
    ),
    "satn/visual_survey.py": (
        "governed external visual-survey evidence contract; "
        "not an automatic compiler input"
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

OPTIONAL_RUNTIME_DISTRIBUTION_GROUPS: Final[dict[str, frozenset[str]]] = {
    "direct-agent-runtime": frozenset({"httpx", "openai", "pydantic-ai-slim"}),
    "osm-source": frozenset({"osmnx"}),
}
_OPTIONAL_RUNTIME_DISTRIBUTIONS: Final = frozenset().union(
    *OPTIONAL_RUNTIME_DISTRIBUTION_GROUPS.values()
)
CORE_RUNTIME_DISTRIBUTIONS: Final = frozenset(COMPILER_RUNTIME_DISTRIBUTIONS).difference(
    _OPTIONAL_RUNTIME_DISTRIBUTIONS
)

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


def _active_dependency_groups(
    config: AreaConfig | None,
    compiler_path: CompilerPath,
) -> set[str]:
    """Resolve conservative optional bundles from one configured execution."""

    if compiler_path not in {
        "network",
        "reference",
        "strategic-reference",
        "ea-recovery",
    }:
        raise ValueError(f"unsupported compiler dependency path: {compiler_path}")
    if config is None:
        return {
            *OPTIONAL_COMPONENT_GROUPS,
            *OPTIONAL_RUNTIME_DISTRIBUTION_GROUPS,
        }
    groups: set[str] = set()
    if config.atm.enabled:
        groups.add("atm-comparison")
    if config.source.national_elevation is not None:
        groups.add("elevation-source")
    if config.compilation.network_selection is not None:
        groups.add("network-selection")
    if compiler_path == "strategic-reference":
        groups.add("strategic-reference")
    if compiler_path == "ea-recovery":
        groups.add("ea-recovery")
    if (
        config.compilation.agent.response_mode == "direct-runtime"
        and config.compilation.agent.review_statuses
        and config.compilation.agent.provider != "fake"
    ):
        groups.add("direct-agent-runtime")
    if config.source.kind == "osm":
        groups.add("osm-source")
    return groups


def _selected_component_paths(active_groups: set[str]) -> set[str]:
    selected = set(CORE_COMPILATION_COMPONENTS)
    for group in active_groups:
        selected.update(OPTIONAL_COMPONENT_GROUPS.get(group, ()))
    return selected


def _selected_runtime_distributions(active_groups: set[str]) -> set[str]:
    selected = set(CORE_RUNTIME_DISTRIBUTIONS)
    for group in active_groups:
        selected.update(OPTIONAL_RUNTIME_DISTRIBUTION_GROUPS.get(group, ()))
    return selected


def _runtime_component_records(
    selected_distributions: set[str],
) -> list[dict[str, str]]:
    """Return the canonical installed-runtime projection used in the manifest."""
    labels: set[str] = set()
    records: list[dict[str, str]] = []
    for distribution in sorted(selected_distributions):
        reason = COMPILER_RUNTIME_DISTRIBUTIONS[distribution]
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
    config: AreaConfig | None = None,
    *,
    compiler_path: CompilerPath = "network",
    package_root: Path | None = None,
    components: Mapping[str, tuple[str, str]] | None = None,
    excluded: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return the complete, explicit semantic dependency manifest.

    ``package_root`` and registry arguments exist for deterministic tests.  The
    normal compiler always discovers files beside this installed module and
    versions from installed distribution metadata. Omitting ``config`` returns
    the complete registry projection for audits and compatibility tooling.
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
    active_groups = _active_dependency_groups(config, compiler_path)
    selected_paths = _selected_component_paths(active_groups)
    if config is None:
        selected_paths = set(compilation)
    selected_paths.intersection_update(compilation)
    selected_distributions = _selected_runtime_distributions(active_groups)
    if config is None:
        selected_distributions = set(COMPILER_RUNTIME_DISTRIBUTIONS)
    component_records = _runtime_component_records(selected_distributions)
    component_records.extend(
        {
            "path": label,
            "kind": compilation[label][0],
            "reason": compilation[label][1],
            "sha256": hashlib.sha256(
                _normalized_component_bytes(_component_path(root, label))
            ).hexdigest(),
        }
        for label in sorted(selected_paths)
    )
    component_records.sort(key=lambda component: component["path"])
    selection = {
        "compiler_path": compiler_path,
        "configuration_sensitive": config is not None,
        "active_groups": ["core", *sorted(active_groups)],
        "component_paths": [
            str(component["path"]) for component in component_records
        ],
    }
    digest_payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dependency_set_version": DEPENDENCY_SET_VERSION,
        "selection": selection,
        "components": component_records,
    }
    inactive_components = [
        {
            "path": path,
            "groups": sorted(
                group
                for group, paths in OPTIONAL_COMPONENT_GROUPS.items()
                if path in paths
            ),
            "reason": "registered optional compiler bundle is inactive",
        }
        for path in sorted(set(compilation).difference(selected_paths))
    ]
    inactive_components.extend(
        {
            "path": f"runtime-distribution/{_canonical_distribution_name(distribution)}",
            "groups": sorted(
                group
                for group, distributions in OPTIONAL_RUNTIME_DISTRIBUTION_GROUPS.items()
                if distribution in distributions
            ),
            "reason": "registered optional runtime bundle is inactive",
        }
        for distribution in sorted(
            set(COMPILER_RUNTIME_DISTRIBUTIONS).difference(selected_distributions)
        )
    )
    return {
        **digest_payload,
        "sha256": hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "excluded_components": [
            {"path": path, "reason": exclusions[path]} for path in sorted(exclusions)
        ],
        "inactive_components": inactive_components,
    }


def validate_compilation_dependency_manifest(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Validate one self-contained current manifest without resolving a config again.

    Publication records carry the exact compiler-path projection used for that
    compilation.  Their validation can therefore verify schema, canonical
    selection structure and digest integrity, but must not replace the recorded
    projection with a new default-path manifest.
    """

    selection = manifest.get("selection")
    components = manifest.get("components")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("dependency_set_version") != DEPENDENCY_SET_VERSION
        or not isinstance(selection, dict)
        or not isinstance(components, list)
    ):
        raise ValueError("compilation dependency manifest schema is unsupported")

    compiler_path = selection.get("compiler_path")
    configuration_sensitive = selection.get("configuration_sensitive")
    active_groups = selection.get("active_groups")
    component_paths = selection.get("component_paths")
    allowed_groups = {
        "core",
        *OPTIONAL_COMPONENT_GROUPS,
        *OPTIONAL_RUNTIME_DISTRIBUTION_GROUPS,
    }
    if (
        compiler_path
        not in {"network", "reference", "strategic-reference", "ea-recovery"}
        or not isinstance(configuration_sensitive, bool)
        or not isinstance(active_groups, list)
        or not active_groups
        or active_groups[0] != "core"
        or any(not isinstance(group, str) for group in active_groups)
        or active_groups != ["core", *sorted(active_groups[1:])]
        or len(active_groups) != len(set(active_groups))
        or not set(active_groups).issubset(allowed_groups)
        or not isinstance(component_paths, list)
        or any(not isinstance(path, str) for path in component_paths)
    ):
        raise ValueError("compilation dependency manifest selection is malformed")

    record_paths: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("compilation dependency manifest component is malformed")
        path = component.get("path")
        kind = component.get("kind")
        reason = component.get("reason")
        sha256 = component.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(kind, str)
            or not kind
            or not isinstance(reason, str)
            or not reason
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise ValueError("compilation dependency manifest component is malformed")
        if kind == "runtime-distribution" and not isinstance(
            component.get("version"), str
        ):
            raise ValueError("compilation dependency manifest runtime is malformed")
        record_paths.append(path)

    if (
        record_paths != sorted(record_paths)
        or len(record_paths) != len(set(record_paths))
        or component_paths != record_paths
    ):
        raise ValueError("compilation dependency manifest selection is stale")

    digest_payload = {
        "schema_version": manifest["schema_version"],
        "dependency_set_version": manifest["dependency_set_version"],
        "selection": selection,
        "components": components,
    }
    expected = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if manifest.get("sha256") != expected:
        raise ValueError("compilation dependency manifest digest is stale")
    return dict(manifest)
