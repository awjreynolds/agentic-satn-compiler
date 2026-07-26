"""Prepare finite alignment candidates for the optional Wayfinding Pass.

This module connects the existing Backbone-and-Access compiler to the approved
alignment-selection domain without claiming that a selection has happened. It
re-runs the existing deterministic route-option boundary for each compiled
*community* access connection, admits only bounded material alternatives, and
loads configured population and education evidence.

The local operator and their chosen input files are trusted. SHA-256 values in
this module are reproducible content identities used for stale-input detection
and lineage; they are not signatures, credentials, certificates, or identity
claims.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString

from satn.alignment_selection import (
    AlignmentCandidateInput,
    AlignmentCandidateSet,
    CanonicalLineString,
    CriterionState,
    NetworkRole,
    admit_candidate_set,
)
from satn.identifiers import stable_id
from satn.network_selection import CandidateSourceClass, NetworkSelectionProfile
from satn.psa_evidence_loaders import (
    EducationAccessEvidenceLoad,
    PopulationReachEvidenceLoad,
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.routing import RoadGraph, RouteOption, choose_alignment

_CANONICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_CURRENT_ASSET_TYPES = frozenset({"ncn-route", "greenway-cycleway"})
_CURRENT_ASSET_ROLES = frozenset({"established-route", "greenway-cycleway"})


def _fingerprint(value: object) -> str:
    """Return a deterministic content identity, never an authentication claim."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class CandidatePreparationIssue:
    """One deterministic reason candidate preparation could not use an input."""

    access_connection_id: str
    reason: str
    detail: str
    route_role: str | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "access_connection_id": self.access_connection_id,
            "reason": self.reason,
            "detail": self.detail,
            "route_role": self.route_role,
        }


@dataclass(frozen=True)
class PreparedCommunityConnection:
    """One actual Backbone Community Connection and its finite candidate set."""

    access_connection_id: str
    candidate_set: AlignmentCandidateSet
    root_spine_id: str
    strategic_source_id: object
    strategic_evidence_id: object
    strategic_provenance: object
    routing_reason: str
    candidate_provenance: tuple[dict[str, object], ...]

    def canonical(self) -> dict[str, object]:
        return {
            "access_connection_id": self.access_connection_id,
            "candidate_set": self.candidate_set.model_dump(mode="json"),
            "root_spine_id": self.root_spine_id,
            "strategic_source_id": _json_safe(self.strategic_source_id),
            "strategic_evidence_id": _json_safe(self.strategic_evidence_id),
            "strategic_provenance": _json_safe(self.strategic_provenance),
            "routing_reason": self.routing_reason,
            "candidate_provenance": [_json_safe(item) for item in self.candidate_provenance],
        }


@dataclass(frozen=True)
class AlignmentEvidencePreparationResult:
    """Honest preparation-only output of the optional compiler seam."""

    profile_fingerprint: str
    status: str
    prepared_connections: tuple[PreparedCommunityConnection, ...]
    generation_issues: tuple[CandidatePreparationIssue, ...]
    missing_inputs: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    evidence_lineage: dict[str, object]
    preparation_fingerprint: str
    diagnostics: dict[str, object]

    @property
    def prepared(self) -> bool:
        return self.status == "prepared"

    def metadata(self) -> dict[str, object]:
        return {
            "profile_fingerprint": self.profile_fingerprint,
            "status": self.status,
            "preparation_fingerprint": self.preparation_fingerprint,
            "missing_inputs": list(self.missing_inputs),
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "evidence_lineage": _json_safe(self.evidence_lineage),
            "candidate_set_count": len(self.prepared_connections),
            "candidate_count": sum(
                len(item.candidate_set.candidates) for item in self.prepared_connections
            ),
            "prepared_connections": [item.canonical() for item in self.prepared_connections],
            "generation_issues": [item.canonical() for item in self.generation_issues],
            "diagnostics": _json_safe(self.diagnostics),
        }


def prepare_alignment_evidence(
    profile: NetworkSelectionProfile,
    *,
    road_graph: RoadGraph,
    spine_access_connections: gpd.GeoDataFrame,
    access_obligations: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    configuration: Mapping[str, object] | None,
    config_directory: Path,
    as_at: date | None,
    school_register_max_age_days: int | None,
    strategic_admissions_max_age_days: int | None,
) -> AlignmentEvidencePreparationResult:
    """Prepare community candidate sets and verify all declared evidence.

    A missing optional evidence declaration yields ``incomplete``. A declared
    file that is missing, malformed, stale, or whose bytes do not match its
    declared content identity raises from the strict loader and therefore
    cannot be published or mistaken for an incomplete optional input.
    """

    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    prepared_connections, issues = _prepare_community_candidate_sets(
        profile,
        road_graph=road_graph,
        spine_access_connections=spine_access_connections,
        access_obligations=access_obligations,
        strategic_spines=strategic_spines,
        context=context,
    )
    missing: list[str] = []
    population_evidence: PopulationReachEvidenceLoad | None = None
    education_evidence: EducationAccessEvidenceLoad | None = None
    configuration = configuration or {}
    population = configuration.get("population_reach_evidence")
    schools = configuration.get("school_register_evidence")
    admissions = configuration.get("strategic_education_destination_admissions")

    if population is None:
        missing.append("population-reach-evidence")
    else:
        population_evidence = load_population_reach_evidence(
            population,
            base_directory=config_directory,
            pwc_outside_tolerance_m=0,
        )
    if schools is None:
        missing.append("school-register-evidence")
    else:
        if as_at is None:
            missing.append("network-selection-as-at")
        if school_register_max_age_days is None:
            missing.append("network-selection-school-register-max-age-days")
        if admissions is not None and strategic_admissions_max_age_days is None:
            missing.append("network-selection-strategic-admissions-max-age-days")
        if (
            as_at is not None
            and school_register_max_age_days is not None
            and (admissions is None or strategic_admissions_max_age_days is not None)
        ):
            education_evidence = load_education_access_evidence(
                schools,
                admissions,
                base_directory=config_directory,
                as_at=as_at,
                school_register_max_age_days=school_register_max_age_days,
                strategic_admissions_max_age_days=strategic_admissions_max_age_days,
            )
    if admissions is None and strategic_admissions_max_age_days is not None:
        raise ValueError(
            "strategic-admissions freshness cannot be configured without an artifact"
        )
    if admissions is not None and schools is None:
        raise ValueError("strategic-admissions evidence requires school-register evidence")

    evidence_lineage = _evidence_lineage(population_evidence, education_evidence)
    evidence_fingerprints = _evidence_fingerprints(
        population_evidence,
        education_evidence,
    )
    ordered_missing = tuple(sorted(set(missing)))
    status = (
        "prepared"
        if population_evidence is not None
        and education_evidence is not None
        and not ordered_missing
        else "incomplete"
    )
    diagnostics = {
        "candidate_set_count": len(prepared_connections),
        "candidate_count": sum(
            len(item.candidate_set.candidates) for item in prepared_connections
        ),
        "community_connection_count": int(
            (
                spine_access_connections.get(
                    "obligation_kind",
                    pd.Series(dtype=object),
                )
                == "community"
            ).sum()
        ),
        "school_branch_candidates_generated": 0,
        "generation_issue_count": len(issues),
        "replay_directive": "recompile-whole-network-on-ledger-change",
        "selection_performed": False,
        "agent_runtime_invoked": False,
    }
    preparation_payload = {
        "contract": "satn-alignment-evidence-preparation/v1",
        "profile_fingerprint": profile.fingerprint,
        "status": status,
        "prepared_connections": [item.canonical() for item in prepared_connections],
        "generation_issues": [item.canonical() for item in issues],
        "missing_inputs": ordered_missing,
        "evidence_lineage": evidence_lineage,
        "evidence_fingerprints": evidence_fingerprints,
        "diagnostics": diagnostics,
    }
    return AlignmentEvidencePreparationResult(
        profile_fingerprint=profile.fingerprint,
        status=status,
        prepared_connections=prepared_connections,
        generation_issues=issues,
        missing_inputs=ordered_missing,
        evidence_fingerprints=evidence_fingerprints,
        evidence_lineage=evidence_lineage,
        preparation_fingerprint=_fingerprint(preparation_payload),
        diagnostics=diagnostics,
    )


def _prepare_community_candidate_sets(
    profile: NetworkSelectionProfile,
    *,
    road_graph: RoadGraph,
    spine_access_connections: gpd.GeoDataFrame,
    access_obligations: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
) -> tuple[
    tuple[PreparedCommunityConnection, ...],
    tuple[CandidatePreparationIssue, ...],
]:
    if spine_access_connections.empty:
        return (), ()
    community = spine_access_connections[
        spine_access_connections["obligation_kind"].eq("community")
    ].copy()
    prepared: list[PreparedCommunityConnection] = []
    issues: list[CandidatePreparationIssue] = []
    for _, connection in community.sort_values("access_connection_id").iterrows():
        access_connection_id = str(connection["access_connection_id"])
        start = _text(connection.get("community_attachment_node"))
        end = _text(connection.get("target_attachment_node")) or _text(
            connection.get("spine_attachment_node")
        )
        endpoint_left = _canonical_endpoint(
            connection.get("community_id") or connection.get("place_id"),
            prefix="community-endpoint",
        )
        endpoint_right = _canonical_endpoint(
            connection.get("parent_place_id")
            or connection.get("parent_target_id")
            or connection.get("spine_id"),
            prefix="network-endpoint",
        )
        if not start or not end:
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason="missing-routing-attachment",
                    detail="community and target attachment nodes are required",
                )
            )
            continue
        if endpoint_left == endpoint_right:
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason="invalid-connection-endpoints",
                    detail="community and network endpoints resolve to the same identifier",
                )
            )
            continue
        _selected, options, routing_reason = choose_alignment(road_graph, start, end)
        if not options:
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason="no-continuous-route",
                    detail=routing_reason,
                )
            )
        spine = _strategic_spine_row(strategic_spines, connection.get("spine_id"))
        strategic_payload = _strategic_payload(spine)
        obligation_ids = _connection_obligation_ids(
            access_obligations,
            connection,
            access_connection_id,
        )
        candidate_inputs: list[AlignmentCandidateInput] = []
        candidate_records: list[dict[str, object]] = []
        retained_route_geometries: dict[str, str] = {}
        for option in options:
            route_role = str(option.role)
            geometry, geometry_issue = _canonical_geometry(option.geometry, road_graph.crs)
            if geometry is None:
                issues.append(
                    CandidatePreparationIssue(
                        access_connection_id=access_connection_id,
                        reason=geometry_issue or "invalid-route-geometry",
                        detail="route option is not one connected simple LineString",
                        route_role=route_role,
                    )
                )
                continue
            retained_role = retained_route_geometries.get(geometry.fingerprint)
            if retained_role is not None:
                issues.append(
                    CandidatePreparationIssue(
                        access_connection_id=access_connection_id,
                        reason="duplicate-routing-geometry",
                        detail=(
                            f"{route_role} duplicates the already retained "
                            f"{retained_role} route geometry"
                        ),
                        route_role=route_role,
                    )
                )
                continue
            retained_route_geometries[geometry.fingerprint] = route_role
            existing_rows, current_asset_share = _current_asset_evidence(
                option.geometry,
                route_crs=road_graph.crs,
                context=context,
            )
            source_class = _candidate_source_class(
                option,
                road_graph,
                current_asset_share=current_asset_share,
                b_road_enabled=(
                    CandidateSourceClass.B_ROAD_CORRIDOR
                    in profile.candidate_source_precedence
                ),
            )
            connection_payload = _connection_payload(connection)
            option_payload = {
                "role": route_role,
                "summary": option.summary(),
                "edge_ids": list(option.edge_ids),
                "geometry_wkb": option.geometry.wkb_hex,
                "source_class": source_class.value,
                "current_asset_share": current_asset_share,
                "current_asset_evidence": existing_rows,
                "connection": connection_payload,
                "strategic_spine": strategic_payload,
            }
            provenance_ids = tuple(
                sorted(
                    {
                        _canonical_provenance_id(access_connection_id),
                        *(
                            _canonical_provenance_id(value)
                            for value in (
                                connection.get("spine_id"),
                                connection.get("obligation_id"),
                                spine.get("evidence_id") if spine is not None else None,
                                spine.get("source_id") if spine is not None else None,
                            )
                            if _text(value)
                        ),
                    }
                )
            )
            candidate = AlignmentCandidateInput(
                network_role=NetworkRole.COMMUNITY_ACCESS,
                endpoints=(endpoint_left, endpoint_right),
                source_class=source_class,
                geometry=geometry,
                evidence_fingerprints=(_fingerprint(option_payload),),
                provenance_ids=provenance_ids,
                topology_state=(
                    CriterionState.SATISFIED
                    if option.bidirectional
                    else CriterionState.UNSATISFIED
                ),
                served_network_place_ids=(endpoint_left,),
                served_access_obligation_ids=obligation_ids,
                directness_m=float(option.length_km * 1000),
            )
            candidate_inputs.append(candidate)
            candidate_records.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "route_role": route_role,
                    "source_class": source_class.value,
                    "routing_edge_ids": list(option.edge_ids),
                    "current_asset_share": current_asset_share,
                    "current_asset_evidence": existing_rows,
                    "strategic_source_id": strategic_payload["source_id"],
                    "strategic_evidence_id": strategic_payload["evidence_id"],
                    "strategic_provenance": strategic_payload["provenance"],
                    "evidence_fingerprint": candidate.evidence_fingerprints[0],
                }
            )
        candidate_set = admit_candidate_set(
            profile,
            network_role=NetworkRole.COMMUNITY_ACCESS,
            endpoints=(endpoint_left, endpoint_right),
            candidates=tuple(candidate_inputs),
            mandatory_network_place_ids=(endpoint_left,),
            mandatory_access_obligation_ids=obligation_ids,
        )
        prepared.append(
            PreparedCommunityConnection(
                access_connection_id=access_connection_id,
                candidate_set=candidate_set,
                root_spine_id=_text(connection.get("spine_id")) or "",
                strategic_source_id=strategic_payload["source_id"],
                strategic_evidence_id=strategic_payload["evidence_id"],
                strategic_provenance=strategic_payload["provenance"],
                routing_reason=routing_reason,
                candidate_provenance=tuple(
                    sorted(candidate_records, key=lambda item: str(item["candidate_id"]))
                ),
            )
        )
    return (
        tuple(sorted(prepared, key=lambda item: item.access_connection_id)),
        tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.access_connection_id,
                    item.reason,
                    item.route_role or "",
                    item.detail,
                ),
            )
        ),
    )


def _canonical_geometry(
    geometry: object,
    crs: object,
) -> tuple[CanonicalLineString | None, str | None]:
    if isinstance(geometry, MultiLineString):
        return None, "disconnected-multipart-route"
    if not isinstance(geometry, LineString) or geometry.is_empty:
        return None, "invalid-route-geometry"
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(27700).iloc[0]
    if not isinstance(projected, LineString) or len(projected.coords) < 2:
        return None, "invalid-route-geometry"
    try:
        return (
            CanonicalLineString(
                coordinates=tuple(
                    (float(coordinate[0]), float(coordinate[1]))
                    for coordinate in projected.coords
                )
            ),
            None,
        )
    except ValueError:
        return None, "non-simple-or-disconnected-route"


def _candidate_source_class(
    option: RouteOption,
    graph: RoadGraph,
    *,
    current_asset_share: float,
    b_road_enabled: bool,
) -> CandidateSourceClass:
    if option.role == "ncn-informed" and current_asset_share > 0:
        return CandidateSourceClass.VERIFIED_EXISTING_ASSET
    refs = _option_refs(option, graph)
    if option.role == "strategic-spine" and any(
        re.fullmatch(r"A\s*\d+[A-Z]?", ref, re.IGNORECASE) for ref in refs
    ):
        return CandidateSourceClass.A_ROAD_CORRIDOR
    if (
        b_road_enabled
        and option.role == "strategic-spine"
        and any(re.fullmatch(r"B\s*\d+[A-Z]?", ref, re.IGNORECASE) for ref in refs)
    ):
        return CandidateSourceClass.B_ROAD_CORRIDOR
    return CandidateSourceClass.OTHER_ROUTABLE


def _option_refs(option: RouteOption, graph: RoadGraph) -> tuple[str, ...]:
    edge_ids = set(option.edge_ids)
    refs: set[str] = set()
    for _, _, edge in graph.graph.edges(data=True):
        if str(edge.get("edge_id")) not in edge_ids:
            continue
        value = edge.get("ref", ())
        if isinstance(value, str):
            refs.add(value)
        else:
            refs.update(str(item) for item in value)
    return tuple(sorted(refs))


def _current_asset_evidence(
    geometry: object,
    *,
    route_crs: object,
    context: gpd.GeoDataFrame,
) -> tuple[list[dict[str, object]], float]:
    if not isinstance(geometry, LineString) or context.empty:
        return [], 0.0
    required = {"feature_type", "ncn_evidence_role", "evidence_id", "source_id"}
    if not required.issubset(context.columns):
        return [], 0.0
    current = context[
        context["feature_type"].isin(_CURRENT_ASSET_TYPES)
        & context["ncn_evidence_role"].isin(_CURRENT_ASSET_ROLES)
        & context["evidence_id"].notna()
        & context["source_id"].notna()
    ]
    if current.empty:
        return [], 0.0
    route = gpd.GeoSeries([geometry], crs=route_crs).to_crs(27700).iloc[0]
    projected = current.to_crs(27700)
    matched: list[dict[str, object]] = []
    match_geometries = []
    for index, row in projected.sort_values("evidence_id").iterrows():
        overlap_m = float(route.intersection(row.geometry.buffer(20)).length)
        if overlap_m <= 0:
            continue
        original = current.loc[index]
        matched.append(
            {
                "evidence_id": _json_safe(original.get("evidence_id")),
                "source_id": _json_safe(original.get("source_id")),
                "feature_type": _json_safe(original.get("feature_type")),
                "ncn_evidence_role": _json_safe(original.get("ncn_evidence_role")),
                "geometry_wkb": original.geometry.wkb_hex,
            }
        )
        match_geometries.append(row.geometry.buffer(20))
    if not match_geometries or route.length <= 0:
        return [], 0.0
    corridor = gpd.GeoSeries(match_geometries, crs=27700).union_all()
    share = min(1.0, float(route.intersection(corridor).length / route.length))
    return matched, share


def _connection_obligation_ids(
    obligations: gpd.GeoDataFrame,
    connection: pd.Series,
    access_connection_id: str,
) -> tuple[str, ...]:
    values: set[str] = set()
    if not obligations.empty and "access_connection_id" in obligations:
        for value in obligations.loc[
            obligations["access_connection_id"].astype(str).eq(access_connection_id),
            "obligation_id",
        ]:
            if _text(value):
                values.add(_canonical_provenance_id(value))
    if not values and _text(connection.get("obligation_id")):
        values.add(_canonical_provenance_id(connection.get("obligation_id")))
    return tuple(sorted(values))


def _strategic_spine_row(
    strategic_spines: gpd.GeoDataFrame,
    spine_id: object,
) -> pd.Series | None:
    if strategic_spines.empty or "spine_id" not in strategic_spines:
        return None
    matches = strategic_spines[strategic_spines["spine_id"].astype(str).eq(str(spine_id))]
    return None if matches.empty else matches.sort_values("spine_id").iloc[0]


def _strategic_payload(spine: pd.Series | None) -> dict[str, object]:
    if spine is None:
        return {"source_id": None, "evidence_id": None, "provenance": None}
    return {
        "source_id": _json_safe(spine.get("source_id")),
        "evidence_id": _json_safe(spine.get("evidence_id")),
        "provenance": _json_safe(spine.get("provenance")),
    }


def _connection_payload(connection: pd.Series) -> dict[str, object]:
    fields = (
        "access_connection_id",
        "obligation_id",
        "obligation_kind",
        "place_id",
        "community_id",
        "spine_id",
        "root_spine_id",
        "branch_id",
        "parent_branch_id",
        "parent_role",
        "parent_target_id",
        "parent_place_id",
        "community_attachment_node",
        "target_attachment_node",
        "spine_attachment_node",
        "source_ids",
        "provenance",
    )
    return {field: _json_safe(connection.get(field)) for field in fields}


def _canonical_endpoint(value: object, *, prefix: str) -> str:
    text = _text(value)
    if text and _CANONICAL_ID.fullmatch(text):
        return text
    return stable_id(prefix, text or "missing")


def _canonical_provenance_id(value: object) -> str:
    text = _text(value)
    if text and _CANONICAL_ID.fullmatch(text):
        return text
    return stable_id("source", text or "missing")


def _text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _evidence_fingerprints(
    population: PopulationReachEvidenceLoad | None,
    education: EducationAccessEvidenceLoad | None,
) -> tuple[str, ...]:
    fingerprints: set[str] = set()
    if population is not None:
        fingerprints.update(
            {
                population.source.content_sha256,
                population.frame_content_sha256,
                *(item.content_sha256 for item in population.artifact_lineage),
            }
        )
    if education is not None:
        fingerprints.add(education.governed_source_fingerprint)
        fingerprints.add(education.school_register_lineage.content_sha256)
        if education.admissions_lineage is not None:
            fingerprints.add(education.admissions_lineage.content_sha256)
    return tuple(sorted(fingerprints))


def _evidence_lineage(
    population: PopulationReachEvidenceLoad | None,
    education: EducationAccessEvidenceLoad | None,
) -> dict[str, object]:
    lineage: dict[str, object] = {}
    if population is not None:
        lineage["population"] = {
            "source": population.source.canonical(),
            "source_content_sha256": population.source.content_sha256,
            "frame_content_sha256": population.frame_content_sha256,
            "artifact_lineage": [
                item.canonical() for item in population.artifact_lineage
            ],
        }
    if education is not None:
        lineage["education"] = {
            "governed_source_fingerprint": education.governed_source_fingerprint,
            "source_snapshot": education.source_snapshot.model_dump(mode="json"),
            "school_register_lineage": education.school_register_lineage.canonical(),
            "admissions_lineage": (
                education.admissions_lineage.canonical()
                if education.admissions_lineage is not None
                else None
            ),
            "as_at": education.as_at.isoformat(),
        }
    return lineage


def _json_safe(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, Path)):
        return value.isoformat() if isinstance(value, date) else value.as_posix()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)
