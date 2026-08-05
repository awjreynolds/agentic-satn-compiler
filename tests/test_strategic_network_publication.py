"""Focused contract tests for the strategic network review-map projection."""

import json
from types import SimpleNamespace

from satn.alignment_selection import CanonicalLineString
from satn.strategic_network_publication import (
    DEFAULT_LAYERS,
    OPTIONAL_LAYERS,
    project_strategic_network,
)


def test_publication_projection_imports() -> None:
    assert callable(project_strategic_network)


def _section(section_id: str, *, authority: str = "compiler", display: str = "existing-provision"):
    return SimpleNamespace(
        section_id=section_id,
        obligation_id=f"obligation-{section_id}",
        candidate_id=f"candidate-{section_id}",
        network_role="interurban-spine",
        routing_edge_ids=(f"edge-{section_id}",),
        reverse_routing_edge_ids=(f"reverse-{section_id}",),
        geometry_wkt="LINESTRING (100000 200000, 100100 200100)",
        authority=authority,
        alignment_bases=("cycleway",),
        primary_alignment_basis="cycleway",
        intervention_state="existing-provision",
        display_state=display,
    )


def _result(*sections, gaps=(), divergences=(), candidates=()):
    candidate_set = SimpleNamespace(
        candidate_set_id="candidate-set-1",
        network_role="interurban-spine",
        candidates=tuple(candidates),
    )
    return SimpleNamespace(
        fingerprint="a" * 64,
        effective_network=SimpleNamespace(sections=tuple(sections)),
        gaps=tuple(gaps),
        divergences=tuple(divergences),
        candidate_sets=(candidate_set,) if candidates else (),
        unselected_candidates=tuple(
            SimpleNamespace(
                candidate_id=item.candidate_id, disposition="unselected", reason="alternative"
            )
            for item in candidates
        ),
    )


def test_selected_network_and_places_are_the_only_default_layers() -> None:
    result = _result(_section("cycleway"), _section("connector"))
    places = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "place-bath",
                "properties": {"place_id": "bath"},
                "geometry": {"type": "Point", "coordinates": [-2.36, 51.38]},
            }
        ],
    }
    projection = project_strategic_network(result, places=places, places_crs="EPSG:4326")
    assert projection.default_layers == DEFAULT_LAYERS == ("Strategic Network", "Places")
    assert projection.optional_layers == OPTIONAL_LAYERS
    assert len(projection.layers["Strategic Network"]["features"]) == 2
    assert len(projection.layers["Places"]["features"]) == 1
    assert all(not projection.layers[name]["features"] for name in OPTIONAL_LAYERS)
    assert projection.layers["Strategic Network"]["features"][0]["geometry"]["type"] == "LineString"
    assert (
        projection.layers["Strategic Network"]["features"][0]["properties"][
            "strategic_result_fingerprint"
        ]
        == "a" * 64
    )


def test_reference_and_divergence_are_explicit_non_grey_variants() -> None:
    reference = _section(
        "reference", authority="governed-reference-provisional", display="reference-route"
    )
    divergence = SimpleNamespace(
        obligation_id="obligation-reference",
        network_role="interurban-spine",
        officer_candidate_id="candidate-officer",
        compiler_candidate_id="candidate-compiler",
        reason="officer decision retained",
    )
    projection = project_strategic_network(
        _result(reference, divergences=(divergence,)), optional_layers=True
    )
    ref = projection.layers["Strategic Network"]["features"][0]["properties"]
    assert ref["authority"] == "governed-reference-provisional"
    assert ref["pattern"] == "long-dash"
    divergence_feature = projection.layers["Officer Divergence"]["features"][0]
    divergence_props = divergence_feature["properties"]
    assert divergence_props["display_state"] == "officer-divergence"
    assert divergence_props["core"] not in {"#8c8c8c", "#606a73"}


def test_gaps_are_null_geometry_and_candidates_remain_optional() -> None:
    gap = SimpleNamespace(
        obligation_id="missing",
        network_role="strategic-destination-access",
        endpoints=("place-a", "destination-a"),
        reason="no admitted candidate",
    )
    candidate = SimpleNamespace(
        candidate_id="candidate-road",
        geometry=CanonicalLineString(coordinates=((100000.0, 200000.0), (100200.0, 200200.0))),
    )
    projection = project_strategic_network(
        _result(gaps=(gap,), candidates=(candidate,)), optional_layers=True
    )
    gap_feature = projection.layers["Strategic Network"]["features"][0]
    assert gap_feature["geometry"] is None
    assert (
        projection.layers["Candidates discarded"]["features"][0]["properties"]["candidate_id"]
        == "candidate-road"
    )
    assert (
        projection.layers["Candidates discarded"]["features"][0]["geometry"]["type"] == "LineString"
    )


def test_projection_is_json_serialisable_and_permutation_stable() -> None:
    first = project_strategic_network(_result(_section("b"), _section("a")))
    second = project_strategic_network(_result(_section("a"), _section("b")))
    assert first.projection_fingerprint == second.projection_fingerprint
    json.dumps(first.feature_collection, sort_keys=True)
    json.dumps(first.layers, sort_keys=True)
    assert all(
        "Backbone" not in json.dumps(feature) for feature in first.feature_collection["features"]
    )
