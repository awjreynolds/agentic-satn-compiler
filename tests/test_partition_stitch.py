from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point

from satn.geographic_partitions import (
    BoundaryPortal,
    CandidateFragment,
    CompilationPartition,
    ExecutionBundle,
    HaloReference,
    PartitionArtifact,
    PartitionFeature,
    PartitionGap,
    PartitionHalo,
)
from satn.partition_stitch import (
    BoundaryObligation,
    MissingRequiredInputError,
    PartitionArtifactInput,
    StitchValidationError,
    deterministic_partition_stitch,
)


def _cells() -> tuple[CompilationPartition, CompilationPartition]:
    return CompilationPartition("ST75"), CompilationPartition("ST85")


def _input(
    artifact: PartitionArtifact,
    *,
    source: str = "a" * 64,
    deps: tuple[str, ...] = (),
) -> PartitionArtifactInput:
    return PartitionArtifactInput(
        artifact,
        source,
        deps,
        provenance={"snapshot": source},
    )


def _crossing_artifacts() -> tuple[PartitionArtifact, PartitionArtifact]:
    west, east = _cells()
    x = west.bounds[2]
    feature = PartitionFeature(
        "crossing",
        LineString([(x - 100, west.bounds[1] + 2_000), (x + 100, west.bounds[1] + 2_000)]),
    )
    return (
        PartitionArtifact.from_features(
            west,
            PartitionHalo(west, 500),
            (feature,),
            (west, east),
            execution_bundle=ExecutionBundle("west", (west,), worker_count=1),
        ),
        PartitionArtifact.from_features(
            east,
            PartitionHalo(east, 500),
            (feature,),
            (west, east),
            execution_bundle=ExecutionBundle("east", (east,), worker_count=2),
        ),
    )


def test_stitch_is_order_and_bundle_invariant() -> None:
    west, east = _crossing_artifacts()
    first = deterministic_partition_stitch((_input(west), _input(east)))
    second = deterministic_partition_stitch(
        (_input(east), _input(west)),
    )

    assert first.fingerprint == second.fingerprint
    assert first.status == "complete"
    assert [item.feature_id for item in first.owned_fragments] == ["crossing"]
    assert first.canonical_payload() == second.canonical_payload()


def test_conflicting_input_envelopes_are_explicit_but_exact_duplicates_are_clean() -> None:
    west, _ = _cells()
    artifact = PartitionArtifact(west, PartitionHalo(west, 50))
    exact = _input(artifact)
    clean = deterministic_partition_stitch((exact, exact))
    assert clean.status == "complete"
    assert not clean.gaps

    conflicting = deterministic_partition_stitch(
        (
            exact,
            _input(artifact, source="b" * 64, deps=("c" * 64,)),
        )
    )
    assert conflicting.status == "complete-with-gaps"
    assert any(gap.kind == "conflicting-partition-input" for gap in conflicting.gaps)
    assert conflicting.evidence_requests


def test_compatible_duplicate_owned_feature_resolves_to_lexical_owner() -> None:
    west, east = _cells()
    x = west.bounds[2]
    crossing = PartitionFeature(
        "inside",
        LineString([(x - 100, west.bounds[1] + 100), (x + 100, west.bounds[1] + 100)]),
    )
    # Each local worker emits the same whole feature as its local owner.  Global
    # ownership rebinding must choose the lexically smallest intersecting cell.
    first = PartitionArtifact.from_features(west, PartitionHalo(west, 50), (crossing,), (west,))
    second = PartitionArtifact.from_features(east, PartitionHalo(east, 50), (crossing,), (east,))

    result = deterministic_partition_stitch((_input(second), _input(first)))

    assert [item.owner_cell for item in result.owned_fragments] == ["ST75"]
    assert not result.gaps


def test_conflicting_duplicate_is_explicit_gap_and_evidence_request() -> None:
    west, east = _cells()
    x = west.bounds[2]
    one = PartitionFeature(
        "same",
        LineString([(x - 100, west.bounds[1] + 100), (x + 100, west.bounds[1] + 100)]),
        {"v": 1},
    )
    two = PartitionFeature(
        "same",
        LineString([(x - 100, west.bounds[1] + 101), (x + 100, west.bounds[1] + 101)]),
        {"v": 2},
    )
    first = PartitionArtifact.from_features(west, PartitionHalo(west, 50), (one,), (west,))
    second = PartitionArtifact.from_features(east, PartitionHalo(east, 50), (two,), (east,))

    result = deterministic_partition_stitch((_input(first), _input(second)))

    assert result.status == "complete-with-gaps"
    conflict = [gap for gap in result.gaps if gap.kind == "conflicting-feature"]
    assert len(conflict) == 1
    assert conflict[0].evidence_request_id
    assert result.evidence_requests


def test_cross_partition_obligation_keeps_global_alternatives() -> None:
    west, east = _crossing_artifacts()
    candidates = (
        CandidateFragment("candidate-z", ("crossing",), {"from": "west", "to": "east"}),
        CandidateFragment("candidate-a", ("crossing",), {"from": "west", "to": "east"}),
    )
    west = PartitionArtifact(
        west.partition,
        west.halo,
        west.owned_fragments,
        west.halo_references,
        west.portals,
        candidate_fragments=(candidates[0],),
    )
    east = PartitionArtifact(
        east.partition,
        east.halo,
        east.owned_fragments,
        east.halo_references,
        east.portals,
        candidate_fragments=(candidates[1],),
    )
    obligation = BoundaryObligation(
        "west-east",
        endpoint_ids=("west", "east"),
        candidate_ids=("candidate-z", "candidate-a"),
        partition_cells=("ST75", "ST85"),
    )

    result = deterministic_partition_stitch(
        (_input(west), _input(east)), obligations=(obligation,)
    )

    assert result.resolutions[0].candidate_ids == ("candidate-a", "candidate-z")
    assert result.resolutions[0].selected_candidate_id == "candidate-a"
    assert result.obligations[0].partition_cells == ("ST75", "ST85")


def test_insufficient_halo_creates_only_targeted_extension_artifact() -> None:
    west, east = _cells()
    west_feature = PartitionFeature("west", Point(west.bounds[0] + 100, west.bounds[1] + 100))
    east_feature = PartitionFeature("east", Point(east.bounds[0] + 100, east.bounds[1] + 100))
    west_artifact = PartitionArtifact.from_features(
        west,
        PartitionHalo(west, 10),
        (west_feature,),
        (west,),
        required_halo_radius_m=100,
    )
    east_artifact = PartitionArtifact.from_features(
        east,
        PartitionHalo(east, 500),
        (east_feature,),
        (east,),
    )

    result = deterministic_partition_stitch((_input(west_artifact), _input(east_artifact)))

    assert [item.partition_cell for item in result.extension_artifacts] == ["ST75"]
    assert result.extension_artifacts[0].required_radius_m == 100


def test_missing_required_partition_fails_before_partial_output() -> None:
    west, _ = _crossing_artifacts()

    with pytest.raises(MissingRequiredInputError, match="ST85"):
        deterministic_partition_stitch(
            (_input(west),), required_partitions=("ST75", "ST85")
        )


def test_dependency_closure_and_directionality_are_validated() -> None:
    west, east = _crossing_artifacts()
    with pytest.raises(StitchValidationError, match="dependency"):
        deterministic_partition_stitch(
            (_input(west, deps=("b" * 64,)), _input(east, deps=())),
            required_dependency_ids=("b" * 64,),
        )

    x = west.partition.bounds[2]
    portal = BoundaryPortal.boundary_intersection(
        west.partition,
        east.partition,
        point=Point(x, west.partition.bounds[1] + 2_000),
        feature_id="crossing",
        permitted_directions=("missing:forward",),
    )
    # The portal is structurally valid, but the direction names an incident
    # feature that is not actually present on the portal.
    portal = BoundaryPortal(
        portal.left_cell,
        portal.right_cell,
        portal.kind,
        intersection_coordinate=portal.intersection_coordinate,
        feature_id=portal.feature_id,
        incident_feature_ids=("crossing",),
        permitted_directions=("missing:forward",),
    )
    malformed = PartitionArtifact(
        west.partition,
        west.halo,
        west.owned_fragments,
        west.halo_references,
        portals=(portal,),
    )
    with pytest.raises(StitchValidationError, match="direction"):
        deterministic_partition_stitch((_input(malformed), _input(east)))


def test_conflicting_duplicate_halo_references_are_explicit() -> None:
    west, east = _cells()
    first = PartitionArtifact(
        west,
        PartitionHalo(west, 50),
        halo_references=(HaloReference("shared", east.cell, "a" * 64, west.cell),),
    )
    second = PartitionArtifact(
        west,
        PartitionHalo(west, 50),
        halo_references=(HaloReference("shared", east.cell, "b" * 64, west.cell),),
    )

    result = deterministic_partition_stitch(
        (_input(first), _input(second), _input(PartitionArtifact(east, PartitionHalo(east, 50))))
    )

    assert any(gap.kind == "conflicting-halo-reference" for gap in result.gaps)
    assert result.evidence_requests


def test_unknown_portal_reference_is_explicit_gap() -> None:
    west, east = _cells()
    x = west.bounds[2]
    portal = BoundaryPortal.boundary_intersection(
        west,
        east,
        point=Point(x, west.bounds[1] + 2_000),
        feature_id="missing-feature",
        permitted_directions=("missing-feature:forward",),
    )
    artifact = PartitionArtifact(west, PartitionHalo(west, 50), portals=(portal,))

    result = deterministic_partition_stitch(
        (
            _input(artifact),
            _input(PartitionArtifact(east, PartitionHalo(east, 50))),
        )
    )

    assert any(gap.kind == "unknown-portal-reference" for gap in result.gaps)
    assert result.status == "complete-with-gaps"


def test_candidate_identifier_cannot_authorise_portal_feature_reference() -> None:
    west, east = _cells()
    x = west.bounds[2]
    portal = BoundaryPortal.boundary_intersection(
        west,
        east,
        point=Point(x, west.bounds[1] + 2_000),
        feature_id="candidate-1",
        permitted_directions=("candidate-1:forward",),
    )
    artifact = PartitionArtifact(
        west,
        PartitionHalo(west, 50),
        portals=(portal,),
        candidate_fragments=(CandidateFragment("candidate-1"),),
    )

    result = deterministic_partition_stitch(
        (
            _input(artifact),
            _input(PartitionArtifact(east, PartitionHalo(east, 50))),
        )
    )

    assert any(gap.kind == "unknown-portal-reference" for gap in result.gaps)
    assert result.evidence_requests


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PartitionArtifactInput(
            PartitionArtifact(
                CompilationPartition("ST75"),
                PartitionHalo(CompilationPartition("ST75"), 1),
            ),
            "a" * 64,
            "candidate",
        ),
        lambda: BoundaryObligation("x", "ab"),
        lambda: BoundaryObligation("x", ("a", "b"), "candidate"),
    ],
)
def test_public_constructors_reject_string_as_iterable(factory: object) -> None:
    with pytest.raises(StitchValidationError):
        factory()  # type: ignore[operator]


def test_multiple_halo_requests_use_maximum_radius_order_independently() -> None:
    west, _ = _cells()
    first = PartitionArtifact(
        west,
        PartitionHalo(west, 10),
        gaps=(PartitionGap.insufficient_halo(west.cell, 10, 100),),
    )
    second = PartitionArtifact(
        west,
        PartitionHalo(west, 10),
        gaps=(PartitionGap.insufficient_halo(west.cell, 10, 300),),
    )

    result = deterministic_partition_stitch((_input(second), _input(first)))

    assert len(result.extension_artifacts) == 1
    assert result.extension_artifacts[0].required_radius_m == 300
