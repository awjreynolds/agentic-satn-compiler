"""Public compiler seam tests for the optional Preferred Alignment pass."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString

from satn.models import SourceConfig
from satn.network_selection import NetworkSelectionProfile
from satn.preferred_alignment_pipeline import (
    compile_preferred_alignment_pipeline,
    strategic_alignment_options,
)


def profile() -> NetworkSelectionProfile:
    return NetworkSelectionProfile.model_validate(
        {
            "profile_id": "public-compiler-integration-v1",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )


def spines() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "spine_id": "a4-corridor",
                "spine_kind": "a-road",
                "source_ids": "[\"a4\"]",
                "topography_evidence_status": "available",
                "geometry": LineString([(400000, 170000), (401000, 170000)]),
            },
            {
                "spine_id": "ncn-connector",
                "spine_kind": "ncn",
                "source_ids": "[\"ncn-1\"]",
                "topography_evidence_status": "available",
                "geometry": LineString([(400000, 170100), (401000, 170100)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def test_existing_status_is_not_promoted_to_verified_asset_without_reuse_evidence() -> None:
    options = strategic_alignment_options(spines(), profile())

    assert list(options["source_class"]) == ["a-road-corridor", "other-routable"]
    assert "verified-existing-asset" not in set(options["source_class"])
    assert (options["directness_m"] > 0).all()


def test_incomplete_config_is_reviewable_and_does_not_load_or_start_agent_work() -> None:
    result = compile_preferred_alignment_pipeline(
        profile(),
        source={},
        strategic_spines=spines(),
        config_directory=Path.cwd(),
        as_at=None,
        school_register_max_age_days=None,
        strategic_admissions_max_age_days=None,
    )

    assert result.activation == "governed-evidence-incomplete"
    assert result.missing_inputs == (
        "network-selection-as-at",
        "network-selection-school-register-max-age-days",
        "network-selection-strategic-admissions-max-age-days",
        "population-reach-evidence",
        "school-register-evidence",
        "strategic-destination-admissions",
    )
    assert result.diagnostics["replay_directive"] == "recompile-whole-network-on-ledger-change"


def test_new_assessment_date_is_optional_and_legacy_serialisation_is_unchanged() -> None:
    source = SourceConfig(snapshot_dir=Path("snapshots"))

    assert source.network_selection_as_at is None
    assert "network_selection_as_at" not in source.model_dump(mode="json")
    configured = SourceConfig(
        snapshot_dir=Path("snapshots"),
        network_selection_as_at=date(2026, 7, 26),
    )
    assert configured.model_dump(mode="json")["network_selection_as_at"] == "2026-07-26"
