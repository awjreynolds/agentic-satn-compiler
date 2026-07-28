"""Validate a Pages release using only the Python standard library.

This is deliberately a standalone script: the deployment runner checks out the
release tag, but does not install SATN's geospatial/compiler dependencies.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_RUNTIME_CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "satn_runtime_governance_contract",
    Path(__file__).parents[1] / "src" / "satn" / "runtime_governance_contract.py",
)
if _RUNTIME_CONTRACT_SPEC is None or _RUNTIME_CONTRACT_SPEC.loader is None:
    raise RuntimeError("cannot load runtime governance contract")
_RUNTIME_CONTRACT = importlib.util.module_from_spec(_RUNTIME_CONTRACT_SPEC)
_RUNTIME_CONTRACT_SPEC.loader.exec_module(_RUNTIME_CONTRACT)

GITHUB_PAGES_LIMIT_BYTES = 1_000_000_000
DEFAULT_MAXIMUM_BYTES = 900_000_000
SCHEMA_VERSION = "satn-deployment-catalogue/v1"
_ARTIFACTS = ("review_map", "network_map_pdf", "review_map_zip")
DISCLAIMER = "Experimental SATN POC — not an adopted plan."
LOCK_NAME = "provenance-lock.json"
LOCK_SCHEMA_VERSION = "satn-deployment-provenance-lock/v2"
CYCLIC_RUNTIME_FILES = frozenset({LOCK_NAME, "review-map.zip"})
ROOT_LOCK_NAME = "catalogue-lock.json"
ROOT_LOCK_SCHEMA_VERSION = "satn-pages-root-lock/v1"
MAX_NESTED_COMPRESSION_RATIO = 100
RUNTIME_GOVERNANCE_SCHEMA_VERSION = "satn-runtime-governance/v1"
REQUIRED_PRODUCTION_URBAN_EVIDENCE = (
    "official_main_road_spines",
    "urban_a_road_evidence_coverage",
)

# This is a release-policy trust anchor, not data supplied by a deployment.
# A person must add both digests after reviewing a real direct-runtime run.
# Empty by default makes a production Pages publication fail closed.
APPROVED_RUNTIME_CLASSES: frozenset[tuple[str, str]] = frozenset()

# This standalone verifier deliberately carries its own progressive-loading
# contract. It must validate a release without importing the SATN package or
# trusting a potentially forged manifest to define optional evidence types.
DEFERRED_GROUPS = {
    "urban": {"urban-spine", "urban-classification-unknown"},
    "low-traffic": {"low-traffic-area", "low-traffic-area-portal"},
    "schools": {"school", "school-street-assessment"},
    "amenities": {"retail-centre", "healthcare"},
}


@dataclass(frozen=True)
class PagesPackage:
    pages_directory: Path
    release_artifact: Path
    pages_size_bytes: int
    release_size_bytes: int


def _runtime_artifacts(deployment: Path) -> dict[str, dict[str, object]]:
    return {
        item.relative_to(deployment).as_posix(): {
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
            "size_bytes": item.stat().st_size,
        }
        for item in _files(deployment)
        if item.relative_to(deployment).as_posix() not in CYCLIC_RUNTIME_FILES
    }


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if quote:
            if character == quote and not escaped:
                quote = None
            escaped = character == "\\" and not escaped
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("YAML scalar must not be blank")
    if value[0] in "'\"":
        try:
            decoded = ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"invalid quoted YAML scalar: {value}") from error
        if not isinstance(decoded, str):
            raise ValueError("YAML scalar must be a string")
        return decoded
    return value


def _yaml_lines(path: Path) -> list[tuple[int, str]]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read tracked YAML: {path}") from error
    lines: list[tuple[int, str]] = []
    for raw in content.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent]:
            raise ValueError(f"tracked YAML must use spaces for indentation: {path}")
        lines.append((indent, line[indent:]))
    return lines


def _yaml_mapping_item(content: str) -> tuple[str, str]:
    if ":" not in content:
        raise ValueError(f"expected YAML mapping item: {content}")
    key, value = content.split(":", 1)
    if not key or key.strip() != key:
        raise ValueError(f"invalid YAML mapping key: {content}")
    return key, value.strip()


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[object, int]:
    if index >= len(lines) or lines[index][0] != indent:
        raise ValueError("invalid YAML indentation")
    is_list = lines[index][1].startswith("- ")
    result: list[object] | dict[str, object] = [] if is_list else {}
    while index < len(lines) and lines[index][0] == indent:
        _, content = lines[index]
        if content.startswith("- ") != is_list:
            raise ValueError("YAML must not mix lists and mappings at one indentation")
        if is_list:
            item = content[2:].strip()
            if not item:
                index += 1
                if index >= len(lines) or lines[index][0] <= indent:
                    raise ValueError("YAML list item must have a value")
                value, index = _parse_yaml_block(lines, index, lines[index][0])
            elif ":" not in item:
                value = _yaml_scalar(item)
                index += 1
            else:
                key, raw_value = _yaml_mapping_item(item)
                value = {key: _yaml_scalar(raw_value)} if raw_value else {key: None}
                index += 1
                if index < len(lines) and lines[index][0] > indent:
                    remainder, index = _parse_yaml_block(lines, index, lines[index][0])
                    if not isinstance(remainder, dict):
                        raise ValueError("YAML list mapping continuation must be a mapping")
                    value.update(remainder)
                if value[key] is None:
                    raise ValueError(f"YAML mapping value is required: {key}")
            result.append(value)
        else:
            key, raw_value = _yaml_mapping_item(content)
            index += 1
            if raw_value:
                result[key] = _yaml_scalar(raw_value)
            else:
                if index >= len(lines) or lines[index][0] <= indent:
                    raise ValueError(f"YAML mapping value is required: {key}")
                result[key], index = _parse_yaml_block(lines, index, lines[index][0])
    return result, index


def _load_simple_yaml(path: Path) -> dict[str, object]:
    lines = _yaml_lines(path)
    if not lines or lines[0][0] != 0:
        raise ValueError(f"tracked YAML must start with a mapping: {path}")
    parsed, index = _parse_yaml_block(lines, 0, 0)
    if index != len(lines) or not isinstance(parsed, dict):
        raise ValueError(f"tracked YAML must be a mapping: {path}")
    return parsed


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _relative_path(value: object, field: str, *, directory: bool = False) -> str:
    path = _text(value, field)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts or "\\" in path:
        raise ValueError(f"{field} must be a relative path without traversal")
    normalized = parsed.as_posix()
    if directory:
        if not path.endswith("/"):
            raise ValueError(f"{field} must end with /")
        return f"{normalized}/"
    if path.endswith("/"):
        raise ValueError(f"{field} must name a file")
    return normalized


def _tracked_file(path: Path, root: Path, field: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{field} must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{field} does not exist: {path}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{field} must be a file below the tracked deployments directory")
    return resolved


def _definition_publication_contract(
    definition: dict[str, object],
) -> tuple[str, dict[str, str], dict[str, object]]:
    """Read the small publication contract with no compiler dependencies."""
    publication = definition.get("publication")
    source = definition.get("source")
    compilation = definition.get("compilation", {})
    if not isinstance(publication, dict) or not isinstance(source, dict):
        raise ValueError("area_definition must contain publication and source mappings")
    if not isinstance(compilation, dict):
        raise ValueError("area_definition compilation must be a mapping")
    agent = compilation.get("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("area_definition compilation.agent must be a mapping")
    area_id = _text(definition.get("area_id"), "area_definition area_id")
    area_name = _text(definition.get("area_name"), "area_definition area_name")
    boundary_queries = source.get("osm_place_queries")
    if boundary_queries is None:
        single_query = source.get("osm_place_query")
        boundary_queries = [] if single_query is None else [_text(single_query, "osm_place_query")]
    if not isinstance(boundary_queries, list) or not all(
        isinstance(query, str) and query.strip() for query in boundary_queries
    ):
        raise ValueError("area_definition source boundary queries must be non-blank strings")
    scope = {
        "area_id": area_id,
        "area_name": area_name,
        "audience": _text(publication.get("audience", "public"), "publication audience"),
    }
    provenance: dict[str, object] = {
        "source": {
            "kind": _text(source.get("kind", "fixture"), "source kind"),
            "authority_boundary_queries": boundary_queries,
        },
        "snapshot": {"snapshot_id": _text(source.get("snapshot_id", "current"), "snapshot_id")},
        "agent_runtime": {
            "response_mode": _text(agent.get("response_mode", "caller"), "agent response_mode"),
            "provider": _text(agent.get("provider", "fake"), "agent provider"),
            "model": agent.get("model"),
        },
    }
    return _text(publication.get("title"), "publication title"), scope, provenance


def _load_expected_catalogue(catalogue_path: str | Path) -> dict[str, object]:
    catalogue_source = Path(catalogue_path)
    if catalogue_source.is_symlink():
        raise ValueError(f"deployment catalogue must not be a symlink: {catalogue_source}")
    catalogue = catalogue_source.resolve()
    deployments_root = catalogue.parent
    _tracked_file(catalogue_source, deployments_root, "deployment catalogue")
    tracked = _load_simple_yaml(catalogue)
    if tracked.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"deployment catalogue schema_version must be {SCHEMA_VERSION}")
    title = _text(tracked.get("title"), "deployment catalogue title")
    entries = tracked.get("deployments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("deployment catalogue must contain deployments")

    expected: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("each deployment catalogue entry must be a mapping")
        deployment_id = _text(raw_entry.get("deployment_id"), "deployment_id")
        if deployment_id in seen_ids:
            raise ValueError("deployment catalogue deployment_ids must be unique")
        seen_ids.add(deployment_id)
        area_id = _text(raw_entry.get("area_id"), "area_id")
        area_name = _text(raw_entry.get("area_name"), "area_name")
        area_definition = _relative_path(raw_entry.get("area_definition"), "area_definition")
        deployment_path = _relative_path(
            raw_entry.get("deployment_path"), "deployment_path", directory=True
        )
        if deployment_path != f"deployments/{deployment_id}/":
            raise ValueError("deployment_path must exactly match deployment_id")
        definition_path = _tracked_file(
            deployments_root / area_definition, deployments_root, "area_definition"
        )
        area_definition_sha256 = hashlib.sha256(definition_path.read_bytes()).hexdigest()
        definition = _load_simple_yaml(definition_path)
        if (
            definition.get("deployment_id") != deployment_id
            or definition.get("area_id") != area_id
            or definition.get("area_name") != area_name
        ):
            raise ValueError("area_definition identity must exactly match its catalogue entry")
        publication_title, scope, evidence_provenance = _definition_publication_contract(definition)
        lock_path = _tracked_file(
            definition_path.parent / LOCK_NAME, deployments_root, "deployment provenance lock"
        )
        lock = _json_object(lock_path, "deployment provenance lock")
        if (
            lock.get("schema_version") != LOCK_SCHEMA_VERSION
            or lock.get("deployment_id") != deployment_id
        ):
            raise ValueError("deployment provenance lock identity is invalid")
        if lock.get("area_id") != area_id or lock.get("area_name") != area_name:
            raise ValueError("deployment provenance lock area identity is invalid")
        if lock.get("title") != publication_title or lock.get("scope") != scope:
            raise ValueError("deployment provenance lock does not exactly match Area Definition")
        for field in (
            "area_definition_sha256",
            "snapshot_manifest_sha256",
            "governed_input_fingerprint",
            "decision_ledger_input_sha256",
            "decision_contract_sha256",
            "accepted_decisions_sha256",
            "compilation_input_fingerprint",
        ):
            _sha256(lock.get(field), f"deployment provenance lock {field}")
        if lock.get("cyclic_runtime_files") != sorted(CYCLIC_RUNTIME_FILES):
            raise ValueError("deployment provenance lock cyclic_runtime_files are invalid")
        raw_artifacts = raw_entry.get("artifacts")
        if not isinstance(raw_artifacts, dict) or set(raw_artifacts) != set(_ARTIFACTS):
            raise ValueError(f"artifacts must contain exactly: {', '.join(_ARTIFACTS)}")
        artifacts = {
            name: f"{deployment_path}{_relative_path(raw_artifacts[name], f'artifacts.{name}')}"
            for name in _ARTIFACTS
        }
        expected.append(
            {
                "deployment_id": deployment_id,
                "area_id": area_id,
                "area_name": area_name,
                "area_definition": area_definition,
                "area_definition_sha256": area_definition_sha256,
                "deployment_path": deployment_path,
                "artifacts": artifacts,
                "title": publication_title,
                "scope": scope,
                "evidence_provenance": evidence_provenance,
                "provenance_lock": lock,
            }
        )
    root_lock_path = _tracked_file(
        deployments_root / ROOT_LOCK_NAME, deployments_root, "Pages root catalogue lock"
    )
    root_lock = _json_object(root_lock_path, "Pages root catalogue lock")
    if root_lock.get("schema_version") != ROOT_LOCK_SCHEMA_VERSION:
        raise ValueError("Pages root catalogue lock schema_version is invalid")
    root_files = root_lock.get("root_files")
    if not isinstance(root_files, dict) or set(root_files) != {"index.html", "catalogue.json"}:
        raise ValueError("Pages root catalogue lock root_files are invalid")
    for name, metadata in root_files.items():
        if (
            not isinstance(metadata, dict)
            or _sha256(metadata.get("sha256"), f"Pages root catalogue lock {name} sha256")
            != metadata.get("sha256")
            or not isinstance(metadata.get("size_bytes"), int)
            or metadata["size_bytes"] < 0
        ):
            raise ValueError("Pages root catalogue lock root file metadata is invalid")
    expected_roots = [f"deployments/{entry['deployment_id']}" for entry in expected]
    if root_lock.get("deployment_roots") != expected_roots:
        raise ValueError("Pages root catalogue lock deployment_roots are invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "title": title,
        "deployments": expected,
        "root_lock": root_lock,
    }


def _files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for root, directory_names, file_names in os.walk(directory, followlinks=False):
        current = Path(root)
        for name in [*directory_names, *file_names]:
            item = current / name
            if item.is_symlink():
                raise ValueError(f"Pages package must not contain symlinks: {item}")
        for name in file_names:
            item = current / name
            if not item.is_file():
                raise ValueError(f"Pages package must contain only regular files: {item}")
            files.append(item)
    return files


def _json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"invalid {description}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object: {path}")
    return value


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _canonical_decision_ledger(value: object, field: str) -> dict[str, object]:
    """Validate the wire form without normalising a persisted decision record."""
    if not isinstance(value, dict) or set(value) != {"decision_contract", "responses"}:
        raise ValueError(f"{field} must be an exact decision ledger object")
    if value.get("decision_contract") != "agent-decision-menu/v1":
        raise ValueError(f"{field} has an unsupported decision contract")
    responses = value.get("responses")
    if not isinstance(responses, list):
        raise ValueError(f"{field}.responses must be a list")
    request_ids: list[str] = []
    for response in responses:
        if not isinstance(response, dict) or set(response) != {
            "request_id", "dependency_fingerprint", "choice_id"
        }:
            raise ValueError(f"{field}.responses must contain exact response objects")
        request_id = response.get("request_id")
        fingerprint = response.get("dependency_fingerprint")
        choice_id = response.get("choice_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError(f"{field}.responses request_id is invalid")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError(f"{field}.responses dependency_fingerprint is invalid")
        if not isinstance(choice_id, str) or (
            choice_id != "terminate"
            and (
                not choice_id.isascii()
                or not choice_id.isdigit()
                or choice_id.startswith("0")
            )
        ):
            raise ValueError(f"{field}.responses choice_id is invalid")
        request_ids.append(request_id)
    if request_ids != sorted(request_ids) or len(request_ids) != len(set(request_ids)):
        raise ValueError(f"{field} must be in canonical request_id order")
    return value


def _relative_file_path(value: object, field: str) -> Path:
    return Path(_relative_path(value, field))


def _feature_collection(path: Path, field: str) -> list[dict[str, Any]]:
    payload = _json_object(path, field)
    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ValueError(f"{field} must be a GeoJSON FeatureCollection")
    if not all(isinstance(feature, dict) for feature in features):
        raise ValueError(f"{field} contains an invalid GeoJSON feature")
    return features


def _coordinates(geometry: object) -> list[tuple[float, float]]:
    if not isinstance(geometry, dict):
        return []
    values: list[tuple[float, float]] = []

    def visit(item: object) -> None:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and all(isinstance(value, (int, float)) for value in item[:2])
        ):
            values.append((float(item[0]), float(item[1])))
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(geometry.get("coordinates"))
    return values


def _bbox(features: list[dict[str, Any]]) -> list[float] | None:
    coordinates = [
        coordinate for feature in features for coordinate in _coordinates(feature.get("geometry"))
    ]
    if not coordinates:
        return None
    return [
        min(value[0] for value in coordinates),
        min(value[1] for value in coordinates),
        max(value[0] for value in coordinates),
        max(value[1] for value in coordinates),
    ]


def _validate_shard_entries(
    deployment: Path, entries: object, *, directory: str, field: str
) -> tuple[int, int, set[Path]]:
    if not isinstance(entries, list):
        raise ValueError(f"{field} must be a list")
    total_features = total_bytes = 0
    paths: set[Path] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{field}[{index}] must be an object")
        path = _relative_file_path(entry.get("path"), f"{field}[{index}].path")
        if path.parent.as_posix() != directory:
            raise ValueError(f"{field}[{index}].path must be rooted at {directory}/")
        if path in paths:
            raise ValueError(f"{field} contains a duplicate shard path")
        paths.add(path)
        shard = deployment / path
        if shard.is_symlink() or not shard.is_file():
            raise ValueError(f"{field}[{index}] shard is missing or unsafe: {path}")
        contents = shard.read_bytes()
        digest = _sha256(entry.get("sha256"), f"{field}[{index}].sha256")
        if hashlib.sha256(contents).hexdigest() != digest:
            raise ValueError(f"{field}[{index}] shard content hash does not match: {path}")
        if shard.name != f"{shard.stem.rsplit('-', 1)[0]}-{digest[:16]}.geojson":
            raise ValueError(f"{field}[{index}] shard filename is not content-addressed")
        if entry.get("size_bytes") != len(contents):
            raise ValueError(f"{field}[{index}] shard size does not match: {path}")
        features = _feature_collection(shard, f"{field}[{index}] shard")
        if entry.get("feature_count") != len(features):
            raise ValueError(f"{field}[{index}] feature_count does not match: {path}")
        if entry.get("bbox") != _bbox(features):
            raise ValueError(f"{field}[{index}] bbox does not match: {path}")
        total_features += len(features)
        total_bytes += len(contents)
    return total_features, total_bytes, paths


def _validate_progressive_manifests(deployment: Path, publication: dict[str, Any]) -> None:
    layer_path = deployment / _relative_file_path(
        publication.get("layer_manifest"), "layer_manifest"
    )
    topo_path = deployment / _relative_file_path(
        publication.get("topography_manifest"), "topography_manifest"
    )
    layers = _json_object(layer_path, "layer manifest")
    topography = _json_object(topo_path, "topography manifest")
    groups = layers.get("groups")
    if not isinstance(groups, dict):
        raise ValueError("layer manifest groups must be an object")
    if set(groups) != set(DEFERRED_GROUPS):
        raise ValueError("layer manifest groups must exactly match canonical deferred groups")
    seen_paths: set[Path] = set()
    for name, group in groups.items():
        if not isinstance(name, str) or not isinstance(group, dict):
            raise ValueError("layer manifest groups must contain named objects")
        features, size, paths = _validate_shard_entries(
            deployment, group.get("shards"), directory="layers", field=f"layer groups.{name}.shards"
        )
        if group.get("feature_count") != features or group.get("size_bytes") != size:
            raise ValueError(f"layer group {name} aggregate counts do not match shards")
        types = group.get("types")
        if not isinstance(types, dict) or not all(
            isinstance(feature_type, str) and feature_type.strip()
            and isinstance(metadata, dict)
            for feature_type, metadata in types.items()
        ):
            raise ValueError(f"layer group {name} types must be named objects")
        expected_types = DEFERRED_GROUPS[name]
        if set(types) != expected_types:
            raise ValueError(
                f"layer group {name} types must exactly match canonical feature types"
            )
        declared_types = group.get("feature_types")
        if declared_types != sorted(expected_types):
            raise ValueError(
                f"layer group {name} feature_types must exactly name canonical sorted types"
            )
        group_path_order = [
            _relative_file_path(entry.get("path"), f"layer groups.{name}.shards[{index}].path")
            for index, entry in enumerate(group["shards"])
            if isinstance(entry, dict)
        ]
        if len(group_path_order) != len(group["shards"]):
            raise ValueError(f"layer groups.{name}.shards must contain objects")
        typed_features = typed_size = 0
        typed_paths: set[Path] = set()
        typed_path_order: list[Path] = []
        for feature_type in sorted(types):
            metadata = types[feature_type]
            assert isinstance(metadata, dict)
            type_features, type_size, type_paths = _validate_shard_entries(
                deployment,
                metadata.get("shards"),
                directory="layers",
                field=f"layer groups.{name}.types.{feature_type}.shards",
            )
            if (
                metadata.get("feature_count") != type_features
                or metadata.get("size_bytes") != type_size
            ):
                raise ValueError(
                    f"layer group {name} type {feature_type} aggregate counts do not match shards"
                )
            if typed_paths.intersection(type_paths):
                raise ValueError("typed layer shards must be assigned exactly once")
            typed_paths.update(type_paths)
            type_path_order = [
                _relative_file_path(
                    entry.get("path"),
                    f"layer groups.{name}.types.{feature_type}.shards[{index}].path",
                )
                for index, entry in enumerate(metadata["shards"])
                if isinstance(entry, dict)
            ]
            if len(type_path_order) != len(metadata["shards"]):
                raise ValueError(
                    f"layer group {name} type {feature_type} shards must contain objects"
                )
            typed_path_order.extend(type_path_order)
            typed_features += type_features
            typed_size += type_size
            for path in type_path_order:
                for feature in _feature_collection(
                    deployment / path,
                    f"layer group {name} type {feature_type} shard",
                ):
                    properties = feature.get("properties")
                    if (
                        not isinstance(properties, dict)
                        or properties.get("feature_type") != feature_type
                    ):
                        raise ValueError(
                            f"layer group {name} type {feature_type} shard contains "
                            "another feature type"
                        )
        if typed_features != features or typed_size != size:
            raise ValueError(f"layer group {name} type totals do not match group totals")
        if typed_paths != paths or typed_path_order != group_path_order:
            raise ValueError(
                f"layer group {name} typed shards must exactly match group shards in order"
            )
        if seen_paths.intersection(paths):
            raise ValueError("progressive manifests contain a duplicate shard path")
        seen_paths.update(paths)
    actual_layers = (
        {item.relative_to(deployment) for item in _files(deployment / "layers")}
        if (deployment / "layers").is_dir()
        else set()
    )
    referenced_layers = {path for path in seen_paths if path.parent.as_posix() == "layers"}
    if actual_layers != referenced_layers:
        raise ValueError("layer manifest must reference every and only packaged layer shard")
    for name in ("overview", "detail"):
        features, size, paths = _validate_shard_entries(
            deployment, topography.get(name), directory="topography", field=f"topography.{name}"
        )
        if (
            topography.get(f"{name}_feature_count") != features
            or topography.get(f"{name}_size_bytes") != size
        ):
            raise ValueError(f"topography {name} aggregate counts do not match shards")
        if seen_paths.intersection(paths):
            raise ValueError("progressive manifests contain a duplicate shard path")
        seen_paths.update(paths)
    actual_topography = (
        {item.relative_to(deployment) for item in _files(deployment / "topography")}
        if (deployment / "topography").is_dir()
        else set()
    )
    referenced_topography = {path for path in seen_paths if path.parent.as_posix() == "topography"}
    if actual_topography != referenced_topography:
        raise ValueError("topography manifest must reference every and only packaged shard")
    profile_index = _json_object(
        deployment
        / _relative_file_path(
            publication.get("topography_profile_evidence_index"),
            "topography_profile_evidence_index",
        ),
        "topography profile evidence index",
    )
    chunks = profile_index.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("topography profile evidence chunks must be a list")
    profile_count = 0
    profile_ids: set[str] = set()
    for index, chunk in enumerate(chunks):
        features, _size, paths = _validate_shard_entries(
            deployment, [chunk], directory="evidence", field=f"profile evidence chunks[{index}]"
        )
        if seen_paths.intersection(paths):
            raise ValueError("progressive manifests contain a duplicate shard path")
        seen_paths.update(paths)
        ids = chunk.get("profile_ids") if isinstance(chunk, dict) else None
        if (
            not isinstance(ids, list)
            or chunk.get("profile_count") != features
            or len(ids) != features
        ):
            raise ValueError(
                "profile evidence chunk profile_count/profile_ids do not match content"
            )
        shard_features = _feature_collection(
            deployment / _relative_file_path(chunk.get("path"), "profile evidence chunk path"),
            "profile evidence chunk",
        )
        actual_ids: list[str] = []
        for feature in shard_features:
            feature_id = feature.get("id")
            properties = feature.get("properties")
            profile_id = properties.get("profile_id") if isinstance(properties, dict) else None
            if (
                not isinstance(feature_id, str)
                or not isinstance(profile_id, str)
                or feature_id != profile_id
            ):
                raise ValueError("profile evidence feature id and properties.profile_id must match")
            actual_ids.append(profile_id)
        if (
            ids != actual_ids
            or len(set(actual_ids)) != len(actual_ids)
            or profile_ids.intersection(actual_ids)
        ):
            raise ValueError("profile evidence profile_ids must uniquely match shard feature ids")
        profile_ids.update(actual_ids)
        profile_count += features
    if profile_index.get("profile_count") != profile_count:
        raise ValueError("profile evidence index profile_count does not match chunks")
    actual_evidence = (
        {item.relative_to(deployment) for item in _files(deployment / "evidence")}
        if (deployment / "evidence").is_dir()
        else set()
    )
    referenced_evidence = {path for path in seen_paths if path.parent.as_posix() == "evidence"}
    if actual_evidence != referenced_evidence:
        raise ValueError(
            "profile evidence index must reference every and only packaged evidence shard"
        )


def _data_payload(path: Path) -> dict[str, Any]:
    prefix = "window.SATN_DATA = "
    source = path.read_text(encoding="utf-8")
    if not source.startswith(prefix) or not source.endswith(";\n"):
        raise ValueError("generated data.js has an unsupported format")
    payload = json.loads(source.removeprefix(prefix).removesuffix(";\n"))
    if not isinstance(payload, dict):
        raise ValueError("generated data.js payload must be an object")
    return payload


def _assert_production_runtime_governance(
    publication: dict[str, Any],
    compiler_run: dict[str, Any],
    expected_lock: dict[str, Any],
    *,
    decision_ledger_input: dict[str, object],
    accepted_decisions: list[object],
) -> None:
    """Require independently trusted runtime evidence for a Pages publication.

    This standalone verifier cannot import the SATN package.  It intentionally
    carries the same empty-by-default, source-controlled policy anchor as the
    packager.  A self-consistent manifest is insufficient: both immutable
    digests must match a reviewed pair in ``APPROVED_RUNTIME_CLASSES``.
    """

    runtime_governance = compiler_run.get("runtime_governance")
    if not isinstance(runtime_governance, dict):
        raise ValueError("production promotion denied: compiler run has no runtime governance")
    if publication.get("runtime_governance") != runtime_governance:
        raise ValueError(
            "production promotion denied: publication runtime governance differs from compiler run"
        )
    runtime_governance_sha256 = hashlib.sha256(
        json.dumps(runtime_governance, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if expected_lock.get("runtime_governance_sha256") != runtime_governance_sha256:
        raise ValueError(
            "production promotion denied: runtime governance is not bound by provenance lock"
        )
    promotion = runtime_governance.get("promotion")
    runtime_class_sha256 = runtime_governance.get("runtime_class_sha256")
    ledger_provenance_sha256 = runtime_governance.get("decision_ledger_provenance_sha256")
    if (
        runtime_governance.get("schema_version") != RUNTIME_GOVERNANCE_SCHEMA_VERSION
        or runtime_governance.get("status") != "production-approved"
        or not isinstance(promotion, dict)
        or promotion.get("allowed") is not True
        or not isinstance(runtime_class_sha256, str)
        or not isinstance(ledger_provenance_sha256, str)
    ):
        raise ValueError("production promotion denied: runtime governance is not approved")
    _sha256(runtime_class_sha256, "runtime governance runtime_class_sha256")
    _sha256(
        ledger_provenance_sha256,
        "runtime governance decision_ledger_provenance_sha256",
    )
    try:
        recomputed_runtime, recomputed_ledger = (
            _RUNTIME_CONTRACT.assert_declared_runtime_governance_digests(
                runtime_governance,
                decision_contract=compiler_run.get("decision_contract"),
                decision_ledger_input=decision_ledger_input,
                accepted_decisions=accepted_decisions,
            )
        )
    except ValueError as error:
        raise ValueError(f"production promotion denied: {error}") from error
    if (runtime_class_sha256, ledger_provenance_sha256) != (
        recomputed_runtime,
        recomputed_ledger,
    ):
        raise ValueError("production promotion denied: runtime governance digests differ")
    if (runtime_class_sha256, ledger_provenance_sha256) not in APPROVED_RUNTIME_CLASSES:
        raise ValueError(
            "production promotion denied: no approved immutable runtime class and "
            "decision-ledger provenance match this publication"
        )


def _assert_required_production_urban_evidence(
    publication: dict[str, Any],
    compiler_run: dict[str, Any],
) -> None:
    scope = publication.get("scope")
    if not isinstance(scope, dict) or scope.get("audience") != "public":
        return
    criteria = compiler_run.get("criteria")
    urban = criteria.get("urban_network") if isinstance(criteria, dict) else None
    blockers = [
        f"{criterion}={urban.get(criterion, 'missing') if isinstance(urban, dict) else 'missing'}"
        for criterion in REQUIRED_PRODUCTION_URBAN_EVIDENCE
        if not isinstance(urban, dict) or urban.get(criterion) != "green"
    ]
    if blockers:
        raise ValueError(
            "production promotion denied: incomplete required urban evidence: "
            + ", ".join(blockers)
        )


def _validate_pages_directory(
    pages: Path,
    expected_catalogue: dict[str, object],
    maximum_bytes: int,
    *,
    require_production_governance: bool = False,
) -> int:
    pages_size = sum(item.stat().st_size for item in _files(pages))
    if pages_size > maximum_bytes:
        raise ValueError(
            "Pages package is "
            f"{pages_size} bytes, exceeding configured budget of {maximum_bytes} bytes"
        )
    if not (pages / "index.html").is_file():
        raise ValueError("Pages package must contain root index.html")
    actual_catalogue = _json_object(pages / "catalogue.json", "Pages catalogue")
    public_expected_catalogue = {
        key: value for key, value in expected_catalogue.items() if key != "root_lock"
    } | {
        "deployments": [
            {key: value for key, value in entry.items() if key != "provenance_lock"}
            for entry in expected_catalogue["deployments"]
            if isinstance(entry, dict)
        ],
    }
    if actual_catalogue != public_expected_catalogue:
        raise ValueError("Pages catalogue does not exactly match the tracked deployment catalogue")
    for entry in expected_catalogue["deployments"]:
        assert isinstance(entry, dict)  # Constructed above; narrows for type checkers.
        deployment_id = entry["deployment_id"]
        area_id = entry["area_id"]
        area_definition_sha256 = entry["area_definition_sha256"]
        title = entry["title"]
        scope = entry["scope"]
        expected_evidence_provenance = entry["evidence_provenance"]
        expected_lock = entry["provenance_lock"]
        assert (
            isinstance(deployment_id, str)
            and isinstance(area_id, str)
            and isinstance(area_definition_sha256, str)
            and isinstance(title, str)
            and isinstance(scope, dict)
            and isinstance(expected_evidence_provenance, dict)
            and isinstance(expected_lock, dict)
        )
        deployment = pages / f"deployments/{deployment_id}"
        if deployment.is_symlink() or not deployment.is_dir():
            raise ValueError(f"Pages catalogue deployment is missing: {deployment}")
        publication = _json_object(deployment / "publication.json", "deployment publication")
        if (
            publication.get("deployment_id") != deployment_id
            or publication.get("area_id") != area_id
        ):
            raise ValueError(f"deployment publication identity does not match {deployment_id}")
        if publication.get("disclaimer") != DISCLAIMER:
            raise ValueError(f"deployment publication disclaimer does not match {deployment_id}")
        if publication.get("area_definition_sha256") != area_definition_sha256:
            raise ValueError(
                "deployment publication area_definition_sha256 does not match the "
                f"tracked Area Definition: {deployment_id}"
            )
        if publication.get("title") != title or publication.get("scope") != scope:
            raise ValueError(f"deployment publication contract does not match {deployment_id}")
        for field in (
            "area_id",
            "area_name",
            "title",
            "scope",
            "area_definition_sha256",
            "run_id",
            "status",
            "criteria",
            "layer_counts",
        ):
            if publication.get(field) != expected_lock.get(field):
                raise ValueError(
                    f"deployment publication {field} does not match tracked provenance lock"
                )
        provenance = publication.get("evidence_provenance")
        if not isinstance(provenance, dict):
            raise ValueError(
                f"deployment publication evidence_provenance is invalid: {deployment_id}"
            )
        for key, expected in expected_evidence_provenance.items():
            actual = provenance.get(key)
            matches = (
                isinstance(expected, dict)
                and isinstance(actual, dict)
                and all(
                    actual.get(nested_key) == nested_expected
                    for nested_key, nested_expected in expected.items()
                )
            ) or actual == expected
            if not matches:
                raise ValueError(
                    "deployment publication evidence_provenance does not match "
                    f"{deployment_id} at {key}"
                )
        snapshot = provenance.get("snapshot")
        run = provenance.get("run")
        if (
            not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("manifest_sha256"), str)
            or _sha256(snapshot["manifest_sha256"], "snapshot manifest_sha256")
            != snapshot["manifest_sha256"]
            or not isinstance(run, dict)
            or run.get("run_id") != publication.get("run_id")
            or run.get("status") != publication.get("status")
        ):
            raise ValueError(
                "deployment publication evidence_provenance run/snapshot identity is invalid: "
                f"{deployment_id}"
            )
        if publication.get("status") not in {"complete", "reviewable"}:
            raise ValueError(f"deployment publication status is not publishable: {deployment_id}")
        compiler_run = _json_object(
            deployment / _relative_file_path(publication.get("compiler_run"), "compiler_run"),
            "compiler run",
        )
        input_ledger = _canonical_decision_ledger(
            compiler_run.get("decision_ledger_input"),
            "compiler run decision_ledger_input",
        )
        accepted_ledger = _canonical_decision_ledger(
            {
                "decision_contract": compiler_run.get("decision_contract"),
                "responses": compiler_run.get("accepted_decisions"),
            },
            "compiler run accepted_decisions",
        )
        if input_ledger["decision_contract"] != compiler_run.get("decision_contract") or (
            accepted_ledger["responses"] != compiler_run.get("accepted_decisions")
        ):
            raise ValueError("compiler run decision provenance contract is inconsistent")
        for field in ("run_id", "status", "compilation_input_fingerprint"):
            if compiler_run.get(field) != publication.get(field):
                raise ValueError(f"compiler run {field} does not match deployment publication")
        if publication.get("compilation_input_fingerprint") != expected_lock.get(
            "compilation_input_fingerprint"
        ):
            raise ValueError(
                "deployment publication compilation_input_fingerprint does not match "
                "tracked provenance lock"
            )
        if compiler_run.get("disclaimer") != DISCLAIMER:
            raise ValueError("compiler run disclaimer does not match")
        if compiler_run.get("snapshot_manifest_sha256") != snapshot["manifest_sha256"]:
            raise ValueError("compiler run snapshot digest does not match evidence provenance")
        if compiler_run.get("area_definition_sha256") != area_definition_sha256:
            raise ValueError(
                "compiler run area definition digest does not match tracked definition"
            )
        for field in (
            "run_id",
            "status",
            "area_definition_sha256",
            "snapshot_manifest_sha256",
            "governed_input_fingerprint",
            "compilation_input_fingerprint",
        ):
            if compiler_run.get(field) != expected_lock.get(field):
                raise ValueError(f"compiler run {field} does not match tracked provenance lock")
        for lock_field, run_field in (
            ("decision_ledger_input_sha256", "decision_ledger_input"),
            ("decision_contract_sha256", "decision_contract"),
            ("accepted_decisions_sha256", "accepted_decisions"),
        ):
            digest = hashlib.sha256(
                json.dumps(
                    compiler_run.get(run_field), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if digest != expected_lock.get(lock_field):
                raise ValueError(f"compiler run {run_field} does not match tracked provenance lock")
        copied_lock = _json_object(deployment / LOCK_NAME, "public deployment provenance lock")
        if copied_lock != expected_lock:
            raise ValueError("public deployment provenance lock does not match tracked lock")
        if require_production_governance:
            _assert_required_production_urban_evidence(publication, compiler_run)
            _assert_production_runtime_governance(
                publication,
                compiler_run,
                expected_lock,
                decision_ledger_input=input_ledger,
                accepted_decisions=accepted_ledger["responses"],
            )
        artifacts_lock = expected_lock.get("artifacts")
        if not isinstance(artifacts_lock, dict):
            raise ValueError("tracked provenance lock artifacts are invalid")
        if (
            publication.get("connection_count") != expected_lock.get("connection_count")
            or publication.get("gap_count") != expected_lock.get("gap_count")
        ):
            raise ValueError("deployment publication counts do not match tracked provenance lock")
        _sha256(publication.get("compilation_input_fingerprint"), "compilation_input_fingerprint")
        data = _data_payload(deployment / "data.js")
        public_data_fields = (
            "title",
            "area_id",
            "area_name",
            "scope",
            "evidence_provenance",
            "run_id",
            "status",
            "area_definition_sha256",
            "compilation_input_fingerprint",
            "criteria",
            "layer_counts",
            "connection_count",
            "gap_count",
            "disclaimer",
        )
        if any(data.get(field) != publication.get(field) for field in public_data_fields):
            raise ValueError("generated data.js does not match the validated publication contract")
        for field, expected in (
            ("network_url", "network.geojson"),
            ("layer_manifest_url", "layer-manifest.json"),
            ("topography_manifest_url", "topography-manifest.json"),
            ("profile_evidence_index_url", "topography-profile-evidence.json"),
        ):
            if data.get(field) != expected:
                raise ValueError(f"generated data.js {field} must be {expected}")
        expected_data_lock = {
            key: expected_lock[key]
            for key in (
                "schema_version", "deployment_id", "run_id", "status", "area_definition_sha256",
                "snapshot_manifest_sha256", "compilation_input_fingerprint",
            )
        }
        if data.get("provenance_lock") != expected_data_lock:
            raise ValueError(
                "generated data.js provenance_lock does not match tracked provenance lock"
            )
        _validate_progressive_manifests(deployment, publication)
        if artifacts_lock != _runtime_artifacts(deployment):
            raise ValueError("tracked provenance lock artifacts are invalid")
        _validate_review_map_zip(deployment)
        artifacts = entry["artifacts"]
        assert isinstance(artifacts, dict)
        for name, artifact in artifacts.items():
            assert isinstance(artifact, str)
            target = pages / artifact
            if target.is_symlink() or not target.is_file():
                raise ValueError(
                    f"Pages catalogue deployment {deployment_id} is missing {name}: {artifact}"
                )
    root_lock = expected_catalogue.get("root_lock")
    if not isinstance(root_lock, dict):
        raise ValueError("Pages root catalogue lock is invalid")
    root_files = root_lock.get("root_files")
    if not isinstance(root_files, dict):
        raise ValueError("Pages root catalogue lock root_files are invalid")
    expected_files = set(root_files)
    for name, metadata in root_files.items():
        assert isinstance(name, str) and isinstance(metadata, dict)
        path = pages / name
        if not path.is_file() or path.is_symlink() or metadata != {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }:
            raise ValueError(f"Pages root file does not match tracked catalogue lock: {name}")
    for entry in expected_catalogue["deployments"]:
        assert isinstance(entry, dict)
        deployment_id = entry["deployment_id"]
        assert isinstance(deployment_id, str)
        lock = entry["provenance_lock"]
        assert isinstance(lock, dict)
        artifacts = lock.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("tracked provenance lock artifacts are invalid")
        for name in artifacts:
            path = _relative_path(name, "provenance lock artifact")
            expected_files.add(f"deployments/{deployment_id}/{path}")
        expected_files.update(
            {
                f"deployments/{deployment_id}/{LOCK_NAME}",
                f"deployments/{deployment_id}/review-map.zip",
            }
        )
    actual_files = {item.relative_to(pages).as_posix() for item in _files(pages)}
    if actual_files != expected_files:
        raise ValueError("Pages package has undeclared or missing files")
    expected_directories = {"deployments"}
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    actual_directories: set[str] = set()
    for root, directory_names, _ in os.walk(pages, followlinks=False):
        current = Path(root)
        for name in directory_names:
            item = current / name
            if item.is_symlink():
                raise ValueError(f"Pages package must not contain symlinks: {item}")
            actual_directories.add(item.relative_to(pages).as_posix())
    if actual_directories != expected_directories:
        raise ValueError("Pages package has undeclared deployment directories")
    return pages_size


def _validate_review_map_zip(deployment: Path) -> None:
    archive_path = deployment / "review-map.zip"
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("deployment is missing review-map.zip")
    expected = {
        item.relative_to(deployment).as_posix(): item
        for item in _files(deployment)
        if item.relative_to(deployment).as_posix() != "review-map.zip"
    }
    with zipfile.ZipFile(archive_path) as archive:
        actual: dict[str, zipfile.ZipInfo] = {}
        declared_size = 0
        for info in archive.infolist():
            name = info.filename
            parsed = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (
                not name.startswith("review-map/")
                or "\\" in name
                or parsed.is_absolute()
                or ".." in parsed.parts
                or "." in parsed.parts
                or stat.S_ISLNK(mode)
                or (not info.is_dir() and stat.S_IFMT(mode) and not stat.S_ISREG(mode))
            ):
                raise ValueError(f"review-map.zip contains unsafe member: {name!r}")
            if info.is_dir():
                raise ValueError("review-map.zip must contain only regular file members")
            relative = name.removeprefix("review-map/")
            if not relative or relative in actual:
                raise ValueError("review-map.zip contains duplicate or invalid members")
            expected_path = expected.get(relative)
            if expected_path is None or info.file_size != expected_path.stat().st_size:
                raise ValueError("review-map.zip does not exactly mirror the locked deployment")
            if info.file_size and (
                not info.compress_size
                or info.file_size > info.compress_size * MAX_NESTED_COMPRESSION_RATIO
            ):
                raise ValueError("review-map.zip contains an unsafe high-compression member")
            declared_size += info.file_size
            actual[relative] = info
        if set(actual) != set(expected) or declared_size != sum(
            item.stat().st_size for item in expected.values()
        ) or declared_size > DEFAULT_MAXIMUM_BYTES:
            raise ValueError("review-map.zip does not exactly mirror the locked deployment")
        for relative, info in actual.items():
            expected_path = expected[relative]
            with archive.open(info) as source, expected_path.open("rb") as target:
                while True:
                    archived = source.read(64 * 1024)
                    locked = target.read(64 * 1024)
                    if archived != locked:
                        raise ValueError(
                            "review-map.zip does not exactly mirror the locked deployment"
                        )
                    if not archived:
                        break
    if set(actual) != set(expected):
        raise ValueError("review-map.zip does not exactly mirror the locked deployment")


def _safe_zip_member(info: zipfile.ZipInfo) -> Path:
    name = info.filename
    parsed = PurePosixPath(name)
    mode = info.external_attr >> 16
    if (
        not name
        or "\\" in name
        or parsed.is_absolute()
        or ".." in parsed.parts
        or "." in parsed.parts
    ):
        raise ValueError(f"release archive contains unsafe path: {name!r}")
    if stat.S_ISLNK(mode):
        raise ValueError(f"release archive contains symlink: {name}")
    if not info.is_dir() and stat.S_IFMT(mode) and not stat.S_ISREG(mode):
        raise ValueError(f"release archive contains non-regular file: {name}")
    return Path(*parsed.parts)


def validate_pages_release(
    release_artifact: str | Path,
    destination: str | Path,
    catalogue_path: str | Path,
    *,
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
    allow_non_production: bool = False,
) -> PagesPackage:
    """Safely extract and validate a release against the checked-out release tag."""

    if maximum_bytes <= 0 or maximum_bytes >= GITHUB_PAGES_LIMIT_BYTES:
        raise ValueError("maximum_bytes must be positive and below the GitHub Pages 1 GB limit")
    release_source = Path(release_artifact)
    output_source = Path(destination)
    if release_source.is_symlink():
        raise ValueError(f"Pages release archive must not be a symlink: {release_source}")
    if output_source.is_symlink():
        raise ValueError(f"Pages validation destination must not be a symlink: {output_source}")
    release = release_source.resolve()
    output = output_source.resolve()
    if not release.is_file():
        raise ValueError(f"expected Pages release archive: {release}")
    if output.exists():
        raise ValueError(f"Pages validation destination must not already exist: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary_root = Path(tempfile.mkdtemp(prefix=".pages-validate-", dir=output.parent))
    try:
        pages = temporary_root / "pages"
        pages.mkdir()
        with zipfile.ZipFile(release) as archive:
            members = archive.infolist()
            member_paths: set[Path] = set()
            declared_size = 0
            for info in members:
                member_path = _safe_zip_member(info)
                if member_path in member_paths:
                    raise ValueError(f"release archive contains duplicate path: {info.filename}")
                member_paths.add(member_path)
                if not info.is_dir():
                    declared_size += info.file_size
            if declared_size > maximum_bytes:
                raise ValueError(
                    "release archive extracted size exceeds configured budget: "
                    f"{declared_size} bytes"
                )
            expected_catalogue = _load_expected_catalogue(catalogue_path)
            for info in members:
                member_path = _safe_zip_member(info)
                target = pages / member_path
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("xb") as extracted:
                        shutil.copyfileobj(source, extracted)
        pages_size = _validate_pages_directory(
            pages,
            expected_catalogue,
            maximum_bytes,
            require_production_governance=not allow_non_production,
        )
        pages.replace(output)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
    return PagesPackage(output, release, pages_size, release.stat().st_size)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_artifact", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--catalogue", type=Path, required=True, help="Tracked deployments/catalogue.yaml"
    )
    parser.add_argument(
        "--maximum-bytes",
        type=int,
        default=int(os.environ.get("SATN_PAGES_MAX_BYTES", DEFAULT_MAXIMUM_BYTES)),
    )
    parser.add_argument(
        "--allow-non-production",
        action="store_true",
        help="Explicitly allow validation of a local, non-production Pages package.",
    )
    args = parser.parse_args()
    result = validate_pages_release(
        args.release_artifact,
        args.destination,
        args.catalogue,
        maximum_bytes=args.maximum_bytes,
        allow_non_production=args.allow_non_production,
    )
    print(f"{result.pages_directory} ({result.pages_size_bytes} extracted bytes)")


if __name__ == "__main__":
    main()
