"""Public seam tests for deterministic Strategic Main Network assembly."""

from itertools import pairwise

from satn.strategic_mesh import (
    CandidateRouteSection,
    MeshCoveragePoint,
    MeshGap,
    StrategicMainNetworkProfile,
    StrategicMainNetworkRequest,
    assemble_strategic_main_network,
    derive_urban_mesh_coverage_points,
)


def test_assemble_strategic_main_network_returns_smallest_connected_main_subset_and_gaps() -> None:
    request = StrategicMainNetworkRequest(
        route_sections=(
            CandidateRouteSection(
                section_id="a-road-main",
                start_node_id="n1",
                end_node_id="n2",
                coordinates=((1000.0, 0.0), (2500.0, 0.0)),
                corridor_class="a-road",
            ),
            CandidateRouteSection(
                section_id="cycle-main",
                start_node_id="n0",
                end_node_id="n1",
                coordinates=((0.0, 0.0), (1000.0, 0.0)),
                corridor_class="existing-cycleway",
            ),
            CandidateRouteSection(
                section_id="other-duplicate",
                start_node_id="n1",
                end_node_id="n2",
                coordinates=((1000.0, 100.0), (2500.0, 100.0)),
                corridor_class="other",
            ),
            CandidateRouteSection(
                section_id="support-only",
                start_node_id="n0",
                end_node_id="n1",
                coordinates=((0.0, 300.0), (1000.0, 300.0)),
                network_role="access-support",
            ),
        ),
        coverage_points=(
            MeshCoveragePoint(
                point_id="urban-served",
                coordinates=(500.0, 100.0),
                scope="urban",
            ),
            MeshCoveragePoint(
                point_id="rural-served",
                coordinates=(2200.0, 100.0),
                scope="rural",
            ),
            MeshCoveragePoint(
                point_id="urban-support-gap",
                coordinates=(500.0, 300.0),
                scope="urban",
            ),
        ),
        profile=StrategicMainNetworkProfile(),
    )

    result = assemble_strategic_main_network(request)

    assert result.selected_section_ids == ("a-road-main", "cycle-main")
    assert result.access_support_section_ids == ("support-only",)
    assert result.nonselected_section_ids == ("other-duplicate",)
    assert result.gaps == (
        MeshGap(
            gap_id="coverage:urban-support-gap",
            coverage_point_id="urban-support-gap",
            scope="urban",
            reason="access-support-only",
            candidate_section_ids=("support-only",),
        ),
    )


def test_derived_urban_proof_points_bound_every_source_position_to_half_width() -> None:
    section = CandidateRouteSection(
        section_id="long-a-road",
        start_node_id="n0",
        end_node_id="n1",
        coordinates=((0.0, 0.0), (1000.0, 0.0)),
        corridor_class="a-road",
    )

    points = derive_urban_mesh_coverage_points((section,), maximum_width_m=500.0)
    spacings = tuple(right.coordinates[0] - left.coordinates[0] for left, right in pairwise(points))

    assert max(spacings) <= 125.0
    assert {point.proof_radius_m for point in points} == {187.5}
    assert points[0].proof_radius_m + max(spacings) / 2 <= 250.0
