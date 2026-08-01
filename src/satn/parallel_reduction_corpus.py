"""Governed synthetic fixtures for the Parallel-Reduction proving corpus.

This module deliberately owns no route-selection policy.  It validates data-only
manifests, supplies the deterministic scripted runtime used by the corpus, and
reduces a production ``ParallelReductionCompilation`` to a stable expected-result
artifact.  It never creates review maps or publication artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from pathlib import Path

from satn.evidence_contracts import canonical_evidence_json

MANIFEST_CONTRACT = "satn-parallel-reduction-scenario-manifest/v1"
EXPECTED_RESULT_CONTRACT = "satn-parallel-reduction-expected-result/v1"
RUNTIME_CONTRACT = "satn-scripted-corpus-runtime/v1"


@dataclass(frozen=True)
class ParallelReductionCorpusManifest:
    """One data-only synthetic Scenario Manifest with its expected result path."""

    path: Path
    scenario_id: str
    expected_result_path: Path
    request: Mapping[str, object]
    zones: tuple[Mapping[str, object], ...]
    runtime_responses: tuple[Mapping[str, object], ...]


class ScriptedCorpusRuntime:
    """Deterministic corpus-only runtime; it never contacts a model provider."""

    def __init__(self, responses: tuple[Mapping[str, object], ...]) -> None:
        self._responses = {
            str(item.get("target_id", item.get("request_id"))): dict(item) for item in responses
        }
        self.calls: list[dict[str, object]] = []

    def choose(self, request: Mapping[str, object]) -> str | Mapping[str, object]:
        materialized = dict(request)
        self.calls.append(materialized)
        request_id = materialized.get("target_id", materialized.get("request_id"))
        if not isinstance(request_id, str) or request_id not in self._responses:
            raise RuntimeError("scripted-runtime-response-missing")
        response = self._responses[request_id]
        if response["outcome"] == "provider-failure":
            raise RuntimeError(str(response["failure_code"]))
        if response["outcome"] != "select":
            return {"invalid": response["outcome"]}
        return {
            "route_id": response["route_id"],
            "decisive_consideration_ids": tuple(response["decisive_consideration_ids"]),
        }


def load_manifest(path: Path) -> ParallelReductionCorpusManifest:
    """Load and fail closed on a checked-in, data-only scenario manifest."""

    value = _load_json(path, "scenario manifest")
    _require_exact_keys(
        value,
        {
            "contract",
            "scenario_id",
            "expected_result",
            "profile_id",
            "area_id",
            "config",
            "routes",
            "zones",
            "scripted_runtime",
            "junction_node_ids",
            "choice_points",
            "required_transitions",
            "officer_decisions",
        },
        "scenario manifest",
    )
    if value["contract"] != MANIFEST_CONTRACT:
        raise ValueError("unsupported Parallel-Reduction Scenario Manifest contract")
    scenario_id = _required_text(value["scenario_id"], "scenario_id")
    profile_id = _required_text(value["profile_id"], "profile_id")
    area_id = _required_text(value["area_id"], "area_id")
    expected_relative = _required_text(value["expected_result"], "expected_result")
    expected_path = (path.parent / expected_relative).resolve()
    if expected_path.parent != (path.parent / "expected").resolve():
        raise ValueError("expected_result must be a direct artifact in expected/")
    config = _mapping(value["config"], "config")
    _require_exact_keys(
        config,
        {
            "urban_proximity_m",
            "rural_proximity_m",
            "minimum_symmetric_coverage_pct",
            "material_population_difference",
            "material_score_difference",
            "runtime_eligible",
            "maximum_hybrids_per_group",
        },
        "config",
    )
    routes = _mapping_list(value["routes"], "routes")
    if not routes:
        raise ValueError("routes must be nonempty")
    route_ids = tuple(_required_text(item.get("route_id"), "route_id") for item in routes)
    if len(set(route_ids)) != len(route_ids):
        raise ValueError("routes must have unique route_id values")
    zones = _mapping_list(value["zones"], "zones")
    zone_ids = tuple(_required_text(item.get("zone_id"), "zone_id") for item in zones)
    if len(set(zone_ids)) != len(zone_ids):
        raise ValueError("zones must have unique zone_id values")
    _validate_zone_separation(zones, config)
    runtime = _mapping(value["scripted_runtime"], "scripted_runtime")
    _require_exact_keys(runtime, {"contract", "responses"}, "scripted_runtime")
    if runtime["contract"] != RUNTIME_CONTRACT:
        raise ValueError("unsupported Scripted Corpus Runtime contract")
    responses = _mapping_list(runtime["responses"], "scripted_runtime.responses")
    response_ids = tuple(
        _required_text(
            item.get("target_id", item.get("request_id")),
            "scripted runtime target_id",
        )
        for item in responses
    )
    if len(set(response_ids)) != len(response_ids):
        raise ValueError("scripted runtime request_id values must be unique")
    request = {
        "profile_id": profile_id,
        "area_id": area_id,
        "config": dict(config),
        "routes": [dict(item) for item in routes],
        "junction_node_ids": value["junction_node_ids"],
        "choice_points": value["choice_points"],
        "required_transitions": value["required_transitions"],
        "officer_decisions": value["officer_decisions"],
    }
    canonical_evidence_json(request)
    return ParallelReductionCorpusManifest(
        path=path.resolve(),
        scenario_id=scenario_id,
        expected_result_path=expected_path,
        request=request,
        zones=tuple(dict(item) for item in zones),
        runtime_responses=tuple(dict(item) for item in responses),
    )


def load_expected_result(path: Path) -> dict[str, object]:
    value = _load_json(path, "expected result")
    _require_exact_keys(
        value,
        {
            "contract",
            "scenario_id",
            "candidate_sets",
            "selections",
            "decisions",
            "parallel_candidate_relations",
            "network_gaps",
            "material_officer_compiler_divergences",
            "alignment_sections",
            "alignment_options",
            "crossing_warnings",
            "officer_target_unavailable",
        },
        "expected result",
    )
    if value["contract"] != EXPECTED_RESULT_CONTRACT:
        raise ValueError("unsupported Parallel-Reduction Expected Result contract")
    canonical_evidence_json(value)
    return value


def canonical_expected_result(
    manifest: ParallelReductionCorpusManifest,
    compilation: object,
) -> dict[str, object]:
    """Extract only the corpus verification contract from a production result."""

    root = _as_mapping(compilation, "ParallelReductionCompilation")
    scenario = _as_mapping(root.get("scenario"), "Scenario Compilation")
    artifact = _as_mapping(root.get("artifact", {}), "parallel reduction artifact")
    selections = _mapping_list(scenario.get("selections", []), "scenario selections")
    candidate_sets = _mapping_list(scenario.get("candidate_sets", []), "scenario candidate sets")
    decision_record = _as_mapping(scenario.get("decision_record", {}), "decision record")
    return {
        "contract": EXPECTED_RESULT_CONTRACT,
        "scenario_id": manifest.scenario_id,
        "candidate_sets": [
            {
                "candidate_set_id": item.get("candidate_set_id"),
                "connection_id": item.get("connection_id"),
                "candidate_ids": sorted(
                    route.get("candidate_id")
                    for route in _mapping_list(item.get("candidates", []), "candidates")
                ),
                "admitted_candidate_ids": sorted(
                    admission.get("candidate_id")
                    for admission in _mapping_list(item.get("admissions", []), "admissions")
                    if admission.get("disposition") == "admitted"
                ),
            }
            for item in sorted(candidate_sets, key=lambda item: str(item.get("candidate_set_id")))
        ],
        "selections": [
            {
                "candidate_set_id": item.get("candidate_set", {}).get("candidate_set_id"),
                "disposition": item.get("disposition"),
                "selected_candidate_id": item.get("selected_candidate_id"),
                "retained_candidate_ids": sorted(
                    [
                        *item.get("admitted_loser_ids", []),
                        *item.get("complementary_candidate_ids", []),
                    ]
                ),
                "decision_action": item.get("decision_action"),
            }
            for item in sorted(
                selections,
                key=lambda item: str(item.get("candidate_set", {}).get("candidate_set_id")),
            )
        ],
        "decisions": _canonical_records(artifact.get("decisions", []))
        or _canonical_decisions(decision_record),
        "parallel_candidate_relations": _canonical_records(artifact.get("relations", [])),
        "network_gaps": _canonical_records(artifact.get("network_gaps", [])),
        "material_officer_compiler_divergences": _canonical_records(
            artifact.get("officer_compiler_divergences", [])
        ),
        "alignment_sections": _canonical_records(artifact.get("sections", [])),
        "alignment_options": _canonical_records(artifact.get("options", [])),
        "crossing_warnings": _canonical_records(artifact.get("crossing_warnings", [])),
        "officer_target_unavailable": _canonical_records(
            artifact.get("officer_target_unavailable", [])
        ),
    }


def assert_matches_expected(actual: Mapping[str, object], expected: Mapping[str, object]) -> None:
    """Fail on any closed-roster addition, omission, or changed value."""

    actual_json = canonical_evidence_json(actual)
    expected_json = canonical_evidence_json(expected)
    if actual_json != expected_json:
        raise AssertionError("Parallel-Reduction Expected Result differs from compilation")


def write_expected_result(path: Path, result: Mapping[str, object]) -> None:
    """Explicit regeneration write; CI only reads expected-result artifacts."""

    canonical = canonical_evidence_json(result)
    path.write_text(canonical + "\n", encoding="ascii")


def _canonical_decisions(record: Mapping[str, object]) -> list[dict[str, object]]:
    attempts = _mapping_list(record.get("runtime_attempts", []), "runtime_attempts")
    return [
        {
            "mode": record.get("mode"),
            "request_id": item.get("request", {}).get("request_id"),
            "outcome": item.get("outcome"),
            "provider_failure_code": item.get("provider_failure_code"),
        }
        for item in sorted(attempts, key=lambda item: str(item.get("attempt_fingerprint", "")))
    ] or [{"mode": record.get("mode")}]


def _canonical_records(value: object) -> list[dict[str, object]]:
    records = _mapping_list(value, "records")
    volatile = {
        "started_at_ms",
        "completed_at_ms",
        "usage",
        "model",
        "model_identity",
        "generated_prose",
        "path",
    }
    return sorted(
        [
            _normalise_result_value({key: item[key] for key in sorted(item) if key not in volatile})
            for item in records
        ],
        key=canonical_evidence_json,
    )


def _normalise_result_value(value: object) -> object:
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, Mapping):
        return {str(key): _normalise_result_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise_result_value(item) for item in value]
    return value


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}: {path}") from error
    return _mapping(value, label)


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return _mapping(value, label)


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _mapping_list(value: object, label: str) -> list[dict[str, object]]:
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be an array of objects")
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be canonical text")
    return value


def _require_exact_keys(value: Mapping[str, object], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ValueError(f"{label} has an unsupported or missing field")


def _validate_zone_separation(zones: list[dict[str, object]], config: Mapping[str, object]) -> None:
    rural = config.get("rural_proximity_m")
    if not isinstance(rural, int) or rural <= 0:
        raise ValueError("config.rural_proximity_m must be a positive integer")
    origins: list[tuple[int, int]] = []
    for zone in zones:
        origin = zone.get("origin_m")
        if (
            not isinstance(origin, list)
            or len(origin) != 2
            or any(not isinstance(item, int) for item in origin)
        ):
            raise ValueError("zone origin_m must be two integer metric coordinates")
        origins.append((origin[0], origin[1]))
    for index, left in enumerate(origins):
        for right in origins[index + 1 :]:
            if max(abs(left[0] - right[0]), abs(left[1] - right[1])) <= rural:
                raise ValueError("acceptance zones must be separated beyond rural proximity")
