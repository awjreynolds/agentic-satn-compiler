"""Package generated Area Deployments for Pages without tracking them in Git."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from satn.deployment import DEFERRED_GROUPS

if TYPE_CHECKING:
    from satn.deployment_catalogue import DeploymentCatalogue

GITHUB_PAGES_LIMIT_BYTES = 1_000_000_000
DEFAULT_MAXIMUM_BYTES = 900_000_000
RELEASE_ARTIFACT_NAME = "satn-pages.zip"
SCHEMA_VERSION = "satn-deployment-catalogue/v1"
DISCLAIMER = "Experimental SATN POC — not an adopted plan."
LOCK_NAME = "provenance-lock.json"
LOCK_SCHEMA_VERSION = "satn-deployment-provenance-lock/v2"
CYCLIC_RUNTIME_FILES = frozenset({LOCK_NAME, "review-map.zip"})
MAX_NESTED_COMPRESSION_RATIO = 100
REQUIRED_PRODUCTION_URBAN_EVIDENCE = (
    "official_main_road_spines",
    "urban_a_road_evidence_coverage",
)


@dataclass(frozen=True)
class PagesPackage:
    """Locations and sizes of one generated Pages release."""

    pages_directory: Path
    release_artifact: Path
    pages_size_bytes: int
    release_size_bytes: int


def _deployment_destination(deployment_id: str) -> Path:
    return Path("deployments") / deployment_id


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


def _files(directory: Path) -> list[Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"expected generated deployment directory: {directory}")
    files: list[Path] = []
    for root, directory_names, file_names in os.walk(directory, followlinks=False):
        current = Path(root)
        for name in [*directory_names, *file_names]:
            item = current / name
            if item.is_symlink():
                raise ValueError(f"generated deployment must not contain symlinks: {item}")
        for name in file_names:
            item = current / name
            if not item.is_file():
                raise ValueError(f"generated deployment must contain only regular files: {item}")
            files.append(item)
    return sorted(files, key=lambda item: item.relative_to(directory).as_posix())


def _write_zip(
    destination: Path,
    directory: Path,
    *,
    prefix: PurePosixPath | None = None,
    excluded: set[Path] | None = None,
) -> None:
    """Write a reproducible compressed archive of files below ``directory``."""

    excluded = excluded or set()
    prefix = prefix or PurePosixPath()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for item in _files(directory):
            relative = item.relative_to(directory)
            if relative in excluded:
                continue
            member = (prefix / relative.as_posix()).as_posix()
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                item.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def _validate_review_map_zip(deployment: Path) -> None:
    """The portable ZIP must be a safe, byte-for-byte mirror of the deployment."""
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


def _directory_size(directory: Path) -> int:
    return sum(item.stat().st_size for item in _files(directory))


def _runtime_artifacts(deployment: Path) -> dict[str, dict[str, object]]:
    """Digest every deployable file, excluding only documented cyclic files."""
    return {
        item.relative_to(deployment).as_posix(): {
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
            "size_bytes": item.stat().st_size,
        }
        for item in _files(deployment)
        if item.relative_to(deployment).as_posix() not in CYCLIC_RUNTIME_FILES
    }


def _validate_budget(maximum_bytes: int) -> None:
    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")
    if maximum_bytes >= GITHUB_PAGES_LIMIT_BYTES:
        raise ValueError(
            "maximum_bytes must remain below the GitHub Pages 1 GB limit "
            f"({GITHUB_PAGES_LIMIT_BYTES} bytes)"
        )


def _json_object(path: Path, description: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"invalid {description}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object: {path}")
    return value


def _nonblank_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _relative_file_path(value: object, field: str) -> Path:
    path = _nonblank_text(value, field)
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or "." in parsed.parts
        or "\\" in path
        or path.endswith("/")
    ):
        raise ValueError(f"{field} must be a relative file path without traversal")
    return Path(*parsed.parts)


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _feature_collection(path: Path, field: str) -> list[dict[str, object]]:
    payload = _json_object(path, field)
    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ValueError(f"{field} must be a GeoJSON FeatureCollection")
    if not all(isinstance(feature, dict) for feature in features):
        raise ValueError(f"{field} contains an invalid GeoJSON feature")
    return features  # type: ignore[return-value]


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


def _bbox(features: list[dict[str, object]]) -> list[float] | None:
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
    deployment: Path,
    entries: object,
    *,
    directory: str,
    field: str,
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
            assert isinstance(metadata, dict)  # checked above for type narrowing
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


def _provenance_lock(path: Path, deployment_id: str) -> dict[str, Any]:
    lock = _json_object(path, "tracked deployment provenance lock")
    if lock.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise ValueError("tracked deployment provenance lock schema_version is invalid")
    if lock.get("deployment_id") != deployment_id:
        raise ValueError("tracked deployment provenance lock deployment_id is invalid")
    for field in (
        "area_definition_sha256",
        "snapshot_manifest_sha256",
        "governed_input_fingerprint",
        "decision_ledger_input_sha256",
        "decision_contract_sha256",
        "accepted_decisions_sha256",
        "compilation_input_fingerprint",
    ):
        _sha256(lock.get(field), f"provenance lock {field}")
    if "runtime_governance_sha256" in lock:
        _sha256(
            lock.get("runtime_governance_sha256"),
            "provenance lock runtime_governance_sha256",
        )
    if lock.get("status") not in {"complete", "reviewable"}:
        raise ValueError("tracked deployment provenance lock is not publishable")
    if not isinstance(lock.get("artifacts"), dict):
        raise ValueError("tracked deployment provenance lock artifacts must be an object")
    if lock.get("cyclic_runtime_files") != sorted(CYCLIC_RUNTIME_FILES):
        raise ValueError("tracked deployment provenance lock cyclic_runtime_files are invalid")
    return lock


def _validate_runtime_lock(deployment: Path, lock: dict[str, Any]) -> None:
    artifacts = lock["artifacts"]
    assert isinstance(artifacts, dict)
    if artifacts != _runtime_artifacts(deployment):
        raise ValueError("deployment runtime files do not exactly match the provenance lock")


def _verify_locked_artifact(
    deployment: Path, lock: dict[str, Any], source: str, target: str
) -> None:
    artifacts = lock["artifacts"]
    assert isinstance(artifacts, dict)
    entry = artifacts.get(source)
    if not isinstance(entry, dict):
        raise ValueError(f"provenance lock is missing compiled artifact {source}")
    digest = _sha256(entry.get("sha256"), f"provenance lock artifacts.{source}.sha256")
    path = deployment / target
    if (
        not path.is_file()
        or path.is_symlink()
        or hashlib.sha256(path.read_bytes()).hexdigest() != digest
    ):
        raise ValueError(f"public deployment {target} does not match locked compiled artifact")
    if entry.get("size_bytes") != path.stat().st_size:
        raise ValueError(f"public deployment {target} size does not match locked compiled artifact")


def _validate_publication(
    deployment: Path,
    deployment_id: str,
    *,
    expected_area_id: str,
    expected_area_definition_sha256: str,
    expected_title: str,
    expected_scope: dict[str, str],
    expected_evidence_provenance: dict[str, object],
    expected_compilation_input_fingerprint: str | None = None,
    expected_snapshot_manifest_sha256: str | None = None,
    expected_lock: dict[str, Any] | None = None,
) -> None:
    from satn.models import canonical_decision_ledger_payload

    publication_path = deployment / "publication.json"
    if not publication_path.is_file():
        raise ValueError(f"generated deployment {deployment_id} is missing publication.json")
    publication = _json_object(publication_path, "deployment publication")
    if publication.get("deployment_id") != deployment_id:
        raise ValueError(
            "deployment publication identity does not match catalogue deployment_id: "
            f"{publication_path}"
        )
    area_id = _nonblank_text(publication.get("area_id"), "publication area_id")
    if area_id != expected_area_id:
        raise ValueError(
            f"deployment publication area_id does not match catalogue area_id: {publication_path}"
        )
    if publication.get("disclaimer") != DISCLAIMER:
        raise ValueError(f"deployment publication disclaimer does not match: {publication_path}")
    if publication.get("area_definition_sha256") != expected_area_definition_sha256:
        raise ValueError(
            "deployment publication area_definition_sha256 does not match the "
            f"tracked Area Definition: {publication_path}"
        )
    if expected_lock is None:
        raise ValueError("a tracked deployment provenance lock is required")
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
    if publication.get("title") != expected_title:
        raise ValueError(
            "deployment publication title does not match the tracked Area Definition: "
            f"{publication_path}"
        )
    if publication.get("scope") != expected_scope:
        raise ValueError(
            "deployment publication scope does not match the tracked Area Definition: "
            f"{publication_path}"
        )
    provenance = publication.get("evidence_provenance")
    if not isinstance(provenance, dict):
        raise ValueError(
            f"deployment publication evidence_provenance must be an object: {publication_path}"
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
                "deployment publication evidence_provenance does not match the "
                f"tracked Area Definition at {key}: {publication_path}"
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
            f"{publication_path}"
        )
    if publication.get("status") not in {"complete", "reviewable"}:
        raise ValueError(f"deployment publication status is not publishable: {publication_path}")
    if (
        expected_snapshot_manifest_sha256 is not None
        and snapshot["manifest_sha256"] != expected_snapshot_manifest_sha256
    ):
        raise ValueError(
            "deployment publication snapshot digest does not match the compiled input: "
            f"{publication_path}"
        )
    compiler_run = _json_object(
        deployment / _relative_file_path(publication.get("compiler_run"), "compiler_run"),
        "compiler run",
    )
    try:
        input_ledger = canonical_decision_ledger_payload(compiler_run["decision_ledger_input"])
        accepted_ledger = canonical_decision_ledger_payload(
            {
                "decision_contract": compiler_run["decision_contract"],
                "responses": compiler_run["accepted_decisions"],
            }
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("compiler run has an invalid decision provenance contract") from error
    if input_ledger.decision_contract != compiler_run["decision_contract"] or (
        accepted_ledger.model_dump(mode="json")["responses"]
        != compiler_run["accepted_decisions"]
    ):
        raise ValueError("compiler run has a non-canonical accepted-decision contract")
    for field in ("run_id", "status", "compilation_input_fingerprint"):
        if compiler_run.get(field) != publication.get(field):
            raise ValueError(f"compiler run {field} does not match deployment publication")
    if "runtime_governance_sha256" in expected_lock:
        runtime_governance = compiler_run.get("runtime_governance")
        if not isinstance(runtime_governance, dict):
            raise ValueError("compiler run runtime governance must be an object")
        if publication.get("runtime_governance") != runtime_governance:
            raise ValueError(
                "deployment publication runtime governance does not match compiler run"
            )
        runtime_governance_digest = hashlib.sha256(
            json.dumps(runtime_governance, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if runtime_governance_digest != expected_lock["runtime_governance_sha256"]:
            raise ValueError(
                "compiler run runtime governance does not match tracked provenance lock"
            )
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
    if compiler_run.get("area_definition_sha256") != expected_area_definition_sha256:
        raise ValueError("compiler run area definition digest does not match tracked definition")
    for field in (
        "run_id",
        "status",
        "area_definition_sha256",
        "snapshot_manifest_sha256",
        "governed_input_fingerprint",
        "compilation_input_fingerprint",
        "decision_contract",
        "decision_ledger_input",
        "accepted_decisions",
    ):
        # The decision payloads are bound through their canonical lock digests below.
        if field in expected_lock and compiler_run.get(field) != expected_lock[field]:
            raise ValueError(f"compiler run {field} does not match tracked provenance lock")
    for field, run_field in (
        ("decision_ledger_input_sha256", "decision_ledger_input"),
        ("decision_contract_sha256", "decision_contract"),
        ("accepted_decisions_sha256", "accepted_decisions"),
    ):
        value = json.dumps(
            compiler_run.get(run_field), sort_keys=True, separators=(",", ":")
        ).encode()
        if hashlib.sha256(value).hexdigest() != expected_lock[field]:
            raise ValueError(f"compiler run {run_field} does not match tracked provenance lock")
    if expected_compilation_input_fingerprint is not None:
        if (
            publication.get("compilation_input_fingerprint")
            != expected_compilation_input_fingerprint
        ):
            raise ValueError("deployment publication compilation input fingerprint is stale")
        if (
            compiler_run.get("compilation_input_fingerprint")
            != expected_compilation_input_fingerprint
        ):
            raise ValueError("compiler run compilation input fingerprint is stale")
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
    if "runtime_governance_sha256" in expected_lock:
        public_data_fields += ("runtime_governance",)
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
        raise ValueError("generated data.js provenance_lock does not match tracked provenance lock")
    copied_lock = _provenance_lock(deployment / LOCK_NAME, deployment_id)
    if copied_lock != expected_lock:
        raise ValueError("public deployment provenance lock does not exactly match tracked lock")
    if (
        publication.get("connection_count") != expected_lock.get("connection_count")
        or publication.get("gap_count") != expected_lock.get("gap_count")
    ):
        raise ValueError("deployment publication counts do not match tracked provenance lock")
    _validate_progressive_manifests(deployment, publication)
    _validate_runtime_lock(deployment, expected_lock)


def _expected_compilation_provenance(definition_path: Path, deployment: Path) -> tuple[str, str]:
    """Recompute the full input binding while compiler inputs are still local.

    The snapshot manifest itself is deliberately not put in the public release:
    it is a compiler input, not a user-facing map artefact.  The release instead
    carries its digest and a compiler-run record; this packaging-time check binds
    those values to the local immutable manifest before the archive is made.
    """
    from satn.models import AreaDefinition, canonical_decision_ledger_payload
    from satn.pipeline import (
        compilation_governed_input_fingerprint,
        decision_ledger_input_fingerprint,
        snapshot_manifest_sha256,
    )

    definition = AreaDefinition.from_yaml(definition_path)
    run = _json_object(deployment / "compiler-run.json", "compiler run")
    try:
        ledger = canonical_decision_ledger_payload(run["decision_ledger_input"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("compiler run has an invalid decision-ledger input") from error
    governed = compilation_governed_input_fingerprint(definition)
    return decision_ledger_input_fingerprint(governed, ledger), snapshot_manifest_sha256(definition)


def _copy_deployments(
    catalogue: DeploymentCatalogue,
    deployments_root: Path,
    pages: Path,
    catalogue_root: Path,
) -> None:
    for entry in catalogue.deployments:
        expected_path = _deployment_destination(entry.deployment_id)
        declared_path = Path(entry.deployment_path.rstrip("/"))
        if declared_path != expected_path:
            raise ValueError(
                f"deployment_path for {entry.deployment_id} must be {expected_path.as_posix()}/"
            )
        source = deployments_root / entry.deployment_id
        target = pages / expected_path
        if not source.is_dir():
            raise ValueError(f"missing generated deployment for {entry.deployment_id}: {source}")
        _files(source)
        shutil.copytree(source, target)
        lock = _provenance_lock(
            catalogue_root / Path(entry.area_definition).parent / LOCK_NAME,
            entry.deployment_id,
        )
        expected_input, expected_snapshot = _expected_compilation_provenance(
            catalogue_root / entry.area_definition, target
        )

        _validate_publication(
            target,
            entry.deployment_id,
            expected_area_id=entry.area_id,
            expected_area_definition_sha256=entry.area_definition_sha256,
            expected_title=entry.title,
            expected_scope=entry.scope,
            expected_evidence_provenance=entry.evidence_provenance,
            expected_compilation_input_fingerprint=expected_input,
            expected_snapshot_manifest_sha256=expected_snapshot,
            expected_lock=lock,
        )

        for name, artifact in entry.artifacts.items():
            if name == "review_map_zip":
                continue
            if not (target / artifact).is_file():
                raise ValueError(
                    f"generated deployment {entry.deployment_id} is missing {name}: {artifact}"
                )

        zip_path = target / entry.artifacts["review_map_zip"]
        _write_zip(
            zip_path,
            target,
            prefix=PurePosixPath("review-map"),
            excluded={Path(entry.artifacts["review_map_zip"])},
        )
        _validate_review_map_zip(target)


def _catalogue_root_lock(
    catalogue_path: Path, catalogue: DeploymentCatalogue
) -> dict[str, object]:
    """Read the tag-tracked lock for the two executable Pages root files."""

    from satn.deployment_catalogue import (
        ROOT_LOCK_NAME,
        ROOT_LOCK_SCHEMA_VERSION,
        catalogue_lock_payload,
    )

    lock_path = catalogue_path.resolve().parent / ROOT_LOCK_NAME
    lock = _json_object(lock_path, "Pages root catalogue lock")
    expected = catalogue_lock_payload(catalogue)
    if lock != expected or lock.get("schema_version") != ROOT_LOCK_SCHEMA_VERSION:
        raise ValueError("Pages root catalogue lock does not exactly match tracked catalogue")
    return lock


def _validate_exact_pages_file_set(
    pages: Path,
    catalogue: DeploymentCatalogue,
    root_lock: dict[str, object],
) -> None:
    root_files = root_lock.get("root_files")
    deployment_roots = root_lock.get("deployment_roots")
    if not isinstance(root_files, dict) or set(root_files) != {"index.html", "catalogue.json"}:
        raise ValueError("Pages root catalogue lock root_files are invalid")
    if not isinstance(deployment_roots, list):
        raise ValueError("Pages root catalogue lock deployment_roots are invalid")
    expected_roots = [f"deployments/{entry.deployment_id}" for entry in catalogue.deployments]
    if deployment_roots != expected_roots:
        raise ValueError("Pages root catalogue lock deployment_roots are invalid")

    expected_files = set(root_files)
    for name, metadata in root_files.items():
        if not isinstance(metadata, dict):
            raise ValueError("Pages root catalogue lock root file metadata is invalid")
        target = pages / name
        if not target.is_file() or target.is_symlink():
            raise ValueError(f"Pages root file is missing: {name}")
        if metadata != {
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "size_bytes": target.stat().st_size,
        }:
            raise ValueError(f"Pages root file does not match tracked catalogue lock: {name}")

    for entry in catalogue.deployments:
        deployment = pages / _deployment_destination(entry.deployment_id)
        lock = _provenance_lock(deployment / LOCK_NAME, entry.deployment_id)
        artifacts = lock.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("tracked provenance lock artifacts are invalid")
        expected_files.update(
            f"deployments/{entry.deployment_id}/{_relative_file_path(name, 'artifact')}"
            for name in artifacts
        )
        expected_files.update(
            {
                f"deployments/{entry.deployment_id}/{LOCK_NAME}",
                f"deployments/{entry.deployment_id}/review-map.zip",
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


def _validate_pages_directory(
    pages: Path, maximum_bytes: int, catalogue: DeploymentCatalogue, catalogue_path: Path
) -> int:
    """Validate a fully assembled Pages tree without trusting its producer."""

    pages_size = _directory_size(pages)
    if pages_size > maximum_bytes:
        raise ValueError(
            f"Pages package is {pages_size} bytes, exceeding configured budget "
            f"of {maximum_bytes} bytes"
        )

    root_index = pages / "index.html"
    catalogue_publication_path = pages / "catalogue.json"
    if not root_index.is_file() or not catalogue_publication_path.is_file():
        raise ValueError("Pages package must contain root index.html and catalogue.json")

    public_catalogue = _json_object(catalogue_publication_path, "Pages catalogue")
    if public_catalogue.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Pages catalogue schema_version must be {SCHEMA_VERSION}")
    _nonblank_text(public_catalogue.get("title"), "catalogue title")
    entries = public_catalogue.get("deployments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Pages catalogue must contain deployments")

    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each Pages catalogue deployment must be an object")
        deployment_id = _nonblank_text(entry.get("deployment_id"), "catalogue deployment_id")
        if deployment_id in seen_ids:
            raise ValueError("Pages catalogue deployment_ids must be unique")
        seen_ids.add(deployment_id)
        deployment_path = _nonblank_text(entry.get("deployment_path"), "catalogue deployment_path")
        expected_directory = _deployment_destination(deployment_id)
        if deployment_path != f"{expected_directory.as_posix()}/":
            raise ValueError(
                f"catalogue deployment_path must match deployment_id: {deployment_path}"
            )
        _nonblank_text(entry.get("area_name"), "catalogue area_name")
        title = _nonblank_text(entry.get("title"), "catalogue title")
        scope = entry.get("scope")
        if not isinstance(scope, dict):
            raise ValueError("catalogue scope must be an object")
        evidence_provenance = entry.get("evidence_provenance")
        if not isinstance(evidence_provenance, dict):
            raise ValueError("catalogue evidence_provenance must be an object")
        area_id = _nonblank_text(entry.get("area_id"), "catalogue area_id")
        area_definition = _nonblank_text(entry.get("area_definition"), "area_definition")
        _relative_file_path(area_definition, "area_definition")
        area_definition_sha256 = _nonblank_text(
            entry.get("area_definition_sha256"), "area_definition_sha256"
        )

        deployment = pages / expected_directory
        if deployment.is_symlink() or not deployment.is_dir():
            raise ValueError(f"Pages catalogue deployment is missing: {deployment}")
        _validate_publication(
            deployment,
            deployment_id,
            expected_area_id=area_id,
            expected_area_definition_sha256=area_definition_sha256,
            expected_title=title,
            expected_scope=scope,
            expected_evidence_provenance=evidence_provenance,
            expected_lock=_provenance_lock(deployment / LOCK_NAME, deployment_id),
        )
        _validate_review_map_zip(deployment)

        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("catalogue deployment artifacts must be an object")
        for name in ("review_map", "network_map_pdf", "review_map_zip"):
            artifact_path = _relative_file_path(artifacts.get(name), f"catalogue artifacts.{name}")
            try:
                artifact_path.relative_to(expected_directory)
            except ValueError as error:
                raise ValueError(
                    f"catalogue artifacts.{name} must be rooted at {expected_directory}/"
                ) from error
            target = pages / artifact_path
            if target.is_symlink() or not target.is_file():
                raise ValueError(
                    f"Pages catalogue deployment {deployment_id} is missing {name}: {artifact_path}"
                )

    _validate_exact_pages_file_set(
        pages, catalogue, _catalogue_root_lock(catalogue_path, catalogue)
    )
    return pages_size


def package_pages(
    catalogue_path: str | Path,
    deployments_root: str | Path,
    destination: str | Path,
    release_artifact: str | Path,
    *,
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
    promote_production: bool = False,
) -> PagesPackage:
    """Assemble a Pages tree and its standalone release archive from local bundles.

    The input bundles are process artifacts under an ignored build directory. This
    function deliberately never reads from or writes to a tracked ``site/`` tree.
    """

    _validate_budget(maximum_bytes)
    from satn.deployment_catalogue import load_deployment_catalogue

    catalogue = load_deployment_catalogue(catalogue_path)
    bundles_source = Path(deployments_root)
    if bundles_source.is_symlink():
        raise ValueError(f"deployments_root must not be a symlink: {bundles_source}")
    bundles = bundles_source.resolve()
    output_source = Path(destination)
    if output_source.is_symlink():
        raise ValueError(f"Pages destination must not be a symlink: {output_source}")
    output = output_source.resolve()
    release_source = Path(release_artifact)
    if release_source.is_symlink():
        raise ValueError(f"release_artifact must not be a symlink: {release_source}")
    release = release_source.resolve()
    if release.is_relative_to(output):
        raise ValueError("release_artifact must be outside the Pages destination")
    if bundles == output or bundles.is_relative_to(output) or output.is_relative_to(bundles):
        raise ValueError("deployments_root and Pages destination must not overlap")

    output.parent.mkdir(parents=True, exist_ok=True)
    release.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".pages-package-", dir=output.parent))
    try:
        pages = temporary_root / "pages"
        pages.mkdir()
        _copy_deployments(catalogue, bundles, pages, Path(catalogue_path).resolve().parent)
        if promote_production:
            from satn.runtime_governance import assert_promotable_runtime_governance

            for entry in catalogue.deployments:
                publication = _json_object(
                    pages / _deployment_destination(entry.deployment_id) / "publication.json",
                    "deployment publication",
                )
                runtime_governance = publication.get("runtime_governance")
                if not isinstance(runtime_governance, dict):
                    raise ValueError(
                        "production promotion denied: deployment has no runtime governance contract"
                    )
                compiler_run = _json_object(
                    pages
                    / _deployment_destination(entry.deployment_id)
                    / _relative_file_path(publication.get("compiler_run"), "compiler_run"),
                    "compiler run",
                )
                _assert_required_production_urban_evidence(publication, compiler_run)
                decision_ledger_input = compiler_run.get("decision_ledger_input")
                accepted_decisions = compiler_run.get("accepted_decisions")
                if not isinstance(decision_ledger_input, dict) or not isinstance(
                    accepted_decisions, list
                ):
                    raise ValueError(
                        "production promotion denied: compiler run decision provenance is invalid"
                    )
                assert_promotable_runtime_governance(
                    runtime_governance,
                    decision_contract=compiler_run.get("decision_contract"),
                    decision_ledger_input=decision_ledger_input,
                    accepted_decisions=accepted_decisions,
                )
        from satn.deployment_catalogue import build_deployment_catalogue

        build_deployment_catalogue(catalogue_path, pages)
        pages_size = _validate_pages_directory(
            pages, maximum_bytes, catalogue, Path(catalogue_path)
        )

        temporary_release = temporary_root / RELEASE_ARTIFACT_NAME
        _write_zip(temporary_release, pages)
        release_size = temporary_release.stat().st_size

        if output.exists():
            shutil.rmtree(output)
        pages.replace(output)
        if release.exists():
            release.unlink()
        os.replace(temporary_release, release)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)

    return PagesPackage(
        pages_directory=output,
        release_artifact=release,
        pages_size_bytes=pages_size,
        release_size_bytes=release_size,
    )
