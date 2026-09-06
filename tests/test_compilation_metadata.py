from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bath_saltford_fixture import configured_bath_saltford

from satn.deployment import build_area_deployment
from satn.filesystem_safety import publication_destination_authority
from satn.pipeline import compile
from satn.sources import snapshot


def _satn_data(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    return json.loads(text.removeprefix("window.SATN_DATA = ").removesuffix(";\n"))


def test_compilation_metadata_is_recorded_in_run_and_review_map(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    run = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))
    metadata = run["compilation_metadata"]
    assert result.metadata["compilation_metadata"] == metadata
    assert metadata["completed_at_utc"].endswith("Z")
    parsed = datetime.fromisoformat(metadata["completed_at_utc"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None
    assert metadata["duration_seconds"] >= 0
    data = _satn_data(result.output_dir / "review-map" / "data.js")
    assert data["compilation_metadata"] == metadata


def test_area_deployment_preserves_compilation_metadata(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    authority = publication_destination_authority(workspace_root=tmp_path)
    result = compile(config, publication_authority=authority)
    deployment = tmp_path / "deployment"
    build_area_deployment(config, deployment, publication_authority=authority)

    run = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))
    publication = json.loads((deployment / "publication.json").read_text(encoding="utf-8"))
    assert publication["compilation_metadata"] == run["compilation_metadata"]
    compiler_run = json.loads((deployment / "compiler-run.json").read_text(encoding="utf-8"))
    assert "compilation_diagnostics" in run
    assert "compilation_diagnostics" not in compiler_run
    assert "compilation_diagnostics" not in publication
    assert publication["run_id"] == compiler_run["run_id"] == run["run_id"]
    assert (
        publication["compilation_input_fingerprint"]
        == compiler_run["compilation_input_fingerprint"]
        == run["compilation_input_fingerprint"]
    )
    assert set(compiler_run) == set(run) - {"compilation_diagnostics"}
    assert _satn_data(deployment / "data.js")["compilation_metadata"] == run["compilation_metadata"]
    for relative_path in (
        "compiler-run.json",
        "publication.json",
    ):
        path = deployment / relative_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert path.read_bytes() == json.dumps(payload, separators=(",", ":")).encode()

    assert not (deployment / "provenance-lock.json").exists()
    assert not (deployment / "strategic-network.json").exists()
