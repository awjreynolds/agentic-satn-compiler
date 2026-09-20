from __future__ import annotations

import copy
import hashlib
import json
import shutil

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString
from test_recompilation import prepared_config

import satn.compiler as compiler_module
import satn.pipeline as pipeline_module
from satn import compile
from satn.compiled_network_bundle import BundleCodecError, decode_geodataframe, encode_geodataframe
from satn.retained_artifacts import RetainedArtifactStore
from satn.routable_edge_enrichment import (
    decode_routable_edge_enrichment,
    encode_routable_edge_enrichment,
    policy_fingerprint,
)


def _frame(order: tuple[int, ...] = (0, 1)) -> gpd.GeoDataFrame:
    rows = [
        {
            "source_id": f"edge-{index}",
            "u": index,
            "v": index + 1,
            "key": 0,
            "length": float(index + 1),
            "satn_ncn": bool(index),
            "geometry": LineString([(index, 0), (index + 1, 0)]),
        }
        for index in order
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:27700")


def _identities() -> dict[str, str]:
    return {
        "snapshot_manifest_sha256": "a" * 64,
        "area_identity": "b" * 64,
        "network_identity": "c" * 64,
        "context_identity": "d" * 64,
        "policy_fingerprint": policy_fingerprint(),
        "implementation_identity": "e" * 64,
        "dependency_identity": "f" * 64,
    }


def test_edge_enrichment_fingerprint_uses_explicit_compiler_revision(monkeypatch) -> None:
    original = pipeline_module._edge_enrichment_implementation_fingerprint()
    monkeypatch.setattr(
        pipeline_module.compilation_dependencies,
        "COMPILER_CACHE_REVISION",
        "poc-test-change",
    )

    assert pipeline_module._edge_enrichment_implementation_fingerprint() != original


def test_marked_network_wire_is_canonical_and_row_order_independent() -> None:
    identities = _identities()
    forward = encode_routable_edge_enrichment(_frame(), identities=identities)
    reverse = encode_routable_edge_enrichment(_frame((1, 0)), identities=identities)

    assert forward == reverse
    restored = decode_routable_edge_enrichment(forward, identities=identities)
    assert list(restored.columns) == list(_frame().columns)
    assert restored.crs.to_epsg() == 27700
    assert restored["satn_ncn"].dtype == bool
    assert restored.geometry.to_wkb().tolist() == (
        _frame().sort_values("source_id").geometry.to_wkb().tolist()
    )


def test_marked_network_wire_round_trips_primitive_ndarray_cells() -> None:
    identities = _identities()
    frame = _frame()
    frame["highway"] = np.asarray(
        [np.array(["cycleway"], dtype="<U8"), np.array(["track", "path"], dtype="<U5")],
        dtype=object,
    )

    payload = encode_routable_edge_enrichment(frame, identities=identities)
    restored = decode_routable_edge_enrichment(payload, identities=identities)

    for expected, actual in zip(frame["highway"], restored["highway"], strict=True):
        assert isinstance(actual, np.ndarray)
        assert actual.dtype == expected.dtype
        assert actual.shape == expected.shape
        np.testing.assert_array_equal(actual, expected)
    assert encode_routable_edge_enrichment(restored, identities=identities) == payload


@pytest.mark.parametrize("dtype", [object, "<M8[ns]", "|V4", [("field", "i4")]])
def test_marked_network_wire_rejects_unsafe_ndarray_dtypes(dtype: object) -> None:
    frame = _frame()
    frame["highway"] = pd.Series([np.empty(1, dtype=dtype), np.empty(1, dtype=dtype)], dtype=object)
    with pytest.raises(BundleCodecError, match="unsupported ndarray dtype"):
        encode_routable_edge_enrichment(frame, identities=_identities())


def test_ndarray_wire_rejects_malformed_payload() -> None:
    frame = _frame()
    frame["highway"] = pd.Series(
        [np.array(["cycleway"], dtype="<U8"), np.array(["track"], dtype="<U5")], dtype=object
    )
    wire = encode_geodataframe(frame, stable_key_columns=("source_id", "u", "v", "key"))
    highway_index = next(
        index for index, column in enumerate(wire["columns"]) if column["name"] == "highway"
    )
    cell = wire["rows"][0]["cells"][highway_index]
    cell["value"] = "AA=="
    wire["rows"][0]["content_sha256"] = hashlib.sha256(
        json.dumps(
            wire["rows"][0]["cells"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    body = {key: value for key, value in wire.items() if key != "content_sha256"}
    wire["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()

    with pytest.raises(BundleCodecError, match="byte length"):
        decode_geodataframe(wire)


def test_marked_network_wire_rejects_corruption_and_forged_identity() -> None:
    identities = _identities()
    payload = encode_routable_edge_enrichment(_frame(), identities=identities)
    corrupted = copy.deepcopy(payload)
    corrupted["frame"]["rows"][0]["cells"][0]["value"] = {"forged": True}
    with pytest.raises(BundleCodecError):
        decode_routable_edge_enrichment(corrupted, identities=identities)

    forged = copy.deepcopy(payload)
    forged["identities"]["network_identity"] = "0" * 64
    with pytest.raises(BundleCodecError):
        decode_routable_edge_enrichment(forged, identities=identities)


def test_routing_replay_bypasses_mark_ncn_edges(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    shutil.rmtree(config.publication.output_dir)
    config.compilation.criteria_version = "edge-enrichment-replay"

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("validated edge enrichment must bypass mark_ncn_edges")

    monkeypatch.setattr(compiler_module, "mark_ncn_edges", forbidden)
    replayed = compile(config, artifact_root=artifact_root)

    assert replayed.metadata["edge_enrichment_disposition"] == "hit"
    assert replayed.metadata["routing_bundle_disposition"] == "hit"
    assert replayed.metadata["edge_enrichment_artifact_id"]
    assert (
        replayed.metadata["routing_bundle_artifact_id"]
        == cold.metadata["routing_bundle_artifact_id"]
    )


def test_routing_only_rebuild_hits_edge_enrichment(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    compile(config, artifact_root=artifact_root)
    shutil.rmtree(config.publication.output_dir)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("routing-only rebuild must replay edge enrichment")

    monkeypatch.setattr(compiler_module, "mark_ncn_edges", forbidden)
    rebuilt = compile(
        config,
        artifact_root=artifact_root,
        rebuild_stages=("routing-assembly",),
    )

    assert rebuilt.metadata["edge_enrichment_disposition"] == "hit"
    assert rebuilt.metadata["routing_bundle_disposition"] == "build"


def test_route_and_semantic_artifacts_have_exact_edge_lineage(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    store = RetainedArtifactStore(artifact_root)
    route = store.resolve(cold.metadata["routing_bundle_artifact_id"]).artifact
    semantic = store.resolve(cold.metadata["semantic_bundle_artifact_id"]).artifact
    assert route is not None and semantic is not None
    assert route.manifest.upstream_artifact_ids == (cold.metadata["edge_enrichment_artifact_id"],)
    assert semantic.manifest.upstream_artifact_ids == (cold.metadata["routing_bundle_artifact_id"],)


def test_targeted_edge_rebuild_forces_descendants(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    shutil.rmtree(config.publication.output_dir)
    rebuilt = compile(
        config,
        artifact_root=artifact_root,
        rebuild_stages=("edge-enrichments",),
    )

    assert rebuilt.metadata["edge_enrichment_disposition"] == "build"
    assert rebuilt.metadata["routing_bundle_disposition"] == "build"
    assert rebuilt.metadata["semantic_bundle_disposition"] == "build"
    assert (
        rebuilt.metadata["edge_enrichment_artifact_id"]
        == cold.metadata["edge_enrichment_artifact_id"]
    )


def test_corrupt_edge_enrichment_is_quarantined_and_rebuilt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    edge_id = cold.metadata["edge_enrichment_artifact_id"]
    artifact = RetainedArtifactStore(artifact_root).resolve(edge_id).artifact
    assert artifact is not None
    output = artifact.path / "outputs" / "routable-network"
    output.write_bytes(output.read_bytes() + b"corrupt")
    shutil.rmtree(config.publication.output_dir)
    config.compilation.criteria_version = "corrupt-edge-rebuild"

    rebuilt = compile(config, artifact_root=artifact_root)

    assert rebuilt.metadata["edge_enrichment_disposition"] == "build"
    assert any(path.name.startswith(edge_id) for path in (artifact_root / "quarantine").iterdir())


def test_changed_context_misses_edge_enrichment_identity(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    shutil.rmtree(config.publication.output_dir)
    config.compilation.criteria_version = "changed-context-edge"
    original = __import__("satn.pipeline", fromlist=["load_snapshot"]).load_snapshot

    def changed_snapshot(council):  # type: ignore[no-untyped-def]
        source = original(council)
        context = source["context"].copy(deep=True)
        if not context.empty:
            context.loc[context.index[0], "name"] = "changed-context"
        source["context"] = context
        return source

    monkeypatch.setattr("satn.pipeline.load_snapshot", changed_snapshot)
    rebuilt = compile(config, artifact_root=artifact_root)

    assert rebuilt.metadata["edge_enrichment_disposition"] == "build"
    assert (
        rebuilt.metadata["edge_enrichment_artifact_id"]
        != cold.metadata["edge_enrichment_artifact_id"]
    )


def test_edge_identity_failure_does_not_create_rootless_descendants(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ValueError("synthetic edge identity failure")

    monkeypatch.setattr("satn.pipeline._edge_enrichment_identities", unavailable)
    result = compile(config, artifact_root=tmp_path / "retained")

    assert result.status == "complete"
    assert result.metadata["edge_enrichment_disposition"] == "unavailable"
    assert result.metadata["routing_bundle_disposition"] == "unavailable"
    assert result.metadata["semantic_bundle_disposition"] == "unavailable"
    assert "routing_bundle_artifact_id" not in result.metadata
    assert "semantic_bundle_artifact_id" not in result.metadata


def test_edge_wire_identities_must_match_manifest_parameters(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    store = RetainedArtifactStore(artifact_root)
    artifact = store.resolve(cold.metadata["edge_enrichment_artifact_id"]).artifact
    assert artifact is not None
    path = artifact.path / "outputs" / "routable-network"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["identities"]["network_identity"] = "0" * 64
    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    payload["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assert pipeline_module._decode_retained_edge_enrichment(store, artifact) is None
