from __future__ import annotations

import json
import shutil

import geopandas as gpd
from bath_saltford_fixture import configured_bath_saltford
from shapely.geometry import LineString

from satn.planning_contracts import run_experimental_planning
from satn.sources import snapshot


def test_experimental_planning_retains_unattached_a_road_as_unresolved_corridor(
    tmp_path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    fixture_dir = tmp_path / "source"
    shutil.copytree(config.source.fixture_dir, fixture_dir)
    config.source.fixture_dir = fixture_dir
    context_path = fixture_dir / "context.geojson"
    context = gpd.read_file(context_path).set_crs(4326, allow_override=True)
    template = context.iloc[0].copy()
    template.update(
        {
            "evidence_id": "a-road-unattached-evidence",
            "feature_type": "a-road-spine",
            "name": "A999",
            "source_id": "official-a999-section-1",
            "network_scope": "rural",
            "geometry": LineString([(-2.365, 51.425), (-2.365, 51.428)]),
        }
    )
    context.loc[len(context)] = template
    context = context.set_crs(4326, allow_override=True)
    context.to_file(context_path, driver="GeoJSON")
    snapshot(config)

    result = run_experimental_planning(config)
    payload = result.as_dict()
    corridor = next(
        item
        for item in payload["source_corridors"]
        if item["source_refs"][0]["source_id"] == "official-a999-section-1"
    )

    assert payload["status"] == "admitted-with-unknowns"
    assert corridor["classification"] == "a-road"
    assert corridor["mandatory_planning_corridor"] is True
    assert corridor["departure_disposition"] == "not-assessed"
    assert corridor["source_refs"] == [
        {
            "evidence_id": "a-road-unattached-evidence",
            "source_id": "official-a999-section-1",
        }
    ]
    assert corridor["geometry_ref"]["geometry"] == {
        "type": "LineString",
        "coordinates": [[-2.365, 51.425], [-2.365, 51.428]],
    }
    assert corridor["topology_fact"] == {
        "graph_attachment": "unattached",
        "node_ids": [],
        "directed_edge_ids": [],
        "source_edge_ids": [],
        "status": "unresolved",
    }
    assert corridor["corridor_id"] in {item["subject_id"] for item in payload["planning_gaps"]}
    assert payload["exclusions"] == []
    assert json.loads(result.to_json()) == payload
