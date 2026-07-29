from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from satn.cli import app
from satn.local_evidence_store import EvidenceWriterBusy

RUNNER = CliRunner()
STATE = "a" * 64


class _Operations:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = SimpleNamespace(
            store=tmp_path / "evidence.duckdb",
            extension_cache=tmp_path / "extensions",
        )
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error: Exception | None = None

    def _call(self, command: str, values: dict[str, object]) -> dict[str, object]:
        self.calls.append((command, values))
        if self.error is not None:
            raise self.error
        return {
            "ok": True,
            "command": command,
            "store": str(self.paths.store),
            "extension_cache": str(self.paths.extension_cache),
            "state": STATE,
            "values": values,
            "exit_code": 0,
        }

    def initialise(self, **values) -> dict[str, object]:
        return self._call("init", values)

    def refresh(self, **values) -> dict[str, object]:
        return self._call("refresh", values)

    def status(self, **values) -> dict[str, object]:
        return self._call("status", values)

    def query(self, **values) -> dict[str, object]:
        return self._call("query", values)

    def delete(self, **values) -> dict[str, object]:
        return self._call("delete", values)


@pytest.fixture
def operations(tmp_path: Path, monkeypatch) -> _Operations:
    from satn import evidence_cli

    instance = _Operations(tmp_path)

    class StoreFacade:
        @classmethod
        def workspace(cls, **_values):
            return instance

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", StoreFacade)
    return instance


def test_evidence_help_exposes_only_the_five_operational_commands() -> None:
    result = RUNNER.invoke(app, ["evidence", "--help"])

    assert result.exit_code == 0
    for command in ("init", "refresh", "status", "query", "delete"):
        assert command in result.stdout
    for forbidden in ("sql", "vacuum", "install", "daemon", "download"):
        assert forbidden not in result.stdout.lower()


def test_text_and_json_render_the_same_deep_status_result(
    operations: _Operations,
) -> None:
    common = ["evidence", "--workspace", "workspace"]

    as_json = RUNNER.invoke(app, [*common, "--format", "json", "status"])
    as_text = RUNNER.invoke(app, [*common, "status"])

    assert as_json.exit_code == as_text.exit_code == 0
    payload = json.loads(as_json.stdout)
    assert payload["state"] == STATE
    assert f"state: {STATE}" in as_text.stdout
    assert operations.calls == [
        (
            "status",
            {"area": None, "state": None, "verify": False, "provenance": False},
        ),
        (
            "status",
            {"area": None, "state": None, "verify": False, "provenance": False},
        ),
    ]


def test_status_and_delete_forward_acceptance_safety_options(
    operations: _Operations,
    tmp_path: Path,
) -> None:
    area = tmp_path / "area.yaml"

    status = RUNNER.invoke(
        app,
        [
            "evidence",
            "status",
            "--area",
            str(area),
            "--state",
            STATE,
            "--verify",
            "--provenance",
        ],
    )
    delete = RUNNER.invoke(
        app,
        [
            "evidence",
            "delete",
            "--yes",
            "--expect-state",
            STATE,
        ],
    )

    assert status.exit_code == delete.exit_code == 0
    assert operations.calls == [
        (
            "status",
            {
                "area": area,
                "state": STATE,
                "verify": True,
                "provenance": True,
            },
        ),
        ("delete", {"yes": True, "expect_state": STATE}),
    ]


def test_refresh_forwards_all_raw_options_without_domain_work(
    operations: _Operations,
    tmp_path: Path,
) -> None:
    area = tmp_path / "area.yaml"
    first = tmp_path / "roads.yaml"
    second = tmp_path / "osm.yaml"

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "refresh",
            str(area),
            "--source-export",
            str(first),
            "--source-export",
            str(second),
            "--replace-source",
            "os-open-roads/RoadLink",
            "--expect-state",
            STATE,
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert operations.calls == [
        (
            "refresh",
            {
                "area": area,
                "source_exports": (first, second),
                "replace_source": "os-open-roads/RoadLink",
                "expect_state": STATE,
                "dry_run": True,
                "rebuild": False,
            },
        )
    ]


def test_query_forwards_closed_selector_filter_and_export_options(
    operations: _Operations,
    tmp_path: Path,
) -> None:
    export = tmp_path / "result.gpkg"

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "query",
            "os-open-roads/RoadLink",
            "--bbox",
            "350000,160000,360000,170000",
            "--where",
            'road_classification="A Road"',
            "--field",
            "road_classification",
            "--export-gpkg",
            str(export),
        ],
    )

    assert result.exit_code == 0
    command, values = operations.calls[0]
    assert command == "query"
    assert values["bbox"] == "350000,160000,360000,170000"
    assert values["where"] == ('road_classification="A Road"',)
    assert values["fields"] == ("road_classification",)
    assert values["export_gpkg"] == export


def test_typed_domain_busy_and_typer_usage_exits_remain_distinct(
    operations: _Operations,
) -> None:
    operations.error = ValueError("coverage state is stale")
    domain = RUNNER.invoke(app, ["evidence", "--format", "json", "status"])
    operations.error = EvidenceWriterBusy("writer lock is busy")
    busy = RUNNER.invoke(app, ["evidence", "--format", "json", "status"])
    usage = RUNNER.invoke(app, ["evidence", "delete"])

    assert domain.exit_code == 1
    assert json.loads(domain.stdout)["error"] == "coverage state is stale"
    assert busy.exit_code == 75
    assert json.loads(busy.stdout)["error"] == "writer lock is busy"
    assert usage.exit_code == 2
