from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from satn.external_analysis import (
    DeterministicFixtureExternalAnalysisAdapter,
    ExternalAnalysisObservation,
    ExternalAnalysisRequest,
    ExternalAnalysisStatus,
    PinnedExternalAnalysisAdapter,
    run_governed_external_analysis,
)


def _request(*, source: str = "a" * 64) -> ExternalAnalysisRequest:
    return ExternalAnalysisRequest(
        analysis_id="accessibility-comparison",
        profile_id="r5-opportunity-trial-v1",
        source_export_hashes=(source,),
        parameters={"threshold_m": 1200},
        defaults={"missing_policy": "retain-null"},
        canonical_crs="EPSG:27700",
        timezone="Europe/London",
        analysis_date=date(2026, 8, 4),
        seed=7,
        thread_policy="single-threaded",
        expected_observation_ids=("place-a", "place-b", "place-c"),
    )


def _fixture_adapter() -> DeterministicFixtureExternalAnalysisAdapter:
    return DeterministicFixtureExternalAnalysisAdapter(
        fixture_id="r5-opportunity-fixture-v1",
        engine_name="r5-fixture",
        engine_version="0.1",
        engine_commit="f" * 40,
        engine_licence="GPL-3.0-only",
        observations=(
            ExternalAnalysisObservation(
                observation_id="place-a",
                subject_id="school-a",
                metric="opportunity-accessibility",
                state="available",
                value="0.73",
                unit="proportion",
                source_row_id="row-a",
            ),
            ExternalAnalysisObservation(
                observation_id="place-b",
                subject_id="school-b",
                metric="opportunity-accessibility",
                state="unreachable",
                value=None,
                unit="proportion",
                source_row_id="row-b",
            ),
            ExternalAnalysisObservation(
                observation_id="place-c",
                subject_id="school-c",
                metric="opportunity-accessibility",
                state="unmatched",
            ),
        ),
    )


def test_fixture_and_pinned_exports_normalize_to_same_schema(tmp_path: Path) -> None:
    fixture = _fixture_adapter()
    request = _request()
    fixture_run = run_governed_external_analysis(request, fixture)

    payload = {
        "schema": "satn-external-analysis-export/v1",
        "observations": [item.canonical_payload() for item in fixture.observations],
    }
    retained = tmp_path / "r5-export.json"
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    retained.write_bytes(raw)
    pinned_source_hash = hashlib.sha256(raw).hexdigest()
    pinned = PinnedExternalAnalysisAdapter(
        export_path=retained,
        expected_export_sha256=pinned_source_hash,
        engine_name="r5-fixture",
        engine_version="0.1",
        engine_commit="f" * 40,
        engine_licence="GPL-3.0-only",
    )
    pinned_run = run_governed_external_analysis(_request(source=pinned_source_hash), pinned)

    assert fixture_run.status is ExternalAnalysisStatus.COMPLETE
    assert pinned_run.status is ExternalAnalysisStatus.COMPLETE
    assert fixture_run.observations == pinned_run.observations
    assert fixture_run.normalized_observation_hash == pinned_run.normalized_observation_hash


def test_run_identity_binds_governance_metadata_and_material_inputs() -> None:
    adapter = _fixture_adapter()
    first = run_governed_external_analysis(_request(), adapter)
    changed = run_governed_external_analysis(
        _request(source="b" * 64),
        adapter,
    )

    assert first.raw_output_hash
    assert first.normalized_observation_hash
    assert first.run_fingerprint != changed.run_fingerprint
    assert first.source_export_hashes == ("a" * 64,)
    assert first.parameters["threshold_m"] == 1200
    assert first.defaults["missing_policy"] == "retain-null"
    assert first.timezone == "Europe/London"
    assert first.seed == 7
    assert first.thread_policy == "single-threaded"


def test_timeout_is_typed_and_preserves_expected_rows() -> None:
    class TimeoutAdapter:
        def run(self, request: ExternalAnalysisRequest) -> object:
            raise TimeoutError("fixture timed out")

    result = run_governed_external_analysis(_request(), TimeoutAdapter())

    assert result.status is ExternalAnalysisStatus.TIMEOUT
    assert {item.observation_id for item in result.observations} == {
        "place-a",
        "place-b",
        "place-c",
    }
    assert all(item.state == "unavailable" for item in result.observations)
    assert result.error_code == "adapter-timeout"


def test_unavailable_response_is_typed_and_complete() -> None:
    adapter = DeterministicFixtureExternalAnalysisAdapter(
        fixture_id="unavailable-fixture",
        status=ExternalAnalysisStatus.UNAVAILABLE,
        unavailable_reason="engine not installed",
    )

    result = run_governed_external_analysis(_request(), adapter)

    assert result.status is ExternalAnalysisStatus.UNAVAILABLE
    assert len(result.observations) == 3
    assert all(item.state == "unavailable" for item in result.observations)
    assert result.error_code == "engine-unavailable"


def test_external_payload_cannot_smuggle_geometry_or_winner_claim() -> None:
    class UnsafeAdapter:
        def run(self, request: ExternalAnalysisRequest) -> object:
            return {
                "status": "complete",
                "engine_name": "unsafe",
                "engine_version": "1",
                "engine_commit": "a" * 40,
                "engine_licence": "MIT",
                "observations": (),
                "raw_output": {"geometry": "LINESTRING (0 0, 1 1)", "winner": "route-a"},
            }

    result = run_governed_external_analysis(_request(), UnsafeAdapter())

    assert result.status is ExternalAnalysisStatus.INVALID
    assert result.error_code == "invalid-external-output"
    assert all(item.state == "unavailable" for item in result.observations)


def test_invalid_request_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="source export hash"):
        _request(source="not-a-sha")
    with pytest.raises(ValueError, match="thread policy"):
        ExternalAnalysisRequest(
            analysis_id="a",
            profile_id="p",
            source_export_hashes=("a" * 64,),
            analysis_date=date(2026, 8, 4),
            thread_policy="",
        )


def test_external_observation_has_no_canonical_geometry_or_winner_surface() -> None:
    observation = ExternalAnalysisObservation(
        observation_id="row-a",
        subject_id="route-a",
        metric="travel-cost",
        state="available",
        value="4.2",
        unit="minutes",
    )

    assert not hasattr(observation, "geometry")
    assert not hasattr(observation, "winner")
    assert observation.value_decimal == "4.2"


def _retained_export(tmp_path: Path, payload: object | None = None) -> tuple[Path, str]:
    payload = payload or {"schema": "satn-external-analysis-export/v1", "observations": []}
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = tmp_path / "export.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_pinned_export_rejects_lexical_parent_even_when_resolved_inside_root(
    tmp_path: Path,
) -> None:
    real, digest = _retained_export(tmp_path)
    lexical = tmp_path / "exports" / ".." / real.name

    with pytest.raises(ValueError, match="parent traversal"):
        PinnedExternalAnalysisAdapter(
            export_path=lexical,
            expected_export_sha256=digest,
        )


def test_pinned_export_rejects_symlink_path_components(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real, digest = _retained_export(real_dir)
    alias = tmp_path / "alias"
    alias.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        PinnedExternalAnalysisAdapter(
            export_path=alias / real.name,
            expected_export_sha256=digest,
        )


def test_pinned_export_rechecks_no_follow_when_file_is_swapped_to_symlink(tmp_path: Path) -> None:
    real, digest = _retained_export(tmp_path)
    adapter = PinnedExternalAnalysisAdapter(export_path=real, expected_export_sha256=digest)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(real.read_bytes())
    real.unlink()
    real.symlink_to(replacement)

    result = run_governed_external_analysis(_request(source=digest), adapter)

    assert result.status is ExternalAnalysisStatus.INVALID
    assert result.error_code == "invalid-external-output"


def test_pinned_export_rejects_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "export.json"
    directory.mkdir()
    digest = "a" * 64
    adapter = PinnedExternalAnalysisAdapter(export_path=directory, expected_export_sha256=digest)

    result = run_governed_external_analysis(_request(source=digest), adapter)

    assert result.status is ExternalAnalysisStatus.INVALID
    assert result.error_code == "invalid-external-output"


def test_pinned_export_checks_size_before_reading_oversized_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, digest = _retained_export(
        tmp_path,
        {"schema": "satn-external-analysis-export/v1", "observations": [], "padding": "x" * 200},
    )
    adapter = PinnedExternalAnalysisAdapter(
        export_path=path,
        expected_export_sha256=digest,
        max_export_bytes=100,
    )
    monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("read_bytes was called"))

    result = run_governed_external_analysis(_request(source=digest), adapter)

    assert result.status is ExternalAnalysisStatus.INVALID
    assert result.error_code == "invalid-external-output"


def test_pinned_export_budget_is_part_of_run_identity(tmp_path: Path) -> None:
    path, digest = _retained_export(tmp_path)
    first = run_governed_external_analysis(
        _request(source=digest),
        PinnedExternalAnalysisAdapter(
            export_path=path,
            expected_export_sha256=digest,
            max_export_bytes=1000,
        ),
    )
    second = run_governed_external_analysis(
        _request(source=digest),
        PinnedExternalAnalysisAdapter(
            export_path=path,
            expected_export_sha256=digest,
            max_export_bytes=2000,
        ),
    )

    assert first.status is ExternalAnalysisStatus.COMPLETE
    assert second.status is ExternalAnalysisStatus.COMPLETE
    assert first.resource_limits["max_export_bytes"] == 1000
    assert second.resource_limits["max_export_bytes"] == 2000
    assert first.run_fingerprint != second.run_fingerprint


def test_pinned_raw_hash_is_exact_retained_bytes_not_canonical_json(tmp_path: Path) -> None:
    compact = b'{"schema":"satn-external-analysis-export/v1","observations":[]}\n'
    spaced = b'{\n  "schema": "satn-external-analysis-export/v1",\n  "observations": []\n}\n'
    first_path = tmp_path / "compact.json"
    second_path = tmp_path / "spaced.json"
    first_path.write_bytes(compact)
    second_path.write_bytes(spaced)
    first_hash = hashlib.sha256(compact).hexdigest()
    second_hash = hashlib.sha256(spaced).hexdigest()

    first = run_governed_external_analysis(
        _request(source=first_hash),
        PinnedExternalAnalysisAdapter(
            export_path=first_path,
            expected_export_sha256=first_hash,
        ),
    )
    second = run_governed_external_analysis(
        _request(source=second_hash),
        PinnedExternalAnalysisAdapter(
            export_path=second_path,
            expected_export_sha256=second_hash,
        ),
    )

    assert first.raw_output_hash == first_hash
    assert second.raw_output_hash == second_hash
    assert first.raw_output_hash != second.raw_output_hash
    assert first.normalized_observation_hash == second.normalized_observation_hash


def test_fixture_raw_hash_uses_its_declared_exact_response_bytes() -> None:
    adapter = _fixture_adapter()
    result = run_governed_external_analysis(_request(), adapter)

    expected_payload = {
        "schema": "satn-external-analysis-fixture/v1",
        "fixture_id": adapter.fixture_id,
        "observations": tuple(item.canonical_payload() for item in adapter.observations),
    }
    expected_bytes = json.dumps(
        expected_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    assert result.raw_output_hash == hashlib.sha256(expected_bytes).hexdigest()
