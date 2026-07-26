"""Prepare finite Spine Access candidates for the optional Wayfinding Pass.

This module connects the existing Backbone-and-Access compiler to the approved
alignment-selection domain without claiming that a selection has happened. It
re-runs the existing deterministic route-option boundary for each compiled
Spine Access connection, admits only bounded material alternatives, and loads
configured population and education inputs. It does not select strategic
Community Connections or produce Preferred Strategic Alignments; a separate
scenario bridge may consume only provenance-proven chained Community rows.

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
from dataclasses import dataclass, replace
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
from satn.evidence import corridor_overlap_share
from satn.identifiers import stable_id
from satn.models import SourceConfig
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
_PREPARATION_CONTRACT = "satn-spine-access-candidate-preparation/v1"


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
    candidate_id: str | None = None
    retained_candidate_id: str | None = None
    source_class: str | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "access_connection_id": self.access_connection_id,
            "reason": self.reason,
            "detail": self.detail,
            "route_role": self.route_role,
            "candidate_id": self.candidate_id,
            "retained_candidate_id": self.retained_candidate_id,
            "source_class": self.source_class,
        }


@dataclass(frozen=True)
class PreparedConnectionRosterRecord:
    """Exhaustive disposition for one compiler-emitted community connection."""

    access_connection_id: str
    obligation_kind: str | None
    parent_role: str | None
    community_id: str | None
    place_id: str | None
    parent_place_id: str | None
    disposition: str
    reason: str | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "access_connection_id": self.access_connection_id,
            "obligation_kind": self.obligation_kind,
            "parent_role": self.parent_role,
            "community_id": self.community_id,
            "place_id": self.place_id,
            "parent_place_id": self.parent_place_id,
            "disposition": self.disposition,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class _GeneratedCandidate:
    """Internal candidate plus its immutable pre-admission audit record."""

    candidate: AlignmentCandidateInput
    route_role: str
    evidence_quality: float
    record: PreparedCandidateRecord
    pre_admission_rejection_reason: str | None = None


@dataclass(frozen=True)
class PreparedCandidateRecord:
    """Complete immutable record for one generated, rejected, or retained option."""

    candidate: AlignmentCandidateInput
    route_role: str
    routing_edge_ids: tuple[str, ...]
    generation_rationale: str
    current_asset_share: float
    current_asset_evidence_json: str
    official_b_road_share: float
    official_b_road_evidence_json: str
    connection_json: str
    strategic_spine_json: str
    preparation_disposition: str = "generated"
    rejection_reason: str | None = None
    retained_candidate_id: str | None = None
    review_required: bool = False

    def canonical(self) -> dict[str, object]:
        candidate = self.candidate.model_dump(mode="json")
        candidate["geometry_fingerprint"] = self.candidate.geometry.fingerprint
        candidate["geometry_equivalence_fingerprint"] = (
            self.candidate.geometry.equivalence_fingerprint
        )
        return {
            "candidate": candidate,
            "candidate_id": self.candidate.candidate_id,
            "route_role": self.route_role,
            "source_class": self.candidate.source_class.value,
            "topology_state": self.candidate.topology_state.value,
            "endpoints": list(self.candidate.endpoints),
            "served_network_place_ids": list(
                self.candidate.served_network_place_ids
            ),
            "served_access_obligation_ids": list(
                self.candidate.served_access_obligation_ids
            ),
            "served_strategic_destination_ids": list(
                self.candidate.served_strategic_destination_ids
            ),
            "directness_m": self.candidate.directness_m,
            "geometry_fingerprint": self.candidate.geometry.fingerprint,
            "routing_edge_ids": list(self.routing_edge_ids),
            "generation_rationale": self.generation_rationale,
            "current_asset_share": self.current_asset_share,
            "current_asset_evidence": json.loads(self.current_asset_evidence_json),
            "official_b_road_share": self.official_b_road_share,
            "official_b_road_evidence": json.loads(
                self.official_b_road_evidence_json
            ),
            "connection": json.loads(self.connection_json),
            "strategic_spine": json.loads(self.strategic_spine_json),
            "preparation_disposition": self.preparation_disposition,
            "rejection_reason": self.rejection_reason,
            "retained_candidate_id": self.retained_candidate_id,
            "review_required": self.review_required,
        }


@dataclass(frozen=True)
class PreparedSpineAccessConnection:
    """One bounded Spine Access connection and its finite candidate set."""

    access_connection_id: str
    candidate_set: AlignmentCandidateSet
    root_spine_id: str
    strategic_source_id: object
    strategic_evidence_id: object
    strategic_provenance: object
    obligation_kind: str
    parent_role: str
    community_id: str
    place_id: str
    parent_place_id: str
    candidate_generation_rationales: tuple[dict[str, str], ...]
    candidate_records: tuple[PreparedCandidateRecord, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "access_connection_id": self.access_connection_id,
            "candidate_set": self.candidate_set.model_dump(mode="json"),
            "root_spine_id": self.root_spine_id,
            "strategic_source_id": _json_safe(self.strategic_source_id),
            "strategic_evidence_id": _json_safe(self.strategic_evidence_id),
            "strategic_provenance": _json_safe(self.strategic_provenance),
            "obligation_kind": self.obligation_kind,
            "parent_role": self.parent_role,
            "community_id": self.community_id,
            "place_id": self.place_id,
            "parent_place_id": self.parent_place_id,
            "candidate_generation_rationales": [
                dict(item) for item in self.candidate_generation_rationales
            ],
            "candidate_records": [item.canonical() for item in self.candidate_records],
        }


@dataclass(frozen=True)
class SpineAccessCandidatePreparationResult:
    """Honest Spine Access preparation-only output of the optional compiler seam."""

    contract: str
    profile_fingerprint: str
    status: str
    prepared_spine_access_connections: tuple[PreparedSpineAccessConnection, ...]
    connection_roster: tuple[PreparedConnectionRosterRecord, ...]
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
            "contract": self.contract,
            "profile_fingerprint": self.profile_fingerprint,
            "status": self.status,
            "preparation_fingerprint": self.preparation_fingerprint,
            "missing_inputs": list(self.missing_inputs),
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "evidence_lineage": _json_safe(self.evidence_lineage),
            "candidate_set_count": len(self.prepared_spine_access_connections),
            "candidate_count": sum(
                len(item.candidate_set.candidates)
                for item in self.prepared_spine_access_connections
            ),
            "prepared_spine_access_connections": [
                item.canonical() for item in self.prepared_spine_access_connections
            ],
            "connection_roster": [item.canonical() for item in self.connection_roster],
            "generation_issues": [item.canonical() for item in self.generation_issues],
            "diagnostics": _json_safe(self.diagnostics),
        }

    def canonical_payload(self) -> dict[str, object]:
        """Return every field bound by ``preparation_fingerprint``."""

        return {
            "contract": self.contract,
            "profile_fingerprint": self.profile_fingerprint,
            "status": self.status,
            "prepared_spine_access_connections": [
                item.canonical() for item in self.prepared_spine_access_connections
            ],
            "connection_roster": [item.canonical() for item in self.connection_roster],
            "generation_issues": [item.canonical() for item in self.generation_issues],
            "missing_inputs": list(self.missing_inputs),
            "evidence_lineage": _json_safe(self.evidence_lineage),
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "diagnostics": _json_safe(self.diagnostics),
        }


def prepare_spine_access_candidates(
    profile: NetworkSelectionProfile,
    *,
    road_graph: RoadGraph,
    spine_access_connections: gpd.GeoDataFrame,
    access_obligations: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    official_road_classification: gpd.GeoDataFrame | None,
    source_config: SourceConfig,
    config_directory: Path,
) -> SpineAccessCandidatePreparationResult:
    """Prepare bounded Spine Access candidate sets and verify declared inputs.

    A missing optional evidence declaration yields ``incomplete``. A declared
    file that is missing, malformed, stale, or whose bytes do not match its
    declared content identity raises from the strict loader and therefore
    cannot be published or mistaken for an incomplete optional input.
    """

    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    prepared_spine_access_connections, issues, connection_roster = (
        _prepare_spine_access_candidate_sets(
            profile,
            road_graph=road_graph,
            spine_access_connections=spine_access_connections,
            access_obligations=access_obligations,
            strategic_spines=strategic_spines,
            context=context,
            official_road_classification=official_road_classification,
        )
    )
    missing: list[str] = []
    population_evidence: PopulationReachEvidenceLoad | None = None
    education_evidence: EducationAccessEvidenceLoad | None = None
    population = source_config.population_reach_evidence
    schools = source_config.school_register_evidence
    admissions = source_config.strategic_education_destination_admissions

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
        assert source_config.network_selection_as_at is not None
        assert source_config.network_selection_school_register_max_age_days is not None
        education_evidence = load_education_access_evidence(
            schools,
            admissions,
            base_directory=config_directory,
            as_at=source_config.network_selection_as_at,
            school_register_max_age_days=(
                source_config.network_selection_school_register_max_age_days
            ),
            strategic_admissions_max_age_days=(
                source_config.network_selection_strategic_admissions_max_age_days
            ),
        )
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
        "candidate_set_count": len(prepared_spine_access_connections),
        "candidate_count": sum(
            len(item.candidate_set.candidates)
            for item in prepared_spine_access_connections
        ),
        "spine_access_connection_count": int(
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
        "expected_connection_roster_count": len(connection_roster),
        "prepared_connection_count": sum(
            item.disposition.startswith("prepared-") for item in connection_roster
        ),
        "out_of_scope_connection_count": sum(
            item.disposition == "out-of-scope-direct-strategic-spine"
            for item in connection_roster
        ),
        "unresolved_connection_count": sum(
            item.disposition == "unresolved-gap" for item in connection_roster
        ),
        "replay_directive": "recompile-whole-network-on-ledger-change",
        "selection_performed": False,
        "agent_runtime_invoked": False,
        "scope": "spine-access-candidate-preparation",
        "strategic_community_connection_scope": "chained-community-connections-only",
    }
    preparation_payload = {
        "contract": _PREPARATION_CONTRACT,
        "profile_fingerprint": profile.fingerprint,
        "status": status,
        "prepared_spine_access_connections": [
            item.canonical() for item in prepared_spine_access_connections
        ],
        "connection_roster": [item.canonical() for item in connection_roster],
        "generation_issues": [item.canonical() for item in issues],
        "missing_inputs": ordered_missing,
        "evidence_lineage": evidence_lineage,
        "evidence_fingerprints": evidence_fingerprints,
        "diagnostics": diagnostics,
    }
    return SpineAccessCandidatePreparationResult(
        contract=_PREPARATION_CONTRACT,
        profile_fingerprint=profile.fingerprint,
        status=status,
        prepared_spine_access_connections=prepared_spine_access_connections,
        connection_roster=connection_roster,
        generation_issues=issues,
        missing_inputs=ordered_missing,
        evidence_fingerprints=evidence_fingerprints,
        evidence_lineage=evidence_lineage,
        preparation_fingerprint=_fingerprint(preparation_payload),
        diagnostics=diagnostics,
    )


def _prepare_spine_access_candidate_sets(
    profile: NetworkSelectionProfile,
    *,
    road_graph: RoadGraph,
    spine_access_connections: gpd.GeoDataFrame,
    access_obligations: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    official_road_classification: gpd.GeoDataFrame | None,
) -> tuple[
    tuple[PreparedSpineAccessConnection, ...],
    tuple[CandidatePreparationIssue, ...],
    tuple[PreparedConnectionRosterRecord, ...],
]:
    if spine_access_connections.empty:
        return (), (), ()
    community = spine_access_connections[
        spine_access_connections["obligation_kind"].eq("community")
    ].copy()
    prepared: list[PreparedSpineAccessConnection] = []
    issues: list[CandidatePreparationIssue] = []
    roster: list[PreparedConnectionRosterRecord] = []
    for _, connection in community.sort_values("access_connection_id").iterrows():
        access_connection_id = str(connection["access_connection_id"])
        obligation_kind = _text(connection.get("obligation_kind"))
        parent_role = _text(connection.get("parent_role"))
        exact_community_id = _text(connection.get("community_id"))
        exact_place_id = _text(connection.get("place_id"))
        exact_parent_place_id = _text(connection.get("parent_place_id"))
        if parent_role == "strategic-spine":
            reason = "out-of-scope-direct-strategic-spine-attachment"
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail=(
                        "A direct Community-to-Strategic-Spine attachment is Spine "
                        "Access, not a strategic Community Connection, and cannot be "
                        "promoted into Preferred Strategic Alignment selection"
                    ),
                )
            )
            roster.append(
                PreparedConnectionRosterRecord(
                    access_connection_id=access_connection_id,
                    obligation_kind=obligation_kind,
                    parent_role=parent_role,
                    community_id=exact_community_id,
                    place_id=exact_place_id,
                    parent_place_id=exact_parent_place_id,
                    disposition="out-of-scope-direct-strategic-spine",
                    reason=reason,
                )
            )
            continue
        if parent_role != "spine-access-connection":
            reason = "unsupported-parent-role"
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail=(
                        "Strategic Community Connection promotion requires exact "
                        "parent_role=spine-access-connection provenance"
                    ),
                )
            )
            roster.append(
                PreparedConnectionRosterRecord(
                    access_connection_id=access_connection_id,
                    obligation_kind=obligation_kind,
                    parent_role=parent_role,
                    community_id=exact_community_id,
                    place_id=exact_place_id,
                    parent_place_id=exact_parent_place_id,
                    disposition="unresolved-gap",
                    reason=reason,
                )
            )
            continue
        start = _first_present(connection.get("community_attachment_node"))
        end = _first_present(
            connection.get("target_attachment_node"),
            connection.get("spine_attachment_node"),
        )
        community_place_id = exact_community_id
        parent_place_id = exact_parent_place_id
        if (
            community_place_id is None
            or exact_place_id is None
            or community_place_id != exact_place_id
        ):
            reason = "missing-or-conflicting-community-network-place"
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail=(
                        "Strategic Community Connection promotion requires matching, "
                        "governed community_id and place_id values"
                    ),
                )
            )
            roster.append(
                PreparedConnectionRosterRecord(
                    access_connection_id=access_connection_id,
                    obligation_kind=obligation_kind,
                    parent_role=parent_role,
                    community_id=exact_community_id,
                    place_id=exact_place_id,
                    parent_place_id=exact_parent_place_id,
                    disposition="unresolved-gap",
                    reason=reason,
                )
            )
            continue
        if parent_place_id is None:
            reason = "missing-parent-network-place-endpoint"
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail=(
                        "Exact parent_place_id provenance is required; parent targets "
                        "and routing attachment nodes are not governed Network Places, "
                        "so this preparation gap retains unresolved topology"
                    ),
                )
            )
            roster.append(
                PreparedConnectionRosterRecord(
                    access_connection_id=access_connection_id,
                    obligation_kind=obligation_kind,
                    parent_role=parent_role,
                    community_id=exact_community_id,
                    place_id=exact_place_id,
                    parent_place_id=exact_parent_place_id,
                    disposition="unresolved-gap",
                    reason=reason,
                )
            )
            continue
        if not start or not end:
            reason = "missing-routing-endpoint"
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail=(
                        "community and target routing attachment identifiers are "
                        "required to generate candidate geometry"
                    ),
                )
            )
            roster.append(
                PreparedConnectionRosterRecord(
                    access_connection_id=access_connection_id,
                    obligation_kind=obligation_kind,
                    parent_role=parent_role,
                    community_id=exact_community_id,
                    place_id=exact_place_id,
                    parent_place_id=exact_parent_place_id,
                    disposition="unresolved-gap",
                    reason=reason,
                )
            )
            continue
        endpoint_left = _canonical_endpoint(
            community_place_id,
            prefix="community-endpoint",
        )
        endpoint_right = _canonical_endpoint(
            parent_place_id,
            prefix="network-endpoint",
        )
        assert endpoint_left is not None
        assert endpoint_right is not None
        if endpoint_left == endpoint_right:
            reason = "invalid-connection-endpoints"
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail="community and network endpoints resolve to the same identifier",
                )
            )
            roster.append(
                PreparedConnectionRosterRecord(
                    access_connection_id=access_connection_id,
                    obligation_kind=obligation_kind,
                    parent_role=parent_role,
                    community_id=exact_community_id,
                    place_id=exact_place_id,
                    parent_place_id=exact_parent_place_id,
                    disposition="unresolved-gap",
                    reason=reason,
                )
            )
            continue
        _ignored_choice, options, _ignored_selection_rationale = choose_alignment(
            road_graph,
            start,
            end,
        )
        if CandidateSourceClass.B_ROAD_CORRIDOR in profile.candidate_source_precedence:
            b_road_option = road_graph.option(start, end, "b-road-corridor")
            if b_road_option is not None:
                options = [*options, b_road_option]
        if not options:
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason="no-continuous-route",
                    detail=(
                        "No continuous routable option could be generated for this "
                        "Spine Access connection"
                    ),
                )
            )
        spine = _strategic_spine_row(strategic_spines, connection.get("spine_id"))
        strategic_payload = _strategic_payload(spine)
        obligation_ids = _connection_obligation_ids(
            access_obligations,
            connection,
            access_connection_id,
        )
        generated_candidates: list[_GeneratedCandidate] = []
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
            existing_rows, current_asset_share = _current_asset_evidence(
                option.geometry,
                route_crs=road_graph.crs,
                context=context,
            )
            b_road_rows, official_b_road_share = _official_b_road_evidence(
                option.geometry,
                route_crs=road_graph.crs,
                official_road_classification=official_road_classification,
            )
            source_class = _candidate_source_class(
                option,
                road_graph,
                current_asset_share=current_asset_share,
                official_b_road_share=official_b_road_share,
                b_road_enabled=(
                    CandidateSourceClass.B_ROAD_CORRIDOR
                    in profile.candidate_source_precedence
                ),
            )
            b_road_evidence_unverified = (
                route_role == "b-road-corridor"
                and source_class != CandidateSourceClass.B_ROAD_CORRIDOR
            )
            connection_payload = _connection_payload(connection)
            generation_rationale = _candidate_generation_rationale(route_role)
            option_payload = {
                "role": route_role,
                "generation_rationale": generation_rationale,
                "summary": option.summary(),
                "edge_ids": list(option.edge_ids),
                "geometry_wkb": option.geometry.wkb_hex,
                "source_class": source_class.value,
                "current_asset_share": current_asset_share,
                "current_asset_evidence": existing_rows,
                "official_b_road_share": official_b_road_share,
                "official_b_road_evidence": b_road_rows,
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
                served_network_place_ids=(endpoint_left, endpoint_right),
                served_access_obligation_ids=obligation_ids,
                directness_m=float(option.length_km * 1000),
            )
            record = PreparedCandidateRecord(
                candidate=candidate,
                route_role=route_role,
                routing_edge_ids=tuple(str(item) for item in option.edge_ids),
                generation_rationale=generation_rationale,
                current_asset_share=current_asset_share,
                current_asset_evidence_json=_canonical_json(existing_rows),
                official_b_road_share=official_b_road_share,
                official_b_road_evidence_json=_canonical_json(b_road_rows),
                connection_json=_canonical_json(connection_payload),
                strategic_spine_json=_canonical_json(strategic_payload),
                review_required=candidate.topology_state == CriterionState.UNKNOWN,
            )
            generated_candidates.append(
                _GeneratedCandidate(
                    candidate=candidate,
                    route_role=route_role,
                    evidence_quality=max(current_asset_share, official_b_road_share),
                    record=record,
                    pre_admission_rejection_reason=(
                        "b-road-evidence-unverified"
                        if b_road_evidence_unverified
                        else None
                    ),
                )
            )
        candidate_inputs, candidate_records, material_issues = _material_representatives(
            profile,
            access_connection_id=access_connection_id,
            generated=generated_candidates,
        )
        issues.extend(material_issues)
        candidate_set = admit_candidate_set(
            profile,
            network_role=NetworkRole.COMMUNITY_ACCESS,
            endpoints=(endpoint_left, endpoint_right),
            candidates=candidate_inputs,
            mandatory_network_place_ids=(endpoint_left, endpoint_right),
            mandatory_access_obligation_ids=obligation_ids,
        )
        prepared.append(
            PreparedSpineAccessConnection(
                access_connection_id=access_connection_id,
                candidate_set=candidate_set,
                root_spine_id=_text(connection.get("spine_id")) or "",
                strategic_source_id=strategic_payload["source_id"],
                strategic_evidence_id=strategic_payload["evidence_id"],
                strategic_provenance=strategic_payload["provenance"],
                obligation_kind=obligation_kind or "",
                parent_role=parent_role,
                community_id=community_place_id,
                place_id=exact_place_id,
                parent_place_id=parent_place_id,
                candidate_generation_rationales=tuple(
                    {
                        "route_role": item.route_role,
                        "rationale": item.record.generation_rationale,
                    }
                    for item in sorted(
                        generated_candidates,
                        key=lambda candidate: (
                            candidate.route_role,
                            candidate.candidate.candidate_id,
                        ),
                    )
                ),
                candidate_records=tuple(
                    sorted(
                        candidate_records,
                        key=lambda item: item.candidate.candidate_id,
                    )
                ),
            )
        )
        roster.append(
            PreparedConnectionRosterRecord(
                access_connection_id=access_connection_id,
                obligation_kind=obligation_kind,
                parent_role=parent_role,
                community_id=exact_community_id,
                place_id=exact_place_id,
                parent_place_id=exact_parent_place_id,
                disposition=(
                    "prepared-candidate-set"
                    if candidate_set.admitted_candidates
                    else "prepared-candidate-set-gap"
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
                    item.candidate_id or "",
                    item.retained_candidate_id or "",
                    item.detail,
                ),
            )
        ),
        tuple(sorted(roster, key=lambda item: item.access_connection_id)),
    )


def _material_representatives(
    profile: NetworkSelectionProfile,
    *,
    access_connection_id: str,
    generated: list[_GeneratedCandidate],
) -> tuple[
    tuple[AlignmentCandidateInput, ...],
    list[PreparedCandidateRecord],
    tuple[CandidatePreparationIssue, ...],
]:
    """Select one deterministic representative per material geometry cluster.

    The approved core admission contract cannot safely receive a material
    duplicate whose representative may later be removed by the profile limit.
    Clustering therefore happens at this adapter boundary. Every suppressed
    candidate remains in provenance with its retained representative.
    """

    precedence = {
        source_class: index
        for index, source_class in enumerate(profile.candidate_source_precedence)
    }
    topology_rank = {
        CriterionState.SATISFIED: 0,
        CriterionState.UNKNOWN: 1,
    }
    issues: list[CandidatePreparationIssue] = []
    eligible: list[_GeneratedCandidate] = []
    records: dict[str, PreparedCandidateRecord] = {}
    for item in generated:
        candidate_id = item.candidate.candidate_id
        if item.pre_admission_rejection_reason is not None:
            reason = item.pre_admission_rejection_reason
            records[candidate_id] = replace(
                item.record,
                preparation_disposition=f"rejected-{reason}",
                rejection_reason=reason,
            )
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason=reason,
                    detail=(
                        "the routed B-road option lacks matching governed official "
                        "B-road classification evidence and cannot enter admission"
                    ),
                    route_role=item.route_role,
                    candidate_id=candidate_id,
                    source_class=item.candidate.source_class.value,
                )
            )
            continue
        if item.candidate.topology_state == CriterionState.UNSATISFIED:
            records[candidate_id] = replace(
                item.record,
                preparation_disposition="rejected-topology-unsatisfied",
                rejection_reason="topology-unsatisfied",
            )
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason="topology-unsatisfied",
                    detail=(
                        f"{item.route_role} is not bidirectionally continuous and "
                        "cannot enter candidate admission"
                    ),
                    route_role=item.route_role,
                    candidate_id=candidate_id,
                    source_class=item.candidate.source_class.value,
                )
            )
            continue
        eligible.append(item)
        records[candidate_id] = item.record
        if item.candidate.topology_state == CriterionState.UNKNOWN:
            issues.append(
                CandidatePreparationIssue(
                    access_connection_id=access_connection_id,
                    reason="topology-unknown-review-required",
                    detail=(
                        f"{item.route_role} retains Grey topology and requires "
                        "review before it can displace a topologically satisfied option"
                    ),
                    route_role=item.route_role,
                    candidate_id=candidate_id,
                    source_class=item.candidate.source_class.value,
                )
            )
    ordered = sorted(
        eligible,
        key=lambda item: (
            topology_rank[item.candidate.topology_state],
            precedence[item.candidate.source_class],
            -item.evidence_quality,
            item.candidate.candidate_id,
        ),
    )
    representatives: list[_GeneratedCandidate] = []
    for item in ordered:
        retained = next(
            (
                representative
                for representative in representatives
                if representative.candidate.geometry.materially_equivalent(
                    item.candidate.geometry
                )
            ),
            None,
        )
        if retained is None:
            records[item.candidate.candidate_id] = replace(
                item.record,
                preparation_disposition="retained-representative",
            )
            representatives.append(item)
            continue
        exact = (
            retained.candidate.geometry.fingerprint
            == item.candidate.geometry.fingerprint
        )
        reason = (
            "exact-equivalent-routing-geometry"
            if exact
            else "materially-equivalent-routing-geometry"
        )
        records[item.candidate.candidate_id] = replace(
            item.record,
            preparation_disposition=f"rejected-{reason}",
            rejection_reason=reason,
            retained_candidate_id=retained.candidate.candidate_id,
        )
        issues.append(
            CandidatePreparationIssue(
                access_connection_id=access_connection_id,
                reason=reason,
                detail=(
                    f"{item.route_role} was suppressed in favour of "
                    f"{retained.route_role} under topology eligibility, profile "
                    "precedence, governed evidence quality, and stable identity"
                ),
                route_role=item.route_role,
                candidate_id=item.candidate.candidate_id,
                retained_candidate_id=retained.candidate.candidate_id,
                source_class=item.candidate.source_class.value,
            )
        )
    if any(
        item.candidate.topology_state == CriterionState.SATISFIED
        for item in representatives
    ):
        admissible_representatives: list[_GeneratedCandidate] = []
        for item in representatives:
            if item.candidate.topology_state != CriterionState.UNKNOWN:
                admissible_representatives.append(item)
                continue
            records[item.candidate.candidate_id] = replace(
                records[item.candidate.candidate_id],
                preparation_disposition="rejected-topology-unknown-review-required",
                rejection_reason="topology-unknown-review-required",
            )
        representatives = admissible_representatives
    return (
        tuple(item.candidate for item in representatives),
        [records[candidate_id] for candidate_id in sorted(records)],
        tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.candidate_id or "",
                    item.retained_candidate_id or "",
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
    official_b_road_share: float,
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
        and option.role == "b-road-corridor"
        and official_b_road_share >= 0.5
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


def _official_b_road_evidence(
    geometry: object,
    *,
    route_crs: object,
    official_road_classification: gpd.GeoDataFrame | None,
) -> tuple[list[dict[str, object]], float]:
    if (
        not isinstance(geometry, LineString)
        or official_road_classification is None
        or official_road_classification.empty
    ):
        return [], 0.0
    required = {
        "official_feature_id",
        "official_classification",
        "source_id",
        "effective_date",
        "licence",
        "content_fingerprint",
    }
    if not required.issubset(official_road_classification.columns):
        return [], 0.0
    b_roads = official_road_classification[
        official_road_classification["official_classification"].eq("b-road")
        & official_road_classification["official_feature_id"].notna()
        & official_road_classification["source_id"].notna()
        & official_road_classification["content_fingerprint"].notna()
    ]
    if b_roads.empty:
        return [], 0.0
    route = gpd.GeoSeries([geometry], crs=route_crs).to_crs(27700).iloc[0]
    projected = b_roads.to_crs(27700)
    matched: list[dict[str, object]] = []
    match_geometries = []
    for index, row in projected.sort_values("official_feature_id").iterrows():
        corridor = row.geometry.buffer(20)
        if float(route.intersection(corridor).length) <= 0:
            continue
        original = b_roads.loc[index]
        matched.append(
            {
                field: _json_safe(original.get(field))
                for field in (
                    "official_feature_id",
                    "official_classification",
                    "source_id",
                    "effective_date",
                    "licence",
                    "content_fingerprint",
                )
            }
            | {"geometry_wkb": original.geometry.wkb_hex}
        )
        match_geometries.append(row.geometry)
    if not match_geometries:
        return [], 0.0
    share = corridor_overlap_share(
        geometry,
        match_geometries,
        route_crs=route_crs,
        corridor_crs=projected.crs,
        buffer_m=20,
    )
    return matched, share


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
        match_geometries.append(row.geometry)
    if not match_geometries:
        return [], 0.0
    share = corridor_overlap_share(
        geometry,
        match_geometries,
        route_crs=route_crs,
        corridor_crs=projected.crs,
        buffer_m=20,
    )
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


def _canonical_endpoint(value: object, *, prefix: str) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if _CANONICAL_ID.fullmatch(text):
        return text
    return stable_id(prefix, text)


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


def _first_present(*values: object) -> str | None:
    return next((text for value in values if (text := _text(value)) is not None), None)


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _candidate_generation_rationale(route_role: str) -> str:
    return {
        "direct": "Generated as the shortest continuous routable baseline.",
        "strategic-spine": (
            "Generated by weighting A-road corridor edges to expose a direct "
            "strategic-corridor alternative."
        ),
        "b-road-corridor": (
            "Generated by weighting B-road edges, subject to governed official "
            "road-classification evidence."
        ),
        "ncn-informed": (
            "Generated by weighting edges associated with current cycle-route assets."
        ),
        "low-traffic": (
            "Generated by weighting lower-traffic routable highway classes."
        ),
    }.get(route_role, "Generated as a deterministic routable alternative.")


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
