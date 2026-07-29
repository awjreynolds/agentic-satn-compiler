from __future__ import annotations

import importlib.util
import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from satn.ea_elevation import (
    FIXED_POINT_PRIMARY_FIELD,
    eligible_route_fingerprint,
    eligible_route_samples,
    fixed_point_route_fingerprint,
)
from satn.ea_fixed_point_convergence import (
    EAFixedPointAcquisition,
    EAFixedPointCompilation,
    EAFixedPointSnapshot,
)
from satn.ea_fixed_point_operations import EAFixedPointProductionOperations
from satn.models import AreaDefinition
from satn.publisher import (
    WECA_PINNED_ELIGIBLE_ROUTE_BBOX,
    WECA_SURVEY_REQUEST_BBOX,
    _ea_fixed_point_next_step,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "acquire_ea_elevation.py"
SPEC = importlib.util.spec_from_file_location("acquire_ea_elevation_convergence", SCRIPT)
assert SPEC and SPEC.loader
acquisition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquisition)


def test_acquisition_refuses_an_eligible_route_collapsed_to_one_point(
    tmp_path: Path,
) -> None:
    route_path = tmp_path / "candidate.geojson"
    output_path = tmp_path / "elevation.geojson"
    feature_id = "spine-access-07d8d07fe59d"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": feature_id,
                "feature_type": "spine-access-connection",
                "topography_profile_id": "topography-profile-dc8152b42c505885",
                "geometry": LineString(
                    [
                        (369092.3832793793, 169040.53825675382),
                        (369092.3832793793, 169040.53825675382),
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(route_path, driver="GeoJSON")

    with pytest.raises(
        ValueError,
        match=(
            rf"{feature_id}.*collapses at identity precision.*"
            "regenerate the candidate network"
        ),
    ):
        acquisition.write_evidence(
            route_path,
            output_path,
            tmp_path / "cache",
            workers=1,
        )

    assert not output_path.with_name("elevation.sampled-routes.geojson").exists()


def test_current_weca_startup_reports_the_invalid_retained_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    feature_id = "spine-access-07d8d07fe59d"
    sampled_routes = tmp_path / "ea-elevation-sampled-routes.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": feature_id,
                "feature_type": "spine-access-connection",
                "topography_profile_id": "topography-profile-dc8152b42c505885",
                "geometry": LineString(
                    [
                        (369092.3832793793, 169040.53825675382),
                        (369092.3832793793, 169040.53825675382),
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(sampled_routes, driver="GeoJSON")
    snapshot_dir = tmp_path / config.source.snapshot_id
    snapshot_dir.mkdir()
    (snapshot_dir / "snapshot.json").write_text(
        json.dumps(
            {
                "evidence_sources": {
                    "elevation": {"pre_elevation_network_sha256": "a" * 64}
                },
                "provenance_file_sha256": {"network.geojson": "b" * 64},
            }
        ),
        encoding="utf-8",
    )
    config.source.snapshot_dir = tmp_path
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations._validated_ea_snapshot_replay_inputs",
        lambda _snapshot_dir: {"sample_routes": sampled_routes},
    )

    with pytest.raises(
        ValueError,
        match=rf"{feature_id}.*collapses at identity precision",
    ):
        EAFixedPointProductionOperations(config, run_token="test").initial_snapshot()


def test_malformed_acquisition_command_is_refused_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    operations = EAFixedPointProductionOperations(config, run_token="test")
    snapshot = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=Path("sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    compilation = EAFixedPointCompilation(
        expected_fingerprint="b" * 64,
        actual_fingerprint="d" * 64,
        candidate_network=Path("candidate.geojson"),
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=("/usr/bin/false",),
    )
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="command is malformed"):
        operations.acquire(snapshot, compilation)

    assert not executed


def test_acquisition_path_escape_is_refused_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    operations = EAFixedPointProductionOperations(config, run_token="test")
    snapshot = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=Path("sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    outside_candidate = tmp_path / "candidate.geojson"
    outside_evidence = tmp_path / "elevation.geojson"
    compilation = EAFixedPointCompilation(
        expected_fingerprint="b" * 64,
        actual_fingerprint="d" * 64,
        candidate_network=outside_candidate,
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=(
            "uv",
            "run",
            "python",
            "scripts/acquire_ea_elevation.py",
            str(outside_candidate),
            str(outside_evidence),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--spacing-m",
            "10",
            "--authority-boundaries",
            str(tmp_path / "authority-boundaries.geojson"),
            "--survey-index",
            str(tmp_path / "survey-index.geojson"),
            "--weca-preflight",
            "--routing-buffer-m",
            "15000",
            "--governed-input-fingerprint",
            "e" * 64,
            "--supplemental-routes",
            str(tmp_path / "sample-routes.geojson"),
        ),
    )
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="path escapes"):
        operations.acquire(snapshot, compilation)

    assert not executed


def test_snapshot_retry_validates_an_already_sealed_target_without_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    config.source.snapshot_dir = tmp_path
    operations = EAFixedPointProductionOperations(config, run_token="test")
    previous = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=Path("sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    acquisition = EAFixedPointAcquisition(
        primary_fingerprint="d" * 64,
        route_inventory=("route-a", "route-b"),
        evidence_path=tmp_path / "elevation.geojson",
    )
    target_id = f"{config.source.snapshot_id}-fp-test-01"
    (tmp_path / target_id).mkdir()
    expected = EAFixedPointSnapshot(
        snapshot_id=target_id,
        manifest_sha256="e" * 64,
        primary_fingerprint=acquisition.primary_fingerprint,
        retained_sample_routes=Path("retained-routes.geojson"),
        route_inventory=acquisition.route_inventory,
        governed_source_identities=(("network.geojson", "f" * 64),),
        parent_snapshot_id=previous.snapshot_id,
        parent_manifest_sha256=previous.manifest_sha256,
    )
    sealed = False

    def unexpected_seal(*_args: object, **_kwargs: object) -> None:
        nonlocal sealed
        sealed = True

    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.create_snapshot",
        unexpected_seal,
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations._snapshot_state",
        lambda _config, *, expected_parent: expected,
    )

    creation = operations.snapshot(previous, acquisition, 1)

    assert not sealed
    assert creation.snapshot == expected
    assert creation.snapshot_seal_ms == 0


def test_retained_supplemental_routes_contain_only_elevation_eligible_features(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary.geojson"
    supplemental = tmp_path / "supplemental.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "primary-spine",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            },
            {
                "feature_id": "irrelevant-gradient",
                "feature_type": "gradient-section",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350005, 150000)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(primary, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "supplemental-access",
                "feature_type": "spine-access-connection",
                "topography_profile_id": "profile-supplemental",
                "geometry": LineString([(350010, 150000), (350010, 150010)]),
            },
            {
                "feature_id": "irrelevant-area",
                "feature_type": "low-traffic-area",
                "topography_profile_id": "profile-area",
                "geometry": LineString([(350020, 150000), (350020, 150010)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(supplemental, driver="GeoJSON")

    retained = acquisition._combined_sample_routes(primary, [supplemental])

    assert retained["feature_type"].tolist() == [
        "strategic-spine",
        "spine-access-connection",
    ]
    assert retained[FIXED_POINT_PRIMARY_FIELD].tolist() == [True, False]
    assert fixed_point_route_fingerprint(retained) == eligible_route_fingerprint(
        gpd.read_file(primary)
    )
    samples, _feature_ids = eligible_route_samples(retained, spacing_m=10)
    assert {
        (sample["geometry"].x, sample["geometry"].y) for sample in samples
    } == {
        (350000, 150000),
        (350010, 150000),
        (350010, 150010),
    }


def test_acquisition_retains_only_normalized_elevation_routes_without_supplements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    output = tmp_path / "elevation.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "primary-spine",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            },
            {
                "feature_id": "irrelevant-gradient",
                "feature_type": "gradient-section",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350005, 150000)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (
            key,
            tmp_path / "synthetic.tif",
            "synthetic-url",
            "a" * 64,
            1,
            None,
        ),
    )
    monkeypatch.setattr(acquisition, "load_tile", lambda _path: object())
    monkeypatch.setattr(acquisition, "sample_grid", lambda _grid, _point: 42.0)

    manifest = acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        workers=1,
    )

    retained_path = output.with_name("elevation.sampled-routes.geojson")
    retained = gpd.read_file(retained_path)
    assert retained["feature_type"].tolist() == ["strategic-spine"]
    assert manifest["pre_elevation_network_sha256"] == eligible_route_fingerprint(
        gpd.read_file(routes)
    )
    assert fixed_point_route_fingerprint(retained) == manifest[
        "pre_elevation_network_sha256"
    ]


def test_supplemental_routes_support_three_set_convergence(tmp_path: Path) -> None:
    route_paths = {
        name: tmp_path / f"{name}.geojson" for name in ("first", "second", "third")
    }
    accumulated_path = tmp_path / "accumulated.geojson"
    for offset, (name, path) in enumerate(route_paths.items()):
        northing = 150000 + offset * 10
        gpd.GeoDataFrame(
            [
                {
                    "feature_id": name,
                    "feature_type": "strategic-spine",
                    "topography_profile_id": f"profile-{name}",
                    "geometry": LineString(
                        [(350000, northing), (350010, northing)]
                    ),
                }
            ],
            geometry="geometry",
            crs=27700,
        ).to_file(path, driver="GeoJSON")

    def selected_route(sampled_routes: gpd.GeoDataFrame) -> str:
        covered = {
            round(float(geometry.centroid.y))
            for geometry in sampled_routes.to_crs(27700).geometry
        }
        return "second" if 150010 not in covered else "third"

    accumulated = acquisition._combined_sample_routes(route_paths["first"], [])
    expected_actual = []
    for iteration, primary_name in enumerate(("first", "second", "third"), start=1):
        primary = route_paths[primary_name]
        if iteration > 1:
            accumulated.to_file(accumulated_path, driver="GeoJSON")
            accumulated = acquisition._combined_sample_routes(
                primary, [accumulated_path]
            )
        actual_name = selected_route(accumulated)
        expected_actual.append((primary_name, actual_name))
        if actual_name == primary_name:
            break

    assert expected_actual == [
        ("first", "second"),
        ("second", "third"),
        ("third", "third"),
    ]
    assert accumulated[FIXED_POINT_PRIMARY_FIELD].tolist() == [True, False, False]
    assert fixed_point_route_fingerprint(accumulated) == eligible_route_fingerprint(
        gpd.read_file(route_paths["third"])
    )


def test_fixed_point_next_step_replays_validated_accumulated_sample_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    authority_boundaries = snapshot / "ea-authority-boundaries.geojson"
    survey_index = snapshot / "ea-survey-index.geojson"
    accumulated_routes = snapshot / "ea-elevation-sampled-routes.geojson"
    candidate = tmp_path / "candidate.geojson"
    validation = tmp_path / "validation.geojson"
    elevation = tmp_path / "elevation.geojson"
    config = SimpleNamespace(
        source=SimpleNamespace(
            national_elevation=SimpleNamespace(path=elevation),
            snapshot_dir=tmp_path,
            snapshot_id=snapshot.name,
        )
    )
    west, south, east, north = WECA_PINNED_ELIGIBLE_ROUTE_BBOX
    monkeypatch.setattr("satn.publisher.gpd.read_file", lambda _path: object())
    monkeypatch.setattr(
        "satn.publisher.eligible_route_samples",
        lambda _routes, spacing_m: (
            [
                {"geometry": Point(west, south)},
                {"geometry": Point(east, north)},
            ],
            ["candidate"],
        ),
    )
    monkeypatch.setattr(
        "satn.publisher.governed_survey_request_bbox",
        lambda _routes, routing_buffer_m: tuple(
            int(value) for value in WECA_SURVEY_REQUEST_BBOX
        ),
    )
    monkeypatch.setattr(
        "satn.publisher._validated_ea_snapshot_replay_inputs",
        lambda _snapshot: {
            "authority_boundaries": authority_boundaries,
            "survey_index": survey_index,
            "sample_routes": accumulated_routes,
        },
    )

    result = _ea_fixed_point_next_step(
        config,
        candidate,
        validation_network=validation,
        governed_input_fingerprint="a" * 64,
    )
    command = shlex.split(result["next_step_command"])

    assert command[command.index("--supplemental-routes") + 1] == str(
        accumulated_routes
    )
