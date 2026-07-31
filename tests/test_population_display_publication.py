"""Publication coverage for local population-capture display sections."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
from bath_saltford_fixture import configured_bath_saltford

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.publisher import publish
from satn.sources import load_snapshot, snapshot


def test_population_display_sections_are_published_for_map_review(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    compiled = compile_network(config, load_snapshot(config), FakeAgentRuntime())

    artifacts = publish(config, compiled, "population-display-publication")

    sections = gpd.read_file(artifacts["geopackage"], layer="population_display_sections")
    geojson = json.loads(artifacts["geojson"].read_text(encoding="utf-8"))
    display_features = [
        feature
        for feature in geojson["features"]
        if feature["properties"]["feature_type"] == "population-display-section"
    ]
    assert set(sections["section_id"]) == {feature["id"] for feature in display_features}
    assert all(
        feature["properties"]["total_residents"]
        == feature["properties"]["inside_area_residents"]
        + feature["properties"]["outside_area_residents"]
        for feature in display_features
    )
    assert all(
        json.loads(value) == feature["properties"]["captured_oa_ids"]
        for value, feature in zip(
            sections.set_index("section_id").loc[
                [feature["id"] for feature in display_features], "captured_oa_ids"
            ],
            display_features,
            strict=True,
        )
    )

    review = artifacts["review_map"].parent
    html = artifacts["review_map"].read_text(encoding="utf-8")
    script = (review / "assets" / "review-map.js").read_text(encoding="utf-8")
    data = (review / "data.js").read_text(encoding="utf-8")
    assert 'id="layer-population-display-sections"' in html
    assert 'id="population-display-legend"' in html
    assert "populationDisplayScale(network.features)" in script
    assert '"population-display-section"' in script
    assert '"population_display_sections"' in data
