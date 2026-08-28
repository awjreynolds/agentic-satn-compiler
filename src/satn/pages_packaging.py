"""Package generated Area Deployments for Pages without tracking them in Git."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from satn.deployment import DEFERRED_GROUPS

if TYPE_CHECKING:
    from satn.deployment_catalogue import DeploymentCatalogue

GITHUB_PAGES_LIMIT_BYTES = 1_000_000_000
DEFAULT_MAXIMUM_BYTES = 950_000_000
RELEASE_ARTIFACT_NAME = "satn-pages.zip"
SCHEMA_VERSION = "satn-deployment-catalogue/v1"
DISCLAIMER = "Experimental SATN POC — not an adopted plan."


@dataclass(frozen=True)
class PagesPackage:
    """Locations and sizes of one generated Pages release."""

    pages_directory: Path
    release_artifact: Path
    pages_size_bytes: int
    release_size_bytes: int


def _deployment_destination(deployment_id: str) -> Path:
    return Path("deployments") / deployment_id


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


def _directory_size(directory: Path) -> int:
    return sum(item.stat().st_size for item in _files(directory))


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


def _validate_wgs84_map_artifacts(deployment: Path) -> None:
    """Reject map coordinates that cannot be handed safely to MapLibre."""
    for artifact in _files(deployment):
        if artifact.suffix.lower() != ".geojson":
            continue
        relative_path = artifact.relative_to(deployment).as_posix()
        features = _feature_collection(artifact, f"map artifact {relative_path}")
        for index, feature in enumerate(features):
            for longitude, latitude in _coordinates(feature.get("geometry")):
                if (
                    not math.isfinite(longitude)
                    or not math.isfinite(latitude)
                    or not -180 <= longitude <= 180
                    or not -90 <= latitude <= 90
                ):
                    raise ValueError(
                        "map artifact "
                        f"{relative_path} feature {index} must contain only finite WGS84 "
                        "longitude/latitude coordinates"
                    )


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
            isinstance(feature_type, str) and feature_type.strip() and isinstance(metadata, dict)
            for feature_type, metadata in types.items()
        ):
            raise ValueError(f"layer group {name} types must be named objects")
        expected_types = DEFERRED_GROUPS[name]
        if set(types) != expected_types:
            raise ValueError(f"layer group {name} types must exactly match canonical feature types")
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


def _canonicalize_runtime_data(path: Path) -> None:
    """Remove equal legacy projections from the Pages-facing runtime payload."""

    prefix = "window.SATN_DATA = "
    data = _data_payload(path)
    legacy = data.get("reviewable")
    canonical = data.get("reviewable_network")
    if legacy is not None and canonical is not None:
        if legacy != canonical:
            raise ValueError("generated data.js reviewable projections disagree")
        data.pop("reviewable")
        path.write_text(
            f"{prefix}{json.dumps(data, separators=(',', ':'))};\n",
            encoding="utf-8",
        )


def _validate_publication_shape(
    deployment: Path,
    deployment_id: str,
    *,
    expected_area_id: str,
) -> None:
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
    if publication.get("status") not in {"complete", "reviewable"}:
        raise ValueError(f"deployment publication status is not publishable: {publication_path}")
    required_paths = {
        "compiler_run": publication.get("compiler_run"),
        "layer_manifest": publication.get("layer_manifest"),
        "topography_manifest": publication.get("topography_manifest"),
        "topography_profile_evidence_index": publication.get("topography_profile_evidence_index"),
    }
    for field, value in required_paths.items():
        path = deployment / _relative_file_path(value, field)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"deployment {deployment_id} is missing {field}: {path}")
    for name in ("index.html", "data.js", "network.geojson"):
        path = deployment / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"deployment {deployment_id} is missing required file: {path}")

    data = _data_payload(deployment / "data.js")
    for field, expected in (
        ("network_url", "network.geojson"),
        ("layer_manifest_url", "layer-manifest.json"),
        ("topography_manifest_url", "topography-manifest.json"),
        ("profile_evidence_index_url", "topography-profile-evidence.json"),
    ):
        if data.get(field) != expected:
            raise ValueError(f"generated data.js {field} must be {expected}")
    _validate_progressive_manifests(deployment, publication)


def _copy_deployments(
    catalogue: DeploymentCatalogue,
    deployments_root: Path,
    pages: Path,
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
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("review-map.zip", "strategic-network.json"),
        )
        _canonicalize_runtime_data(target / "data.js")
        _validate_wgs84_map_artifacts(target)

        for name, artifact in entry.artifacts.items():
            if name == "review_map_zip":
                continue
            if not (target / artifact).is_file():
                raise ValueError(
                    f"generated deployment {entry.deployment_id} is missing {name}: {artifact}"
                )


def _validate_pages_directory(
    pages: Path, maximum_bytes: int, catalogue: DeploymentCatalogue
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
    expected_ids = {entry.deployment_id for entry in catalogue.deployments}
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
        _nonblank_text(entry.get("title"), "catalogue title")
        scope = entry.get("scope")
        if not isinstance(scope, dict):
            raise ValueError("catalogue scope must be an object")
        evidence_provenance = entry.get("evidence_provenance")
        if not isinstance(evidence_provenance, dict):
            raise ValueError("catalogue evidence_provenance must be an object")
        area_id = _nonblank_text(entry.get("area_id"), "catalogue area_id")
        deployment = pages / expected_directory
        if deployment.is_symlink() or not deployment.is_dir():
            raise ValueError(f"Pages catalogue deployment is missing: {deployment}")
        _validate_publication_shape(
            deployment,
            deployment_id,
            expected_area_id=area_id,
        )
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("catalogue deployment artifacts must be an object")
        for name in ("review_map", "network_map_pdf"):
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

    if seen_ids != expected_ids:
        raise ValueError("Pages catalogue deployments do not match the tracked catalogue")
    for item in _files(pages):
        if item.name in {"provenance-lock.json", "catalogue-lock.json"}:
            raise ValueError(f"Pages package contains an obsolete lock: {item}")
    return pages_size


def package_pages(
    catalogue_path: str | Path,
    deployments_root: str | Path,
    destination: str | Path,
    release_artifact: str | Path,
    *,
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
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
        _copy_deployments(catalogue, bundles, pages)
        from satn.deployment_catalogue import build_deployment_catalogue

        build_deployment_catalogue(catalogue_path, pages)
        pages_size = _validate_pages_directory(pages, maximum_bytes, catalogue)

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
