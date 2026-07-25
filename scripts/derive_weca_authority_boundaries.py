"""Create the governed four-authority boundary artifact consumed by WECA EA acquisition.

The caller supplies an already-downloaded authoritative boundary dataset.  This
script never guesses boundaries from labels: it retains the source stable ID,
the exact source query/provenance string, and only the four constituent WECA
authorities in EPSG:27700.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

from satn.ea_elevation import canonical_polygon_geometry

WECA_AUTHORITIES = (
    "Bath and North East Somerset",
    "Bristol",
    "North Somerset",
    "South Gloucestershire",
)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").lower().replace("&", "and").split())


def derive(
    source: Path,
    output: Path,
    *,
    name_field: str,
    id_field: str,
    source_query: str,
) -> None:
    if not source_query.strip():
        raise ValueError("source query/provenance is required")
    frame = gpd.read_file(source)
    if frame.crs is None or name_field not in frame or id_field not in frame:
        raise ValueError("boundary source requires CRS plus configured name and stable ID fields")
    expected = {_normalise(name): name for name in WECA_AUTHORITIES}
    records = []
    for _, row in frame.to_crs(27700).iterrows():
        name = expected.get(_normalise(row[name_field]))
        if name is None or row.geometry is None or row.geometry.is_empty:
            continue
        # Reject geometry we cannot canonically bind, even though GeoJSON could
        # serialise it.  This makes the later authority digest meaningful.
        canonical_polygon_geometry(row.geometry)
        identifier = str(row[id_field]).strip()
        if not identifier:
            raise ValueError(f"WECA authority {name} has no stable source ID")
        records.append(
            {
                "authority": name,
                "authority_id": identifier,
                "source_query": source_query,
                "geometry": row.geometry,
            }
        )
    names = {str(record["authority"]) for record in records}
    if names != set(WECA_AUTHORITIES) or len(records) != 4:
        raise ValueError("source does not contain exactly the four WECA authorities")
    if len({str(record["authority_id"]) for record in records}) != 4:
        raise ValueError("WECA authority source IDs must be unique")
    output.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(records, geometry="geometry", crs=27700).sort_values(
        "authority_id"
    ).to_crs(4326).to_file(output, driver="GeoJSON")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--name-field", required=True)
    parser.add_argument("--id-field", required=True)
    parser.add_argument("--source-query", required=True)
    args = parser.parse_args()
    derive(
        args.source,
        args.output,
        name_field=args.name_field,
        id_field=args.id_field,
        source_query=args.source_query,
    )


if __name__ == "__main__":
    main()
