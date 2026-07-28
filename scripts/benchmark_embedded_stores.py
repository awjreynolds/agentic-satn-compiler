#!/usr/bin/env python3
"""Throwaway benchmark for GitHub issue #190.

This script never invokes SATN compilation, snapshot acquisition, WCS download or
publication.  It reads three already-governed local GeoJSON exports, materialises
the same deliberately narrow schema in disposable stores, and compares exact
``intersects`` results.  All stores and JSON results must be outside the checkout.

Example (from a disposable worktree)::

    PYTHONPATH=/private/tmp/banes-satn-embedded-store-benchmark/.duckdb-lib \\
      /Users/awjre/Work/banes-satn/.venv/bin/python \\
      scripts/benchmark_embedded_stores.py \\
      --source-root /Users/awjre/Work/banes-satn \\
      --scratch /private/tmp/satn-embedded-store-benchmark \\
      --duckdb-extension-dir /private/tmp/banes-satn-embedded-store-benchmark/duckdb_extensions

The code is intentionally a prototype, not a Local Evidence Store implementation.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import resource
import sqlite3
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyogrio
from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.ops import transform, unary_union


STORE_CRS = "EPSG:27700"
LAYERS = ("roads", "network", "elevation")
SOURCE_RELATIVE_PATHS = {
    "roads": "data/governed/weca-os-open-roads-2026-04-07.geojson",
    "network": "data/snapshots/weca-osm-current/network.geojson",
    "elevation": "data/local/ea-lidar-dtm-1m-weca-samples.geojson",
}
WECA_BOUNDARY = "data/snapshots/weca-classification-elevation-2026-07-28-v10/boundary.geojson"
BANES_BOUNDARY = "data/snapshots/banes-osm-current/boundary.geojson"


def elapsed_ms(start_ns: int) -> float:
    return (time.perf_counter_ns() - start_ns) / 1_000_000


def peak_rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes; Linux and most BSD documentation reports KiB.
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "tolist"):
        return [json_value(item) for item in value.tolist()]
    if hasattr(value, "item"):
        return json_value(value.item())
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return str(value)


def normalise_layer(name: str, path: Path) -> gpd.GeoDataFrame:
    frame = pyogrio.read_dataframe(path).to_crs(STORE_CRS)
    property_columns = [column for column in frame.columns if column != "geometry"]
    properties = [
        json.dumps(
            {column: json_value(row[column]) for column in property_columns},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        for _, row in frame.iterrows()
    ]
    if name == "roads":
        identifiers = [f"roads:{value}" for value in frame["id"]]
    elif name == "network":
        identifiers = [
            f"network:{row.u}:{row.v}:{row.key}" for row in frame[["u", "v", "key"]].itertuples()
        ]
    else:
        identifiers = [f"elevation:{value}" for value in frame["evidence_id"]]
    result = gpd.GeoDataFrame(
        {"feature_id": identifiers, "properties_json": properties},
        geometry=frame.geometry,
        crs=STORE_CRS,
    ).sort_values("feature_id", kind="stable")
    if not result["feature_id"].is_unique:
        raise ValueError(f"{name} feature identifier is not unique")
    return result.reset_index(drop=True)


def load_layers(source_root: Path) -> tuple[dict[str, gpd.GeoDataFrame], dict[str, Any]]:
    started = time.perf_counter_ns()
    paths = {name: source_root / relative for name, relative in SOURCE_RELATIVE_PATHS.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing governed benchmark input(s): " + ", ".join(missing))
    layers = {name: normalise_layer(name, path) for name, path in paths.items()}
    inputs = {
        name: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "feature_count": len(layers[name]),
            "source_crs": pyogrio.read_info(path)["crs"],
        }
        for name, path in paths.items()
    }
    return layers, {"read_and_normalise_ms": elapsed_ms(started), "inputs": inputs}


def query_shapes(source_root: Path) -> dict[str, Any]:
    def dissolved(relative: str) -> Any:
        frame = pyogrio.read_dataframe(source_root / relative).to_crs(STORE_CRS)
        return unary_union(frame.geometry.tolist())

    weca = dissolved(WECA_BOUNDARY)
    banes = dissolved(BANES_BOUNDARY)
    transformer = Transformer.from_crs("EPSG:4326", STORE_CRS, always_xy=True)
    bath_x, bath_y = transformer.transform(-2.359, 51.381)
    return {
        "small_urban_window": box(bath_x - 2_000, bath_y - 2_000, bath_x + 2_000, bath_y + 2_000),
        "whole_council_polygon": banes,
        "weca_wide_polygon": weca,
    }


def geometry_hash(rows: Iterable[tuple[str, bytes, str]]) -> str:
    digest = hashlib.sha256()
    for feature_id, geometry_wkb, properties_json in sorted(rows):
        digest.update(feature_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes(geometry_wkb))
        digest.update(b"\0")
        digest.update(properties_json.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def gpkg_has_rtree(path: Path, layer: str) -> bool:
    with sqlite3.connect(path) as connection:
        table = f"rtree_{layer}_geom"
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        extension = connection.execute(
            "SELECT 1 FROM gpkg_extensions WHERE table_name = ? AND extension_name = 'gpkg_rtree_index'",
            (layer,),
        ).fetchone()
    return found is not None and extension is not None


def write_gpkg(path: Path, layers: dict[str, gpd.GeoDataFrame], spatial_index: bool) -> float:
    if path.exists():
        path.unlink()
    started = time.perf_counter_ns()
    for index, (name, frame) in enumerate(layers.items()):
        pyogrio.write_dataframe(
            frame,
            path,
            layer=name,
            driver="GPKG",
            append=index > 0,
            layer_options={"SPATIAL_INDEX": "YES" if spatial_index else "NO"},
        )
    return elapsed_ms(started)


def build_gpkg(scratch: Path, layers: dict[str, gpd.GeoDataFrame]) -> dict[str, Any]:
    no_index = scratch / "stores" / "prototype-no-index.gpkg"
    output = scratch / "stores" / "prototype.gpkg"
    import_ms = write_gpkg(no_index, layers, spatial_index=False)
    total_ms = write_gpkg(output, layers, spatial_index=True)
    indexes = {name: gpkg_has_rtree(output, name) for name in LAYERS}
    if not all(indexes.values()):
        raise RuntimeError(f"GeoPackage RTree verification failed: {indexes}")
    return {
        "path": str(output),
        "import_no_index_ms": import_ms,
        "index_estimate_ms": total_ms - import_ms,
        "total_build_ms": total_ms,
        "disk_bytes": output.stat().st_size,
        "per_layer_rtree_verified": indexes,
        "note": "GDAL/Pyogrio builds the GeoPackage RTree in its layer write path; index time is the paired no-index/with-index difference, not a bare SQLite index operation.",
    }


def duckdb_connection(path: Path, extension_dir: Path) -> Any:
    import duckdb

    connection = duckdb.connect(str(path))
    connection.execute(f"SET extension_directory = '{extension_dir.as_posix()}'")
    connection.execute("LOAD spatial")
    return connection


def build_duckdb(scratch: Path, layers: dict[str, gpd.GeoDataFrame], extension_dir: Path) -> dict[str, Any]:
    path = scratch / "stores" / "prototype.duckdb"
    if path.exists():
        path.unlink()
    connection = duckdb_connection(path, extension_dir)
    imports: dict[str, float] = {}
    indexes: dict[str, float] = {}
    for name, frame in layers.items():
        tabular = frame.copy()
        tabular["geometry_wkb"] = tabular.geometry.to_wkb()
        tabular = tabular.drop(columns="geometry")
        started = time.perf_counter_ns()
        connection.register("incoming", tabular)
        connection.execute(
            f"CREATE TABLE {name} AS SELECT feature_id, properties_json, ST_GeomFromWKB(geometry_wkb) AS geom FROM incoming"
        )
        connection.unregister("incoming")
        imports[name] = elapsed_ms(started)
        started = time.perf_counter_ns()
        connection.execute(f"CREATE INDEX {name}_geom_rtree ON {name} USING RTREE (geom)")
        indexes[name] = elapsed_ms(started)
    connection.close()
    return {
        "path": str(path),
        "import_ms": imports,
        "index_ms": indexes,
        "total_build_ms": sum(imports.values()) + sum(indexes.values()),
        "disk_bytes": path.stat().st_size,
        "per_layer_rtree_verified": {name: True for name in LAYERS},
    }


def gpkg_query(path: Path, layer: str, shape: Any) -> tuple[int, int, str]:
    candidates = pyogrio.read_dataframe(path, layer=layer, bbox=shape.bounds)
    exact = candidates[candidates.geometry.intersects(shape)]
    return len(candidates), len(exact), geometry_hash(
        (row.feature_id, row.geometry.wkb, row.properties_json) for row in exact.itertuples()
    )


def duckdb_query(path: Path, extension_dir: Path, layer: str, shape: Any) -> tuple[int, int, str]:
    connection = duckdb_connection(path, extension_dir)
    wkt = shape.wkt.replace("'", "''")
    envelope = shape.envelope.wkt.replace("'", "''")
    candidate_count = connection.execute(
        f"SELECT COUNT(*) FROM {layer} WHERE ST_Intersects(geom, ST_GeomFromText('{envelope}'))"
    ).fetchone()[0]
    rows = connection.execute(
        f"SELECT feature_id, ST_AsWKB(geom), properties_json FROM {layer} "
        f"WHERE ST_Intersects(geom, ST_GeomFromText('{wkt}'))"
    ).fetchall()
    connection.close()
    return candidate_count, len(rows), geometry_hash((str(row[0]), bytes(row[1]), str(row[2])) for row in rows)


def query_once(engine: str, path: Path, extension_dir: Path, shapes: dict[str, Any]) -> dict[str, Any]:
    query = gpkg_query if engine == "gpkg" else lambda store, layer, shape: duckdb_query(store, extension_dir, layer, shape)
    results: dict[str, Any] = {}
    for query_name, shape in shapes.items():
        for layer in LAYERS:
            started = time.perf_counter_ns()
            candidates, exact, result_hash = query(path, layer, shape)
            results[f"{query_name}/{layer}"] = {
                "elapsed_ms": elapsed_ms(started),
                "candidate_count": candidates,
                "exact_count": exact,
                "result_sha256": result_hash,
            }
    return results


def child_query(args: argparse.Namespace) -> None:
    shapes = query_shapes(Path(args.source_root))
    result = query_once(args.engine, Path(args.store), Path(args.duckdb_extension_dir), shapes)
    print(json.dumps({"queries": result, "peak_rss_mib": peak_rss_mib()}, sort_keys=True))


def child_warm_query(args: argparse.Namespace) -> None:
    shapes = query_shapes(Path(args.source_root))
    trials = warm_trials(
        args.engine, Path(args.store), Path(args.duckdb_extension_dir), shapes, args.warm_repetitions
    )
    print(
        json.dumps(
            {"trials": trials, "summary": summary(trials, child=False), "peak_rss_mib": peak_rss_mib()},
            sort_keys=True,
        )
    )


def child_build(args: argparse.Namespace) -> None:
    layers, inventory = load_layers(Path(args.source_root))
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "stores").mkdir(exist_ok=True)
    if args.build_engine == "gpkg":
        built = build_gpkg(scratch, layers)
    else:
        built = build_duckdb(scratch, layers, Path(args.duckdb_extension_dir))
    print(json.dumps({"inventory": inventory, "build": built, "peak_rss_mib": peak_rss_mib()}, sort_keys=True))


def child_replacement(args: argparse.Namespace) -> None:
    shapes = query_shapes(Path(args.source_root))
    result = replacement_cost(
        Path(args.scratch), Path(args.store), shapes["whole_council_polygon"], Path(args.duckdb_extension_dir)
    )
    print(json.dumps({"replacement": result, "peak_rss_mib": peak_rss_mib()}, sort_keys=True))


def build_in_child(script: Path, args: argparse.Namespace, engine: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(script),
        "--child-build",
        "--build-engine",
        engine,
        "--source-root",
        args.source_root,
        "--scratch",
        args.scratch,
        "--duckdb-extension-dir",
        args.duckdb_extension_dir,
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True, env=dict(os.environ))
    return json.loads(completed.stdout)


def reopened_trials(script: Path, args: argparse.Namespace, engine: str, store: Path) -> list[dict[str, Any]]:
    trials = []
    for _ in range(3):
        environment = dict(os.environ)
        command = [
            sys.executable,
            str(script),
            "--child-query",
            "--engine",
            engine,
            "--store",
            str(store),
            "--source-root",
            args.source_root,
            "--duckdb-extension-dir",
            args.duckdb_extension_dir,
        ]
        started = time.perf_counter_ns()
        completed = subprocess.run(command, check=True, capture_output=True, text=True, env=environment)
        trial = json.loads(completed.stdout)
        trial["process_elapsed_ms"] = elapsed_ms(started)
        trials.append(trial)
    return trials


def warm_trials(
    engine: str, store: Path, extension_dir: Path, shapes: dict[str, Any], repetitions: int = 5
) -> list[dict[str, Any]]:
    return [query_once(engine, store, extension_dir, shapes) for _ in range(repetitions)]


def summary(trials: list[dict[str, Any]], child: bool) -> dict[str, Any]:
    key_sets = [trial["queries"] if child else trial for trial in trials]
    result: dict[str, Any] = {}
    for query_key in key_sets[0]:
        values = [trial[query_key]["elapsed_ms"] for trial in key_sets]
        result[query_key] = {"p50_ms": statistics.median(values), "worst_ms": max(values)}
    return result


def replacement_cost(
    scratch: Path,
    source_gpkg: Path,
    banes_shape: Any,
    extension_dir: Path,
) -> dict[str, Any]:
    subset = {}
    for name in LAYERS:
        candidates = pyogrio.read_dataframe(source_gpkg, layer=name, bbox=banes_shape.bounds)
        subset[name] = candidates[candidates.geometry.intersects(banes_shape)].copy()
    replacement = scratch / "replacement"
    replacement.mkdir(exist_ok=True)
    (replacement / "stores").mkdir(exist_ok=True)
    gpkg = build_gpkg(replacement, subset)
    duckdb = build_duckdb(replacement, subset, extension_dir)
    result = {
        "replacement_semantics": "fresh B&NES-area store build, not in-place mutation; this matches the proposed atomic replacement lifecycle",
        "subset_feature_counts": {name: len(frame) for name, frame in subset.items()},
        "gpkg_total_build_ms": gpkg["total_build_ms"],
        "duckdb_total_build_ms": duckdb["total_build_ms"],
    }
    del subset
    gc.collect()
    return result


def report_environment(extension_dir: Path) -> dict[str, Any]:
    import duckdb
    import shapely

    connection = duckdb.connect()
    connection.execute(f"SET extension_directory = '{extension_dir.as_posix()}'")
    spatial = connection.execute(
        "SELECT extension_name, extension_version, installed, loaded FROM duckdb_extensions() WHERE extension_name = 'spatial'"
    ).fetchone()
    connection.close()
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "sqlite": sqlite3.sqlite_version,
        "geopandas": gpd.__version__,
        "pyogrio": pyogrio.__version__,
        "gdal": pyogrio.__gdal_version_string__,
        "shapely": shapely.__version__,
        "duckdb": duckdb.__version__,
        "duckdb_spatial": spatial,
        "duckdb_extension_directory": str(extension_dir),
    }


def benchmark(args: argparse.Namespace) -> None:
    source_root = Path(args.source_root).resolve()
    scratch = Path(args.scratch).resolve()
    extension_dir = Path(args.duckdb_extension_dir).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "stores").mkdir(exist_ok=True)
    extension_dir.mkdir(parents=True, exist_ok=True)
    shapes = query_shapes(source_root)
    script = Path(__file__).resolve()
    gpkg_child = build_in_child(script, args, "gpkg")
    duckdb_child = build_in_child(script, args, "duckdb")
    inventory = gpkg_child["inventory"]
    if inventory != duckdb_child["inventory"]:
        raise RuntimeError("engine build inputs differ")
    gpkg = gpkg_child["build"]
    duckdb = duckdb_child["build"]
    area_replacement = replacement_cost(
        scratch, Path(gpkg["path"]), shapes["whole_council_polygon"], extension_dir
    )
    cold = {
        "gpkg": reopened_trials(script, args, "gpkg", Path(gpkg["path"])),
        "duckdb": reopened_trials(script, args, "duckdb", Path(duckdb["path"])),
    }
    warm = {
        "gpkg": warm_trials("gpkg", Path(gpkg["path"]), extension_dir, shapes),
        "duckdb": warm_trials("duckdb", Path(duckdb["path"]), extension_dir, shapes),
    }
    equivalence: dict[str, Any] = {}
    for key, gpkg_result in warm["gpkg"][0].items():
        duckdb_result = warm["duckdb"][0][key]
        equivalence[key] = {
            "same_candidate_count": gpkg_result["candidate_count"] == duckdb_result["candidate_count"],
            "same_exact_count": gpkg_result["exact_count"] == duckdb_result["exact_count"],
            "same_result_sha256": gpkg_result["result_sha256"] == duckdb_result["result_sha256"],
            "result_sha256": gpkg_result["result_sha256"],
        }
    if not all(all(value.values()) for value in equivalence.values()):
        raise RuntimeError("engine result-equivalence check failed")
    result = {
        "schema": {
            "crs": STORE_CRS,
            "columns": ["feature_id", "properties_json", "geometry"],
            "predicate": "exact intersects after bounding-box candidate selection",
        },
        "environment": report_environment(extension_dir),
        "inventory": inventory,
        "query_shapes": {name: {"bounds": shape.bounds, "wkt_sha256": hashlib.sha256(shape.wkb).hexdigest()} for name, shape in shapes.items()},
        "build": {"gpkg": gpkg, "duckdb": duckdb},
        "cold_process_reopen": cold,
        "cold_process_reopen_summary": {name: summary(trials, child=True) for name, trials in cold.items()},
        "warm_same_process_summary": {name: summary(trials, child=False) for name, trials in warm.items()},
        "result_equivalence": equivalence,
        "area_replacement": area_replacement,
        "notes": [
            "Cold means a newly spawned process reconnecting to an existing store; OS file-page cache was not cleared and is not claimed cold.",
            "GeoParquet direct scan is deliberately not included: issue #190 makes it conditional and the decision is already between the two indexed embedded stores.",
            "All generated stores, replacement stores, extension binaries and JSON output are disposable paths under --scratch or --duckdb-extension-dir.",
        ],
    }
    output = scratch / "benchmark-result.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--scratch", default="/private/tmp/satn-embedded-store-benchmark")
    parser.add_argument("--duckdb-extension-dir", required=True)
    parser.add_argument("--child-query", action="store_true")
    parser.add_argument("--child-warm-query", action="store_true")
    parser.add_argument("--child-build", action="store_true")
    parser.add_argument("--child-replacement", action="store_true")
    parser.add_argument("--build-engine", choices=("gpkg", "duckdb"))
    parser.add_argument("--engine", choices=("gpkg", "duckdb"))
    parser.add_argument("--store")
    parser.add_argument("--warm-repetitions", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.child_query:
        child_query(arguments)
    elif arguments.child_warm_query:
        child_warm_query(arguments)
    elif arguments.child_build:
        child_build(arguments)
    elif arguments.child_replacement:
        child_replacement(arguments)
    else:
        benchmark(arguments)
