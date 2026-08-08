"""Explicitly acquire and retain one bounded governed raw OSM network export."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from satn import osm_network_adapter
from satn.evidence_contracts import IngestionContract
from satn.remote_endpoints import (
    open_configured_https,
    validate_configured_https_endpoint,
)

MAX_RAW_BYTES = 2 * 1024 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024


def ingestion_contract() -> IngestionContract:
    """Expose the exact contract paired with the acquired Source Export."""

    return osm_network_adapter.ingestion_contract()


def acquire_osm_export(
    bounds: Sequence[object],
    cache_dir: Path,
    *,
    endpoint: str,
    timeout_seconds: int,
    retrieved_at: str | None = None,
    max_raw_bytes: int = MAX_RAW_BYTES,
) -> Path:
    """Acquire one bounded Overpass response and publish its immutable receipt."""

    query, area = osm_network_adapter.governed_overpass_query(bounds, timeout_seconds)
    canonical_endpoint = _canonical_endpoint(endpoint)
    if (
        not isinstance(max_raw_bytes, int)
        or isinstance(max_raw_bytes, bool)
        or not 1 <= max_raw_bytes <= MAX_RAW_BYTES
    ):
        raise ValueError(f"OSM max_raw_bytes must be between 1 and {MAX_RAW_BYTES}")
    if retrieved_at is not None:
        _validate_retrieved_at(retrieved_at)
    if cache_dir.exists() and (not cache_dir.is_dir() or cache_dir.is_symlink()):
        raise ValueError("OSM acquisition cache must be a real directory")

    request = urllib.request.Request(
        canonical_endpoint,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={
            "Accept": "application/osm3s+xml, application/xml, text/xml",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "banes-satn/0.1 governed-osm-acquisition",
        },
        method="POST",
    )
    incoming_dir = cache_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".osm-network.", suffix=".part", dir=incoming_dir
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            with open_configured_https(request, timeout=timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    raise ValueError(
                        f"Overpass returned HTTP status {getattr(response, 'status', 'unknown')}"
                    )
                response_headers = _response_headers(response.headers)
                content_type = response_headers["content_type"]
                if content_type is None or not _is_osm_xml_content_type(content_type):
                    raise ValueError("Overpass response Content-Type is not OSM XML")
                byte_count = 0
                digest = hashlib.sha256()
                while chunk := response.read(READ_CHUNK_BYTES):
                    byte_count += len(chunk)
                    if byte_count > max_raw_bytes:
                        raise ValueError(
                            f"Overpass OSM XML exceeds the {max_raw_bytes}-byte acquisition limit"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            if not byte_count:
                raise ValueError("Overpass returned an empty OSM XML response")
            output.flush()
            os.fsync(output.fileno())

        raw_sha256 = digest.hexdigest()
        raw_object_path = f"objects/sha256/{raw_sha256}.osm"
        effective_retrieved_at = retrieved_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        receipt = osm_network_adapter.acquisition_receipt_payload(
            temporary,
            raw_object_path=raw_object_path,
            endpoint=canonical_endpoint,
            query=query,
            area=area,
            timeout_seconds=timeout_seconds,
            retrieved_at=effective_retrieved_at,
            response_headers=response_headers,
        )
        object_path = cache_dir / raw_object_path
        _publish_object(temporary, object_path, raw_sha256, byte_count)
        receipt_bytes = osm_network_adapter.acquisition_receipt_bytes(receipt)
        receipt_path = cache_dir / "receipts" / f"{hashlib.sha256(receipt_bytes).hexdigest()}.json"
        _publish_bytes(receipt_path, receipt_bytes, "OSM acquisition receipt")
        return receipt_path
    finally:
        if temporary.exists():
            temporary.unlink()


def _canonical_endpoint(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("OSM acquisition endpoint must be an HTTPS URL")
    validate_configured_https_endpoint(value, field_name="OSM acquisition endpoint")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "OSM acquisition endpoint must be an HTTPS URL without credentials, query or fragment"
        )
    return value


def _validate_retrieved_at(value: str) -> None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError) as error:
        raise ValueError("OSM retrieved_at must be a UTC RFC3339 timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("OSM retrieved_at must be a UTC RFC3339 timestamp")


def _response_headers(headers: Mapping[str, str]) -> dict[str, str | None]:
    return {
        "content_type": headers.get("Content-Type"),
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "date": headers.get("Date"),
    }


def _is_osm_xml_content_type(value: str) -> bool:
    media_type = value.partition(";")[0].strip().lower()
    return media_type in {
        "application/osm3s+xml",
        "application/xml",
        "text/xml",
    }


def _publish_object(temporary: Path, object_path: Path, raw_sha256: str, byte_count: int) -> None:
    object_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(temporary, object_path)
    except FileExistsError as error:
        if (
            object_path.is_symlink()
            or not object_path.is_file()
            or object_path.stat().st_size != byte_count
            or _sha256_file(object_path) != raw_sha256
        ):
            raise ValueError(
                "OSM raw object conflicts with its content-addressed digest"
            ) from error


def _publish_bytes(path: Path, content: bytes, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise ValueError(f"{label} conflicts with existing retained bytes") from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        nargs=4,
        metavar=("SOUTH", "WEST", "NORTH", "EAST"),
        required=True,
        help="Bounded WGS84 acquisition area.",
    )
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-raw-bytes", type=int, default=MAX_RAW_BYTES)
    args = parser.parse_args()
    receipt_path = acquire_osm_export(
        args.bbox,
        args.cache_dir,
        endpoint=args.endpoint,
        timeout_seconds=args.timeout_seconds,
        max_raw_bytes=args.max_raw_bytes,
    )
    source_export = osm_network_adapter.load_acquisition_receipt(receipt_path)
    print(f"receipt: {receipt_path}")
    print(f"raw object: {source_export.provenance['retained_path']}")
    print(f"source export: {source_export.fingerprint}")


if __name__ == "__main__":
    main()
