"""Tests for bounded compiler liveness reporting."""

from __future__ import annotations

import inspect
import logging
import shutil
import time
from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import pytest

from satn import compile
from satn.heartbeat import DEFAULT_HEARTBEAT_INTERVAL_SECONDS, StageHeartbeat
from satn.models import CouncilConfig
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


class RecordingHeartbeat:
    """A no-wait heartbeat replacement that records public API stage wiring."""

    instances: ClassVar[list[RecordingHeartbeat]] = []

    def __init__(
        self,
        _logger: logging.Logger,
        stage: str,
        context: dict[str, object],
        *,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.stages = [stage]
        self.context = context
        self.interval_seconds = interval_seconds
        self.instances.append(self)

    def __enter__(self) -> RecordingHeartbeat:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def set_stage(self, stage: str) -> None:
        self.stages.append(stage)


def _fixture_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    return CouncilConfig.from_yaml(fixture / "council.yaml")


def _wait_for_heartbeats(caplog: pytest.LogCaptureFixture, count: int) -> None:
    deadline = time.monotonic() + 1
    while len(caplog.records) < count and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(caplog.records) >= count


def test_heartbeat_logs_current_stage_context_and_elapsed_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.heartbeat")
    caplog.set_level(logging.INFO, logger=logger.name)

    with StageHeartbeat(
        logger,
        "snapshot-acquisition",
        {"area_id": "west-of-england", "snapshot_id": "weca-osm-current"},
        interval_seconds=0.01,
    ) as heartbeat:
        _wait_for_heartbeats(caplog, 1)
        heartbeat.set_stage("snapshot-validation")
        _wait_for_heartbeats(caplog, 2)

    messages = [record.getMessage() for record in caplog.records]
    assert any("event=satn_heartbeat stage=snapshot-acquisition" in message for message in messages)
    assert any("stage=snapshot-validation" in message for message in messages)
    assert all("elapsed_seconds=" in message for message in messages)
    assert all('"area_id": "west-of-england"' in message for message in messages)
    assert not heartbeat.running


def test_heartbeat_merges_operational_progress_context(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tests.heartbeat.progress")
    caplog.set_level(logging.INFO, logger=logger.name)

    with StageHeartbeat(
        logger,
        "cross-spine-assembly",
        {"area_id": "west-of-england"},
        interval_seconds=0.01,
    ) as heartbeat:
        heartbeat.update_context(
            {
                "cross_spine_connectors_assessed": 4,
                "cross_spine_connectors_total": 10,
                "cross_spine_throughput_connectors_per_second": 2.5,
                "cross_spine_estimated_remaining_seconds": 2.4,
            }
        )
        _wait_for_heartbeats(caplog, 1)

    message = caplog.records[0].getMessage()
    assert '"cross_spine_connectors_assessed": 4' in message
    assert '"cross_spine_estimated_remaining_seconds": 2.4' in message


def test_heartbeat_stops_when_guarded_work_fails(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tests.heartbeat.failure")
    caplog.set_level(logging.INFO, logger=logger.name)
    heartbeat = StageHeartbeat(logger, "network-compilation", {}, interval_seconds=0.01)

    with pytest.raises(RuntimeError, match="failed"), heartbeat:
        _wait_for_heartbeats(caplog, 1)
        raise RuntimeError("failed")

    messages_before_wait = [record.getMessage() for record in caplog.records]
    time.sleep(0.03)
    assert [record.getMessage() for record in caplog.records] == messages_before_wait
    assert not heartbeat.running


def test_heartbeat_rejects_a_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        StageHeartbeat(logging.getLogger("tests.heartbeat"), "stage", {}, interval_seconds=0)


def test_default_heartbeat_interval_is_thirty_seconds() -> None:
    parameter = inspect.signature(StageHeartbeat).parameters["interval_seconds"]

    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 30.0
    assert parameter.default == DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def test_public_snapshot_heartbeats_existing_snapshot_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _fixture_config(tmp_path)
    RecordingHeartbeat.instances.clear()
    monkeypatch.setattr("satn.sources.StageHeartbeat", RecordingHeartbeat)

    snapshot(config)
    existing_snapshot = snapshot(config)

    heartbeat = RecordingHeartbeat.instances[-1]
    assert existing_snapshot == config.source.snapshot_dir / config.source.snapshot_id
    assert heartbeat.stages == ["snapshot-acquisition", "existing-snapshot-validation"]
    assert heartbeat.context == {
        "area_id": config.area_id,
        "snapshot_id": config.source.snapshot_id,
        "source_kind": "fixture",
    }
    assert heartbeat.interval_seconds == DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def test_public_compile_heartbeats_seeded_atm_and_publication_preparation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _fixture_config(tmp_path)
    config.atm.enabled = True
    config.atm.mode = "seeded"
    config.compilation.agent.review_statuses = ()
    RecordingHeartbeat.instances.clear()
    monkeypatch.setattr("satn.sources.StageHeartbeat", RecordingHeartbeat)
    monkeypatch.setattr("satn.pipeline.StageHeartbeat", RecordingHeartbeat)
    monkeypatch.setattr(
        "satn.pipeline.load_atm",
        lambda _config: gpd.GeoDataFrame(
            {"portal_feature_id": []}, geometry=[], crs=4326
        ),
    )

    snapshot(config)
    RecordingHeartbeat.instances.clear()
    result = compile(config)

    heartbeat = RecordingHeartbeat.instances[-1]
    assert result.status == "complete"
    assert heartbeat.context == {
        "area_id": config.area_id,
        "snapshot_id": config.source.snapshot_id,
    }
    assert heartbeat.stages == [
        "publication-reuse-check",
        "snapshot-load",
        "atm-seeded-load-reprojection",
        "network-compilation",
        "cross-spine-assembly",
        "network-compilation",
        "atm-comparison",
        "post-compilation-artifact-preparation",
        "publication-fingerprint",
        "publication",
    ]
