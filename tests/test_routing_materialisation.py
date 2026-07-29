from __future__ import annotations

import math
from collections.abc import Mapping

import geopandas as gpd
from test_backbone_assembly import config, parallel_spine_source

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.routing_materialisation import (
    AssemblyRecordKind,
    materialise_compiled_routing_assembly,
)

ROUTING_INPUT_FINGERPRINT = "a" * 64
ROUTING_CONFIGURATION = {
    "contract": "fixture-routing-assembly/v1",
    "tie_breaking": "legacy-backbone-outward",
    "topology_safeguards": ["bidirectional", "acyclic-root-tree"],
}

FRAME_SPECS: dict[AssemblyRecordKind, tuple[str, str]] = {
    "spine-access-connection": (
        "spine_access_connections",
        "access_connection_id",
    ),
    "access-obligation": ("access_obligations", "obligation_id"),
    "spine-access-branch": ("spine_access_branches", "branch_id"),
    "branch-meeting-connection": (
        "branch_meeting_connections",
        "meeting_connection_id",
    ),
    "cross-spine-connector": (
        "cross_spine_connectors",
        "cross_spine_connector_id",
    ),
}


def _materialise(compiled: object, *, elapsed: float = 1.25, rss: int = 4096):
    return materialise_compiled_routing_assembly(
        compiled,
        routing_input_fingerprint=ROUTING_INPUT_FINGERPRINT,
        routing_configuration=ROUTING_CONFIGURATION,
        elapsed_seconds=elapsed,
        peak_rss_bytes=rss,
    )


def _normalise(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if hasattr(value, "item"):
        return _normalise(value.item())
    return value


def _assert_exact_frame_records(
    source: gpd.GeoDataFrame,
    identifier: str,
    materialised_rows: list[dict[str, object]],
) -> None:
    expected = source.sort_values(identifier, kind="stable")
    actual = sorted(materialised_rows, key=lambda row: str(row[identifier]))
    assert len(actual) == len(expected)
    for expected_row, actual_row in zip(expected.to_dict("records"), actual, strict=True):
        assert set(actual_row) == set(expected.columns)
        assert actual_row["geometry"].wkb_hex == expected_row["geometry"].wkb_hex
        assert {
            key: _normalise(value)
            for key, value in actual_row.items()
            if key != "geometry"
        } == {
            key: _normalise(value)
            for key, value in expected_row.items()
            if key != "geometry"
        }


def test_fixture_materialisation_preserves_candidates_assembly_ids_and_geometry() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    materialised = _materialise(compiled)

    expected_candidate_ids = sorted(
        [
            *compiled.spine_access_connections["access_connection_id"],
            *compiled.branch_meeting_connections["meeting_connection_id"],
        ]
    )
    assert sorted(item.candidate_id for item in materialised.candidates) == (
        expected_candidate_ids
    )
    assert materialised.candidate_fingerprint
    assert materialised.assembly_fingerprint
    assert materialised.fingerprint

    for kind, (attribute, identifier) in FRAME_SPECS.items():
        source = getattr(compiled, attribute)
        rows = [
            record.to_row()
            for record in materialised.assembly_records
            if record.record_kind == kind
        ]
        _assert_exact_frame_records(source, identifier, rows)

    assert [item.gap_id for item in materialised.gaps] == sorted(
        compiled.gaps["connection_id"]
    )
    assert materialised.diagnostics.search_count > 0
    assert materialised.diagnostics.settled_node_count >= 0
    assert materialised.diagnostics.edge_relaxation_count >= 0
    assert materialised.diagnostics.peak_frontier_size >= 0
    assert materialised.diagnostics.metadata()["elapsed_seconds"] == 1.25
    assert materialised.diagnostics.metadata()["peak_rss_bytes"] == 4096


def test_fixture_order_and_run_resources_do_not_change_materialisation_identity() -> None:
    first = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    reordered = compile_network(
        config(),
        parallel_spine_source(reverse=True),
        FakeAgentRuntime(),
    )

    first_materialisation = _materialise(first, elapsed=1.0, rss=1024)
    reordered_materialisation = _materialise(reordered, elapsed=99.0, rss=999_999)

    assert reordered_materialisation.fingerprint == first_materialisation.fingerprint
    assert (
        reordered_materialisation.candidate_fingerprint
        == first_materialisation.candidate_fingerprint
    )
    assert (
        reordered_materialisation.assembly_fingerprint
        == first_materialisation.assembly_fingerprint
    )
    assert [item.candidate_id for item in reordered_materialisation.candidates] == [
        item.candidate_id for item in first_materialisation.candidates
    ]
    assert [item.geometry.wkb_hex for item in reordered_materialisation.candidates] == [
        item.geometry.wkb_hex for item in first_materialisation.candidates
    ]
