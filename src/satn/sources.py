"""Immutable OSM/fixture snapshots and council-neutral Network Place derivation."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point
from shapely.ops import unary_union

from satn.constants import DISCLAIMER, SCHEMA_VERSION
from satn.content_identity import canonical_network_geometry_fingerprint
from satn.ea_elevation import (
    CONTRACT_SCHEMA_VERSION as EA_ELEVATION_CONTRACT_VERSION,
)
from satn.ea_elevation import (
    DTM_ATTRIBUTION as EA_LIDAR_ATTRIBUTION,
)
from satn.ea_elevation import (
    DTM_COVERAGE_ID as EA_LIDAR_COVERAGE_ID,
)
from satn.ea_elevation import (
    DTM_DATASET_ID as EA_LIDAR_DATASET_ID,
)
from satn.ea_elevation import (
    DTM_ENDPOINT as EA_LIDAR_ENDPOINT,
)
from satn.ea_elevation import (
    DTM_LICENCE as EA_LIDAR_LICENCE,
)
from satn.ea_elevation import (
    ELIGIBLE_FEATURE_TYPES,
    FIXED_POINT_PRIMARY_FIELD,
    LEGACY_CONTRACT_SCHEMA_VERSION,
    LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION,
    SAMPLE_LEDGER_FILENAME,
    SAMPLE_LEDGER_SCHEMA_VERSION,
    _canonical_line_geometry,
    canonical_ea_elevation_evidence_bytes,
    eligible_route_samples,
    evidence_row_sha256,
    fixed_point_route_fingerprint,
    read_sample_ledger,
    sha256_file,
    validate_eligible_route_geometries,
    validate_official_weca_survey_index,
)
from satn.evidence import (
    derive_context_layers,
    empty_context,
    govern_network_scope_for_urban_communities,
)
from satn.heartbeat import StageHeartbeat
from satn.models import (
    AreaConfig,
    GovernedSpatialSourceConfig,
    NationalElevationConfig,
    OfficialRoadClassification,
    safe_snapshot_id,
)
from satn.settlement import assess_community_urban_eligibility
from satn.streaming_geojson import iter_geojson_features

if TYPE_CHECKING:
    from satn.ea_snapshot_recovery import ValidatedEARecoveryParent

CORE_SOURCE_FILES = ("boundary.geojson", "places.geojson", "network.geojson")
OSM_ATTRIBUTION = "© OpenStreetMap contributors; data available under the ODbL"
NCN_ATTRIBUTION = "Walk Wheel Cycle Trust National Cycle Network; Open Government Licence v3.0"
ROAD_CLASSIFICATION_FILENAME = "official-road-classification.geojson"
OBSERVED_THROUGH_TRAFFIC_FILENAME = "observed-through-traffic.geojson"
ELEVATION_EVIDENCE_FILENAME = "elevation-evidence.geojson"
EA_RETAINED_ROUTE_FILENAME = "ea-elevation-sampled-routes.geojson"
EA_LIDAR_SOURCE_ID = "ea-lidar-composite-dtm-1m"
EA_LIDAR_WECA_ACQUISITION_CONTRACT = "ea-lidar-weca-v1"
EA_FIXED_POINT_REPLAY_INPUT_SCHEMA_VERSION = "ea-fixed-point-replay-inputs/v1"
ELEVATION_ARTIFACT_FILENAMES = frozenset(
    {
        ELEVATION_EVIDENCE_FILENAME,
        "elevation-evidence.manifest.json",
        "ea-survey-index.geojson",
        "ea-authority-boundaries.geojson",
        SAMPLE_LEDGER_FILENAME,
        EA_RETAINED_ROUTE_FILENAME,
    }
)
ROAD_CLASSIFICATION_COLUMNS = [
    "official_feature_id",
    "official_classification",
    "official_road_number",
    "official_road_name",
    "official_road_function",
    "source_id",
    "effective_date",
    "licence",
    "content_fingerprint",
    "geometry",
]
LOGGER = logging.getLogger(__name__)
STREAMING_GEOJSON_THRESHOLD_BYTES = 64 * 1024 * 1024


def _snapshot_destination(config: AreaConfig) -> Path:
    """Validate the snapshot root and target before any source input is consumed.

    The root is allowed to be absent for a first snapshot, but it is created and
    re-checked before a temporary directory or acquisition can be started.  An
    existing root/target link is never followed, and the resolved target must
    remain the root's direct child.
    """
    snapshot_id = safe_snapshot_id(config.source.snapshot_id)
    root = config.source.snapshot_dir
    if not isinstance(root, Path) or root.is_symlink():
        raise ValueError("snapshot root is missing or unsafe")
    if root.exists():
        if not root.is_dir():
            raise ValueError("snapshot root is missing or unsafe")
        resolved_root = root.resolve(strict=True)
    else:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("snapshot root is missing or unsafe")
        resolved_root = root.resolve(strict=True)
    destination = root / snapshot_id
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError("snapshot target is missing or unsafe")
    resolved_destination = destination.resolve(strict=destination.exists())
    if resolved_destination.parent != resolved_root:
        raise ValueError("snapshot target escapes snapshot root")
    return destination


def _regular_sibling(directory: Path, name: object, *, label: str) -> Path:
    """Resolve a manifest sibling without permitting traversal or links."""
    if not isinstance(name, str) or Path(name).name != name or not name:
        raise ValueError(f"{label} must be a safe sibling basename")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    try:
        path.resolve(strict=True).relative_to(directory.resolve(strict=True))
    except ValueError as error:
        raise ValueError(f"{label} must resolve inside its manifest directory") from error
    return path


def _manifest_siblings(
    directory: Path,
    names: object,
    *,
    label: str,
) -> dict[str, Path]:
    """Validate every retained manifest filename before consuming any of them.

    A manifest is untrusted input, including when it was produced by an older
    local run.  Keep this as a separate first pass so a manifest containing a
    later traversal, link, directory, or duplicate cannot cause an earlier
    entry to be read, copied, or hashed first.
    """
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError(f"{label} names must be a list of safe sibling basenames")
    if len(names) != len(set(names)):
        raise ValueError(f"{label} names must not contain duplicates")
    return {name: _regular_sibling(directory, name, label=f"{label} {name!r}") for name in names}


def _manifest_hashes(manifest: dict[str, object], field: str, *, label: str) -> dict[str, str]:
    """Return a typed, unique manifest hash map without trusting its paths."""
    values = manifest.get(field, {})
    if not isinstance(values, dict) or any(
        not isinstance(name, str)
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for name, digest in values.items()
    ):
        raise ValueError(f"{label} must map safe filenames to SHA-256 digests")
    return dict(values)


def _retained_core_lineage(manifest: dict[str, object]) -> dict[str, str] | None:
    """Validate the optional immutable source identity carried by a final snapshot."""
    value = manifest.get("retained_core_lineage")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "source_snapshot_id",
        "source_manifest_sha256",
    }:
        raise ValueError("invalid snapshot: retained-core lineage is malformed")
    try:
        target_snapshot_id = safe_snapshot_id(
            manifest.get("snapshot_id"), field_name="retained-core target snapshot identifier"
        )
        snapshot_id = safe_snapshot_id(
            value.get("source_snapshot_id"), field_name="retained-core source snapshot identifier"
        )
    except ValueError as error:
        raise ValueError("invalid snapshot: retained-core lineage is malformed") from error
    manifest_sha256 = value.get("source_manifest_sha256")
    if (
        not isinstance(manifest_sha256, str)
        or len(manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in manifest_sha256)
    ):
        raise ValueError("invalid snapshot: retained-core lineage is malformed")
    if target_snapshot_id == snapshot_id:
        raise ValueError("invalid snapshot: retained-core source equals target snapshot")
    return {
        "source_snapshot_id": snapshot_id,
        "source_manifest_sha256": manifest_sha256,
    }


def _lineaged_retained_core_source(config: AreaConfig) -> tuple[Path, dict[str, object]] | None:
    """Load one separately identified immutable snapshot before copying any bytes."""
    lineage = config.source.retained_core_source
    if lineage is None:
        return None
    source_snapshot_id = safe_snapshot_id(
        lineage.snapshot_id, field_name="retained-core lineage source snapshot identifier"
    )
    target_snapshot_id = safe_snapshot_id(config.source.snapshot_id)
    if source_snapshot_id == target_snapshot_id:
        raise ValueError("retained-core source snapshot must differ from target snapshot")
    source = config.source.snapshot_dir / source_snapshot_id
    if source.is_symlink() or not source.is_dir():
        raise ValueError("retained-core lineage source snapshot is missing or unsafe")
    try:
        source.resolve(strict=True).relative_to(config.source.snapshot_dir.resolve(strict=True))
    except ValueError as error:
        raise ValueError(
            "retained-core lineage source snapshot escapes snapshot directory"
        ) from error
    manifest_path = _regular_sibling(source, "snapshot.json", label="retained-core source manifest")
    manifest_bytes = manifest_path.read_bytes()
    actual_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_sha256 != lineage.manifest_sha256:
        raise ValueError("retained-core lineage source manifest SHA-256 mismatch")
    _validate_snapshot(source)
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("snapshot_id") != lineage.snapshot_id:
        raise ValueError("retained-core lineage source snapshot identity mismatch")
    return source, manifest


def _validated_ea_recovery_source(
    config: AreaConfig,
    parent: ValidatedEARecoveryParent,
) -> tuple[Path, dict[str, object]]:
    """Consume one exact recovery capability only for its configured lineage."""

    lineage = config.source.retained_core_source
    expected_path = config.source.snapshot_dir / parent.snapshot_id
    if (
        lineage is None
        or lineage.snapshot_id != parent.snapshot_id
        or lineage.manifest_sha256 != parent.manifest_sha256
        or parent.path != expected_path
        or hashlib.sha256(parent.manifest_bytes).hexdigest() != parent.manifest_sha256
    ):
        raise ValueError("EA recovery parent capability differs from configured lineage")
    manifest = json.loads(parent.manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("snapshot_id") != parent.snapshot_id:
        raise ValueError("EA recovery parent capability has invalid snapshot identity")
    return parent.path, manifest


def _validate_existing_lineaged_target(
    destination: Path,
    config: AreaConfig,
    source_manifest: dict[str, object],
) -> None:
    """Make repeated final retained-core snapshots a read-only idempotent operation.

    Both snapshots have already passed their own complete hash validation.  This
    second comparison binds the completed target to the separately verified
    historical manifest, so a target cannot be modified and self-resealed.
    """
    manifest_path = _regular_sibling(destination, "snapshot.json", label="snapshot manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("retained-core lineage target manifest is invalid")
    expected = config.source.retained_core_source
    if expected is None:  # pragma: no cover - caller establishes this invariant.
        raise ValueError("retained-core lineage target has no configured source")
    if _retained_core_lineage(manifest) != {
        "source_snapshot_id": expected.snapshot_id,
        "source_manifest_sha256": expected.manifest_sha256,
    }:
        raise ValueError("retained-core lineage target does not match configured source")
    if manifest.get("snapshot_id") != config.source.snapshot_id:
        raise ValueError("retained-core lineage target snapshot identity does not match config")

    target_file_hashes = _manifest_hashes(
        manifest, "file_sha256", label="retained-core lineage target file hashes"
    )
    source_file_hashes = _manifest_hashes(
        source_manifest, "file_sha256", label="retained-core lineage source file hashes"
    )
    target_provenance_hashes = _manifest_hashes(
        manifest,
        "provenance_file_sha256",
        label="retained-core lineage target provenance hashes",
    )
    source_provenance_hashes = _manifest_hashes(
        source_manifest,
        "provenance_file_sha256",
        label="retained-core lineage source provenance hashes",
    )
    configured_governed_files = {
        filename
        for configured, filename in (
            (config.source.official_road_classification, ROAD_CLASSIFICATION_FILENAME),
            (config.source.observed_through_traffic, OBSERVED_THROUGH_TRAFFIC_FILENAME),
        )
        if configured is not None
    }

    def compare_retained_hashes(
        target: dict[str, str], source: dict[str, str], *, kind: str
    ) -> None:
        for filename, digest in source.items():
            if filename in ELEVATION_ARTIFACT_FILENAMES | configured_governed_files:
                continue
            if target.get(filename) != digest:
                raise ValueError(f"retained-core lineage target changed retained {kind}")
        for filename in target:
            if (
                filename not in source
                and filename not in ELEVATION_ARTIFACT_FILENAMES
                and filename not in configured_governed_files
            ):
                raise ValueError(f"retained-core lineage target added unconfigured {kind}")

    compare_retained_hashes(target_file_hashes, source_file_hashes, kind="file")
    compare_retained_hashes(target_provenance_hashes, source_provenance_hashes, kind="provenance")

    source_evidence = source_manifest.get("evidence_sources")
    target_evidence = manifest.get("evidence_sources")
    if not isinstance(source_evidence, dict) or not isinstance(target_evidence, dict):
        raise ValueError("retained-core lineage target evidence identity is invalid")
    governed_evidence_keys = {
        key
        for configured, key in (
            (config.source.official_road_classification, "official_road_classification"),
            (config.source.observed_through_traffic, "observed_through_traffic"),
        )
        if configured is not None
    }
    with tempfile.TemporaryDirectory(
        prefix=".retained-governed-validation-",
        dir=destination.parent,
    ) as validation_directory:
        validation_path = Path(validation_directory)
        expected_governed_evidence: dict[str, dict[str, object] | None] = {}
        if config.source.official_road_classification is not None:
            shutil.copy2(
                _regular_sibling(
                    destination,
                    "boundary.geojson",
                    label="retained-core lineage target boundary",
                ),
                validation_path / "boundary.geojson",
            )
            _snapshot_official_road_classification(config, validation_path)
            expected_governed_evidence["official_road_classification"] = (
                _road_classification_manifest(config, validation_path)
            )
        if config.source.observed_through_traffic is not None:
            _snapshot_observed_through_traffic(config, validation_path)
            expected_governed_evidence["observed_through_traffic"] = (
                _observed_through_traffic_manifest(config, validation_path)
            )
        for key, expected_evidence in expected_governed_evidence.items():
            filename = str(expected_evidence["snapshot_file"]) if expected_evidence else ""
            expected_path = _regular_sibling(
                validation_path,
                filename,
                label=f"expected retained {key}",
            )
            if target_file_hashes.get(filename) != sha256_file(expected_path):
                raise ValueError(
                    f"retained-core lineage target {key} does not match configured source"
                )
            if target_evidence.get(key) != expected_evidence:
                raise ValueError(
                    f"retained-core lineage target {key} provenance mismatch"
                )
    national_elevation = config.source.national_elevation
    if (
        national_elevation is not None
        and national_elevation.acquisition_contract == EA_LIDAR_WECA_ACQUISITION_CONTRACT
    ):
        # A target-local manifest can be self-resealed.  Recompute the retained
        # EA acquisition proof from its ledger, official index and evidence so
        # the elevation file is also bound outside those mutable manifest claims.
        recomputed_ea_provenance = _ea_elevation_acquisition_provenance(
            national_elevation, snapshot_dir=destination
        )
        target_elevation = target_evidence.get("elevation")
        if not isinstance(target_elevation, dict) or any(
            target_elevation.get(field) != value
            for field, value in recomputed_ea_provenance.items()
        ):
            raise ValueError("retained-core lineage target elevation provenance mismatch")
        _validate_canonical_retained_ea_evidence(
            _regular_sibling(
                destination,
                ELEVATION_EVIDENCE_FILENAME,
                label="retained EA Elevation Evidence",
            ),
            national_elevation,
            json.loads(
                _regular_sibling(
                    destination,
                    "elevation-evidence.manifest.json",
                    label="retained EA Elevation Evidence acquisition manifest",
                ).read_text(encoding="utf-8")
            ),
        )
    missing_evidence = object()
    for key, value in source_evidence.items():
        if (
            key != "elevation"
            and key not in governed_evidence_keys
            and target_evidence.get(key, missing_evidence) != value
        ):
            raise ValueError("retained-core lineage target changed retained evidence identity")
    if any(
        key not in source_evidence
        and key != "elevation"
        and key not in governed_evidence_keys
        for key in target_evidence
    ):
        raise ValueError("retained-core lineage target added unconfigured evidence identity")

    allowed_differences = {
        "snapshot_id",
        "files",
        "file_sha256",
        "provenance_file_sha256",
        "evidence_sources",
        "retained_core_lineage",
    }
    missing = object()
    for key in set(source_manifest) | set(manifest):
        if key not in allowed_differences and manifest.get(key, missing) != source_manifest.get(
            key, missing
        ):
            raise ValueError("retained-core lineage target changed retained manifest identity")

    files = manifest.get("files")
    if not isinstance(files, list) or ELEVATION_EVIDENCE_FILENAME not in files:
        raise ValueError("retained-core lineage target has no completed elevation evidence")


def _replace_snapshot_directory(temporary: Path, destination: Path) -> None:
    """Swap a validated snapshot with recoverable rollback on replacement failure."""
    backup = destination.with_name(f".{destination.name}.previous")
    if backup.exists():
        if backup.is_symlink() or not backup.is_dir():
            raise ValueError("snapshot backup is not a regular directory")
        if not destination.exists():
            # A previous process crashed after moving the live snapshot aside.
            # Restore it before doing anything else; it is the only verified copy.
            backup.replace(destination)
        else:
            # The replacement may have succeeded just before a process crash.
            # Do not discard the recoverable old copy until the live destination
            # has independently passed the immutable snapshot checks.
            _validate_snapshot(destination)
            shutil.rmtree(backup)
    if destination.exists():
        destination.replace(backup)
    try:
        temporary.replace(destination)
        _validate_snapshot(destination)
    except Exception:
        if backup.exists() and destination.exists():
            destination.replace(temporary)
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


@dataclass
class OSMData:
    boundary: gpd.GeoDataFrame
    place_features: gpd.GeoDataFrame
    stations: gpd.GeoDataFrame
    network: gpd.GeoDataFrame
    graph: object | None = None
    ncn_routes: gpd.GeoDataFrame | None = None
    facilities: gpd.GeoDataFrame | None = None
    circulation_boundaries: gpd.GeoDataFrame | None = None


@dataclass(frozen=True)
class StagedSnapshot:
    """A fully validated snapshot awaiting an absent-only directory promotion."""

    path: Path
    destination: Path


class OSMAdapter(Protocol):
    def acquire(self, config: AreaConfig) -> OSMData: ...


class OSMnxAdapter:
    """Thin adapter around OSMnx so acquisition can be replaced in offline tests."""

    def acquire(self, config: AreaConfig) -> OSMData:
        import osmnx as ox

        ox.settings.overpass_url = config.source.overpass_url
        ox.settings.requests_timeout = config.source.osm_timeout_seconds
        queries = config.source.boundary_queries
        query: str | list[str] = queries[0] if len(queries) == 1 else list(queries)
        geocoded = ox.geocode_to_gdf(query).to_crs(4326)
        boundary = gpd.GeoDataFrame(
            {
                "boundary_id": [
                    f"osm-{row.get('osm_type', 'feature')}-{row.get('osm_id', index)}"
                    for index, (_, row) in enumerate(geocoded.iterrows(), start=1)
                ],
                "name": [
                    str(row.get("display_name") or queries[index]).split(",")[0]
                    for index, (_, row) in enumerate(geocoded.iterrows())
                ],
                "source_query": list(queries),
                "geometry": list(geocoded.geometry),
            },
            geometry="geometry",
            crs=4326,
        )
        governed_polygon = boundary.geometry.union_all()
        buffered = (
            gpd.GeoSeries([governed_polygon], crs=4326)
            .to_crs(27700)
            .buffer(config.source.external_buffer_km * 1000)
            .to_crs(4326)
            .iloc[0]
        )
        place_features = ox.features_from_polygon(
            buffered,
            tags={"place": [*config.source.community_place_types, "hamlet", "city"]},
        ).reset_index()
        stations = ox.features_from_polygon(
            governed_polygon,
            tags={"railway": "station", "amenity": "bus_station", "public_transport": "station"},
        ).reset_index()
        cycle_route_frames: list[gpd.GeoDataFrame] = []
        if config.source.ncn_feature_service_url:
            cycle_route_frames.append(
                _load_ncn_features(config.source.ncn_feature_service_url, boundary)
            )
        if config.source.reclassified_ncn_feature_service_url:
            cycle_route_frames.append(
                _load_reclassified_ncn_features(
                    config.source.reclassified_ncn_feature_service_url,
                    boundary,
                )
            )
        populated_cycle_routes = [frame for frame in cycle_route_frames if not frame.empty]
        ncn_routes = (
            gpd.GeoDataFrame(
                pd.concat(populated_cycle_routes, ignore_index=True, sort=False),
                geometry="geometry",
                crs=4326,
            )
            if populated_cycle_routes
            else None
        )
        facilities = _features_from_tag_groups(
            ox,
            governed_polygon,
            (
                {
                    "amenity": [
                        "school",
                        "college",
                        "university",
                        "doctors",
                        "pharmacy",
                        "clinic",
                        "hospital",
                        "marketplace",
                    ]
                },
                {"shop": True, "landuse": "retail"},
                {
                    "entrance": True,
                    "barrier": ["gate", "lift_gate", "swing_gate"],
                },
            ),
        )
        circulation_boundaries = _features_from_tag_groups(
            ox,
            governed_polygon,
            (
                {
                    "waterway": ["river", "canal"],
                    "railway": ["rail", "light_rail", "subway"],
                },
                {
                    "landuse": [
                        "residential",
                        "commercial",
                        "industrial",
                        "retail",
                        "farmland",
                        "meadow",
                        "grass",
                        "forest",
                        "recreation_ground",
                    ]
                },
                {"natural": ["wood", "heath", "scrub", "grassland"]},
            ),
        )
        graph = ox.graph_from_polygon(
            governed_polygon,
            network_type=config.source.network_type,
            simplify=True,
            retain_all=True,
        )
        _, network = ox.graph_to_gdfs(graph)
        network = network.reset_index()
        return OSMData(
            boundary,
            place_features,
            stations,
            network,
            graph=graph,
            ncn_routes=ncn_routes,
            facilities=facilities,
            circulation_boundaries=circulation_boundaries,
        )


def _features_from_tag_groups(
    ox: object,
    polygon: object,
    tag_groups: tuple[dict[str, object], ...],
) -> gpd.GeoDataFrame:
    """Fetch bounded OSM feature groups and merge them by stable OSM identity."""
    frames = []
    for index, tags in enumerate(tag_groups, start=1):
        LOGGER.info(
            "OSM feature query group started group=%d/%d tags=%s",
            index,
            len(tag_groups),
            ",".join(sorted(tags)),
        )
        frame = ox.features_from_polygon(  # type: ignore[attr-defined]
            polygon, tags=tags
        ).reset_index()
        frames.append(frame)
        LOGGER.info(
            "OSM feature query group completed group=%d/%d features=%d",
            index,
            len(tag_groups),
            len(frame),
        )
    populated = [frame for frame in frames if not frame.empty]
    if not populated:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
    merged = gpd.GeoDataFrame(
        pd.concat(populated, ignore_index=True, sort=False),
        geometry="geometry",
        crs=populated[0].crs,
    )
    identity = [
        column for column in ("element", "id", "element_type", "osmid") if column in merged.columns
    ]
    return merged.drop_duplicates(identity or None).reset_index(drop=True)


def _materialise_snapshot(
    config: AreaConfig,
    *,
    replace: bool = False,
    retain_core: bool = False,
    osm_adapter: OSMAdapter | None = None,
    stage_only: bool = False,
    ea_recovery_parent: ValidatedEARecoveryParent | None = None,
) -> Path | StagedSnapshot:
    """Materialise an immutable, attributable source snapshot."""
    destination = _snapshot_destination(config)
    LOGGER.info(
        "Snapshot acquisition started source_kind=%s snapshot=%s replace=%s",
        config.source.kind,
        config.source.snapshot_id,
        replace,
    )
    with StageHeartbeat(
        LOGGER,
        "snapshot-acquisition",
        {
            "area_id": config.area_id,
            "snapshot_id": config.source.snapshot_id,
            "source_kind": config.source.kind,
        },
    ) as heartbeat:
        retained_manifest: dict[str, object] | None = None
        retained_source: Path | None = None
        retained_provenance_files: list[str] = []
        regenerated_governed_files: set[str] = set()
        lineaged_source = config.source.retained_core_source
        if lineaged_source is not None and not retain_core:
            raise ValueError("retained-core source lineage requires --retain-core")
        if retain_core and config.source.national_elevation is None:
            raise ValueError("retained-core snapshot augmentation requires national elevation")

        if ea_recovery_parent is not None:
            if not retain_core or not stage_only:
                raise ValueError("EA recovery parent capability requires staged retained-core mode")
            source_lineage = _validated_ea_recovery_source(config, ea_recovery_parent)
        else:
            source_lineage = _lineaged_retained_core_source(config)
        if destination.exists():
            manifest = json.loads(
                _regular_sibling(destination, "snapshot.json", label="snapshot manifest").read_text(
                    encoding="utf-8"
                )
            )
            if isinstance(manifest, dict) and manifest.get("schema_version") == SCHEMA_VERSION:
                heartbeat.set_stage("existing-snapshot-validation")
                _validate_snapshot(destination)
                if lineaged_source is not None:
                    if replace:
                        raise ValueError(
                            "lineaged retained-core snapshots cannot replace a final target"
                        )
                    if source_lineage is None:  # pragma: no cover - source validation raises first.
                        raise ValueError("retained-core lineage source is unavailable")
                    _validate_existing_lineaged_target(
                        destination,
                        config,
                        source_lineage[1],
                    )
                    LOGGER.info("Existing lineaged snapshot validated path=%s", destination)
                    return destination
                if not replace and not retain_core:
                    LOGGER.info("Existing snapshot validated path=%s", destination)
                    return destination
                if retain_core:
                    retained_manifest = manifest
                    retained_source = destination
            elif retain_core:
                raise ValueError("retained-core snapshot augmentation requires a valid snapshot")
        elif retain_core:
            if source_lineage is None:
                raise ValueError(
                    "retained-core snapshot augmentation requires an existing snapshot"
                )
            retained_source, retained_manifest = source_lineage

        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
        remove_temporary = True
        try:
            if retained_manifest is not None and retained_source is not None:
                excluded = {
                    ELEVATION_EVIDENCE_FILENAME,
                    "elevation-evidence.manifest.json",
                    "ea-survey-index.geojson",
                    "ea-authority-boundaries.geojson",
                    SAMPLE_LEDGER_FILENAME,
                    EA_RETAINED_ROUTE_FILENAME,
                }
                retained_files = _manifest_siblings(
                    retained_source,
                    retained_manifest.get("files"),
                    label="retained snapshot file",
                )
                files = [filename for filename in retained_files if filename not in excluded]
                if not files:
                    raise ValueError("retained-core snapshot has missing source files")
                for filename in files:
                    shutil.copy2(retained_files[filename], temporary / filename)
                retained_provenance = _manifest_siblings(
                    retained_source,
                    list(
                        _manifest_hashes(
                            retained_manifest,
                            "provenance_file_sha256",
                            label="retained snapshot provenance hashes",
                        )
                    ),
                    label="retained snapshot provenance file",
                )
                for filename, source_file in retained_provenance.items():
                    if filename in excluded:
                        continue
                    if filename not in files:
                        shutil.copy2(source_file, temporary / filename)
                    retained_provenance_files.append(filename)
                if _snapshot_official_road_classification(config, temporary):
                    regenerated_governed_files.add(ROAD_CLASSIFICATION_FILENAME)
                    if ROAD_CLASSIFICATION_FILENAME not in files:
                        files.append(ROAD_CLASSIFICATION_FILENAME)
                if _snapshot_observed_through_traffic(config, temporary):
                    regenerated_governed_files.add(OBSERVED_THROUGH_TRAFFIC_FILENAME)
                    if OBSERVED_THROUGH_TRAFFIC_FILENAME not in files:
                        files.append(OBSERVED_THROUGH_TRAFFIC_FILENAME)
                _snapshot_national_elevation(config, temporary)
                files.append(ELEVATION_EVIDENCE_FILENAME)
                source_identifier = str(retained_manifest["source_identifier"])
                attribution = str(retained_manifest["attribution"])
            elif config.source.kind == "fixture":
                source_identifier, files = _write_fixture_snapshot(config, temporary)
                attribution = "Synthetic test fixture"
            else:
                source_identifier, files = _write_osm_snapshot(
                    config, temporary, osm_adapter or OSMnxAdapter()
                )
                attribution = (
                    f"{OSM_ATTRIBUTION}; {NCN_ATTRIBUTION}"
                    if config.source.ncn_feature_service_url
                    else OSM_ATTRIBUTION
                )
            generated_provenance_files = [
                filename
                for filename in (
                    ELEVATION_EVIDENCE_FILENAME,
                    "elevation-evidence.manifest.json",
                    "ea-survey-index.geojson",
                    "ea-authority-boundaries.geojson",
                    SAMPLE_LEDGER_FILENAME,
                    EA_RETAINED_ROUTE_FILENAME,
                )
                if (temporary / filename).exists()
            ]
            provenance_files = [*retained_provenance_files]
            provenance_files.extend(
                filename
                for filename in generated_provenance_files
                if filename not in provenance_files
            )
            for filename in (
                "ea-survey-index.geojson",
                "ea-authority-boundaries.geojson",
                SAMPLE_LEDGER_FILENAME,
                EA_RETAINED_ROUTE_FILENAME,
            ):
                if (temporary / filename).exists() and filename not in files:
                    files.append(filename)
            heartbeat.set_stage("snapshot-validation")
            retrieved_at = datetime.now(UTC).isoformat()
            file_paths = _manifest_siblings(temporary, files, label="snapshot file")
            provenance_paths = _manifest_siblings(
                temporary, provenance_files, label="snapshot provenance file"
            )
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": config.source.snapshot_id,
                # Keep the manifest key stable for existing snapshots while an
                # Area Definition supplies the canonical identity.
                "council_id": config.area_id,
                "area_id": config.area_id,
                "area_name": config.area_name,
                "source_kind": config.source.kind,
                "source_identifier": source_identifier,
                "retrieved_at": retrieved_at,
                "attribution": attribution,
                "evidence_sources": {
                    "osm": list(config.source.boundary_queries),
                    "ncn": config.source.ncn_feature_service_url,
                    "reclassified_ncn": config.source.reclassified_ncn_feature_service_url,
                    "official_road_classification": _road_classification_manifest(
                        config, temporary
                    ),
                    "observed_through_traffic": _observed_through_traffic_manifest(
                        config, temporary
                    ),
                    "elevation": _elevation_evidence_manifest(config, temporary, retrieved_at),
                },
                "files": files,
                "file_sha256": {
                    filename: sha256_file(file_paths[filename])
                    for filename in files
                },
                "provenance_file_sha256": {
                    filename: sha256_file(provenance_paths[filename])
                    for filename in provenance_files
                },
                "disclaimer": DISCLAIMER,
            }
            if retained_manifest is not None:
                # Retained-core augmentation is not a new OSM acquisition.  It
                # carries the former core identity/provenance byte-for-byte and
                # appends independently validated governed evidence.
                for key in (
                    "source_identifier",
                    "retrieved_at",
                    "attribution",
                    "disclaimer",
                ):
                    manifest[key] = retained_manifest[key]
                for key in ("area_id", "area_name"):
                    if key in retained_manifest:
                        manifest[key] = retained_manifest[key]
                    else:
                        manifest.pop(key, None)
                previous_sources = retained_manifest.get("evidence_sources")
                if isinstance(previous_sources, dict):
                    current_sources = manifest["evidence_sources"]
                    manifest["evidence_sources"] = {
                        **previous_sources,
                        **{
                            key: current_sources[key]
                            for key in (
                                "official_road_classification",
                                "observed_through_traffic",
                                "elevation",
                            )
                            if current_sources[key] is not None
                        },
                    }
                previous_hashes = retained_manifest.get("file_sha256", {})
                if isinstance(previous_hashes, dict):
                    for filename, digest in previous_hashes.items():
                        if (
                            filename in files
                            and filename
                            not in {
                                ELEVATION_EVIDENCE_FILENAME,
                                "ea-survey-index.geojson",
                                "ea-authority-boundaries.geojson",
                                SAMPLE_LEDGER_FILENAME,
                                EA_RETAINED_ROUTE_FILENAME,
                            }
                            and filename not in regenerated_governed_files
                            and manifest["file_sha256"].get(filename) != digest
                        ):
                            raise ValueError("retained-core snapshot changed a governed core file")
                previous_provenance_hashes = _manifest_hashes(
                    retained_manifest,
                    "provenance_file_sha256",
                    label="retained snapshot provenance hashes",
                )
                for filename in retained_provenance_files:
                    if (
                        manifest["provenance_file_sha256"].get(filename)
                        != previous_provenance_hashes[filename]
                    ):
                        raise ValueError("retained-core snapshot changed governed provenance")
                if lineaged_source is not None:
                    manifest["retained_core_lineage"] = {
                        "source_snapshot_id": lineaged_source.snapshot_id,
                        "source_manifest_sha256": lineaged_source.manifest_sha256,
                    }
            (temporary / "snapshot.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
            _validate_snapshot(temporary)
            if stage_only:
                staged = StagedSnapshot(path=temporary, destination=destination)
                remove_temporary = False
                LOGGER.info(
                    "Snapshot validated and staged path=%s destination=%s files=%d",
                    staged.path,
                    staged.destination,
                    len(files),
                )
                return staged
            _replace_snapshot_directory(temporary, destination)
            LOGGER.info(
                "Snapshot validated and committed path=%s files=%d", destination, len(files)
            )
        finally:
            if remove_temporary and temporary.exists():
                shutil.rmtree(temporary)
    return destination


def snapshot(
    config: AreaConfig,
    *,
    replace: bool = False,
    retain_core: bool = False,
    osm_adapter: OSMAdapter | None = None,
) -> Path:
    """Materialise and promote an immutable, attributable source snapshot."""

    result = _materialise_snapshot(
        config,
        replace=replace,
        retain_core=retain_core,
        osm_adapter=osm_adapter,
    )
    if isinstance(result, StagedSnapshot):  # pragma: no cover - internal invariant.
        raise ValueError("snapshot materialisation unexpectedly remained staged")
    return result


def stage_retained_core_snapshot(config: AreaConfig) -> StagedSnapshot:
    """Build and validate a retained-core target without making it visible."""

    destination = _snapshot_destination(config)
    if destination.exists():
        raise ValueError("staged snapshot destination already exists")
    if config.source.retained_core_source is None:
        raise ValueError("staged retained-core snapshot requires explicit parent lineage")
    result = _materialise_snapshot(
        config,
        retain_core=True,
        stage_only=True,
    )
    if not isinstance(result, StagedSnapshot):  # pragma: no cover - guarded above.
        raise ValueError("retained-core snapshot was not staged")
    return result


def stage_ea_recovery_snapshot(config: AreaConfig) -> StagedSnapshot:
    """Stage one clean target from the exactly governed legacy EA parent."""

    destination = _snapshot_destination(config)
    if destination.exists():
        raise ValueError("staged snapshot destination already exists")
    lineage = config.source.retained_core_source
    if lineage is None:
        raise ValueError("staged retained-core snapshot requires explicit parent lineage")
    from satn.ea_snapshot_recovery import validate_legacy_ea_recovery_parent

    parent = validate_legacy_ea_recovery_parent(
        config.source.snapshot_dir,
        snapshot_id=lineage.snapshot_id,
        manifest_sha256=lineage.manifest_sha256,
    )
    result = _materialise_snapshot(
        config,
        retain_core=True,
        stage_only=True,
        ea_recovery_parent=parent,
    )
    if not isinstance(result, StagedSnapshot):  # pragma: no cover - guarded above.
        raise ValueError("EA recovery snapshot was not staged")
    return result


def promote_staged_snapshot(staged: StagedSnapshot) -> Path:
    """Atomically expose one validated staged snapshot only when its target is absent."""

    source = staged.path
    destination = staged.destination
    if source.is_symlink() or not source.is_dir():
        raise ValueError("staged snapshot is missing or unsafe")
    if destination.is_symlink() or destination.exists():
        raise ValueError("staged snapshot destination already exists")
    if source.resolve(strict=True).parent != destination.parent.resolve(strict=True):
        raise ValueError("staged snapshot must be a sibling of its destination")
    _validate_snapshot(source)
    manifest = json.loads(
        _regular_sibling(source, "snapshot.json", label="staged snapshot manifest").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(manifest, dict) or manifest.get("snapshot_id") != destination.name:
        raise ValueError("staged snapshot identity differs from its destination")
    try:
        source.rename(destination)
    except FileExistsError as error:
        raise ValueError("staged snapshot destination already exists") from error
    except OSError as error:
        if destination.exists():
            raise ValueError("staged snapshot destination already exists") from error
        raise
    _validate_snapshot(destination)
    return destination


def _write_fixture_snapshot(config: AreaConfig, temporary: Path) -> tuple[str, list[str]]:
    if config.source.fixture_dir is None:
        raise ValueError("fixture sources require source.fixture_dir")
    for filename in CORE_SOURCE_FILES:
        shutil.copy2(config.source.fixture_dir / filename, temporary / filename)
    context_source = config.source.fixture_dir / "context.geojson"
    if context_source.exists():
        shutil.copy2(context_source, temporary / "context.geojson")
    else:
        network = gpd.read_file(temporary / "network.geojson")
        derive_context_layers(network).to_file(temporary / "context.geojson", driver="GeoJSON")
    files = [*CORE_SOURCE_FILES, "context.geojson"]
    if _snapshot_official_road_classification(config, temporary):
        files.append(ROAD_CLASSIFICATION_FILENAME)
    if _snapshot_observed_through_traffic(config, temporary):
        files.append(OBSERVED_THROUGH_TRAFFIC_FILENAME)
    elevation_source = config.source.fixture_dir / ELEVATION_EVIDENCE_FILENAME
    if config.source.national_elevation is not None:
        _snapshot_national_elevation(config, temporary)
        files.append(ELEVATION_EVIDENCE_FILENAME)
    elif elevation_source.exists():
        _validate_fixture_elevation_evidence(elevation_source)
        shutil.copy2(elevation_source, temporary / ELEVATION_EVIDENCE_FILENAME)
        files.append(ELEVATION_EVIDENCE_FILENAME)
    return str(config.source.fixture_dir), files


def _write_osm_snapshot(
    config: AreaConfig,
    temporary: Path,
    adapter: OSMAdapter,
) -> tuple[str, list[str]]:
    data = adapter.acquire(config)
    places = derive_network_places(
        data.boundary,
        data.place_features,
        data.stations,
        data.network,
        config,
    )
    strategic_destinations = derive_strategic_destinations(
        data.facilities,
        config.source.strategic_destination_source_ids,
        places.crs,
    )
    if not strategic_destinations.empty:
        places = gpd.GeoDataFrame(
            pd.concat([places, strategic_destinations], ignore_index=True),
            geometry="geometry",
            crs=places.crs,
        ).sort_values("place_id")
    context = derive_context_layers(
        data.network,
        data.ncn_routes,
        data.facilities,
        data.circulation_boundaries,
    )
    communities = assess_community_urban_eligibility(
        places[places["kind"] == "community"],
        data.network,
        context,
        config.source,
    )
    places = gpd.GeoDataFrame(
        pd.concat(
            [communities, places[places["kind"] != "community"]],
            ignore_index=True,
            sort=False,
        ),
        geometry="geometry",
        crs=places.crs,
    ).sort_values("place_id")
    context = govern_network_scope_for_urban_communities(
        context,
        communities[communities["urban_circulation_eligible"].astype(bool)],
        urban_scope_buffer_km=config.source.urban_scope_buffer_km,
    )
    frames = {
        "boundary.geojson": data.boundary.to_crs(4326),
        "places.geojson": places.to_crs(4326),
        "network.geojson": data.network.to_crs(4326),
        "osm-place-features.geojson": data.place_features.to_crs(4326),
        "osm-stations.geojson": data.stations.to_crs(4326),
        "context.geojson": context.to_crs(4326),
    }
    for filename, frame in frames.items():
        frame.to_file(temporary / filename, driver="GeoJSON")
    if _snapshot_official_road_classification(config, temporary):
        frames[ROAD_CLASSIFICATION_FILENAME] = gpd.read_file(
            temporary / ROAD_CLASSIFICATION_FILENAME
        )
    if _snapshot_observed_through_traffic(config, temporary):
        frames[OBSERVED_THROUGH_TRAFFIC_FILENAME] = gpd.read_file(
            temporary / OBSERVED_THROUGH_TRAFFIC_FILENAME
        )
    if data.graph is not None:
        import osmnx as ox

        ox.save_graphml(data.graph, temporary / "network.graphml")
    if config.source.national_elevation is not None:
        _snapshot_national_elevation(config, temporary)
        frames[ELEVATION_EVIDENCE_FILENAME] = gpd.read_file(temporary / ELEVATION_EVIDENCE_FILENAME)
    return json.dumps(config.source.boundary_queries, separators=(",", ":")), list(frames)


def _snapshot_official_road_classification(
    config: AreaConfig,
    temporary: Path,
) -> bool:
    governed = config.source.official_road_classification
    if governed is None:
        return False
    source, fingerprint = _load_governed_line_source(governed, "official road classification")
    boundary = gpd.read_file(temporary / "boundary.geojson")
    if boundary.crs is None or boundary.empty:
        raise ValueError("snapshot boundary is required to select official road classification")
    governed_area = boundary.to_crs(source.crs).geometry.union_all()
    if governed_area.is_empty or not governed_area.is_valid:
        raise ValueError("snapshot boundary is invalid for official road classification")
    source = source[source.geometry.intersects(governed_area)]
    classification_column = next(
        (
            column
            for column in ("official_classification", "road_classification", "classification")
            if column in source
        ),
        None,
    )
    if classification_column is None:
        raise ValueError("official road classification requires an official_classification column")
    rows: list[dict[str, object]] = []
    for _, feature in source.iterrows():
        if not isinstance(feature.geometry, (LineString, MultiLineString)):
            continue
        classification = _normalise_official_classification(feature.get(classification_column))
        feature_id = _official_road_identifier(feature, classification)
        rows.append(
            {
                "official_feature_id": feature_id,
                "official_classification": classification,
                "official_road_number": _first_source_text(
                    feature,
                    "official_road_number",
                    "road_classification_number",
                ),
                "official_road_name": _first_source_text(
                    feature,
                    "official_road_name",
                    "name_1",
                ),
                "official_road_function": _first_source_text(
                    feature,
                    "official_road_function",
                    "road_function",
                ),
                "source_id": governed.source_id,
                "effective_date": governed.effective_date.isoformat(),
                "licence": governed.licence,
                "content_fingerprint": fingerprint,
                "geometry": feature.geometry,
            }
        )
    if not rows:
        raise ValueError("official road classification source has no line features")
    frame = gpd.GeoDataFrame(
        rows,
        columns=ROAD_CLASSIFICATION_COLUMNS,
        geometry="geometry",
        crs=source.crs,
    )
    frame.to_crs(4326).to_file(temporary / ROAD_CLASSIFICATION_FILENAME, driver="GeoJSON")
    return True


def _optional_source_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _first_source_text(feature: pd.Series, *columns: str) -> str | None:
    for column in columns:
        text = _optional_source_text(feature.get(column))
        if text is not None:
            return text
    return None


def _snapshot_observed_through_traffic(
    config: AreaConfig,
    temporary: Path,
) -> bool:
    governed = config.source.observed_through_traffic
    if governed is None:
        return False
    source, fingerprint = _load_governed_line_source(governed, "observed through-traffic")
    rows: list[dict[str, object]] = []
    for index, feature in source.iterrows():
        if not isinstance(feature.geometry, (LineString, MultiLineString)):
            continue
        rows.append(
            {
                "evidence_id": _source_identifier(feature, index),
                "source_id": governed.source_id,
                "effective_date": governed.effective_date.isoformat(),
                "licence": governed.licence,
                "content_fingerprint": fingerprint,
                "geometry": feature.geometry,
            }
        )
    if not rows:
        raise ValueError("observed through-traffic source has no line features")
    gpd.GeoDataFrame(rows, geometry="geometry", crs=source.crs).to_crs(4326).to_file(
        temporary / OBSERVED_THROUGH_TRAFFIC_FILENAME,
        driver="GeoJSON",
    )
    return True


def _validate_fixture_elevation_evidence(path: Path) -> None:
    evidence = gpd.read_file(path)
    required = {
        "evidence_id",
        "source_id",
        "effective_date",
        "licence",
        "elevation_m",
    }
    missing = sorted(required - set(evidence.columns))
    if missing:
        raise ValueError(
            f"fixture Elevation Evidence is missing governed fields: {', '.join(missing)}"
        )
    if evidence.empty or not evidence.geometry.geom_type.eq("Point").all():
        raise ValueError("fixture Elevation Evidence requires Point samples")
    if pd.to_numeric(evidence["elevation_m"], errors="coerce").isna().any():
        raise ValueError("fixture Elevation Evidence has unusable elevation_m values")
    for field in ("evidence_id", "source_id", "effective_date", "licence"):
        if evidence[field].isna().any() or evidence[field].astype(str).str.strip().eq("").any():
            raise ValueError(f"fixture Elevation Evidence has missing {field}")
    if evidence["evidence_id"].astype(str).duplicated().any():
        raise ValueError("fixture Elevation Evidence has duplicate evidence_id values")


def _elevation_evidence_manifest(
    config: AreaConfig,
    path: Path,
    retrieved_at: str,
) -> dict[str, object] | None:
    evidence_path = path / ELEVATION_EVIDENCE_FILENAME
    if not evidence_path.exists():
        return None
    evidence = gpd.read_file(evidence_path)
    governed = config.source.national_elevation
    if governed is not None:
        manifest: dict[str, object] = {
            "provider": governed.provider,
            "source_id": governed.source_id,
            "effective_date": (
                governed.effective_date.isoformat()
                if governed.effective_date is not None
                else retrieved_at.split("T", maxsplit=1)[0]
            ),
            "date_kind": ("effective" if governed.effective_date is not None else "retrieved"),
            "licence": governed.licence,
            "attribution": governed.attribution,
            "bounded_to_compilation_area": True,
            "coverage_status": ("available" if not evidence.empty else "explicit-unknown"),
            "sample_count": len(evidence),
            "content_fingerprint": sha256_file(evidence_path),
            "retrieved_at": retrieved_at,
        }
        ea_provenance = _ea_elevation_acquisition_provenance(governed, snapshot_dir=path)
        manifest.update(ea_provenance)
        if ea_provenance:
            manifest["coverage_status"] = ea_provenance["coverage_status"]
            manifest["effective_date"] = ea_provenance["effective_survey_date"]
            manifest["date_kind"] = "official-sampled-ed_flown"
        return manifest
    return {
        "source_ids": sorted({str(value) for value in evidence["source_id"]}),
        "effective_dates": sorted(
            {str(value).split(" ", maxsplit=1)[0] for value in evidence["effective_date"]}
        ),
        "licences": sorted({str(value) for value in evidence["licence"]}),
        "sample_count": len(evidence),
        "content_fingerprint": sha256_file(evidence_path),
    }


def _recompute_ea_sample_ledger(
    evidence_path: Path,
    ledger_path: Path,
    *,
    authority_boundaries_path: Path,
    survey_index_path: Path,
    elevation_field: str = "elevation_m",
    ledger_schema_version: str = SAMPLE_LEDGER_SCHEMA_VERSION,
    tile_requests: dict[str, str] | None = None,
) -> dict[str, object]:
    """Derive EA coverage solely from retained evidence and ledger bytes.

    The acquisition sidecar is deliberately not an authority for these values:
    it is useful operational context, but it may have changed after acquisition.
    """
    if ledger_schema_version not in {
        LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION,
        SAMPLE_LEDGER_SCHEMA_VERSION,
    }:
        raise ValueError("EA sample ledger schema version is unsupported")
    is_legacy = ledger_schema_version == LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION
    rows = read_sample_ledger(ledger_path)
    evidence = gpd.read_file(evidence_path).to_crs(27700)
    boundaries = gpd.read_file(authority_boundaries_path).to_crs(27700)
    authority_id_field = next(
        (field for field in ("authority_id", "boundary_id", "id") if field in boundaries), None
    )
    if authority_id_field is None or boundaries.empty:
        raise ValueError("EA authority boundaries lack stable retained identities")
    survey_index = gpd.read_file(survey_index_path).to_crs(27700)
    if "id" not in survey_index or survey_index["id"].astype(str).duplicated().any():
        raise ValueError("EA survey index has duplicate or missing official feature identities")
    observed: dict[tuple[str, int], tuple[str, float, int, int]] = {}
    for _, sample in evidence.iterrows():
        route_id, sample_index = str(sample.get("route_id")), int(sample.get("sample_index"))
        point = sample.geometry
        if point is None or point.is_empty:
            raise ValueError("EA Elevation Evidence contains an empty sample geometry")
        identity = (route_id, sample_index)
        row_hash = sample.get("evidence_row_sha256")
        if not isinstance(row_hash, str):
            raise ValueError("EA Elevation Evidence lacks an immutable evidence row hash")
        if identity in observed:
            raise ValueError("EA Elevation Evidence has duplicate route/sample observations")
        observed[identity] = (
            row_hash,
            float(sample[elevation_field]),
            round(point.x * 1000),
            round(point.y * 1000),
        )
    buckets: dict[str, dict[str, object]] = {}
    transitions: list[dict[str, object]] = []
    by_route: dict[str, list[dict[str, object]]] = {}
    chosen_dates: list[str] = []
    for row in rows:
        required = {
            "schema_version",
            "route_id",
            "sample_index",
            "east_mm",
            "north_mm",
            "authority_id",
            "bucket",
            "availability",
            "elevation_m",
            "survey_feature_id",
            "ed_flown",
            "resolution_m",
            "evidence_row_sha256",
            "route_position",
            "previous_sample_index",
            "next_sample_index",
        }
        if not is_legacy:
            required.update({"tile_request_fingerprint", "tile_raw_sha256", "tile_pixel_status"})
        if not required <= set(row) or row["schema_version"] != ledger_schema_version:
            raise ValueError("EA sample ledger lacks required immutable fields")
        allowed_availability = {"available", "nodata"}
        if not is_legacy:
            allowed_availability.add("explicit-unknown")
        if (
            row["bucket"] not in {"authority", "routing-buffer"}
            or row["availability"] not in allowed_availability
        ):
            raise ValueError("EA sample ledger has invalid availability or authority bucket")
        identity = (str(row["route_id"]), int(row["sample_index"]))
        evidence_hash = row["evidence_row_sha256"]
        point = Point(int(row["east_mm"]) / 1000, int(row["north_mm"]) / 1000)
        matched_authorities = boundaries[boundaries.geometry.covers(point)]
        expected_authority_id = (
            str(sorted(matched_authorities[authority_id_field].astype(str))[0])
            if not matched_authorities.empty
            else "routing-buffer"
        )
        expected_bucket = (
            "authority" if expected_authority_id != "routing-buffer" else "routing-buffer"
        )
        if row["authority_id"] != expected_authority_id or row["bucket"] != expected_bucket:
            raise ValueError(
                "EA sample ledger authority/buffer assignment differs from retained boundaries"
            )
        matches = survey_index[survey_index.geometry.covers(point)]
        choices = []
        for _, survey in matches.iterrows():
            date = str(survey.get("ed_flown") or "")[:10]
            try:
                resolution = float(survey.get("resolution"))
            except (TypeError, ValueError):
                resolution = float("inf")
            choices.append(
                (
                    -(int((date or "0000-00-00").replace("-", ""))),
                    resolution,
                    str(survey["id"]),
                    date or None,
                )
            )
        selected = sorted(choices)[0] if choices else None
        if selected is None:
            if any(
                row[field] is not None
                for field in ("survey_feature_id", "ed_flown", "resolution_m")
            ):
                raise ValueError(
                    "EA sample ledger invents survey evidence outside retained coverage"
                )
        elif (
            row["survey_feature_id"] != selected[2]
            or row["ed_flown"] != selected[3]
            or float(row.get("resolution_m")) != selected[1]
        ):
            raise ValueError("EA sample ledger survey selection differs from retained index")
        # A NoData WCS response still has official survey coverage.  Effective
        # survey date therefore comes from the same selected index row during
        # acquisition and retained-ledger recomputation.
        if isinstance(row.get("ed_flown"), str):
            chosen_dates.append(str(row["ed_flown"]))
        if row["availability"] == "available":
            if not isinstance(evidence_hash, str) or identity not in observed:
                raise ValueError(
                    "EA sample ledger available observation does not bind retained evidence"
                )
            _hash, elevation, east_mm, north_mm = observed[identity]
            expected_hash = evidence_row_sha256(
                route_id=identity[0],
                sample_index=identity[1],
                east_mm=int(row["east_mm"]),
                north_mm=int(row["north_mm"]),
                elevation_m=elevation,
            )
            if (
                row["elevation_m"] is None
                or round(float(row["elevation_m"]), 3) != round(elevation, 3)
                or abs(int(row["east_mm"]) - east_mm) > 10
                or abs(int(row["north_mm"]) - north_mm) > 10
                or evidence_hash != expected_hash
                or _hash != expected_hash
            ):
                raise ValueError("EA sample ledger observation differs from retained evidence")
            if not isinstance(row["survey_feature_id"], str) or not isinstance(
                row["ed_flown"], str
            ):
                raise ValueError(
                    "EA sample ledger available observation lacks official survey evidence"
                )
        elif evidence_hash is not None or identity in observed:
            raise ValueError(
                "EA sample ledger missing observation conflicts with retained evidence"
            )
        if not is_legacy:
            tile_fingerprint = row["tile_request_fingerprint"]
            tile_digest = row["tile_raw_sha256"]
            pixel_status = row["tile_pixel_status"]
            if row["availability"] == "nodata":
                if (
                    selected is None
                    or row["elevation_m"] is not None
                    or evidence_hash is not None
                    or pixel_status != "validated-nodata"
                    or not isinstance(tile_fingerprint, str)
                    or not isinstance(tile_digest, str)
                    or tile_requests is None
                    or tile_requests.get(tile_fingerprint) != tile_digest
                ):
                    raise ValueError(
                        "EA sample ledger NoData lacks a governed validated tile/pixel proof"
                    )
            elif row["availability"] == "explicit-unknown":
                if (
                    row["elevation_m"] is not None
                    or evidence_hash is not None
                    or pixel_status != "unavailable"
                    or tile_fingerprint is not None
                    or tile_digest is not None
                ):
                    raise ValueError(
                        "EA sample ledger explicit-unknown observation is inconsistent"
                    )
            elif (
                pixel_status != "validated-value"
                or not isinstance(tile_fingerprint, str)
                or not isinstance(tile_digest, str)
                or tile_requests is None
                or tile_requests.get(tile_fingerprint) != tile_digest
            ):
                raise ValueError("EA sample ledger available observation lacks governed tile proof")
        key = str(row["authority_id"])
        bucket = buckets.setdefault(
            key,
            {
                "authority_id": key,
                "requested_sample_count": 0,
                "available_sample_count": 0,
                "nodata_sample_count": 0,
                **({} if is_legacy else {"explicit_unknown_sample_count": 0}),
            },
        )
        bucket["requested_sample_count"] = int(bucket["requested_sample_count"]) + 1
        if row["availability"] == "available":
            bucket["available_sample_count"] = int(bucket["available_sample_count"]) + 1
        elif row["availability"] == "nodata":
            bucket["nodata_sample_count"] = int(bucket["nodata_sample_count"]) + 1
        elif not is_legacy:
            bucket["explicit_unknown_sample_count"] = (
                int(bucket["explicit_unknown_sample_count"]) + 1
            )
        by_route.setdefault(identity[0], []).append(row)
    if set(observed) != {
        (str(row["route_id"]), int(row["sample_index"]))
        for row in rows
        if row["availability"] == "available"
    }:
        raise ValueError(
            "EA Elevation Evidence has observations absent from immutable sample ledger"
        )
    summary = []
    for item in buckets.values():
        requested, available = (
            int(item["requested_sample_count"]),
            int(item["available_sample_count"]),
        )
        item["status"] = (
            "available"
            if requested and available == requested
            else "partial"
            if available
            else "unavailable"
        )
        summary.append(item)
    for route_id, route_rows in sorted(by_route.items()):
        route_rows.sort(key=lambda row: int(row["sample_index"]))
        for position, row in enumerate(route_rows):
            if (
                row.get("route_position") != position
                or row.get("previous_sample_index")
                != (route_rows[position - 1]["sample_index"] if position else None)
                or row.get("next_sample_index")
                != (
                    route_rows[position + 1]["sample_index"]
                    if position + 1 < len(route_rows)
                    else None
                )
            ):
                raise ValueError(
                    "EA sample ledger route sequence is not contiguous retained provenance"
                )
        for before, after in pairwise(route_rows):
            if before["authority_id"] == after["authority_id"]:
                continue
            transitions.append(
                {
                    "route_id": route_id,
                    "before_sample_index": before["sample_index"],
                    "after_sample_index": after["sample_index"],
                    "from_authority_id": before["authority_id"],
                    "to_authority_id": after["authority_id"],
                    "status": "available"
                    if before["availability"] == after["availability"] == "available"
                    else "missing-elevation",
                }
            )
    available_total = sum(int(item["available_sample_count"]) for item in summary)
    requested_total = len(rows)
    nodata_total = sum(int(item["nodata_sample_count"]) for item in summary)
    explicit_unknown_total = (
        sum(int(item["explicit_unknown_sample_count"]) for item in summary) if not is_legacy else 0
    )
    result: dict[str, object] = {
        "sample_ledger_sha256": sha256_file(ledger_path),
        "requested_point_count": requested_total,
        "evidence_sample_count": len(observed),
        "nodata_sample_count": nodata_total,
        "coverage_status": "available"
        if requested_total and available_total == requested_total
        else "partial"
        if available_total
        else "unavailable",
        "effective_survey_date": max(chosen_dates) if chosen_dates else None,
        "authority_coverage": sorted(summary, key=lambda item: str(item["authority_id"])),
        "cross_boundary_transitions": transitions,
        "cross_boundary_sample_count": sum(item["status"] == "available" for item in transitions),
        "evidence_row_count": len(observed),
        "evidence_row_sha256s": sorted(value[0] for value in observed.values()),
    }
    if not is_legacy:
        result["explicit_unknown_sample_count"] = explicit_unknown_total
        result["availability_outcome"] = _ea_availability_outcome(
            available_total, nodata_total, explicit_unknown_total
        )
    return result


def _ea_availability_outcome(available: int, nodata: int, explicit_unknown: int) -> str:
    requested = available + nodata + explicit_unknown
    if requested and available == requested:
        return "available"
    if available:
        return "partial"
    if requested and nodata == requested:
        return "all-nodata"
    if requested and explicit_unknown == requested:
        return "all-explicit-unknown"
    return "all-missing-mixed"


def _validate_ea_ledger_completeness(*, rows: list[dict[str, object]], route_path: Path) -> None:
    """Prove every ledger identity is exactly the governed 10m route sequence."""
    routes = gpd.read_file(route_path)
    expected = [
        (
            str(sample["route_id"]),
            int(sample["sample_index"]),
            round(float(sample["geometry"].x) * 1000),
            round(float(sample["geometry"].y) * 1000),
        )
        for sample in eligible_route_samples(routes, spacing_m=10.0)[0]
    ]
    actual = [
        (str(row["route_id"]), int(row["sample_index"]), int(row["east_mm"]), int(row["north_mm"]))
        for row in rows
    ]
    if sorted(actual) != sorted(expected):
        raise ValueError("EA sample ledger is not complete for the retained governed 10m routes")


def _validate_streaming_ea_ledger_completeness(
    *, rows: list[dict[str, object]], route_path: Path
) -> tuple[bool, str]:
    """Reconcile a large route inventory exactly with bounded Python memory."""

    route_records: list[tuple[str, str, int, bool, Path]] = []
    sample_groups: list[tuple[str, int, Path]] = []
    has_primary = False
    primary_count = 0
    with tempfile.TemporaryDirectory(prefix=".satn-route-validation-") as directory:
        spool = Path(directory)
        for position, properties, geometry, source_crs in _iter_streaming_geojson_features(
            route_path
        ):
            frame_properties = dict(properties)
            if not frame_properties.get("feature_id") and not frame_properties.get("id"):
                frame_properties["feature_id"] = str(position)
            frame = gpd.GeoDataFrame(
                [{**frame_properties, "geometry": geometry}],
                geometry="geometry",
                crs=source_crs,
            ).to_crs(27700)
            validate_eligible_route_geometries(frame, source=route_path)
            row = frame.iloc[0]
            if FIXED_POINT_PRIMARY_FIELD in properties:
                has_primary = True
            if row.get("feature_type") not in ELIGIBLE_FEATURE_TYPES or pd.isna(
                row.get("topography_profile_id")
            ):
                continue
            feature_id = str(row.get("feature_id") or row.get("id") or position)
            feature_type = str(row["feature_type"])
            is_primary = row.get(FIXED_POINT_PRIMARY_FIELD) == True  # noqa: E712
            primary_count += int(is_primary)
            record = {
                "feature_id": feature_id,
                "feature_type": feature_type,
                "topography_profile_id": str(row["topography_profile_id"]),
                "geometry": _canonical_line_geometry(row.geometry),
            }
            record_path = spool / f"record-{position:012d}.json"
            record_path.write_text(
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            route_records.append((feature_type, feature_id, position, is_primary, record_path))

            samples = eligible_route_samples(frame, spacing_m=10.0)[0]
            sample_path = spool / f"samples-{position:012d}.jsonl"
            with sample_path.open("w", encoding="utf-8") as stream:
                for sample in samples:
                    point = sample["geometry"]
                    stream.write(
                        json.dumps(
                            [
                                str(sample["route_id"]),
                                int(sample["sample_index"]),
                                round(float(point.x) * 1000),
                                round(float(point.y) * 1000),
                            ],
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
            sample_groups.append((feature_id, position, sample_path))

        def expected_sample_rows() -> Iterator[list[object]]:
            for _feature_id, _position, sample_path in sorted(sample_groups):
                with sample_path.open(encoding="utf-8") as stream:
                    for line in stream:
                        value = json.loads(line)
                        if not isinstance(value, list):
                            raise ValueError("invalid temporary EA route sample reconciliation")
                        yield value

        expected_rows = expected_sample_rows()
        for actual in rows:
            try:
                expected = next(expected_rows)
            except StopIteration as error:
                raise ValueError(
                    "EA sample ledger is not complete for the retained governed 10m routes"
                ) from error
            actual_identity = [
                str(actual["route_id"]),
                int(actual["sample_index"]),
                int(actual["east_mm"]),
                int(actual["north_mm"]),
            ]
            if actual_identity != expected:
                raise ValueError(
                    "EA sample ledger is not complete for the retained governed 10m routes"
                )
        try:
            next(expected_rows)
        except StopIteration:
            pass
        else:
            raise ValueError(
                "EA sample ledger is not complete for the retained governed 10m routes"
            )

        selected_records = [
            record
            for record in route_records
            if not has_primary or record[3]
        ]
        if has_primary and primary_count == 0:
            raise ValueError("EA sampled routes do not mark a fixed-point primary route set")
        digest = hashlib.sha256()
        digest.update(b"[")
        for index, (_feature_type, _feature_id, _position, _primary, record_path) in enumerate(
            sorted(selected_records)
        ):
            if index:
                digest.update(b",")
            with record_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        digest.update(b"]")
        return has_primary, digest.hexdigest()


def _ea_elevation_acquisition_provenance(
    governed: NationalElevationConfig,
    *,
    snapshot_dir: Path | None = None,
) -> dict[str, object]:
    """Carry a validated EA acquisition sidecar into the immutable snapshot manifest.

    EA evidence is never trusted without this sidecar.  The sidecar itself is
    only a statement; it is independently bound to the official WFS index and
    the actual retained elevation bytes before entering the snapshot.
    """

    if governed.acquisition_contract != EA_LIDAR_WECA_ACQUISITION_CONTRACT or governed.path is None:
        return {}
    sidecar = (
        snapshot_dir / "elevation-evidence.manifest.json"
        if snapshot_dir is not None
        else governed.path.with_suffix(".manifest.json")
    )
    sidecar = _regular_sibling(
        sidecar.parent, sidecar.name, label="EA Elevation Evidence acquisition manifest"
    )
    try:
        acquisition = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("EA Elevation Evidence acquisition manifest is invalid JSON") from error
    contract_schema_version = acquisition.get("contract_schema_version")
    if contract_schema_version not in {
        LEGACY_CONTRACT_SCHEMA_VERSION,
        EA_ELEVATION_CONTRACT_VERSION,
    }:
        raise ValueError("EA Elevation Evidence acquisition manifest has invalid contract schema")
    is_legacy = contract_schema_version == LEGACY_CONTRACT_SCHEMA_VERSION
    required = {
        "source_id": EA_LIDAR_SOURCE_ID,
        "acquisition_protocol": "two-pass-fixed-point/v1",
        "contract_schema_version": contract_schema_version,
        "dataset_id": EA_LIDAR_DATASET_ID,
        "coverage_id": EA_LIDAR_COVERAGE_ID,
        "endpoint": EA_LIDAR_ENDPOINT,
        "licence": EA_LIDAR_LICENCE,
        "attribution": EA_LIDAR_ATTRIBUTION,
        "dataset_title": "LIDAR Composite Digital Terrain Model (DTM) - 1m",
        "source_resolution_m": 1,
        "output_sample_spacing_m": 10,
        "vertical_accuracy": "+/-15cm RMSE",
    }
    for field, expected in required.items():
        if acquisition.get(field) != expected:
            raise ValueError(f"EA Elevation Evidence acquisition manifest has invalid {field}")
    output_digest = acquisition.get("output_sha256")
    network_digest = acquisition.get("pre_elevation_network_sha256")
    if not isinstance(output_digest, str) or len(output_digest) != 64:
        raise ValueError("EA Elevation Evidence acquisition manifest is missing output_sha256")
    if not isinstance(network_digest, str) or len(network_digest) != 64:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest is missing pre_elevation_network_sha256"
        )
    actual_digest = sha256_file(governed.path)
    if output_digest != actual_digest:
        raise ValueError("EA Elevation Evidence acquisition manifest does not bind its output")
    ledger_name = acquisition.get("sample_ledger_path")
    ledger_digest = acquisition.get("sample_ledger_sha256")
    expected_ledger_schema = (
        LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION if is_legacy else SAMPLE_LEDGER_SCHEMA_VERSION
    )
    if (
        acquisition.get("sample_ledger_schema_version") != expected_ledger_schema
        or not isinstance(ledger_name, str)
        or Path(ledger_name).name != ledger_name
        or not isinstance(ledger_digest, str)
        or len(ledger_digest) != 64
    ):
        raise ValueError("EA Elevation Evidence acquisition manifest lacks immutable sample ledger")
    preflight = acquisition.get("survey_coverage_preflight")
    if not isinstance(preflight, dict) or preflight.get("status") not in {
        "available",
        "partial",
        "unavailable",
    }:
        raise ValueError("EA Elevation Evidence acquisition manifest lacks survey coverage")
    survey_index_value = acquisition.get("survey_index_path")
    authority_identity = preflight.get("authority_boundaries")
    authority_path_value = acquisition.get("authority_boundaries_path")
    if (
        not isinstance(survey_index_value, str)
        or Path(survey_index_value).name != survey_index_value
        or not isinstance(authority_identity, dict)
        or not isinstance(authority_path_value, str)
        or Path(authority_path_value).name != authority_path_value
    ):
        raise ValueError("EA Elevation Evidence acquisition manifest lacks immutable scope proofs")
    route_name = acquisition.get("sample_route_path")
    route_digest = acquisition.get("sample_route_sha256")
    if not isinstance(route_digest, str) or len(route_digest) != 64:
        raise ValueError("EA Elevation Evidence lacks the retained sampled-route digest")
    referenced_names = [ledger_name, survey_index_value, authority_path_value, route_name]
    if len(set(referenced_names)) != len(referenced_names):
        raise ValueError("EA Elevation Evidence manifest reuses immutable sibling filenames")
    retained_paths = {
        "ledger": _regular_sibling(sidecar.parent, ledger_name, label="EA sample ledger"),
        "survey_index": _regular_sibling(
            sidecar.parent, survey_index_value, label="EA survey index"
        ),
        "authority": _regular_sibling(
            sidecar.parent, authority_path_value, label="EA authority boundaries"
        ),
        "routes": _regular_sibling(sidecar.parent, route_name, label="EA sampled routes"),
    }
    # Do not consume one sidecar reference before every retained reference has
    # been shown to be a distinct regular sibling in the acquisition directory.
    evidence_path = (
        snapshot_dir / ELEVATION_EVIDENCE_FILENAME if snapshot_dir is not None else governed.path
    )
    ledger_path = retained_paths["ledger"]
    survey_index = retained_paths["survey_index"]
    authority_path = retained_paths["authority"]
    route_path = retained_paths["routes"]
    if sha256_file(ledger_path) != ledger_digest:
        raise ValueError("EA Elevation Evidence immutable sample ledger is missing or tampered")
    if authority_identity.get("raw_sha256") != sha256_file(authority_path):
        raise ValueError("EA Elevation Evidence authority-boundary binding is invalid")
    official_index = validate_official_weca_survey_index(survey_index)
    if acquisition.get("survey_index_sha256") != official_index["raw_sha256"]:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest does not bind official survey-index bytes"
        )
    if acquisition.get("survey_index_feature_sha256") != official_index["canonical_feature_sha256"]:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest does not bind official "
            "survey-index features"
        )
    if preflight.get("official_survey_index") != official_index:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest has forged survey-index preflight"
        )
    tile_requests: dict[str, str] | None = None
    if not is_legacy:
        requests = acquisition.get("requests")
        if not isinstance(requests, list):
            raise ValueError(
                "EA Elevation Evidence acquisition manifest lacks governed tile receipts"
            )
        tile_requests = {}
        for request in requests:
            if (
                not isinstance(request, dict)
                or request.get("status") != "available"
                or not isinstance(request.get("request_fingerprint"), str)
                or not isinstance(request.get("sha256"), str)
                or len(str(request["sha256"])) != 64
            ):
                continue
            fingerprint = str(request["request_fingerprint"])
            digest = str(request["sha256"])
            if fingerprint in tile_requests and tile_requests[fingerprint] != digest:
                raise ValueError("EA Elevation Evidence governed tile receipt is ambiguous")
            tile_requests[fingerprint] = digest
    recomputed = _recompute_ea_sample_ledger(
        evidence_path,
        ledger_path,
        authority_boundaries_path=authority_path,
        survey_index_path=survey_index,
        elevation_field=("elevation_m" if snapshot_dir is not None else governed.elevation_field),
        ledger_schema_version=expected_ledger_schema,
        tile_requests=tile_requests,
    )
    if sha256_file(route_path) != route_digest:
        raise ValueError("EA Elevation Evidence sampled routes are missing or tampered")
    ledger_rows = read_sample_ledger(ledger_path)
    if route_path.stat().st_size >= STREAMING_GEOJSON_THRESHOLD_BYTES:
        has_fixed_point_primary, retained_route_fingerprint = (
            _validate_streaming_ea_ledger_completeness(
                rows=ledger_rows,
                route_path=route_path,
            )
        )
        if has_fixed_point_primary and retained_route_fingerprint != network_digest:
            raise ValueError(
                "EA Elevation Evidence sampled routes detach the fixed-point primary route set"
            )
    else:
        retained_routes = gpd.read_file(route_path)
        validate_eligible_route_geometries(retained_routes, source=route_path)
        if (
            FIXED_POINT_PRIMARY_FIELD in retained_routes
            and fixed_point_route_fingerprint(retained_routes) != network_digest
        ):
            raise ValueError(
                "EA Elevation Evidence sampled routes detach the fixed-point primary route set"
            )
        _validate_ea_ledger_completeness(rows=ledger_rows, route_path=route_path)
    comparison_fields = [
        "requested_point_count",
        "evidence_sample_count",
        "nodata_sample_count",
        "effective_survey_date",
    ]
    if not is_legacy:
        comparison_fields.extend(["explicit_unknown_sample_count", "availability_outcome"])
    for field in comparison_fields:
        if acquisition.get(field) != recomputed[field]:
            raise ValueError(f"EA Elevation Evidence acquisition sidecar forges {field}")
    if acquisition.get("sample_validation", {}).get("status") != recomputed["coverage_status"]:
        raise ValueError(
            "EA Elevation Evidence acquisition sidecar forges sample validation status"
        )
    sample_validation = (
        preflight.get("sample_validation") if is_legacy else acquisition.get("sample_validation")
    )
    if not isinstance(sample_validation, dict):
        sample_validation = acquisition.get("sample_validation")
    if not isinstance(sample_validation, dict) or sample_validation.get("status") not in {
        "available",
        "partial",
        "unavailable",
    }:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest lacks actual complete sample validation"
        )
    authorities = sample_validation.get("authorities")
    if not isinstance(authorities, list) or len(authorities) != 5:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest lacks authority sample evidence"
        )
    count_fields = [
        "requested_sample_count",
        "available_sample_count",
        "nodata_sample_count",
    ]
    if not is_legacy:
        count_fields.append("explicit_unknown_sample_count")
    try:
        sample_totals = {
            field: sum(int(row.get(field, 0)) for row in authorities) for field in count_fields
        }
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("EA Elevation Evidence authority sample evidence is invalid") from error
    transitions = sample_validation.get("cross_boundary_transitions")
    if not isinstance(transitions, list):
        raise ValueError("EA Elevation Evidence sample transitions are missing")

    def transition_identity(items: list[object]) -> list[tuple[str, int, int, str]]:
        return sorted(
            (
                str(row.get("route_id")),
                int(row.get("before_sample_index")),
                int(row.get("after_sample_index")),
                str(row.get("status")),
            )
            for row in items
            if isinstance(row, dict)
        )

    if (
        sample_validation.get("status") != recomputed["coverage_status"]
        or sample_totals["requested_sample_count"] != recomputed["requested_point_count"]
        or sample_totals["available_sample_count"] != recomputed["evidence_sample_count"]
        or sample_totals["nodata_sample_count"] != recomputed["nodata_sample_count"]
        or (
            not is_legacy
            and sample_totals["explicit_unknown_sample_count"]
            != recomputed["explicit_unknown_sample_count"]
        )
        or (
            not is_legacy
            and sample_validation.get("availability_outcome") != recomputed["availability_outcome"]
        )
        or len(transitions) != len(transition_identity(transitions))
        or transition_identity(transitions)
        != transition_identity(recomputed["cross_boundary_transitions"])
    ):
        raise ValueError("EA Elevation Evidence sample totals or transitions are forged")
    requested = acquisition.get("requested_point_count")
    if not isinstance(requested, int) or requested <= 0:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest has invalid requested point count"
        )
    governed_input_fingerprint = acquisition.get("governed_input_fingerprint")
    if not isinstance(governed_input_fingerprint, str) or len(governed_input_fingerprint) != 64:
        raise ValueError(
            "EA Elevation Evidence acquisition manifest lacks governed input fingerprint"
        )
    provenance = {
        "ea_acquisition_manifest_sha256": sha256_file(sidecar),
        "acquisition_output_sha256": output_digest,
        "pre_elevation_network_sha256": network_digest,
        "acquisition_protocol": acquisition["acquisition_protocol"],
        "survey_index_sha256": official_index["raw_sha256"],
        "survey_index_feature_sha256": official_index["canonical_feature_sha256"],
        "coverage_status": recomputed["coverage_status"],
        "effective_survey_date": recomputed["effective_survey_date"],
        "governed_input_fingerprint": governed_input_fingerprint,
        "authority_boundary_sha256": authority_identity["raw_sha256"],
        "authority_coverage": recomputed["authority_coverage"],
        "sample_ledger_sha256": recomputed["sample_ledger_sha256"],
        "cross_boundary_transitions": recomputed["cross_boundary_transitions"],
        "evidence_row_sha256s": recomputed["evidence_row_sha256s"],
        "replay_inputs": {
            "schema_version": EA_FIXED_POINT_REPLAY_INPUT_SCHEMA_VERSION,
            "licence": acquisition["licence"],
            "files": {
                "authority_boundaries": {
                    "path": "ea-authority-boundaries.geojson",
                    "sha256": authority_identity["raw_sha256"],
                },
                "sample_ledger": {
                    "path": SAMPLE_LEDGER_FILENAME,
                    "sha256": ledger_digest,
                },
                "sample_routes": {
                    "path": EA_RETAINED_ROUTE_FILENAME,
                    "sha256": route_digest,
                },
                "survey_index": {
                    "path": "ea-survey-index.geojson",
                    "sha256": official_index["raw_sha256"],
                },
            },
        },
    }
    if not is_legacy:
        provenance["explicit_unknown_sample_count"] = recomputed["explicit_unknown_sample_count"]
        provenance["availability_outcome"] = recomputed["availability_outcome"]
    return provenance


def _validated_ea_snapshot_replay_inputs(snapshot_dir: Path) -> dict[str, Path]:
    """Resolve only complete, content-bound fixed-point replay inputs."""
    manifest_path = _regular_sibling(
        snapshot_dir,
        "snapshot.json",
        label="EA fixed-point snapshot manifest",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        elevation = manifest["evidence_sources"]["elevation"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("EA fixed-point snapshot replay metadata is invalid") from error
    if not isinstance(elevation, dict):
        raise ValueError("EA fixed-point snapshot replay metadata is invalid")
    replay = elevation.get("replay_inputs")
    if replay is None:
        raise ValueError("legacy EA fixed-point snapshot is not self-contained")
    if (
        not isinstance(replay, dict)
        or replay.get("schema_version") != EA_FIXED_POINT_REPLAY_INPUT_SCHEMA_VERSION
        or not isinstance(replay.get("licence"), str)
        or not replay["licence"].strip()
        or not isinstance(replay.get("files"), dict)
    ):
        raise ValueError("EA fixed-point snapshot replay contract is invalid")

    file_hashes = _manifest_hashes(
        manifest,
        "file_sha256",
        label="EA fixed-point replay file hashes",
    )
    provenance_hashes = _manifest_hashes(
        manifest,
        "provenance_file_sha256",
        label="EA fixed-point replay provenance hashes",
    )
    expected_names = {
        "authority_boundaries": "ea-authority-boundaries.geojson",
        "sample_ledger": SAMPLE_LEDGER_FILENAME,
        "sample_routes": EA_RETAINED_ROUTE_FILENAME,
        "survey_index": "ea-survey-index.geojson",
    }
    records = replay["files"]
    if set(records) != set(expected_names):
        raise ValueError("EA fixed-point snapshot replay files are incomplete")
    resolved: dict[str, Path] = {}
    for role, expected_name in expected_names.items():
        record = records[role]
        if not isinstance(record, dict):
            raise ValueError("EA fixed-point snapshot replay file record is invalid")
        digest = record.get("sha256")
        if (
            record.get("path") != expected_name
            or not isinstance(digest, str)
            or len(digest) != 64
            or file_hashes.get(expected_name) != digest
            or provenance_hashes.get(expected_name) != digest
        ):
            raise ValueError(f"EA fixed-point snapshot replay {role} is unbound")
        retained = _regular_sibling(
            snapshot_dir,
            expected_name,
            label=f"EA fixed-point replay {role}",
        )
        if sha256_file(retained) != digest:
            raise ValueError(f"EA fixed-point snapshot replay {role} is tampered")
        resolved[role] = retained

    sidecar = _regular_sibling(
        snapshot_dir,
        "elevation-evidence.manifest.json",
        label="EA fixed-point replay acquisition manifest",
    )
    sidecar_digest = sha256_file(sidecar)
    if (
        provenance_hashes.get(sidecar.name) != sidecar_digest
        or elevation.get("ea_acquisition_manifest_sha256") != sidecar_digest
    ):
        raise ValueError("EA fixed-point replay acquisition manifest is unbound")
    try:
        acquisition = json.loads(sidecar.read_text(encoding="utf-8"))
        authority_identity = acquisition["survey_coverage_preflight"]["authority_boundaries"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("EA fixed-point replay acquisition manifest is invalid") from error
    expected_sidecar_values = {
        "authority_boundaries_path": expected_names["authority_boundaries"],
        "sample_ledger_path": expected_names["sample_ledger"],
        "sample_route_path": expected_names["sample_routes"],
        "survey_index_path": expected_names["survey_index"],
        "sample_ledger_sha256": records["sample_ledger"]["sha256"],
        "sample_route_sha256": records["sample_routes"]["sha256"],
        "survey_index_sha256": records["survey_index"]["sha256"],
    }
    if (
        not isinstance(acquisition, dict)
        or acquisition.get("licence") != replay["licence"]
        or any(
            acquisition.get(field) != expected
            for field, expected in expected_sidecar_values.items()
        )
        or not isinstance(authority_identity, dict)
        or authority_identity.get("raw_sha256")
        != records["authority_boundaries"]["sha256"]
    ):
        raise ValueError("EA fixed-point replay acquisition inputs do not match the snapshot")
    return resolved


def _validate_canonical_retained_ea_evidence(
    evidence_path: Path,
    governed: NationalElevationConfig,
    acquisition: object,
) -> None:
    """Bind retained EA evidence bytes to governed and validated acquisition metadata."""
    if not isinstance(acquisition, dict):
        raise ValueError("EA elevation snapshot retained acquisition manifest must be an object")

    def required_equal(field: str, expected: object, label: str) -> None:
        if acquisition.get(field) != expected:
            raise ValueError(
                f"EA elevation snapshot retained acquisition manifest mismatches {label}"
            )

    required_equal("source_id", governed.source_id, "configured source identifier")
    required_equal("licence", governed.licence, "configured licence")
    canonical_evidence = canonical_ea_elevation_evidence_bytes(
        gpd.read_file(evidence_path),
        source_id=governed.source_id,
        licence=governed.licence,
        effective_date=str(acquisition["effective_survey_date"]),
        source_resolution_m=float(acquisition["source_resolution_m"]),
        output_sample_spacing_m=float(acquisition["output_sample_spacing_m"]),
    )
    if evidence_path.read_bytes() != canonical_evidence:
        raise ValueError("EA elevation snapshot retained evidence is not canonical GeoJSON")


def _snapshot_national_elevation(config: AreaConfig, temporary: Path) -> None:
    governed = config.source.national_elevation
    if governed is None:
        return
    ea_metadata: dict[str, object] | None = None
    if governed.provider == "local-geojson":
        if governed.path is None or not governed.path.exists():
            raise ValueError("configured national Elevation Evidence path is missing")
        source = gpd.read_file(governed.path)
        if governed.acquisition_contract == EA_LIDAR_WECA_ACQUISITION_CONTRACT:
            configured_sidecar = governed.path.with_suffix(".manifest.json")
            sidecar = _regular_sibling(
                configured_sidecar.parent,
                configured_sidecar.name,
                label="EA Elevation Evidence acquisition manifest",
            )
            # Validate first; only an independently pinned official response is copied.
            _ea_elevation_acquisition_provenance(governed)
            acquisition = json.loads(sidecar.read_text(encoding="utf-8"))
            ea_metadata = {
                "effective_date": acquisition["effective_survey_date"],
                "source_resolution_m": acquisition["source_resolution_m"],
                "output_sample_spacing_m": acquisition["output_sample_spacing_m"],
            }
            survey_index = _regular_sibling(
                sidecar.parent, acquisition["survey_index_path"], label="EA survey index"
            )
            authority_boundaries = _regular_sibling(
                sidecar.parent,
                acquisition["authority_boundaries_path"],
                label="EA authority boundaries",
            )
            ledger = _regular_sibling(
                sidecar.parent, acquisition.get("sample_ledger_path"), label="EA sample ledger"
            )
            sampled_routes = _regular_sibling(
                sidecar.parent, acquisition.get("sample_route_path"), label="EA sampled routes"
            )
            # The immutable sidecar points only at the immutable sibling proofs;
            # never leave a release depending on a mutable acquisition directory.
            acquisition["survey_index_path"] = "ea-survey-index.geojson"
            acquisition["authority_boundaries_path"] = "ea-authority-boundaries.geojson"
            acquisition["sample_ledger_path"] = SAMPLE_LEDGER_FILENAME
            acquisition["sample_route_path"] = EA_RETAINED_ROUTE_FILENAME
            (temporary / "elevation-evidence.manifest.json").write_text(
                json.dumps(acquisition, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            shutil.copy2(survey_index, temporary / "ea-survey-index.geojson")
            shutil.copy2(authority_boundaries, temporary / "ea-authority-boundaries.geojson")
            shutil.copy2(ledger, temporary / SAMPLE_LEDGER_FILENAME)
            shutil.copy2(sampled_routes, temporary / EA_RETAINED_ROUTE_FILENAME)
    else:
        source = _load_remote_elevation(config, temporary)
    if source.crs is None:
        raise ValueError("national Elevation Evidence has no CRS")
    if governed.elevation_field not in source:
        raise ValueError(
            "national Elevation Evidence is missing configured elevation field: "
            f"{governed.elevation_field}"
        )
    if not source.empty and not source.geometry.geom_type.eq("Point").all():
        raise ValueError("national Elevation Evidence requires Point samples")
    boundary = gpd.read_file(temporary / "boundary.geojson")
    compilation_area = (
        boundary.to_crs(27700).geometry.buffer(config.source.external_buffer_km * 1000).union_all()
    )
    bounded = source.to_crs(27700)
    bounded = bounded[bounded.geometry.intersects(compilation_area)].to_crs(source.crs)
    rows: list[dict[str, object]] = []
    evidence_date = (
        str(ea_metadata["effective_date"])
        if ea_metadata is not None
        else governed.effective_date.isoformat()
        if governed.effective_date is not None
        else datetime.now(UTC).date().isoformat()
    )
    for _index, sample in bounded.iterrows():
        try:
            elevation = float(sample[governed.elevation_field])
        except (TypeError, ValueError) as error:
            raise ValueError("national Elevation Evidence has unusable heights") from error
        if not math.isfinite(elevation):
            raise ValueError("national Elevation Evidence has unusable heights")
        identifier = sample.get(governed.identifier_field)
        if pd.isna(identifier) or not str(identifier).strip():
            identifier = hashlib.sha256(sample.geometry.wkb).hexdigest()[:16]
        metric_point = gpd.GeoSeries([sample.geometry], crs=source.crs).to_crs(27700).iloc[0]
        rows.append(
            {
                "evidence_id": str(identifier),
                "source_id": governed.source_id,
                "effective_date": evidence_date,
                "licence": governed.licence,
                "elevation_m": elevation,
                **(
                    {
                        "source_resolution_m": ea_metadata["source_resolution_m"],
                        "output_sample_spacing_m": ea_metadata["output_sample_spacing_m"],
                    }
                    if ea_metadata is not None
                    else {}
                ),
                "evidence_row_sha256": (
                    str(sample["evidence_row_sha256"])
                    if "evidence_row_sha256" in sample
                    and pd.notna(sample.get("evidence_row_sha256"))
                    else evidence_row_sha256(
                        route_id=str(sample.get("route_id")),
                        sample_index=int(sample.get("sample_index")),
                        east_mm=round(metric_point.x * 1000),
                        north_mm=round(metric_point.y * 1000),
                        elevation_m=elevation,
                    )
                    if "route_id" in sample and "sample_index" in sample
                    else None
                ),
                **{
                    field: sample.get(field)
                    for field in (
                        "route_id",
                        "sample_index",
                        "evidence_row_sha256",
                        "vertical_accuracy_m",
                        "source_resolution_m",
                        "output_sample_spacing_m",
                    )
                    if field in sample and pd.notna(sample.get(field))
                },
                "geometry": sample.geometry,
            }
        )
    identifiers = [str(row["evidence_id"]) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("national Elevation Evidence has duplicate sample identifiers")
    columns = [
        "evidence_id",
        "source_id",
        "effective_date",
        "licence",
        "elevation_m",
        *[
            field
            for field in (
                "route_id",
                "sample_index",
                "evidence_row_sha256",
                "vertical_accuracy_m",
                "source_resolution_m",
                "output_sample_spacing_m",
            )
            if any(field in row for row in rows)
        ],
        "geometry",
    ]
    evidence = gpd.GeoDataFrame(
        rows,
        columns=columns,
        geometry="geometry",
        crs=source.crs,
    ).sort_values("evidence_id")
    evidence_path = temporary / ELEVATION_EVIDENCE_FILENAME
    if ea_metadata is not None:
        evidence_path.write_bytes(
            canonical_ea_elevation_evidence_bytes(
                evidence,
                source_id=governed.source_id,
                licence=governed.licence,
                effective_date=str(ea_metadata["effective_date"]),
                source_resolution_m=float(ea_metadata["source_resolution_m"]),
                output_sample_spacing_m=float(ea_metadata["output_sample_spacing_m"]),
            )
        )
    else:
        evidence.to_crs(4326).to_file(evidence_path, driver="GeoJSON")


def _load_remote_elevation(
    config: AreaConfig,
    temporary: Path,
) -> gpd.GeoDataFrame:
    governed = config.source.national_elevation
    if governed is None or not governed.url:
        raise ValueError("remote national Elevation Evidence requires url")
    boundary = gpd.read_file(temporary / "boundary.geojson").to_crs(27700)
    compilation_area = gpd.GeoDataFrame(
        geometry=boundary.geometry.buffer(config.source.external_buffer_km * 1000),
        crs=27700,
    ).to_crs(4326)
    minx, miny, maxx, maxy = compilation_area.total_bounds
    parsed = urllib.parse.urlparse(governed.url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("bbox", ",".join(f"{value:.8f}" for value in (minx, miny, maxx, maxy))))
    url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
    request = urllib.request.Request(url, headers={"Accept": "application/geo+json"})
    with urllib.request.urlopen(request, timeout=governed.timeout_seconds) as response:
        payload = json.loads(response.read())
    if payload.get("type") != "FeatureCollection":
        raise ValueError("remote national Elevation Evidence is not a GeoJSON FeatureCollection")
    features = payload.get("features", [])
    if not features:
        return gpd.GeoDataFrame(
            columns=[governed.elevation_field, "geometry"],
            geometry="geometry",
            crs=4326,
        )
    return gpd.GeoDataFrame.from_features(features, crs=4326)


def _load_governed_line_source(
    governed: GovernedSpatialSourceConfig,
    label: str,
) -> tuple[gpd.GeoDataFrame, str]:
    if not governed.path.exists():
        raise ValueError(f"{label} source is missing: {governed.path}")
    fingerprint = sha256_file(governed.path)
    source = gpd.read_file(governed.path)
    if source.crs is None:
        raise ValueError(f"{label} source has no CRS")
    return source, fingerprint


def _normalise_official_classification(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if text in {"a", "a road", "class a"}:
        return OfficialRoadClassification.A_ROAD.value
    if text in {"b", "b road", "class b"}:
        return OfficialRoadClassification.B_ROAD.value
    if text in {
        "c",
        "c road",
        "class c",
        "cu",
        "classified unnumbered",
        "classified unnumbered road",
    }:
        return OfficialRoadClassification.CLASSIFIED_UNNUMBERED.value
    if text in {"unclassified", "u", "unclassified road"}:
        return OfficialRoadClassification.UNCLASSIFIED.value
    return OfficialRoadClassification.UNKNOWN.value


def _official_road_identifier(feature: pd.Series, classification: str) -> str:
    for key in ("official_feature_id", "road_id", "osmid", "osm_id", "id"):
        value = feature.get(key)
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return str(value)
    identity = f"{classification}:{feature.geometry.wkb_hex}"
    return f"official-road-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def _road_classification_manifest(
    config: AreaConfig,
    snapshot_path: Path,
) -> dict[str, object] | None:
    governed = config.source.official_road_classification
    path = snapshot_path / ROAD_CLASSIFICATION_FILENAME
    if governed is None or not path.exists():
        return None
    manifest = _governed_source_manifest(governed, path, ROAD_CLASSIFICATION_FILENAME)
    manifest["selection"] = {
        "contract": "satn-official-road-boundary-selection/v1",
        "predicate": "intersects",
        "geometry_treatment": "retain-whole-source-feature",
        "selected_feature_count": len(gpd.read_file(path)),
        "source_export_sha256": sha256_file(governed.path),
        "boundary_file": "boundary.geojson",
        "boundary_sha256": sha256_file(snapshot_path / "boundary.geojson"),
    }
    return manifest


def _observed_through_traffic_manifest(
    config: AreaConfig,
    snapshot_path: Path,
) -> dict[str, object] | None:
    governed = config.source.observed_through_traffic
    path = snapshot_path / OBSERVED_THROUGH_TRAFFIC_FILENAME
    if governed is None or not path.exists():
        return None
    return _governed_source_manifest(governed, path, OBSERVED_THROUGH_TRAFFIC_FILENAME)


def _governed_source_manifest(
    governed: GovernedSpatialSourceConfig,
    path: Path,
    filename: str,
) -> dict[str, object]:
    snapshotted = gpd.read_file(path)
    return {
        "source_id": governed.source_id,
        "effective_date": governed.effective_date.isoformat(),
        "licence": governed.licence,
        "attribution": f"{governed.source_id}; {governed.licence}",
        "content_fingerprint": str(snapshotted.iloc[0]["content_fingerprint"]),
        "snapshot_file": filename,
    }


def _load_ncn_features(
    service_url: str,
    boundary: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    return _load_arcgis_cycle_routes(
        service_url,
        boundary,
        where="RouteType IN ('NCN','LINK') OR Greenway = 'Yes'",
        source_label="NCN",
    )


def _load_reclassified_ncn_features(
    service_url: str,
    boundary: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    routes = _load_arcgis_cycle_routes(
        service_url,
        boundary,
        where="1=1",
        source_label="reclassified NCN",
    )
    if not routes.empty:
        routes["RouteType"] = "RECLASSIFIED"
    return routes


def _load_arcgis_cycle_routes(
    service_url: str,
    boundary: gpd.GeoDataFrame,
    *,
    where: str,
    source_label: str,
) -> gpd.GeoDataFrame:
    min_x, min_y, max_x, max_y = boundary.to_crs(4326).total_bounds
    page_size = 2000
    offset = 0
    features: list[dict[str, object]] = []
    page_fingerprints: set[str] = set()
    while True:
        parameters = urllib.parse.urlencode(
            {
                "f": "geojson",
                "where": where,
                "geometry": f"{min_x},{min_y},{max_x},{max_y}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "outSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            }
        )
        request = urllib.request.Request(
            f"{service_url.rstrip('/')}/0/query?{parameters}",
            headers={"User-Agent": "banes-satn/0.1 cycle-route snapshot"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.load(response)
        if "error" in payload:
            raise ValueError(f"{source_label} feature service failed: {payload['error']}")
        page = payload.get("features", [])
        if not isinstance(page, list):
            raise ValueError(
                f"{source_label} feature service returned an invalid features collection"
            )
        fingerprint = hashlib.sha256(
            json.dumps(page, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if page and fingerprint in page_fingerprints:
            raise ValueError(f"{source_label} feature service repeated a page while paginating")
        page_fingerprints.add(fingerprint)
        features.extend(page)
        properties = payload.get("properties")
        transfer_limit = payload.get("exceededTransferLimit")
        if transfer_limit is None and isinstance(properties, dict):
            transfer_limit = properties.get("exceededTransferLimit")
        exceeded = transfer_limit is True or str(transfer_limit).lower() == "true"
        if exceeded and not page:
            raise ValueError(
                f"{source_label} feature service reported a transfer limit without "
                "returning features"
            )
        if exceeded or (transfer_limit is None and len(page) == page_size):
            offset += len(page)
            continue
        break
    if not features:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
    return gpd.GeoDataFrame.from_features(features, crs=4326)


def derive_network_places(
    boundary: gpd.GeoDataFrame,
    place_features: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
    network: gpd.GeoDataFrame,
    config: AreaConfig,
) -> gpd.GeoDataFrame:
    """Derive Communities, portals, station access points and outward gateways."""
    crs = boundary.crs
    boundary_shape = boundary.to_crs(4326).geometry.union_all()
    features = place_features.to_crs(4326).copy()
    rows: list[dict[str, object]] = []
    external_centres: list[dict[str, object]] = []

    for index, feature in features.iterrows():
        name = _string_value(feature.get("name"))
        place_type = _string_value(feature.get("place"))
        if not name or place_type == "hamlet":
            continue
        point = feature.geometry.representative_point()
        inside = boundary_shape.covers(point)
        source_id = _source_identifier(feature, index)
        if not inside:
            if place_type in {"town", "city"}:
                external_centres.append({"name": name, "geometry": point})
            continue
        if place_type not in config.source.community_place_types:
            continue
        community_id = _stable_id("community", source_id, name)
        rows.append(
            {
                "place_id": community_id,
                "name": name,
                "kind": "community",
                "place_class": place_type,
                "parent_place_id": None,
                "source_id": source_id,
                "geometry": point,
            }
        )
        if _span_km(feature.geometry, features.crs) > config.source.internal_portal_threshold_km:
            portals = _connected_portals(feature.geometry, network.to_crs(4326))
            for number, portal in enumerate(portals, start=1):
                rows.append(
                    {
                        "place_id": f"{community_id}-portal-{number}",
                        "name": f"{name} portal {number}",
                        "kind": "community_portal",
                        "place_class": place_type,
                        "parent_place_id": community_id,
                        "source_id": source_id,
                        "geometry": portal,
                    }
                )

    for index, station in stations.to_crs(4326).iterrows():
        point = station.geometry.representative_point()
        if not boundary_shape.covers(point):
            continue
        source_id = _source_identifier(station, index)
        name = _string_value(station.get("name")) or "Unnamed station"
        rows.append(
            {
                "place_id": _stable_id("station", source_id, name),
                "name": name,
                "kind": "station_access",
                "place_class": _station_class(station),
                "parent_place_id": None,
                "source_id": source_id,
                "geometry": point,
            }
        )

    rows.extend(_derive_gateways(boundary_shape, network.to_crs(4326), external_centres))
    result = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    if result.empty:
        return result.to_crs(crs)
    return result.drop_duplicates("place_id").sort_values("place_id").to_crs(crs)


def derive_strategic_destinations(
    facilities: gpd.GeoDataFrame | None,
    source_ids: list[str],
    target_crs: object,
) -> gpd.GeoDataFrame:
    """Promote explicitly configured education sites to Network Places, not Schools."""
    columns = [
        "place_id",
        "name",
        "kind",
        "place_class",
        "parent_place_id",
        "source_id",
        "geometry",
    ]
    if facilities is None or facilities.empty or not source_ids:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=target_crs)
    configured = set(source_ids)
    rows: list[dict[str, object]] = []
    for index, facility in facilities.to_crs(target_crs).iterrows():
        source_id = _source_identifier(facility, index)
        amenity = (_string_value(facility.get("amenity")) or "").lower()
        if source_id not in configured or amenity not in {"college", "university"}:
            continue
        name = _string_value(facility.get("name")) or f"Unnamed {amenity}"
        rows.append(
            {
                "place_id": _stable_id("strategic-destination", source_id, name),
                "name": name,
                "kind": "strategic_destination",
                "place_class": amenity,
                "parent_place_id": None,
                "source_id": source_id,
                "geometry": facility.geometry.representative_point(),
            }
        )
    return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=target_crs)


def _connected_portals(community: object, network: gpd.GeoDataFrame) -> list[Point]:
    if isinstance(community, Point):
        return []
    lines = [
        geometry.intersection(community)
        for geometry in network.geometry
        if geometry.intersects(community)
    ]
    lines = [line for line in lines if not line.is_empty and line.length > 0]
    if not lines or not _linework_connected(lines):
        return []
    intersections = unary_union(network.geometry.tolist()).intersection(community.boundary)
    points = _extract_points(intersections)
    unique: dict[tuple[float, float], Point] = {
        (round(point.x, 7), round(point.y, 7)): point for point in points
    }
    candidates = list(unique.values())
    if len(candidates) <= 4:
        return candidates
    centre = community.representative_point()
    return sorted(candidates, key=lambda point: point.distance(centre), reverse=True)[:4]


def _linework_connected(lines: list[object]) -> bool:
    graph = nx.Graph()
    for linework in lines:
        parts = list(linework.geoms) if isinstance(linework, MultiLineString) else [linework]
        for line in parts:
            if not isinstance(line, LineString) or len(line.coords) < 2:
                continue
            coordinates = [(round(x, 7), round(y, 7)) for x, y in line.coords]
            nx.add_path(graph, coordinates)
    return bool(graph) and nx.is_connected(graph)


def _derive_gateways(
    boundary: object,
    network: gpd.GeoDataFrame,
    external_centres: list[dict[str, object]],
) -> list[dict[str, object]]:
    crossings: list[tuple[Point, Point | None]] = []
    for geometry in network.geometry:
        if geometry.crosses(boundary.boundary):
            for crossing in _extract_points(geometry.intersection(boundary.boundary)):
                crossings.append((crossing, _outward_endpoint(geometry, boundary, crossing)))
    grouped: dict[str, tuple[Point, float]] = {}
    for crossing, outward in crossings:
        if outward is None or not external_centres:
            name = "Unresolved onward corridor"
            grouped.setdefault(name, (crossing, 0.0))
            continue
        destination = min(
            external_centres,
            key=lambda item: _gateway_destination_score(crossing, outward, item["geometry"]),
        )
        distance = _gateway_destination_score(crossing, outward, destination["geometry"])
        name = str(destination["name"])
        if name not in grouped or distance < grouped[name][1]:
            grouped[name] = (crossing, distance)
    return [
        {
            "place_id": _stable_id("gateway", name, name),
            "name": f"Towards {name}",
            "kind": "cross_boundary_gateway",
            "place_class": "gateway",
            "parent_place_id": None,
            "source_id": name,
            "geometry": point,
        }
        for name, (point, _) in sorted(grouped.items())
    ]


def _outward_endpoint(geometry: object, boundary: object, crossing: Point) -> Point | None:
    """Return the closest exterior endpoint, preserving the road's outward bearing."""
    parts = list(geometry.geoms) if isinstance(geometry, MultiLineString) else [geometry]
    candidates: list[Point] = []
    for part in parts:
        if not isinstance(part, LineString) or len(part.coords) < 2:
            continue
        for coordinate in (part.coords[0], part.coords[-1]):
            endpoint = Point(coordinate)
            if not boundary.covers(endpoint):
                candidates.append(endpoint)
    return min(candidates, key=crossing.distance) if candidates else None


def _gateway_destination_score(
    crossing: Point,
    outward: Point | None,
    destination: object,
) -> float:
    """Prefer a named centre lying along the outward road corridor."""
    if not isinstance(destination, Point) or outward is None:
        return float("inf")
    direction_x = outward.x - crossing.x
    direction_y = outward.y - crossing.y
    magnitude = (direction_x**2 + direction_y**2) ** 0.5
    if magnitude == 0:
        return crossing.distance(destination)
    direction_x /= magnitude
    direction_y /= magnitude
    destination_x = destination.x - crossing.x
    destination_y = destination.y - crossing.y
    progress = destination_x * direction_x + destination_y * direction_y
    perpendicular = abs(destination_x * direction_y - destination_y * direction_x)
    behind_penalty = 10.0 if progress <= 0 else 0.0
    return behind_penalty + perpendicular + 0.1 * crossing.distance(destination)


def _extract_points(geometry: object) -> list[Point]:
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, MultiPoint):
        return list(geometry.geoms)
    if hasattr(geometry, "geoms"):
        points: list[Point] = []
        for item in geometry.geoms:
            points.extend(_extract_points(item))
        return points
    return []


def _span_km(geometry: object, crs: object) -> float:
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(27700)
    min_x, min_y, max_x, max_y = projected.total_bounds
    return max(max_x - min_x, max_y - min_y) / 1000


def _source_identifier(row: pd.Series, fallback: object) -> str:
    for key in ("osmid", "osm_id", "id"):
        value = row.get(key)
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return str(value)
    return str(fallback)


def _stable_id(prefix: str, source_id: str, name: str) -> str:
    digest = hashlib.sha256(f"{source_id}:{name}".encode()).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _string_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _station_class(row: pd.Series) -> str:
    if _string_value(row.get("railway")) == "station":
        return "rail"
    if _string_value(row.get("amenity")) == "bus_station":
        return "bus"
    return "public_transport"


def _validate_geojson_file(
    path: Path,
    *,
    defer_ea_route_nondegeneracy: bool = False,
    legacy_nan_property_key: str | None = None,
    expected_legacy_nan_count: int | None = None,
    normalization_report: dict[str, int] | None = None,
) -> None:
    """Validate large canonical GeoJSON without materialising its feature collection."""

    if (
        path.stat().st_size < STREAMING_GEOJSON_THRESHOLD_BYTES
        and legacy_nan_property_key is None
    ):
        frame = gpd.read_file(path)
        if frame.crs is None:
            raise ValueError(f"invalid snapshot: {path.name} has no CRS")
        return
    _validate_streaming_geojson(
        path,
        defer_ea_route_nondegeneracy=defer_ea_route_nondegeneracy,
        legacy_nan_property_key=legacy_nan_property_key,
        expected_legacy_nan_count=expected_legacy_nan_count,
        normalization_report=normalization_report,
    )


def _iter_streaming_geojson_features(
    path: Path,
    *,
    legacy_nan_property_key: str | None = None,
    expected_legacy_nan_count: int | None = None,
    normalization_report: dict[str, int] | None = None,
) -> Iterator[tuple[int, dict[str, object], object, CRS]]:
    """Yield one GeoJSON feature after validating the complete strict envelope."""

    yield from iter_geojson_features(
        path,
        legacy_nan_property_key=legacy_nan_property_key,
        expected_legacy_nan_count=expected_legacy_nan_count,
        normalization_report=normalization_report,
    )


def _validate_streaming_geojson(
    path: Path,
    *,
    defer_ea_route_nondegeneracy: bool = False,
    legacy_nan_property_key: str | None = None,
    expected_legacy_nan_count: int | None = None,
    normalization_report: dict[str, int] | None = None,
) -> None:
    for position, properties, geometry, source_crs in _iter_streaming_geojson_features(
        path,
        legacy_nan_property_key=legacy_nan_property_key,
        expected_legacy_nan_count=expected_legacy_nan_count,
        normalization_report=normalization_report,
    ):
        if (
            not defer_ea_route_nondegeneracy
            and path.name == EA_RETAINED_ROUTE_FILENAME
            and properties.get("feature_type") in ELIGIBLE_FEATURE_TYPES
            and not pd.isna(properties.get("topography_profile_id"))
        ):
            feature_id = str(
                properties.get("feature_id")
                or properties.get("id")
                or position
            )
            try:
                canonical_network_geometry_fingerprint(geometry, source_crs)
            except ValueError as error:
                raise ValueError(
                    f"invalid snapshot: {path.name} eligible route "
                    f"{feature_id!r} {error}"
                ) from error


def _validate_snapshot(
    path: Path,
    *,
    defer_ea_route_nondegeneracy: bool = False,
    legacy_nan_property_key: str | None = None,
    expected_legacy_nan_count: int | None = None,
    normalization_report: dict[str, int] | None = None,
) -> None:
    manifest_candidate = path / "snapshot.json"
    if not manifest_candidate.exists():
        raise ValueError(f"invalid snapshot: missing {manifest_candidate}")
    manifest_path = _regular_sibling(path, "snapshot.json", label="snapshot manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("invalid snapshot: manifest must be an object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"invalid snapshot schema: expected {SCHEMA_VERSION}, "
            f"found {manifest.get('schema_version')}"
        )
    retained_core_lineage = _retained_core_lineage(manifest)
    files = _manifest_siblings(path, manifest.get("files"), label="snapshot file")
    file_hashes = _manifest_hashes(manifest, "file_sha256", label="snapshot file hashes")
    provenance_hashes = _manifest_hashes(
        manifest, "provenance_file_sha256", label="snapshot provenance hashes"
    )
    if set(file_hashes) != set(files):
        raise ValueError("invalid snapshot: file hashes must cover exactly snapshot files")
    provenance = _manifest_siblings(path, list(provenance_hashes), label="snapshot provenance file")
    if ELEVATION_EVIDENCE_FILENAME in files:
        evidence_sources = manifest.get("evidence_sources")
        elevation = (
            evidence_sources.get("elevation") if isinstance(evidence_sources, dict) else None
        )
        content_fingerprint = (
            elevation.get("content_fingerprint") if isinstance(elevation, dict) else None
        )
        if (
            not isinstance(content_fingerprint, str)
            or len(content_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in content_fingerprint)
        ):
            raise ValueError("invalid snapshot: elevation evidence has no content fingerprint")
        legacy_generic_local_elevation = (
            "provenance_file_sha256" not in manifest
            and retained_core_lineage is None
            and isinstance(elevation, dict)
            and elevation.get("provider") == "local-geojson"
            and not set(files).intersection(
                ELEVATION_ARTIFACT_FILENAMES - {ELEVATION_EVIDENCE_FILENAME}
            )
            and not {
                "acquisition_protocol",
                "acquisition_output_sha256",
                "authority_boundary_sha256",
                "cross_boundary_transitions",
                "ea_acquisition_manifest_sha256",
                "evidence_row_sha256s",
                "governed_input_fingerprint",
                "pre_elevation_network_sha256",
                "sample_ledger_sha256",
                "survey_index_feature_sha256",
                "survey_index_sha256",
            }.intersection(elevation)
        )
        if (
            file_hashes[ELEVATION_EVIDENCE_FILENAME] != content_fingerprint
            or (
                provenance_hashes.get(ELEVATION_EVIDENCE_FILENAME) != content_fingerprint
                and not legacy_generic_local_elevation
            )
        ):
            raise ValueError("invalid snapshot: elevation evidence provenance mismatch")
    # All sibling paths have now passed containment, regular-file and duplicate
    # checks.  Only after that first pass is a retained byte read or hashed.
    for filename, file_path in files.items():
        if sha256_file(file_path) != file_hashes[filename]:
            raise ValueError(f"invalid snapshot: {filename} content hash mismatch")
        if file_path.suffix == ".geojson":
            _validate_geojson_file(
                file_path,
                defer_ea_route_nondegeneracy=defer_ea_route_nondegeneracy,
                legacy_nan_property_key=(
                    legacy_nan_property_key
                    if filename == EA_RETAINED_ROUTE_FILENAME
                    else None
                ),
                expected_legacy_nan_count=(
                    expected_legacy_nan_count
                    if filename == EA_RETAINED_ROUTE_FILENAME
                    else None
                ),
                normalization_report=(
                    normalization_report
                    if filename == EA_RETAINED_ROUTE_FILENAME
                    else None
                ),
            )
    if (
        expected_legacy_nan_count is not None
        and normalization_report is not None
        and legacy_nan_property_key not in normalization_report
    ):
        raise ValueError(
            "invalid snapshot: legacy GeoJSON NaN normalization proof is missing"
        )
    for filename, file_path in provenance.items():
        if sha256_file(file_path) != provenance_hashes[filename]:
            raise ValueError(f"invalid snapshot: {filename} provenance hash mismatch")


def load_snapshot(config: AreaConfig) -> dict[str, gpd.GeoDataFrame]:
    path = config.source.snapshot_dir / config.source.snapshot_id
    _validate_snapshot(path)
    return _read_snapshot_frames(path)


def _read_snapshot_frames(path: Path) -> dict[str, gpd.GeoDataFrame]:
    """Read an already validated snapshot into the compiler's source frames."""

    network = gpd.read_file(path / "network.geojson")
    context_path = path / "context.geojson"
    context = (
        gpd.read_file(context_path) if context_path.exists() else derive_context_layers(network)
    )
    if context.empty:
        context = empty_context(network.crs)
    place_features_path = path / "osm-place-features.geojson"
    classification_path = path / ROAD_CLASSIFICATION_FILENAME
    observed_traffic_path = path / OBSERVED_THROUGH_TRAFFIC_FILENAME
    elevation_path = path / ELEVATION_EVIDENCE_FILENAME
    return {
        "boundary": gpd.read_file(path / "boundary.geojson"),
        "places": gpd.read_file(path / "places.geojson"),
        "label_places": (
            gpd.read_file(place_features_path)
            if place_features_path.exists()
            else gpd.read_file(path / "places.geojson")
        ),
        "network": network,
        "context": context,
        "official_road_classification": (
            gpd.read_file(classification_path)
            if classification_path.exists()
            else gpd.GeoDataFrame(
                columns=ROAD_CLASSIFICATION_COLUMNS,
                geometry="geometry",
                crs=network.crs,
            )
        ),
        "observed_through_traffic": (
            gpd.read_file(observed_traffic_path)
            if observed_traffic_path.exists()
            else gpd.GeoDataFrame(
                columns=[
                    "evidence_id",
                    "source_id",
                    "effective_date",
                    "licence",
                    "content_fingerprint",
                    "geometry",
                ],
                geometry="geometry",
                crs=network.crs,
            )
        ),
        "elevation_evidence": (
            gpd.read_file(elevation_path)
            if elevation_path.exists()
            else gpd.GeoDataFrame(
                columns=[
                    "evidence_id",
                    "source_id",
                    "effective_date",
                    "licence",
                    "elevation_m",
                    "geometry",
                ],
                geometry="geometry",
                crs=network.crs,
            )
        ),
        "elevation_corroboration": _osm_elevation_corroboration(network),
    }


def _osm_elevation_corroboration(network: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for index, feature in network.iterrows():
        elevation = feature.get("ele")
        incline = feature.get("incline")
        if not str(elevation or "").strip() and not str(incline or "").strip():
            continue
        source_id = _source_identifier(feature, index)
        discriminator = hashlib.sha256(
            "::".join(
                (
                    source_id,
                    str(feature.get("u") or ""),
                    str(feature.get("v") or ""),
                    str(feature.get("key") or ""),
                    feature.geometry.wkb_hex,
                )
            ).encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "corroboration_id": f"osm-elevation-{discriminator}",
                "source_id": source_id,
                "osm_elevation": elevation,
                "osm_incline": incline,
                "evidence_role": "corroborating-only",
                "geometry": feature.geometry,
            }
        )
    return gpd.GeoDataFrame(
        rows,
        columns=[
            "corroboration_id",
            "source_id",
            "osm_elevation",
            "osm_incline",
            "evidence_role",
            "geometry",
        ],
        geometry="geometry",
        crs=network.crs,
    )
