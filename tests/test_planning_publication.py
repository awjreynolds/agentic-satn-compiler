from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from shapely.geometry import shape

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.planning_contracts import _stable_id
from satn.planning_publication import (
    PublicationValidationError,
    project_planning_output,
    publish_planning_output,
)


def _geometry(geometry_id: str, coordinates: list[list[float]]) -> dict[str, object]:
    return {
        "geometry_id": geometry_id,
        "crs": "EPSG:4326",
        "geometry_kind": "LineString",
        "content_fingerprint": geometry_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def _validated_output() -> dict[str, object]:
    full_ref = _geometry("geom-full", [[-2.40, 51.30], [-2.30, 51.30]])
    partial_source_ref = _geometry("geom-partial-source", [[-2.30, 51.30], [-2.20, 51.30]])
    partial_ref = _geometry("geom-partial-affected", [[-2.30, 51.30], [-2.25, 51.30]])
    source_only_ref = _geometry("geom-source-only", [[-2.20, 51.30], [-2.10, 51.30]])
    return {
        "schema_version": "validated-output/v1",
        "status": "reviewable-incomplete",
        "output_id": "planning-case-1",
        "output_fingerprint": "output-fingerprint-1",
        "state_fingerprint": "state-fingerprint-1",
        "proposal_state_ref": "proposal-state-1",
        "problem_ref": "planning-problem-1",
        "source_inventory": [
            {
                "corridor_id": "corridor-full",
                "section_id": "section-full",
                "classification": "a-road",
                "mandatory_planning_corridor": True,
                "topology_fact": {"status": "resolved"},
                "geometry_ref": full_ref,
                "source_refs": ["source-full"],
                "evidence_refs": ["evidence-full"],
                "provision_status": "current",
            },
            {
                "corridor_id": "corridor-partial",
                "section_id": "section-partial",
                "classification": "a-road",
                "mandatory_planning_corridor": True,
                "topology_fact": {"status": "resolved"},
                "geometry_ref": partial_source_ref,
                "source_refs": ["source-partial"],
                "evidence_refs": ["evidence-partial"],
                "provision_status": "unknown",
            },
            {
                "corridor_id": "corridor-source-only",
                "section_id": "section-source-only",
                "classification": "a-road",
                "mandatory_planning_corridor": True,
                "topology_fact": {"status": "unresolved"},
                "geometry_ref": source_only_ref,
                "source_refs": ["source-only"],
                "evidence_refs": ["evidence-source-only"],
                "provision_status": "unknown",
            },
        ],
        "places": [],
        "selected_alignments": [
            {
                "candidate_id": "candidate-current",
                "source_corridor_refs": ["corridor-full"],
                "current_or_future": "current",
                "geometry_ref": _geometry(
                    "geom-selected-current", [[-2.40, 51.301], [-2.30, 51.301]]
                ),
            },
            {
                "candidate_id": "candidate-future",
                "source_corridor_refs": ["corridor-partial"],
                "current_or_future": "future",
                "geometry_ref": _geometry("geom-selected-future", [[-2.30, 51.31], [-2.20, 51.31]]),
            },
            {
                "candidate_id": "candidate-unknown",
                "source_corridor_refs": ["corridor-source-only"],
                "current_or_future": "unknown",
                "geometry_ref": _geometry(
                    "geom-selected-unknown", [[-2.20, 51.32], [-2.10, 51.32]]
                ),
            },
        ],
        "departures": [
            {
                "departure_id": "departure-full",
                "source_corridor_refs": ["corridor-full"],
                "extent": "full",
                "affected_geometry_refs": [full_ref],
                "reason": "current provision cannot carry the governed connection",
                "evidence_refs": ["evidence-full"],
                "outcome": {"kind": "alternate", "candidate_id": "candidate-current"},
            },
            {
                "departure_id": "departure-partial",
                "source_corridor_refs": ["corridor-partial"],
                "extent": "partial",
                "affected_geometry_refs": [partial_ref],
                "reason": "protected section needs a separate treatment",
                "evidence_refs": ["evidence-partial"],
                "outcome": {"kind": "no-loss", "no_loss_ref": "evidence-partial"},
            },
        ],
        "planning_gaps": [
            {
                "gap_id": "gap-source-only",
                "subject_id": "corridor-source-only",
                "reason": "no graph attachment",
            }
        ],
        "unknown_facts": [
            {
                "unknown_id": "unknown-source-only",
                "subject_id": "corridor-source-only",
                "claim": "routing graph attachment",
            }
        ],
        "future_interventions": [],
        "validation": {"diagnostics": []},
    }


def test_projection_keeps_full_partial_and_source_only_records_distinct() -> None:
    projected = project_planning_output(
        _validated_output(),
        {"history_ref": "history-1", "branch_id": "branch-main"},
    )

    assert projected["source_inventory"][2]["corridor_id"] == "corridor-source-only"
    assert projected["selected_alignments"][0]["current_or_future"] == "current"
    assert projected["selected_alignments"][1]["current_or_future"] == "future"
    assert {item["extent"] for item in projected["departures"]} == {"full", "partial"}
    assert projected["state_fingerprint"] == "state-fingerprint-1"

    features = projected["geojson"]["features"]
    source_features = [
        item for item in features if item["properties"]["feature_type"] == "planning-source"
    ]
    departure_features = [
        item for item in features if item["properties"]["feature_type"] == "planning-departure"
    ]
    assert {item["properties"]["source_corridor_id"] for item in source_features} == {
        "corridor-full",
        "corridor-partial",
        "corridor-source-only",
    }
    assert {item["properties"]["departure_id"] for item in departure_features} == {
        "departure-full",
        "departure-partial",
    }
    assert not any(
        item["properties"].get("source_corridor_refs") == ["corridor-source-only"]
        and item["properties"]["feature_type"] == "planning-departure"
        for item in features
    )


def test_projection_retains_and_labels_provisional_selection() -> None:
    output = _validated_output()
    output["selected_alignments"][2].update(
        {
            "provisional": True,
            "reason": (
                "The preferred route is supported while a governance fact remains unresolved."
            ),
            "uncertainties": ["Whether continuous access can be confirmed."],
        }
    )

    projected = project_planning_output(output)

    selection = projected["selected_alignments"][2]
    assert selection["provisional"] is True
    assert selection["reason"].startswith("The preferred route")
    assert selection["uncertainties"] == ["Whether continuous access can be confirmed."]
    feature = next(
        item
        for item in projected["geojson"]["features"]
        if item["properties"].get("candidate_id") == "candidate-unknown"
    )
    assert feature["properties"]["map_label"] == "Provisional selection — best guess"
    assert feature["properties"]["reason"] == selection["reason"]
    assert feature["properties"]["uncertainties"] == selection["uncertainties"]


@pytest.mark.parametrize(
    "metadata",
    (
        {"provisional": True, "uncertainties": ["A stated uncertainty"]},
        {"provisional": True, "reason": "A reason"},
    ),
)
def test_projection_rejects_incomplete_provisional_selection(metadata) -> None:
    invalid = _validated_output()
    invalid["selected_alignments"][0].update(metadata)

    with pytest.raises(PublicationValidationError, match="provisional selection"):
        project_planning_output(invalid)


def test_publication_is_atomic_and_preserves_previous_pointer_on_invalid_bundle(
    tmp_path: Path,
) -> None:
    valid = _validated_output()
    first = publish_planning_output(
        valid, tmp_path, {"history_ref": "history-1", "branch_id": "branch-main"}
    )
    pointer = tmp_path / "current.json"
    before = pointer.read_bytes()
    first_output = Path(first["publication_dir"]) / "planning-output.json"
    before_output = first_output.read_bytes()

    invalid = copy.deepcopy(valid)
    invalid["status"] = "invalid"
    with pytest.raises(PublicationValidationError):
        publish_planning_output(invalid, tmp_path)

    assert pointer.read_bytes() == before
    assert first_output.read_bytes() == before_output
    assert json.loads(pointer.read_text(encoding="utf-8"))["output_id"] == "planning-case-1"


def test_protected_historical_publication_cannot_be_overwritten(tmp_path: Path) -> None:
    valid = _validated_output()
    publish_planning_output(valid, tmp_path)
    changed = copy.deepcopy(valid)
    changed["output_fingerprint"] = "different-output"
    changed["departures"][0]["reason"] = "changed after publication"

    with pytest.raises(PublicationValidationError, match="protected historical"):
        publish_planning_output(changed, tmp_path)


def test_full_departure_cannot_omit_original_source_geometry() -> None:
    invalid = _validated_output()
    invalid["departures"][0]["affected_geometry_refs"] = []

    with pytest.raises(PublicationValidationError, match="no affected geometry"):
        project_planning_output(invalid)


def _canonical_geometry(geometry_id: str, coordinates: list[list[float]]) -> dict[str, object]:
    geometry = shape({"type": "LineString", "coordinates": coordinates})
    fingerprint = canonical_network_geometry_fingerprint(geometry, "EPSG:4326")
    return {
        "geometry_id": _stable_id("geometry", fingerprint),
        "crs": "EPSG:4326",
        "geometry_kind": "LineString",
        "content_fingerprint": fingerprint,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def _engine_bound_output() -> dict[str, object]:
    output = _validated_output()
    source_ref = _canonical_geometry("source", [[-2.40, 51.30], [-2.30, 51.30]])
    output["source_inventory"][0]["geometry_ref"] = source_ref
    output["departures"][0]["affected_geometry_refs"] = [source_ref]
    return output


def test_projection_rejects_stale_engine_geometry_identity() -> None:
    invalid = _engine_bound_output()
    invalid["source_inventory"][0]["geometry_ref"]["geometry"]["coordinates"][1] = [
        -2.29,
        51.30,
    ]

    with pytest.raises(PublicationValidationError, match="geometry identity"):
        project_planning_output(invalid)


def test_projection_rejects_stale_engine_output_fingerprint() -> None:
    invalid = _validated_output()
    canonical = {key: value for key, value in invalid.items() if key != "output_fingerprint"}
    invalid["output_fingerprint"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    invalid["departures"][0]["reason"] = "changed after validation"

    with pytest.raises(PublicationValidationError, match="output fingerprint"):
        project_planning_output(invalid)


def test_alternate_departure_must_reference_selected_alignment() -> None:
    invalid = _validated_output()
    invalid["selected_alignments"] = []

    with pytest.raises(PublicationValidationError, match="selected alignment"):
        project_planning_output(invalid)


def test_projection_retains_unresolved_obligation_records() -> None:
    output = _validated_output()
    output["obligations"] = [{"obligation_id": "obligation-1", "subject_refs": []}]
    output["obligation_dispositions"] = {"obligation-1": "unresolved"}
    output["unresolved_obligation_refs"] = ["obligation-1"]

    projected = project_planning_output(output)

    assert projected["obligations"] == output["obligations"]
    assert projected["obligation_dispositions"] == output["obligation_dispositions"]
    assert projected["unresolved_obligation_refs"] == ["obligation-1"]
    assert any(
        item["properties"].get("unknown_kind") == "unresolved-obligation"
        for item in projected["geojson"]["features"]
    )


def test_projection_retains_decision_metadata_in_machine_and_map_records(tmp_path: Path) -> None:
    output = _validated_output()
    output["departures"][0].update(
        {
            "decision_ref": "decision-1",
            "decision_class": "classifier",
            "decision_origin": "Jev",
            "history_origin": "recorded-history",
            "provider": "typesafe-provider",
            "model": "Jev",
        }
    )
    output.update({"provider": "typesafe-provider", "model": "Jev"})

    projected = project_planning_output(output)
    assert projected["departures"][0]["decision_class"] == "classifier"
    assert projected["departures"][0]["decision_origin"] == "Jev"
    assert projected["provider"] == "typesafe-provider"
    departure_feature = next(
        item
        for item in projected["geojson"]["features"]
        if item["properties"].get("departure_id") == "departure-full"
    )
    assert departure_feature["properties"]["decision_class"] == "classifier"
    assert departure_feature["properties"]["decision_origin"] == "Jev"
    assert departure_feature["properties"]["history_origin"] == "recorded-history"
    publication = publish_planning_output(output, tmp_path)
    machine_departures = json.loads(
        (Path(publication["publication_dir"]) / "departure-decisions.json").read_text(
            encoding="utf-8"
        )
    )
    assert machine_departures[0]["decision_class"] == "classifier"


def test_existing_publication_requires_complete_manifest_artifacts(tmp_path: Path) -> None:
    valid = _validated_output()
    first = publish_planning_output(valid, tmp_path)
    pointer = (tmp_path / "current.json").read_bytes()
    (Path(first["publication_dir"]) / "planning-network.geojson").unlink()

    with pytest.raises(PublicationValidationError, match="artifact"):
        publish_planning_output(valid, tmp_path)

    assert (tmp_path / "current.json").read_bytes() == pointer
