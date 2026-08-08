from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from satn import osm_network_adapter as adapter
from satn.evidence_contracts import EvidencePartitionKey

PROJECT = Path(__file__).parents[1]
RAW_OSM_FIXTURE = PROJECT / "tests" / "fixtures" / "osm-network.xml"
SCRIPT = PROJECT / "scripts" / "acquire_osm_network.py"
SPEC = importlib.util.spec_from_file_location("acquire_osm_network", SCRIPT)
assert SPEC and SPEC.loader
acquisition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquisition)


class _Response:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._position = 0
        self.headers = {
            "Content-Type": "application/osm3s+xml; charset=utf-8",
            "ETag": '"fixture-v1"',
            "Last-Modified": "Mon, 27 Jul 2026 10:11:12 GMT",
            "Date": "Wed, 29 Jul 2026 12:00:00 GMT",
        }

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._position
        chunk = self._payload[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


def test_explicit_acquisition_retains_exact_bytes_and_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_bytes = RAW_OSM_FIXTURE.read_bytes()
    requests: list[object] = []

    def online(request: object, *, timeout: int) -> _Response:
        requests.append((request, timeout))
        return _Response(raw_bytes)

    monkeypatch.setattr(acquisition, "open_configured_https", online)
    receipt_path = acquisition.acquire_osm_export(
        (51.28, -2.39, 51.40, -2.19),
        tmp_path / "osm-cache",
        endpoint="https://overpass.example.test/api/interpreter",
        timeout_seconds=90,
        retrieved_at="2026-07-29T12:00:00Z",
    )

    assert len(requests) == 1
    request, timeout = requests[0]
    assert timeout == 90
    assert request.full_url == "https://overpass.example.test/api/interpreter"
    assert request.get_method() == "POST"
    assert b"way%5B%22highway%22%5D" in request.data

    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    object_path = tmp_path / "osm-cache" / "objects" / "sha256" / f"{raw_sha256}.osm"
    assert object_path.read_bytes() == raw_bytes
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["contract"] == "satn-osm-network-acquisition-receipt/v1"
    assert receipt["raw_object"] == {
        "path": f"objects/sha256/{raw_sha256}.osm",
        "sha256": raw_sha256,
        "byte_count": len(raw_bytes),
    }
    assert receipt["acquisition"]["area"] == {
        "type": "bbox",
        "crs": "EPSG:4326",
        "south": "51.28",
        "west": "-2.39",
        "north": "51.4",
        "east": "-2.19",
    }
    assert receipt["acquisition"]["retrieved_at"] == "2026-07-29T12:00:00Z"
    assert receipt["publisher"] == {
        "generator": "satn-test",
        "osm_base": "2026-07-27T10:11:12Z",
    }
    assert receipt["licence"] == {
        "spdx": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "url": "https://www.openstreetmap.org/copyright",
    }
    assert receipt["source_export"]["raw_bytes_sha256"] == raw_sha256
    assert receipt_path.name == f"{hashlib.sha256(receipt_path.read_bytes()).hexdigest()}.json"

    monkeypatch.setattr(
        acquisition,
        "open_configured_https",
        lambda *_args, **_kwargs: pytest.fail("offline replay must not access the network"),
    )
    source_export = adapter.load_acquisition_receipt(receipt_path)
    assert source_export.raw_bytes_sha256 == raw_sha256
    assert source_export.publisher_release == "2026-07-27T10:11:12Z"
    assert source_export.effective_date == "2026-07-27"
    assert source_export.provenance["retained_path"] == str(object_path.resolve())
    assert source_export.provenance["acquisition_receipt"] == str(receipt_path.resolve())
    contract = acquisition.ingestion_contract()
    adapter.validate_export(
        source_export,
        contract,
    )
    partitions = adapter.read_partitions(
        object_path,
        source_export,
        contract,
        (
            EvidencePartitionKey(adapter.SOURCE_LAYER, adapter.PARTITION_SCHEME, "ST75"),
            EvidencePartitionKey(adapter.SOURCE_LAYER, adapter.PARTITION_SCHEME, "ST76"),
            EvidencePartitionKey(adapter.SOURCE_LAYER, adapter.PARTITION_SCHEME, "ST86"),
        ),
    )
    assert {
        partition.partition_key.cell: [feature.logical_key for feature in partition.features]
        for partition in partitions
    } == {
        "ST75": ["osm-way:2002"],
        "ST76": ["osm-way:2001"],
        "ST86": ["osm-way:2001"],
    }


@pytest.mark.parametrize(
    "bounds",
    (
        (51.4, -2.39, 51.28, -2.19),
        (51.28, -2.19, 51.4, -2.39),
        (-91, -2.39, 51.4, -2.19),
    ),
)
def test_acquisition_rejects_unbounded_or_invalid_area_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bounds: tuple[float, float, float, float],
) -> None:
    monkeypatch.setattr(
        acquisition,
        "open_configured_https",
        lambda *_args, **_kwargs: pytest.fail("invalid area must fail before network access"),
    )

    with pytest.raises(ValueError, match="bbox"):
        acquisition.acquire_osm_export(
            bounds,
            tmp_path / "cache",
            endpoint="https://overpass.example.test/api/interpreter",
            timeout_seconds=90,
        )
    assert not (tmp_path / "cache").exists()


def test_acquisition_rejects_untrusted_endpoint_and_invalid_raw_response_without_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        acquisition,
        "open_configured_https",
        lambda *_args, **_kwargs: pytest.fail("non-HTTPS endpoint must fail before network access"),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        acquisition.acquire_osm_export(
            (51.28, -2.39, 51.40, -2.19),
            tmp_path / "cache",
            endpoint="http://overpass.example.test/api/interpreter",
            timeout_seconds=90,
        )

    monkeypatch.setattr(
        acquisition,
        "open_configured_https",
        lambda *_args, **_kwargs: _Response(b"<html>not an OSM receipt</html>"),
    )
    with pytest.raises(ValueError, match="OSM XML"):
        acquisition.acquire_osm_export(
            (51.28, -2.39, 51.40, -2.19),
            tmp_path / "cache",
            endpoint="https://overpass.example.test/api/interpreter",
            timeout_seconds=90,
        )
    assert not list((tmp_path / "cache").rglob("*.osm"))
    assert not list((tmp_path / "cache").rglob("*.json"))


def test_disconnected_acquisitions_are_retained_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = RAW_OSM_FIXTURE.read_bytes()
    second = first.replace(b"satn-test", b"satn-test-oxfordshire")
    responses = iter((_Response(first), _Response(second)))
    monkeypatch.setattr(
        acquisition,
        "open_configured_https",
        lambda *_args, **_kwargs: next(responses),
    )
    cache = tmp_path / "cache"

    first_receipt = acquisition.acquire_osm_export(
        (51.28, -2.39, 51.40, -2.19),
        cache,
        endpoint="https://overpass.example.test/api/interpreter",
        timeout_seconds=90,
        retrieved_at="2026-07-29T12:00:00Z",
    )
    second_receipt = acquisition.acquire_osm_export(
        (51.70, -1.50, 51.85, -1.10),
        cache,
        endpoint="https://overpass.example.test/api/interpreter",
        timeout_seconds=90,
        retrieved_at="2026-07-29T12:01:00Z",
    )

    assert first_receipt != second_receipt
    assert len(list((cache / "receipts").glob("*.json"))) == 2
    assert len(list((cache / "objects" / "sha256").glob("*.osm"))) == 2
    first_export = adapter.load_acquisition_receipt(first_receipt)
    second_export = adapter.load_acquisition_receipt(second_receipt)
    assert (
        first_export.provenance["acquisition_area"] != second_export.provenance["acquisition_area"]
    )
    assert first_export.fingerprint != second_export.fingerprint


def test_offline_receipt_load_fails_closed_on_missing_tampered_or_schema_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_bytes = RAW_OSM_FIXTURE.read_bytes()
    monkeypatch.setattr(
        acquisition,
        "open_configured_https",
        lambda *_args, **_kwargs: _Response(raw_bytes),
    )
    receipt_path = acquisition.acquire_osm_export(
        (51.28, -2.39, 51.40, -2.19),
        tmp_path / "cache",
        endpoint="https://overpass.example.test/api/interpreter",
        timeout_seconds=90,
        retrieved_at="2026-07-29T12:00:00Z",
    )
    source_export = adapter.load_acquisition_receipt(receipt_path)
    object_path = Path(source_export.provenance["retained_path"])
    original = object_path.read_bytes()

    object_path.write_bytes(original + b"\n<!-- tampered -->")
    with pytest.raises(ValueError, match="checksum"):
        adapter.load_acquisition_receipt(receipt_path)
    object_path.write_bytes(original)

    changed = json.loads(receipt_path.read_bytes())
    changed["contract"] = "satn-osm-network-acquisition-receipt/v2"
    receipt_path.write_text(json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(ValueError, match="receipt"):
        adapter.load_acquisition_receipt(receipt_path)

    receipt_path.unlink()
    with pytest.raises(ValueError, match="not retained"):
        adapter.load_acquisition_receipt(receipt_path)
