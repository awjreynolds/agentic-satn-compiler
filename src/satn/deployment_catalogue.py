"""Build the lightweight Pages index for independently reproducible deployments."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from satn.models import AreaDefinition

SCHEMA_VERSION = "satn-deployment-catalogue/v1"
ROOT_LOCK_NAME = "catalogue-lock.json"
ROOT_LOCK_SCHEMA_VERSION = "satn-pages-root-lock/v1"
_AREA_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_ARTIFACTS = ("review_map", "network_map_pdf", "review_map_zip")


@dataclass(frozen=True)
class DeploymentEntry:
    """A stable deployment identity and location, without generated data.

    ``deployment_id`` identifies the published deployment (and its Pages path).
    The Area Definition named by ``area_definition`` owns the geographical
    ``area_id`` that appears in the generated ``publication.json``.
    """

    deployment_id: str
    area_id: str
    area_name: str
    area_definition: str
    area_definition_sha256: str
    deployment_path: str
    artifacts: dict[str, str]
    title: str
    scope: dict[str, str]
    evidence_provenance: dict[str, object]

    def publication_links(self) -> dict[str, str]:
        return {
            name: f"{self.deployment_path}{path}"
            for name, path in self.artifacts.items()
        }


@dataclass(frozen=True)
class DeploymentCatalogue:
    title: str
    deployments: tuple[DeploymentEntry, ...]

    def as_publication(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "title": self.title,
            "deployments": [
                {
                    "deployment_id": entry.deployment_id,
                    "area_id": entry.area_id,
                    "area_name": entry.area_name,
                    "area_definition": entry.area_definition,
                    "area_definition_sha256": entry.area_definition_sha256,
                    "deployment_path": entry.deployment_path,
                    "artifacts": entry.publication_links(),
                    "title": entry.title,
                    "scope": entry.scope,
                    "evidence_provenance": entry.evidence_provenance,
                }
                for entry in self.deployments
            ],
        }


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _relative_path(value: object, field: str, *, directory: bool = False) -> str:
    path = _text(value, field)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        raise ValueError(f"{field} must be a relative path without traversal")
    normalized = parsed.as_posix()
    if directory:
        if not path.endswith("/"):
            raise ValueError(f"{field} must end with /")
        return f"{normalized}/"
    elif normalized.endswith("/"):
        raise ValueError(f"{field} must name a file")
    return normalized


def load_deployment_catalogue(path: str | Path) -> DeploymentCatalogue:
    """Load and validate a compact, tracked Deployment Catalogue definition."""

    source = Path(path)
    if source.is_symlink():
        raise ValueError("deployment catalogue must not be a symlink")
    source = source.resolve()
    deployments_root = source.parent
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("deployment catalogue must be a mapping")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"deployment catalogue schema_version must be {SCHEMA_VERSION}")
    entries = raw.get("deployments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("deployment catalogue must contain deployments")

    deployments: list[DeploymentEntry] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("each deployment catalogue entry must be a mapping")
        deployment_id = _text(raw_entry.get("deployment_id"), "deployment_id")
        if not _AREA_ID.fullmatch(deployment_id):
            raise ValueError(
                "deployment_id must use lowercase letters, digits, and hyphens"
            )
        if deployment_id in seen_ids:
            raise ValueError("deployment catalogue deployment_ids must be unique")
        deployment_path = _relative_path(
            raw_entry.get("deployment_path"), "deployment_path", directory=True
        )
        if not deployment_path.startswith("deployments/"):
            raise ValueError("deployment_path must be rooted at deployments/")
        if deployment_path in seen_paths:
            raise ValueError("deployment catalogue deployment_paths must be unique")
        raw_artifacts = raw_entry.get("artifacts")
        if not isinstance(raw_artifacts, dict) or set(raw_artifacts) != set(_ARTIFACTS):
            raise ValueError(f"artifacts must contain exactly: {', '.join(_ARTIFACTS)}")
        artifacts = {
            name: _relative_path(raw_artifacts[name], f"artifacts.{name}")
            for name in _ARTIFACTS
        }
        area_id = _text(raw_entry.get("area_id"), "area_id")
        if not _AREA_ID.fullmatch(area_id):
            raise ValueError("area_id must use lowercase letters, digits, and hyphens")
        area_name = _text(raw_entry.get("area_name"), "area_name")
        area_definition = _relative_path(raw_entry.get("area_definition"), "area_definition")
        definition_path = deployments_root / area_definition
        try:
            resolved_definition = definition_path.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"area_definition does not exist: {definition_path}") from error
        if not resolved_definition.is_relative_to(deployments_root):
            raise ValueError("area_definition must resolve under deployments/")
        if not resolved_definition.is_file():
            raise ValueError(f"area_definition must be a file: {definition_path}")
        area_definition_sha256 = hashlib.sha256(
            resolved_definition.read_bytes()
        ).hexdigest()
        definition = AreaDefinition.from_yaml(resolved_definition)
        if definition.deployment_slug != deployment_id:
            raise ValueError(
                "area_definition deployment_slug must match catalogue deployment_id: "
                f"{definition_path}"
            )
        if definition.area_id != area_id:
            raise ValueError(
                "area_definition area_id must match catalogue area_id: "
                f"{definition_path}"
            )
        if definition.area_name != area_name:
            raise ValueError(
                "area_definition area_name must match catalogue area_name: "
                f"{definition_path}"
            )
        deployments.append(
            DeploymentEntry(
                deployment_id=deployment_id,
                area_id=area_id,
                area_name=area_name,
                area_definition=area_definition,
                area_definition_sha256=area_definition_sha256,
                deployment_path=deployment_path,
                artifacts=artifacts,
                title=definition.publication.title,
                scope={
                    "area_id": definition.area_id,
                    "area_name": definition.area_name,
                    "audience": definition.publication.audience,
                },
                evidence_provenance={
                    "source": {
                        "kind": definition.source.kind,
                        "authority_boundary_queries": list(definition.source.boundary_queries),
                    },
                    "snapshot": {"snapshot_id": definition.source.snapshot_id},
                    "agent_runtime": {
                        "response_mode": definition.compilation.agent.response_mode,
                        "provider": definition.compilation.agent.provider,
                        "model": definition.compilation.agent.model,
                    },
                },
            )
        )
        seen_ids.add(deployment_id)
        seen_paths.add(deployment_path)
    return DeploymentCatalogue(
        title=_text(raw.get("title"), "title"), deployments=tuple(deployments)
    )


def _html(catalogue: DeploymentCatalogue) -> str:
    rows = "\n".join(
        """      <li>
        <h2>{name}</h2>
        <p><a href=\"{map}\">Open interactive review map</a></p>
        <p><a href=\"{pdf}\" download>Download strategic overview PDF</a></p>
        <p><a href=\"{zip}\" download>Download review-map ZIP</a></p>
      </li>""".format(
            name=html.escape(entry.area_name),
            map=html.escape(entry.publication_links()["review_map"], quote=True),
            pdf=html.escape(entry.publication_links()["network_map_pdf"], quote=True),
            zip=html.escape(entry.publication_links()["review_map_zip"], quote=True),
        )
        for entry in catalogue.deployments
    )
    return f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>{html.escape(catalogue.title)}</title>
  </head>
  <body>
    <main>
      <h1>{html.escape(catalogue.title)}</h1>
      <p>Select an independently reproducible Area Deployment.</p>
      <ul>
{rows}
      </ul>
    </main>
  </body>
</html>
"""


def catalogue_runtime_files(catalogue: DeploymentCatalogue) -> dict[str, bytes]:
    """Return the complete, deterministic Pages root produced from a catalogue."""

    return {
        "catalogue.json": (json.dumps(catalogue.as_publication(), indent=2) + "\n").encode(),
        "index.html": _html(catalogue).encode(),
    }


def catalogue_lock_payload(catalogue: DeploymentCatalogue) -> dict[str, object]:
    """Create the tag-tracked trust root for Pages' executable root files."""

    files = catalogue_runtime_files(catalogue)
    return {
        "schema_version": ROOT_LOCK_SCHEMA_VERSION,
        "root_files": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
            for name, content in files.items()
        },
        "deployment_roots": [
            f"deployments/{entry.deployment_id}" for entry in catalogue.deployments
        ],
    }


def generate_catalogue_lock(catalogue_path: str | Path, path: str | Path | None = None) -> Path:
    """Write the small tracked lock that binds the root catalogue page to its tag."""

    source = Path(catalogue_path)
    target = Path(path) if path is not None else source.parent / ROOT_LOCK_NAME
    payload = catalogue_lock_payload(load_deployment_catalogue(source))
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def build_deployment_catalogue(catalogue_path: str | Path, destination: str | Path) -> Path:
    """Write only the small Pages root index; deployments are assembled separately."""

    catalogue = load_deployment_catalogue(catalogue_path)
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    for name, content in catalogue_runtime_files(catalogue).items():
        (output / name).write_bytes(content)
    return output
