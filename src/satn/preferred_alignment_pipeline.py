"""Compiler adapter for Preferred Strategic Alignment evidence.

This module is deliberately a narrow seam between the existing SATN compiler
and the evidence-bound selection contract.  It never changes a route while
the selection profile is absent.  When enabled it exposes the finite strategic
corridors that the backbone already generated, verifies any declared governed
evidence, and reports an explicit incomplete state instead of making up a
population, education, cost, condition, or delivery conclusion.

The first public integration is intentionally conservative: configuration
without the complete governed evidence set is a reviewable *scenario input*,
not authority to silently re-rank the compiled network.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

from satn.network_selection import CandidateSourceClass, NetworkSelectionProfile
from satn.psa_evidence_loaders import (
    EducationAccessEvidenceLoad,
    GovernedEvidenceLoadError,
    PopulationReachEvidenceLoad,
    load_education_access_evidence,
    load_population_reach_evidence,
)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PreferredAlignmentPipelineResult:
    """Evidence-safe public result of the optional Wayfinding Pass.

    ``activation`` is deliberately not a plan/readiness status.  It records
    whether the compiler could construct the governed inputs needed by the
    already-approved selection core.  Publication adapters consume this as
    machine data in the next package.
    """

    profile_fingerprint: str
    activation: str
    candidate_options: gpd.GeoDataFrame
    missing_inputs: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    diagnostics: dict[str, object]

    @property
    def active(self) -> bool:
        return self.activation == "governed-evidence-ready"


def compile_preferred_alignment_pipeline(
    profile: NetworkSelectionProfile,
    *,
    source: Mapping[str, object],
    strategic_spines: gpd.GeoDataFrame,
    config_directory: Path,
    as_at: date | None,
    school_register_max_age_days: int | None,
    strategic_admissions_max_age_days: int | None,
) -> PreferredAlignmentPipelineResult:
    """Prepare finite strategic options and verify declared evidence inputs.

    The selection core is invoked only after this adapter has a declared
    as-at date and all three evidence themes.  A local user can always inspect
    the candidate options, but absent evidence remains explicit rather than
    being converted into an apparently decisive choice.
    """

    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    options = strategic_alignment_options(strategic_spines, profile)
    missing: list[str] = []
    if as_at is None:
        missing.append("network-selection-as-at")
    if school_register_max_age_days is None:
        missing.append("network-selection-school-register-max-age-days")
    if strategic_admissions_max_age_days is None:
        missing.append("network-selection-strategic-admissions-max-age-days")
    population_config = source.get("population_reach_evidence_config")
    school_config = source.get("school_register_evidence_config")
    admissions_config = source.get("strategic_education_destination_admissions_config")
    # Compiler callers normally pass source configuration through the explicit
    # keyword adapters below.  These fields are intentionally unsupported in
    # a source frame: a GeoDataFrame is not a governed configuration object.
    if population_config is not None or school_config is not None or admissions_config is not None:
        raise TypeError("PSA evidence configuration belongs to AreaConfig, not source frames")

    # The compiler supplies validated SourceConfig values via these reserved
    # non-spatial entries.  Keeping them out of GeoDataFrame handling prevents
    # a route artifact from masquerading as governed evidence configuration.
    configuration = source.get("_network_selection_configuration")
    if not isinstance(configuration, dict):
        missing.extend(
            [
                "population-reach-evidence",
                "school-register-evidence",
                "strategic-destination-admissions",
            ]
        )
        return _incomplete(profile, options, missing, "no governed PSA evidence configuration")

    population = configuration.get("population_reach_evidence")
    schools = configuration.get("school_register_evidence")
    admissions = configuration.get("strategic_education_destination_admissions")
    if population is None:
        missing.append("population-reach-evidence")
    if schools is None:
        missing.append("school-register-evidence")
    if admissions is None:
        missing.append("strategic-destination-admissions")
    if missing:
        return _incomplete(profile, options, missing, "governed evidence declaration is incomplete")

    assert as_at is not None
    try:
        population_evidence = load_population_reach_evidence(
            population,
            base_directory=config_directory,
            pwc_outside_tolerance_m=0,
        )
        education_evidence = load_education_access_evidence(
            schools,
            admissions,
            base_directory=config_directory,
            as_at=as_at,
            school_register_max_age_days=school_register_max_age_days,
            strategic_admissions_max_age_days=strategic_admissions_max_age_days,
        )
    except GovernedEvidenceLoadError as error:
        return _incomplete(
            profile,
            options,
            ("governed-evidence-validation",),
            str(error),
        )
    if population_evidence is None or education_evidence is None:
        return _incomplete(
            profile,
            options,
            ("governed-evidence-load",),
            "loader returned no evidence",
        )
    return _ready(profile, options, population_evidence, education_evidence, as_at)


def strategic_alignment_options(
    strategic_spines: gpd.GeoDataFrame,
    profile: NetworkSelectionProfile,
) -> gpd.GeoDataFrame:
    """Return only finite, material strategic-spine corridor candidates.

    Current NCN/Greenway labels alone are not promoted to
    ``verified-existing-asset``: that class requires the separate governed
    availability/reuse evidence contract.  An A-road spine is a corridor
    candidate; every remaining known strategic spine is ``other-routable``.
    """

    columns = [
        "option_id",
        "source_class",
        "source_spine_id",
        "directness_m",
        "topography_status",
        "provenance_fingerprint",
        "geometry",
    ]
    if strategic_spines.empty:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=strategic_spines.crs)
    rows: list[dict[str, object]] = []
    for _, spine in strategic_spines.sort_values("spine_id").iterrows():
        geometry = _continuous_line(spine.geometry)
        if geometry is None:
            continue
        spine_kind = str(spine.get("spine_kind") or "")
        source_class = (
            CandidateSourceClass.A_ROAD_CORRIDOR.value
            if spine_kind == "a-road"
            else CandidateSourceClass.OTHER_ROUTABLE.value
        )
        if CandidateSourceClass(source_class) not in profile.candidate_source_precedence:
            continue
        provenance = _fingerprint(
            {
                "spine_id": str(spine["spine_id"]),
                "spine_kind": spine_kind,
                "source_ids": str(spine.get("source_ids") or ""),
                "geometry_wkb": geometry.wkb_hex,
            }
        )
        rows.append(
            {
                "option_id": f"strategic-spine-{spine['spine_id']!s}",
                "source_class": source_class,
                "source_spine_id": str(spine["spine_id"]),
                "directness_m": _metric_length_m(geometry, strategic_spines.crs),
                "topography_status": str(spine.get("topography_evidence_status") or "unknown"),
                "provenance_fingerprint": provenance,
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=strategic_spines.crs)


def _continuous_line(geometry: object) -> LineString | None:
    if isinstance(geometry, LineString) and not geometry.is_empty and len(geometry.coords) >= 2:
        return geometry
    if isinstance(geometry, MultiLineString) and not geometry.is_empty:
        merged = linemerge(geometry)
        if isinstance(merged, LineString) and len(merged.coords) >= 2:
            return merged
    return None


def _metric_length_m(geometry: LineString, crs: object) -> float:
    return float(gpd.GeoSeries([geometry], crs=crs).to_crs(27700).length.iloc[0])


def _incomplete(
    profile: NetworkSelectionProfile,
    options: gpd.GeoDataFrame,
    missing: tuple[str, ...] | list[str],
    rationale: str,
) -> PreferredAlignmentPipelineResult:
    ordered_missing = tuple(sorted(set(missing)))
    return PreferredAlignmentPipelineResult(
        profile_fingerprint=profile.fingerprint,
        activation="governed-evidence-incomplete",
        candidate_options=options,
        missing_inputs=ordered_missing,
        evidence_fingerprints=(),
        diagnostics={
            "rationale": rationale,
            "candidate_option_count": len(options),
            "candidate_source_classes": tuple(sorted(set(options.get("source_class", [])))),
            "replay_directive": "recompile-whole-network-on-ledger-change",
        },
    )


def _ready(
    profile: NetworkSelectionProfile,
    options: gpd.GeoDataFrame,
    population: PopulationReachEvidenceLoad,
    education: EducationAccessEvidenceLoad,
    as_at: date,
) -> PreferredAlignmentPipelineResult:
    fingerprints = tuple(
        sorted(
            {
                population.frame_content_sha256,
                education.governed_source_fingerprint,
                *(item.content_sha256 for item in population.artifact_lineage),
                *(item.content_sha256 for item in education.artifact_lineage),
            }
        )
    )
    return PreferredAlignmentPipelineResult(
        profile_fingerprint=profile.fingerprint,
        activation="governed-evidence-ready",
        candidate_options=options,
        missing_inputs=(),
        evidence_fingerprints=fingerprints,
        diagnostics={
            "as_at": as_at.isoformat(),
            "candidate_option_count": len(options),
            "population_evidence": "verified",
            "education_evidence": "verified",
            "existing_alignment_advantage": (
                "not-derived-without-governed-status-and-reuse-evidence"
            ),
            "replay_directive": "recompile-whole-network-on-ledger-change",
        },
    )
