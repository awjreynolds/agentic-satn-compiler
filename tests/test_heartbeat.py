"""Tests for bounded compiler liveness reporting."""

from __future__ import annotations

import inspect
import logging
import shutil
import time
from io import StringIO
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
    while (
        sum("event=satn_heartbeat" in record.getMessage() for record in caplog.records)
        < count
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)
    assert (
        sum("event=satn_heartbeat" in record.getMessage() for record in caplog.records)
        >= count
    )


def test_progress_reports_phase_transitions_and_completion_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter((10.0, 10.0, 12.5, 14.0))
    monkeypatch.setattr("satn.heartbeat.time.perf_counter", lambda: next(clock))
    stdout = StringIO()
    stderr = StringIO()
    logger = logging.getLogger("tests.heartbeat.stderr")
    handler = logging.StreamHandler(stderr)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        with StageHeartbeat(
            logger,
            "snapshot-acquisition",
            {"completed": 2, "total": 5},
            interval_seconds=60,
        ) as heartbeat:
            print("machine-readable-result", file=stdout)
            heartbeat.set_stage("snapshot-validation")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True

    assert stdout.getvalue() == "machine-readable-result\n"
    messages = stderr.getvalue().splitlines()
    assert messages == [
        (
            "event=satn_progress status=started stage=snapshot-acquisition "
            'elapsed_seconds=0.0 context={"completed": 2, "total": 5}'
        ),
        (
            "event=satn_progress status=running stage=snapshot-validation "
            'elapsed_seconds=2.5 context={"completed": 2, "total": 5}'
        ),
        (
            "event=satn_progress status=completed stage=snapshot-validation "
            'elapsed_seconds=4.0 context={"completed": 2, "total": 5}'
        ),
    ]


@pytest.mark.parametrize(
    ("exception", "status"),
    [
        (RuntimeError("failed"), "failed"),
        (KeyboardInterrupt(), "interrupted"),
    ],
)
def test_progress_reports_failed_and_interrupted_terminal_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    exception: BaseException,
    status: str,
) -> None:
    clock = iter((20.0, 20.0, 23.0))
    monkeypatch.setattr("satn.heartbeat.time.perf_counter", lambda: next(clock))
    logger = logging.getLogger(f"tests.heartbeat.{status}")
    caplog.set_level(logging.INFO, logger=logger.name)

    with (
        pytest.raises(type(exception)),
        StageHeartbeat(logger, "network-compilation", {}, interval_seconds=60),
    ):
        raise exception

    assert (
        f"event=satn_progress status={status} stage=network-compilation "
        "elapsed_seconds=3.0 context={}"
    ) in [record.getMessage() for record in caplog.records]


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

    message = next(
        record.getMessage()
        for record in caplog.records
        if "event=satn_heartbeat" in record.getMessage()
    )
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
