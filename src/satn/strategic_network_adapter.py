"""Thin compatibility adapter for prepared effective-network inputs.

Canonical graph construction, candidate discovery and officer/compiler policy
live in :mod:`satn.effective_strategic_network`.  This module retains the old
path-based entrypoint and helper names for existing callers.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import geopandas as gpd

from satn.effective_strategic_network import (
    EffectiveStrategicNetworkRequest,
    EffectiveStrategicNetworkState,
    compile_effective_strategic_network,
)
from satn.effective_strategic_network import (
    _compiler_preferences as _canonical_compiler_preferences,
)
from satn.effective_strategic_network import (
    _officer_choices as _canonical_officer_choices,
)
from satn.effective_strategic_network import (
    discovery_from_preparation as _canonical_discovery_from_preparation,
)
from satn.effective_strategic_network import (
    planning_graph_from_compiler_edges as _canonical_planning_graph_from_compiler_edges,
)
from satn.strategic_corridors import StrategicCorridorPreparationResult
from satn.strategic_network_planning import StrategicNetworkPlanningResult


def planning_graph_from_compiler_edges(
    routable_network: gpd.GeoDataFrame,
    *,
    source_export_fingerprint: str,
):
    """Compatibility re-export for the canonical graph builder."""

    return _canonical_planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint=source_export_fingerprint,
    )


def discovery_from_preparation(preparation, graph):
    """Compatibility re-export for canonical candidate discovery."""

    return _canonical_discovery_from_preparation(preparation, graph)


def _compiler_preferences(preparation, candidate_sets=()):
    """Compatibility re-export for canonical compiler preference policy."""

    return _canonical_compiler_preferences(preparation, candidate_sets)


def _officer_choices(preparation, decisions, candidate_sets=()):
    """Compatibility re-export for canonical officer-choice translation."""

    return _canonical_officer_choices(preparation, decisions, candidate_sets)


def compile_prepared_effective_strategic_network(
    *,
    routable_network: gpd.GeoDataFrame,
    preparation: StrategicCorridorPreparationResult,
    snapshot_manifest_path: Path,
    area_definition_path: Path,
    officer_decisions: tuple[object, ...] = (),
    urban_spines: gpd.GeoDataFrame | None = None,
) -> EffectiveStrategicNetworkState:
    """Hash governed paths, then delegate one complete request to the seam."""

    if not snapshot_manifest_path.is_file() or not area_definition_path.is_file():
        return EffectiveStrategicNetworkState.unavailable()
    source_fingerprint = hashlib.sha256(snapshot_manifest_path.read_bytes()).hexdigest()
    area_fingerprint = hashlib.sha256(area_definition_path.read_bytes()).hexdigest()
    return compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=routable_network,
            preparation=preparation,
            area_fingerprint=area_fingerprint,
            snapshot_fingerprint=source_fingerprint,
            officer_decisions=officer_decisions,
            urban_spines=urban_spines,
        )
    )


def compile_prepared_strategic_network(
    *,
    routable_network: gpd.GeoDataFrame,
    preparation: StrategicCorridorPreparationResult,
    snapshot_manifest_path: Path,
    area_definition_path: Path,
    officer_decisions: tuple[object, ...] = (),
    urban_spines: gpd.GeoDataFrame | None = None,
) -> StrategicNetworkPlanningResult | None:
    """Legacy result view; selection is performed only by the canonical seam."""

    return compile_prepared_effective_strategic_network(
        routable_network=routable_network,
        preparation=preparation,
        snapshot_manifest_path=snapshot_manifest_path,
        area_definition_path=area_definition_path,
        officer_decisions=officer_decisions,
        urban_spines=urban_spines,
    ).result


__all__ = [
    "compile_prepared_effective_strategic_network",
    "compile_prepared_strategic_network",
    "discovery_from_preparation",
    "planning_graph_from_compiler_edges",
]
