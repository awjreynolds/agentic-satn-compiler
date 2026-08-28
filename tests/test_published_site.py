from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from satn.constants import DISCLAIMER
from satn.deployment import build_area_deployment
from satn.filesystem_safety import publication_destination_authority
from satn.models import CouncilConfig
from satn.pipeline import compile
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


def test_area_deployment_requires_explicit_authority_outside_definition_workspace(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    definition = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(definition)
    compile(definition)
    external = tmp_path / "external-deployment"

    with pytest.raises(ValueError, match="outside the declared publication workspace"):
        build_area_deployment(definition, external)

    assert not external.exists()

    authority = publication_destination_authority(
        workspace_root=fixture,
        approved_external_destination=external,
    )
    assert (
        build_area_deployment(
            definition,
            external,
            publication_authority=authority,
        )
        == external
    )


def test_area_deployment_is_progressive_portable_and_not_git_path_bound(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    definition = CouncilConfig.from_yaml(fixture / "council.yaml")
    definition.publication.output_dir = tmp_path / "compiled"
    definition.source.snapshot_dir = tmp_path / "snapshots"
    authority = publication_destination_authority(
        workspace_root=fixture,
        approved_external_destination=definition.publication.output_dir,
    )

    snapshot(definition)
    result = compile(definition, publication_authority=authority)
    deployment = tmp_path / "deployments" / "tiny"
    deployment_authority = publication_destination_authority(
        workspace_root=tmp_path,
    )
    build_area_deployment(
        definition,
        deployment,
        publication_authority=deployment_authority,
    )

    publication = json.loads((deployment / "publication.json").read_text(encoding="utf-8"))
    network = json.loads((deployment / "network.geojson").read_text(encoding="utf-8"))
    layer_manifest = json.loads((deployment / "layer-manifest.json").read_text(encoding="utf-8"))
    topography_manifest = json.loads(
        (deployment / "topography-manifest.json").read_text(encoding="utf-8")
    )
    profile_index = json.loads(
        (deployment / "topography-profile-evidence.json").read_text(encoding="utf-8")
    )
    original_network = json.loads(result.artifacts["geojson"].read_text(encoding="utf-8"))

    assert result.status in {"complete", "reviewable"}
    assert publication["area_id"] == definition.area_id
    assert publication["area_name"] == definition.area_name
    assert publication["title"] == definition.publication.title
    assert publication["scope"] == {
        "area_id": definition.area_id,
        "area_name": definition.area_name,
        "audience": definition.publication.audience,
    }
    assert publication["evidence_provenance"] == {
        "source": {
            "kind": definition.source.kind,
            "authority_boundary_queries": list(definition.source.boundary_queries),
        },
        "snapshot": {
            "snapshot_id": definition.source.snapshot_id,
            "manifest_sha256": hashlib.sha256(
                (
                    definition.source.snapshot_dir / definition.source.snapshot_id / "snapshot.json"
                ).read_bytes()
            ).hexdigest(),
        },
        "run": {"run_id": result.run_id, "status": result.status},
        "agent_runtime": {
            "response_mode": definition.compilation.agent.response_mode,
            "provider": definition.compilation.agent.provider,
            "model": definition.compilation.agent.model,
        },
    }
    assert (
        publication["area_definition_sha256"]
        == hashlib.sha256(definition.config_path.read_bytes()).hexdigest()
    )
    assert publication["disclaimer"] == DISCLAIMER
    assert publication["network_model"] == "backbone-outward"
    assert publication["layer_manifest"] == "layer-manifest.json"
    assert publication["topography_manifest"] == "topography-manifest.json"
    assert set(layer_manifest["groups"]) == {"amenities", "low-traffic", "schools", "urban"}
    assert all(
        entry["path"].startswith("layers/")
        for group in layer_manifest["groups"].values()
        for entry in group["shards"]
    )
    assert all(
        "sha256" not in entry
        for group in layer_manifest["groups"].values()
        for entry in group["shards"]
    )
    assert all(
        feature["properties"]["feature_type"] != "gradient-section"
        for feature in network["features"]
    )
    assert any(
        feature["properties"]["feature_type"] == "authority-boundary"
        for feature in network["features"]
    )
    deferred_types = {
        "urban-spine",
        "urban-classification-unknown",
        "low-traffic-area",
        "low-traffic-area-portal",
        "school",
        "school-street-assessment",
        "retail-centre",
        "healthcare",
    }
    assert (
        not {feature["properties"]["feature_type"] for feature in network["features"]}
        & deferred_types
    )
    assert all(
        json.loads(feature["properties"]["micro_gradient_intervals"]) == []
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "topography-profile"
    )
    assert topography_manifest["detail_min_zoom"] == 10
    assert (
        topography_manifest["gradient_section_count"]
        == publication["layer_counts"]["gradient_sections"]
    )
    assert topography_manifest["detail_feature_count"] == (
        topography_manifest["gradient_section_count"]
        + topography_manifest["unavailable_profile_count"]
    )
    assert profile_index["profile_count"] == publication["layer_counts"]["topography_profiles"]
    assert all("profile_ids" in chunk for chunk in profile_index["chunks"])
    assert (deployment / "network-map.pdf").read_bytes().startswith(b"%PDF-")
    service_worker = (deployment / "service-worker.js").read_text(encoding="utf-8")
    assert "caches.match" in service_worker
    assert "event.waitUntil" in service_worker
    assert "const cacheResponse = response.ok ? response.clone() : null" in service_worker
    assert "cache.put(event.request, cacheResponse)" in service_worker
    assert '"assets/maplibre-gl.js"' in service_worker
    assert '"assets/review-map.' in service_worker
    assert "network.geojson" not in service_worker
    assert "layer-manifest.json" not in service_worker
    assert "topography-manifest.json" not in service_worker

    assert "topography-profile-evidence.json" not in service_worker
    assert 'event.data?.type !== "cache-core"' in service_worker
    assert '"network_url":"network.geojson"' in (deployment / "data.js").read_text()
    assert '"layer_manifest_url":"layer-manifest.json"' in (deployment / "data.js").read_text()

    deferred_features = [
        feature
        for group in layer_manifest["groups"].values()
        for entry in group["shards"]
        for feature in json.loads((deployment / entry["path"]).read_text(encoding="utf-8"))[
            "features"
        ]
    ]
    overview_features = [
        feature
        for entry in topography_manifest["overview"]
        for feature in json.loads((deployment / entry["path"]).read_text(encoding="utf-8"))[
            "features"
        ]
    ]
    detail_features = [
        feature
        for entry in topography_manifest["detail"]
        for feature in json.loads((deployment / entry["path"]).read_text(encoding="utf-8"))[
            "features"
        ]
    ]
    original_by_id = {feature["id"]: feature for feature in original_network["features"]}
    delivered_by_id = {
        feature["id"]: feature
        for feature in [
            *network["features"],
            *deferred_features,
            *overview_features,
            *detail_features,
        ]
    }
    assert set(delivered_by_id) == set(original_by_id)
    for feature_id, original in original_by_id.items():
        delivered = delivered_by_id[feature_id]
        feature_type = original["properties"]["feature_type"]
        if feature_type in {"gradient-section", "topography-profile"}:
            assert delivered["geometry"] == original["geometry"]
        elif feature_type not in deferred_types:
            assert delivered["properties"] == original["properties"]
    unavailable_profile_ids = {
        feature["id"]
        for feature in original_network["features"]
        if feature["properties"]["feature_type"] == "topography-profile"
        and feature["properties"].get("evidence_status") == "evidence-unavailable"
    }
    assert unavailable_profile_ids <= {feature["id"] for feature in detail_features}

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise generated service-worker behaviour")
    worker_probe = """
import fs from 'node:fs';
const listeners = {};
const calls = [];
let delayCacheOpen = false;
let releaseCacheOpen;
const cacheOpenGate = new Promise(resolve => { releaseCacheOpen = resolve; });
let cachedBody;
global.location = { origin: 'https://example.test' };
global.self = {
  addEventListener: (name, listener) => { listeners[name] = listener; },
  skipWaiting: () => {},
  clients: { claim: async () => {} }
};
const cache = {
  addAll: async urls => calls.push(urls),
  put: async (_request, response) => { cachedBody = await response.text(); }
};
global.caches = {
  open: async () => {
    if (delayCacheOpen) await cacheOpenGate;
    return cache;
  },
  keys: async () => [],
  delete: async () => true,
  match: async () => null
};
global.fetch = async () => new Response('payload', { status: 200 });
eval(fs.readFileSync(0, 'utf8'));
let install;
listeners.install({ waitUntil: promise => { install = promise; } });
await install;
let cacheCore;
const replies = [];
listeners.message({
  data: { type: 'cache-core', urls: ['network.geojson'] },
  ports: [{ postMessage: reply => replies.push(reply) }],
  waitUntil: promise => { cacheCore = promise; }
});
await cacheCore;
delayCacheOpen = true;
let fetchResponse;
let cacheWrite;
listeners.fetch({
  request: new Request('https://example.test/network.geojson'),
  respondWith: promise => { fetchResponse = promise; },
  waitUntil: promise => { cacheWrite = promise; }
});
const clientBody = await (await fetchResponse).text();
releaseCacheOpen();
await cacheWrite;
console.log(JSON.stringify({ calls, replies, clientBody, cachedBody }));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", worker_probe],
        input=service_worker,
        text=True,
        check=True,
        capture_output=True,
    )
    assert json.loads(completed.stdout) == {
        "calls": [
            [
                "./",
                "index.html",
                "data.js",
                "publication.json",
                *sorted(
                    [
                        item.relative_to(deployment).as_posix()
                        for item in (deployment / "assets").iterdir()
                        if item.is_file() and item.suffix in {".css", ".js"}
                    ]
                ),
            ],
            ["network.geojson"],
        ],
        "replies": [{"ok": True}],
        "clientBody": "payload",
        "cachedBody": "payload",
    }


def test_area_deployment_omits_redundant_standalone_audits_but_keeps_embedded_reviewable_data(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    definition = CouncilConfig.from_yaml(fixture / "council.yaml")
    definition.publication.output_dir = tmp_path / "compiled"
    definition.source.snapshot_dir = tmp_path / "snapshots"
    compiler_authority = publication_destination_authority(
        workspace_root=fixture,
        approved_external_destination=definition.publication.output_dir,
    )
    snapshot(definition)
    compile(definition, publication_authority=compiler_authority)

    deployment = tmp_path / "deployments" / "tiny"
    deployment_authority = publication_destination_authority(workspace_root=tmp_path)
    build_area_deployment(
        definition,
        deployment,
        publication_authority=deployment_authority,
    )

    for filename in (
        "asset-accounting.json",
        "asset-accounting.geojson",
        "reviewable-network.geojson",
        "strategic-network.json",
    ):
        assert not (deployment / filename).exists()

    data = json.loads(
        (deployment / "data.js")
        .read_text(encoding="utf-8")
        .removeprefix("window.SATN_DATA = ")
        .removesuffix(";\n")
    )
    assert "reviewable_network" in data
    assert "reviewable" not in data
    assert (deployment / "network.geojson").is_file()
    assert (deployment / "compiler-run.json").is_file()
    assert (deployment / "publication.json").is_file()

    assert not (deployment / "provenance-lock.json").exists()
