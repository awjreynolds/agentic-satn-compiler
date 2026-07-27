"""Reference SATN publication regressions for PRD #137."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
from playwright.sync_api import sync_playwright
from shapely.geometry import LineString, Polygon
from test_backbone_assembly import parallel_spine_source
from test_reference_application import reference_for_area
from test_reference_replay import (
    _configured_area,
    _reference_from_current_preparation,
)

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.pipeline import compile_reference, compile_reference_network
from satn.publisher import publish
from satn.reference_application import ReferenceSATNPublicationRecord


def _reference_fixture(tmp_path: Path):
    config = _configured_area(tmp_path)
    config.publication.output_dir = tmp_path / "reference-output"
    baseline = compile_network(config, parallel_spine_source(), FakeAgentRuntime())
    preparation = baseline.spine_access_candidate_preparation
    assert preparation is not None
    reference = reference_for_area(
        _reference_from_current_preparation(config, preparation),
        config,
    )
    return config, reference, preparation


def _data_payload(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    prefix = "window.SATN_DATA = "
    assert text.startswith(prefix) and text.endswith(";\n")
    return json.loads(text[len(prefix) : -2])


def _publishable_parallel_source():
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])]},
        geometry="geometry",
        crs=4326,
    )
    return source


def _refingerprint_publication(payload: dict[str, object]) -> None:
    unsigned = dict(payload)
    unsigned.pop("reference_publication_fingerprint", None)
    payload["reference_publication_fingerprint"] = hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_reference_compile_atomically_publishes_matching_canonical_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())

    result = compile_reference(config, reference, preparation)

    run = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))
    data = _data_payload(result.output_dir / "review-map" / "data.js")
    assert result.run_id.startswith("reference-")
    assert (
        run["reference_satn"]["reference_publication_fingerprint"]
        == (data["reference_satn"]["reference_publication_fingerprint"])
    )
    assert run["reference_satn"]["reference_selection"]["reference_selection_fingerprint"] == (
        reference.reference_selection_fingerprint
    )
    alternatives = data["reference_satn_options"]
    assert alternatives["type"] == "FeatureCollection"
    assert alternatives["features"]
    assert all(
        feature["geometry"]["coordinates"][0][0] < 180 for feature in alternatives["features"]
    )
    html = (result.output_dir / "review-map" / "index.html").read_text(encoding="utf-8")
    script = (result.output_dir / "review-map" / "assets").glob("review-map.*.js")
    assert "layer-reference-options" in html
    script_text = next(script).read_text(encoding="utf-8")
    assert "reference-satn-options" in script_text
    assert 'layout: { visibility: "none" }' in script_text
    assert html.count('class="reference-option"') == len(alternatives["features"])
    for feature in alternatives["features"]:
        candidate_id = feature["properties"]["candidate_id"]
        assert f'data-candidate-id="{candidate_id}"' in html
        assert f"{feature['properties']['disposition'].title()}: {candidate_id}" in html
    assert "500 m" in html and "1 km" in html
    assert "Education and independent travel" in html
    assert "Existing-alignment evidence and unknowns" in html
    assert "Directness and topography" in html
    assert "Decision and critique provenance" in html
    assert "<details" in html and "<summary>" in html
    assert 'reference-satn-summary" aria-labelledby="reference-satn-heading" hidden' not in html
    assert "Red dashed linework means selected" in html
    assert "purple dashed linework means complementary" in html
    assert "grey dashed linework means rejected" in html
    assert "All dashed alternative linework is review-only evidence" in html
    persistent_status = html.split('id="reference-options-status"', maxsplit=1)[1].split(
        "</div>", maxsplit=1
    )[0]
    assert (
        "All dashed alternative linework is review-only evidence, "
        "not the authoritative selected network."
    ) in persistent_status


def test_reference_publication_record_rejects_stale_self_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    result = compile_reference(config, reference, preparation)
    run = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))
    payload = run["reference_satn"]
    payload["application_diagnostics"] = {"status": "forged"}

    with pytest.raises(ValueError, match="diagnostics are stale"):
        ReferenceSATNPublicationRecord.from_publication_payload(payload)


def test_reference_publication_public_parser_requires_exact_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    result = compile_reference(config, reference, preparation)
    payload = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))[
        "reference_satn"
    ]

    missing = dict(payload)
    missing.pop("reference_publication_fingerprint")
    with pytest.raises(ValueError, match="requires its exact nonblank fingerprint"):
        ReferenceSATNPublicationRecord.from_publication_payload(missing)

    blank = dict(payload)
    blank["reference_publication_fingerprint"] = ""
    with pytest.raises(ValueError, match="requires its exact nonblank fingerprint"):
        ReferenceSATNPublicationRecord.from_publication_payload(blank)

    stale = dict(payload)
    stale["reference_publication_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="publication fingerprint is stale"):
        ReferenceSATNPublicationRecord.from_publication_payload(stale)


def test_reference_publication_record_is_deeply_immutable_and_rejects_forged_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    result = compile_reference(config, reference, preparation)
    payload = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))[
        "reference_satn"
    ]
    record = ReferenceSATNPublicationRecord.from_publication_payload(payload)
    fingerprint = record.reference_publication_fingerprint
    payload["application_diagnostics"]["status"] = "caller-mutated"

    assert record.reference_publication_fingerprint == fingerprint
    assert record.publication_payload()["application_diagnostics"]["status"] == "applied"
    forged = record.publication_payload()
    forged["decision_contract"] = "forged-contract"
    _refingerprint_publication(forged)
    with pytest.raises(ValueError, match="decision contract disagrees"):
        ReferenceSATNPublicationRecord.from_publication_payload(forged)


def test_publisher_rejects_freshly_refingerprinted_cross_lineage_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    compiled = compile_reference_network(
        config,
        FakeAgentRuntime(),
        reference,
        preparation,
    )
    publish(config, compiled, "reference-good")
    previous = (config.publication.output_dir / "run.json").read_bytes()
    original = compiled.reference_satn_publication
    assert original is not None

    snapshot_forgery = original.publication_payload()
    snapshot_forgery["snapshot_manifest_sha256"] = "0" * 64

    manifest_forgery = original.publication_payload()
    manifest = manifest_forgery["compilation_dependency_manifest"]
    assert isinstance(manifest, dict)
    components = manifest["components"]
    assert isinstance(components, list) and components
    components[0]["sha256"] = "0" * 64
    manifest["sha256"] = hashlib.sha256(
        json.dumps(
            {
                "schema_version": manifest["schema_version"],
                "dependency_set_version": manifest["dependency_set_version"],
                "components": components,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    diagnostics_forgery = original.publication_payload()
    diagnostics = diagnostics_forgery["application_diagnostics"]
    assert isinstance(diagnostics, dict)
    mapping = diagnostics["source_to_regenerated_access_connection_ids"]
    assert isinstance(mapping, dict) and mapping
    first_source = sorted(mapping)[0]
    mapping[first_source] = "foreign-regenerated-access"

    for forged_payload in (
        snapshot_forgery,
        manifest_forgery,
        diagnostics_forgery,
    ):
        _refingerprint_publication(forged_payload)
        compiled.reference_satn_publication = (
            ReferenceSATNPublicationRecord.from_publication_payload(forged_payload)
        )
        with pytest.raises(ValueError, match="not anchored"):
            publish(config, compiled, "reference-forged")
        assert (config.publication.output_dir / "run.json").read_bytes() == previous


def test_publisher_rejects_mutated_final_reference_route_rows_before_serialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    compiled = compile_reference_network(
        config,
        FakeAgentRuntime(),
        reference,
        preparation,
    )
    publish(config, compiled, "reference-good")
    previous = (config.publication.output_dir / "run.json").read_bytes()
    original = compiled.spine_access_connections.copy(deep=True)
    reference_indices = [
        index
        for index, row in original.iterrows()
        if "reference_application" in json.loads(str(row["provenance"]))
    ]
    assert reference_indices
    index = reference_indices[0]
    temporary_before = set(
        config.publication.output_dir.parent.glob(f".{config.publication.output_dir.name}-*")
    )

    for mutation in (
        "geometry",
        "community-node",
        "target-node",
        "row-role",
        "provenance-role",
        "forward-edges",
        "reverse-edges",
        "option-reverse-edges",
        "option-directness",
    ):
        rows = original.copy(deep=True)
        if mutation == "geometry":
            geometry = rows.at[index, "geometry"]
            rows.at[index, "geometry"] = LineString(
                [(float(x) + 0.001, float(y)) for x, y in geometry.coords]
            )
        elif mutation == "community-node":
            rows.at[index, "community_attachment_node"] = "forged-community-node"
        elif mutation == "target-node":
            rows.at[index, "target_attachment_node"] = "forged-target-node"
        elif mutation == "row-role":
            rows.at[index, "topography_selected_role"] = "forged-route-role"
        elif mutation in {
            "provenance-role",
            "forward-edges",
            "reverse-edges",
        }:
            provenance = json.loads(str(rows.at[index, "provenance"]))
            application = provenance["reference_application"]
            if mutation == "provenance-role":
                application["selected_route_role"] = "forged-route-role"
            elif mutation == "forward-edges":
                application["routing_edge_ids"] = ["forged-forward-edge"]
            else:
                application["reverse_routing_edge_ids"] = ["forged-reverse-edge"]
            rows.at[index, "provenance"] = json.dumps(provenance, sort_keys=True)
        else:
            options = json.loads(str(rows.at[index, "alignment_options"]))
            selected = next(option for option in options if option.get("selected") is True)
            if mutation == "option-reverse-edges":
                selected["reverse_edge_ids"] = ["forged-reverse-edge"]
            else:
                selected["length_km"] = float(selected["length_km"]) + 0.001
            rows.at[index, "alignment_options"] = json.dumps(options, sort_keys=True)
        compiled.spine_access_connections = rows

        with pytest.raises(ValueError, match="Compiled Reference"):
            publish(config, compiled, f"reference-forged-{mutation}")
        assert (config.publication.output_dir / "run.json").read_bytes() == previous
        assert (
            set(
                config.publication.output_dir.parent.glob(
                    f".{config.publication.output_dir.name}-*"
                )
            )
            == temporary_before
        )


@pytest.mark.browser
def test_reference_option_legend_remains_visible_while_layer_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    result = compile_reference(config, reference, preparation)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        control = page.locator("#layer-reference-options")
        status = page.locator("#reference-options-status")
        reference_information = page.get_by_role(
            "button",
            name="About reviewed Reference alternatives",
        )

        assert status.is_hidden()
        control.check()
        assert status.is_visible()
        assert all(
            meaning in status.inner_text() for meaning in ("selected", "complementary", "rejected")
        )
        persistent_semantics = (
            "All dashed alternative linework is review-only evidence, "
            "not the authoritative selected network."
        )
        assert persistent_semantics in status.inner_text()

        reference_information.click()
        page.locator("#map").hover()
        assert status.is_visible()
        assert persistent_semantics in status.inner_text()
        page.get_by_role("button", name="About the strategic network").click()
        assert status.is_visible()
        assert persistent_semantics in status.inner_text()

        control.uncheck()
        assert status.is_hidden()
        browser.close()


def test_reference_publication_failure_preserves_previous_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, reference, preparation = _reference_fixture(tmp_path)
    monkeypatch.setattr("satn.pipeline.load_snapshot", lambda area: _publishable_parallel_source())
    first = compile_reference(config, reference, preparation)
    previous = (first.output_dir / "run.json").read_bytes()
    monkeypatch.setattr(
        "satn.pipeline.load_snapshot",
        lambda area: (_ for _ in ()).throw(ValueError("fresh baseline rejected")),
    )

    with pytest.raises(ValueError, match="fresh baseline rejected"):
        compile_reference(config, reference, preparation)
    assert (first.output_dir / "run.json").read_bytes() == previous
