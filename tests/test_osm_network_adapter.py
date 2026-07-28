from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point

from satn import osm_network_adapter as adapter
from satn.evidence_contracts import EvidencePartitionKey, IngestionContract, SourceExport

PROJECT = Path(__file__).parents[1]
RAW_OSM_FIXTURE = PROJECT / "tests" / "fixtures" / "osm-network.xml"


def _raw_osm(tmp_path: Path, *, replacement: tuple[str, str] | None = None) -> Path:
    contents = RAW_OSM_FIXTURE.read_text()
    if replacement is not None:
        contents = contents.replace(*replacement)
    path = tmp_path / "network.osm"
    path.write_text(contents)
    return path


def _source_export(
    path: Path,
    *,
    publisher_release: str = "2026-07-27T10:11:12Z",
    effective_date: str = "2026-07-27",
) -> SourceExport:
    return SourceExport(
        source_family="openstreetmap",
        dataset="network",
        layer="lines",
        publisher_release=publisher_release,
        effective_date=effective_date,
        licence="ODbL-1.0",
        format="OSM XML",
        declared_crs="EPSG:4326",
        raw_bytes_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance={"retained_path": str(path.resolve())},
    )


def _contract() -> IngestionContract:
    payload = adapter.contract_payload()
    payload.pop("contract")
    return IngestionContract(**payload)


def _partition(cell: str) -> EvidencePartitionKey:
    return EvidencePartitionKey(adapter.SOURCE_LAYER, "bng-10km/v1", cell)


def _changed_export(export: SourceExport, **changes: object) -> SourceExport:
    return replace(export, fingerprint="", **changes)


def test_validate_export_binds_raw_xml_receipt_schema_and_provenance(tmp_path: Path) -> None:
    path = _raw_osm(tmp_path)
    source_export = _source_export(path)
    contract = _contract()

    assert adapter.validate_export(source_export, contract) == path.resolve()
    assert contract.source_layer == "openstreetmap/lines"
    assert contract.stable_feature_key_policy == "source-export-scoped-osm-way-id/v1"
    assert contract.normalisation["raw_xml_receipt"] == {
        "root": {"element": "osm", "version": "0.6"},
        "meta": {"element": "meta", "attribute": "osm_base"},
        "timestamp_format": "UTC-RFC3339-seconds/Z",
        "source_export_binding": {
            "publisher_release": "exact osm_base",
            "effective_date": "UTC date of osm_base",
        },
    }
    assert source_export.provenance == {"retained_path": str(path.resolve())}


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"source_family": "osm"}, "unsupported"),
        ({"dataset": "roads"}, "unsupported"),
        ({"layer": "network"}, "unsupported"),
        ({"format": "OSM PBF"}, "OSM XML exports only"),
        ({"declared_crs": "EPSG:27700"}, "declared_crs EPSG:4326"),
        ({"licence": "Open Database License"}, "licence ODbL-1.0"),
    ),
)
def test_validate_export_rejects_any_unaccepted_source_declaration(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    source_export = _changed_export(_source_export(_raw_osm(tmp_path)), **changes)

    with pytest.raises(ValueError, match=message):
        adapter.validate_export(source_export, _contract())


def test_validate_export_rejects_tamper_and_raw_xml_receipt_mismatches(tmp_path: Path) -> None:
    path = _raw_osm(tmp_path)
    source_export = _source_export(path)
    path.write_text(path.read_text() + "\n<!-- tampered -->\n")

    with pytest.raises(ValueError, match="checksum"):
        adapter.validate_export(source_export, _contract())

    path = _raw_osm(tmp_path, replacement=("<meta osm_base=", "<metadata osm_base="))
    with pytest.raises(ValueError, match="exactly one <meta"):
        adapter.validate_export(_source_export(path), _contract())

    path = _raw_osm(tmp_path, replacement=('version="0.6"', 'version="0.5"'))
    with pytest.raises(ValueError, match="root <osm"):
        adapter.validate_export(_source_export(path), _contract())

    path = _raw_osm(tmp_path)
    with pytest.raises(ValueError, match="publisher_release"):
        adapter.validate_export(
            _source_export(path, publisher_release="2026-07-28T10:11:12Z"), _contract()
        )
    with pytest.raises(ValueError, match="effective_date"):
        adapter.validate_export(_source_export(path, effective_date="2026-07-28"), _contract())


@pytest.mark.parametrize(
    ("replacement", "message"),
    (
        (
            (
                '<meta osm_base="2026-07-27T10:11:12Z" />',
                '<meta osm_base="2026-07-27T10:11:12Z" /><meta osm_base="2026-07-27T10:11:12Z" />',
            ),
            "exactly one <meta",
        ),
        (
            (
                'osm_base="2026-07-27T10:11:12Z" />',
                'osm_base="2026-07-27T10:11:12Z" malformed="yes" />',
            ),
            "only the osm_base",
        ),
        (("2026-07-27T10:11:12Z", "2026-02-30T10:11:12Z"), "valid UTC timestamp"),
        (("<osm version=", '<osm xmlns="urn:osm" version='), "root <osm"),
        (
            ("<meta osm_base=", '<osm-meta:meta xmlns:osm-meta="urn:osm" osm_base='),
            "namespace-qualified",
        ),
    ),
)
def test_validate_export_rejects_malformed_namespaced_or_duplicate_xml_receipts(
    tmp_path: Path, replacement: tuple[str, str], message: str
) -> None:
    path = _raw_osm(tmp_path, replacement=replacement)

    with pytest.raises(ValueError, match=message):
        adapter.validate_export(_source_export(path), _contract())


def test_validate_export_rejects_non_xml_and_pbf_bytes(tmp_path: Path) -> None:
    for name, contents in (
        ("not-xml.osm", b"not XML"),
        ("network.osm.pbf", b"\x00\x00\x00\x0aOSMHeader"),
    ):
        path = tmp_path / name
        path.write_bytes(contents)

        with pytest.raises(ValueError, match="well-formed OSM XML"):
            adapter.validate_export(_source_export(path), _contract())


def test_validate_export_requires_absolute_provenance_and_its_current_fingerprint(
    tmp_path: Path,
) -> None:
    path = _raw_osm(tmp_path)
    source_export = _source_export(path)
    relative = _changed_export(source_export, provenance={"retained_path": "network.osm"})

    with pytest.raises(ValueError, match="must be absolute"):
        adapter.validate_export(relative, _contract())

    untrusted_contract = replace(
        _contract(),
        fingerprint="",
        implementation_dependency_fingerprint="0" * 64,
    )
    with pytest.raises(ValueError, match="untrusted"):
        adapter.validate_export(source_export, untrusted_contract)


def test_validate_export_rejects_ogr_schema_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read_info = adapter.pyogrio.read_info

    def schema_without_other_tags(*args: object, **kwargs: object) -> dict[str, object]:
        info = dict(original_read_info(*args, **kwargs))
        info["fields"] = tuple(field for field in info["fields"] if field != "other_tags")
        info["dtypes"] = tuple(info["dtypes"][:-1])
        return info

    monkeypatch.setattr(adapter.pyogrio, "read_info", schema_without_other_tags)

    with pytest.raises(ValueError, match="closed OGR schema"):
        adapter.validate_export(_source_export(_raw_osm(tmp_path)), _contract())


def test_validate_export_restores_ogr_config_after_reader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = adapter.pyogrio.get_gdal_config_option("OSM_CONFIG_FILE")

    def failing_read_info(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated OGR failure")

    monkeypatch.setattr(adapter.pyogrio, "read_info", failing_read_info)

    with pytest.raises(ValueError, match="cannot inspect"):
        adapter.validate_export(_source_export(_raw_osm(tmp_path)), _contract())
    assert adapter.pyogrio.get_gdal_config_option("OSM_CONFIG_FILE") == prior


def test_read_partitions_scans_once_preserves_full_ways_and_fans_requested_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_export = _source_export(_raw_osm(tmp_path))
    contract = _contract()
    source_path = adapter.validate_export(source_export, contract)
    original_read_dataframe = adapter.pyogrio.read_dataframe
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def counted_read_dataframe(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return original_read_dataframe(*args, **kwargs)

    monkeypatch.setattr(adapter.pyogrio, "read_dataframe", counted_read_dataframe)
    requested = (_partition("ST86"), _partition("ST75"), _partition("ST66"), _partition("ST76"))

    partitions = adapter.read_partitions(source_path, source_export, contract, requested)

    assert len(calls) == 1
    assert calls[0][1] == {"layer": "lines", "columns": ["osm_id", "name", "highway", "other_tags"]}
    assert [partition.partition_key.cell for partition in partitions] == [
        "ST66",
        "ST75",
        "ST76",
        "ST86",
    ]
    by_cell = {partition.partition_key.cell: partition.features for partition in partitions}
    assert by_cell["ST66"] == ()
    assert [feature.logical_key for feature in by_cell["ST75"]] == ["osm-way:2002"]
    assert [feature.logical_key for feature in by_cell["ST76"]] == ["osm-way:2001"]
    assert [feature.logical_key for feature in by_cell["ST86"]] == ["osm-way:2001"]

    crossing = by_cell["ST76"][0]
    assert crossing.geometry.equals(by_cell["ST86"][0].geometry)
    assert crossing.geometry.bounds[0] < 375_000
    assert crossing.geometry.bounds[2] > 385_000
    assert crossing.attributes == {
        "name": "Main & Cross Road",
        "highway": "residential",
        "ref": "C123",
        "oneway": "yes",
        "surface": "asphalt",
        "access": "permissive",
        "bicycle": "yes",
        "foot": "designated",
        "cycleway": "lane",
        "service": None,
        "tracktype": "grade1",
        "bridge": "no",
        "tunnel": "no",
        "junction": None,
        "maxspeed": "20 mph",
        "lanes": "2",
        "width": "3.2",
        "lit": "yes",
        "ele": "45.5",
        "incline": "-5%",
    }
    assert by_cell["ST75"][0].attributes["highway"] == "footway"
    assert by_cell["ST75"][0].attributes["bicycle"] == "yes"
    assert all(
        value is None
        for name, value in by_cell["ST75"][0].attributes.items()
        if name not in {"highway", "bicycle"}
    )


@pytest.mark.parametrize(
    ("replacement", "message"),
    (
        ("2002", "duplicate osm_id"),
        ("2001,2002", "unambiguous positive way identifier"),
        ("02001", "unambiguous positive way identifier"),
        ("   ", "non-empty way identifier"),
    ),
)
def test_read_partitions_rejects_duplicate_missing_or_ambiguous_way_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    message: str,
) -> None:
    source_export = _source_export(_raw_osm(tmp_path))
    contract = _contract()
    source_path = adapter.validate_export(source_export, contract)
    original_read_dataframe = adapter.pyogrio.read_dataframe

    def malformed_ids(*args: object, **kwargs: object) -> object:
        frame = original_read_dataframe(*args, **kwargs)
        frame = frame.copy()
        frame.loc[frame.index[0], "osm_id"] = replacement
        return frame

    monkeypatch.setattr(adapter.pyogrio, "read_dataframe", malformed_ids)

    with pytest.raises(ValueError, match=message):
        adapter.read_partitions(source_path, source_export, contract, (_partition("ST76"),))


@pytest.mark.parametrize(
    ("transformed", "message"),
    (
        (LineString(), "unsupported"),
        (LineString([(1, 1), (1, 1)]), "unsupported"),
        (Point(1, 1), "unsupported"),
        (LineString([(float("nan"), 1), (1, 1)]), "non-finite"),
        (LineString([(701_000, 100_000), (702_000, 100_000)]), "outside the supported BNG"),
    ),
)
def test_read_partitions_rejects_unsafe_transformed_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transformed: object,
    message: str,
) -> None:
    source_export = _source_export(_raw_osm(tmp_path))
    contract = _contract()
    source_path = adapter.validate_export(source_export, contract)
    monkeypatch.setattr(adapter, "transform_geometry", lambda *args: transformed)

    with pytest.raises(ValueError, match=message):
        adapter.read_partitions(source_path, source_export, contract, (_partition("ST76"),))


def test_read_partitions_includes_a_way_touching_a_shared_cell_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_export = _source_export(_raw_osm(tmp_path))
    contract = _contract()
    source_path = adapter.validate_export(source_export, contract)
    transformed_geometries = iter(
        (
            LineString([(375_000, 165_000), (380_000, 165_000)]),
            LineString([(375_000, 155_000), (376_000, 155_000)]),
        )
    )
    monkeypatch.setattr(
        adapter,
        "transform_geometry",
        lambda *args: next(transformed_geometries),
    )

    partitions = adapter.read_partitions(
        source_path,
        source_export,
        contract,
        (_partition("ST86"), _partition("ST76")),
    )

    by_cell = {partition.partition_key.cell: partition.features for partition in partitions}
    assert [feature.logical_key for feature in by_cell["ST76"]] == ["osm-way:2001"]
    assert [feature.logical_key for feature in by_cell["ST86"]] == ["osm-way:2001"]
    assert by_cell["ST76"][0].geometry.equals(by_cell["ST86"][0].geometry)


def test_other_tags_parser_is_closed_and_never_evaluates_tag_text() -> None:
    parsed = adapter._parse_other_tags('"ref"=>"__import__(\\"os\\")","maxspeed"=>"20"')

    assert parsed == {"ref": '__import__("os")', "maxspeed": "20"}
    assert adapter._parse_other_tags(r'"ref"=>"a\\b\"c"') == {"ref": 'a\\b"c'}
    with pytest.raises(ValueError, match="valid OGR hstore"):
        adapter._parse_other_tags('"ref"=>__import__("os")')
    with pytest.raises(ValueError, match="unsupported escape"):
        adapter._parse_other_tags(r'"ref"=>"bad\n"')
