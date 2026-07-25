"""Governed ATM seeding, comparison and typed divergence review."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd

from satn.agents import (
    AgentCompilationTerminated,
    AgentDecisionRequired,
    AgentDecisionResolver,
    AgentRuntimeSource,
    build_agent_decision_request,
    resolve_agent_decision,
    termination_choice,
)
from satn.models import (
    AgentConfig,
    AgentDecisionAction,
    AgentDecisionChoice,
    AgentFinding,
    AreaConfig,
    DivergenceRecord,
    TrafficLight,
)
from satn.routing import RouteOption

if TYPE_CHECKING:
    from satn.compiler import CompiledNetwork


def load_atm(config: AreaConfig) -> gpd.GeoDataFrame:
    if not config.atm.enabled:
        raise ValueError("ATM comparison is not enabled")
    if config.atm.path is None or not config.atm.path.exists():
        raise ValueError("ATM comparison requires an existing local atm.path")
    frame = gpd.read_file(config.atm.path)
    if frame.crs is None:
        raise ValueError("ATM reference geometry has no CRS")
    if frame.empty:
        raise ValueError("ATM reference geometry is empty")
    return frame


def source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_seeded_alignment(
    options: list[RouteOption],
    atm: gpd.GeoDataFrame,
    left: object,
    right: object,
) -> RouteOption | None:
    if not options:
        return None
    projected_atm = atm.to_crs(27700)
    endpoints = gpd.GeoSeries([left, right], crs=atm.crs).to_crs(27700)
    candidates = projected_atm[
        (projected_atm.distance(endpoints.iloc[0]) <= 2000)
        & (projected_atm.distance(endpoints.iloc[1]) <= 2000)
    ]
    if candidates.empty:
        return None
    reference = candidates.geometry.union_all()
    ranked: list[tuple[float, RouteOption]] = []
    for option in options:
        geometry = gpd.GeoSeries([option.geometry], crs=atm.crs).to_crs(27700).iloc[0]
        ranked.append((geometry.hausdorff_distance(reference), option))
    return min(ranked, key=lambda item: item[0])[1]


def compare_atm(
    compiled: CompiledNetwork,
    atm: gpd.GeoDataFrame,
    runtime: AgentRuntimeSource,
    config: AreaConfig,
    decision_resolver: AgentDecisionResolver | None = None,
) -> list[DivergenceRecord]:
    resolver = decision_resolver or AgentDecisionResolver(
        None,
        compiled.compilation_input_fingerprint,
    )
    connections = _authoritative_connections(compiled).to_crs(27700)
    reference = atm.to_crs(27700).copy().reset_index(drop=False)
    reference["_atm_id"] = [_feature_id(row, index) for index, row in reference.iterrows()]
    reference_buffer = reference.geometry.buffer(config.atm.match_buffer_m).union_all()
    records: list[DivergenceRecord] = []
    matched_atm_ids: set[str] = set()

    for _, connection in connections.iterrows():
        geometry = connection.geometry
        overlap = (
            geometry.intersection(reference_buffer).length / geometry.length
            if geometry.length
            else 0
        )
        matched = reference[
            reference.geometry.buffer(config.atm.match_buffer_m).intersects(geometry)
        ]
        atm_ids = sorted(matched["_atm_id"].tolist())
        matched_atm_ids.update(atm_ids)
        status = "match" if overlap >= 0.7 else "deviation" if overlap > 0 else "addition"
        records.append(
            _assess(
                runtime,
                connection.connection_id,
                status,
                atm_ids,
                min(float(overlap), 1.0),
                config.compilation.agent,
                compiled.compilation_input_fingerprint,
                resolver,
            )
        )

    for _, feature in reference.iterrows():
        atm_id = feature["_atm_id"]
        if atm_id in matched_atm_ids:
            continue
        records.append(
            _assess(
                runtime,
                f"atm:{atm_id}",
                "omission",
                [atm_id],
                0.0,
                config.compilation.agent,
                compiled.compilation_input_fingerprint,
                resolver,
            )
        )
    return records


def _authoritative_connections(compiled: CompiledNetwork) -> gpd.GeoDataFrame:
    access = compiled.spine_access_connections[["access_connection_id", "geometry"]].rename(
        columns={"access_connection_id": "connection_id"}
    )
    meetings = compiled.branch_meeting_connections[["meeting_connection_id", "geometry"]].rename(
        columns={"meeting_connection_id": "connection_id"}
    )
    return gpd.GeoDataFrame(
        pd.concat([access, meetings], ignore_index=True),
        geometry="geometry",
        crs=compiled.places.crs,
    )


def _assess(
    runtime: AgentRuntimeSource,
    connection_id: str,
    status: str,
    atm_ids: list[str],
    overlap: float,
    agent_config: AgentConfig,
    governed_input_fingerprint: str,
    decision_resolver: AgentDecisionResolver,
) -> DivergenceRecord:
    governing_status = TrafficLight.GREEN if status == "match" else TrafficLight.AMBER
    review = agent_config.review_decision(governing_status)
    if not review.review_required:
        return DivergenceRecord(
            connection_id=connection_id,
            status=status,
            **review.model_dump(),
            atm_feature_ids=atm_ids,
            overlap_ratio=overlap,
            explanation=(
                f"Agent review skipped by policy; governed geometry comparison is {status}."
            ),
            resolution_attempts=[],
            resolved=status == "match",
        )
    finding = AgentFinding(
        code=f"atm-{status}",
        severity="advisory",
        message=f"The governed ATM geometry comparison is {status}.",
        evidence_ids=atm_ids,
    )
    request = build_agent_decision_request(
        compilation_scope="atm-comparison",
        affected_identifiers=[connection_id, *atm_ids],
        criterion="atm-geometry-comparison",
        status=governing_status,
        evidence_references=atm_ids,
        findings=[finding],
        choices=[
            AgentDecisionChoice(
                choice_id="1",
                label=f"Retain the {status} comparison",
                compiler_action=AgentDecisionAction(
                    kind="retain-atm-comparison",
                    comparison_status=status,
                ),
                expected_consequence=(
                    "Keep the governed comparison visible without changing compiled "
                    "network geometry."
                ),
                mandatory_constraints=(
                    "ATM geometry remains a non-truth comparison source.",
                    "The choice cannot mutate authoritative compiled geometry.",
                ),
            ),
            termination_choice(),
        ],
        review_policy=review.review_policy,
        governed_input_fingerprint=decision_resolver.request_context_fingerprint(),
    )
    resolved = resolve_agent_decision(
        request,
        decision_resolver,
        runtime,
        agent_config,
    )
    action = resolved.choice.compiler_action
    if action.kind not in {"retain-atm-comparison", "terminate"} or (
        action.kind == "retain-atm-comparison" and action.comparison_status != status
    ):
        decision_resolver.validation = "invalid-action"
        raise AgentDecisionRequired(request, decision_resolver)
    record = DivergenceRecord(
        connection_id=connection_id,
        status=status,
        **review.model_dump(),
        atm_feature_ids=atm_ids,
        overlap_ratio=overlap,
        explanation=(
            f"{resolved.responder_mode.title()} selected choice "
            f"{resolved.response.choice_id}: {resolved.choice.label}."
        ),
        resolution_attempts=[],
        resolved=status == "match",
        runtime=resolved.runtime,
        model=resolved.model,
        usage=resolved.usage,
        decision_request=request,
        selected_choice_id=resolved.response.choice_id,
        mapped_action=action,
        responder_mode=resolved.responder_mode,
        choice_validation="accepted",
        affected_feature_identifiers=request.affected_identifiers,
    )
    decision_resolver.accept(resolved.response, record)
    if action.kind == "terminate":
        raise AgentCompilationTerminated(decision_resolver)
    return record


def _feature_id(row: object, index: object) -> str:
    for key in ("portal_feature_id", "id", "fid", "osmid"):
        value = row.get(key)
        if value is not None and str(value).lower() not in {"nan", "none", ""}:
            return str(value)
    return str(index)
