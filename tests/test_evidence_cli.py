from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import LineString, box
from typer.testing import CliRunner

from satn.cli import app

RUNNER = CliRunner()
SHA = "a" * 64


@dataclass(frozen=True)
class _Coverage:
    fingerprint: str = SHA
    state: str = "complete"
    attestations: tuple[object, ...] = ()
    requested_partition_keys: tuple[object, ...] = ()


class _Store:
    initialised = 0

    def __init__(
        self,
        *,
        store_path: Path,
        runtime_lock_path: Path,
        extension_cache: Path,
    ) -> None:
        self.store_path = store_path
        self.runtime_lock_path = runtime_lock_path
        self.extension_cache = extension_cache

    def initialise(self) -> None:
        type(self).initialised += 1

    def status(self, *, verify: bool = False) -> object:
        return SimpleNamespace(state="ready", current_coverage=_Coverage())


def test_evidence_help_exposes_only_the_five_operational_commands() -> None:
    result = RUNNER.invoke(app, ["evidence", "--help"])

    assert result.exit_code == 0
    for command in ("init", "refresh", "status", "query", "delete"):
        assert command in result.stdout
    for forbidden in ("sql", "vacuum", "install", "daemon", "download"):
        assert forbidden not in result.stdout.lower()


def test_init_resolves_paths_deterministically_and_json_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    _Store.initialised = 0
    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", _Store)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")

    args = [
        "evidence",
        "--workspace",
        str(tmp_path / "workspace"),
        "--format",
        "json",
        "init",
    ]
    first = RUNNER.invoke(app, args)
    second = RUNNER.invoke(app, args)

    assert first.exit_code == second.exit_code == 0
    assert json.loads(first.stdout) == json.loads(second.stdout)
    payload = json.loads(first.stdout)
    assert payload == {
        "ok": True,
        "command": "init",
        "store": str((tmp_path / "workspace" / ".satn/evidence/local-evidence.duckdb").resolve()),
        "extension_cache": str((tmp_path / "workspace" / ".satn/evidence/extensions").resolve()),
        "state": SHA,
        "created": False,
        "exit_code": 0,
    }
    assert _Store.initialised == 2


def test_status_text_and_json_report_the_same_coverage_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", _Store)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    common = ["evidence", "--workspace", str(tmp_path)]

    json_result = RUNNER.invoke(app, [*common, "--format", "json", "status"])
    text_result = RUNNER.invoke(app, [*common, "status"])

    assert json_result.exit_code == text_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["state"] == SHA
    assert payload["coverage"]["status"] == "complete"
    assert f"state: {SHA}" in text_result.stdout
    assert "coverage.status: complete" in text_result.stdout


def test_delete_requires_expected_state_and_moves_store_to_recoverable_trash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", _Store)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    store = tmp_path / "evidence.duckdb"
    store.write_bytes(b"duckdb")
    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--store",
            str(store),
            "--format",
            "json",
            "delete",
            "--yes",
            "--expect-state",
            SHA,
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    restore_path = Path(payload["restore_path"])
    assert not store.exists()
    assert restore_path.is_file()
    assert restore_path.read_bytes() == b"duckdb"
    assert restore_path.parent == tmp_path / "trash"


def test_domain_failure_is_exit_one_and_usage_failure_remains_exit_two(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    class BrokenStore(_Store):
        def status(self, *, verify: bool = False) -> object:
            raise ValueError("coverage state is stale")

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", BrokenStore)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")

    domain = RUNNER.invoke(
        app,
        ["evidence", "--workspace", str(tmp_path), "--format", "json", "status"],
    )
    usage = RUNNER.invoke(app, ["evidence", "delete"])

    assert domain.exit_code == 1
    assert json.loads(domain.stdout)["error"] == "coverage state is stale"
    assert usage.exit_code == 2


def test_refresh_dry_run_reports_exact_plan_without_mutating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    calls = {"plan": 0, "refresh": 0}
    binding = SimpleNamespace(
        source_export=SimpleNamespace(fingerprint="b" * 64),
        ingestion_contract=SimpleNamespace(source_layer="os-open-roads/RoadLink"),
    )

    class PlanningStore(_Store):
        def plan_refresh(self, **kwargs) -> object:
            calls["plan"] += 1
            return SimpleNamespace(
                coverage=_Coverage("c" * 64),
                reused_cells=("ST56",),
                missing_cells=("ST57",),
                replaced_cells=(),
            )

        def refresh(self, **kwargs) -> object:
            calls["refresh"] += 1
            raise AssertionError("dry-run must not refresh")

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", PlanningStore)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(
        evidence_cli,
        "_load_area_geometry",
        lambda _path: box(350000, 160000, 360000, 170000),
    )
    monkeypatch.setattr(evidence_cli, "_load_source_descriptor", lambda _path: binding)
    monkeypatch.setattr(
        evidence_cli,
        "_partition_keys",
        lambda _layer, _selector: (SimpleNamespace(cell="ST56"), SimpleNamespace(cell="ST57")),
    )

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
            "refresh",
            str(tmp_path / "area.yaml"),
            "--source-export",
            str(tmp_path / "open-roads.yaml"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["state"] == "c" * 64
    assert payload["dry_run"] is True
    assert payload["reused_cells"] == ["ST56"]
    assert payload["missing_cells"] == ["ST57"]
    assert calls == {"plan": 1, "refresh": 0}


def test_refresh_replacement_requires_layer_and_expected_state_together(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", _Store)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
            "refresh",
            str(tmp_path / "area.yaml"),
            "--replace-source",
            "os-open-roads/RoadLink",
        ],
    )

    assert result.exit_code == 1
    assert "--replace-source and --expect-state must be supplied together" in result.stdout


def test_refresh_rebuild_holds_one_writer_lock_for_both_operations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    locked = False
    binding = SimpleNamespace(
        source_export=SimpleNamespace(fingerprint="b" * 64),
        ingestion_contract=SimpleNamespace(source_layer="os-open-roads/RoadLink"),
    )

    @contextmanager
    def writer_lock(_paths):
        nonlocal locked
        assert locked is False
        locked = True
        try:
            yield tmp_path / "evidence.duckdb.lock"
        finally:
            locked = False

    class RebuildingStore(_Store):
        def plan_refresh(self, **kwargs) -> object:
            assert locked
            return SimpleNamespace(
                coverage=_Coverage("b" * 64),
                reused_cells=(),
                missing_cells=("ST56",),
                replaced_cells=(),
            )

        def refresh(self, **kwargs) -> object:
            assert locked
            return SimpleNamespace(coverage=_Coverage("c" * 64))

        def rebuild(self) -> object:
            assert locked
            return SimpleNamespace(coverage=_Coverage("d" * 64))

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", RebuildingStore)
    monkeypatch.setattr(evidence_cli, "_writer_lock", writer_lock)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(evidence_cli, "_load_area_geometry", lambda _path: box(0, 0, 1, 1))
    monkeypatch.setattr(evidence_cli, "_load_source_descriptor", lambda _path: binding)
    monkeypatch.setattr(
        evidence_cli,
        "_partition_keys",
        lambda _layer, _selector: (SimpleNamespace(cell="ST56"),),
    )

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
            "refresh",
            str(tmp_path / "area.yaml"),
            "--source-export",
            str(tmp_path / "open-roads.yaml"),
            "--rebuild",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["state"] == "d" * 64
    assert locked is False


def test_query_parses_bbox_and_closed_equality_filters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    captured: dict[str, object] = {}
    geometry = LineString([(350000, 165000), (351000, 166000)])
    row = SimpleNamespace(
        source_export_fingerprint="d" * 64,
        logical_key="roadlink:100",
        feature_content_fingerprint="e" * 64,
        geometry_fingerprint="f" * 64,
        geometry=geometry,
        crs="EPSG:27700",
        attributes={"road_classification": "A Road"},
        attestation_fingerprints=("1" * 64,),
        fingerprint="2" * 64,
    )

    class QueryStore(_Store):
        def query(self, **kwargs) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                rows=(row,),
                manifest={
                    "coverage_state_fingerprint": SHA,
                    "row_count": 1,
                    "predicate": "intersects",
                },
                fingerprint="3" * 64,
            )

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", QueryStore)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
            "query",
            "os-open-roads/RoadLink",
            "--bbox",
            "350000,160000,360000,170000",
            "--where",
            'road_classification="A Road"',
            "--field",
            "road_classification",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["state"] == SHA
    assert payload["row_count"] == 1
    assert payload["rows"][0]["logical_key"] == "roadlink:100"
    assert captured["bbox"] == (350000.0, 160000.0, 360000.0, 170000.0)
    assert captured["filters"] == {"road_classification": "A Road"}
    assert captured["projection"] == ("road_classification",)


def test_query_requires_exactly_one_geometry_selector(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", _Store)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
            "query",
            "os-open-roads/RoadLink",
        ],
    )

    assert result.exit_code == 1
    assert "exactly one of --area, --geometry or --bbox" in result.stdout


def test_status_area_distinguishes_missing_no_data_and_explicit_unknown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    def attestation(cell: str, availability: str) -> object:
        key = SimpleNamespace(
            source_layer="os-open-roads/RoadLink",
            cell=cell,
            fingerprint=f"{cell:0<64}"[:64],
        )
        contract = SimpleNamespace(
            fingerprint="4" * 64,
            canonical_payload=lambda: {"contract": "ingestion"},
        )
        content = SimpleNamespace(
            partition_key=key,
            ingestion_contract=contract,
            availability=availability,
            fingerprint="5" * 64,
        )
        export = SimpleNamespace(
            fingerprint="6" * 64,
            provenance={"retained_path": str(tmp_path / "raw.geojson")},
            canonical_payload=lambda: {"contract": "source"},
        )
        return SimpleNamespace(
            partition_content=content,
            source_export=export,
            fingerprint="7" * 64,
        )

    coverage = _Coverage(
        attestations=(
            attestation("ST56", "explicit-unknown"),
            attestation("ST57", "no-data"),
        )
    )

    class StatusStore(_Store):
        def status(self, *, verify: bool = False) -> object:
            return SimpleNamespace(state="ready", current_coverage=coverage)

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", StatusStore)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(evidence_cli, "_load_area_geometry", lambda _path: box(0, 0, 1, 1))
    monkeypatch.setattr(
        evidence_cli,
        "_partition_keys",
        lambda _layer, _selector: (
            SimpleNamespace(cell="ST56"),
            SimpleNamespace(cell="ST57"),
            SimpleNamespace(cell="ST58"),
        ),
    )

    result = RUNNER.invoke(
        app,
        [
            "evidence",
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
            "status",
            "--area",
            str(tmp_path / "area.yaml"),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)["coverage"]
    assert payload["no_data"] == 1
    assert payload["explicit_unknown"] == 1
    assert payload["explicit_unknown_cells"] == ["os-open-roads/RoadLink:ST56"]
    assert payload["missing"] == ["os-open-roads/RoadLink:ST58"]
    assert payload["area_status"] == "incomplete"


def test_query_empty_result_can_be_exported_but_not_over_a_retained_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import evidence_cli

    result_object = SimpleNamespace(
        rows=(),
        manifest={
            "coverage_state_fingerprint": SHA,
            "row_count": 0,
            "predicate": "intersects",
        },
        fingerprint="8" * 64,
    )
    retained = tmp_path / "retained.geojson"
    retained.write_text("retained", encoding="utf-8")
    attestation = SimpleNamespace(
        source_export=SimpleNamespace(provenance={"retained_path": str(retained)})
    )

    class ExportStore(_Store):
        def query(self, **kwargs) -> object:
            return result_object

        def status(self, *, verify: bool = False) -> object:
            return SimpleNamespace(
                state="ready",
                current_coverage=_Coverage(attestations=(attestation,)),
            )

    monkeypatch.setattr(evidence_cli, "LocalEvidenceStore", ExportStore)
    monkeypatch.setattr(evidence_cli, "_runtime_lock_path", lambda: tmp_path / "runtime.json")
    export = tmp_path / "result.gpkg"
    common = [
        "evidence",
        "--workspace",
        str(tmp_path),
        "--format",
        "json",
        "query",
        "os-open-roads/RoadLink",
        "--bbox",
        "350000,160000,360000,170000",
        "--export-gpkg",
    ]

    success = RUNNER.invoke(app, [*common, str(export)])
    collision = RUNNER.invoke(app, [*common, str(retained)])

    assert success.exit_code == 0
    assert export.is_file()
    assert export.with_suffix(".gpkg.manifest.json").is_file()
    assert collision.exit_code == 1
    assert "retained Source Export" in collision.stdout
    assert retained.read_text(encoding="utf-8") == "retained"


def test_source_descriptor_resolves_raw_path_relative_to_descriptor(
    tmp_path: Path,
) -> None:
    import hashlib

    from satn.evidence_cli import _load_source_descriptor

    raw = tmp_path / "exports" / "roads.geojson"
    raw.parent.mkdir()
    raw.write_text("governed bytes", encoding="utf-8")
    descriptor = tmp_path / "descriptors" / "roads.yaml"
    descriptor.parent.mkdir()
    descriptor.write_text(
        "\n".join(
            (
                "source_family: os-open-roads",
                "dataset: open-roads",
                "layer: RoadLink",
                "publisher_release: '2026-04'",
                "effective_date: '2026-04-07'",
                "licence: OGL-3.0",
                "format: GeoJSON",
                "declared_crs: EPSG:27700",
                f"raw_bytes_sha256: {hashlib.sha256(raw.read_bytes()).hexdigest()}",
                "path: ../exports/roads.geojson",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    binding = _load_source_descriptor(descriptor)

    assert binding.source_export.provenance["retained_path"] == str(raw.resolve())
    assert binding.source_export.provenance["descriptor_path"] == str(descriptor.resolve())
    assert binding.ingestion_contract.source_layer == "os-open-roads/RoadLink"
