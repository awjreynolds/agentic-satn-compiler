from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point

from satn.geographic_partitions import (
    BoundaryPortal,
    CandidateFragment,
    CompilationPartition,
    ExecutionBundle,
    HaloRequest,
    PartitionArtifact,
    PartitionFeature,
    PartitionGap,
    PartitionHalo,
    boundary_intersection_portals,
    deterministic_feature_owner,
)


def _cells() -> tuple[CompilationPartition, CompilationPartition]:
    return CompilationPartition("ST75"), CompilationPartition("ST85")


def test_compilation_partition_is_versioned_bng_identity() -> None:
    partition = CompilationPartition("ST75")

    assert partition.partition_scheme == "bng-10km/v1"
    assert partition.crs == "EPSG:27700"
    assert len(partition.fingerprint) == 64
    assert partition.canonical_payload()["cell"] == "ST75"


def test_real_node_portal_is_distinct_from_boundary_intersection() -> None:
    west, east = _cells()
    x = west.bounds[2]
    node = Point(x, west.bounds[1] + 5_000)

    portal = BoundaryPortal.real_node(
        west,
        east,
        node_id="node-1",
        point=node,
        incident_feature_ids=("edge-1",),
        permitted_directions=("edge-1:forward",),
    )

    assert portal.kind == "real-node"
    assert portal.node_id == "node-1"
    assert portal.intersection_coordinate is None
    assert portal.left_cell == "ST75"
    assert portal.right_cell == "ST85"


@pytest.mark.parametrize("field", ["incident_feature_ids", "permitted_directions"])
@pytest.mark.parametrize("bad_value", [" ", 123])
def test_direct_portal_requires_canonical_crossing_identifiers(
    field: str, bad_value: object
) -> None:
    west, east = _cells()
    coordinate = (int(west.bounds[2] * 1000), int((west.bounds[1] + 5_000) * 1000))
    values: dict[str, object] = {
        "incident_feature_ids": ("edge-1",),
        "permitted_directions": ("edge-1:forward",),
    }
    values[field] = (bad_value,)

    with pytest.raises(ValueError, match=field):
        BoundaryPortal(
            west.cell,
            east.cell,
            "real-node",
            node_id="node-1",
            node_coordinate=coordinate,
            **values,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("factory", ["real-node", "boundary-intersection"])
@pytest.mark.parametrize("field", ["incident_feature_ids", "permitted_directions"])
@pytest.mark.parametrize("bad_value", [" ", 123])
def test_portal_factories_require_canonical_crossing_identifiers(
    factory: str, field: str, bad_value: object
) -> None:
    west, east = _cells()
    values: dict[str, object] = {
        "incident_feature_ids": ("edge-1",),
        "permitted_directions": ("edge-1:forward",),
    }
    values[field] = (bad_value,)

    with pytest.raises(ValueError, match=field):
        if factory == "real-node":
            BoundaryPortal.real_node(
                west,
                east,
                node_id="node-1",
                point=Point(west.bounds[2], west.bounds[1] + 5_000),
                **values,  # type: ignore[arg-type]
            )
        else:
            BoundaryPortal.boundary_intersection(
                west,
                east,
                point=Point(west.bounds[2], west.bounds[1] + 2_000),
                **values,  # type: ignore[arg-type]
            )


def test_direct_portal_construction_still_requires_an_exact_shared_boundary() -> None:
    west, east = _cells()
    x = west.bounds[2]

    with pytest.raises(ValueError, match="do not share a boundary"):
        BoundaryPortal(
            "ST75",
            "ST95",
            "boundary-intersection",
            intersection_coordinate=(int(x * 1000), int(west.bounds[1] * 1000)),
        )
    with pytest.raises(ValueError, match="lie exactly on shared cell boundary"):
        BoundaryPortal(
            west.cell,
            east.cell,
            "boundary-intersection",
            intersection_coordinate=(
                int((x - 1) * 1000),
                int((west.bounds[1] + 2_000) * 1000),
            ),
        )


@pytest.mark.parametrize("node_id", [" ", 123])
def test_direct_real_node_portal_requires_canonical_node_id(node_id: object) -> None:
    west, east = _cells()
    x = int(west.bounds[2] * 1000)
    y = int((west.bounds[1] + 5_000) * 1000)

    with pytest.raises(ValueError, match="node_id"):
        BoundaryPortal(
            west.cell,
            east.cell,
            "real-node",
            node_id=node_id,  # type: ignore[arg-type]
            node_coordinate=(x, y),
        )


@pytest.mark.parametrize("feature_id", ["", " ", 123])
def test_direct_boundary_portal_requires_canonical_feature_id(feature_id: object) -> None:
    west, east = _cells()
    x = int(west.bounds[2] * 1000)
    y = int((west.bounds[1] + 2_000) * 1000)

    with pytest.raises(ValueError, match="feature_id"):
        BoundaryPortal(
            west.cell,
            east.cell,
            "boundary-intersection",
            feature_id=feature_id,  # type: ignore[arg-type]
            intersection_coordinate=(x, y),
        )


@pytest.mark.parametrize(
    "coordinate",
    [
        (380000000.5, 152000000),
        ("380000000", 152000000),
        (380000000, True),
    ],
)
def test_direct_boundary_portal_requires_integer_millimetre_coordinates(
    coordinate: object,
) -> None:
    west, east = _cells()

    with pytest.raises(ValueError, match="coordinate"):
        BoundaryPortal(
            west.cell,
            east.cell,
            "boundary-intersection",
            intersection_coordinate=coordinate,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "available,required",
    [("10", 20), (float("nan"), 20), (10, float("inf")), (True, 20)],
)
def test_halo_request_rejects_malformed_radii(available: object, required: object) -> None:
    with pytest.raises(ValueError, match="radius"):
        HaloRequest("ST75", available, required)  # type: ignore[arg-type]


def test_partition_artifact_rejects_untyped_diagnostic_before_fingerprinting() -> None:
    partition = CompilationPartition("ST75")

    with pytest.raises(ValueError, match="diagnostics"):
        PartitionArtifact(
            partition,
            PartitionHalo(partition, 500),
            diagnostics=("oops",),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "field",
    [
        "owned_fragments",
        "halo_references",
        "portals",
        "candidate_fragments",
        "diagnostics",
        "gaps",
    ],
)
@pytest.mark.parametrize("bad_collection", [None, 123])
def test_partition_artifact_rejects_non_iterable_typed_collections(
    field: str, bad_collection: object
) -> None:
    partition = CompilationPartition("ST75")

    with pytest.raises(ValueError, match=field):
        PartitionArtifact(
            partition,
            PartitionHalo(partition, 500),
            **{field: bad_collection},  # type: ignore[arg-type]
        )


def test_crossing_line_emits_canonical_boundary_intersection_portal() -> None:
    west, east = _cells()
    x = west.bounds[2]
    line = LineString([(x - 100, west.bounds[1] + 2_000), (x + 100, west.bounds[1] + 2_000)])
    feature = PartitionFeature("crossing", line)

    portals = boundary_intersection_portals(feature, (west, east))

    assert len(portals) == 1
    portal = portals[0]
    assert portal.kind == "boundary-intersection"
    assert portal.node_id is None
    assert portal.intersection_coordinate == (int(x * 1000), int((west.bounds[1] + 2_000) * 1000))


def test_owner_is_lexicographically_smallest_and_halo_is_reference_only() -> None:
    west, east = _cells()
    x = west.bounds[2]
    feature = PartitionFeature(
        "crossing",
        LineString([(x - 100, west.bounds[1] + 2_000), (x + 100, west.bounds[1] + 2_000)]),
    )
    assert deterministic_feature_owner(feature, (west, east)) == "ST75"

    halo = PartitionHalo(east, radius_m=500)
    west_artifact = PartitionArtifact.from_features(
        west,
        PartitionHalo(west, 500),
        (feature,),
        (west, east),
    )
    east_artifact = PartitionArtifact.from_features(east, halo, (feature,), (west, east))

    assert [item.feature_id for item in west_artifact.owned_fragments] == ["crossing"]
    assert east_artifact.owned_fragments == ()
    assert [item.feature_id for item in east_artifact.halo_references] == ["crossing"]
    assert east_artifact.halo_references[0].owner_cell == "ST75"


def test_halo_retains_read_only_context_that_does_not_touch_the_core() -> None:
    west, east = _cells()
    x = west.bounds[2]
    neighbour_feature = PartitionFeature(
        "east-context",
        LineString(
            [
                (x + 100, west.bounds[1] + 2_000),
                (x + 200, west.bounds[1] + 2_000),
            ]
        ),
    )

    artifact = PartitionArtifact.from_features(
        west,
        PartitionHalo(west, 500),
        (neighbour_feature,),
        (west, east),
    )

    assert artifact.owned_fragments == ()
    assert [item.feature_id for item in artifact.halo_references] == [
        "east-context"
    ]
    assert artifact.halo_references[0].owner_cell == "ST85"


def test_missing_optional_boundary_evidence_and_insufficient_halo_are_explicit_gaps() -> None:
    west, _ = _cells()
    feature = PartitionFeature("inside", Point(west.bounds[0] + 100, west.bounds[1] + 100))
    artifact = PartitionArtifact.from_features(
        west,
        PartitionHalo(west, radius_m=10),
        (feature,),
        (west,),
        gaps=(PartitionGap.missing_optional_boundary_evidence(("ST85",), "boundary-1"),),
        required_halo_radius_m=100,
    )

    kinds = {gap.kind for gap in artifact.gaps}
    assert "missing-optional-boundary-evidence" in kinds
    assert "insufficient-halo" in kinds
    assert artifact.gaps[-1].evidence_request_id


def test_artifact_identity_ignores_input_completion_and_execution_bundle_order() -> None:
    west, east = _cells()
    x = west.bounds[2]
    feature = PartitionFeature(
        "crossing",
        LineString([(x - 100, west.bounds[1] + 2_000), (x + 100, west.bounds[1] + 2_000)]),
    )
    candidate_a = CandidateFragment("candidate-a", ("crossing",), {"role": "access"})
    candidate_b = CandidateFragment("candidate-b", ("crossing",), {"role": "spine"})
    first = PartitionArtifact.from_features(
        west,
        PartitionHalo(west, 500),
        (feature,),
        (west, east),
        candidate_fragments=(candidate_a, candidate_b),
        completion_order=("worker-2", "worker-1"),
        execution_bundle=ExecutionBundle("bath", (west, east), worker_count=1),
    )
    second = PartitionArtifact.from_features(
        west,
        PartitionHalo(west, 500),
        (feature,),
        (east, west),
        candidate_fragments=(candidate_b, candidate_a),
        completion_order=("worker-1", "worker-2"),
        execution_bundle=ExecutionBundle("bristol", (east, west), worker_count=8),
    )

    assert first.fingerprint == second.fingerprint
