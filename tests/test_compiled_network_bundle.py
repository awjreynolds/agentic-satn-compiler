from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from enum import Enum
from types import MappingProxyType

import geopandas as gpd
import pandas as pd
import pytest
from geopandas.testing import assert_geodataframe_equal
from pydantic import BaseModel
from pyproj import CRS
from shapely.geometry import LineString, Point
from test_backbone_assembly import config, parallel_spine_source
from test_strategic_network_planning import discovery, fixture_graph, request

from satn.agents import FakeAgentRuntime
from satn.candidate_discovery import CorridorObligation
from satn.compiled_network_bundle import (
    BundleCodecError,
    decode_compiled_network_bundle,
    decode_geodataframe,
    encode_compiled_network_bundle,
    encode_geodataframe,
)
from satn.compiler import CompiledNetwork, compile_network
from satn.spine_access_candidate_preparation import SpineAccessCandidatePreparationResult
from satn.strategic_corridors import NetworkSelectionPreparationResult
from satn.strategic_network_planning import (
    StrategicNetworkPlanningResult,
    compile_strategic_network,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "feature_id": pd.Series(["beta", "alpha"], dtype="string"),
            "count": pd.Series([None, 3], dtype="Int64"),
            "enabled": pd.Series([True, None], dtype="boolean"),
            "ratio": pd.Series([1.25, None], dtype="Float64"),
            "metadata": [{"roles": ["access", "spine"]}, None],
            "geometry": [LineString([(1, 1), (2, 2)]), Point(0, 0)],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )


def _rehash_frame_payload(payload: dict[str, object]) -> None:
    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    payload["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_geodataframe_wire_round_trip_is_canonical_and_exact() -> None:
    frame = _frame()

    encoded = encode_geodataframe(frame)
    shuffled = encode_geodataframe(frame.iloc[::-1].reset_index(drop=True))

    assert encoded == shuffled
    assert encoded["contract"] == "satn-geodataframe-wire/v1"
    assert len(encoded["content_sha256"]) == 64
    assert all(len(row["content_sha256"]) == 64 for row in encoded["rows"])
    assert encoded["geometry"]["encoding"] == "ogc-wkb-hex"
    assert encoded["crs"]["authority"] == {"name": "EPSG", "code": "27700"}
    assert [item["dtype"] for item in encoded["columns"]] == [
        "string",
        "Int64",
        "boolean",
        "Float64",
        "object",
        "geometry",
    ]

    decoded = decode_geodataframe(encoded)
    expected = frame.sort_values("feature_id").reset_index(drop=True)
    assert_geodataframe_equal(decoded, expected, check_dtype=True, check_crs=True)


def test_equivalent_authority_crs_representations_share_one_canonical_payload() -> None:
    ensemble = CRS.from_epsg(4326)
    legacy = CRS.from_wkt(ensemble.to_wkt(version="WKT1_GDAL"))
    assert ensemble.to_authority() == legacy.to_authority() == ("EPSG", "4326")
    frame = gpd.GeoDataFrame(
        {"feature_id": ["route"], "geometry": [LineString([(-2, 51), (-1, 52)])]},
        geometry="geometry",
        crs=ensemble,
    )
    legacy_frame = frame.to_crs(legacy)

    encoded = encode_geodataframe(frame)
    legacy_encoded = encode_geodataframe(legacy_frame)

    assert encoded == legacy_encoded
    assert encoded["crs"] == {
        "authority": {"name": "EPSG", "code": "4326"},
        "projjson": None,
    }
    decoded = decode_geodataframe(legacy_encoded)
    assert decoded.crs == CRS.from_epsg(4326)


def test_crs_metadata_rejects_forged_authority_and_custom_definitions() -> None:
    authority_payload = encode_geodataframe(_frame().set_crs("EPSG:4326", allow_override=True))
    authority_forgery = deepcopy(authority_payload)
    authority_forgery["crs"]["projjson"] = {}
    _rehash_frame_payload(authority_forgery)
    with pytest.raises(BundleCodecError, match=r"authority-backed CRS.*projjson"):
        decode_geodataframe(authority_forgery)

    custom_crs = CRS.from_user_input("+proj=longlat +a=6378137 +rf=298.257223563 +no_defs")
    custom_frame = _frame().set_crs(custom_crs, allow_override=True)
    custom_payload = encode_geodataframe(custom_frame)
    assert custom_payload["crs"]["authority"] is None
    assert isinstance(custom_payload["crs"]["projjson"], dict)
    custom_forgery = deepcopy(custom_payload)
    custom_forgery["crs"]["projjson"]["id"] = {"authority": "EPSG", "code": 4326}
    _rehash_frame_payload(custom_forgery)
    with pytest.raises(BundleCodecError, match="authority-free CRS"):
        decode_geodataframe(custom_forgery)


def test_custom_projected_crs_with_local_root_id_round_trips() -> None:
    base = CRS.from_user_input(
        "+proj=tmerc +lat_0=51 +lon_0=-2 +k=0.9996 +x_0=400000 "
        "+y_0=-100000 +ellps=GRS80 +units=m +no_defs"
    )
    definition = base.to_json_dict()
    definition["id"] = {"authority": "LOCAL", "code": "BANES-GRID"}
    local_crs = CRS.from_json_dict(definition)
    assert local_crs.to_authority() is None

    frame = _frame().set_crs(local_crs, allow_override=True)
    encoded = encode_geodataframe(frame)

    assert encoded["crs"]["authority"] is None
    assert "id" not in encoded["crs"]["projjson"]
    assert decode_geodataframe(encoded).crs.to_authority() is None


def test_geodataframe_decode_fails_closed_for_tampering() -> None:
    encoded = encode_geodataframe(_frame())

    fingerprint_tamper = deepcopy(encoded)
    fingerprint_tamper["rows"][0]["cells"][0]["value"] = "tampered"
    with pytest.raises(BundleCodecError, match="fingerprint mismatch"):
        decode_geodataframe(fingerprint_tamper)

    geometry_tamper = deepcopy(encoded)
    geometry_tamper["rows"][0]["cells"][-1]["value"] = "00"
    geometry_tamper["rows"][0]["content_sha256"] = hashlib.sha256(
        json.dumps(
            geometry_tamper["rows"][0]["cells"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    geometry_body = {
        key: value for key, value in geometry_tamper.items() if key != "content_sha256"
    }
    geometry_tamper["content_sha256"] = hashlib.sha256(
        json.dumps(
            geometry_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    with pytest.raises(BundleCodecError, match="invalid geometry WKB"):
        decode_geodataframe(geometry_tamper)


def test_duplicate_rows_and_ambiguous_stable_keys_fail_closed() -> None:
    duplicate = pd.concat([_frame().iloc[[0]], _frame().iloc[[0]]], ignore_index=True)
    duplicate = gpd.GeoDataFrame(duplicate, geometry="geometry", crs=27700)

    with pytest.raises(BundleCodecError, match="duplicate rows are ambiguous"):
        encode_geodataframe(duplicate)
    with pytest.raises(BundleCodecError, match="stable key columns are null, duplicate"):
        encode_geodataframe(duplicate, stable_key_columns=("feature_id",))


@pytest.mark.parametrize("stable_keys", [None, "feature_id", [7], ["feature_id", "feature_id"]])
def test_geodataframe_decode_rejects_invalid_stable_key_schema(stable_keys: object) -> None:
    encoded = encode_geodataframe(_frame(), stable_key_columns=("feature_id",))
    encoded["stable_key_columns"] = stable_keys
    body = {key: value for key, value in encoded.items() if key != "content_sha256"}
    encoded["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()

    with pytest.raises(BundleCodecError, match="stable_key_columns"):
        decode_geodataframe(encoded)


@dataclass
class _CompiledFixture:
    routes: gpd.GeoDataFrame
    area_name: str
    diagnostics: dict[str, object]
    generation: int
    optional_note: str | None


def _bundle() -> dict[str, object]:
    return encode_compiled_network_bundle(
        _CompiledFixture(
            routes=_frame(),
            area_name="B&NES",
            diagnostics={"warnings": [], "complete": True},
            generation=7,
            optional_note=None,
        ),
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(SHA_A, SHA_B),
        frame_stable_keys={"routes": ("feature_id",)},
    )


def _rehash_bundle(encoded: dict[str, object]) -> None:
    body = {key: value for key, value in encoded.items() if key != "content_sha256"}
    encoded["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_compiled_network_bundle_binds_every_field_and_identity() -> None:
    encoded = _bundle()

    assert encoded["identities"] == {
        "area": SHA_A,
        "input": SHA_B,
        "dependency": SHA_C,
    }
    assert set(encoded["fields"]) == {
        "routes",
        "area_name",
        "diagnostics",
        "generation",
        "optional_note",
    }
    assert len(encoded["content_sha256"]) == 64

    decoded = decode_compiled_network_bundle(encoded, _CompiledFixture)
    assert decoded.area_name == "B&NES"
    assert decoded.diagnostics == {"complete": True, "warnings": []}
    assert decoded.generation == 7
    assert decoded.optional_note is None
    expected_routes = _frame().sort_values("feature_id").reset_index(drop=True)
    assert_geodataframe_equal(decoded.routes, expected_routes, check_dtype=True, check_crs=True)


@pytest.mark.parametrize("mutation", ["omit", "unknown"])
def test_compiled_network_bundle_refuses_omitted_or_unknown_fields(mutation: str) -> None:
    encoded = _bundle()
    if mutation == "omit":
        del encoded["fields"]["area_name"]
    else:
        encoded["fields"]["invented"] = encoded["fields"]["generation"]

    body = {key: value for key, value in encoded.items() if key != "content_sha256"}
    encoded["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    with pytest.raises(BundleCodecError, match="bundle fields differ"):
        decode_compiled_network_bundle(encoded, _CompiledFixture)


@dataclass
class _UnsupportedFixture:
    routes: gpd.GeoDataFrame
    callback: object


def test_bundle_rejects_unsupported_fields_by_name() -> None:
    with pytest.raises(BundleCodecError, match=r"callback \(unsupported value type function\)"):
        encode_compiled_network_bundle(
            _UnsupportedFixture(routes=_frame(), callback=lambda: None),
            area_identity=SHA_A,
            input_identity=SHA_B,
            dependency_identity=SHA_C,
            upstream_artifact_ids=(),
        )


@dataclass(frozen=True)
class _MappingFixture:
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        def freeze(value: object) -> object:
            if isinstance(value, Mapping):
                return MappingProxyType({key: freeze(item) for key, item in value.items()})
            return value

        object.__setattr__(self, "values", freeze(self.values))


def test_bundle_round_trips_mappingproxy_fields_and_nested_mappings() -> None:
    fixture = _MappingFixture(
        MappingProxyType(
            {
                "nested": MappingProxyType({"enabled": True, "label": "cycleway"}),
                "count": 2,
            }
        )
    )
    encoded = encode_compiled_network_bundle(
        fixture,
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
    )

    decoded = decode_compiled_network_bundle(encoded, _MappingFixture)

    assert decoded == fixture
    assert isinstance(decoded.values, MappingProxyType)
    assert isinstance(decoded.values["nested"], MappingProxyType)


def test_bundle_round_trips_network_selection_preparation_result() -> None:
    spine = SpineAccessCandidatePreparationResult(
        contract="satn-spine-access-candidate-preparation/v1",
        profile_fingerprint=SHA_A,
        status="prepared",
        prepared_spine_access_connections=(),
        connection_roster=(),
        generation_issues=(),
        missing_inputs=(),
        evidence_fingerprints=(),
        evidence_lineage={},
        preparation_fingerprint=SHA_B,
        diagnostics={},
    )
    fixture = NetworkSelectionPreparationResult(spine, None)
    encoded = encode_compiled_network_bundle(
        fixture,
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
    )

    assert decode_compiled_network_bundle(encoded, NetworkSelectionPreparationResult) == fixture


def test_bundle_round_trips_strategic_planning_candidate_sets() -> None:
    graph = fixture_graph()
    fixture = compile_strategic_network(
        request(graph, discovery(graph, CorridorObligation("corridor-a-d", "A", "D")))
    )
    encoded = encode_compiled_network_bundle(
        fixture,
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
    )

    decoded = decode_compiled_network_bundle(encoded, StrategicNetworkPlanningResult)

    assert decoded == fixture
    assert type(decoded.candidate_sets[0]) is type(fixture.candidate_sets[0])


@pytest.mark.parametrize("wrong_encoding", ["typed-string", "geodataframe"])
def test_bundle_decode_rejects_field_encoding_incompatible_with_annotation(
    wrong_encoding: str,
) -> None:
    encoded = _bundle()
    if wrong_encoding == "typed-string":
        encoded["fields"]["generation"]["payload"] = {
            "type": "string",
            "value": "7",
        }
    else:
        encoded["fields"]["generation"] = deepcopy(encoded["fields"]["routes"])
    _rehash_bundle(encoded)

    with pytest.raises(BundleCodecError, match=r"generation.*annotation"):
        decode_compiled_network_bundle(encoded, _CompiledFixture)


class _State(Enum):
    READY = "ready"


@dataclass
class _TypedFixture:
    states: dict[str, _State]
    counts: tuple[int, ...]
    labels: list[str]
    ratio: float


@dataclass(frozen=True)
class _NestedValue:
    state: _State
    weights: tuple[int, ...]


@dataclass
class _NestedFixture:
    nested: _NestedValue
    optional_nested: _NestedValue | None


class _NestedModel(BaseModel):
    name: str


@dataclass
class _NestedModelFixture:
    models: list[_NestedModel]


def _typed_bundle() -> dict[str, object]:
    return encode_compiled_network_bundle(
        _TypedFixture(
            states={"primary": _State.READY},
            counts=(1, 2),
            labels=["one", "two"],
            ratio=1.5,
        ),
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
    )


def test_bundle_restores_recursive_container_types() -> None:
    decoded = decode_compiled_network_bundle(_typed_bundle(), _TypedFixture)

    assert decoded == _TypedFixture(
        states={"primary": _State.READY},
        counts=(1, 2),
        labels=["one", "two"],
        ratio=1.5,
    )


@pytest.mark.parametrize("field", ["states", "counts", "labels"])
def test_bundle_rejects_recursive_container_type_mismatches(field: str) -> None:
    encoded = _typed_bundle()
    payload = encoded["fields"][field]["payload"]
    if field == "states":
        payload["items"][0][1] = {"type": "string", "value": "not-a-state"}
    elif field == "counts":
        payload["type"] = "list"
    else:
        payload["items"][0] = {"type": "int", "value": "1"}
    _rehash_bundle(encoded)

    with pytest.raises(BundleCodecError, match=field):
        decode_compiled_network_bundle(encoded, _TypedFixture)


def test_bundle_rejects_noncanonical_typed_json_after_decode() -> None:
    encoded = _typed_bundle()
    encoded["fields"]["ratio"]["payload"]["value"] = "Infinity"
    _rehash_bundle(encoded)

    with pytest.raises(BundleCodecError, match=r"ratio.*canonical"):
        decode_compiled_network_bundle(encoded, _TypedFixture)


def test_bundle_round_trips_versioned_nested_dataclass_and_rejects_corruption() -> None:
    fixture = _NestedFixture(
        nested=_NestedValue(_State.READY, (1, 2)),
        optional_nested=None,
    )
    encoded = encode_compiled_network_bundle(
        fixture,
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
    )

    assert decode_compiled_network_bundle(encoded, _NestedFixture) == fixture
    corrupted = deepcopy(encoded)
    corrupted["fields"]["nested"]["payload"]["fields"][0][1]["value"]["value"] = "corrupt"
    _rehash_bundle(corrupted)
    with pytest.raises(BundleCodecError, match=r"nested.*fingerprint"):
        decode_compiled_network_bundle(corrupted, _NestedFixture)


def test_bundle_rejects_nested_pydantic_model_fingerprint_corruption() -> None:
    encoded = encode_compiled_network_bundle(
        _NestedModelFixture(models=[_NestedModel(name="primary")]),
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
    )
    encoded["fields"]["models"]["payload"]["items"][0]["content_sha256"] = "0" * 64
    _rehash_bundle(encoded)

    with pytest.raises(BundleCodecError, match=r"models.*fingerprint"):
        decode_compiled_network_bundle(encoded, _NestedModelFixture)


def test_missing_crs_rule_is_explicit_and_empty_only() -> None:
    empty = gpd.GeoDataFrame(columns=["id", "geometry"], geometry="geometry", crs=None)
    with pytest.raises(BundleCodecError, match="CRS is required"):
        encode_geodataframe(empty)

    encoded_bundle = encode_compiled_network_bundle(
        _CompiledFixture(empty, "B&NES", {}, 1, None),
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(),
        bundle_crs="EPSG:27700",
    )
    decoded = decode_compiled_network_bundle(encoded_bundle, _CompiledFixture)
    assert decoded.routes.empty
    assert decoded.routes.crs is None

    nonempty = gpd.GeoDataFrame(
        {"id": ["route"], "geometry": [Point(0, 0)]}, geometry="geometry", crs=None
    )
    with pytest.raises(BundleCodecError, match="empty-frame rule"):
        encode_compiled_network_bundle(
            _CompiledFixture(nonempty, "B&NES", {}, 1, None),
            area_identity=SHA_A,
            input_identity=SHA_B,
            dependency_identity=SHA_C,
            upstream_artifact_ids=(),
            bundle_crs="EPSG:27700",
        )


@pytest.mark.parametrize("identity", [None, 7])
def test_bundle_invalid_identity_type_has_codec_error(identity: object) -> None:
    with pytest.raises(BundleCodecError, match="full lowercase SHA-256"):
        encode_compiled_network_bundle(
            _CompiledFixture(_frame(), "B&NES", {}, 1, None),
            area_identity=identity,
            input_identity=SHA_B,
            dependency_identity=SHA_C,
            upstream_artifact_ids=(),
        )


def test_real_ordinary_compiled_network_round_trip_is_complete_and_canonical() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())

    encoded = encode_compiled_network_bundle(
        compiled,
        area_identity=SHA_A,
        input_identity=SHA_B,
        dependency_identity=SHA_C,
        upstream_artifact_ids=(SHA_A,),
        bundle_crs=compiled.boundary.crs,
    )
    decoded = decode_compiled_network_bundle(encoded, CompiledNetwork)

    assert len(fields(CompiledNetwork)) == 57
    assert set(encoded["fields"]) == {field.name for field in fields(CompiledNetwork)}
    for field in fields(CompiledNetwork):
        original = getattr(compiled, field.name)
        restored = getattr(decoded, field.name)
        assert type(restored) is type(original), field.name
        if isinstance(original, gpd.GeoDataFrame):
            assert encode_geodataframe(
                restored,
                missing_crs_rule=encoded["frame_crs_rule"],
            ) == encode_geodataframe(
                original,
                missing_crs_rule=encoded["frame_crs_rule"],
            )
        else:
            assert restored == original, field.name

    shuffled = replace(
        compiled,
        **{
            field.name: getattr(compiled, field.name).iloc[::-1].reset_index(drop=True)
            for field in fields(CompiledNetwork)
            if isinstance(getattr(compiled, field.name), gpd.GeoDataFrame)
        },
    )
    assert (
        encode_compiled_network_bundle(
            shuffled,
            area_identity=SHA_A,
            input_identity=SHA_B,
            dependency_identity=SHA_C,
            upstream_artifact_ids=(SHA_A,),
            bundle_crs=compiled.boundary.crs,
        )["content_sha256"]
        == encoded["content_sha256"]
    )


def test_bundle_rejects_non_artifact_upstream_references() -> None:
    with pytest.raises(BundleCodecError, match="full lowercase SHA-256"):
        encode_compiled_network_bundle(
            _CompiledFixture(
                routes=_frame(),
                area_name="B&NES",
                diagnostics={},
                generation=1,
                optional_note=None,
            ),
            area_identity=SHA_A,
            input_identity=SHA_B,
            dependency_identity=SHA_C,
            upstream_artifact_ids=("snapshot:" + SHA_A,),
        )
