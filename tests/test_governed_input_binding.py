from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bath_saltford_fixture import configured_bath_saltford

import satn.pipeline as pipeline
from satn.filesystem_safety import publication_destination_authority
from satn.local_evidence_store import LocalEvidenceStore
from satn.sources import snapshot


def test_pipeline_resolves_bound_evidence_once_without_byte_reverification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configured_bath_saltford(tmp_path)
    # Keep this focused on the pipeline/compiler boundary. The fixture's
    # network-selection evidence is unrelated to the store binding under test.
    config.compilation.network_selection = None
    snapshot(config)
    state_fingerprint = "a" * 64
    calls: list[tuple[str, bool]] = []

    class CountingStore(LocalEvidenceStore):
        def resolve_coverage(
            self,
            *,
            state_fingerprint: str,
            verify: bool = False,
        ) -> SimpleNamespace:
            calls.append((state_fingerprint, verify))
            return SimpleNamespace(fingerprint=state_fingerprint)

    store = object.__new__(CountingStore)
    monkeypatch.setattr(pipeline, "publish", lambda *_args, **_kwargs: {})
    result = pipeline.compile(
        config,
        evidence_store=store,
        evidence_state=state_fingerprint,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    assert result.status in {"complete", "reviewable"}
    assert calls == [(state_fingerprint, False)]
