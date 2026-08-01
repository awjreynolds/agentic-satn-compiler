from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pypdf import PdfReader
from shapely.geometry import LineString

from lcwip import (
    ArtifactLink,
    AuditFinding,
    AuditFindingStatus,
    Plan,
    PlanHorizon,
    StudyArea,
)
from satn import (
    PublishedArtifactReference,
    PublishedNetworkFeatureReference,
    compile,
    published_artifact_reference,
    published_feature_reference,
)
from satn.constants import DISCLAIMER
from satn.filesystem_safety import publication_destination_authority
from satn.models import (
    CouncilConfig,
    ObservedThroughTrafficConfig,
    OfficialRoadClassificationConfig,
    TrafficLight,
)
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


def prepared_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    return config


def prepared_governed_urban_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "governed-urban-fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    places_path = fixture / "source" / "places.geojson"
    places = gpd.read_file(places_path)
    places["place_class"] = "town"
    places.to_file(places_path, driver="GeoJSON")
    network_path = fixture / "source" / "network.geojson"
    network = gpd.read_file(network_path)
    residential_grid = gpd.GeoDataFrame(
        [
            {
                "source_id": f"urban-horizontal-{index}",
                "highway": "residential",
                "geometry": LineString([(-2.49, latitude), (-2.48, latitude)]),
            }
            for index, latitude in enumerate((51.402, 51.405, 51.408, 51.41), start=1)
        ]
        + [
            {
                "source_id": f"urban-vertical-{index}",
                "highway": "residential",
                "geometry": LineString([(longitude, 51.4), (longitude, 51.412)]),
            }
            for index, longitude in enumerate((-2.488, -2.485, -2.482), start=1)
        ],
        crs=4326,
    )
    gpd.GeoDataFrame(
        pd.concat([network, residential_grid], ignore_index=True),
        geometry="geometry",
        crs=4326,
    ).to_file(network_path, driver="GeoJSON")
    context_path = fixture / "source" / "context.geojson"
    context = gpd.read_file(context_path)
    context.loc[
        context["feature_type"].isin(["ncn-route", "school"]),
        "network_scope",
    ] = "urban"
    context.to_file(context_path, driver="GeoJSON")
    classification_path = fixture / "source" / "official-roads.geojson"
    gpd.GeoDataFrame(
        [
            {
                "road_id": f"official-{classification}",
                "classification": classification,
                "geometry": LineString([(longitude, 51.4), (longitude, 51.412)]),
            }
            for classification, longitude in (
                ("A road", -2.49),
                ("B road", -2.48),
                ("Classified Unnumbered", -2.47),
                ("Unclassified", -2.475),
                ("", -2.465),
            )
        ]
        + [
            {
                "road_id": f"official-a-boundary-{position}",
                "classification": "A road",
                "geometry": LineString([(-2.49, latitude), (-2.48, latitude)]),
            }
            for position, latitude in (("south", 51.4), ("north", 51.412))
        ],
        crs=4326,
    ).to_file(classification_path, driver="GeoJSON")
    observed_traffic_path = fixture / "source" / "observed-through-traffic.geojson"
    gpd.GeoDataFrame(
        [
            {
                "id": "traffic-study-1",
                "geometry": LineString([(-2.491, 51.406), (-2.481, 51.412)]),
            }
        ],
        crs=4326,
    ).to_file(observed_traffic_path, driver="GeoJSON")
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    config.source.official_road_classification = OfficialRoadClassificationConfig(
        path=classification_path,
        source_id="tiny-council-highways",
        effective_date="2026-04-01",
        licence="Open Government Licence v3.0",
    )
    config.source.observed_through_traffic = ObservedThroughTrafficConfig(
        path=observed_traffic_path,
        source_id="tiny-council-traffic-study",
        effective_date="2026-03-01",
        licence="Open Government Licence v3.0",
    )
    snapshot(config)
    return config


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_public_artifact_reference_uses_the_successful_run_identity_and_file_digest(
    tmp_path: Path,
) -> None:
    result = compile(prepared_config(tmp_path))

    reference = published_artifact_reference(result, "geojson")

    assert isinstance(reference, PublishedArtifactReference)
    assert reference.run_id == result.run_id
    assert reference.artifact_key == "geojson"
    assert reference.uri == result.artifacts["geojson"].resolve().as_uri()
    assert reference.sha256 == checksum(result.artifacts["geojson"])
    assert reference.public_identifier == f"{result.run_id}:geojson"


def test_public_feature_reference_derives_one_geometry_free_feature_from_real_public_geojson(
    tmp_path: Path,
) -> None:
    result = compile(prepared_config(tmp_path))
    network = json.loads(result.artifacts["geojson"].read_text())
    feature = next(item for item in network["features"] if item["properties"].get("network_role"))

    reference = published_feature_reference(result, str(feature["id"]))

    assert isinstance(reference, PublishedNetworkFeatureReference)
    assert reference.run_id == result.run_id
    assert reference.feature_id == feature["id"]
    assert reference.feature_type == feature["properties"]["feature_type"]
    assert reference.network_role == feature["properties"]["network_role"]
    assert reference.source_artifact_uri == result.artifacts["geojson"].resolve().as_uri()
    assert reference.source_artifact_sha256 == checksum(result.artifacts["geojson"])
    assert "geometry" not in reference.model_dump()

    plan = Plan(
        plan_id="fixture-feature-plan",
        name="Fixture feature plan",
        study_area=StudyArea(
            area_id="fixture-area",
            name="Fixture area",
            boundary=ArtifactLink(
                artifact_id="fixture-area",
                uri="bundle://fixture-area",
                kind="study-area",
            ),
        ),
        horizon=PlanHorizon(start_year=2026, end_year=2027),
        satn_features=(reference,),
        audit_findings=(
            AuditFinding(
                finding_id="fixture-feature-audit",
                subject_id=reference.feature_id,
                status=AuditFindingStatus.UNKNOWN,
            ),
        ),
    )
    assert plan.audit_findings[0].subject_id == reference.feature_id

    with pytest.raises(ValueError, match="audit-referable identifiers"):
        Plan(
            plan_id="colliding-feature-plan",
            name="Colliding feature plan",
            study_area=StudyArea(
                area_id=reference.feature_id,
                name="Fixture area",
                boundary=ArtifactLink(
                    artifact_id="fixture-area",
                    uri="bundle://fixture-area",
                    kind="study-area",
                ),
            ),
            horizon=PlanHorizon(start_year=2026, end_year=2027),
            satn_features=(reference,),
        )

    with pytest.raises(ValueError, match="no feature"):
        published_feature_reference(result, "missing-feature")

    duplicate = tmp_path / "duplicate-network.geojson"
    duplicate.write_text(json.dumps({**network, "features": [*network["features"], feature]}))
    duplicate_result = result.model_copy(
        update={"artifacts": {**result.artifacts, "geojson": duplicate}}
    )
    with pytest.raises(ValueError, match="exactly one"):
        published_feature_reference(duplicate_result, str(feature["id"]))


def test_public_feature_reference_allows_a_real_published_feature_without_a_network_role(
    tmp_path: Path,
) -> None:
    result = compile(prepared_config(tmp_path))
    network = json.loads(result.artifacts["geojson"].read_text())
    feature = next(item for item in network["features"] if item["id"] == "a-road-fixture")

    reference = published_feature_reference(result, "a-road-fixture")

    assert reference.feature_id == "a-road-fixture"
    assert reference.feature_type == feature["properties"]["feature_type"]
    assert reference.network_role is None
    assert reference.source_artifact_sha256 == checksum(result.artifacts["geojson"])
    assert "geometry" not in reference.model_dump()


@pytest.mark.parametrize(
    ("field", "value", "requested_id", "message"),
    (
        ("type", "Bogus", "fixture", "type 'Feature'"),
        ("type", None, "fixture", "type 'Feature'"),
        ("id", None, "None", "feature identity"),
        ("id", ["feature"], "['feature']", "feature identity"),
        ("feature_type", ["connection"], "fixture", "feature_type"),
        ("feature_type", True, "fixture", "feature_type"),
        ("network_role", None, "fixture", "network_role"),
        ("network_role", " \t ", "fixture", "network_role"),
        ("network_role", True, "fixture", "network_role"),
        ("network_role", {"role": "spine"}, "fixture", "network_role"),
    ),
)
def test_public_feature_reference_rejects_malformed_geojson_identity_values(
    tmp_path: Path, field: str, value: object, requested_id: str, message: str
) -> None:
    result = compile(prepared_config(tmp_path))
    network = json.loads(result.artifacts["geojson"].read_text())
    feature = next(item for item in network["features"] if item["properties"].get("network_role"))
    feature = {**feature, "id": "fixture", "properties": dict(feature["properties"])}
    if field == "id":
        feature["id"] = value
    elif field == "type":
        feature["type"] = value
    else:
        feature["properties"][field] = value
    malformed = tmp_path / "malformed-network.geojson"
    malformed.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    malformed_result = result.model_copy(
        update={"artifacts": {**result.artifacts, "geojson": malformed}}
    )

    with pytest.raises(ValueError, match=message):
        published_feature_reference(malformed_result, requested_id)


@pytest.mark.parametrize(
    ("case", "geometry"),
    (
        ("missing", None),
        ("null", None),
        ("empty object", {}),
        ("empty coordinates", {"type": "LineString", "coordinates": []}),
        ("non-array coordinates", {"type": "LineString", "coordinates": "not-an-array"}),
    ),
)
def test_public_feature_reference_rejects_missing_empty_and_malformed_selected_geometry(
    tmp_path: Path, case: str, geometry: object
) -> None:
    result = compile(prepared_config(tmp_path))
    network = json.loads(result.artifacts["geojson"].read_text())
    feature = next(item for item in network["features"] if item["properties"].get("network_role"))
    feature = {**feature, "id": "fixture", "properties": dict(feature["properties"])}
    if case == "missing":
        feature.pop("geometry")
    else:
        feature["geometry"] = geometry
    malformed = tmp_path / "malformed-geometry-network.geojson"
    malformed.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    malformed_result = result.model_copy(
        update={"artifacts": {**result.artifacts, "geojson": malformed}}
    )

    with pytest.raises(ValueError, match="geometry"):
        published_feature_reference(malformed_result, "fixture")


def test_bundle_identifiers_zip_and_pdf_are_consistent(tmp_path: Path) -> None:
    result = compile(prepared_config(tmp_path))
    connections = gpd.read_file(result.artifacts["geopackage"], layer="spine_access_connections")
    network = json.loads(result.artifacts["geojson"].read_text())
    run = json.loads(result.artifacts["run"].read_text())
    agents = json.loads(result.artifacts["agents"].read_text())
    assert "connections" not in set(gpd.list_layers(result.artifacts["geopackage"])["name"])
    gated_access_ids = {
        feature["id"]
        for feature in network["features"]
        if feature["properties"]["feature_type"]
        in {"spine-access-connection", "school-access-connection"}
    }
    meeting_ids = {
        feature["id"]
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "branch-meeting-connection"
    }
    connector_ids = {
        feature["id"]
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "cross-spine-connector"
    }

    assert gated_access_ids == set(connections["access_connection_id"])
    assert gated_access_ids | meeting_ids == {
        record["connection_id"] for record in agents["records"]
    }
    authoritative_roles = {
        feature["id"]: feature["properties"]["network_role"]
        for feature in network["features"]
        if feature["id"] in gated_access_ids | meeting_ids | connector_ids
    }
    assert run["authoritative_features"] == [
        {"feature_id": feature_id, "network_role": role}
        for feature_id, role in sorted(authoritative_roles.items())
    ]
    agent_roles = {record["connection_id"]: record["network_role"] for record in agents["records"]}
    agent_roles.update(
        {
            reference["feature_id"]: reference["network_role"]
            for record in agents["records"]
            for reference in record["derived_features"]
        }
    )
    assert agent_roles == authoritative_roles
    assert run["connection_count"] == len(gated_access_ids | meeting_ids)
    assert run["network_model"] == "backbone-outward"
    assert run["compilation_diagnostics"]["assembly_strategy"] == "backbone-outward"
    assert run["compilation_diagnostics"]["candidate_evaluations"] > 0
    profiles = gpd.read_file(result.artifacts["geopackage"], layer="topography_profiles")
    profile_features = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "topography-profile"
    ]
    assert {feature["id"] for feature in profile_features} == set(profiles["profile_id"])
    assert set(profiles["evidence_status"]) == {"available", "evidence-unavailable"}
    unavailable_count = int((profiles["evidence_status"] == "evidence-unavailable").sum())
    assert run["topography"] == {
        "profile_count": len(profiles),
        "gradient_section_count": run["layer_counts"]["gradient_sections"],
        "evidence_unavailable_count": unavailable_count,
        "corroboration_count": 0,
        "alternative_trigger_count": int(connections["topography_alternative_trigger"].sum()),
        "easier_alternative_selected_count": int(
            (connections["topography_comparison_status"] == "easier-alternative-selected").sum()
        ),
        "original_retained_count": int(
            connections["topography_comparison_status"]
            .isin(
                [
                    "original-retained-no-easier-option",
                    "strategic-spine-retained",
                ]
            )
            .sum()
        ),
    }
    assert run["criteria"] == {
        section: {criterion: status for criterion, status in values.items()}
        for section, values in result.criteria.items()
    }

    review = result.artifacts["review_map"].parent
    assert (review / "backbone-comparison.json").read_bytes() == result.artifacts[
        "backbone_comparison"
    ].read_bytes()
    review_script = (review / "assets" / "review-map.js").read_text(encoding="utf-8")
    assert '"school-access-topography-warnings"' in review_script
    assert '["connections", "topography-retained-warnings"' not in review_script
    expected = {
        f"review-map/{item.relative_to(review)}" for item in review.rglob("*") if item.is_file()
    }
    with zipfile.ZipFile(result.artifacts["review_zip"]) as archive:
        assert set(archive.namelist()) == expected

    pdf = PdfReader(result.artifacts["pdf"])
    width = float(pdf.pages[0].mediabox.width)
    height = float(pdf.pages[0].mediabox.height)
    assert width > height
    assert width == pytest.approx(1190.55, abs=1)
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert all(value in text for value in (DISCLAIMER, "Legend", "scale", "Compiled"))
    assert "Authoritative edge register" in text
    assert "spine-access-connection" in text
    if connector_ids:
        assert "cross-spine-connector" in text
    assert connections.iloc[0]["access_connection_id"] in text


def test_failed_publication_preserves_the_previous_complete_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    before = {name: checksum(path) for name, path in first.artifacts.items() if path.is_file()}

    def fail_pdf(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated print failure")

    monkeypatch.setattr("satn.publisher._write_pdf", fail_pdf)
    config.compilation.full = True
    with pytest.raises(RuntimeError, match="simulated print failure"):
        compile(config)

    after = {name: checksum(path) for name, path in first.artifacts.items() if path.is_file()}
    assert after == before


def test_failed_final_install_rolls_back_the_previous_complete_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    before = {name: checksum(path) for name, path in first.artifacts.items() if path.is_file()}
    import satn.filesystem_safety as filesystem_safety

    original_rename = filesystem_safety.os.rename

    def fail_temporary_install(source: str, target: str, *args: object, **kwargs: object) -> None:
        if (
            source.startswith(f".{config.publication.output_dir.name}-")
            and not source.startswith(f".{config.publication.output_dir.name}-previous-")
            and target == config.publication.output_dir.name
        ):
            raise OSError("simulated final install failure")
        original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr(filesystem_safety.os, "rename", fail_temporary_install)
    config.compilation.full = True
    with pytest.raises(OSError, match="simulated final install failure"):
        compile(config)

    after = {name: checksum(path) for name, path in first.artifacts.items() if path.is_file()}
    assert after == before


def test_compiler_refuses_an_untrusted_existing_destination_outside_its_workspace(
    tmp_path: Path,
) -> None:
    """A definition cannot nominate an unrelated directory for replacement."""
    config = prepared_config(tmp_path)
    unrelated = tmp_path / "unrelated"
    sentinel = unrelated / "do-not-replace.txt"
    unrelated.mkdir()
    sentinel.write_text("preserve", encoding="utf-8")
    config.publication.output_dir = unrelated

    with pytest.raises(ValueError, match="outside the declared publication workspace"):
        compile(config)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_compiler_allows_an_explicit_capability_for_an_external_destination(
    tmp_path: Path,
) -> None:
    """A caller can deliberately grant an exact non-interactive external destination."""
    config = prepared_config(tmp_path)
    destination = tmp_path / "approved-external-output"
    config.publication.output_dir = destination
    authority = publication_destination_authority(
        workspace_root=config.config_path.parent,
        approved_external_destination=destination,
    )

    result = compile(config, publication_authority=authority)

    assert result.output_dir == destination
    assert (destination / ".satn-publication-owner.json").is_file()


def test_governed_urban_spines_and_ncn_evidence_publish_distinctly(tmp_path: Path) -> None:
    config = prepared_governed_urban_config(tmp_path)
    config.compilation.agent.review_statuses = ()
    result = compile(config)
    network = json.loads(result.artifacts["geojson"].read_text())
    run = json.loads(result.artifacts["run"].read_text())
    urban_spines = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "urban-spine"
    ]
    ncn_evidence = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "ncn-route"
    ]
    classification_unknowns = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "urban-classification-unknown"
    ]
    candidate_areas = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "low-traffic-area"
    ]
    area_portals = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "low-traffic-area-portal"
    ]
    school_obligations = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "school-access-obligation"
    ]
    school_connections = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "school-access-connection"
    ]
    school_street_assessments = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "school-street-assessment"
    ]

    assert network["urban_classification_status"] == "explicit-unknown"
    assert run["urban_classification_status"] == "explicit-unknown"
    assert run["layer_counts"]["urban_spines"] == 4
    assert run["layer_counts"]["urban_classification_unknowns"] == 1
    assert {feature["properties"]["official_classification"] for feature in urban_spines} == {
        "a-road",
        "b-road",
    }
    assert len({feature["id"] for feature in urban_spines}) == 4
    assert all(feature["id"].startswith("urban-spine-") for feature in urban_spines)
    assert all(
        feature["properties"]["source_id"] == "tiny-council-highways"
        and feature["properties"]["effective_date"] == "2026-04-01"
        and len(feature["properties"]["content_fingerprint"]) == 64
        for feature in urban_spines
    )
    assert len(classification_unknowns) == 1
    assert classification_unknowns[0]["properties"]["classification_status"] == ("explicit-unknown")
    assert not candidate_areas
    assert not area_portals
    assert len(school_obligations) == 1
    urban_school = school_obligations[0]
    assert urban_school["geometry"]["type"] == "Point"
    assert urban_school["properties"]["network_role"] == ("urban-school-access-obligation")
    assert urban_school["properties"]["service_status"] == "network-gap"
    assert urban_school["properties"].get("low_traffic_area_id") is None
    assert urban_school["properties"].get("portal_id") is None
    assert urban_school["properties"]["geometry_semantics"] == (
        "area-permeability-no-internal-centreline"
    )
    assert json.loads(urban_school["properties"]["fabric_source_ids"]) == []
    assert not school_connections
    assert len(school_street_assessments) == 1
    school_street = school_street_assessments[0]
    assert school_street["id"].startswith("school-street-assessment-")
    assert school_street["properties"]["assessment_status"] == "red"
    assert school_street["properties"]["assessment_label"] == "Unlikely"
    assert (
        "not scheme feasibility or calibrated probability"
        in school_street["properties"]["qualification"]
    )
    assert all(
        "school-fixture"
        not in {
            str(feature["properties"].get("from_place")),
            str(feature["properties"].get("to_place")),
        }
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "connection"
    )
    assert len(ncn_evidence) == 1
    assert ncn_evidence[0]["id"] == "ncn-fixture"
    assert ncn_evidence[0]["properties"]["network_scope"] == "urban"
    published_layers = set(gpd.list_layers(result.artifacts["geopackage"])["name"])
    assert {
        "urban_spines",
        "urban_classification_unknowns",
        "school_street_assessments",
    } <= published_layers
    assert "candidate_low_traffic_areas" not in published_layers
    assert "low_traffic_area_portals" not in published_layers
    published_obligations = gpd.read_file(
        result.artifacts["geopackage"], layer="access_obligations"
    )
    published_urban_school = published_obligations[
        published_obligations["network_role"] == "urban-school-access-obligation"
    ].iloc[0]
    assert pd.isna(published_urban_school["low_traffic_area_id"])
    assert pd.isna(published_urban_school["portal_id"])
    assert published_urban_school["geometry_semantics"] == (
        "area-permeability-no-internal-centreline"
    )
    published_school_streets = gpd.read_file(
        result.artifacts["geopackage"], layer="school_street_assessments"
    )
    assert list(published_school_streets["assessment_id"]) == [school_street["id"]]
    assert list(published_school_streets["rationale"]) == [school_street["properties"]["rationale"]]
    review_html = result.artifacts["review_map"].read_text()
    review_js = (result.artifacts["review_map"].parent / "assets/review-map.js").read_text()
    review_css_path = result.artifacts["review_map"].parent / "assets/review-map.css"
    css_digest = hashlib.sha256(review_css_path.read_bytes()).hexdigest()[:12]
    js_digest = hashlib.sha256(review_js.encode()).hexdigest()[:12]
    assert "Urban Main-Road Spines" in review_html
    assert f'href="assets/review-map.{css_digest}.css"' in review_html
    assert f'src="assets/review-map.{js_digest}.js"' in review_html
    assert "?v=" not in review_html
    assert (
        result.artifacts["review_map"].parent / f"assets/review-map.{css_digest}.css"
    ).read_bytes() == review_css_path.read_bytes()
    assert (
        result.artifacts["review_map"].parent / f"assets/review-map.{js_digest}.js"
    ).read_text() == review_js
    assert 'aria-label="Map legend"' in review_html
    assert "Cross-spine connector" in review_html
    assert "Candidate low-traffic area" in review_html
    assert "Network gap" in review_html
    assert 'id="layer-urban-classification-unknowns" type="checkbox"' in review_html
    assert 'id="layer-urban-classification-unknowns" type="checkbox" checked' not in review_html
    assert 'id="layer-low-traffic-area-portals" type="checkbox"' in review_html
    assert 'id="layer-low-traffic-area-portals" type="checkbox" checked' not in review_html
    assert "declassified NCN routes and Greenway cycleways" in review_html
    assert "not an existing LTN" in review_html
    assert "no preferred residential cycling centreline" in review_html
    assert "School Street Candidate Assessments" in review_html
    assert "Green — Promising" in review_html
    assert "Grey — Not Evaluated" in review_html
    assert '<a href="network-map.pdf" download>Network map PDF</a>' in review_html
    assert '"network_scope"], "urban"' not in review_js
    assert 'id: "strategic-network"' in review_js
    assert '"line-color": "#c0392b"' in review_js
    assert '"low-traffic-area-portal"].includes' in review_js


def test_public_compile_reviews_configured_grey_urban_school_gap(tmp_path: Path) -> None:
    config = prepared_governed_urban_config(tmp_path)
    context_path = config.source.fixture_dir / "context.geojson"
    context = gpd.read_file(context_path)
    school = context["feature_type"] == "school"
    context.loc[school, "access_point_status"] = "unresolved"
    context.loc[school, "access_point_source_id"] = None
    context.loc[school, "access_point_rationale"] = "No governed School Access Point is available."
    context.to_file(context_path, driver="GeoJSON")
    snapshot(config, replace=True)
    config.compilation.agent.review_statuses = (TrafficLight.GREY,)

    result = compile(config)

    request = result.decision_requests[0]
    assert result.status == "decision-required"
    assert result.artifacts == {}
    assert request.compilation_scope == "urban-school-access-gap"
    assert request.criterion == "endpoints"
    assert request.status == TrafficLight.GREY
