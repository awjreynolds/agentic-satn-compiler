"""Focused contract tests for the strategic network review-map projection."""

import json
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import Point

from satn.alignment_selection import CanonicalLineString
from satn.publisher import _reviewable_map_collection
from satn.strategic_network_planning import ReviewableNetworkGap
from satn.strategic_network_publication import (
    DEFAULT_LAYERS,
    OPTIONAL_LAYERS,
    project_strategic_network,
)


def test_publication_projection_imports() -> None:
    assert callable(project_strategic_network)


def _section(
    section_id: str,
    *,
    authority: str = "compiler",
    display: str = "existing-provision",
    network_role: str = "interurban-spine",
):
    return SimpleNamespace(
        section_id=section_id,
        obligation_id=f"obligation-{section_id}",
        candidate_id=f"candidate-{section_id}",
        network_role=network_role,
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
    assert projection.default_layers == DEFAULT_LAYERS == ("Strategic Main Network", "Places")
    assert projection.optional_layers == OPTIONAL_LAYERS
    assert len(projection.layers["Strategic Main Network"]["features"]) == 2
    assert len(projection.layers["Places"]["features"]) == 1
    assert all(not projection.layers[name]["features"] for name in OPTIONAL_LAYERS)
    assert (
        projection.layers["Strategic Main Network"]["features"][0]["geometry"]["type"]
        == "LineString"
    )
    assert (
        projection.layers["Strategic Main Network"]["features"][0]["properties"][
            "strategic_result_fingerprint"
        ]
        == "a" * 64
    )


def test_stored_roles_are_published_as_main_or_access_support_without_roster_loss() -> None:
    main_roles = {
        "interurban-spine",
        "urban-main-road-spine",
    }
    access_roles = {
        "cross-spine-connector",
        "community-access",
        "school-access",
        "strategic-destination-access",
    }
    sections = tuple(
        _section(role, network_role=role) for role in (*sorted(main_roles), *sorted(access_roles))
    )

    projection = project_strategic_network(_result(*sections))

    assert projection.default_layers == ("Strategic Main Network", "Places")
    assert projection.optional_layers[0] == "Access Support"
    assert {
        feature["properties"]["network_role"]
        for feature in projection.layers["Strategic Main Network"]["features"]
    } == main_roles
    assert {
        feature["properties"]["network_role"]
        for feature in projection.layers["Access Support"]["features"]
    } == access_roles
    assert {
        feature["properties"]["network_role"]
        for feature in projection.feature_collection["features"]
        if feature["properties"].get("feature_type") == "reviewable-selected-route"
    } == main_roles
    reviewable_routes = {
        feature["properties"]["network_role"]
        for feature in projection.reviewable_feature_collection["features"]
        if feature["properties"].get("feature_type") == "reviewable-selected-route"
    }
    assert reviewable_routes == main_roles | access_roles
    assert all(
        feature["properties"]["strategic_result_fingerprint"] == "a" * 64
        for feature in projection.reviewable_feature_collection["features"]
        if feature["properties"].get("feature_type") == "reviewable-selected-route"
    )


def test_required_urban_spine_is_published_as_selected_strategic_geometry() -> None:
    urban = SimpleNamespace(
        section_id="urban-spine-bristol-a4",
        obligation_id="urban-structure:urban-spine-bristol-a4",
        candidate_id=None,
        network_role="urban-main-road-spine",
        routing_edge_ids=("urban-edge-a4",),
        reverse_routing_edge_ids=(),
        geometry_wkt="LINESTRING (100000 200000, 100100 200100)",
        authority="compiler",
        alignment_bases=("a-road",),
        primary_alignment_basis="a-road",
        intervention_state="upgrade-required",
        display_state="upgrade-required",
    )

    projection = project_strategic_network(_result(urban))

    feature = projection.layers["Strategic Main Network"]["features"][0]
    assert feature["id"] == "urban-spine-bristol-a4"
    assert feature["properties"]["network_role"] == "urban-main-road-spine"
    assert feature["properties"]["selection_disposition"] == "selected"
    assert feature["properties"]["display_state"] == "upgrade-required"


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
    ref = projection.layers["Strategic Main Network"]["features"][0]["properties"]
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
    gap_feature = projection.layers["Access Support"]["features"][0]
    assert gap_feature["geometry"] is None
    assert (
        projection.layers["Candidates discarded"]["features"][0]["properties"]["candidate_id"]
        == "candidate-road"
    )
    assert (
        projection.layers["Candidates discarded"]["features"][0]["geometry"]["type"] == "LineString"
    )


def test_structural_gap_coordinates_publish_endpoint_markers_without_places() -> None:
    gap = ReviewableNetworkGap(
        obligation_id="a-road-obligation",
        network_role="interurban-spine",
        endpoints=("official-start", "official-end"),
        reason="official A-road endpoint is not attached",
        endpoint_coordinates=((100000.0, 200000.0), (100100.0, 200100.0)),
    )

    projection = project_strategic_network(_result(gaps=(gap,)))

    features = projection.layers["Strategic Main Network"]["features"]
    markers = [
        feature
        for feature in features
        if feature["properties"].get("feature_type") == "reviewable-gap-endpoint"
    ]
    assert [feature["geometry"]["type"] for feature in markers] == ["Point", "Point"]
    assert all(not feature["properties"]["missing_endpoint_geometry"] for feature in markers)


def test_projection_is_json_serialisable_and_permutation_stable() -> None:
    first = project_strategic_network(_result(_section("b"), _section("a")))
    second = project_strategic_network(_result(_section("a"), _section("b")))
    assert first.projection_fingerprint == second.projection_fingerprint
    json.dumps(first.feature_collection, sort_keys=True)
    json.dumps(first.layers, sort_keys=True)
    assert all(
        "Backbone" not in json.dumps(feature) for feature in first.feature_collection["features"]
    )


def test_projection_owns_contextual_evidence_and_final_reviewable_collection() -> None:
    result = _result(_section("selected"))
    projection = project_strategic_network(
        result,
        places={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "place-a",
                    "properties": {"place_id": "place-a"},
                    "geometry": {"type": "Point", "coordinates": [-2.0, 51.0]},
                }
            ],
        },
        places_crs="EPSG:4326",
        assets=[
            {
                "asset_id": "asset-a",
                "intervention_state": "existing-provision",
                "geometry": {"type": "LineString", "coordinates": [[-2.0, 51.0], [-1.9, 51.0]]},
            }
        ],
        upgradeable_assets=[
            {
                "asset_id": "asset-b",
                "intervention_state": "upgrade-required",
                "geometry": {"type": "LineString", "coordinates": [[-2.0, 51.0], [-1.9, 51.0]]},
            }
        ],
        traffic=[
            {
                "observation_id": "traffic-a",
                "geometry": {"type": "Point", "coordinates": [-2.0, 51.0]},
            }
        ],
        diagnostics=[{"diagnostic_id": "diag-a", "message": "retained"}],
        source_crs="EPSG:4326",
        optional_layers=True,
    )

    assert {
        feature["properties"]["feature_type"]
        for feature in projection.reviewable_feature_collection["features"]
    } >= {
        "reviewable-selected-route",
        "asset-existing-provision",
        "asset-upgrade-required",
        "dft-motor-traffic",
    }
    assert [
        feature["properties"]["layer"] for feature in projection.feature_collection["features"]
    ] == ["Strategic Main Network", "Places"]
    assert (
        projection.projection_fingerprint
        != project_strategic_network(
            result,
            places={"type": "FeatureCollection", "features": []},
            assets=[],
            upgradeable_assets=[],
            traffic=[],
            diagnostics=[],
            source_crs="EPSG:4326",
            optional_layers=True,
        ).projection_fingerprint
    )


def test_projection_keeps_contextual_families_and_divergence_variants_owned() -> None:
    compiler = SimpleNamespace(
        candidate_id="candidate-compiler",
        geometry=CanonicalLineString(coordinates=((100000.0, 200000.0), (100100.0, 200100.0))),
        network_role="community-access",
        evidence_fingerprints=("compiler-evidence",),
        intervention_state="existing-provision",
        alignment_bases=("mapped-cycleway",),
    )
    officer = SimpleNamespace(
        candidate_id="candidate-officer",
        geometry=CanonicalLineString(coordinates=((100000.0, 200000.0), (100100.0, 200000.0))),
        network_role="community-access",
        evidence_fingerprints=("officer-evidence",),
        intervention_state="upgrade-required",
        alignment_bases=("local-connector",),
    )
    divergence = SimpleNamespace(
        obligation_id="obligation-selected",
        network_role="community-access",
        officer_candidate_id="candidate-officer",
        compiler_candidate_id="candidate-compiler",
        reason="officer choice differs",
    )
    gap = SimpleNamespace(
        gap_id="gap-governed",
        obligation_id="obligation-gap",
        network_role="strategic-destination-access",
        endpoints=("place-a", "missing-place"),
        reason="no admitted candidate",
    )
    result = _result(
        _section("selected"),
        gaps=(gap,),
        divergences=(divergence,),
        candidates=(compiler, officer),
    )
    projection = project_strategic_network(
        result,
        places={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "place-a",
                    "properties": {"place_id": "place-a"},
                    "geometry": {"type": "Point", "coordinates": [-2.0, 51.0]},
                }
            ],
        },
        places_crs="EPSG:4326",
        traffic=[{"candidate_id": "candidate-compiler", "observation_id": "traffic-1"}],
        source_crs="EPSG:27700",
        optional_layers=True,
    )

    divergence_features = projection.layers["Officer Divergence"]["features"]
    assert {
        (feature["properties"]["divergence_variant"], feature["properties"]["candidate_id"])
        for feature in divergence_features
    } == {
        ("compiler", "candidate-compiler"),
        ("officer", "candidate-officer"),
    }
    traffic = projection.layers["DfT Traffic"]["features"][0]
    assert traffic["properties"]["geometry_semantics"] == (
        "bounded-candidate-route-evidence-no-point"
    )
    gap_features = {
        feature["properties"]["endpoint_id"]: feature
        for feature in projection.layers["Access Support"]["features"]
        if feature["properties"].get("feature_type") == "reviewable-gap-endpoint"
    }
    assert gap_features["place-a"]["geometry"]["type"] == "Point"
    assert gap_features["missing-place"]["geometry"] is None
    assert gap_features["missing-place"]["properties"]["missing_endpoint_geometry"] is True


def test_publisher_uses_projection_owned_reviewable_roster_without_legacy_splice() -> None:
    result = _result(_section("selected"))
    compiled = SimpleNamespace(
        strategic_network_planning=result,
        places={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "place-a",
                    "properties": {"place_id": "place-a"},
                    "geometry": {"type": "Point", "coordinates": [-2.0, 51.0]},
                }
            ],
        },
        asset_accounting={
            "contract": "satn-asset-accounting/v1",
            "records": [
                {
                    "asset_id": "asset-a",
                    "intervention_state": "existing-provision",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-2.0, 51.0], [-1.9, 51.0]],
                    },
                    "geometry_crs": "EPSG:4326",
                }
            ],
        },
    )
    payload = _reviewable_map_collection(compiled)
    feature_types = {feature["properties"].get("feature_type") for feature in payload["features"]}
    assert "asset-existing-provision" in feature_types
    assert "reviewable-strategic-spine" not in json.dumps(payload)
    assert (
        payload["projection_fingerprint"]
        == project_strategic_network(
            result,
            places=compiled.places,
            assets=compiled.asset_accounting["records"],
            source_crs="EPSG:27700",
            places_crs="EPSG:4326",
            optional_layers=True,
        ).projection_fingerprint
    )


def test_projection_gaps_with_empty_duplicate_endpoints_have_stable_fallback_ids() -> None:
    gap = SimpleNamespace(
        gap_id="discovery",
        obligation_id="obligation-gap",
        network_role="strategic-destination-access",
        endpoints=("", ""),
        reason="missing governed endpoint identities",
    )
    projection = project_strategic_network(_result(gaps=(gap,)))
    gap_features = [
        feature
        for feature in projection.layers["Access Support"]["features"]
        if feature["properties"].get("feature_type") == "reviewable-gap-endpoint"
    ]

    assert [feature["id"] for feature in gap_features] == [
        "reviewable-gap:discovery:endpoint-missing-1",
        "reviewable-gap:discovery:endpoint-missing-2",
    ]
    assert [feature["properties"]["endpoint_id"] for feature in gap_features] == ["", ""]
    assert [feature["properties"]["endpoint_position"] for feature in gap_features] == [1, 2]
    assert all(
        feature["properties"]["endpoint_identity_fallback"] is True and feature["geometry"] is None
        for feature in gap_features
    )


def test_publisher_projection_resolves_geodataframe_place_endpoints() -> None:
    gap = SimpleNamespace(
        gap_id="place-gap",
        obligation_id="place-gap-obligation",
        network_role="strategic-destination-access",
        endpoints=("place-a", "place-b"),
        reason="missing access connection",
    )
    places = gpd.GeoDataFrame(
        [
            {"place_id": "place-a", "geometry": Point(-2.1, 51.3)},
            {"place_id": "place-b", "geometry": Point(-2.0, 51.4)},
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    compiled = SimpleNamespace(
        strategic_network_planning=_result(gaps=(gap,)),
        places=places,
        asset_accounting={"records": []},
    )

    payload = _reviewable_map_collection(compiled)
    gap_features = [
        feature
        for feature in payload["features"]
        if feature["properties"].get("feature_type") == "reviewable-gap-endpoint"
    ]

    assert [feature["geometry"] for feature in gap_features] == [
        {"type": "Point", "coordinates": [-2.1, 51.3]},
        {"type": "Point", "coordinates": [-2.0, 51.4]},
    ]


def test_projection_distinguishes_gap_findings_for_the_same_obligation() -> None:
    gaps = (
        ReviewableNetworkGap(
            obligation_id="shared-obligation",
            network_role="unresolved-strategic-alignment",
            endpoints=("place-a", "place-b"),
            reason="prepared route is unusable",
        ),
        ReviewableNetworkGap(
            obligation_id="shared-obligation",
            network_role="interurban-spine",
            endpoints=("place-a", "place-b"),
            reason="no admitted candidate",
            candidate_set_id="candidate-set-a",
        ),
    )

    projection = project_strategic_network(_result(gaps=gaps))
    gap_features = [
        feature
        for feature in projection.reviewable_feature_collection["features"]
        if feature["properties"].get("feature_type") == "reviewable-gap-endpoint"
    ]
    feature_ids = [feature["id"] for feature in gap_features]

    assert len(feature_ids) == 4
    assert len(set(feature_ids)) == 4
    assert {feature["properties"]["gap_id"] for feature in gap_features} == {
        gap.gap_id for gap in gaps
    }


def test_projection_diagnostics_are_data_only_with_permutation_stable_ids() -> None:
    diagnostics = (
        SimpleNamespace(code="z-code", subject_id="subject-z", message="last"),
        SimpleNamespace(code="a-code", subject_id="subject-a", message="first"),
    )
    first = project_strategic_network(
        _result(_section("selected")), diagnostics=diagnostics, optional_layers=True
    )
    second = project_strategic_network(
        _result(_section("selected")),
        diagnostics=tuple(reversed(diagnostics)),
        optional_layers=True,
    )

    first_layer = first.layers["Graph Diagnostics"]
    second_layer = second.layers["Graph Diagnostics"]
    assert first_layer["features"] == second_layer["features"] == []
    assert [record["diagnostic_id"] for record in first_layer["records"]] == [
        record["diagnostic_id"] for record in second_layer["records"]
    ]
    assert all(record["layer"] == "Graph Diagnostics" for record in first_layer["records"])
    assert first.reviewable_feature_collection["diagnostics"] == first_layer["records"]
    assert all(
        feature["properties"].get("feature_type") != "graph-diagnostic"
        for feature in first.reviewable_feature_collection["features"]
    )


def test_projection_reprojects_bng_assets_and_retains_source_crs() -> None:
    projection = project_strategic_network(
        _result(_section("selected")),
        assets=[
            {
                "asset_id": "asset-bng",
                "intervention_state": "existing-provision",
                "geometry_crs": "EPSG:27700",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[370000, 170000], [370100, 170100]],
                },
            }
        ],
        optional_layers=True,
    )
    feature = projection.layers["Existing Assets"]["features"][0]
    assert feature["properties"]["geometry_crs"] == "EPSG:4326"
    assert feature["properties"]["source_geometry_crs"] == "EPSG:27700"
    assert feature["geometry"]["coordinates"][0][0] < 0


def test_projection_uses_declared_asset_crs_not_coordinate_magnitude() -> None:
    projection = project_strategic_network(
        _result(_section("selected")),
        assets=[
            {
                "asset_id": "asset-declared-wgs",
                "intervention_state": "existing-provision",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[370000, 170000], [370100, 170100]],
                },
            }
        ],
        assets_crs="EPSG:4326",
        optional_layers=True,
    )
    feature = projection.layers["Existing Assets"]["features"][0]
    assert feature["properties"]["source_geometry_crs"] == "EPSG:4326"
    assert feature["geometry"]["coordinates"][0][0] == 370000


def test_publisher_leaves_candidate_traffic_derivation_to_projection() -> None:
    candidate = SimpleNamespace(
        candidate_id="candidate-traffic",
        geometry=CanonicalLineString(coordinates=((100000.0, 200000.0), (100100.0, 200100.0))),
        network_role="community-access",
        traffic_observations=(SimpleNamespace(observation_id="traffic-1"),),
    )
    result = _result(_section("selected"), candidates=(candidate,))
    compiled = SimpleNamespace(
        strategic_network_planning=result,
        places={"type": "FeatureCollection", "features": []},
        asset_accounting={"records": []},
    )

    payload = _reviewable_map_collection(compiled)
    traffic = [
        feature
        for feature in payload["features"]
        if feature["properties"].get("feature_type") == "dft-motor-traffic"
    ]
    assert len(traffic) == 1
    assert traffic[0]["properties"]["geometry_semantics"] == (
        "bounded-candidate-route-evidence-no-point"
    )
