"""Cross-Spine traversal, lineage and publication integrity.

The public interface deliberately exposes only final Cross-Spine outcomes:
validated connector linework or point-only Route Refinement Findings.  Noding,
deterministic traversal, endpoint closure, generated lineage validation and
Agent Record reconciliation remain implementation details of this module.
"""

from __future__ import annotations

import heapq
import json
import math
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise
from numbers import Number

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely import get_parts
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, shape
from shapely.ops import nearest_points, split, unary_union

from satn.backbone import GAP_COLUMNS
from satn.evidence import continuous_linework
from satn.identifiers import stable_id
from satn.models import AgentRecord, TrafficLight, WithheldDerivedFeatureReference

PUBLIC_ROUTE_TERMINUS_CLOSURE_MAX_M = 100.0
CROSS_SPINE_DIAGNOSTICS_SCHEMA_VERSION = "cross-spine-diagnostics/v2"

CrossSpineProgress = Callable[[int, int, Mapping[str, object]], None]


@dataclass
class _CrossSpineWork:
    """Deterministic accounting for traversal work at this module boundary.

    The counters describe only logical operations.  In particular, they never
    include elapsed time, machine capacity or cache state: those are useful
    operational observations but cannot be reproduced as compiler output.
    """

    connector_traversal_attempts: int = 0
    noded_graphs_built: int = 0
    noded_graph_nodes_total: int = 0
    noded_graph_edges_total: int = 0
    peak_noded_graph_nodes: int = 0
    peak_noded_graph_edges: int = 0
    root_candidate_nodes_examined: int = 0
    eligible_root_endpoint_candidates: int = 0
    endpoint_pairs_considered: int = 0
    weighted_shortest_path_searches: int = 0
    weighted_shortest_path_nodes_settled: int = 0
    weighted_shortest_path_edge_relaxations: int = 0
    peak_shortest_path_frontier: int = 0
    deterministic_path_nodes_selected: int = 0

    def record_graph(self, graph: nx.Graph) -> None:
        """Record the graph exposed to one connector traversal."""
        nodes = graph.number_of_nodes()
        edges = graph.number_of_edges()
        self.noded_graphs_built += 1
        self.noded_graph_nodes_total += nodes
        self.noded_graph_edges_total += edges
        self.peak_noded_graph_nodes = max(self.peak_noded_graph_nodes, nodes)
        self.peak_noded_graph_edges = max(self.peak_noded_graph_edges, edges)

    def as_dict(
        self,
        *,
        candidate_connectors: int,
        authoritative_connectors: int,
        route_refinement_findings: int,
        assembly_diagnostics: Mapping[str, object] | None,
    ) -> dict[str, object]:
        """Return the versioned public diagnostic contract for compiler runs."""
        return {
            "schema_version": CROSS_SPINE_DIAGNOSTICS_SCHEMA_VERSION,
            **_assembly_diagnostics(assembly_diagnostics),
            "candidate_connectors": candidate_connectors,
            "authoritative_connectors": authoritative_connectors,
            "route_refinement_findings": route_refinement_findings,
            "typed_refinement_findings": {
                "route-refinement-required": route_refinement_findings,
            },
            "noded_graphs_built": self.noded_graphs_built,
            "noded_graph_nodes_total": self.noded_graph_nodes_total,
            "noded_graph_edges_total": self.noded_graph_edges_total,
            "peak_noded_graph_nodes": self.peak_noded_graph_nodes,
            "peak_noded_graph_edges": self.peak_noded_graph_edges,
            "root_candidate_nodes_examined": self.root_candidate_nodes_examined,
            "eligible_root_endpoint_candidates": self.eligible_root_endpoint_candidates,
            "endpoint_pairs_considered": self.endpoint_pairs_considered,
            "weighted_shortest_path_searches": self.weighted_shortest_path_searches,
            "weighted_shortest_path_nodes_settled": self.weighted_shortest_path_nodes_settled,
            "weighted_shortest_path_edge_relaxations": self.weighted_shortest_path_edge_relaxations,
            "peak_shortest_path_frontier": self.peak_shortest_path_frontier,
            "deterministic_path_nodes_selected": self.deterministic_path_nodes_selected,
            # This is one noded named-root traversal attempted for each
            # assembled candidate connector.  It is deliberately not labelled
            # as a meeting or agent evaluation, which occurs upstream.
            "connector_traversal_attempts": self.connector_traversal_attempts,
        }


def _assembly_diagnostics(value: Mapping[str, object] | None) -> dict[str, object]:
    """Normalise the upstream assembly seam's deterministic counters.

    Direct traversal callers do not have an upstream backbone assembly.  They
    receive explicit zeros rather than misleading inferences from connector
    linework.  Compiler callers pass the accounting collected at the only seam
    that knows whether a root-pair search or meeting-agent evaluation occurred.
    """
    defaults: dict[str, object] = {
        "root_pairs_considered": 0,
        "root_pair_candidate_searches": 0,
        "meeting_agent_evaluations": 0,
        "meeting_agent_evaluation_initial_outcomes": {
            "accept": 0,
            "reject": 0,
            "gap": 0,
        },
        "meeting_agent_evaluation_final_dispositions": {
            "accept": 0,
            "reject": 0,
            "gap": 0,
            "superseded": 0,
        },
    }
    if value is None:
        return defaults
    return {
        **defaults,
        **deepcopy(dict(value)),
    }


@dataclass(frozen=True)
class CrossSpineAssembly:
    """The final, typed Cross-Spine outcome at the assembly seam.

    ``valid_connectors`` is authoritative linework.  ``route_refinement_findings``
    is deliberately point-only evidence for the mutually exclusive unsafe cases.
    ``agent_records`` is a deep copy reconciled to exactly those two registries;
    neither generated input linework nor its provenance is mutated.
    """

    valid_connectors: gpd.GeoDataFrame
    route_refinement_findings: gpd.GeoDataFrame
    agent_records: tuple[AgentRecord, ...]
    diagnostics: dict[str, object]


class CrossSpineRouteRefinementRequired(ValueError):
    """Expected per-connector traversal failure safe to publish as a finding."""


def resolve_cross_spine_assembly(
    assembled_connectors: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    agent_records: Sequence[AgentRecord] | None = None,
    *,
    progress: CrossSpineProgress | None = None,
    assembly_diagnostics: Mapping[str, object] | None = None,
) -> CrossSpineAssembly:
    """Resolve assembled Cross-Spine connectors into authoritative outcomes.

    The caller supplies only the assembly products.  Missing named roots,
    malformed compiler lineage and malformed generated geometry are producer
    invariant failures and stop compilation.  Expected routing evidence gaps
    produce one point-only Route Refinement Finding per withheld connector.

    Passing ``agent_records`` engages exact authoritative/withheld reconciliation.
    It is mandatory at the compiler seam; ``None`` is reserved for traversal-only
    interface tests and tooling where no assembly audit exists.
    """
    connectors = assembled_connectors.copy()
    candidate_connectors = len(connectors)
    work = _CrossSpineWork()
    copied_records = tuple(record.model_copy(deep=True) for record in agent_records or ())
    _report_progress(
        progress,
        0,
        candidate_connectors,
        work.as_dict(
            candidate_connectors=candidate_connectors,
            authoritative_connectors=0,
            route_refinement_findings=0,
            assembly_diagnostics=assembly_diagnostics,
        ),
    )
    if connectors.empty:
        findings = _finding_frame([], connectors.crs)
        if agent_records is not None:
            _reconcile_agent_records(copied_records, connectors, connectors, findings)
        diagnostics = work.as_dict(
            candidate_connectors=candidate_connectors,
            authoritative_connectors=len(connectors),
            route_refinement_findings=len(findings),
            assembly_diagnostics=assembly_diagnostics,
        )
        _report_progress(progress, 0, candidate_connectors, diagnostics)
        return CrossSpineAssembly(connectors, findings, copied_records, diagnostics)
    roots = _named_strategic_spines(strategic_spines.to_crs(27700))
    _validate_assembled_connectors(connectors)
    crs = connectors.crs
    projected = connectors.to_crs(27700).copy() if not connectors.empty else connectors.copy()
    valid_rows: list[gpd.GeoDataFrame] = []
    finding_rows: list[dict[str, object]] = []
    for _row_index, connector in projected.sort_values("cross_spine_connector_id").iterrows():
        work.connector_traversal_attempts += 1
        try:
            valid_rows.append(_resolve_connector(connector, roots, work).to_frame().T)
        except CrossSpineRouteRefinementRequired as error:
            finding_rows.append(_route_refinement_finding(connector, str(error)))
        _report_progress(
            progress,
            work.connector_traversal_attempts,
            candidate_connectors,
            work.as_dict(
                candidate_connectors=candidate_connectors,
                authoritative_connectors=0,
                route_refinement_findings=0,
                assembly_diagnostics=assembly_diagnostics,
            ),
        )
    valid_connectors = _connector_frame(valid_rows, connectors)
    if not valid_connectors.empty:
        valid_connectors = valid_connectors.set_crs(27700, allow_override=True).to_crs(crs)
    findings = _finding_frame(finding_rows, crs)
    if agent_records is not None:
        _reconcile_agent_records(
            copied_records,
            connectors,
            valid_connectors,
            findings,
        )
    diagnostics = work.as_dict(
        candidate_connectors=candidate_connectors,
        authoritative_connectors=len(valid_connectors),
        route_refinement_findings=len(findings),
        assembly_diagnostics=assembly_diagnostics,
    )
    _report_progress(progress, candidate_connectors, candidate_connectors, diagnostics)
    return CrossSpineAssembly(valid_connectors, findings, copied_records, diagnostics)


def _report_progress(
    progress: CrossSpineProgress | None,
    assessed: int,
    total: int,
    diagnostics: Mapping[str, object],
) -> None:
    """Expose only deterministic progress state to an optional operational observer."""
    if progress is None:
        return
    # Observers are operational integrations outside the compiler contract.
    # Give each event a deep snapshot so accidental or malicious mutation cannot
    # change later events, returned diagnostics, or the published run record.
    progress(assessed, total, deepcopy(dict(diagnostics)))


def validate_cross_spine_publication(
    agent_records: Sequence[AgentRecord],
    public_features: Sequence[object],
    authoritative_registry: Mapping[str, str],
    review_features: Sequence[object] | None = None,
) -> None:
    """Prove published Cross-Spine findings exactly match reconciled records."""
    public_findings = _published_finding_features(public_features, "public GeoJSON")
    reviewed_findings = (
        _published_finding_features(review_features, "review-map GeoJSON")
        if review_features is not None
        else None
    )
    nonaccepted = [
        reference
        for record in agent_records
        if record.decision != "accept"
        for reference in [*record.derived_features, *record.withheld_derived_features]
        if reference.network_role == "cross-spine-connector"
    ]
    if nonaccepted:
        raise ValueError(
            "non-accepted AgentRecord cannot establish or withhold cross-spine "
            f"connector derived feature: {nonaccepted[0].feature_id}"
        )
    references = [
        reference
        for record in agent_records
        if record.decision == "accept"
        for reference in record.withheld_derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    _validate_publication_references(
        references,
        public_features,
        public_findings,
        authoritative_registry,
        "public GeoJSON",
    )
    if reviewed_findings is not None:
        _validate_publication_references(
            references,
            review_features or (),
            reviewed_findings,
            authoritative_registry,
            "review-map GeoJSON",
        )
        for reference in references:
            public = _exact_published_finding(
                public_features, reference.finding_id, "public GeoJSON"
            )
            review = _exact_published_finding(
                review_features or (), reference.finding_id, "review-map GeoJSON"
            )
            if public["geometry"] != review["geometry"]:
                raise ValueError(
                    "review-map Route Refinement Finding geometry differs from public "
                    f"GeoJSON: {reference.finding_id}"
                )


def _validate_assembled_connectors(connectors: gpd.GeoDataFrame) -> None:
    for _, connector in connectors.sort_values("cross_spine_connector_id").iterrows():
        connector_id = str(connector["cross_spine_connector_id"])
        _validate_linework(connector.geometry, f"cross-spine connector {connector_id}")
        provenance = _connector_provenance(connector, connector_id)
        _source_ids(connector, connector_id, provenance)


def _resolve_connector(
    connector: pd.Series,
    roots: dict[str, object],
    work: _CrossSpineWork,
) -> pd.Series:
    connector_id = str(connector["cross_spine_connector_id"])
    provenance = _connector_provenance(connector, connector_id)
    _source_ids(connector, connector_id, provenance)
    from_root_id, from_root = _named_root(connector, connector_id, "from_root_spine_id", roots)
    to_root_id, to_root = _named_root(connector, connector_id, "to_root_spine_id", roots)
    graph = _noded_graph(connector.geometry, connector_id, (from_root, to_root))
    work.record_graph(graph)
    from_node, to_node, path = _named_root_path(
        graph, connector_id, from_root_id, from_root, to_root_id, to_root, work
    )
    from_point, to_point = Point(from_node), Point(to_node)
    _, from_target = nearest_points(from_point, from_root)
    _, to_target = nearest_points(to_point, to_root)
    from_distance, to_distance = (
        float(from_point.distance(from_target)),
        float(to_point.distance(to_target)),
    )
    route = _path_geometry(graph, path, from_target, to_target)
    closures = [
        {"target_id": root_id, "distance_m": round(distance, 3)}
        for root_id, distance in ((from_root_id, from_distance), (to_root_id, to_distance))
        if distance > 0.01
    ]
    if closures:
        provenance["terminus_closures"] = closures
    else:
        provenance.pop("terminus_closures", None)
    provenance["named_root_traversal"] = {
        "from_root_spine_id": from_root_id,
        "to_root_spine_id": to_root_id,
        "noded_segment_count": graph.number_of_edges(),
        "selected_segment_count": len(path) - 1,
        "pruned_segment_count": graph.number_of_edges() - (len(path) - 1),
        "from_root_distance_m": round(from_distance, 3),
        "to_root_distance_m": round(to_distance, 3),
    }
    resolved = connector.copy()
    resolved["geometry"] = route
    resolved["distance_km"] = round(route.length / 1000.0, 3)
    resolved["provenance"] = json.dumps(provenance, sort_keys=True)
    resolved["geometry_semantics"] = (
        f"{connector['geometry_semantics']}; noded named-root traversal between "
        "the connector's recorded Strategic Spines, with unrelated dangling "
        "linework pruned and bounded source-alignment closures only at named "
        "Strategic Spines"
    )
    return resolved


def _connector_frame(rows: list[gpd.GeoDataFrame], source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if not rows:
        return source.iloc[0:0].copy()
    return gpd.GeoDataFrame(
        pd.concat(rows, ignore_index=True, sort=False), geometry="geometry", crs=27700
    ).sort_values("cross_spine_connector_id")


def _finding_frame(rows: list[dict[str, object]], crs: object) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame(columns=GAP_COLUMNS, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(rows, columns=GAP_COLUMNS, geometry="geometry", crs=crs).sort_values(
        "connection_id"
    )


def _route_refinement_finding(connector: pd.Series, error: str) -> dict[str, object]:
    connector_id = str(connector["cross_spine_connector_id"])
    provenance = _connector_provenance(connector, connector_id)
    source_ids = _source_ids(connector, connector_id, provenance)
    rationale = (
        f"{error}. Aggregate connector omitted; Route Refinement Finding requires "
        "a verified traversable alignment to the recorded named Strategic Spines."
    )
    return {
        "connection_id": stable_id("cross-spine-connector-gap", connector_id),
        "network_role": "cross-spine-connector-gap",
        "from_place": _text(connector.get("from_root_spine_id")),
        "to_place": _text(connector.get("to_root_spine_id")),
        "from_place_name": _text(connector.get("from_root_spine_name")),
        "to_place_name": _text(connector.get("to_root_spine_name")),
        "distance_km": connector.get("distance_km"),
        "classification": "network-gap",
        "intervention_archetype": "cross-spine route refinement",
        "geometry_semantics": (
            "point-only termini of an unsafe cross-spine connector; aggregate "
            "connector linework is withheld pending route refinement"
        ),
        "status": "gap",
        "selection_reason": rationale,
        "agent_outcome": "route-refinement-required",
        "agent_attempt_count": 0,
        "agent_findings": json.dumps(
            [
                {
                    "code": "cross-spine-named-root-traversal-invalid",
                    "severity": "blocking",
                    "message": rationale,
                    "evidence_ids": source_ids,
                }
            ],
            sort_keys=True,
        ),
        "agent_decision_request_id": None,
        "agent_decision_choice_id": None,
        "agent_decision_action": None,
        "agent_decision_responder_mode": None,
        "school_id": None,
        "school_kind": None,
        "access_point_status": None,
        "access_point_source_id": None,
        "access_point_rationale": None,
        "source_ids": json.dumps(source_ids),
        "cache_status": "not-cacheable",
        "alignment_options": "[]",
        "criterion_endpoints": TrafficLight.RED.value,
        "criterion_continuity": TrafficLight.RED.value,
        "criterion_bidirectional": TrafficLight.GREY.value,
        "criterion_distance": TrafficLight.RED.value,
        "topography_alternative_trigger": False,
        "topography_comparison_status": "not-evaluated",
        "topography_comparison_rationale": (
            "No aggregate connector is published, so topography comparison cannot "
            "be assessed until route refinement supplies a traversable alignment."
        ),
        "topography_original_role": "cross-spine-connector",
        "topography_selected_role": None,
        "geometry": _finding_geometry(connector.geometry),
    }


def _source_ids(
    connector: pd.Series, connector_id: str, provenance: Mapping[str, object]
) -> list[str]:
    raw = connector.get("source_ids")
    if raw is None or (isinstance(raw, Number) and pd.isna(raw)):
        source_ids: list[object] = []
    elif isinstance(raw, list):
        source_ids = raw
    else:
        try:
            decoded = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"cross-spine connector {connector_id} has invalid source_ids lineage"
            ) from error
        if not isinstance(decoded, list):
            raise ValueError(
                f"cross-spine connector {connector_id} source_ids lineage is not a list"
            )
        source_ids = decoded
    provenance_source_ids = provenance.get("source_ids", [])
    if not isinstance(provenance_source_ids, list):
        raise ValueError(
            f"cross-spine connector {connector_id} provenance source_ids lineage is not a list"
        )
    values = [*source_ids, *provenance_source_ids]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(
            f"cross-spine connector {connector_id} source_ids lineage contains "
            "an invalid identifier"
        )
    return sorted(set(values))


def _finding_geometry(geometry: object) -> MultiPoint:
    try:
        linework = continuous_linework(geometry)
    except (AttributeError, TypeError):
        linework = []
    points = {tuple(line.coords[0]) for line in linework if len(line.coords) >= 2} | {
        tuple(line.coords[-1]) for line in linework if len(line.coords) >= 2
    }
    if points:
        return MultiPoint(sorted(points))
    if geometry is not None and not getattr(geometry, "is_empty", True):
        return MultiPoint([geometry.representative_point()])
    return MultiPoint()


def _reconcile_agent_records(
    records: Sequence[AgentRecord],
    assembled: gpd.GeoDataFrame,
    published: gpd.GeoDataFrame,
    findings: gpd.GeoDataFrame,
) -> None:
    assembled_ids = _unique_connector_ids(assembled, "assembled")
    published_ids = _unique_connector_ids(published, "published")
    if not published_ids <= assembled_ids:
        unexpected = sorted(published_ids - assembled_ids)[0]
        raise ValueError(
            f"published cross-spine connector is absent from the assembled registry: {unexpected}"
        )
    withheld_ids = assembled_ids - published_ids
    finding_ids = _finding_ids(findings, withheld_ids)
    accepted = _validated_accepted_records(records, assembled_ids)
    for record in accepted:
        retained = []
        for reference in record.derived_features:
            if (
                reference.network_role != "cross-spine-connector"
                or reference.feature_id not in withheld_ids
            ):
                retained.append(reference)
                continue
            record.withheld_derived_features.append(
                WithheldDerivedFeatureReference(
                    feature_id=reference.feature_id,
                    network_role=reference.network_role,
                    reason=(
                        "Aggregate connector withheld after named-root traversal failed; "
                        "the associated point-only Route Refinement Finding is published instead."
                    ),
                    finding_id=finding_ids[reference.feature_id],
                )
            )
        record.derived_features = retained
    _validate_reconciled_records(records, published_ids, withheld_ids, finding_ids)


def _unique_connector_ids(connectors: gpd.GeoDataFrame, name: str) -> set[str]:
    if connectors.empty:
        return set()
    if "cross_spine_connector_id" not in connectors:
        raise ValueError(f"{name} cross-spine connector registry has no identifier column")
    values = [_text(value) for value in connectors["cross_spine_connector_id"]]
    if any(value is None for value in values):
        raise ValueError(f"{name} cross-spine connector registry has a blank identifier")
    identifiers = [str(value) for value in values]
    duplicates = sorted(value for value in set(identifiers) if identifiers.count(value) != 1)
    if duplicates:
        raise ValueError(
            f"{name} cross-spine connector registry has duplicate identifier: {duplicates[0]}"
        )
    return set(identifiers)


def _finding_ids(findings: gpd.GeoDataFrame, withheld_ids: set[str]) -> dict[str, str]:
    if findings.empty:
        if withheld_ids:
            raise ValueError("withheld cross-spine connector has no Route Refinement Finding")
        return {}
    if "connection_id" not in findings or "network_role" not in findings:
        raise ValueError("cross-spine connector gaps omit required identifiers or roles")
    ids = [_text(value) for value in findings["connection_id"]]
    if any(value is None for value in ids):
        raise ValueError("cross-spine connector gaps contain a blank finding identifier")
    normalized = [str(value) for value in ids]
    duplicates = sorted(value for value in set(normalized) if normalized.count(value) != 1)
    if duplicates:
        raise ValueError(
            f"cross-spine connector gaps duplicate finding identifier: {duplicates[0]}"
        )
    if set(findings["network_role"].astype(str)) != {"cross-spine-connector-gap"}:
        raise ValueError("connector closure emitted a non-cross-spine Route Refinement Finding")
    expected = {
        connector_id: stable_id("cross-spine-connector-gap", connector_id)
        for connector_id in withheld_ids
    }
    if set(normalized) != set(expected.values()):
        raise ValueError(
            "withheld connector and Route Refinement Finding identifiers are not bijective"
        )
    return expected


def _validated_accepted_records(
    records: Sequence[AgentRecord], assembled_ids: set[str]
) -> list[AgentRecord]:
    accepted = [record for record in records if record.decision == "accept"]
    for record in records:
        references = [
            *(
                reference
                for reference in record.derived_features
                if reference.network_role == "cross-spine-connector"
            ),
            *(
                reference
                for reference in record.withheld_derived_features
                if reference.network_role == "cross-spine-connector"
            ),
        ]
        if record.decision != "accept" and references:
            raise ValueError(
                "non-accepted AgentRecord cannot establish or withhold cross-spine "
                f"connector derived feature: {references[0].feature_id}"
            )
    references = [
        reference
        for record in accepted
        for reference in record.derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    ids = [reference.feature_id for reference in references]
    duplicates = sorted(value for value in set(ids) if ids.count(value) != 1)
    if duplicates:
        raise ValueError(
            "cross-spine connector derived feature registry has duplicate identifier: "
            f"{duplicates[0]}"
        )
    if set(ids) != assembled_ids:
        raise ValueError(
            "cross-spine connector derived feature registry differs from the assembled registry"
        )
    return accepted


def _validate_reconciled_records(
    records: Sequence[AgentRecord],
    published_ids: set[str],
    withheld_ids: set[str],
    finding_ids: Mapping[str, str],
) -> None:
    retained = [
        reference
        for record in records
        if record.decision == "accept"
        for reference in record.derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    retained_ids = [reference.feature_id for reference in retained]
    if set(retained_ids) != published_ids or len(retained_ids) != len(published_ids):
        raise ValueError("published cross-spine connector registry is not exact after withholding")
    withheld = [
        reference
        for record in records
        if record.decision == "accept"
        for reference in record.withheld_derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    ids = [reference.feature_id for reference in withheld]
    duplicates = sorted(value for value in set(ids) if ids.count(value) != 1)
    if duplicates:
        raise ValueError(
            f"withheld cross-spine connector registry has duplicate identifier: {duplicates[0]}"
        )
    if set(ids) != withheld_ids:
        raise ValueError("withheld cross-spine connector registry differs from omitted connectors")
    reported_findings = [reference.finding_id for reference in withheld]
    duplicates = sorted(
        value for value in set(reported_findings) if reported_findings.count(value) != 1
    )
    if duplicates:
        raise ValueError(
            "withheld cross-spine connector registry reuses Route Refinement Finding: "
            f"{duplicates[0]}"
        )
    for reference in withheld:
        if reference.finding_id != finding_ids.get(reference.feature_id):
            raise ValueError(
                "withheld cross-spine connector references the wrong Route Refinement Finding: "
                f"{reference.feature_id}"
            )


def _named_strategic_spines(strategic_spines: gpd.GeoDataFrame) -> dict[str, object]:
    roots: dict[str, object] = {}
    for _, spine in strategic_spines.sort_values("spine_id").iterrows():
        spine_id = str(spine["spine_id"])
        _validate_linework(spine.geometry, f"Strategic Spine {spine_id!r}")
        if spine_id in roots:
            raise ValueError(f"Strategic Spine {spine_id!r} is not uniquely identified")
        roots[spine_id] = spine.geometry
    return roots


def _validate_linework(geometry: object, description: str) -> None:
    if (
        not isinstance(geometry, (LineString, MultiLineString))
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise ValueError(
            f"{description} has invalid geometry; expected a non-empty valid LineString or "
            "MultiLineString"
        )


def _named_root(
    connector: pd.Series,
    connector_id: str,
    column: str,
    roots: Mapping[str, object],
) -> tuple[str, object]:
    value = connector.get(column)
    if value is None or pd.isna(value) or not str(value).strip():
        raise ValueError(f"cross-spine connector {connector_id} has no {column}")
    root_id = str(value)
    if root_id not in roots:
        raise ValueError(
            f"cross-spine connector {connector_id} names missing Strategic Spine {root_id}"
        )
    return root_id, roots[root_id]


def _noded_graph(
    geometry: object, connector_id: str, named_roots: tuple[object, object]
) -> nx.Graph:
    linework = [line for line in continuous_linework(geometry) if line.length > 0.01]
    if not linework:
        raise CrossSpineRouteRefinementRequired(
            f"cross-spine connector {connector_id} has no routed linework"
        )
    graph = nx.Graph()
    segments = sorted(
        (
            segment
            for segment in get_parts(unary_union(linework))
            if isinstance(segment, LineString) and segment.length > 0.01
        ),
        key=_canonical_segment,
    )
    for raw_segment in segments:
        for segment in _split_at_named_roots(raw_segment, named_roots):
            _add_segment(graph, segment)
    if graph.number_of_edges() == 0:
        raise CrossSpineRouteRefinementRequired(
            f"cross-spine connector {connector_id} has no usable routed segments"
        )
    return graph


def _split_at_named_roots(segment: LineString, roots: tuple[object, object]) -> list[LineString]:
    points = [
        point
        for root in roots
        for point in _point_parts(segment.intersection(root))
        if point.distance(Point(segment.coords[0])) > 0.01
        and point.distance(Point(segment.coords[-1])) > 0.01
    ]
    if not points:
        return [segment]
    return [
        part
        for part in get_parts(
            split(segment, MultiPoint(sorted(points, key=lambda point: point.wkb_hex)))
        )
        if isinstance(part, LineString) and part.length > 0.01
    ]


def _point_parts(geometry: object) -> list[Point]:
    if getattr(geometry, "is_empty", False):
        return []
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, LineString):
        return [Point(geometry.coords[0]), Point(geometry.coords[-1])]
    if hasattr(geometry, "geoms"):
        return [point for part in geometry.geoms for point in _point_parts(part)]
    return []


def _add_segment(graph: nx.Graph, segment: LineString) -> None:
    signature = _canonical_segment(segment)
    start, end = signature[0], signature[-1]
    if start == end:
        return
    existing = graph.get_edge_data(start, end)
    if existing is None or (segment.length, signature) < (
        existing["weight"],
        existing["signature"],
    ):
        graph.add_edge(
            start, end, geometry=LineString(signature), signature=signature, weight=segment.length
        )


def _canonical_segment(segment: LineString) -> tuple[tuple[float, ...], ...]:
    coordinates = tuple(tuple(coordinate) for coordinate in segment.coords)
    return min(coordinates, tuple(reversed(coordinates)))


def _named_root_path(
    graph: nx.Graph,
    connector_id: str,
    from_root_id: str,
    from_root: object,
    to_root_id: str,
    to_root: object,
    work: _CrossSpineWork,
) -> tuple[tuple[float, float], tuple[float, float], list[tuple[float, float]]]:
    try:
        from_candidates, from_exact = _root_candidates(graph, from_root, work)
    except CrossSpineRouteRefinementRequired as error:
        raise CrossSpineRouteRefinementRequired(
            f"cross-spine connector {connector_id} named Strategic Spine {from_root_id}: {error}"
        ) from error
    try:
        to_candidates, to_exact = _root_candidates(graph, to_root, work)
    except CrossSpineRouteRefinementRequired as error:
        raise CrossSpineRouteRefinementRequired(
            f"cross-spine connector {connector_id} named Strategic Spine {to_root_id}: {error}"
        ) from error
    from_candidates = [
        (distance, node)
        for distance, node in from_candidates
        if distance <= 0.01 or graph.degree[node] == 1
    ]
    to_candidates = [
        (distance, node)
        for distance, node in to_candidates
        if distance <= 0.01 or graph.degree[node] == 1
    ]
    work.eligible_root_endpoint_candidates += len(from_candidates) + len(to_candidates)
    selected: tuple[float, tuple[float, float], tuple[float, float]] | None = None
    distances_by_start: dict[tuple[float, float], dict[tuple[float, float], float]] = {}
    for from_distance, from_node in from_candidates:
        work.weighted_shortest_path_searches += 1
        distances = _weighted_distances(graph, from_node, work)
        distances_by_start[from_node] = distances
        for to_distance, to_node in to_candidates:
            work.endpoint_pairs_considered += 1
            if from_node == to_node or to_node not in distances:
                continue
            candidate = (from_distance + distances[to_node] + to_distance, from_node, to_node)
            if selected is None or candidate < selected:
                selected = candidate
    if selected is None:
        if from_exact and to_exact:
            raise CrossSpineRouteRefinementRequired(
                f"cross-spine connector {connector_id} has disconnected exact named-root "
                f"intersections between Strategic Spines {from_root_id} and {to_root_id}"
            )
        raise CrossSpineRouteRefinementRequired(
            f"cross-spine connector {connector_id} has no connected endpoint traversal "
            f"between named Strategic Spines {from_root_id} and {to_root_id}"
        )
    _, from_node, to_node = selected
    return (
        from_node,
        to_node,
        _deterministic_path(graph, from_node, to_node, distances_by_start[from_node], work),
    )


def _root_candidates(
    graph: nx.Graph,
    root: object,
    work: _CrossSpineWork,
) -> tuple[list[tuple[float, tuple[float, float]]], bool]:
    work.root_candidate_nodes_examined += graph.number_of_nodes()
    candidates = sorted((float(Point(node).distance(root)), node) for node in graph.nodes)
    exact = [candidate for candidate in candidates if candidate[0] <= 0.01]
    if exact:
        return exact, True
    bounded = [
        candidate for candidate in candidates if candidate[0] <= PUBLIC_ROUTE_TERMINUS_CLOSURE_MAX_M
    ]
    if bounded:
        return bounded, False
    nearest = candidates[0][0]
    raise CrossSpineRouteRefinementRequired(
        "named Strategic Spine is beyond bounded source-alignment closure: "
        f"{nearest:.1f} m exceeds {PUBLIC_ROUTE_TERMINUS_CLOSURE_MAX_M:.1f} m"
    )


def _deterministic_path(
    graph: nx.Graph,
    start: tuple[float, float],
    end: tuple[float, float],
    distances: Mapping[tuple[float, float], float],
    work: _CrossSpineWork,
) -> list[tuple[float, float]]:
    _validate_weights(graph)
    if end not in distances:
        raise nx.NetworkXNoPath(f"No path between {start!r} and {end!r}")
    viable, pending = {end}, [end]
    while pending:
        node = pending.pop()
        for neighbour in graph.neighbors(node):
            neighbour_distance = distances.get(neighbour)
            if neighbour_distance is None:
                continue
            if (
                neighbour_distance + float(graph.edges[neighbour, node]["weight"])
                != distances[node]
            ):
                continue
            if neighbour not in viable:
                viable.add(neighbour)
                pending.append(neighbour)
    path, node = [start], start
    while node != end:
        next_nodes = [
            neighbour
            for neighbour in graph.neighbors(node)
            if neighbour in viable
            and distances[node] + float(graph.edges[node, neighbour]["weight"])
            == distances.get(neighbour)
        ]
        if not next_nodes:
            raise nx.NetworkXNoPath(f"No path between {start!r} and {end!r}")
        node = min(next_nodes)
        path.append(node)
    work.deterministic_path_nodes_selected += len(path)
    return path


def _validate_weights(graph: nx.Graph) -> None:
    for source, destination, attributes in graph.edges(data=True):
        raw = attributes.get("weight")
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            weight = float("nan")
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(
                f"connector graph edge {source!r} -> {destination!r} has invalid weight {raw!r}; "
                "expected a finite, strictly positive number"
            )


def _weighted_distances(
    graph: nx.Graph,
    start: tuple[float, float],
    work: _CrossSpineWork,
) -> dict[tuple[float, float], float]:
    _validate_weights(graph)
    queue: list[tuple[float, tuple[float, float]]] = [(0.0, start)]
    distances = {start: 0.0}
    while queue:
        work.peak_shortest_path_frontier = max(work.peak_shortest_path_frontier, len(queue))
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        work.weighted_shortest_path_nodes_settled += 1
        for neighbour in sorted(graph.neighbors(node)):
            work.weighted_shortest_path_edge_relaxations += 1
            candidate = distance + float(graph.edges[node, neighbour]["weight"])
            if candidate >= distances.get(neighbour, float("inf")):
                continue
            distances[neighbour] = candidate
            heapq.heappush(queue, (candidate, neighbour))
    return distances


def _path_geometry(
    graph: nx.Graph,
    path: list[tuple[float, float]],
    from_target: Point,
    to_target: Point,
) -> LineString:
    coordinates: list[tuple[float, float]] = [tuple(from_target.coords[0])]
    for index, (start, end) in enumerate(pairwise(path)):
        segment_coordinates = list(graph.edges[start, end]["geometry"].coords)
        if tuple(segment_coordinates[0]) != start:
            segment_coordinates.reverse()
        if index == 0 and tuple(segment_coordinates[0]) != coordinates[-1]:
            coordinates.append(tuple(segment_coordinates[0]))
        coordinates.extend(tuple(coordinate) for coordinate in segment_coordinates[1:])
    if tuple(to_target.coords[0]) != coordinates[-1]:
        coordinates.append(tuple(to_target.coords[0]))
    return LineString(coordinates)


def _connector_provenance(connector: pd.Series, connector_id: str) -> dict[str, object]:
    try:
        provenance = json.loads(str(connector["provenance"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"cross-spine connector {connector_id} has invalid provenance") from error
    if not isinstance(provenance, dict):
        raise ValueError(f"cross-spine connector {connector_id} has non-object provenance")
    return provenance


def _text(value: object) -> str | None:
    if value is None or (isinstance(value, Number) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _published_finding_features(
    features: Sequence[object], artifact_name: str
) -> dict[str, list[dict[str, object]]]:
    findings: dict[str, list[dict[str, object]]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict) or (
            properties.get("feature_type"),
            properties.get("network_role"),
        ) != ("gap", "cross-spine-connector-gap"):
            continue
        findings.setdefault(str(feature.get("id")), []).append(feature)
    return findings


def _validate_publication_references(
    references: Sequence[WithheldDerivedFeatureReference],
    features: Sequence[object],
    findings: Mapping[str, list[dict[str, object]]],
    authoritative_registry: Mapping[str, str],
    artifact_name: str,
) -> None:
    reference_ids = [reference.feature_id for reference in references]
    finding_ids = [reference.finding_id for reference in references]
    duplicates = sorted(value for value in set(reference_ids) if reference_ids.count(value) != 1)
    if duplicates:
        raise ValueError(
            "withheld cross-spine connector audit has duplicate connector reference: "
            f"{duplicates[0]}"
        )
    duplicates = sorted(value for value in set(finding_ids) if finding_ids.count(value) != 1)
    if duplicates:
        raise ValueError(
            "withheld cross-spine connector audit has conflicting finding reference: "
            f"{duplicates[0]}"
        )
    for reference in references:
        expected = stable_id("cross-spine-connector-gap", reference.feature_id)
        if reference.finding_id != expected:
            raise ValueError(
                "withheld cross-spine connector audit names the wrong finding: "
                f"{reference.feature_id}"
            )
        feature = _exact_published_finding(features, reference.finding_id, artifact_name)
        _validate_published_geometry(feature, reference.finding_id, artifact_name)
        if reference.feature_id in authoritative_registry:
            raise ValueError(
                "withheld cross-spine connector is still present in the authoritative registry: "
                f"{reference.feature_id}"
            )
    if set(findings) != set(finding_ids):
        raise ValueError(
            f"{artifact_name} cross-spine Route Refinement Findings differ from withheld "
            "connector audits"
        )


def _exact_published_finding(
    features: Sequence[object], finding_id: str, artifact_name: str
) -> dict[str, object]:
    matching = [
        feature
        for feature in features
        if isinstance(feature, dict) and str(feature.get("id")) == finding_id
    ]
    if len(matching) != 1:
        raise ValueError(
            "withheld cross-spine connector audit must reference exactly one "
            f"{artifact_name} Route Refinement Finding: {finding_id}"
        )
    feature = matching[0]
    properties = feature.get("properties")
    if not isinstance(properties, dict) or (
        properties.get("feature_type"),
        properties.get("network_role"),
    ) != ("gap", "cross-spine-connector-gap"):
        raise ValueError(
            f"{artifact_name} Route Refinement Finding has the wrong feature type or role: "
            f"{finding_id}"
        )
    return feature


def _validate_published_geometry(
    feature: Mapping[str, object], finding_id: str, artifact_name: str
) -> None:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError(
            f"{artifact_name} Route Refinement Finding has invalid point geometry: {finding_id}"
        )
    try:
        parsed = shape(geometry)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"{artifact_name} Route Refinement Finding has invalid point geometry: {finding_id}"
        ) from error
    if parsed.is_empty or parsed.geom_type not in {"Point", "MultiPoint"}:
        raise ValueError(
            f"{artifact_name} Route Refinement Finding must have non-empty Point or "
            f"MultiPoint geometry: {finding_id}"
        )
