"""Strict bounded-memory parsing for large GeoJSON FeatureCollections."""

from __future__ import annotations

import codecs
import json
import re
import struct
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from pyproj import CRS
from shapely.geometry import shape

READ_CHUNK_BYTES = 1024 * 1024
MAX_FEATURE_BYTES = 64 * 1024 * 1024
MAX_KEY_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 128
_WHITESPACE = b" \t\r\n"
_HEX = frozenset(b"0123456789abcdefABCDEF")
_FEATURES_LINE = re.compile(rb'^\s*"features"\s*:\s*\[\s*$')


class _LegacyNaN:
    pass


def iter_geojson_features(
    path: Path,
    *,
    max_feature_bytes: int = MAX_FEATURE_BYTES,
    legacy_nan_property_key: str | None = None,
    expected_legacy_nan_count: int | None = None,
    normalization_report: dict[str, int] | None = None,
) -> Iterator[tuple[int, dict[str, object], object, CRS]]:
    """Yield features after strictly validating the complete JSON envelope.

    Feature payloads are spooled to a temporary file while the outer object is
    checked. This permits ``crs`` to appear after ``features`` without retaining
    the collection in memory and makes parsing independent of line layout.
    """

    with tempfile.TemporaryFile(mode="w+b") as spool:
        line_result = _try_spool_line_oriented_collection(
            path,
            spool,
            max_feature_bytes=max_feature_bytes,
        )
        if line_result is None:
            spool.seek(0)
            spool.truncate()
            with path.open("rb") as source:
                parser = _Parser(source)
                source_crs, feature_count = parser.parse_feature_collection(
                    spool,
                    max_feature_bytes=max_feature_bytes,
                    allow_feature_nan=legacy_nan_property_key is not None,
                )
        else:
            source_crs, feature_count = line_result
        spool.seek(0)
        normalized_count = 0
        for position in range(feature_count):
            length_bytes = spool.read(8)
            if len(length_bytes) != 8:  # pragma: no cover - private spool invariant.
                raise ValueError(f"invalid GeoJSON spool for {path.name}")
            length = struct.unpack(">Q", length_bytes)[0]
            payload = spool.read(length)
            if len(payload) != length:  # pragma: no cover - private spool invariant.
                raise ValueError(f"invalid GeoJSON spool for {path.name}")
            try:
                feature = json.loads(
                    payload,
                    object_pairs_hook=_reject_duplicate_pairs,
                    parse_constant=(
                        _parse_legacy_constant
                        if legacy_nan_property_key is not None
                        else _reject_nonstandard_constant
                    ),
                )
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(f"invalid GeoJSON feature in {path.name}") from error
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError(f"invalid GeoJSON feature in {path.name}")
            properties = feature.get("properties")
            geometry_value = feature.get("geometry")
            if not isinstance(properties, dict) or not isinstance(geometry_value, dict):
                raise ValueError(f"malformed GeoJSON feature in {path.name}")
            if legacy_nan_property_key is not None:
                for key, value in feature.items():
                    if key == "properties":
                        continue
                    if _contains_legacy_nan(value):
                        raise ValueError(
                            f"legacy NaN is forbidden outside GeoJSON properties in {path.name}"
                        )
                properties, feature_count_normalized = _normalize_legacy_nan_property(
                    properties,
                    allowed_key=legacy_nan_property_key,
                )
                normalized_count += feature_count_normalized
            try:
                geometry = shape(geometry_value)
            except Exception as error:
                raise ValueError(f"invalid GeoJSON geometry in {path.name}") from error
            yield position, properties, geometry, source_crs
        if (
            expected_legacy_nan_count is not None
            and normalized_count != expected_legacy_nan_count
        ):
            raise ValueError(
                f"legacy GeoJSON NaN normalization count differs: "
                f"expected {expected_legacy_nan_count}, found {normalized_count}"
            )
        if normalization_report is not None:
            normalization_report[legacy_nan_property_key or "disabled"] = normalized_count


def _try_spool_line_oriented_collection(
    path: Path,
    spool: BinaryIO,
    *,
    max_feature_bytes: int,
) -> tuple[CRS, int] | None:
    """Fast strict path for canonical one-feature-per-line GeoJSON."""

    prefix = bytearray()
    suffix = bytearray()
    in_features = False
    closed_features = False
    feature_count = 0
    previous_had_comma: bool | None = None
    with path.open("rb") as source:
        while True:
            line = source.readline(max_feature_bytes + 1)
            if not line:
                break
            if len(line) > max_feature_bytes and not line.endswith(b"\n"):
                return None
            stripped = line.strip()
            if not in_features:
                prefix.extend(line)
                if _FEATURES_LINE.fullmatch(stripped):
                    in_features = True
                elif len(prefix) > MAX_FEATURE_BYTES:
                    return None
                continue
            if not closed_features and stripped.startswith(b"]"):
                if previous_had_comma:
                    raise ValueError("trailing comma in GeoJSON feature array")
                closed_features = True
                closing_index = line.index(b"]")
                suffix.extend(line[closing_index + 1 :])
                continue
            if closed_features:
                suffix.extend(line)
                continue
            if not stripped:
                continue
            if not stripped.startswith(b"{"):
                return None
            had_comma = stripped.endswith(b",")
            payload = stripped[:-1].rstrip() if had_comma else stripped
            if not payload.endswith(b"}"):
                return None
            if previous_had_comma is False:
                raise ValueError("missing comma in GeoJSON feature array")
            spool.write(struct.pack(">Q", len(payload)))
            spool.write(payload)
            feature_count += 1
            previous_had_comma = had_comma
    if not in_features or not closed_features:
        return None
    wrapper = bytes(prefix) + b"]" + bytes(suffix)
    try:
        document = json.loads(
            wrapper,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonstandard_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("invalid GeoJSON FeatureCollection envelope") from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON document is not a FeatureCollection")
    if document.get("features") != []:
        raise ValueError("invalid GeoJSON feature-array envelope")
    if feature_count == 0:
        raise ValueError("GeoJSON FeatureCollection contains no features")
    return _Parser._parse_crs(document.get("crs")), feature_count


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON member {key!r}")
        value[key] = item
    return value


def _reject_nonstandard_constant(value: str) -> object:
    raise ValueError(f"nonstandard JSON constant {value!r}")


def _parse_legacy_constant(value: str) -> object:
    if value == "NaN":
        return _LegacyNaN()
    return _reject_nonstandard_constant(value)


def _contains_legacy_nan(value: object) -> bool:
    if isinstance(value, _LegacyNaN):
        return True
    if isinstance(value, dict):
        return any(_contains_legacy_nan(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_legacy_nan(item) for item in value)
    return False


def _normalize_legacy_nan_property(
    value: object,
    *,
    allowed_key: str,
    parent_key: str | None = None,
) -> tuple[object, int]:
    if isinstance(value, _LegacyNaN):
        if parent_key != allowed_key:
            raise ValueError(
                f"legacy NaN is permitted only as the value of {allowed_key!r}"
            )
        return None, 1
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        count = 0
        for key, item in value.items():
            result, item_count = _normalize_legacy_nan_property(
                item,
                allowed_key=allowed_key,
                parent_key=key,
            )
            normalized[key] = result
            count += item_count
        return normalized, count
    if isinstance(value, list):
        normalized_list: list[object] = []
        count = 0
        for item in value:
            result, item_count = _normalize_legacy_nan_property(
                item,
                allowed_key=allowed_key,
                parent_key=None,
            )
            normalized_list.append(result)
            count += item_count
        return normalized_list, count
    return value, 0


class _Reader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.buffer = b""
        self.position = 0
        self.eof = False
        self.capture: bytearray | None = None
        self.capture_limit = 0

    def peek(self) -> int | None:
        if self.position == len(self.buffer):
            self.buffer = self.stream.read(READ_CHUNK_BYTES)
            self.position = 0
            if not self.buffer:
                self.eof = True
                return None
        return self.buffer[self.position]

    def take(self) -> int:
        value = self.peek()
        if value is None:
            raise ValueError("unexpected end of JSON")
        self.position += 1
        if self.capture is not None:
            if len(self.capture) >= self.capture_limit:
                raise ValueError("GeoJSON feature exceeds bounded parser limit")
            self.capture.append(value)
        return value

    def skip_whitespace(self) -> None:
        while (value := self.peek()) is not None and value in _WHITESPACE:
            self.take()

    def start_capture(self, limit: int) -> None:
        if self.capture is not None:  # pragma: no cover - parser invariant.
            raise RuntimeError("nested JSON capture")
        self.capture = bytearray()
        self.capture_limit = limit

    def finish_capture(self) -> bytes:
        if self.capture is None:  # pragma: no cover - parser invariant.
            raise RuntimeError("JSON capture is not active")
        payload = bytes(self.capture)
        self.capture = None
        self.capture_limit = 0
        return payload


class _Parser:
    def __init__(self, stream: BinaryIO) -> None:
        self.reader = _Reader(stream)

    def parse_feature_collection(
        self,
        spool: BinaryIO,
        *,
        max_feature_bytes: int,
        allow_feature_nan: bool,
    ) -> tuple[CRS, int]:
        self.reader.skip_whitespace()
        self._expect(ord("{"))
        keys: set[str] = set()
        feature_count: int | None = None
        collection_type: object = None
        crs_value: object = None
        self.reader.skip_whitespace()
        if self.reader.peek() == ord("}"):
            self.reader.take()
        else:
            while True:
                key = self._parse_string(decode=True)
                if key in keys:
                    raise ValueError(f"duplicate GeoJSON member {key!r}")
                keys.add(key)
                self.reader.skip_whitespace()
                self._expect(ord(":"))
                self.reader.skip_whitespace()
                if key == "features":
                    feature_count = self._parse_feature_array(
                        spool,
                        max_feature_bytes=max_feature_bytes,
                        allow_feature_nan=allow_feature_nan,
                    )
                elif key in {"type", "crs"}:
                    self.reader.start_capture(MAX_KEY_BYTES)
                    self._parse_value(depth=1)
                    payload = self.reader.finish_capture()
                    try:
                        value = json.loads(payload)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        raise ValueError(f"invalid GeoJSON {key}") from error
                    if key == "type":
                        collection_type = value
                    else:
                        crs_value = value
                else:
                    self._parse_value(depth=1)
                self.reader.skip_whitespace()
                delimiter = self.reader.take()
                if delimiter == ord("}"):
                    break
                if delimiter != ord(","):
                    raise ValueError("invalid GeoJSON object delimiter")
                self.reader.skip_whitespace()
        self.reader.skip_whitespace()
        if self.reader.peek() is not None:
            raise ValueError("trailing content after GeoJSON document")
        if collection_type != "FeatureCollection" or feature_count is None:
            raise ValueError("GeoJSON document is not a FeatureCollection")
        if feature_count == 0:
            raise ValueError("GeoJSON FeatureCollection contains no features")
        return self._parse_crs(crs_value), feature_count

    def _parse_feature_array(
        self,
        spool: BinaryIO,
        *,
        max_feature_bytes: int,
        allow_feature_nan: bool,
    ) -> int:
        self._expect(ord("["))
        self.reader.skip_whitespace()
        if self.reader.peek() == ord("]"):
            self.reader.take()
            return 0
        count = 0
        while True:
            self.reader.start_capture(max_feature_bytes)
            self._parse_value(depth=1, allow_nan=allow_feature_nan)
            payload = self.reader.finish_capture()
            spool.write(struct.pack(">Q", len(payload)))
            spool.write(payload)
            count += 1
            self.reader.skip_whitespace()
            delimiter = self.reader.take()
            if delimiter == ord("]"):
                return count
            if delimiter != ord(","):
                raise ValueError("invalid GeoJSON feature-array delimiter")
            self.reader.skip_whitespace()

    def _parse_value(self, *, depth: int, allow_nan: bool = False) -> None:
        if depth > MAX_JSON_DEPTH:
            raise ValueError("GeoJSON exceeds maximum JSON nesting depth")
        value = self.reader.peek()
        if value == ord("{"):
            self._parse_object(depth=depth, allow_nan=allow_nan)
        elif value == ord("["):
            self._parse_array(depth=depth, allow_nan=allow_nan)
        elif value == ord('"'):
            self._parse_string(decode=False)
        elif value == ord("t"):
            self._parse_literal(b"true")
        elif value == ord("f"):
            self._parse_literal(b"false")
        elif value == ord("n"):
            self._parse_literal(b"null")
        elif value == ord("N") and allow_nan:
            self._parse_literal(b"NaN")
        elif value == ord("-") or (value is not None and ord("0") <= value <= ord("9")):
            self._parse_number()
        else:
            raise ValueError("invalid JSON value")

    def _parse_object(self, *, depth: int, allow_nan: bool = False) -> None:
        self._expect(ord("{"))
        self.reader.skip_whitespace()
        if self.reader.peek() == ord("}"):
            self.reader.take()
            return
        keys: set[str] = set()
        while True:
            key = self._parse_string(decode=True)
            if key in keys:
                raise ValueError(f"duplicate JSON member {key!r}")
            keys.add(key)
            self.reader.skip_whitespace()
            self._expect(ord(":"))
            self.reader.skip_whitespace()
            self._parse_value(depth=depth + 1, allow_nan=allow_nan)
            self.reader.skip_whitespace()
            delimiter = self.reader.take()
            if delimiter == ord("}"):
                return
            if delimiter != ord(","):
                raise ValueError("invalid JSON object delimiter")
            self.reader.skip_whitespace()

    def _parse_array(self, *, depth: int, allow_nan: bool = False) -> None:
        self._expect(ord("["))
        self.reader.skip_whitespace()
        if self.reader.peek() == ord("]"):
            self.reader.take()
            return
        while True:
            self._parse_value(depth=depth + 1, allow_nan=allow_nan)
            self.reader.skip_whitespace()
            delimiter = self.reader.take()
            if delimiter == ord("]"):
                return
            if delimiter != ord(","):
                raise ValueError("invalid JSON array delimiter")
            self.reader.skip_whitespace()

    def _parse_string(self, *, decode: bool) -> str:
        raw = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")("strict")

        def consume() -> int:
            value = self.reader.take()
            decoder.decode(bytes((value,)), final=False)
            if decode:
                if len(raw) >= MAX_KEY_BYTES:
                    raise ValueError("JSON object key exceeds bounded parser limit")
                raw.append(value)
            return value

        if consume() != ord('"'):
            raise ValueError("expected JSON string")
        while True:
            value = consume()
            if value == ord('"'):
                break
            if value < 0x20:
                raise ValueError("unescaped control character in JSON string")
            if value != ord("\\"):
                continue
            escaped = consume()
            if escaped in b'"\\/bfnrt':
                continue
            if escaped != ord("u"):
                raise ValueError("invalid JSON string escape")
            for _ in range(4):
                if consume() not in _HEX:
                    raise ValueError("invalid JSON unicode escape")
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise ValueError("invalid UTF-8 in JSON string") from error
        if not decode:
            return ""
        try:
            value = json.loads(bytes(raw))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("invalid JSON string") from error
        if not isinstance(value, str):  # pragma: no cover - json string invariant.
            raise ValueError("invalid JSON string")
        return value

    def _parse_number(self) -> None:
        if self.reader.peek() == ord("-"):
            self.reader.take()
        value = self.reader.peek()
        if value == ord("0"):
            self.reader.take()
            following = self.reader.peek()
            if following is not None and ord("0") <= following <= ord("9"):
                raise ValueError("leading zero in JSON number")
        elif value is not None and ord("1") <= value <= ord("9"):
            self.reader.take()
            while (value := self.reader.peek()) is not None and ord("0") <= value <= ord("9"):
                self.reader.take()
        else:
            raise ValueError("invalid JSON number")
        if self.reader.peek() == ord("."):
            self.reader.take()
            self._require_digits()
        if self.reader.peek() in {ord("e"), ord("E")}:
            self.reader.take()
            if self.reader.peek() in {ord("+"), ord("-")}:
                self.reader.take()
            self._require_digits()

    def _require_digits(self) -> None:
        value = self.reader.peek()
        if value is None or not ord("0") <= value <= ord("9"):
            raise ValueError("invalid JSON number")
        while (value := self.reader.peek()) is not None and ord("0") <= value <= ord("9"):
            self.reader.take()

    def _parse_literal(self, expected: bytes) -> None:
        for value in expected:
            self._expect(value)

    def _expect(self, expected: int) -> None:
        if self.reader.take() != expected:
            raise ValueError(f"expected JSON byte {chr(expected)!r}")

    @staticmethod
    def _parse_crs(value: object) -> CRS:
        if value is None:
            return CRS.from_epsg(4326)
        if not isinstance(value, dict):
            raise ValueError("GeoJSON CRS metadata is malformed")
        properties = value.get("properties")
        name = properties.get("name") if isinstance(properties, dict) else None
        if not isinstance(name, str):
            raise ValueError("GeoJSON CRS metadata is malformed")
        try:
            return CRS.from_user_input(name)
        except Exception as error:
            raise ValueError("GeoJSON CRS metadata is invalid") from error
