from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
from bath_saltford_fixture import configured_bath_saltford
from shapely.geometry import LineString, Point
from test_strategic_network_planning import discovery, fixture_graph

from satn.agents import FakeAgentRuntime
from satn.candidate_discovery import CorridorObligation
from satn.compiler import compile_network
from satn.sources import load_snapshot, snapshot
from satn.strategic_network_planning import (
    StrategicNetworkPlanningRequest,
    compile_strategic_network,
)
from satn.strategic_network_publication import project_strategic_network


def test_unroutable_urban_journey_is_a_visible_network_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("urban-gap", "X", "Y"))
    preparation = SimpleNamespace(
        units=(
            SimpleNamespace(
                unit_id="urban-gap",
                urban_journey_id="town-a-to-town-b",
                backbone_required=False,
                candidate_set=discovered.candidate_sets[0],
            ),
        ),
        issues=(),
    )
    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            corridor_obligations=preparation,
        )
    )
    assert result.status == "complete-with-gaps"
    assert [gap.obligation_id for gap in result.gaps] == ["urban-gap"]


def test_public_compile_publishes_isolated_canonical_place_gap(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)

    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    labels.loc[len(labels)] = {
        "element": "node",
        "id": "yate",
        "kind": "town",
        "name": "Yate",
        "geometry": Point(-2.45, 51.42),
    }
    source["label_places"] = gpd.GeoDataFrame(
        labels,
        geometry="geometry",
        crs=labels.crs,
    )

    network = source["network"].copy()
    isolated_rows = []
    for source_id, start, end, geometry in (
        (
            "isolated-yate-forward",
            "yate-a",
            "yate-b",
            LineString([(-2.45, 51.42), (-2.46, 51.42)]),
        ),
        (
            "isolated-yate-reverse",
            "yate-b",
            "yate-a",
            LineString([(-2.46, 51.42), (-2.45, 51.42)]),
        ),
    ):
        row = network.iloc[0].copy()
        row["source_id"] = source_id
        row["u"] = start
        row["v"] = end
        row["highway"] = "unclassified"
        row["ref"] = None
        row["name"] = "Synthetic isolated Yate edge"
        row["geometry"] = geometry
        isolated_rows.append(row)
    source["network"] = gpd.GeoDataFrame(
        pd.concat(
            [
                network,
                gpd.GeoDataFrame(isolated_rows, geometry="geometry", crs=network.crs),
            ],
            ignore_index=True,
        ),
        geometry="geometry",
        crs=network.crs,
    )

    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None and preparation.urban_journeys is not None
    issue = next(
        item for item in preparation.urban_journeys.issues if item.source_id == "node/yate"
    )
    yate_place = next(
        place for place in preparation.urban_journeys.places if place.source_id == "node/yate"
    )

    planning = compiled.strategic_network_planning
    assert planning.status == "complete-with-gaps"
    gap = next(gap for gap in planning.gaps if "node/yate" in gap.reason)
    assert gap.endpoints == (yate_place.place_id,)
    projection = project_strategic_network(planning)
    markers = [
        feature
        for feature in projection.layers["Strategic Main Network"]["features"]
        if feature["properties"].get("feature_type") == "reviewable-gap-endpoint"
    ]
    assert issue.detail in gap.reason
    assert len(markers) == 1
    assert markers[0]["properties"]["endpoints"] == [yate_place.place_id]
    assert any(
        feature["properties"]["obligation_id"] == gap.obligation_id
        and feature["geometry"] is not None
        for feature in markers
    )
