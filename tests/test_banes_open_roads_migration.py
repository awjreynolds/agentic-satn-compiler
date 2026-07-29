from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd

PROJECT = Path(__file__).parents[1]
GOVERNED = PROJECT / "data" / "governed"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_banes_v1_migration_receipt_matches_unchanged_governed_sources() -> None:
    legacy_path = GOVERNED / "banes-os-open-roads-2026-04-07.geojson"
    source_path = GOVERNED / "weca-os-open-roads-2026-04-07.geojson"
    receipt = json.loads(
        (GOVERNED / "banes-open-roads-v1-migration.json").read_text(
            encoding="utf-8"
        )
    )
    legacy = gpd.read_file(legacy_path).set_index("id")
    source = gpd.read_file(source_path).set_index("id")
    selected = source.loc[legacy.index]

    assert receipt["contract"] == "satn-banes-open-roads-source-migration/v1"
    assert receipt["legacy"]["raw_sha256"] == _sha256(legacy_path) == (
        "bb39710d078c52366fc0c75205cbd396db6d408f96f43bfb216ba15613d82f5b"
    )
    assert receipt["source_export"]["raw_sha256"] == _sha256(source_path) == (
        "87c944fb4c4f77c949f25913c58b3e7f49df80bbe0bf317606b32feb0653e89c"
    )
    assert receipt["source_export"]["path"] == source_path.name
    assert receipt["source_export"]["declared_crs"] == "EPSG:4326"
    assert receipt["source_export"]["schema"] == [
        "id",
        "road_classification",
        "road_function",
        "road_classification_number",
        "name_1",
    ]
    assert receipt["selection"] == {
        "contract": "satn-official-road-boundary-selection/v1",
        "predicate": "intersects",
        "geometry_treatment": "retain-whole-source-feature",
        "selected_feature_count": 10776,
        "stable_id_mismatch_count": 0,
        "classification_mismatch_count": 0,
        "changed_geometry_count": 136,
    }
    assert len(legacy) == len(selected) == 10776
    assert legacy.index.is_unique and source.index.is_unique
    assert legacy.index.difference(source.index).empty
    assert int(
        (legacy["road_classification"] != selected["road_classification"]).sum()
    ) == 0
    assert selected.geometry.geom_type.value_counts().to_dict() == {
        "LineString": 10776
    }
    assert (
        sum(
            not old.equals_exact(new, tolerance=1e-12)
            for old, new in zip(
                legacy.geometry,
                selected.geometry,
                strict=True,
            )
        )
        == 136
    )
