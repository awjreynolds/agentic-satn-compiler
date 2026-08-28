"""Build one standalone, progressive Area Deployment from validated SATN artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path

from satn.constants import DISCLAIMER
from satn.filesystem_safety import (
    PublicationDestinationAuthority,
    commit_replacement,
    default_publication_destination_authority,
    publication_destination_authority,
    stage_replacement,
    write_ownership_marker,
)
from satn.models import AreaConfig, AreaDefinition

PROJECT = Path(__file__).parents[2]
DEFERRED_GROUPS = {
    "urban": {"urban-spine", "urban-classification-unknown"},
    "low-traffic": {"low-traffic-area", "low-traffic-area-portal"},
    "schools": {"school", "school-street-assessment"},
    "amenities": {"retail-centre", "healthcare"},
}
_AREA_DEPLOYMENT_REDUNDANT_AUDITS = frozenset(
    {
        "asset-accounting.json",
        "asset-accounting.geojson",
        "reviewable-network.geojson",
        # The compiler publication and review-map ZIP retain this complete
        # strategic projection. The Pages runtime already receives the same
        # projection through data.js under the canonical reviewable_network key.
        "strategic-network.json",
    }
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("area_definition", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--publication-workspace-root", type=Path)
    parser.add_argument("--approved-external-publication-destination", type=Path)
    parser.add_argument("--expected-prior-run-fingerprint")
    return parser.parse_args()


def _write_collection(path: Path, features: list[dict[str, object]]) -> int:
    payload = json.dumps(
        {"type": "FeatureCollection", "features": features},
        separators=(",", ":"),
    ).encode()
    path.write_bytes(payload)
    return len(payload)


def _compact_json_file(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _coordinates(geometry: dict[str, object] | None) -> list[tuple[float, float]]:
    if not geometry:
        return []
    values: list[tuple[float, float]] = []

    def visit(item: object) -> None:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            values.append((float(item[0]), float(item[1])))
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(geometry.get("coordinates"))
    return values


def _bbox(features: list[dict[str, object]]) -> list[float] | None:
    coordinates = [
        coordinate
        for feature in features
        for coordinate in _coordinates(feature.get("geometry"))  # type: ignore[arg-type]
    ]
    if not coordinates:
        return None
    return [
        min(value[0] for value in coordinates),
        min(value[1] for value in coordinates),
        max(value[0] for value in coordinates),
        max(value[1] for value in coordinates),
    ]


def _spatial_chunks(
    features: list[dict[str, object]],
    *,
    maximum_features: int,
    cell_degrees: float = 0.1,
) -> list[list[dict[str, object]]]:
    cells: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    unlocated: list[dict[str, object]] = []
    for feature in features:
        coordinates = _coordinates(feature.get("geometry"))  # type: ignore[arg-type]
        if not coordinates:
            unlocated.append(feature)
            continue
        centre_x = (min(item[0] for item in coordinates) + max(item[0] for item in coordinates)) / 2
        centre_y = (min(item[1] for item in coordinates) + max(item[1] for item in coordinates)) / 2
        cells[(math.floor(centre_x / cell_degrees), math.floor(centre_y / cell_degrees))].append(
            feature
        )
    chunks: list[list[dict[str, object]]] = []
    for key in sorted(cells):
        cell = cells[key]
        chunks.extend(
            cell[index : index + maximum_features]
            for index in range(0, len(cell), maximum_features)
        )
    chunks.extend(
        unlocated[index : index + maximum_features]
        for index in range(0, len(unlocated), maximum_features)
    )
    return chunks


def _write_shards(
    directory: Path,
    prefix: str,
    features: list[dict[str, object]],
    *,
    maximum_features: int = 1000,
) -> list[dict[str, object]]:
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, chunk in enumerate(_spatial_chunks(features, maximum_features=maximum_features)):
        encoded = json.dumps(
            {"type": "FeatureCollection", "features": chunk},
            separators=(",", ":"),
        ).encode()
        filename = f"{prefix}-{index:04d}.geojson"
        (directory / filename).write_bytes(encoded)
        entries.append(
            {
                "path": f"{directory.name}/{filename}",
                "size_bytes": len(encoded),
                "feature_count": len(chunk),
                "bbox": _bbox(chunk),
            }
        )
    return entries


def _gradient_band(properties: dict[str, object]) -> str:
    raw = properties.get("steepest_sustained_gradient_pct")
    if raw is None:
        return "unavailable"
    value = abs(float(raw))
    if value <= 3:
        return "gentle"
    if value <= 5:
        return "noticeable"
    if value <= 8:
        return "steep"
    if value <= 12.5:
        return "very-steep"
    return "severe"


def _service_worker(deployment_id: str, run_id: str, shell_assets: list[str]) -> str:
    cache_name = f"satn-{deployment_id}-{run_id}"
    shell = ["./", "index.html", "data.js", "publication.json", *sorted(shell_assets)]
    return f"""const CACHE = {json.dumps(cache_name)};
self.addEventListener("install", event => {{
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll({json.dumps(shell)})));
}});
self.addEventListener("activate", event => {{
  event.waitUntil((async () => {{
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith("satn-{deployment_id}-") && key !== CACHE)
      .map(key => caches.delete(key)));
    await self.clients.claim();
  }})());
}});
self.addEventListener("message", event => {{
  if (event.data?.type !== "cache-core") return;
  const urls = Array.isArray(event.data.urls) ? event.data.urls : [];
  const reply = (payload) => event.ports[0]?.postMessage(payload);
  event.waitUntil(caches.open(CACHE).then(async cache => {{
    await cache.addAll(urls);
    reply({{ ok: true }});
  }}).catch(error => reply({{ ok: false, error: String(error) }})));
}});
self.addEventListener("fetch", event => {{
  if (event.request.method !== "GET" ||
      new URL(event.request.url).origin !== location.origin) return;
  event.respondWith((async () => {{
    const cached = await caches.match(event.request);
    if (cached) return cached;
    const response = await fetch(event.request);
    const cacheResponse = response.ok ? response.clone() : null;
    if (cacheResponse) {{
      event.waitUntil(caches.open(CACHE).then(cache =>
        cache.put(event.request, cacheResponse)
      ));
    }}
    return response;
  }})());
}});
"""


def _evidence_provenance(definition: AreaConfig, run: dict[str, object]) -> dict[str, object]:
    """Expose the actual configured inputs without claiming an unrun agent."""
    return {
        "source": {
            "kind": definition.source.kind,
            "authority_boundary_queries": list(definition.source.boundary_queries),
        },
        "snapshot": {
            "snapshot_id": definition.source.snapshot_id,
            "manifest_sha256": run["snapshot_manifest_sha256"],
        },
        "run": {
            "run_id": run["run_id"],
            "status": run["status"],
        },
        "agent_runtime": {
            "response_mode": definition.compilation.agent.response_mode,
            "provider": definition.compilation.agent.provider,
            "model": definition.compilation.agent.model,
        },
    }


def build_area_deployment(
    definition: AreaConfig,
    destination: Path,
    *,
    publication_authority: PublicationDestinationAuthority | None = None,
) -> Path:
    destination = Path(destination)
    if publication_authority is None:
        publication_authority = default_publication_destination_authority(
            definition.config_path,
        )
    output = definition.publication.output_dir
    run_path = output / "run.json"
    review_map = output / "review-map"
    pdf_map = output / "network-map.pdf"
    if not run_path.exists() or not (review_map / "index.html").exists() or not pdf_map.exists():
        raise SystemExit(f"compile {definition.config_path} before building its Area Deployment")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if run["council_id"] != definition.area_id:
        raise SystemExit("compiled artifacts do not match the requested Area Definition")
    if run["status"] not in {"complete", "reviewable"}:
        raise SystemExit("the current run is not publishable")
    if run["atm_geometry_included"]:
        raise SystemExit("a public Area Deployment must not contain governed ATM geometry")
    interventions = json.loads(
        (review_map / "human-intervention-requests.json").read_text(encoding="utf-8")
    )
    comparison = json.loads((review_map / "backbone-comparison.json").read_text(encoding="utf-8"))
    authority = publication_authority
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind=f"area-deployment:{definition.deployment_slug}",
        prior_record_name="publication.json",
    )
    temporary = staging.temporary
    try:

        def ignore_redundant_audits(source: str, names: list[str]) -> set[str]:
            # The compiler publication and review-map ZIP retain the complete
            # audit files. The Area Deployment adapter only needs to omit the
            # standalone copies because data.js embeds the canonical runtime
            # projection.
            if Path(source).resolve() != review_map.resolve():
                return set()
            return set(names).intersection(_AREA_DEPLOYMENT_REDUNDANT_AUDITS)

        shutil.copytree(
            review_map,
            temporary,
            dirs_exist_ok=True,
            ignore=ignore_redundant_audits,
        )
        content = temporary
        shutil.copy2(run_path, content / "compiler-run.json")
        _compact_json_file(content / "compiler-run.json")
        strategic_network_path = content / "strategic-network.json"
        if strategic_network_path.is_file():
            _compact_json_file(strategic_network_path)
        network_path = content / "network.geojson"
        network = json.loads(network_path.read_text(encoding="utf-8"))
        gradients: list[dict[str, object]] = []
        unavailable_profiles: list[dict[str, object]] = []
        profiles: list[dict[str, object]] = []
        overview: list[dict[str, object]] = []
        deferred: dict[str, list[dict[str, object]]] = defaultdict(list)
        core: list[dict[str, object]] = []
        type_to_group = {
            feature_type: group
            for group, feature_types in DEFERRED_GROUPS.items()
            for feature_type in feature_types
        }
        for feature in network["features"]:
            properties = feature["properties"]
            feature_type = properties.get("feature_type")
            if feature_type == "gradient-section":
                gradients.append(feature)
                continue
            if feature_type == "topography-profile":
                profiles.append({**feature, "geometry": None})
                lightweight = dict(properties)
                lightweight["micro_gradient_intervals"] = "[]"
                capability = json.loads(lightweight.get("micro_gradient_capability") or "{}")
                capability.pop("uncertainty", None)
                lightweight["micro_gradient_capability"] = json.dumps(
                    capability, separators=(",", ":")
                )
                overview_properties = {
                    **lightweight,
                    "gradient_band": _gradient_band(properties),
                }
                overview.append({**feature, "properties": overview_properties})
                if properties.get("evidence_status") == "evidence-unavailable":
                    unavailable_profiles.append(feature)
                core.append({**feature, "properties": lightweight, "geometry": None})
                continue
            group = type_to_group.get(feature_type)
            if group:
                deferred[group].append(feature)
            else:
                core.append(feature)
        network["features"] = core
        network_path.write_text(json.dumps(network, separators=(",", ":")), encoding="utf-8")

        layer_directory = content / "layers"
        groups: dict[str, dict[str, object]] = {}
        for group in sorted(DEFERRED_GROUPS):
            # Keep physical shards homogeneous.  The UI exposes these feature
            # types as separate logical layers (for example, schools and School
            # Street assessments) and must never have to download a sibling type
            # just because both happen to share a manifest group.
            types: dict[str, dict[str, object]] = {}
            entries: list[dict[str, object]] = []
            for feature_type in sorted(DEFERRED_GROUPS[group]):
                typed_features = [
                    feature
                    for feature in deferred[group]
                    if feature["properties"].get("feature_type") == feature_type
                ]
                typed_entries = _write_shards(
                    layer_directory,
                    f"{group}-{feature_type}",
                    typed_features,
                )
                entries.extend(typed_entries)
                types[feature_type] = {
                    "feature_count": len(typed_features),
                    "size_bytes": sum(int(entry["size_bytes"]) for entry in typed_entries),
                    "shards": typed_entries,
                }
            expected_entries = [
                entry for feature_type in sorted(types) for entry in types[feature_type]["shards"]
            ]
            if entries != expected_entries:
                raise AssertionError("typed layer shards must preserve the group shard order")
            if sorted(types) != sorted(DEFERRED_GROUPS[group]):
                raise AssertionError(
                    "typed layer manifest must include every declared feature type"
                )
            if any(
                feature["properties"].get("feature_type") not in types
                for feature in deferred[group]
            ):
                raise AssertionError(
                    "typed layer shards must contain only their declared feature type"
                )
            if sum(int(metadata["feature_count"]) for metadata in types.values()) != len(
                deferred[group]
            ) or sum(int(metadata["size_bytes"]) for metadata in types.values()) != sum(
                int(entry["size_bytes"]) for entry in entries
            ):
                raise AssertionError("typed layer manifest totals must match the group totals")
            groups[group] = {
                "feature_types": sorted(DEFERRED_GROUPS[group]),
                "feature_count": len(deferred[group]),
                "size_bytes": sum(int(entry["size_bytes"]) for entry in entries),
                "shards": entries,
                "types": types,
            }
        layer_manifest = {
            "schema_version": run["schema_version"],
            "area_id": definition.area_id,
            "groups": groups,
        }
        (content / "layer-manifest.json").write_text(
            json.dumps(layer_manifest, indent=2), encoding="utf-8"
        )

        topography_directory = content / "topography"
        overview_entries = _write_shards(
            topography_directory,
            "overview",
            overview,
            maximum_features=750,
        )
        detail_entries = _write_shards(
            topography_directory,
            "detail",
            [*gradients, *unavailable_profiles],
            maximum_features=1500,
        )
        topography_manifest = {
            "schema_version": run["schema_version"],
            "area_id": definition.area_id,
            "overview": overview_entries,
            "detail": detail_entries,
            "overview_feature_count": len(overview),
            "detail_feature_count": len(gradients) + len(unavailable_profiles),
            "gradient_section_count": len(gradients),
            "unavailable_profile_count": len(unavailable_profiles),
            "overview_size_bytes": sum(int(item["size_bytes"]) for item in overview_entries),
            "detail_size_bytes": sum(int(item["size_bytes"]) for item in detail_entries),
            "detail_min_zoom": 10,
        }
        (content / "topography-manifest.json").write_text(
            json.dumps(topography_manifest, indent=2), encoding="utf-8"
        )

        evidence_directory = content / "evidence"
        evidence_directory.mkdir(exist_ok=True)
        evidence_chunks = []
        for index in range(0, len(profiles), 200):
            chunk = profiles[index : index + 200]
            encoded = json.dumps(
                {"type": "FeatureCollection", "features": chunk},
                separators=(",", ":"),
            ).encode()
            filename = f"topography-profiles-{index // 200:04d}.geojson"
            (evidence_directory / filename).write_bytes(encoded)
            evidence_chunks.append(
                {
                    "path": f"evidence/{filename}",
                    "profile_ids": [feature["properties"].get("profile_id") for feature in chunk],
                    "profile_count": len(chunk),
                    "size_bytes": len(encoded),
                    "feature_count": len(chunk),
                    "bbox": _bbox(chunk),
                }
            )
        profile_index = {
            "schema_version": run["schema_version"],
            "profile_count": len(profiles),
            "chunks": evidence_chunks,
            "disclaimer": DISCLAIMER,
        }
        (content / "topography-profile-evidence.json").write_text(
            json.dumps(profile_index, indent=2), encoding="utf-8"
        )

        data_path = content / "data.js"
        prefix = "window.SATN_DATA = "
        source = data_path.read_text(encoding="utf-8")
        if not source.startswith(prefix) or not source.endswith(";\n"):
            raise SystemExit("review-map data.js has an unsupported format")
        data = json.loads(source.removeprefix(prefix).removesuffix(";\n"))
        data.pop("network", None)
        # ``reviewable`` was the original public key. Keep one canonical
        # runtime projection; the browser prefers ``reviewable_network`` and
        # still accepts the old key for legacy publications.
        if "reviewable" in data and "reviewable_network" in data:
            if data["reviewable"] != data["reviewable_network"]:
                raise SystemExit("review-map reviewable projections disagree")
            data.pop("reviewable")
        data["area_id"] = definition.area_id
        data["area_name"] = definition.area_name
        data["network_url"] = "network.geojson"
        data["layer_manifest_url"] = "layer-manifest.json"
        data["topography_manifest_url"] = "topography-manifest.json"
        data["profile_evidence_index_url"] = "topography-profile-evidence.json"
        shutil.copy2(pdf_map, content / "network-map.pdf")
        (content / ".nojekyll").write_text("", encoding="utf-8")
        shell_assets = [
            item.relative_to(content).as_posix()
            for item in (content / "assets").iterdir()
            if item.is_file() and item.suffix in {".css", ".js"}
        ]
        (content / "service-worker.js").write_text(
            _service_worker(definition.deployment_slug, run["run_id"], shell_assets),
            encoding="utf-8",
        )
        publication = {
            "schema_version": run["schema_version"],
            "title": definition.publication.title,
            "area_id": definition.area_id,
            "area_name": definition.area_name,
            "deployment_id": definition.deployment_slug,
            "scope": {
                "area_id": definition.area_id,
                "area_name": definition.area_name,
                "audience": definition.publication.audience,
            },
            "area_definition_sha256": hashlib.sha256(
                definition.config_path.read_bytes()
            ).hexdigest(),
            "boundary_queries": list(definition.source.boundary_queries),
            "evidence_provenance": _evidence_provenance(definition, run),
            "run_id": run["run_id"],
            "status": run["status"],
            # A governed compiler run always supplies this contract.  Keep a
            # legacy build explicitly unclassified rather than implying that
            # its older, incomplete audit was production agent review.
            "runtime_governance": run.get(
                "runtime_governance",
                {
                    "schema_version": "satn-runtime-governance/v1",
                    "status": "reviewable",
                    "reason": "legacy-runtime-governance-unavailable",
                    "promotion": {
                        "allowed": False,
                        "reason": "legacy-runtime-governance-unavailable",
                    },
                },
            ),
            "compilation_input_fingerprint": run["compilation_input_fingerprint"],
            "compiler_run": "compiler-run.json",
            "network_model": run["network_model"],
            "connection_count": run["connection_count"],
            "gap_count": run["gap_count"],
            "human_intervention_request_count": len(interventions["records"]),
            "superseded_hypotheses": run["superseded_hypotheses"],
            "layer_counts": run["layer_counts"],
            "criteria": run["criteria"],
            "compilation_diagnostics": run["compilation_diagnostics"],
            "comparison_role": comparison["comparison_role"],
            "layer_manifest": "layer-manifest.json",
            "topography_manifest": "topography-manifest.json",
            "topography_profile_evidence_index": "topography-profile-evidence.json",
            "disclaimer": DISCLAIMER,
        }
        if "compilation_metadata" in run:
            publication["compilation_metadata"] = run["compilation_metadata"]
        (content / "publication.json").write_text(
            json.dumps(publication, separators=(",", ":")), encoding="utf-8"
        )
        # data.js is a first-class public contract, not an unbound convenience
        # payload.  Keep every user-facing provenance and scope field identical.
        for field in (
            "title",
            "area_id",
            "area_name",
            "scope",
            "evidence_provenance",
            "run_id",
            "status",
            "runtime_governance",
            "area_definition_sha256",
            "compilation_input_fingerprint",
            "criteria",
            "layer_counts",
            "connection_count",
            "gap_count",
            "disclaimer",
        ):
            data[field] = publication[field]
        if "compilation_metadata" in publication:
            data["compilation_metadata"] = publication["compilation_metadata"]
        data_path.write_text(
            f"{prefix}{json.dumps(data, separators=(',', ':')).replace('</', '<\\/')};\n",
            encoding="utf-8",
        )
        write_ownership_marker(
            content,
            owner_kind=f"area-deployment:{definition.deployment_slug}",
        )
        commit_replacement(
            staging,
            authority=authority,
            owner_kind=f"area-deployment:{definition.deployment_slug}",
            prior_record_name="publication.json",
        )
    finally:
        staging.cleanup()
    return destination


def main() -> None:
    args = _arguments()
    definition = AreaDefinition.from_yaml(args.area_definition)
    destination = args.destination or (
        PROJECT / "build" / "deployments" / definition.deployment_slug
    )
    authority = None
    if (
        args.publication_workspace_root is not None
        or args.approved_external_publication_destination is not None
        or args.expected_prior_run_fingerprint is not None
    ):
        default_authority = default_publication_destination_authority(
            definition.config_path,
        )
        authority = publication_destination_authority(
            workspace_root=args.publication_workspace_root or default_authority.workspace_root,
            approved_external_destination=args.approved_external_publication_destination,
            expected_prior_run_fingerprint=args.expected_prior_run_fingerprint,
        )
    print(
        build_area_deployment(
            definition,
            destination,
            publication_authority=authority,
        )
    )


if __name__ == "__main__":
    main()
