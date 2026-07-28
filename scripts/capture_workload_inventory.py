#!/usr/bin/env python3
"""Read-only inventory for the B&NES/WECA benchmark corpus.

This deliberately does not invoke the SATN compiler, snapshotter, downloader,
or publisher.  It binds an observed local artifact set to file digests, byte
counts, and GeoJSON feature counts so a separate timed run can name its inputs
without rebuilding an area merely to inspect it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _geojson_features(path: Path) -> int | None:
    if path.suffix != ".geojson":
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    features = payload.get("features") if isinstance(payload, dict) else None
    return len(features) if isinstance(features, list) else None


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    feature_count = _geojson_features(path)
    if feature_count is not None:
        record["feature_count"] = feature_count
    return record


def _files_below(path: Path) -> Iterator[Path]:
    if path.is_dir():
        yield from (candidate for candidate in sorted(path.rglob("*")) if candidate.is_file())


def _snapshot_records(root: Path, snapshot: Path) -> list[dict[str, Any]]:
    manifest_path = snapshot / "snapshot.json"
    if not manifest_path.is_file():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = {"snapshot.json"}
    if isinstance(manifest, dict):
        for key in ("files", "provenance_file_sha256"):
            value = manifest.get(key, {})
            if isinstance(value, (dict, list)):
                names.update(name for name in value if isinstance(name, str))
    return [
        _file_record(root, snapshot / name)
        for name in sorted(names)
        if (snapshot / name).is_file()
    ]


def _deployment_summary(root: Path, deployment: str) -> dict[str, Any]:
    deployment_dir = root / "build" / "deployments" / deployment
    compiler_run = deployment_dir / "compiler-run.json"
    publication = deployment_dir / "publication.json"
    result: dict[str, Any] = {
        "directory": str(deployment_dir.relative_to(root)),
        "total_bytes": sum(path.stat().st_size for path in _files_below(deployment_dir)),
        "largest_files": sorted(
            (_file_record(root, path) for path in _files_below(deployment_dir)),
            key=lambda record: int(record["bytes"]),
            reverse=True,
        )[:12],
    }
    if compiler_run.is_file():
        run = json.loads(compiler_run.read_text(encoding="utf-8"))
        result["compiler_run"] = {
            key: run.get(key)
            for key in (
                "run_id",
                "status",
                "connection_count",
                "gap_count",
                "area_definition_sha256",
                "snapshot_manifest_sha256",
                "governed_input_fingerprint",
                "compilation_input_fingerprint",
                "elevation_evidence_status",
                "urban_classification_status",
                "layer_counts",
            )
        }
        result["compiler_run"]["record"] = _file_record(root, compiler_run)
    if publication.is_file():
        result["publication_record"] = _file_record(root, publication)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="Repository root to inspect"
    )
    parser.add_argument(
        "--deployment", action="append", default=[], help="Deployment name (repeatable)"
    )
    parser.add_argument(
        "--snapshot",
        action="append",
        default=[],
        help="Snapshot ID under data/snapshots (repeatable)",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    result = {
        "schema_version": "workload-inventory/v1",
        "repository_root": str(root),
        "deployments": {name: _deployment_summary(root, name) for name in args.deployment},
        "snapshots": {
            name: _snapshot_records(root, root / "data" / "snapshots" / name)
            for name in args.snapshot
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
