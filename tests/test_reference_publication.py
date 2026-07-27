"""Reference SATN publication regressions for PRD #137."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from test_backbone_assembly import parallel_spine_source
from test_reference_application import reference_for_area
from test_reference_replay import (
    _configured_area,
    _reference_from_current_preparation,
)

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.pipeline import compile_reference
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
    assert "reference-satn-summary\" aria-labelledby=\"reference-satn-heading\" hidden" not in html


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
    forged["reference_publication_fingerprint"] = ""
    with pytest.raises(ValueError, match="decision contract disagrees"):
        ReferenceSATNPublicationRecord.from_publication_payload(forged)


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
