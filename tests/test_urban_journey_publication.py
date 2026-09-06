"""Urban endpoint names are display data, not additional access places."""

import json

from bath_saltford_fixture import configured_bath_saltford

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.publisher import _write_review_map
from satn.sources import load_snapshot, snapshot


def test_map_names_urban_endpoints_without_adding_access_places(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    source["label_places"] = labels
    compiled = compile_network(config, source, FakeAgentRuntime())
    (tmp_path / "backbone-comparison.json").write_text("{}")
    output = tmp_path / "review"
    output.mkdir()
    _write_review_map(output, config, compiled)
    data = json.loads(
        (output / "data.js").read_text().removeprefix("window.SATN_DATA = ").rstrip(";\n")
    )
    assert data["urban_journey_place_names"] == {
        place.place_id: place.name
        for place in compiled.strategic_corridor_preparation.urban_journeys.places
    }
    assert set(data["urban_journey_place_names"].values()) == {"Bath", "Saltford"}
    assert len(data["places"]["features"]) == len(compiled.places)
