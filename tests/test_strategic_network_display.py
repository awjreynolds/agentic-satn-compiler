"""Focused tests for the display-only strategic Main geometry projection."""

from types import SimpleNamespace

from satn.strategic_network_publication import project_strategic_network


def _section(section_id: str, geometry_wkt: str) -> SimpleNamespace:
    return SimpleNamespace(
        section_id=section_id,
        obligation_id=f"journey-{section_id}",
        candidate_id=f"candidate-{section_id}",
        network_role="interurban-spine",
        routing_edge_ids=(),
        reverse_routing_edge_ids=(),
        geometry_wkt=geometry_wkt,
        authority="compiler",
        alignment_bases=("cycleway",),
        primary_alignment_basis="cycleway",
        intervention_state="existing-provision",
        display_state="existing-provision",
    )


def test_display_projection_draws_an_exact_shared_segment_once() -> None:
    result = SimpleNamespace(
        fingerprint="a" * 64,
        effective_network=SimpleNamespace(
            sections=(
                _section("bath-keynsham", "LINESTRING (0 0, 1 0, 2 0)"),
                _section("bath-radstock", "LINESTRING (3 0, 2 0, 1 0)"),
            )
        ),
        candidate_sets=(),
        selections=(),
        unselected_candidates=(),
        gaps=(),
        divergences=(),
    )

    projection = project_strategic_network(result, source_crs="EPSG:4326")

    display = projection.reviewable_feature_collection["strategic_main_display"]
    shared = [
        feature
        for feature in display["features"]
        if set(feature["properties"]["participating_journey_ids"])
        == {"bath-keynsham", "bath-radstock"}
    ]
    assert len(shared) == 1
    assert shared[0]["geometry"]["type"] in {"LineString", "MultiLineString"}
    coordinates = shared[0]["geometry"]["coordinates"]
    if shared[0]["geometry"]["type"] == "LineString":
        assert coordinates == [[1.0, 0.0], [2.0, 0.0]]
    else:
        assert coordinates == [[[1.0, 0.0], [2.0, 0.0]]]
    assert len(projection.layers["Strategic Main Network"]["features"]) == 2
    assert all(
        not feature["properties"].get("display_only")
        for feature in projection.reviewable_feature_collection["features"]
    )
