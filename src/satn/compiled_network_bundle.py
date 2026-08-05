"""Canonical, non-executable wire contracts for compiled network artifacts.

The codec deliberately accepts data, not Python import paths.  Bundle callers
must supply the dataclass they expect when decoding, and unsupported fields are
rejected while encoding rather than being silently omitted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

import geopandas as gpd
import numpy as np
import pandas as pd
from pydantic import BaseModel
from pyproj import CRS
from shapely import from_wkb, to_wkb

_FRAME_CONTRACT = "satn-geodataframe-wire/v1"
_BUNDLE_CONTRACT = "satn-compiled-network-bundle/v1"
_JSON_CONTRACT = "satn-canonical-typed-json/v1"
_CRS_RULE_CONTRACT = "satn-bundle-frame-crs-rule/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NDARRAY_KINDS = frozenset("biufcSU")


class BundleCodecError(ValueError):
    """A bundle or frame violates its declared canonical wire contract."""


@dataclass(frozen=True)
class _DecodedDataclass:
    name: str
    values: dict[str, object]


@dataclass(frozen=True)
class _DecodedPydanticModel:
    name: str
    value: object


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BundleCodecError("value is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleCodecError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise BundleCodecError(f"{label} keys differ: missing={missing}, unknown={unknown}")
    return value


def _canonical_ndarray_dtype(value: object) -> np.dtype:
    """Return a safe, canonical primitive dtype for the ndarray wire codec."""

    if not isinstance(value, str):
        raise BundleCodecError("ndarray dtype must be a string")
    try:
        dtype = np.dtype(value)
    except (TypeError, ValueError) as exc:
        raise BundleCodecError("invalid ndarray dtype") from exc
    # Structured, object, subarray, datetime, and void dtypes can carry
    # interpretation or executable-adjacent metadata that this data-only wire
    # contract does not need.  Keep the allow-list deliberately narrow.
    if (
        dtype.str != value
        or dtype.fields is not None
        or dtype.hasobject
        or dtype.subdtype is not None
        or dtype.kind not in _NDARRAY_KINDS
        or dtype.itemsize <= 0
    ):
        raise BundleCodecError("unsupported ndarray dtype")
    return dtype


def _wire_value(value: object) -> dict[str, object]:
    if value is None or value is pd.NA or (value is pd.NaT):
        return {"type": "null"}
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            encoded = "nan"
        elif math.isinf(value):
            encoded = "+inf" if value > 0 else "-inf"
        else:
            encoded = value.hex()
        return {"type": "float", "value": encoded}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, np.ndarray):
        dtype = _canonical_ndarray_dtype(value.dtype.str)
        raw = value.tobytes(order="C")
        return {
            "type": "ndarray",
            "dtype": dtype.str,
            "shape": list(value.shape),
            "value": base64.b64encode(raw).decode("ascii"),
        }
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, Enum):
        return {"type": "enum", "value": _wire_value(value.value)}
    if isinstance(value, BaseModel):
        model_name = type(value).__name__
        body = {
            "contract": f"satn-pydantic-model/{model_name}/v1",
            "model": type(value).__name__,
            "value": _wire_value(value.model_dump(mode="json")),
        }
        return {"type": "pydantic-model", **body, "content_sha256": _sha256(body)}
    if is_dataclass(value) and not isinstance(value, type):
        dataclass_name = type(value).__name__
        body = {
            "contract": f"satn-dataclass/{dataclass_name}/v1",
            "dataclass": dataclass_name,
            "fields": [
                [item.name, _wire_value(getattr(value, item.name))] for item in fields(value)
            ],
        }
        return {"type": "dataclass", **body, "content_sha256": _sha256(body)}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_wire_value(item) for item in value]}
    if isinstance(value, list):
        return {"type": "list", "items": [_wire_value(item) for item in value]}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise BundleCodecError("canonical mappings require string keys")
        return {
            "type": "object",
            "items": [[key, _wire_value(value[key])] for key in sorted(value)],
        }
    raise BundleCodecError(f"unsupported value type {type(value).__name__}")


def _unwire_value(value: object) -> object:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise BundleCodecError("typed JSON value lacks a type tag")
    kind = value["type"]
    if kind == "null":
        _require_keys(value, {"type"}, "null value")
        return None
    if kind in {"bool", "string"}:
        item = _require_keys(value, {"type", "value"}, f"{kind} value")["value"]
        if not isinstance(item, bool if kind == "bool" else str):
            raise BundleCodecError(f"invalid {kind} value")
        return item
    if kind == "int":
        item = _require_keys(value, {"type", "value"}, "int value")["value"]
        if not isinstance(item, str) or re.fullmatch(r"-?(0|[1-9][0-9]*)", item) is None:
            raise BundleCodecError("invalid canonical integer")
        return int(item)
    if kind == "float":
        item = _require_keys(value, {"type", "value"}, "float value")["value"]
        if not isinstance(item, str):
            raise BundleCodecError("invalid canonical float")
        if item == "nan":
            return float("nan")
        if item == "+inf":
            return float("inf")
        if item == "-inf":
            return float("-inf")
        try:
            return float.fromhex(item)
        except ValueError as exc:
            raise BundleCodecError("invalid canonical float") from exc
    if kind == "bytes":
        item = _require_keys(value, {"type", "value"}, "bytes value")["value"]
        try:
            return base64.b64decode(item, validate=True)
        except (TypeError, ValueError) as exc:
            raise BundleCodecError("invalid canonical bytes") from exc
    if kind == "ndarray":
        item = _require_keys(value, {"type", "dtype", "shape", "value"}, "ndarray value")
        dtype = _canonical_ndarray_dtype(item["dtype"])
        shape = item["shape"]
        if (
            not isinstance(shape, list)
            or any(
                isinstance(dimension, bool) or not isinstance(dimension, int)
                for dimension in shape
            )
            or any(dimension < 0 for dimension in shape)
        ):
            raise BundleCodecError("invalid ndarray shape")
        encoded = item["value"]
        if not isinstance(encoded, str):
            raise BundleCodecError("invalid ndarray bytes")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError) as exc:
            raise BundleCodecError("invalid ndarray bytes") from exc
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise BundleCodecError("ndarray bytes are not canonical base64")
        element_count = math.prod(shape, start=1)
        expected_size = element_count * dtype.itemsize
        if (
            element_count > sys.maxsize
            or expected_size > sys.maxsize
            or expected_size != len(raw)
        ):
            raise BundleCodecError("ndarray byte length does not match dtype and shape")
        try:
            return np.frombuffer(raw, dtype=dtype, count=element_count).reshape(shape).copy()
        except (TypeError, ValueError) as exc:
            raise BundleCodecError("invalid ndarray payload") from exc
    if kind in {"date", "datetime"}:
        item = _require_keys(value, {"type", "value"}, f"{kind} value")["value"]
        if not isinstance(item, str):
            raise BundleCodecError(f"invalid canonical {kind}")
        try:
            return datetime.fromisoformat(item) if kind == "datetime" else date.fromisoformat(item)
        except ValueError as exc:
            raise BundleCodecError(f"invalid canonical {kind}") from exc
    if kind == "enum":
        return _unwire_value(_require_keys(value, {"type", "value"}, "enum value")["value"])
    if kind in {"list", "tuple"}:
        items = _require_keys(value, {"type", "items"}, f"{kind} value")["items"]
        if not isinstance(items, list):
            raise BundleCodecError(f"invalid canonical {kind}")
        decoded = [_unwire_value(item) for item in items]
        return tuple(decoded) if kind == "tuple" else decoded
    if kind == "object":
        items = _require_keys(value, {"type", "items"}, "object value")["items"]
        if not isinstance(items, list):
            raise BundleCodecError("invalid canonical object")
        result: dict[str, object] = {}
        for pair in items:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or pair[0] in result
            ):
                raise BundleCodecError("canonical object keys must be unique strings")
            result[pair[0]] = _unwire_value(pair[1])
        if list(result) != sorted(result):
            raise BundleCodecError("canonical object keys are not sorted")
        return result
    if kind == "pydantic-model":
        item = _require_keys(
            value,
            {"type", "contract", "model", "value", "content_sha256"},
            "pydantic model value",
        )
        if not isinstance(item["model"], str):
            raise BundleCodecError("invalid pydantic model name")
        body = {key: item[key] for key in ("contract", "model", "value")}
        if item["contract"] != f"satn-pydantic-model/{item['model']}/v1":
            raise BundleCodecError("invalid pydantic model contract")
        if (
            not isinstance(item["content_sha256"], str)
            or _SHA256.fullmatch(item["content_sha256"]) is None
            or _sha256(body) != item["content_sha256"]
        ):
            raise BundleCodecError("pydantic model fingerprint mismatch")
        return _DecodedPydanticModel(item["model"], _unwire_value(item["value"]))
    if kind == "dataclass":
        item = _require_keys(
            value,
            {"type", "contract", "dataclass", "fields", "content_sha256"},
            "dataclass value",
        )
        name = item["dataclass"]
        if not isinstance(name, str) or item["contract"] != f"satn-dataclass/{name}/v1":
            raise BundleCodecError("invalid dataclass contract")
        body = {key: item[key] for key in ("contract", "dataclass", "fields")}
        if (
            not isinstance(item["content_sha256"], str)
            or _SHA256.fullmatch(item["content_sha256"]) is None
            or _sha256(body) != item["content_sha256"]
        ):
            raise BundleCodecError("dataclass fingerprint mismatch")
        field_items = item["fields"]
        if not isinstance(field_items, list):
            raise BundleCodecError("dataclass fields must be an array")
        decoded: dict[str, object] = {}
        for pair in field_items:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or pair[0] in decoded
            ):
                raise BundleCodecError("dataclass fields must have unique string names")
            decoded[pair[0]] = _unwire_value(pair[1])
        return _DecodedDataclass(name, decoded)
    raise BundleCodecError(f"unknown typed JSON tag {kind!r}")


def _canonical_crs(crs: object) -> dict[str, object]:
    parsed = CRS.from_user_input(crs)
    authority = parsed.to_authority()
    return {
        "authority": (
            {"name": authority[0], "code": authority[1]} if authority is not None else None
        ),
        "projjson": parsed.to_json_dict(),
    }


def _bundle_crs_rule(bundle_crs: object | None) -> dict[str, object]:
    return {
        "contract": _CRS_RULE_CONTRACT,
        "empty_missing_crs": "reject" if bundle_crs is None else "preserve-none",
        "reference_crs": None if bundle_crs is None else _canonical_crs(bundle_crs),
    }


def _validate_crs_rule(value: object) -> dict[str, object]:
    rule = _require_keys(
        value,
        {"contract", "empty_missing_crs", "reference_crs"},
        "frame CRS rule",
    )
    if rule["contract"] != _CRS_RULE_CONTRACT:
        raise BundleCodecError("unsupported frame CRS rule contract")
    if rule["empty_missing_crs"] == "reject":
        if rule["reference_crs"] is not None:
            raise BundleCodecError("rejecting frame CRS rule cannot have a reference CRS")
    elif rule["empty_missing_crs"] == "preserve-none":
        reference = _require_keys(rule["reference_crs"], {"authority", "projjson"}, "reference CRS")
        try:
            parsed = CRS.from_json_dict(reference["projjson"])
        except Exception as exc:
            raise BundleCodecError("invalid reference CRS PROJJSON") from exc
        if _canonical_crs(parsed) != reference:
            raise BundleCodecError("reference CRS is not canonical")
    else:
        raise BundleCodecError("unknown empty missing CRS rule")
    return rule


def encode_geodataframe(
    frame: gpd.GeoDataFrame,
    *,
    stable_key_columns: tuple[str, ...] | None = None,
    missing_crs_rule: dict[str, object] | None = None,
) -> dict[str, object]:
    """Encode a GeoDataFrame as deterministic typed rows and canonical WKB."""

    if not isinstance(frame, gpd.GeoDataFrame):
        raise TypeError("frame must be a GeoDataFrame")
    crs_rule = _validate_crs_rule(missing_crs_rule) if missing_crs_rule is not None else None
    if frame.crs is None and (
        not frame.empty or crs_rule is None or crs_rule["empty_missing_crs"] != "preserve-none"
    ):
        raise BundleCodecError("GeoDataFrame CRS is required unless an empty-frame rule applies")
    if not all(isinstance(name, str) for name in frame.columns):
        raise BundleCodecError("GeoDataFrame column names must be strings")
    if len(set(frame.columns)) != len(frame.columns):
        raise BundleCodecError("GeoDataFrame column names must be unique")
    geometry_columns = [
        str(name)
        for name in frame.columns
        if isinstance(frame[name].dtype, gpd.array.GeometryDtype)
    ]
    if frame.geometry.name not in geometry_columns:
        raise BundleCodecError("active geometry column is not a geometry dtype")
    columns = [
        {
            "name": str(name),
            "dtype": "geometry" if str(name) in geometry_columns else str(frame[name].dtype),
            "kind": "geometry" if str(name) in geometry_columns else "typed-json",
        }
        for name in frame.columns
    ]
    row_cells: list[list[dict[str, object]]] = []
    for row in frame.itertuples(index=False, name=None):
        encoded_row = []
        for name, item in zip(frame.columns, row, strict=True):
            if str(name) in geometry_columns:
                encoded_row.append(
                    {"type": "null"}
                    if item is None
                    else {
                        "type": "geometry-wkb",
                        "value": to_wkb(item, hex=True, byte_order=1, include_srid=False),
                    }
                )
            else:
                encoded_row.append(_wire_value(item))
        row_cells.append(encoded_row)

    names = [str(name) for name in frame.columns]
    if stable_key_columns is not None:
        if not stable_key_columns or len(set(stable_key_columns)) != len(stable_key_columns):
            raise BundleCodecError("stable key columns must be non-empty and unique")
        if any(name not in names for name in stable_key_columns):
            raise BundleCodecError("stable key column is absent from the frame")
        indexes = [names.index(name) for name in stable_key_columns]
        keys = [tuple(_canonical_bytes(row[index]) for index in indexes) for row in row_cells]
        if len(keys) != len(set(keys)):
            raise BundleCodecError("stable key columns are null, duplicate, or ambiguous")
        if any(any(row[index].get("type") == "null" for index in indexes) for row in row_cells):
            raise BundleCodecError("stable key columns are null, duplicate, or ambiguous")
        row_cells = [
            row for _, row in sorted(zip(keys, row_cells, strict=True), key=lambda item: item[0])
        ]
    else:
        row_keys = [_canonical_bytes(row) for row in row_cells]
        if len(row_keys) != len(set(row_keys)):
            raise BundleCodecError(
                "duplicate rows are ambiguous; supply stable key columns after adding a stable key"
            )
        row_cells = [
            row
            for _, row in sorted(zip(row_keys, row_cells, strict=True), key=lambda item: item[0])
        ]
    rows = [{"cells": row, "content_sha256": _sha256(row)} for row in row_cells]

    body: dict[str, object] = {
        "contract": _FRAME_CONTRACT,
        "columns": columns,
        "geometry": {
            "active_column": str(frame.geometry.name),
            "columns": geometry_columns,
            "encoding": "ogc-wkb-hex",
            "byte_order": "little-endian",
        },
        "crs": (
            _canonical_crs(frame.crs)
            if frame.crs is not None
            else {
                "rule": "bundle-empty-missing-crs/v1",
                "reference_crs": crs_rule["reference_crs"],
            }
        ),
        "stable_key_columns": list(stable_key_columns or ()),
        "rows": rows,
    }
    return {**body, "content_sha256": _sha256(body)}


def decode_geodataframe(
    payload: object, *, missing_crs_rule: dict[str, object] | None = None
) -> gpd.GeoDataFrame:
    """Strictly validate and decode a canonical GeoDataFrame wire payload."""

    wire = _require_keys(
        payload,
        {"contract", "columns", "geometry", "crs", "stable_key_columns", "rows", "content_sha256"},
        "GeoDataFrame payload",
    )
    content_sha256 = wire["content_sha256"]
    if not isinstance(content_sha256, str) or _SHA256.fullmatch(content_sha256) is None:
        raise BundleCodecError("GeoDataFrame content_sha256 must be a full lowercase SHA-256")
    body = {key: value for key, value in wire.items() if key != "content_sha256"}
    if _sha256(body) != content_sha256:
        raise BundleCodecError("GeoDataFrame content fingerprint mismatch")
    if wire["contract"] != _FRAME_CONTRACT:
        raise BundleCodecError("unsupported GeoDataFrame wire contract")
    geometry = _require_keys(
        wire["geometry"],
        {"active_column", "columns", "encoding", "byte_order"},
        "geometry metadata",
    )
    if geometry["encoding"] != "ogc-wkb-hex" or geometry["byte_order"] != "little-endian":
        raise BundleCodecError("unsupported geometry encoding")
    columns = wire["columns"]
    rows = wire["rows"]
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise BundleCodecError("columns and rows must be arrays")
    names: list[str] = []
    dtypes: list[str] = []
    kinds: list[str] = []
    for column in columns:
        item = _require_keys(column, {"name", "dtype", "kind"}, "column")
        if not all(isinstance(item[key], str) for key in ("name", "dtype", "kind")):
            raise BundleCodecError("column metadata values must be strings")
        names.append(item["name"])
        dtypes.append(item["dtype"])
        kinds.append(item["kind"])
    if len(names) != len(set(names)):
        raise BundleCodecError("column names must be unique")
    stable_key_columns = wire["stable_key_columns"]
    if (
        not isinstance(stable_key_columns, list)
        or any(not isinstance(name, str) for name in stable_key_columns)
        or len(stable_key_columns) != len(set(stable_key_columns))
        or any(name not in names for name in stable_key_columns)
    ):
        raise BundleCodecError(
            "stable_key_columns must be an array of unique declared column names"
        )
    geometry_columns = geometry["columns"]
    if (
        not isinstance(geometry_columns, list)
        or set(geometry_columns)
        != {name for name, kind in zip(names, kinds, strict=True) if kind == "geometry"}
        or geometry["active_column"] not in geometry_columns
    ):
        raise BundleCodecError("geometry column metadata is inconsistent")
    crs_wire = wire["crs"]
    if isinstance(crs_wire, dict) and crs_wire.get("rule") == "bundle-empty-missing-crs/v1":
        missing = _require_keys(crs_wire, {"rule", "reference_crs"}, "missing CRS metadata")
        crs_rule = _validate_crs_rule(missing_crs_rule) if missing_crs_rule is not None else None
        if (
            rows
            or crs_rule is None
            or crs_rule["empty_missing_crs"] != "preserve-none"
            or missing["reference_crs"] != crs_rule["reference_crs"]
        ):
            raise BundleCodecError("missing CRS metadata violates the empty-frame bundle rule")
        crs = None
    else:
        declared_crs = _require_keys(crs_wire, {"authority", "projjson"}, "CRS metadata")
        try:
            crs = CRS.from_json_dict(declared_crs["projjson"])
        except Exception as exc:
            raise BundleCodecError("invalid CRS PROJJSON") from exc
        if _canonical_crs(crs) != declared_crs:
            raise BundleCodecError("CRS metadata is not canonical")

    values: dict[str, list[object]] = {name: [] for name in names}
    for row_wire in rows:
        row_item = _require_keys(row_wire, {"cells", "content_sha256"}, "row")
        row_digest = row_item["content_sha256"]
        if not isinstance(row_digest, str) or _SHA256.fullmatch(row_digest) is None:
            raise BundleCodecError("row content_sha256 must be a full lowercase SHA-256")
        row = row_item["cells"]
        if _sha256(row) != row_digest:
            raise BundleCodecError("row content fingerprint mismatch")
        if not isinstance(row, list) or len(row) != len(names):
            raise BundleCodecError("row width does not match columns")
        for name, kind, cell in zip(names, kinds, row, strict=True):
            if kind == "geometry":
                if cell == {"type": "null"}:
                    value = None
                else:
                    tagged = _require_keys(cell, {"type", "value"}, "geometry cell")
                    if tagged["type"] != "geometry-wkb" or not isinstance(tagged["value"], str):
                        raise BundleCodecError("invalid geometry cell")
                    try:
                        value = from_wkb(bytes.fromhex(tagged["value"]), on_invalid="raise")
                    except Exception as exc:
                        raise BundleCodecError("invalid geometry WKB") from exc
                    canonical = to_wkb(value, hex=True, byte_order=1, include_srid=False)
                    if canonical != tagged["value"]:
                        raise BundleCodecError("geometry WKB is not canonical")
                values[name].append(value)
            elif kind == "typed-json":
                values[name].append(_unwire_value(cell))
            else:
                raise BundleCodecError("unknown column kind")

    data: dict[str, object] = {}
    for name, dtype in zip(names, dtypes, strict=True):
        if name in geometry_columns:
            if dtype != "geometry":
                raise BundleCodecError("geometry column dtype must be geometry")
            data[name] = gpd.GeoSeries(values[name], crs=crs)
        else:
            try:
                data[name] = pd.Series(values[name], dtype=dtype)
            except (TypeError, ValueError) as exc:
                raise BundleCodecError(f"column {name!r} does not match dtype {dtype!r}") from exc
    result = gpd.GeoDataFrame(data, geometry=geometry["active_column"], crs=crs)
    canonical = encode_geodataframe(
        result,
        stable_key_columns=tuple(stable_key_columns) or None,
        missing_crs_rule=missing_crs_rule,
    )
    if canonical != wire:
        raise BundleCodecError("decoded GeoDataFrame does not reproduce its canonical wire payload")
    return result


def encode_compiled_network_bundle(
    compiled: object,
    *,
    area_identity: str,
    input_identity: str,
    dependency_identity: str,
    upstream_artifact_ids: tuple[str, ...],
    frame_stable_keys: dict[str, tuple[str, ...]] | None = None,
    bundle_crs: object | None = None,
) -> dict[str, object]:
    """Bind every field of a compiled-network-like dataclass into one envelope."""

    if not is_dataclass(compiled) or isinstance(compiled, type):
        raise TypeError("compiled must be a dataclass instance")
    identities = {
        "area": area_identity,
        "input": input_identity,
        "dependency": dependency_identity,
    }
    invalid = [
        name
        for name, value in identities.items()
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None
    ]
    if invalid:
        raise BundleCodecError(f"bundle identities must be full lowercase SHA-256: {invalid}")
    if any(
        not isinstance(item, str) or _SHA256.fullmatch(item) is None
        for item in upstream_artifact_ids
    ):
        raise BundleCodecError("upstream artifact IDs must be full lowercase SHA-256 values")
    if len(upstream_artifact_ids) != len(set(upstream_artifact_ids)):
        raise BundleCodecError("upstream artifact IDs must be unique")
    stable_keys = frame_stable_keys or {}
    frame_crs_rule = _bundle_crs_rule(bundle_crs)
    declared_fields = [item.name for item in fields(compiled)]
    unknown_keys = sorted(set(stable_keys) - set(declared_fields))
    if unknown_keys:
        raise BundleCodecError(f"frame stable keys name unknown fields: {unknown_keys}")
    encoded_fields: dict[str, object] = {}
    unsupported: list[str] = []
    for item in fields(compiled):
        value = getattr(compiled, item.name)
        if isinstance(value, gpd.GeoDataFrame):
            encoded_fields[item.name] = {
                "encoding": _FRAME_CONTRACT,
                "payload": encode_geodataframe(
                    value,
                    stable_key_columns=stable_keys.get(item.name),
                    missing_crs_rule=frame_crs_rule,
                ),
            }
            continue
        try:
            typed = _wire_value(value)
        except BundleCodecError as exc:
            unsupported.append(f"{item.name} ({exc})")
            continue
        encoded_fields[item.name] = {
            "encoding": _JSON_CONTRACT,
            "payload": typed,
        }
    if unsupported:
        raise BundleCodecError("unsupported compiled fields: " + ", ".join(unsupported))
    body: dict[str, object] = {
        "contract": _BUNDLE_CONTRACT,
        "dataclass": type(compiled).__name__,
        "identities": identities,
        "upstream_artifact_ids": sorted(upstream_artifact_ids),
        "frame_crs_rule": frame_crs_rule,
        "fields": encoded_fields,
    }
    return {**body, "content_sha256": _sha256(body)}


def _restore_expected(value: object, annotation: object, model_name: str | None = None) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation in {Any, object}:
        return value
    if origin in {Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        candidates = [item for item in args if item is not type(None)]
        if len(candidates) == 1:
            return _restore_expected(value, candidates[0], model_name)
        for candidate in candidates:
            try:
                return _restore_expected(value, candidate, model_name)
            except BundleCodecError:
                continue
        raise BundleCodecError("value does not match any union annotation")
    if annotation is type(None):
        if value is not None:
            raise BundleCodecError("value does not match None annotation")
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if not isinstance(value, _DecodedPydanticModel):
            raise BundleCodecError("pydantic annotation requires a model wire contract")
        if value.name != annotation.__name__ or (
            model_name is not None and model_name != annotation.__name__
        ):
            raise BundleCodecError("pydantic model tag does not match expected field type")
        try:
            return annotation.model_validate(value.value)
        except (TypeError, ValueError) as exc:
            raise BundleCodecError(
                f"value does not match annotation {annotation.__name__}"
            ) from exc
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, _DecodedDataclass) or value.name != annotation.__name__:
            raise BundleCodecError("dataclass tag does not match expected field type")
        expected_fields = [item.name for item in fields(annotation)]
        if list(value.values) != expected_fields:
            raise BundleCodecError("nested dataclass fields differ from expected contract")
        annotations = _resolved_type_hints(annotation)
        try:
            return annotation(
                **{
                    name: _restore_expected(value.values[name], annotations[name])
                    for name in expected_fields
                }
            )
        except (TypeError, ValueError) as exc:
            raise BundleCodecError(
                f"value does not satisfy dataclass {annotation.__name__}"
            ) from exc
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise BundleCodecError(
                f"value does not match annotation {annotation.__name__}"
            ) from exc
    if origin is list:
        if type(value) is not list:
            raise BundleCodecError("value does not match list annotation")
        subtype = args[0] if args else object
        return [_restore_expected(item, subtype) for item in value]
    if origin is tuple:
        if type(value) is not tuple:
            raise BundleCodecError("value does not match tuple annotation")
        if not args:
            return value
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_restore_expected(item, args[0]) for item in value)
        if len(value) != len(args):
            raise BundleCodecError("value does not match fixed tuple annotation")
        return tuple(
            _restore_expected(item, subtype) for item, subtype in zip(value, args, strict=True)
        )
    if origin in {dict, Mapping}:
        if type(value) is not dict:
            raise BundleCodecError("value does not match mapping annotation")
        key_type, item_type = args if args else (object, object)
        return {
            _restore_expected(key, key_type): _restore_expected(item, item_type)
            for key, item in value.items()
        }
    if annotation in {bool, int, float, str, bytes, date, datetime}:
        if type(value) is not annotation:
            raise BundleCodecError(f"value does not match annotation {annotation.__name__}")
        return value
    if isinstance(annotation, type):
        if not isinstance(value, annotation):
            raise BundleCodecError(f"value does not match annotation {annotation.__name__}")
        return value
    raise BundleCodecError(f"unsupported annotation {annotation!r}")


def _resolved_type_hints(expected_type: type[Any]) -> dict[str, object]:
    """Resolve only compiler-owned forward types from a fixed trusted registry."""

    from satn.strategic_network_planning import StrategicNetworkPlanningResult
    from satn.strategic_reference_publication import StrategicReferencePublicationRecord

    module = sys.modules.get(expected_type.__module__)
    namespace = dict(vars(module)) if module is not None else {}
    namespace.update(
        {
            "StrategicNetworkPlanningResult": StrategicNetworkPlanningResult,
            "StrategicReferencePublicationRecord": StrategicReferencePublicationRecord,
        }
    )
    try:
        return get_type_hints(expected_type, globalns=namespace, localns=namespace)
    except (NameError, TypeError) as exc:
        raise BundleCodecError(
            f"unresolved annotations for expected dataclass {expected_type.__name__}"
        ) from exc


def decode_compiled_network_bundle(payload: object, expected_type: type[Any]) -> object:
    """Validate an envelope and construct only the caller-supplied dataclass type."""

    if not is_dataclass(expected_type):
        raise TypeError("expected_type must be a dataclass type")
    wire = _require_keys(
        payload,
        {
            "contract",
            "dataclass",
            "identities",
            "upstream_artifact_ids",
            "frame_crs_rule",
            "fields",
            "content_sha256",
        },
        "CompiledNetworkBundle",
    )
    digest = wire["content_sha256"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise BundleCodecError("bundle content_sha256 must be a full lowercase SHA-256")
    body = {key: value for key, value in wire.items() if key != "content_sha256"}
    if _sha256(body) != digest:
        raise BundleCodecError("bundle content fingerprint mismatch")
    if wire["contract"] != _BUNDLE_CONTRACT:
        raise BundleCodecError("unsupported CompiledNetworkBundle contract")
    if wire["dataclass"] != expected_type.__name__:
        raise BundleCodecError("bundle dataclass does not match expected type")
    identities = _require_keys(wire["identities"], {"area", "input", "dependency"}, "identities")
    if any(
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
        for value in identities.values()
    ):
        raise BundleCodecError("bundle identities must be full lowercase SHA-256")
    upstream_ids = wire["upstream_artifact_ids"]
    if (
        not isinstance(upstream_ids, list)
        or any(
            not isinstance(item, str) or _SHA256.fullmatch(item) is None for item in upstream_ids
        )
        or upstream_ids != sorted(set(upstream_ids))
    ):
        raise BundleCodecError(
            "upstream artifact IDs must be sorted unique full lowercase SHA-256 values"
        )
    frame_crs_rule = _validate_crs_rule(wire["frame_crs_rule"])
    field_payloads = wire["fields"]
    if not isinstance(field_payloads, dict):
        raise BundleCodecError("bundle fields must be an object")
    expected_fields = {item.name for item in fields(expected_type)}
    if set(field_payloads) != expected_fields:
        missing = sorted(expected_fields - set(field_payloads))
        unknown = sorted(set(field_payloads) - expected_fields)
        raise BundleCodecError(f"bundle fields differ: missing={missing}, unknown={unknown}")
    annotations = _resolved_type_hints(expected_type)
    values: dict[str, object] = {}
    for name in sorted(expected_fields):
        item = _require_keys(field_payloads[name], {"encoding", "payload"}, f"field {name}")
        annotation = annotations.get(name, object)
        try:
            if item["encoding"] == _FRAME_CONTRACT:
                values[name] = _restore_expected(
                    decode_geodataframe(item["payload"], missing_crs_rule=frame_crs_rule),
                    annotation,
                )
            elif item["encoding"] == _JSON_CONTRACT:
                model_name = (
                    item["payload"].get("model")
                    if isinstance(item["payload"], dict)
                    and item["payload"].get("type") == "pydantic-model"
                    else None
                )
                values[name] = _restore_expected(
                    _unwire_value(item["payload"]), annotation, model_name
                )
                if _wire_value(values[name]) != item["payload"]:
                    raise BundleCodecError("typed JSON is not canonical for its annotation")
            else:
                raise BundleCodecError("unsupported encoding")
        except BundleCodecError as exc:
            raise BundleCodecError(f"field {name} does not match annotation: {exc}") from exc
    try:
        return expected_type(**values)
    except (TypeError, ValueError) as exc:
        raise BundleCodecError("decoded fields do not satisfy the expected dataclass") from exc
