from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString

import satn.ea_snapshot_recovery as recovery_module
import satn.sources as sources_module
import satn.streaming_geojson as streaming_geojson_module
from satn.constants import SCHEMA_VERSION
from satn.ea_elevation import sha256_file
from satn.ea_snapshot_recovery import (
    preflight_recovery_output_family,
    promote_recovery_transaction,
    reconcile_stationary_route_recovery,
    recovery_transaction_journal_path,
    validate_recovery_sampled_route_output,
    validate_recovery_target,
    verified_official_road_identity,
    write_recovery_record,
)
from satn.models import OfficialRoadClassificationConfig
from satn.sources import EA_RETAINED_ROUTE_FILENAME, StagedSnapshot, _validate_snapshot


def _routes(path: Path, rows: list[dict[str, object]]) -> Path:
    gpd.GeoDataFrame(rows, geometry="geometry", crs=27700).to_file(
        path, driver="GeoJSON"
    )
    return path


def _recovery_feature(feature_id: str = "route-a") -> bytes:
    return json.dumps(
        {
            "type": "Feature",
            "properties": {"feature_id": feature_id},
            "geometry": {
                "type": "LineString",
                "coordinates": [[350000, 150000], [350010, 150000]],
            },
        },
        separators=(",", ":"),
    ).encode()


def _recovery_collection(features: list[bytes]) -> bytes:
    return (
        b'{"type":"FeatureCollection","crs":{"type":"name","properties":'
        b'{"name":"urn:ogc:def:crs:EPSG::27700"}},"features":['
        + b",".join(features)
        + b"]}"
    )


def test_recovery_stream_accepts_single_line_collection_across_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "routes.geojson"
    path.write_bytes(
        _recovery_collection(
            [_recovery_feature(f"route-{index}") for index in range(2_000)]
        )
    )
    monkeypatch.setattr(streaming_geojson_module, "READ_CHUNK_BYTES", 64)

    assert validate_recovery_sampled_route_output(path) == 2_000


@pytest.mark.parametrize(
    "payload",
    [
        b"garbage" + _recovery_collection([_recovery_feature()]),
        (
            b'{"type":"FeatureCollection","features":['
            + _recovery_feature()
            + b"] garbage}"
        ),
        _recovery_collection([_recovery_feature()]) + b"}",
        _recovery_collection([_recovery_feature()]) + b" true",
    ],
    ids=["prefix", "middle", "suffix", "trailing"],
)
def test_recovery_stream_rejects_wrapper_garbage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    path = tmp_path / "routes.geojson"
    path.write_bytes(payload)
    monkeypatch.setattr(streaming_geojson_module, "READ_CHUNK_BYTES", 17)

    with pytest.raises(ValueError):
        validate_recovery_sampled_route_output(path)


@pytest.mark.parametrize(
    ("properties", "coordinates", "expected_count", "message"),
    [
        ({"other_property": float("nan")}, [[0, 0], [1, 1]], 1, "permitted only"),
        (
            {"access_point_source_id": float("nan")},
            [[float("nan"), 0], [1, 1]],
            1,
            "forbidden outside",
        ),
        (
            {"access_point_source_id": float("nan")},
            [[0, 0], [1, 1]],
            2,
            "normalization count differs",
        ),
    ],
    ids=["wrong-key", "geometry", "wrong-count"],
)
def test_legacy_nan_normalization_is_exactly_scoped(
    tmp_path: Path,
    properties: dict[str, object],
    coordinates: list[list[float]],
    expected_count: int,
    message: str,
) -> None:
    path = tmp_path / "legacy.geojson"
    feature = {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }
    path.write_bytes(
        _recovery_collection(
            [json.dumps(feature, separators=(",", ":")).encode()]
        )
    )

    with pytest.raises(ValueError, match=message):
        list(
            streaming_geojson_module.iter_geojson_features(
                path,
                legacy_nan_property_key="access_point_source_id",
                expected_legacy_nan_count=expected_count,
            )
        )


def _transaction_fixture(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    target = tmp_path / "weca-v11"
    staged_path = tmp_path / ".weca-v11-staged"
    staged_path.mkdir()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": "weca-v11",
        "retained_core_lineage": {
            "source_snapshot_id": "weca-v10",
            "source_manifest_sha256": "a" * 64,
        },
        "evidence_sources": {
            "official_road_classification": {
                "source_id": "os-open-roads-2026-04-07",
                "content_fingerprint": "b" * 64,
            }
        },
        "files": [],
        "file_sha256": {},
        "provenance_file_sha256": {},
    }
    manifest_path = staged_path / "snapshot.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config_path = tmp_path / "area.yaml"
    original_config = b"source:\n  snapshot_id: weca-v10\n"
    promoted_config = b"source:\n  snapshot_id: weca-v11\n"
    config_path.write_bytes(original_config)
    record_path = tmp_path / "recovery.json"
    record = {
        "schema_version": "ea-snapshot-recovery/v1",
        "status": "sealed",
        "parent_snapshot_id": "weca-v10",
        "parent_manifest_sha256": "a" * 64,
        "target_snapshot_id": "weca-v11",
        "target_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
    }
    arguments: dict[str, object] = {
        "staged_snapshot": StagedSnapshot(staged_path, target),
        "target": target,
        "record_path": record_path,
        "config_path": config_path,
        "expected_config_sha256": hashlib.sha256(original_config).hexdigest(),
        "promoted_config_bytes": promoted_config,
        "record": record,
        "parent_snapshot_id": "weca-v10",
        "parent_manifest_sha256": "a" * 64,
        "official_source_id": "os-open-roads-2026-04-07",
        "official_content_fingerprint": "b" * 64,
    }
    expected = {
        "original_config": original_config,
        "promoted_config": promoted_config,
        "record_path": record_path,
        "config_path": config_path,
        "target": target,
        "staged_path": staged_path,
    }
    return arguments, expected


def test_sha256_file_reads_large_sparse_input_in_bounded_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "large-sparse.bin"
    with path.open("wb") as stream:
        stream.write(b"start")
        stream.seek(32 * 1024 * 1024)
        stream.write(b"end")
    with path.open("rb") as stream:
        expected = hashlib.file_digest(stream, "sha256").hexdigest()
    original_open = Path.open
    read_sizes: list[int] = []

    class GuardedReader:
        def __init__(self, raw: object) -> None:
            self.raw = raw

        def __enter__(self) -> GuardedReader:
            self.raw.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self.raw.__exit__(*args)

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            if not 0 < size <= 1024 * 1024:
                raise AssertionError(f"unbounded hash read: {size}")
            return self.raw.read(size)

    def guarded_open(candidate: Path, *args: object, **kwargs: object):
        raw = original_open(candidate, *args, **kwargs)
        return GuardedReader(raw) if candidate == path else raw

    monkeypatch.setattr(Path, "open", guarded_open)

    assert sha256_file(path) == expected
    assert len(read_sizes) > 1


def test_recovery_accounts_for_every_collapsed_route_without_using_it_as_supplement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _routes(
        tmp_path / "invalid.geojson",
        [
            {
                "feature_id": "spine-access-07d8d07fe59d",
                "feature_type": "spine-access-connection",
                "place_id": "community-a",
                "topography_profile_id": "profile-a",
                "access_point_source_id": "__legacy_nan__",
                "geometry": LineString([(0, 0), (0, 0)]),
            },
            {
                "feature_id": "spine-access-old-gap",
                "feature_type": "spine-access-connection",
                "place_id": "community-b",
                "topography_profile_id": "profile-b",
                "geometry": LineString([(10, 0), (10, 0)]),
            },
        ],
    )
    invalid.write_bytes(invalid.read_bytes().replace(b'"__legacy_nan__"', b"NaN"))
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_SNAPSHOT_ID",
        "weca-v10",
    )
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        "a" * 64,
    )
    monkeypatch.setattr(recovery_module, "LEGACY_NAN_EXPECTED_COUNT", 1)
    candidate = _routes(
        tmp_path / "candidate.geojson",
        [
            {
                "feature_id": "spine-access-corrected",
                "feature_type": "spine-access-connection",
                "place_id": "community-a",
                "topography_profile_id": "profile-new",
                "geometry": LineString([(0, 0), (20, 0)]),
            },
            {
                "feature_id": "network-gap-community-b",
                "feature_type": "network-gap",
                "place_id": "community-b",
                "topography_profile_id": None,
                "geometry": LineString([(10, 0), (11, 0)]),
            },
        ],
    )

    record = reconcile_stationary_route_recovery(
        invalid,
        candidate,
        parent_snapshot_id="weca-v10",
        parent_manifest_sha256="a" * 64,
        target_snapshot_id="weca-v11",
    )

    assert record["invalid_supplemental_routes_used"] is False
    assert record["legacy_nonfinite_property_normalization"] == {
        "parent_snapshot_id": "weca-v10",
        "parent_manifest_sha256": "a" * 64,
        "property_key": "access_point_source_id",
        "token": "NaN",
        "replacement": None,
        "count": 1,
    }
    assert record["collapsed_route_count"] == 2
    assert record["resolutions"] == [
        {
            "collapsed_feature_id": "spine-access-07d8d07fe59d",
            "place_id": "community-a",
            "resolution": "superseded-by-distinct-node-route",
            "replacement_feature_id": "spine-access-corrected",
        },
        {
            "collapsed_feature_id": "spine-access-old-gap",
            "place_id": "community-b",
            "resolution": "superseded-by-network-gap",
            "replacement_feature_id": "network-gap-community-b",
        },
    ]


def test_recovery_refuses_an_unaccounted_collapsed_route(tmp_path: Path) -> None:
    invalid = _routes(
        tmp_path / "invalid.geojson",
        [
            {
                "feature_id": "spine-access-orphan",
                "feature_type": "spine-access-connection",
                "place_id": "community-orphan",
                "topography_profile_id": "profile-a",
                "geometry": LineString([(0, 0), (0, 0)]),
            }
        ],
    )
    candidate = _routes(
        tmp_path / "candidate.geojson",
        [
            {
                "feature_id": "unrelated",
                "feature_type": "strategic-spine",
                "place_id": None,
                "topography_profile_id": "profile-new",
                "geometry": LineString([(0, 0), (20, 0)]),
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"spine-access-orphan.*exactly one route or network gap",
    ):
        reconcile_stationary_route_recovery(
            invalid,
            candidate,
            parent_snapshot_id="weca-v10",
            parent_manifest_sha256="a" * 64,
            target_snapshot_id="weca-v11",
        )


def test_recovery_accounts_for_supplemental_school_duplicate_without_admitting_it(
    tmp_path: Path,
) -> None:
    invalid = _routes(
        tmp_path / "invalid.geojson",
        [
            {
                "feature_id": "spine-access-1382801e7fc5",
                "feature_type": "spine-access-connection",
                "obligation_id": "school-access-obligation-93e31e823a69",
                "school_id": "school-889a81c4837f",
                "topography_profile_id": "profile-a",
                "geometry": LineString([(0, 0), (0, 0)]),
            },
            {
                "feature_id": "spine-access-6e746452bf70",
                "feature_type": "spine-access-connection",
                "obligation_id": "school-access-obligation-b674b50f5e09",
                "school_id": "school-e9e5b72474f4",
                "topography_profile_id": "profile-b",
                "geometry": LineString([(10, 0), (10, 0)]),
            },
            {
                "feature_id": "supplemental-10dda413d160-378-spine-access-6e746452bf70",
                "feature_type": "spine-access-connection",
                "obligation_id": "school-access-obligation-b674b50f5e09",
                "school_id": "school-e9e5b72474f4",
                "topography_profile_id": "profile-b",
                "geometry": LineString([(10, 0), (10, 0)]),
            },
        ],
    )
    candidate = _routes(
        tmp_path / "candidate.geojson",
        [
            {
                "feature_id": "school-route-a",
                "feature_type": "spine-access-connection",
                "obligation_id": "school-access-obligation-93e31e823a69",
                "topography_profile_id": "profile-new-a",
                "geometry": LineString([(0, 0), (20, 0)]),
            },
            {
                "feature_id": "school-gap-b",
                "feature_type": "network-gap",
                "obligation_id": "school-access-obligation-b674b50f5e09",
                "topography_profile_id": None,
                "geometry": LineString([(10, 0), (11, 0)]),
            },
        ],
    )

    record = reconcile_stationary_route_recovery(
        invalid,
        candidate,
        parent_snapshot_id="weca-v10",
        parent_manifest_sha256="a" * 64,
        target_snapshot_id="weca-v11",
    )

    assert record["collapsed_route_count"] == 3
    assert record["unique_collapsed_route_count"] == 2
    assert record["supplemental_duplicate_deduplication"] == [
        {
            "retained_feature_id": (
                "supplemental-10dda413d160-378-spine-access-6e746452bf70"
            ),
            "deduplicated_against": "spine-access-6e746452bf70",
            "obligation_id": "school-access-obligation-b674b50f5e09",
            "school_id": "school-e9e5b72474f4",
            "admitted_as_recovery_input": False,
        }
    ]
    accounted = {
        resolution["collapsed_feature_id"] for resolution in record["resolutions"]
    } | {
        duplicate["retained_feature_id"]
        for duplicate in record["supplemental_duplicate_deduplication"]
    }
    assert accounted == {
        "spine-access-1382801e7fc5",
        "spine-access-6e746452bf70",
        "supplemental-10dda413d160-378-spine-access-6e746452bf70",
    }


def test_recovery_requires_exactly_one_route_or_gap_resolution(tmp_path: Path) -> None:
    invalid = _routes(
        tmp_path / "invalid.geojson",
        [
            {
                "feature_id": "collapsed-a",
                "feature_type": "spine-access-connection",
                "place_id": "community-a",
                "topography_profile_id": "profile-a",
                "geometry": LineString([(0, 0), (0, 0)]),
            }
        ],
    )
    candidate = _routes(
        tmp_path / "candidate.geojson",
        [
            {
                "feature_id": "route-a",
                "feature_type": "spine-access-connection",
                "place_id": "community-a",
                "topography_profile_id": "profile-new",
                "geometry": LineString([(0, 0), (20, 0)]),
            },
            {
                "feature_id": "gap-a",
                "feature_type": "network-gap",
                "place_id": "community-a",
                "topography_profile_id": None,
                "geometry": LineString([(0, 0), (1, 0)]),
            },
        ],
    )

    with pytest.raises(ValueError, match="exactly one route or network gap"):
        reconcile_stationary_route_recovery(
            invalid,
            candidate,
            parent_snapshot_id="weca-v10",
            parent_manifest_sha256="a" * 64,
            target_snapshot_id="weca-v11",
        )


def test_recovery_refuses_one_replacement_identity_used_twice(tmp_path: Path) -> None:
    invalid = _routes(
        tmp_path / "invalid.geojson",
        [
            {
                "feature_id": "collapsed-a",
                "feature_type": "spine-access-connection",
                "place_id": "community-a",
                "topography_profile_id": "profile-a",
                "geometry": LineString([(0, 0), (0, 0)]),
            },
            {
                "feature_id": "collapsed-b",
                "feature_type": "spine-access-connection",
                "place_id": "community-b",
                "topography_profile_id": "profile-b",
                "geometry": LineString([(10, 0), (10, 0)]),
            },
        ],
    )
    candidate = _routes(
        tmp_path / "candidate.geojson",
        [
            {
                "feature_id": "shared-replacement",
                "feature_type": "spine-access-connection",
                "place_id": "community-a",
                "topography_profile_id": "profile-new-a",
                "geometry": LineString([(0, 0), (20, 0)]),
            },
            {
                "feature_id": "shared-replacement",
                "feature_type": "network-gap",
                "place_id": "community-b",
                "topography_profile_id": None,
                "geometry": LineString([(10, 0), (11, 0)]),
            },
        ],
    )

    with pytest.raises(ValueError, match="replacement feature identities must be globally unique"):
        reconcile_stationary_route_recovery(
            invalid,
            candidate,
            parent_snapshot_id="weca-v10",
            parent_manifest_sha256="a" * 64,
            target_snapshot_id="weca-v11",
        )


def test_recovery_record_is_idempotent_but_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "recovery.json"
    record = {"schema_version": "ea-snapshot-recovery/v1", "status": "sealed"}

    write_recovery_record(path, record)
    write_recovery_record(path, record)

    with pytest.raises(ValueError, match="already exists with different content"):
        write_recovery_record(path, {**record, "status": "different"})


def test_recovery_target_requires_exact_parent_and_governed_classification(
    tmp_path: Path,
) -> None:
    target = tmp_path / "weca-v11"
    target.mkdir()
    (target / "snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": "weca-v11",
                "retained_core_lineage": {
                    "source_snapshot_id": "weca-v10",
                    "source_manifest_sha256": "a" * 64,
                },
                "evidence_sources": {
                    "official_road_classification": {
                        "source_id": "os-open-roads-2026-04-07",
                        "content_fingerprint": "b" * 64,
                    }
                },
                "files": [],
                "file_sha256": {},
                "provenance_file_sha256": {},
            }
        ),
        encoding="utf-8",
    )

    validate_recovery_target(
        target,
        target_snapshot_id="weca-v11",
        parent_snapshot_id="weca-v10",
        parent_manifest_sha256="a" * 64,
        official_source_id="os-open-roads-2026-04-07",
        official_content_fingerprint="b" * 64,
    )

    with pytest.raises(ValueError, match="official-road classification identity"):
        validate_recovery_target(
            target,
            target_snapshot_id="weca-v11",
            parent_snapshot_id="weca-v10",
            parent_manifest_sha256="a" * 64,
            official_source_id="os-open-roads-2026-04-07",
            official_content_fingerprint="c" * 64,
        )


def test_official_identity_is_derived_from_verified_parent_not_target_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governed_source = _routes(
        tmp_path / "configured-official.geojson",
        [
            {
                "road_id": "road-a",
                "official_classification": "A road",
                "geometry": LineString([(0, 0), (20, 0)]),
            }
        ],
    )
    governed = OfficialRoadClassificationConfig(
        path=governed_source,
        source_id="os-open-roads-2026-04-07",
        effective_date="2026-04-07",
        licence="Open Government Licence v3.0",
    )
    fingerprint = hashlib.sha256(governed_source.read_bytes()).hexdigest()
    parent = tmp_path / "weca-v10"
    parent.mkdir()
    snapshotted_official = _routes(
        parent / "official-road-classification.geojson",
        [
            {
                "official_feature_id": "road-a",
                "official_classification": "a-road",
                "source_id": governed.source_id,
                "effective_date": "2026-04-07",
                "licence": governed.licence,
                "content_fingerprint": fingerprint,
                "geometry": LineString([(0, 0), (20, 0)]),
            }
        ],
    )
    official_digest = hashlib.sha256(snapshotted_official.read_bytes()).hexdigest()
    collapsed = json.loads(_recovery_feature("collapsed-route"))
    collapsed["properties"].update(
        {
            "feature_type": "spine-access-connection",
            "topography_profile_id": "profile-collapsed",
            "access_point_source_id": "__legacy_nan__",
        }
    )
    collapsed["geometry"]["coordinates"] = [[350000, 150000], [350000, 150000]]
    retained_routes = parent / EA_RETAINED_ROUTE_FILENAME
    valid_retained_route_bytes = _recovery_collection(
        [json.dumps(collapsed, separators=(",", ":")).encode()]
    ).replace(b'"__legacy_nan__"', b"NaN")
    retained_routes.write_bytes(valid_retained_route_bytes)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": "weca-v10",
        "evidence_sources": {
            "official_road_classification": {
                "source_id": governed.source_id,
                "effective_date": "2026-04-07",
                "licence": governed.licence,
                "content_fingerprint": fingerprint,
                "snapshot_file": "official-road-classification.geojson",
            }
        },
        "files": [
            "official-road-classification.geojson",
            EA_RETAINED_ROUTE_FILENAME,
        ],
        "file_sha256": {
            "official-road-classification.geojson": official_digest,
            EA_RETAINED_ROUTE_FILENAME: sha256_file(retained_routes),
        },
        "provenance_file_sha256": {},
    }
    parent_manifest = parent / "snapshot.json"
    parent_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)
    parent_manifest_sha256 = sha256_file(parent_manifest)
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_SNAPSHOT_ID",
        "weca-v10",
    )
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        parent_manifest_sha256,
    )
    monkeypatch.setattr(recovery_module, "LEGACY_NAN_EXPECTED_COUNT", 1)

    with pytest.raises(ValueError, match="invalid JSON value"):
        _validate_snapshot(parent)
    with pytest.raises(ValueError, match="manifest SHA-256 differs"):
        verified_official_road_identity(
            parent,
            parent_manifest_sha256="0" * 64,
            governed=governed,
        )

    identity = verified_official_road_identity(
        parent,
        parent_manifest_sha256=parent_manifest_sha256,
        governed=governed,
    )
    normalization = identity["recovery_parent_validation"][
        "legacy_nonfinite_property_normalization"
    ]
    assert normalization["property_key"] == "access_point_source_id"
    assert normalization["count"] == 1

    retained_routes.write_bytes(valid_retained_route_bytes + b"}")
    manifest["file_sha256"][EA_RETAINED_ROUTE_FILENAME] = sha256_file(retained_routes)
    parent_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    malformed_manifest_sha256 = sha256_file(parent_manifest)
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        malformed_manifest_sha256,
    )
    with pytest.raises(ValueError, match="trailing content"):
        verified_official_road_identity(
            parent,
            parent_manifest_sha256=malformed_manifest_sha256,
            governed=governed,
        )
    retained_routes.write_bytes(valid_retained_route_bytes)
    manifest["file_sha256"][EA_RETAINED_ROUTE_FILENAME] = sha256_file(retained_routes)
    parent_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    parent_manifest_sha256 = sha256_file(parent_manifest)
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        parent_manifest_sha256,
    )

    target = tmp_path / "weca-v11"
    target.mkdir()
    (target / "snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": "weca-v11",
                "retained_core_lineage": {
                    "source_snapshot_id": "weca-v10",
                    "source_manifest_sha256": hashlib.sha256(
                        parent_manifest.read_bytes()
                    ).hexdigest(),
                },
                "evidence_sources": {
                    "official_road_classification": {
                        **identity,
                        "content_fingerprint": "f" * 64,
                        "snapshot_file": "official-road-classification.geojson",
                    }
                },
                "files": [],
                "file_sha256": {},
                "provenance_file_sha256": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="official-road classification identity"):
        validate_recovery_target(
            target,
            target_snapshot_id="weca-v11",
            parent_snapshot_id="weca-v10",
            parent_manifest_sha256=hashlib.sha256(
                parent_manifest.read_bytes()
            ).hexdigest(),
            official_source_id=identity["source_id"],
            official_content_fingerprint=identity["content_fingerprint"],
        )


def test_recovery_target_rejects_tampered_file_even_after_manifest_reseal(
    tmp_path: Path,
) -> None:
    target = tmp_path / "weca-v11"
    target.mkdir()
    retained = target / "ea-elevation-sample-ledger.jsonl"
    retained.write_text("trusted", encoding="utf-8")
    digest = hashlib.sha256(retained.read_bytes()).hexdigest()
    (target / "snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": "weca-v11",
                "retained_core_lineage": {
                    "source_snapshot_id": "weca-v10",
                    "source_manifest_sha256": "a" * 64,
                },
                "evidence_sources": {
                    "official_road_classification": {
                        "source_id": "os-open-roads-2026-04-07",
                        "content_fingerprint": "b" * 64,
                    }
                },
                "files": ["ea-elevation-sample-ledger.jsonl"],
                "file_sha256": {
                    "ea-elevation-sample-ledger.jsonl": digest
                },
                "provenance_file_sha256": {
                    "ea-elevation-sample-ledger.jsonl": digest
                },
            }
        ),
        encoding="utf-8",
    )
    retained.write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="content hash mismatch"):
        validate_recovery_target(
            target,
            target_snapshot_id="weca-v11",
            parent_snapshot_id="weca-v10",
            parent_manifest_sha256="a" * 64,
            official_source_id="os-open-roads-2026-04-07",
            official_content_fingerprint="b" * 64,
        )


def test_recovery_transaction_promotes_once_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    arguments, expected = _transaction_fixture(tmp_path)

    promoted = promote_recovery_transaction(**arguments)

    assert promoted == expected["target"]
    assert expected["config_path"].read_bytes() == expected["promoted_config"]
    record_bytes = expected["record_path"].read_bytes()
    journal = json.loads(
        recovery_transaction_journal_path(expected["record_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert journal["phase"] == "complete"

    arguments["staged_snapshot"] = None
    assert promote_recovery_transaction(**arguments) == promoted
    assert expected["record_path"].read_bytes() == record_bytes


def test_recovery_transaction_refuses_unjournaled_existing_target(
    tmp_path: Path,
) -> None:
    arguments, expected = _transaction_fixture(tmp_path)
    expected["staged_path"].rename(expected["target"])
    arguments["staged_snapshot"] = None

    with pytest.raises(ValueError, match="without its transaction journal"):
        promote_recovery_transaction(**arguments)

    assert expected["target"].is_dir()
    assert expected["config_path"].read_bytes() == expected["original_config"]


def test_recovery_transaction_refuses_unsealed_status_before_promotion(
    tmp_path: Path,
) -> None:
    arguments, expected = _transaction_fixture(tmp_path)
    arguments["record"] = {**arguments["record"], "status": "candidate-reconciled"}

    with pytest.raises(ValueError, match="must be sealed"):
        promote_recovery_transaction(**arguments)

    assert not expected["target"].exists()
    assert expected["staged_path"].is_dir()


def test_recovery_transaction_leaves_staged_snapshot_on_promotion_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.ea_snapshot_recovery as recovery

    arguments, expected = _transaction_fixture(tmp_path)

    def fail_promotion(_staged: StagedSnapshot) -> Path:
        raise OSError("injected promotion failure")

    monkeypatch.setattr(recovery, "promote_staged_snapshot", fail_promotion)
    with pytest.raises(OSError, match="injected promotion failure"):
        promote_recovery_transaction(**arguments)

    assert not expected["target"].exists()
    assert expected["staged_path"].is_dir()
    assert not expected["record_path"].exists()
    assert expected["config_path"].read_bytes() == expected["original_config"]


def test_recovery_transaction_refuses_tampered_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.ea_snapshot_recovery as recovery

    arguments, expected = _transaction_fixture(tmp_path)
    original_promote = recovery.promote_staged_snapshot

    def fail_after_promotion(staged: StagedSnapshot) -> Path:
        promoted = original_promote(staged)
        raise OSError(f"injected crash after {promoted.name}")

    monkeypatch.setattr(recovery, "promote_staged_snapshot", fail_after_promotion)
    with pytest.raises(OSError, match="injected crash"):
        promote_recovery_transaction(**arguments)
    journal_path = recovery_transaction_journal_path(expected["record_path"])
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["phase"] = "attacker-controlled"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    arguments["staged_snapshot"] = None

    with pytest.raises(ValueError, match="transaction journal differs"):
        promote_recovery_transaction(**arguments)


def test_recovery_transaction_resumes_when_target_promoted_before_journal_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.ea_snapshot_recovery as recovery

    arguments, expected = _transaction_fixture(tmp_path)
    original_promote = recovery.promote_staged_snapshot

    def crash_after_promotion(staged: StagedSnapshot) -> Path:
        promoted = original_promote(staged)
        raise OSError(f"injected crash after {promoted.name}")

    monkeypatch.setattr(recovery, "promote_staged_snapshot", crash_after_promotion)
    with pytest.raises(OSError, match="injected crash"):
        promote_recovery_transaction(**arguments)

    assert expected["target"].is_dir()
    journal_path = recovery_transaction_journal_path(expected["record_path"])
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "prepared"

    monkeypatch.setattr(recovery, "promote_staged_snapshot", original_promote)
    arguments["staged_snapshot"] = None
    assert promote_recovery_transaction(**arguments) == expected["target"]
    assert expected["config_path"].read_bytes() == expected["promoted_config"]


def test_recovery_transaction_preserves_target_when_record_collides_after_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.ea_snapshot_recovery as recovery

    arguments, expected = _transaction_fixture(tmp_path)
    original_promote = recovery.promote_staged_snapshot

    def collide_after_promotion(staged: StagedSnapshot) -> Path:
        promoted = original_promote(staged)
        expected["record_path"].write_text("attacker", encoding="utf-8")
        return promoted

    monkeypatch.setattr(recovery, "promote_staged_snapshot", collide_after_promotion)
    with pytest.raises(ValueError, match="already exists with different content"):
        promote_recovery_transaction(**arguments)

    assert expected["target"].is_dir()
    assert expected["record_path"].read_text(encoding="utf-8") == "attacker"
    assert expected["config_path"].read_bytes() == expected["original_config"]


def test_recovery_transaction_resumes_after_configuration_promotion_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, expected = _transaction_fixture(tmp_path)
    original_replace = Path.replace

    def fail_config_replace(self: Path, target: Path) -> Path:
        if target == expected["config_path"] and self.name.startswith(".area.yaml."):
            raise OSError("injected configuration promotion failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_config_replace)
    with pytest.raises(OSError, match="injected configuration promotion failure"):
        promote_recovery_transaction(**arguments)

    assert expected["target"].is_dir()
    assert expected["record_path"].is_file()
    assert expected["config_path"].read_bytes() == expected["original_config"]

    monkeypatch.setattr(Path, "replace", original_replace)
    arguments["staged_snapshot"] = None
    assert promote_recovery_transaction(**arguments) == expected["target"]
    assert expected["config_path"].read_bytes() == expected["promoted_config"]


@pytest.mark.parametrize(
    "colliding_name",
    [
        "recovered.geojson",
        "recovered.manifest.json",
        "recovered.sampled-routes.geojson",
        "recovered.sample-ledger.jsonl",
    ],
)
def test_recovery_preflight_refuses_every_output_family_collision(
    tmp_path: Path,
    colliding_name: str,
) -> None:
    collision = tmp_path / colliding_name
    collision.write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match=colliding_name):
        preflight_recovery_output_family(tmp_path / "recovered.geojson")

    assert collision.read_text(encoding="utf-8") == "existing"
