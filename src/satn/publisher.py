"""Atomic publication of spatial, machine-readable and visual artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shlex
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import escape
from importlib.resources import files
from pathlib import Path, PurePosixPath

import geopandas as gpd
import networkx as nx
import pandas as pd
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A2, A3, A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from shapely.geometry import LineString, MultiLineString, mapping, shape

from satn.alignment_selection import CandidateValidity, CanonicalLineString, CriterionState
from satn.asset_accounting import accounting_geojson
from satn.compiler import CompiledNetwork
from satn.constants import DISCLAIMER, SCHEMA_VERSION
from satn.content_identity import content_fingerprint
from satn.cross_spine import validate_cross_spine_publication
from satn.ea_elevation import (
    SAMPLE_LEDGER_FILENAME,
    WECA_PINNED_ELIGIBLE_ROUTE_BBOX,
    WECA_ROUTING_BUFFER_M,
    WECA_SURVEY_REQUEST_BBOX,
    eligible_route_fingerprint,
    eligible_route_samples,
    governed_survey_request_bbox,
)
from satn.filesystem_safety import (
    OWNER_MARKER_NAME,
    PublicationDestinationAuthority,
    commit_replacement,
    default_publication_destination_authority,
    stage_replacement,
    write_ownership_marker,
)
from satn.models import (
    AgentDecisionLedger,
    AgentRecord,
    AreaConfig,
    CompilationResult,
    DivergenceRecord,
    PublishedArtifactReference,
    PublishedNetworkFeatureReference,
    TrafficLight,
    canonical_decision_ledger_payload,
)
from satn.reference_application import (
    REFERENCE_SELECTED_ALIGNMENT_OPTION_FIELDS,
    ReferenceApplicationPlan,
    ReferenceSATNPublicationRecord,
)
from satn.runtime_governance import classify_runtime_governance, validate_runtime_governance
from satn.sources import (
    EA_LIDAR_WECA_ACQUISITION_CONTRACT,
    EA_RETAINED_ROUTE_FILENAME,
    ELEVATION_EVIDENCE_FILENAME,
    NCN_ATTRIBUTION,
    OSM_ATTRIBUTION,
    _validate_canonical_retained_ea_evidence,
    _validated_ea_snapshot_replay_inputs,
)
from satn.strategic_reference_application import StrategicReferenceApplicationDisposition
from satn.strategic_reference_publication import StrategicReferencePublicationRecord

LOGGER = logging.getLogger(__name__)

EA_FIXED_POINT_CANDIDATE_SCHEMA_VERSION = "ea-fixed-point-candidate/v1"
EA_FIXED_POINT_CANDIDATE_DIRECTORY = ".satn-ea-fixed-point-candidates"
EA_FIXED_POINT_CANDIDATE_NETWORK = "network.geojson"
EA_FIXED_POINT_CANDIDATE_STATUS = "status.json"
REVIEW_MAP_ZIP_MAX_COMPRESSION_RATIO = 200


class EAFixedPointMismatchError(ValueError):
    """The sole EA fixed-point failure that can retain a candidate route network."""

    def __init__(self, *, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            "EA elevation two-pass fixed point failed: final eligible routes differ from "
            "the routes sampled before elevation acquisition "
            f"(expected={expected}, actual={actual})"
        )


@dataclass(frozen=True)
class _ValidatedReferencePublication:
    """Canonical publication bytes anchored to one actual compiled network."""

    record: ReferenceSATNPublicationRecord
    payload_json: str
    options_json: str

    def payload(self) -> dict[str, object]:
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("Validated Reference publication payload is not an object")
        return payload

    def options(self) -> dict[str, object]:
        options = json.loads(self.options_json)
        if not isinstance(options, dict):
            raise ValueError("Validated Reference publication options are not an object")
        return options


@dataclass(frozen=True)
class _ValidatedStrategicReferencePublication:
    """A deep-validated strategic publication sibling bound to compiler frames."""

    record: StrategicReferencePublicationRecord
    payload_json: str

    def payload(self) -> dict[str, object]:
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("validated strategic Reference payload is not an object")
        return payload


def _strategic_binding_identity(value: object, *, application_plan: bool = False) -> dict[str, str]:
    """Project a plan binding or replay record onto one canonical identity."""
    getter = getattr(value, "get", None)
    if not callable(getter):
        raise ValueError("strategic binding identity source is not a mapping")
    aliases = (
        {
            "binding_id": "binding_fingerprint",
            "candidate_id": "selected_candidate_id",
            "network_role": "unit_role",
        }
        if application_plan
        else {}
    )
    return {
        field: str(getter(aliases.get(field, field)))
        for field in (
            "binding_id",
            "candidate_id",
            "physical_alignment_id",
            "routing_start_node_id",
            "routing_end_node_id",
            "geometry_fingerprint",
            "network_role",
        )
    }


def publication_artifacts(output: Path) -> dict[str, Path]:
    """Return the stable artifact contract for a validated publication directory."""
    return {
        "geopackage": output / "network.gpkg",
        "geojson": output / "network.geojson",
        "asset_accounting": output / "asset-accounting.json",
        "asset_accounting_geojson": output / "asset-accounting.geojson",
        "run": output / "run.json",
        "agents": output / "agent-records.json",
        "divergences": output / "divergence-records.json",
        "human_intervention_requests": output / "human-intervention-requests.json",
        "backbone_comparison": output / "backbone-comparison.json",
        "review_map": output / "review-map" / "index.html",
        "review_zip": output / "review-map.zip",
        "pdf": output / "network-map.pdf",
    }


def published_artifact_reference(
    result: CompilationResult, artifact_key: str
) -> PublishedArtifactReference:
    """Derive the public identity of one artifact from a successful SATN result."""
    if result.status not in {"complete", "reviewable"}:
        raise ValueError("only successful SATN compilation results can publish artifact references")
    try:
        artifact = result.artifacts[artifact_key]
    except KeyError as error:
        raise ValueError(f"SATN result has no artifact with key {artifact_key!r}") from error
    if not artifact.is_file():
        raise ValueError(f"SATN artifact {artifact_key!r} is not a file")
    return PublishedArtifactReference(
        run_id=result.run_id,
        artifact_key=artifact_key,
        uri=artifact.resolve().as_uri(),
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )


def published_feature_reference(
    result: CompilationResult, feature_id: str | int, artifact_key: str = "geojson"
) -> PublishedNetworkFeatureReference:
    """Return one geometry-free feature identity from a successful public GeoJSON artifact."""
    artifact = published_artifact_reference(result, artifact_key)
    try:
        payload = json.loads(result.artifacts[artifact_key].read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(
            f"SATN artifact {artifact_key!r} is not readable public GeoJSON"
        ) from error
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError(f"SATN artifact {artifact_key!r} is not a GeoJSON FeatureCollection")
    requested_feature_id = _published_geojson_feature_id(feature_id)
    if requested_feature_id is None:
        raise ValueError("SATN public feature identity must be a nonblank string or integer")
    features = []
    for feature in payload["features"]:
        if not isinstance(feature, dict):
            raise ValueError("SATN public GeoJSON feature identity must be an object with an ID")
        if feature.get("type") != "Feature":
            raise ValueError("SATN public GeoJSON item must have type 'Feature'")
        published_id = _published_geojson_feature_id(feature.get("id"))
        if published_id is None:
            raise ValueError(
                "SATN public GeoJSON feature identity must be a nonblank string or integer"
            )
        if published_id == requested_feature_id:
            features.append((feature, published_id))
    if not features:
        raise ValueError(f"SATN public GeoJSON has no feature {requested_feature_id!r}")
    if len(features) != 1:
        raise ValueError(
            f"SATN public GeoJSON must contain exactly one feature {requested_feature_id!r}"
        )
    feature, published_id = features[0]
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise ValueError(f"SATN public GeoJSON feature {published_id!r} has no properties")
    _validate_published_geojson_geometry(feature, published_id)
    feature_type = _published_geojson_text_property(properties, "feature_type", published_id)
    network_role = _published_geojson_optional_text_property(
        properties, "network_role", published_id
    )
    reference_data = {
        "run_id": artifact.run_id,
        "artifact_key": artifact.artifact_key,
        "feature_id": published_id,
        "feature_type": feature_type,
        "source_artifact_uri": artifact.uri,
        "source_artifact_sha256": artifact.sha256,
    }
    if network_role is not None:
        reference_data["network_role"] = network_role
    return PublishedNetworkFeatureReference(**reference_data)


def _published_geojson_feature_id(value: object) -> str | None:
    """Normalize the supported, scalar public GeoJSON feature identifiers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _published_geojson_text_property(
    properties: dict[object, object], property_name: str, feature_id: str
) -> str:
    value = properties.get(property_name)
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"SATN public GeoJSON feature {feature_id!r} has no {property_name}")
    return normalized


def _published_geojson_optional_text_property(
    properties: dict[object, object], property_name: str, feature_id: str
) -> str | None:
    """Return an optional text property, rejecting a malformed present value."""
    if property_name not in properties:
        return None
    return _published_geojson_text_property(properties, property_name, feature_id)


def _validate_published_geojson_geometry(feature: dict[object, object], feature_id: str) -> None:
    """Require the selected public feature to carry a non-empty valid GeoJSON geometry."""
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError(f"SATN public GeoJSON feature {feature_id!r} has invalid geometry")
    try:
        parsed_geometry = shape(geometry)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"SATN public GeoJSON feature {feature_id!r} has invalid geometry"
        ) from error
    if parsed_geometry.is_empty:
        raise ValueError(f"SATN public GeoJSON feature {feature_id!r} has empty geometry")


def validate_publication(output: Path, config: AreaConfig) -> None:
    """Validate an existing publication before any whole-run reuse."""
    _validate_artifacts(output, config)
    # Reuse must apply the current EA two-pass proof, not merely trust the
    # validation that ran when the artefacts were first published.
    _validate_ea_elevation_fixed_point(config, output / "network.geojson")


def publish(
    config: AreaConfig,
    compiled: CompiledNetwork,
    run_id: str,
    *,
    publication_authority: PublicationDestinationAuthority | None = None,
) -> dict[str, Path]:
    reference_publication = _validated_reference_publication(
        compiled.reference_satn_publication,
        compiled,
    )
    strategic_reference_publication = _validated_strategic_reference_publication(
        compiled.strategic_reference_publication,
        compiled,
    )
    output = config.publication.output_dir
    authority = publication_authority or default_publication_destination_authority(
        config.config_path
    )
    LOGGER.info("Publication started temporary_parent=%s", output.parent)
    staging = stage_replacement(
        output,
        authority=authority,
        owner_kind=f"compiled-network:{config.area_id}",
        prior_record_name="run.json",
    )
    temporary = staging.temporary
    try:
        _write_geopackage(temporary / "network.gpkg", compiled)
        _write_geojson(temporary / "network.geojson", compiled)
        _write_asset_accounting(
            temporary / "asset-accounting.json",
            temporary / "asset-accounting.geojson",
            compiled,
        )
        try:
            _validate_ea_elevation_fixed_point(config, temporary / "network.geojson")
        except EAFixedPointMismatchError as error:
            try:
                candidate = _retain_ea_fixed_point_candidate(
                    config,
                    run_id=run_id,
                    network_path=temporary / "network.geojson",
                    expected=error.expected,
                    actual=error.actual,
                    governed_input_fingerprint=compiled.governed_input_fingerprint,
                )
            except ValueError as retention_error:
                raise ValueError(
                    f"{error}; candidate retention failed: {retention_error}"
                ) from retention_error
            raise ValueError(f"{error}; retained candidate={candidate}") from error
        _write_json_records(
            temporary,
            config,
            compiled,
            run_id,
            reference_publication,
            strategic_reference_publication,
        )
        _write_backbone_comparison(
            temporary / "backbone-comparison.json",
            compiled,
            config.publication.comparison_reference or output,
        )
        review = temporary / "review-map"
        review.mkdir()
        _write_review_map(
            review, config, compiled, reference_publication, strategic_reference_publication
        )
        (review / "asset-accounting.json").write_text(
            (temporary / "asset-accounting.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (review / "asset-accounting.geojson").write_text(
            (temporary / "asset-accounting.geojson").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        _zip_review_map(temporary / "review-map.zip", review)
        _write_pdf(temporary / "network-map.pdf", config, compiled)
        _validate_artifacts(temporary, config)
        write_ownership_marker(
            temporary,
            owner_kind=f"compiled-network:{config.area_id}",
        )
        LOGGER.info("Publication artifacts validated temporary=%s", temporary)
        commit_replacement(
            staging,
            authority=authority,
            owner_kind=f"compiled-network:{config.area_id}",
            prior_record_name="run.json",
        )
        LOGGER.info("Publication atomically replaced output=%s", output)
        if _uses_ea_lidar_weca_fixed_point(config):
            _remove_ea_fixed_point_candidate(config)
    finally:
        staging.cleanup()
    return publication_artifacts(output)


def _validated_reference_publication(
    attached: ReferenceSATNPublicationRecord | None,
    compiled: CompiledNetwork,
) -> _ValidatedReferencePublication | None:
    """Bind one strict Reference record to the exact compiler-produced result."""

    if attached is None:
        return None
    record = ReferenceSATNPublicationRecord.from_publication_payload(attached.publication_payload())
    payload = record.publication_payload()
    compiled_fields: tuple[tuple[str, object], ...] = (
        ("snapshot_manifest_sha256", compiled.snapshot_manifest_sha256),
        ("area_definition_sha256", compiled.area_definition_sha256),
        ("governed_input_fingerprint", compiled.governed_input_fingerprint),
        ("compilation_input_fingerprint", compiled.compilation_input_fingerprint),
        (
            "compilation_dependency_manifest",
            compiled.compilation_dependency_manifest,
        ),
        ("decision_contract", compiled.decision_contract),
        ("decision_ledger_input", compiled.decision_ledger_input),
        ("accepted_decisions", compiled.accepted_decisions),
    )
    for field, expected in compiled_fields:
        if payload.get(field) != expected:
            raise ValueError(f"Reference publication is not anchored to compiled {field}")
    actual_diagnostics = compiled.compilation_diagnostics.get("reference_application")
    if (
        not isinstance(actual_diagnostics, dict)
        or payload.get("application_diagnostics") != actual_diagnostics
    ):
        raise ValueError(
            "Reference publication is not anchored to compiled application diagnostics"
        )

    plan_payload = payload.get("application_plan")
    if not isinstance(plan_payload, dict):
        raise ValueError("Reference publication application plan is unavailable")
    plan = ReferenceApplicationPlan.model_validate(plan_payload)
    bindings = {binding.logical_connection_id: binding for binding in plan.candidate_bindings}
    selected_candidates = {
        candidate.candidate_id: candidate
        for candidate_set in record.reference_selection.scenario.candidate_sets
        for candidate in candidate_set.admitted_candidates
    }
    tagged_rows: dict[str, tuple[object, dict[str, object]]] = {}
    regenerated_ids: set[str] = set()
    for row in compiled.spine_access_connections.itertuples():
        raw_provenance = row.provenance
        try:
            provenance = (
                json.loads(raw_provenance) if isinstance(raw_provenance, str) else raw_provenance
            )
        except json.JSONDecodeError as error:
            raise ValueError("Compiled Spine Access provenance is not valid JSON") from error
        if not isinstance(provenance, dict):
            raise ValueError("Compiled Spine Access provenance is not an object")
        application = provenance.get("reference_application")
        if application is None:
            continue
        if not isinstance(application, dict):
            raise ValueError("Compiled Reference application provenance is malformed")
        logical_id = application.get("logical_connection_id")
        if not isinstance(logical_id, str) or logical_id in tagged_rows:
            raise ValueError(
                "Compiled Reference application rows duplicate or omit a logical connection"
            )
        regenerated_id = str(row.access_connection_id)
        if regenerated_id in regenerated_ids:
            raise ValueError(
                "Compiled Reference application rows duplicate a regenerated connection"
            )
        regenerated_ids.add(regenerated_id)
        tagged_rows[logical_id] = (row, application)
    if set(tagged_rows) != set(bindings):
        raise ValueError(
            "Compiled Reference application rows are missing or contain foreign bindings"
        )
    diagnostic_options = actual_diagnostics.get("selected_alignment_options")
    diagnostic_distances = actual_diagnostics.get("published_distances_km")
    if (
        not isinstance(diagnostic_options, dict)
        or set(diagnostic_options) != set(bindings)
        or not isinstance(diagnostic_distances, dict)
        or set(diagnostic_distances) != set(bindings)
    ):
        raise ValueError("Compiled Reference diagnostics omit canonical selected route evidence")

    source_to_regenerated: dict[str, str] = {}
    for logical_id, binding in bindings.items():
        row, application = tagged_rows[logical_id]
        expected_application = {
            "plan_fingerprint": plan.plan_fingerprint,
            "binding_fingerprint": binding.binding_fingerprint,
            "logical_connection_id": logical_id,
            "selected_candidate_id": binding.selected_candidate_id,
            "selected_route_role": binding.route_role,
            "routing_edge_ids": list(binding.routing_edge_ids),
            "reverse_routing_edge_ids": list(binding.reverse_routing_edge_ids),
            "geometry_fingerprint": binding.geometry_fingerprint,
        }
        for field, expected in expected_application.items():
            if application.get(field) != expected:
                raise ValueError(f"Compiled Reference application row is stale for {field}")
        expected_row_fields = {
            "place_id": binding.community_place_id,
            "parent_place_id": binding.parent_place_id,
            "root_spine_id": binding.root_spine_id,
            "community_attachment_node": binding.routing_start_node_id,
            "target_attachment_node": binding.routing_end_node_id,
            "topography_selected_role": binding.route_role,
        }
        for field, expected in expected_row_fields.items():
            if str(getattr(row, field)) != expected:
                raise ValueError(f"Compiled Reference application row is stale for {field}")
        try:
            alignment_options = json.loads(str(row.alignment_options))
        except json.JSONDecodeError as error:
            raise ValueError("Compiled Reference alignment options are not valid JSON") from error
        if not isinstance(alignment_options, list):
            raise ValueError("Compiled Reference alignment options are not a list")
        selected_options = [
            option
            for option in alignment_options
            if isinstance(option, dict) and option.get("selected") is True
        ]
        selected_candidate = selected_candidates.get(binding.selected_candidate_id)
        diagnostic_option = diagnostic_options[logical_id]
        diagnostic_distance = diagnostic_distances[logical_id]
        if selected_candidate is None:
            raise ValueError("Compiled Reference binding has no selected Scenario candidate")
        if (
            len(selected_options) != 1
            or not isinstance(diagnostic_option, dict)
            or set(diagnostic_option) != REFERENCE_SELECTED_ALIGNMENT_OPTION_FIELDS
            or selected_options[0] != diagnostic_option
            or application.get("selected_alignment_option") != diagnostic_option
            or row.distance_km != diagnostic_distance
            or application.get("published_distance_km") != diagnostic_distance
            or selected_options[0].get("role") != binding.route_role
            or selected_options[0].get("reverse_edge_ids") != list(binding.reverse_routing_edge_ids)
            or selected_options[0].get("length_km")
            != round(selected_candidate.directness_m / 1000, 3)
        ):
            raise ValueError("Compiled Reference selected alignment option is stale")
        geometry = row.geometry
        if (
            compiled.spine_access_connections.crs is None
            or not isinstance(geometry, LineString)
            or geometry.is_empty
            or not geometry.is_simple
        ):
            raise ValueError("Compiled Reference application geometry is empty or noncanonical")
        try:
            canonical_geometry = (
                gpd.GeoSeries(
                    [geometry],
                    crs=compiled.spine_access_connections.crs,
                )
                .to_crs(27700)
                .iloc[0]
            )
            geometry_fingerprint = CanonicalLineString(
                coordinates=tuple((float(x), float(y)) for x, y in canonical_geometry.coords)
            ).fingerprint
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Compiled Reference application geometry is not canonical linework"
            ) from error
        if geometry_fingerprint != binding.geometry_fingerprint:
            raise ValueError(
                "Compiled Reference application geometry differs from its selected route"
            )
        if binding.source_access_connection_id in source_to_regenerated:
            raise ValueError("Reference application plan duplicates a source access connection")
        source_to_regenerated[binding.source_access_connection_id] = str(row.access_connection_id)
    diagnostic_mapping = actual_diagnostics.get("source_to_regenerated_access_connection_ids")
    if (
        diagnostic_mapping != source_to_regenerated
        or set(source_to_regenerated.values()) != regenerated_ids
    ):
        raise ValueError("Compiled Reference diagnostics do not exactly map final regenerated rows")

    payload_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    options_json = json.dumps(
        _reference_option_collection(record),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return _ValidatedReferencePublication(
        record=record,
        payload_json=payload_json,
        options_json=options_json,
    )


def _validated_strategic_reference_publication(
    attached: StrategicReferencePublicationRecord | None,
    compiled: CompiledNetwork,
) -> _ValidatedStrategicReferencePublication | None:
    """Fail closed unless the strategic sibling and all replay frames agree.

    The publisher only verifies compiler-made bindings.  It never chooses an
    alternative, calls an agent, or turns a plan into authority by itself.
    """

    frames_present = any(
        not frame.empty
        for frame in (
            compiled.strategic_interurban_connections,
            compiled.strategic_destination_access_connections,
        )
    )
    if (attached is not None) != frames_present:
        raise ValueError("strategic publication record and replay frames must appear together")
    if attached is None:
        if compiled.strategic_reference_diagnostics:
            raise ValueError("strategic replay diagnostics require a publication record")
        return None
    record = StrategicReferencePublicationRecord.from_publication_payload(
        attached.publication_payload()
    )
    payload = record.publication_payload()
    expected_fields = {
        "area_definition_sha256": compiled.area_definition_sha256,
        "snapshot_manifest_sha256": compiled.snapshot_manifest_sha256,
        "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
        "governed_input_fingerprint": compiled.governed_input_fingerprint,
        "compilation_dependency_manifest": compiled.compilation_dependency_manifest,
        "decision_contract": compiled.decision_contract,
        "decision_ledger_input": compiled.decision_ledger_input,
        "accepted_decisions": compiled.accepted_decisions,
    }
    for name, expected in expected_fields.items():
        if payload.get(name) != expected:
            raise ValueError(f"strategic publication is not anchored to compiled {name}")
    diagnostics = payload["replay_diagnostics"]
    if diagnostics != compiled.strategic_reference_diagnostics:
        raise ValueError("strategic publication diagnostics are not compiler-bound")
    if payload.get("publication_created") or payload.get("agent_runtime_invoked"):
        raise ValueError("strategic publication record makes an impermissible authority claim")
    plan_payload = payload["application_plan"]
    bindings = plan_payload.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("strategic publication requires replay bindings")
    by_binding = {
        str(item.get("binding_fingerprint")): item for item in bindings if isinstance(item, dict)
    }
    if len(by_binding) != len(bindings):
        raise ValueError("strategic publication has duplicate binding identities")
    if set(diagnostics.get("consumed_binding_ids", ())) != set(by_binding):
        raise ValueError("strategic publication did not consume every binding exactly once")
    expected_roles = {
        "interurban-spine": StrategicReferenceApplicationDisposition.SELECTED_SUBSTITUTE.value,
        "strategic-destination-access": (
            StrategicReferenceApplicationDisposition.COMPLEMENTARY_REQUIRED.value
        ),
    }
    if any(
        expected_roles.get(str(item.get("unit_role"))) != item.get("application_disposition")
        for item in bindings
        if isinstance(item, dict)
    ):
        raise ValueError("strategic publication has unsupported role or disposition")
    rows = [
        *(row for _, row in compiled.strategic_interurban_connections.iterrows()),
        *(row for _, row in compiled.strategic_destination_access_connections.iterrows()),
    ]
    if len(rows) != len(by_binding):
        raise ValueError("strategic publication replay frame count is stale")
    seen: set[str] = set()
    for row in rows:
        binding_id = str(row.get("binding_id"))
        binding = by_binding.get(binding_id)
        if binding is None or binding_id in seen:
            raise ValueError("strategic publication replay frame has foreign or duplicate binding")
        seen.add(binding_id)
        role = str(row.get("network_role"))
        if binding.get("unit_role") != role or binding.get(
            "application_disposition"
        ) != expected_roles.get(role):
            raise ValueError("strategic publication replay disposition or role is stale")
        if _strategic_binding_identity(row) != _strategic_binding_identity(
            binding, application_plan=True
        ):
            raise ValueError("strategic publication replay binding identity differs")
        for field in (
            "routing_edge_ids",
            "reverse_routing_edge_ids",
            "source_ids",
            "evidence_ids",
            "generation_strategies",
        ):
            value = row.get(field)
            try:
                actual = json.loads(value) if isinstance(value, str) else list(value)
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError(f"strategic replay lineage is malformed for {field}") from error
            if actual != binding.get(field):
                raise ValueError(f"strategic publication replay lineage differs for {field}")
        if row.geometry is None or row.geometry.is_empty:
            raise ValueError("strategic publication replay geometry is missing")
        actual_geometry = _strategic_geometry_fingerprint(
            row.geometry,
            getattr(row.geometry, "crs", None),
            compiled.strategic_spines.crs,
        )
        if actual_geometry != binding.get("geometry_fingerprint"):
            raise ValueError("strategic publication replay geometry differs from plan binding")
        if role == "interurban-spine":
            endpoints = binding.get("endpoint_binding", {})
            if [row.get("from_network_place_id"), row.get("to_network_place_id")] != endpoints.get(
                "network_place_ids"
            ):
                raise ValueError("strategic interurban endpoints do not match the application plan")
        else:
            endpoints = binding.get("endpoint_binding", {})
            if [row.get("from_network_place_id")] != endpoints.get("network_place_ids") or [
                row.get("strategic_destination_id")
            ] != endpoints.get("strategic_destination_ids"):
                raise ValueError(
                    "strategic destination endpoints do not match the application plan"
                )
    if seen != set(by_binding):
        raise ValueError("strategic publication leaves a binding unmaterialised")
    replay_spine_bindings = {
        item
        for value in compiled.strategic_spines.get("replay_binding_ids", ())
        if isinstance(value, str)
        for item in json.loads(value)
    }
    interurban_bindings = {
        str(row.binding_id) for row in compiled.strategic_interurban_connections.itertuples()
    }
    if not interurban_bindings.issubset(replay_spine_bindings):
        raise ValueError("selected interurban replay is absent from effective strategic spines")
    return _ValidatedStrategicReferencePublication(
        record=record,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _strategic_geometry_fingerprint(
    geometry: object, geometry_crs: object, fallback_crs: object
) -> str:
    """Use the replay/application canonical projected line identity."""
    if geometry is None:
        raise ValueError("strategic geometry is missing")
    series = gpd.GeoSeries([geometry], crs=geometry_crs or fallback_crs).to_crs(27700)
    projected = series.iloc[0]
    if not isinstance(projected, LineString):
        raise ValueError("strategic geometry must be one LineString")
    return CanonicalLineString(
        coordinates=tuple((float(x), float(y)) for x, y in projected.coords)
    ).fingerprint


def _ea_fixed_point_candidate_path(config: AreaConfig) -> Path:
    """Return one containment-safe, deterministic candidate directory per Area Definition."""
    output_parent = config.publication.output_dir.parent.resolve()
    root = output_parent / EA_FIXED_POINT_CANDIDATE_DIRECTORY
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise ValueError("EA fixed-point candidate root must be a regular directory")
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    if resolved_root.parent != output_parent:
        raise ValueError("EA fixed-point candidate root escapes publication parent")
    area_key = hashlib.sha256(config.area_id.encode("utf-8")).hexdigest()
    candidate = resolved_root / area_key
    if candidate.parent != resolved_root:
        raise ValueError("EA fixed-point candidate path escapes candidate root")
    return candidate


def _uses_ea_lidar_weca_fixed_point(config: AreaConfig) -> bool:
    """Return whether this publication is governed by the EA fixed-point contract."""
    elevation = config.source.national_elevation
    return (
        elevation is not None
        and elevation.acquisition_contract == EA_LIDAR_WECA_ACQUISITION_CONTRACT
    )


def _ea_fixed_point_next_step(
    config: AreaConfig,
    candidate_network: Path,
    *,
    validation_network: Path,
    governed_input_fingerprint: str,
) -> dict[str, str]:
    """Advertise reacquisition only when the candidate matches the pinned WECA request."""
    elevation = config.source.national_elevation
    if elevation is None or elevation.path is None:
        return _ea_fixed_point_repin_required("candidate-has-no-local-elevation-output")
    snapshot = config.source.snapshot_dir / config.source.snapshot_id
    try:
        routes = gpd.read_file(validation_network)
        samples, _ = eligible_route_samples(routes, spacing_m=10.0)
        if not samples:
            raise ValueError("candidate has no eligible routes")
        actual_extent = (
            min(float(sample["geometry"].x) for sample in samples),
            min(float(sample["geometry"].y) for sample in samples),
            max(float(sample["geometry"].x) for sample in samples),
            max(float(sample["geometry"].y) for sample in samples),
        )
        if any(
            not math.isclose(actual, pinned, abs_tol=0.001)
            for actual, pinned in zip(actual_extent, WECA_PINNED_ELIGIBLE_ROUTE_BBOX, strict=True)
        ):
            return _ea_fixed_point_repin_required(
                "candidate-extent-or-request-differs-from-pinned-survey-index"
            )
        if governed_survey_request_bbox(routes, routing_buffer_m=WECA_ROUTING_BUFFER_M) != tuple(
            int(value) for value in WECA_SURVEY_REQUEST_BBOX
        ):
            return _ea_fixed_point_repin_required(
                "candidate-extent-or-request-differs-from-pinned-survey-index"
            )
    except (OSError, ValueError):
        return _ea_fixed_point_repin_required(
            "candidate-extent-or-request-differs-from-pinned-survey-index"
        )
    if not _is_sha256(governed_input_fingerprint):
        return _ea_fixed_point_repin_required(
            "candidate-current-governed-input-fingerprint-is-invalid"
        )
    try:
        replay_inputs = _validated_ea_snapshot_replay_inputs(snapshot)
    except ValueError as error:
        reason = (
            "legacy-snapshot-not-self-contained"
            if str(error) == "legacy EA fixed-point snapshot is not self-contained"
            else "candidate-snapshot-replay-inputs-invalid"
        )
        LOGGER.warning("EA fixed-point replay unavailable reason=%s detail=%s", reason, error)
        return _ea_fixed_point_repin_required(reason)
    authority_boundaries = replay_inputs["authority_boundaries"]
    survey_index = replay_inputs["survey_index"]
    sample_routes = replay_inputs["sample_routes"]
    cache_dir = elevation.path.parent / "ea-dtm-cache"
    command = [
        "uv",
        "run",
        "python",
        "scripts/acquire_ea_elevation.py",
        str(candidate_network),
        str(elevation.path),
        "--cache-dir",
        str(cache_dir),
        "--spacing-m",
        "10",
        "--authority-boundaries",
        str(authority_boundaries),
        "--survey-index",
        str(survey_index),
        "--weca-preflight",
        "--routing-buffer-m",
        "15000",
        "--governed-input-fingerprint",
        governed_input_fingerprint,
        "--supplemental-routes",
        str(sample_routes),
    ]
    return {
        "next_step_status": "ea-acquisition-ready",
        "next_step_command": " ".join(shlex.quote(part) for part in command),
    }


def _ea_fixed_point_repin_required(reason: str) -> dict[str, str]:
    """Return a machine-readable refusal to reuse a stale pinned survey request."""
    return {
        "next_step_status": "survey-index-repin-required",
        "next_step_reason": reason,
    }


def _is_sha256(value: object) -> bool:
    """Return whether value is a canonical SHA-256 hex digest."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def retain_ea_recovery_candidate(
    config: AreaConfig,
    compiled: CompiledNetwork,
    run_id: str,
) -> dict[str, Path]:
    """Retain one fixed-point mismatch candidate without any publication path."""

    candidate = _ea_fixed_point_candidate_path(config)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{candidate.name}-recovery-", dir=candidate.parent)
    )
    try:
        network_path = temporary / EA_FIXED_POINT_CANDIDATE_NETWORK
        _write_geojson(network_path, compiled)
        try:
            _validate_ea_elevation_fixed_point(config, network_path)
        except EAFixedPointMismatchError as error:
            retained = _retain_ea_fixed_point_candidate(
                config,
                run_id=run_id,
                network_path=network_path,
                expected=error.expected,
                actual=error.actual,
                governed_input_fingerprint=compiled.governed_input_fingerprint,
            )
            return {"candidate": retained}
        raise ValueError(
            "EA recovery candidate unexpectedly matches its invalid parent; "
            "refusing invalid-parent publication"
        )
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _retain_ea_fixed_point_candidate(
    config: AreaConfig,
    *,
    run_id: str,
    network_path: Path,
    expected: str,
    actual: str,
    governed_input_fingerprint: str,
) -> Path:
    """Atomically replace the sole retained candidate after an EA route mismatch."""
    candidate = _ea_fixed_point_candidate_path(config)
    root = candidate.parent
    if candidate.exists() and (not candidate.is_dir() or candidate.is_symlink()):
        raise ValueError("EA fixed-point candidate path must be a regular directory")
    temporary = Path(tempfile.mkdtemp(prefix=f".{candidate.name}-", dir=root))
    try:
        retained_network = temporary / EA_FIXED_POINT_CANDIDATE_NETWORK
        candidate_bytes = network_path.read_bytes()
        retained_network.write_bytes(candidate_bytes)
        candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
        status = {
            "schema_version": EA_FIXED_POINT_CANDIDATE_SCHEMA_VERSION,
            "status": "eligible-route-mismatch",
            "timestamp": datetime.now(UTC).isoformat(),
            "area_id": config.area_id,
            "snapshot_id": config.source.snapshot_id,
            "run_id": run_id,
            "expected_eligible_route_fingerprint": expected,
            "actual_eligible_route_fingerprint": actual,
            "governed_input_fingerprint": governed_input_fingerprint,
            "candidate_network_path": EA_FIXED_POINT_CANDIDATE_NETWORK,
            "candidate_network_sha256": candidate_digest,
            **_ea_fixed_point_next_step(
                config,
                candidate / retained_network.name,
                validation_network=retained_network,
                governed_input_fingerprint=governed_input_fingerprint,
            ),
        }
        (temporary / EA_FIXED_POINT_CANDIDATE_STATUS).write_text(
            json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        backup = candidate.with_name(f".{candidate.name}-previous")
        if backup.exists():
            if not backup.is_dir() or backup.is_symlink():
                raise ValueError("EA fixed-point candidate backup must be a regular directory")
            shutil.rmtree(backup)
        if candidate.exists():
            candidate.replace(backup)
        try:
            temporary.replace(candidate)
        except Exception:
            if backup.exists() and not candidate.exists():
                backup.replace(candidate)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    LOGGER.info(
        "EA fixed-point candidate retained path=%s expected=%s actual=%s",
        candidate,
        expected,
        actual,
    )
    return candidate


def _remove_ea_fixed_point_candidate(config: AreaConfig) -> None:
    """Discard an obsolete candidate only after a complete atomic publication succeeds."""
    root = config.publication.output_dir.parent.resolve() / EA_FIXED_POINT_CANDIDATE_DIRECTORY
    if not root.exists():
        return
    candidate = _ea_fixed_point_candidate_path(config)
    if not candidate.exists():
        return
    if not candidate.is_dir() or candidate.is_symlink():
        raise ValueError("EA fixed-point candidate path must be a regular directory")
    shutil.rmtree(candidate)
    LOGGER.info("EA fixed-point candidate cleared after successful publication path=%s", candidate)


def _validate_ea_elevation_fixed_point(config: AreaConfig, network_path: Path) -> None:
    """Fail publication if retained EA snapshot provenance and final routes diverge."""
    elevation = config.source.national_elevation
    if elevation is None or elevation.acquisition_contract != EA_LIDAR_WECA_ACQUISITION_CONTRACT:
        return
    snapshot_manifest = config.source.snapshot_dir / config.source.snapshot_id / "snapshot.json"
    if not snapshot_manifest.exists():
        raise ValueError("EA elevation fixed-point validation is missing immutable snapshot.json")
    try:
        manifest = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError("snapshot manifest must be an object")
        evidence_sources = manifest["evidence_sources"]
        if not isinstance(evidence_sources, dict):
            raise TypeError("snapshot evidence_sources must be an object")
        elevation_provenance = evidence_sources["elevation"]
        if not isinstance(elevation_provenance, dict):
            raise TypeError("snapshot elevation provenance must be an object")
        if elevation_provenance["acquisition_protocol"] != "two-pass-fixed-point/v1":
            raise ValueError("EA elevation snapshot lacks the two-pass fixed-point protocol")

        def required_digest(value: object, label: str) -> str:
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"EA elevation snapshot has invalid {label}")
            try:
                int(value, 16)
            except ValueError as error:
                raise ValueError(f"EA elevation snapshot has invalid {label}") from error
            return value

        expected = required_digest(
            elevation_provenance["pre_elevation_network_sha256"],
            "pre-elevation route fingerprint",
        )
        acquisition_output = required_digest(
            elevation_provenance["acquisition_output_sha256"],
            "acquisition elevation output provenance",
        )
        expected_files = {
            ELEVATION_EVIDENCE_FILENAME: required_digest(
                elevation_provenance["content_fingerprint"], "elevation evidence provenance"
            ),
            SAMPLE_LEDGER_FILENAME: required_digest(
                elevation_provenance["sample_ledger_sha256"], "sample-ledger provenance"
            ),
            "elevation-evidence.manifest.json": required_digest(
                elevation_provenance["ea_acquisition_manifest_sha256"],
                "EA acquisition provenance",
            ),
            EA_RETAINED_ROUTE_FILENAME: None,
        }
        provenance_files = manifest["provenance_file_sha256"]
        if not isinstance(provenance_files, dict):
            raise TypeError("snapshot provenance_file_sha256 must be an object")
        for filename, expected_digest in expected_files.items():
            recorded_digest = required_digest(provenance_files[filename], f"{filename} provenance")
            if expected_digest is not None and recorded_digest != expected_digest:
                raise ValueError(f"EA elevation snapshot has mismatched {filename} provenance")
            retained = snapshot_manifest.parent / filename
            if not retained.is_file() or retained.is_symlink():
                raise ValueError(f"EA elevation snapshot is missing retained {filename}")
            actual_digest = hashlib.sha256(retained.read_bytes()).hexdigest()
            if actual_digest != recorded_digest:
                raise ValueError(f"EA elevation snapshot has unreadable or tampered {filename}")

        retained_acquisition = snapshot_manifest.parent / "elevation-evidence.manifest.json"
        try:
            acquisition = json.loads(retained_acquisition.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(
                "EA elevation snapshot has invalid retained acquisition manifest"
            ) from error
        if not isinstance(acquisition, dict):
            raise ValueError(
                "EA elevation snapshot retained acquisition manifest must be an object"
            )

        def required_equal(field: str, expected_value: object, label: str) -> None:
            actual_value = acquisition.get(field)
            if actual_value != expected_value:
                raise ValueError(
                    f"EA elevation snapshot retained acquisition manifest mismatches {label}"
                )

        # The snapshot is the publication authority, but the retained
        # acquisition statement independently witnesses the exact two-pass
        # input and each retained proof.  Do not let a self-consistent set of
        # snapshot file hashes detach it from that statement.
        required_equal(
            "acquisition_protocol",
            elevation_provenance["acquisition_protocol"],
            "acquisition protocol",
        )
        required_equal(
            "pre_elevation_network_sha256",
            expected,
            "pre-elevation route fingerprint",
        )
        required_equal(
            "output_sha256",
            acquisition_output,
            "acquisition elevation output digest",
        )
        required_equal(
            "sample_ledger_sha256",
            provenance_files[SAMPLE_LEDGER_FILENAME],
            "sample-ledger digest",
        )
        required_equal(
            "sample_route_sha256",
            provenance_files[EA_RETAINED_ROUTE_FILENAME],
            "sampled-route digest",
        )
        required_equal("sample_ledger_path", SAMPLE_LEDGER_FILENAME, "sample-ledger path")
        required_equal("sample_route_path", EA_RETAINED_ROUTE_FILENAME, "sampled-route path")
        # Hashes prove transport integrity but cannot by themselves prevent a
        # self-resealed manifest.  Reconstruct the one governed GeoJSON form
        # and bind its provenance-bearing metadata to both the configuration
        # and independently retained acquisition statement.
        _validate_canonical_retained_ea_evidence(
            snapshot_manifest.parent / ELEVATION_EVIDENCE_FILENAME,
            elevation,
            acquisition,
        )
    except (json.JSONDecodeError, KeyError, OSError, TypeError) as error:
        raise ValueError(
            "EA elevation fixed-point validation cannot read immutable snapshot provenance"
        ) from error
    try:
        actual = eligible_route_fingerprint(gpd.read_file(network_path))
    except (OSError, ValueError) as error:
        raise ValueError("EA elevation fixed-point validation cannot read final routes") from error
    if expected != actual:
        raise EAFixedPointMismatchError(expected=expected, actual=actual)


def _metadata_frame(crs: object) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"schema_version": SCHEMA_VERSION, "disclaimer": DISCLAIMER, "geometry": None}],
        geometry="geometry",
        crs=crs,
    )


def _write_geopackage(path: Path, compiled: CompiledNetwork) -> None:
    if not compiled.boundary.empty:
        _geopackage_safe(compiled.boundary).to_file(
            path, layer="authority_boundaries", driver="GPKG"
        )
    compiled.places.to_file(path, layer="places", driver="GPKG")
    if not compiled.strategic_spines.empty:
        compiled.strategic_spines.to_file(path, layer="strategic_spines", driver="GPKG")
    if not compiled.access_obligations.empty:
        compiled.access_obligations.to_file(path, layer="access_obligations", driver="GPKG")
    if not compiled.school_street_assessments.empty:
        compiled.school_street_assessments.to_file(
            path, layer="school_street_assessments", driver="GPKG"
        )
    if not compiled.topography_profiles.empty:
        compiled.topography_profiles.to_file(path, layer="topography_profiles", driver="GPKG")
    if not compiled.gradient_sections.empty:
        compiled.gradient_sections.to_file(path, layer="gradient_sections", driver="GPKG")
    if not compiled.population_display_sections.empty:
        _population_display_geopackage(compiled.population_display_sections).to_file(
            path, layer="population_display_sections", driver="GPKG"
        )
    if not compiled.elevation_corroboration.empty:
        compiled.elevation_corroboration.to_file(
            path, layer="elevation_corroboration", driver="GPKG"
        )
    if not compiled.spine_access_connections.empty:
        compiled.spine_access_connections.to_file(
            path, layer="spine_access_connections", driver="GPKG"
        )
    if not compiled.spine_access_branches.empty:
        compiled.spine_access_branches.to_file(path, layer="spine_access_branches", driver="GPKG")
    if not compiled.branch_meeting_connections.empty:
        compiled.branch_meeting_connections.to_file(
            path, layer="branch_meeting_connections", driver="GPKG"
        )
    if not compiled.cross_spine_connectors.empty:
        compiled.cross_spine_connectors.to_file(path, layer="cross_spine_connectors", driver="GPKG")
    if not compiled.gaps.empty:
        compiled.gaps.to_file(path, layer="gaps", driver="GPKG")
    if not compiled.urban_spines.empty:
        compiled.urban_spines.to_file(path, layer="urban_spines", driver="GPKG")
    if not compiled.urban_classification_unknowns.empty:
        compiled.urban_classification_unknowns.to_file(
            path, layer="urban_classification_unknowns", driver="GPKG"
        )
    if not compiled.low_traffic_areas.empty:
        compiled.low_traffic_areas.to_file(path, layer="candidate_low_traffic_areas", driver="GPKG")
    if not compiled.low_traffic_area_portals.empty:
        compiled.low_traffic_area_portals.to_file(
            path, layer="low_traffic_area_portals", driver="GPKG"
        )
    if not compiled.crossing_warnings.empty:
        compiled.crossing_warnings.to_file(path, layer="crossing_warnings", driver="GPKG")
    # The selected interurban route is already the effective strategic-spine
    # geometry.  Only the distinct complementary destination frame receives a
    # public layer, preventing a duplicate line/end-point interpretation.
    if not compiled.strategic_destination_access_connections.empty:
        compiled.strategic_destination_access_connections.to_file(
            path, layer="strategic_destination_access_connections", driver="GPKG"
        )
    for layer_name, frame in (
        ("a_road_spines", compiled.a_road_spines),
        ("ncn_routes", compiled.ncn_routes),
        ("schools", compiled.schools),
        ("retail_centres", compiled.retail_centres),
        ("healthcare", compiled.healthcare),
    ):
        if not frame.empty:
            _geopackage_safe(frame).to_file(path, layer=layer_name, driver="GPKG")
    if compiled.atm_reference is not None:
        _geopackage_safe(compiled.atm_reference).to_file(path, layer="atm_reference", driver="GPKG")
    _metadata_frame(compiled.places.crs).to_file(path, layer="metadata", driver="GPKG")


def _geopackage_safe(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Preserve source attributes without colliding with GeoPackage internals."""
    renamed: dict[str, str] = {}
    occupied = set(frame.columns)
    for column in frame.columns:
        if column.lower() != "fid":
            continue
        candidate = "source_fid"
        suffix = 2
        while candidate in occupied:
            candidate = f"source_fid_{suffix}"
            suffix += 1
        renamed[column] = candidate
        occupied.add(candidate)
    return frame.rename(columns=renamed)


def _population_display_geopackage(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Store captured OA IDs as canonical JSON, not a driver-specific list repr."""
    safe = _geopackage_safe(frame).copy()
    for field in ("captured_oa_ids", "captured_output_areas"):
        if field not in safe:
            continue
        safe[field] = safe[field].map(
            lambda value: json.dumps(_json_value(value), separators=(",", ":"))
        )
    return safe


def _features(frame: gpd.GeoDataFrame, feature_type: str) -> list[dict[str, object]]:
    return [
        {
            "type": "Feature",
            "id": _feature_id(row, feature_type),
            "properties": {
                key: _json_value(value)
                for key, value in row.items()
                if key != "geometry" and _json_value(value) is not None
            }
            | {"feature_type": feature_type},
            "geometry": mapping(row.geometry) if row.geometry is not None else None,
        }
        for _, row in frame.to_crs(4326).iterrows()
    ]


def _features_preserving_type(frame: gpd.GeoDataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    return [
        feature
        for feature_type, typed_frame in frame.groupby("feature_type", sort=True)
        for feature in _features(typed_frame, str(feature_type))
    ]


def _feature_id(row: pd.Series, feature_type: str | None = None) -> str:
    preferred = {
        "access-obligation": "obligation_id",
        "school-access-obligation": "obligation_id",
        "school-street-assessment": "assessment_id",
        "topography-profile": "profile_id",
        "gradient-section": "section_id",
        "population-display-section": "section_id",
        "elevation-corroboration": "corroboration_id",
        "spine-access-connection": "access_connection_id",
        "school-access-connection": "access_connection_id",
        "spine-access-branch": "branch_id",
        "branch-meeting-connection": "meeting_connection_id",
        "cross-spine-connector": "cross_spine_connector_id",
        "low-traffic-area-portal": "portal_id",
        "strategic-spine": "spine_id",
        "strategic-destination-access-connection": "strategic_connection_id",
        "authority-boundary": "boundary_id",
        "gap": "connection_id",
        "school-access-gap": "connection_id",
    }.get(feature_type)
    if preferred:
        value = _json_value(row.get(preferred))
        if value is not None:
            return str(value)
    for key in (
        "connection_id",
        "obligation_id",
        "access_connection_id",
        "branch_id",
        "meeting_connection_id",
        "cross_spine_connector_id",
        "strategic_connection_id",
        "spine_id",
        "place_id",
        "structure_id",
        "warning_id",
        "portal_feature_id",
        "evidence_id",
        "id",
        "fid",
    ):
        value = _json_value(row.get(key))
        if value is not None:
            return str(value)
    digest = hashlib.sha256(row.geometry.wkb).hexdigest()[:12]
    return f"feature-{digest}"


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or (not isinstance(value, str) and bool(pd.isna(value))):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _artifact_values_equal(left: object, right: object) -> bool:
    if left is None:
        return right is None or (not isinstance(right, str) and bool(pd.isna(right)))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    return left == right


def _network_collection(compiled: CompiledNetwork) -> dict[str, object]:
    community_obligations = compiled.access_obligations[
        compiled.access_obligations["obligation_kind"] == "community"
    ]
    school_obligations = compiled.access_obligations[
        compiled.access_obligations["obligation_kind"] == "school"
    ]
    school_connections = compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] == "school"
    ]
    other_access_connections = compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] != "school"
    ]
    gap_roles = compiled.gaps.get(
        "network_role", pd.Series("", index=compiled.gaps.index, dtype=object)
    )
    school_gaps = compiled.gaps[gap_roles == "school-access-gap"]
    other_gaps = compiled.gaps[gap_roles != "school-access-gap"]
    return {
        "type": "FeatureCollection",
        "name": "SATN compiled network",
        "disclaimer": DISCLAIMER,
        "urban_classification_status": compiled.urban_classification_status,
        "elevation_evidence_status": compiled.elevation_evidence_status,
        "features": (
            _features(compiled.boundary, "authority-boundary")
            + _features(compiled.strategic_spines, "strategic-spine")
            + (
                _features(
                    compiled.strategic_destination_access_connections,
                    "strategic-destination-access-connection",
                )
                if not compiled.strategic_destination_access_connections.empty
                else []
            )
            + _features(community_obligations, "access-obligation")
            + _features(school_obligations, "school-access-obligation")
            + _features(other_access_connections, "spine-access-connection")
            + _features(school_connections, "school-access-connection")
            + _features(compiled.spine_access_branches, "spine-access-branch")
            + _features(compiled.branch_meeting_connections, "branch-meeting-connection")
            + _features(compiled.cross_spine_connectors, "cross-spine-connector")
            + _features(other_gaps, "gap")
            + _features(school_gaps, "school-access-gap")
            + _features(compiled.urban_spines, "urban-spine")
            + _features(
                compiled.urban_classification_unknowns,
                "urban-classification-unknown",
            )
            + _features(compiled.low_traffic_areas, "low-traffic-area")
            + _features(compiled.low_traffic_area_portals, "low-traffic-area-portal")
            + _features(compiled.crossing_warnings, "crossing-warning")
            + _features(compiled.a_road_spines, "a-road-spine")
            + _features_preserving_type(compiled.ncn_routes)
            + _features(compiled.schools, "school")
            + _features(
                compiled.school_street_assessments,
                "school-street-assessment",
            )
            + _features(compiled.topography_profiles, "topography-profile")
            + _features(compiled.gradient_sections, "gradient-section")
            + _features(
                compiled.population_display_sections,
                "population-display-section",
            )
            + _features(
                compiled.elevation_corroboration,
                "elevation-corroboration",
            )
            + _features(compiled.retail_centres, "retail-centre")
            + _features(compiled.healthcare, "healthcare")
            + (
                _features(compiled.atm_reference, "atm-reference")
                if compiled.atm_reference is not None
                else []
            )
        ),
    }


def _write_geojson(path: Path, compiled: CompiledNetwork) -> None:
    path.write_text(json.dumps(_network_collection(compiled), indent=2), encoding="utf-8")


def _write_asset_accounting(
    json_path: Path,
    geojson_path: Path,
    compiled: CompiledNetwork,
) -> None:
    """Write the exhaustive reusable-asset accounting and spatial sibling."""
    accounting = compiled.asset_accounting
    if not accounting or not isinstance(accounting.get("records"), list):
        raise ValueError("compiled network has no valid asset accounting")
    json_path.write_text(json.dumps(accounting, indent=2), encoding="utf-8")
    geojson_path.write_text(
        json.dumps(accounting_geojson(accounting), indent=2),
        encoding="utf-8",
    )


def _layer_counts(compiled: CompiledNetwork) -> dict[str, int]:
    return {
        "strategic_spines": len(compiled.strategic_spines),
        **(
            {
                "strategic_destination_access_connections": len(
                    compiled.strategic_destination_access_connections
                ),
            }
            if compiled.strategic_reference_publication is not None
            else {}
        ),
        "access_obligations": len(compiled.access_obligations),
        "school_access_obligations": int(
            (compiled.access_obligations["obligation_kind"] == "school").sum()
        ),
        "gaps": len(compiled.gaps),
        "spine_access_connections": len(compiled.spine_access_connections),
        "spine_access_branches": len(compiled.spine_access_branches),
        "branch_meeting_connections": len(compiled.branch_meeting_connections),
        "cross_spine_connectors": len(compiled.cross_spine_connectors),
        "a_road_spines": len(compiled.a_road_spines),
        "ncn_routes": len(compiled.ncn_routes),
        "urban_spines": len(compiled.urban_spines),
        "urban_classification_unknowns": len(compiled.urban_classification_unknowns),
        "candidate_low_traffic_areas": len(compiled.low_traffic_areas),
        "low_traffic_area_portals": len(compiled.low_traffic_area_portals),
        "schools": len(compiled.schools),
        "school_street_assessments": len(compiled.school_street_assessments),
        "topography_profiles": len(compiled.topography_profiles),
        "gradient_sections": len(compiled.gradient_sections),
        "population_display_sections": len(compiled.population_display_sections),
        "elevation_corroboration": len(compiled.elevation_corroboration),
        "retail_centres": len(compiled.retail_centres),
        "healthcare": len(compiled.healthcare),
    }


def _write_json_records(
    output: Path,
    config: AreaConfig,
    compiled: CompiledNetwork,
    run_id: str,
    reference_publication: _ValidatedReferencePublication | None = None,
    strategic_reference_publication: _ValidatedStrategicReferencePublication | None = None,
) -> None:
    if compiled.reference_satn_publication is not None and reference_publication is None:
        raise ValueError(
            "Reference JSON serialization requires compiler-bound publication evidence"
        )
    if (
        compiled.strategic_reference_publication is not None
        and strategic_reference_publication is None
    ):
        raise ValueError("strategic Reference JSON serialization requires compiler-bound evidence")
    topography_comparisons = pd.concat(
        [compiled.spine_access_connections, compiled.branch_meeting_connections],
        ignore_index=True,
        sort=False,
    )
    review_records = [*compiled.agent_records, *compiled.divergence_records]
    runtime_governance = classify_runtime_governance(
        config.compilation.agent,
        review_records,
        decision_ledger_input=compiled.decision_ledger_input,
        accepted_decisions=compiled.accepted_decisions,
    )
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        # Historical artifact name retained for compatibility; the value is the
        # canonical Area Definition identity.
        "council_id": config.area_id,
        "status": compiled.status,
        "criteria": {
            section: {criterion: status.value for criterion, status in values.items()}
            for section, values in compiled.criteria.items()
        },
        "network_model": "backbone-outward",
        "authoritative_features": _authoritative_feature_records(compiled),
        "agent_review": _agent_review_summary(config, review_records),
        "runtime_governance": runtime_governance,
        "decision_contract": compiled.decision_contract,
        "decision_ledger_input": compiled.decision_ledger_input,
        "accepted_decisions": compiled.accepted_decisions,
        "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
        "governed_input_fingerprint": compiled.governed_input_fingerprint,
        "snapshot_manifest_sha256": compiled.snapshot_manifest_sha256,
        "area_definition_sha256": compiled.area_definition_sha256,
        "compilation_dependency_manifest": compiled.compilation_dependency_manifest,
        "compilation_diagnostics": compiled.compilation_diagnostics,
        "connection_count": compiled.connection_count,
        "gap_count": len(compiled.gaps),
        "crossing_warning_count": len(compiled.crossing_warnings),
        "urban_classification_status": compiled.urban_classification_status,
        "elevation_evidence_status": compiled.elevation_evidence_status,
        "topography": {
            "profile_count": len(compiled.topography_profiles),
            "gradient_section_count": len(compiled.gradient_sections),
            "evidence_unavailable_count": int(
                (compiled.topography_profiles["evidence_status"] == "evidence-unavailable").sum()
            ),
            "corroboration_count": len(compiled.elevation_corroboration),
            "alternative_trigger_count": int(
                topography_comparisons.get(
                    "topography_alternative_trigger",
                    pd.Series(False, index=topography_comparisons.index),
                ).sum()
            ),
            "easier_alternative_selected_count": int(
                (
                    topography_comparisons.get(
                        "topography_comparison_status",
                        pd.Series("", index=topography_comparisons.index),
                    )
                    == "easier-alternative-selected"
                ).sum()
            ),
            "original_retained_count": int(
                topography_comparisons.get(
                    "topography_comparison_status",
                    pd.Series("", index=topography_comparisons.index),
                )
                .isin(
                    [
                        "original-retained-no-easier-option",
                        "strategic-spine-retained",
                    ]
                )
                .sum()
            ),
        },
        "layer_counts": _layer_counts(compiled),
        "asset_accounting": {
            "contract": compiled.asset_accounting.get("contract"),
            "asset_count": compiled.asset_accounting.get("asset_count", 0),
            "excluded_observation_count": len(
                compiled.asset_accounting.get("excluded_observations", [])
            ),
        },
        "network_units": compiled.network_units,
        "superseded_hypotheses": compiled.superseded_hypotheses,
        "atm_mode": config.atm.mode if config.atm.enabled else "disabled",
        "atm_geometry_included": compiled.atm_reference is not None,
        "disclaimer": DISCLAIMER,
    }
    if reference_publication is not None:
        run["reference_satn"] = reference_publication.payload()
    if strategic_reference_publication is not None:
        run["strategic_reference"] = _strategic_publication_view(
            compiled, strategic_reference_publication
        )
    (output / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    records = {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "records": [record.model_dump(mode="json") for record in compiled.agent_records],
    }
    (output / "agent-records.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    divergences = {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "records": [record.model_dump(mode="json") for record in compiled.divergence_records],
    }
    (output / "divergence-records.json").write_text(
        json.dumps(divergences, indent=2), encoding="utf-8"
    )
    intervention_requests = {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "records": [
            request.model_dump(mode="json") for request in compiled.human_intervention_requests
        ],
    }
    (output / "human-intervention-requests.json").write_text(
        json.dumps(intervention_requests, indent=2), encoding="utf-8"
    )


def _agent_review_summary(
    config: AreaConfig,
    records: list[AgentRecord | DivergenceRecord],
) -> dict[str, object]:
    return {
        "statuses": [status.value for status in config.compilation.agent.review_statuses],
        "reviewed_decisions": sum(record.review_required for record in records),
        "skipped_decisions": sum(not record.review_required for record in records),
        "decisions_by_status": {
            status.value: {
                "reviewed": sum(
                    record.governing_status == status and record.review_required
                    for record in records
                ),
                "skipped": sum(
                    record.governing_status == status and not record.review_required
                    for record in records
                ),
            }
            for status in TrafficLight
        },
    }


def _authoritative_feature_records(
    compiled: CompiledNetwork,
) -> list[dict[str, str]]:
    records = (
        [
            {
                "feature_id": str(row.access_connection_id),
                "network_role": str(row.network_role),
            }
            for row in compiled.spine_access_connections.itertuples()
        ]
        + [
            {
                "feature_id": str(row.meeting_connection_id),
                "network_role": str(row.network_role),
            }
            for row in compiled.branch_meeting_connections.itertuples()
        ]
        + [
            {
                "feature_id": str(row.cross_spine_connector_id),
                "network_role": str(row.network_role),
            }
            for row in compiled.cross_spine_connectors.itertuples()
        ]
    )
    return sorted(records, key=lambda record: record["feature_id"])


def _strategic_authoritative_feature_records(
    compiled: CompiledNetwork,
) -> list[dict[str, str]]:
    """Strategic registry kept separate from legacy authoritative features."""

    records = []
    for row in compiled.strategic_interurban_connections.itertuples():
        matching = compiled.strategic_spines[
            compiled.strategic_spines["replay_binding_ids"]
            .fillna("")
            .map(
                lambda value, binding_id=str(row.binding_id): (
                    binding_id in json.loads(value)
                    if isinstance(value, str) and value.strip()
                    else False
                )
            )
        ]
        if len(matching) != 1:
            raise ValueError("strategic interurban binding has no unique published spine")
        spine = matching.iloc[0]
        records.append(
            {
                "feature_id": str(spine["spine_id"]),
                "binding_id": str(row.binding_id),
                "candidate_id": str(row.candidate_id),
                "physical_alignment_id": str(row.physical_alignment_id),
                "geometry_fingerprint": str(row.geometry_fingerprint),
                "network_role": str(row.network_role),
                "published_as": "strategic-spine",
            }
        )
    records += [
        {
            "feature_id": str(row.strategic_connection_id),
            "binding_id": str(row.binding_id),
            "candidate_id": str(row.candidate_id),
            "physical_alignment_id": str(row.physical_alignment_id),
            "geometry_fingerprint": str(row.geometry_fingerprint),
            "network_role": str(row.network_role),
            "published_as": "strategic-destination-access-connection",
        }
        for row in compiled.strategic_destination_access_connections.itertuples()
    ]
    return sorted(records, key=lambda record: record["feature_id"])


def _strategic_replay_features(
    frame: gpd.GeoDataFrame, feature_type: str, bindings: dict[str, dict[str, object]]
) -> dict[str, object]:
    features = _features(frame, feature_type)
    for feature in features:
        binding = bindings.get(str(feature["properties"].get("binding_id")))
        if binding is None:
            raise ValueError("strategic replay feature has no canonical plan binding")
        for name in (
            "routing_edge_ids",
            "reverse_routing_edge_ids",
            "source_ids",
            "evidence_ids",
            "generation_strategies",
        ):
            value = feature["properties"].get(name)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, list):
                    feature["properties"][name] = parsed
        for name in (
            "mandatory_network_place_ids",
            "mandatory_access_obligation_ids",
            "mandatory_strategic_destination_ids",
            "served_network_place_ids",
            "served_access_obligation_ids",
            "served_strategic_destination_ids",
        ):
            feature["properties"][name] = binding.get(name, [])
        feature["properties"]["endpoint_binding"] = binding.get("endpoint_binding", {})
    return {"type": "FeatureCollection", "features": features}


def _strategic_publication_view(
    compiled: CompiledNetwork, validated: _ValidatedStrategicReferencePublication
) -> dict[str, object]:
    plan = validated.payload()["application_plan"]
    bindings = {str(item["binding_fingerprint"]): item for item in plan["bindings"]}
    view = {
        "record": validated.payload(),
        "replay": {
            "diagnostics": compiled.strategic_reference_diagnostics,
            "interurban_connections": _strategic_replay_features(
                compiled.strategic_interurban_connections,
                "strategic-interurban-connection",
                bindings,
            ),
            "destination_access_connections": _strategic_replay_features(
                compiled.strategic_destination_access_connections,
                "strategic-destination-access-connection",
                bindings,
            ),
        },
        "alignment_options": _strategic_alignment_options(plan),
        "authoritative_features": _strategic_authoritative_feature_records(compiled),
    }
    view["cross_artifact_integrity"] = _strategic_integrity_report(view)
    return view


def _strategic_integrity_report(view: dict[str, object]) -> dict[str, object]:
    """Deterministic local structural identity; never a signature or authority claim."""
    replay = view["replay"]
    assert isinstance(replay, dict)
    payload = {
        "contract": "satn-strategic-publication-integrity/v1",
        "publication_record_fingerprint": view["record"]["record_fingerprint"],
        "binding_count": sum(
            len(replay[name]["features"])
            for name in ("interurban_connections", "destination_access_connections")
        ),
        "interurban_count": len(replay["interurban_connections"]["features"]),
        "destination_count": len(replay["destination_access_connections"]["features"]),
        "registry_count": len(view["authoritative_features"]),
        "alignment_option_count": len(view["alignment_options"]["features"]),
    }
    return {**payload, "report_fingerprint": content_fingerprint(payload)}


def _strategic_alignment_options(plan: dict[str, object]) -> dict[str, object]:
    """Expose governed selected and rejected Scenario options, never as network edges."""
    reference = plan.get("reference", {})
    scenario = reference.get("scenario", {}) if isinstance(reference, dict) else {}
    selected = {
        str(binding.get("selected_candidate_id")): str(binding.get("application_disposition"))
        for binding in plan.get("bindings", [])
        if isinstance(binding, dict)
    }
    features = []
    for candidate_set in scenario.get("candidate_sets", []) if isinstance(scenario, dict) else []:
        if not isinstance(candidate_set, dict):
            continue
        for candidate in candidate_set.get("candidates", []):
            if not isinstance(candidate, dict) or not isinstance(candidate.get("geometry"), dict):
                continue
            identifier = str(candidate.get("candidate_id"))
            canonical = candidate["geometry"]
            coordinates = canonical.get("coordinates") if isinstance(canonical, dict) else None
            if not isinstance(coordinates, list):
                continue
            projected = gpd.GeoSeries([LineString(coordinates)], crs=27700).to_crs(4326).iloc[0]
            features.append(
                {
                    "type": "Feature",
                    "id": identifier,
                    "properties": {
                        "candidate_id": identifier,
                        "candidate_set_id": candidate_set.get("candidate_set_id"),
                        "network_role": candidate_set.get("network_role"),
                        "disposition": {
                            "selected-substitute": "selected",
                            "complementary-required": "complementary",
                        }.get(selected.get(identifier), "rejected"),
                        "default_visibility": "hidden",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(point) for point in projected.coords],
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _write_backbone_comparison(
    path: Path,
    compiled: CompiledNetwork,
    previous_reference: Path,
) -> None:
    """Compare the current model with any superseded publication, never as truth."""
    current_lines = [
        *compiled.spine_access_connections.geometry,
        *compiled.branch_meeting_connections.geometry,
    ]
    current_length_m = _linework_length_m(current_lines, compiled.places.crs)
    previous_path = (
        previous_reference / "network.geojson"
        if previous_reference.is_dir()
        else previous_reference
    )
    previous_features: list[dict[str, object]] = []
    previous_gaps: list[dict[str, object]] = []
    previous_model = "unavailable"
    previous_connection_count = 0
    previous_gap_count = 0
    previous_length_m = 0.0
    previous_topology: dict[str, int] = _topology_metrics([])
    previous_role_counts: dict[str, int] = {}
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        if "features" not in previous and previous.get("comparison_role") == (
            "superseded-reference-not-ground-truth"
        ):
            previous_model = str(previous["network_model"])
            previous_connection_count = int(previous["connection_count"])
            previous_gap_count = int(previous["network_gap_count"])
            previous_length_m = float(previous["linework_length_m"])
            previous_topology = {key: int(value) for key, value in previous["topology"].items()}
            previous_role_counts = {
                str(key): int(value) for key, value in previous["feature_role_counts"].items()
            }
        else:
            previous_types = {
                feature.get("properties", {}).get("feature_type")
                for feature in previous.get("features", [])
            }
            previous_model = (
                "legacy-pairwise"
                if "connection" in previous_types
                else "backbone-outward"
                if previous_types
                & {
                    "spine-access-connection",
                    "school-access-connection",
                    "branch-meeting-connection",
                }
                else "unknown"
            )
            previous_features = [
                feature
                for feature in previous.get("features", [])
                if feature.get("properties", {}).get("feature_type")
                in {
                    "connection",
                    "spine-access-connection",
                    "school-access-connection",
                    "branch-meeting-connection",
                }
            ]
            previous_gaps = [
                feature
                for feature in previous.get("features", [])
                if feature.get("properties", {}).get("feature_type") in {"gap", "school-access-gap"}
            ]
            previous_lines = [
                shape(feature["geometry"])
                for feature in previous_features
                if feature.get("geometry") is not None
            ]
            previous_endpoints = [
                endpoints
                for feature in previous_features
                if (endpoints := _feature_endpoints(feature)) is not None
            ]
            previous_connection_count = len(previous_features)
            previous_gap_count = len(previous_gaps)
            previous_length_m = _linework_length_m(previous_lines, 4326)
            previous_topology = _topology_metrics(previous_endpoints)
            previous_role_counts = dict(
                sorted(
                    pd.Series(
                        [
                            feature.get("properties", {}).get("feature_type", "unknown")
                            for feature in previous_features
                        ],
                        dtype=object,
                    )
                    .value_counts()
                    .to_dict()
                    .items()
                )
            )
    rationale_complete = sum(
        bool(str(row.get("selection_reason", "")).strip())
        for frame in (
            compiled.spine_access_connections,
            compiled.branch_meeting_connections,
        )
        for _, row in frame.iterrows()
    )
    typed_role_complete = sum(
        bool(str(row.get("network_role", "")).strip())
        for frame in (
            compiled.spine_access_connections,
            compiled.branch_meeting_connections,
        )
        for _, row in frame.iterrows()
    )
    current_endpoints = [
        (str(row.place_id), str(row.parent_target_id))
        for row in compiled.spine_access_connections.itertuples()
    ] + [
        (str(row.from_place_id), str(row.to_place_id))
        for row in compiled.branch_meeting_connections.itertuples()
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "comparison_role": "superseded-reference-not-ground-truth",
        "previous_publication_available": previous_path.exists(),
        "current_backbone": {
            "network_model": "backbone-outward",
            "connection_count": compiled.connection_count,
            "spine_access_connection_count": len(compiled.spine_access_connections),
            "branch_meeting_connection_count": len(compiled.branch_meeting_connections),
            "cross_spine_connector_count": len(compiled.cross_spine_connectors),
            "served_obligation_count": int(
                compiled.access_obligations["service_status"]
                .isin(["served", "served-provisional"])
                .sum()
            ),
            "network_gap_count": len(compiled.gaps),
            "linework_length_m": round(current_length_m, 1),
            "typed_role_count": typed_role_complete,
            "selection_rationale_count": rationale_complete,
        },
        "topology": {
            "current": {
                "strategic_spine_count": len(compiled.strategic_spines),
                "network_unit_count": len(compiled.network_units),
                "spine_access_branch_count": len(compiled.spine_access_branches),
                "spine_access_connection_count": len(compiled.spine_access_connections),
                "branch_meeting_connection_count": len(compiled.branch_meeting_connections),
                "cross_spine_connector_count": len(compiled.cross_spine_connectors),
                **_topology_metrics(current_endpoints),
            },
            "previous": {
                "network_model": previous_model,
                "network_gap_count": previous_gap_count,
                **previous_topology,
                "feature_role_counts": previous_role_counts,
            },
        },
        "superseded_pairwise_reference": {
            "network_model": previous_model,
            "connection_count": previous_connection_count,
            "linework_length_m": round(previous_length_m, 1),
        },
        "visual_noise": {
            "connection_count_delta": (compiled.connection_count - previous_connection_count),
            "linework_length_m_delta": round(current_length_m - previous_length_m, 1),
        },
        "explainability": {
            "all_current_connections_have_typed_roles": (
                typed_role_complete == compiled.connection_count
            ),
            "all_current_connections_have_selection_rationale": (
                rationale_complete == compiled.connection_count
            ),
        },
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _feature_endpoints(feature: dict[str, object]) -> tuple[str, str] | None:
    properties = feature.get("properties", {})
    if not isinstance(properties, dict):
        return None
    for left, right in (
        ("from_place", "to_place"),
        ("place_id", "parent_target_id"),
        ("from_place_id", "to_place_id"),
    ):
        if properties.get(left) is not None and properties.get(right) is not None:
            return str(properties[left]), str(properties[right])
    return None


def _topology_metrics(endpoints: list[tuple[str, str]]) -> dict[str, int]:
    graph = nx.Graph()
    graph.add_edges_from(endpoints)
    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "component_count": nx.number_connected_components(graph) if graph else 0,
        "degree_one_node_count": sum(degree == 1 for _, degree in graph.degree()),
    }


def _linework_length_m(geometries: list[object], crs: object) -> float:
    if not geometries:
        return 0.0
    return float(gpd.GeoSeries(geometries, crs=crs).to_crs(27700).length.sum())


def _reference_option_collection(
    record: ReferenceSATNPublicationRecord,
) -> dict[str, object]:
    """Render Reference options as a non-authoritative WGS84 review overlay.

    Candidate geometries in the governed record are canonical EPSG:27700
    linework.  Transforming here, rather than in browser code, keeps the map
    payload inspectable and ensures the selected authoritative network remains
    the ordinary ``network`` collection.
    """

    reference = record.reference_selection
    selected = set(reference.selected_candidate_ids)
    complementary = set(reference.complementary_candidate_ids)
    selections = {item.candidate_set_id: item for item in reference.scenario.selections}
    classifications = {
        item.candidate_set_id: str(item.disposition)
        for item in reference.scenario.candidate_set_classifications
    }
    resolutions = {item.candidate_set_id: item for item in reference.scenario.resolved_selections}
    features: list[dict[str, object]] = []
    for candidate_set in sorted(
        reference.scenario.candidate_sets,
        key=lambda item: item.candidate_set_id,
    ):
        selection = selections[candidate_set.candidate_set_id]
        criteria = selection.criteria.model_dump(mode="json")
        resolution = resolutions.get(candidate_set.candidate_set_id)
        for candidate in candidate_set.candidates:
            if candidate.candidate_id in selected:
                disposition = "selected"
            elif candidate.candidate_id in complementary:
                disposition = "complementary"
            else:
                disposition = "rejected"
            geometry = (
                gpd.GeoSeries([candidate.geometry.as_shapely()], crs="EPSG:27700")
                .to_crs(4326)
                .iloc[0]
            )
            population = _reference_population_summary(
                selection.criteria,
                candidate.candidate_id,
            )
            features.append(
                {
                    "type": "Feature",
                    "id": candidate.candidate_id,
                    "geometry": mapping(geometry),
                    "properties": {
                        "feature_type": "reference-satn-option",
                        "candidate_id": candidate.candidate_id,
                        "candidate_set_id": candidate_set.candidate_set_id,
                        "connection_id": candidate_set.connection_id,
                        "disposition": disposition,
                        "candidate_set_classification": classifications.get(
                            candidate_set.candidate_set_id,
                            "uncertain",
                        ),
                        "network_role": str(candidate.network_role),
                        "source_class": str(candidate.source_class),
                        "directness_m": candidate.directness_m,
                        "maximum_gradient_pct": candidate.maximum_gradient_pct,
                        "evidence_fingerprints": list(candidate.evidence_fingerprints),
                        "criteria": criteria,
                        "population": population,
                        "population_500m": (
                            population.get("500m", {}).get("resident_count")
                            if isinstance(population.get("500m"), dict)
                            else None
                        ),
                        "education": _reference_education_summary(
                            selection.criteria,
                            candidate.candidate_id,
                        ),
                        "existing_alignment": _reference_existing_alignment_summary(
                            selection.criteria,
                            candidate.candidate_id,
                        ),
                        "directness": _reference_finding_summary(
                            selection.criteria,
                            "directness",
                            candidate.candidate_id,
                        ),
                        "topography": _reference_finding_summary(
                            selection.criteria,
                            "gradient",
                            candidate.candidate_id,
                        ),
                        "uncertainty": _reference_finding_summary(
                            selection.criteria,
                            "uncertainty",
                            candidate.candidate_id,
                        ),
                        "decision_and_critique": _reference_decision_summary(
                            selection,
                            resolution,
                        ),
                        "change_conditions": [
                            str(condition) for condition in selection.change_conditions
                        ],
                        "reference_publication_fingerprint": (
                            record.reference_publication_fingerprint
                        ),
                        "disclaimer": (
                            "Alternative alignment evidence is for review only; it is not a "
                            "safe, feasible, funded, or adopted scheme."
                        ),
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _reference_population_summary(criteria: object, candidate_id: str) -> dict[str, object]:
    population = getattr(criteria, "population", None)
    if population is None:
        return {"status": "unavailable"}
    binding = next(
        (item for item in population.option_bindings if item.candidate_id == candidate_id),
        None,
    )
    if binding is None:
        return {"status": "unavailable"}
    result: dict[str, object] = {
        "status": "available",
        "assessment_id": population.assessment_id,
    }
    summaries = {
        (item.option_id, int(item.corridor_distance_m)): item
        for item in population.assessment.summaries
    }
    sensitivities = {
        int(item.corridor_distance_m): item for item in population.assessment.sensitivities
    }
    for radius, findings in (
        (500, population.headline_500m),
        (1000, population.sensitivity_1000m),
    ):
        finding = next(item for item in findings if item.candidate_id == candidate_id)
        summary = summaries[(binding.option_id, radius)]
        sensitivity = sensitivities[radius]
        result[f"{radius}m"] = {
            "resident_count": finding.resident_count,
            "shared_residents": summary.shared_residents,
            "option_exclusive_residents": summary.option_exclusive_residents,
            "rank": finding.rank,
            "near_equivalent": finding.near_equivalent,
            "sensitive": sensitivity.sensitive,
            "within_tolerance": sensitivity.within_tolerance,
            "ordering_flips_from_500m": (sensitivity.ordering_flips_from_first_distance),
            "current_development_omission": finding.current_development_omission,
            "borderline_oa_ids": list(finding.borderline_oa_ids),
        }
    return result


def _reference_education_summary(criteria: object, candidate_id: str) -> dict[str, object]:
    education = getattr(criteria, "education", None)
    if education is None:
        return {"status": "unavailable"}
    completeness = next(
        item for item in education.completeness if item.candidate_id == candidate_id
    )
    opportunity = next(
        item
        for item in education.independent_travel_opportunity
        if item.candidate_id == candidate_id
    )
    return {
        "status": "available",
        "assessment_id": education.assessment_id,
        "completeness_state": str(completeness.state),
        "completeness_evidence_record_id": completeness.evidence_record_id,
        "independent_travel_state": str(opportunity.state),
        "independent_travel_opportunity_count": opportunity.opportunity_count,
        "independent_travel_caveat": (
            "This is not a finding that the route is safe, suitable, or independently accessible."
        ),
    }


def _reference_existing_alignment_summary(
    criteria: object,
    candidate_id: str,
) -> dict[str, object]:
    existing = getattr(criteria, "existing_alignment", None)
    if existing is None:
        return {
            "status": "unknown",
            "unknown_reasons": ["no-governed-existing-alignment-comparison"],
        }
    advantage = next(
        item for item in existing.comparison.advantages if item.candidate_id == candidate_id
    )
    return {
        "status": "available",
        "assessment_id": existing.proof.proof_id,
        "matched_share": advantage.matched_share,
        "recognised_current_share": advantage.recognised_current_share,
        "reusable_asset_share": advantage.reusable_asset_share,
        "unknown_length_m": advantage.unknown_length_m,
        "unknown_reasons": [str(item) for item in advantage.unknown_reasons],
        "evidence_fingerprint": advantage.evidence_fingerprint,
        "caveat": (
            "Existing alignment does not establish legal access, condition, cost, "
            "deliverability, or feasibility."
        ),
    }


def _reference_finding_summary(
    criteria: object,
    field: str,
    candidate_id: str,
) -> dict[str, object]:
    findings = getattr(criteria, field, ())
    finding = next((item for item in findings if item.candidate_id == candidate_id), None)
    if finding is None:
        return {"state": "unknown"}
    return {
        "state": str(finding.state),
        "assessment_id": finding.assessment_id,
        "evidence_record_id": finding.evidence_record_id,
    }


def _reference_decision_summary(selection: object, resolution: object) -> dict[str, object]:
    result: dict[str, object] = {
        "selection_fingerprint": selection.selection_fingerprint,
        "decision_action": str(selection.decision_action),
    }
    envelope = getattr(resolution, "accepted_decision_envelope", None)
    if envelope is None:
        result["mode"] = "deterministic-profile"
        return result
    result.update(
        {
            "mode": "accepted-agent-decision-ledger",
            "request_id": envelope.request.request_id,
            "selected_option_id": envelope.response.option_id,
            "decision_evidence_ids": list(envelope.response.evidence_ids),
            "critique_finding": str(envelope.critique.finding),
            "critique_evidence_ids": list(envelope.critique.evidence_ids),
            "resolved_challenge_fingerprints": list(envelope.resolved_challenge_fingerprints),
            "envelope_fingerprint": envelope.envelope_fingerprint,
        }
    )
    return result


def _reference_option_evidence_html(
    options: dict[str, object],
    record: ReferenceSATNPublicationRecord,
) -> str:
    """Create keyboard-native semantic evidence from the exact map option payload."""

    features = options.get("features", [])
    if not isinstance(features, list):
        raise ValueError("Reference option evidence requires a feature list")
    decision = record.reference_selection.governed_decision
    parts = [
        '<h2 id="reference-satn-heading">Reference SATN review record</h2>',
        (
            "<p>The selected network remains the authoritative default map. "
            "Reviewed alternatives are hidden on the map until enabled, while every "
            "option and its evidence remains available below.</p>"
        ),
        f"<p><strong>Human selection rationale:</strong> {escape(decision.rationale)}</p>",
        f'<div id="reference-option-evidence" data-reference-option-count="{len(features)}">',
    ]
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise ValueError("Reference option evidence contains an invalid feature")
        properties = feature["properties"]
        candidate_id = str(properties["candidate_id"])
        candidate_set_id = str(properties["candidate_set_id"])
        disposition = str(properties["disposition"])
        population = properties.get("population", {})
        education = properties.get("education", {})
        existing = properties.get("existing_alignment", {})
        decision_summary = properties.get("decision_and_critique", {})
        parts.extend(
            [
                (
                    '<details class="reference-option" '
                    f'id="reference-option-{escape(candidate_id)}" '
                    f'data-candidate-id="{escape(candidate_id)}">'
                ),
                (
                    f"<summary>{escape(disposition.title())}: {escape(candidate_id)} "
                    f"({escape(str(properties['source_class']))})</summary>"
                ),
                "<dl>",
                f"<dt>Candidate Set</dt><dd>{escape(candidate_set_id)}</dd>",
                f"<dt>Disposition</dt><dd>{escape(disposition)}</dd>",
                (
                    "<dt>Substitute/complementary classification</dt><dd>"
                    f"{escape(str(properties['candidate_set_classification']))}</dd>"
                ),
                f"<dt>Network role</dt><dd>{escape(str(properties['network_role']))}</dd>",
                f"<dt>Source class</dt><dd>{escape(str(properties['source_class']))}</dd>",
                "</dl>",
                "<h3>Population Reach</h3>",
                _reference_population_html(population),
                (
                    '<p class="caveat">Straight-line whole-Output-Area evidence; '
                    "not demand, a walking-time claim, or population actually connected.</p>"
                ),
                "<h3>Education and independent travel</h3>",
                _reference_mapping_html(
                    education,
                    preferred=(
                        "completeness_state",
                        "independent_travel_state",
                        "independent_travel_opportunity_count",
                        "assessment_id",
                    ),
                ),
                (
                    '<p class="caveat">Independent-Travel Opportunity is not a '
                    "finding that this route is safe, suitable, or independently accessible.</p>"
                ),
                "<h3>Existing-alignment evidence and unknowns</h3>",
                _reference_mapping_html(
                    existing,
                    preferred=(
                        "status",
                        "matched_share",
                        "recognised_current_share",
                        "reusable_asset_share",
                        "unknown_length_m",
                        "unknown_reasons",
                        "assessment_id",
                    ),
                ),
                "<h3>Directness and topography</h3>",
                (
                    f"<p>Directness: {escape(str(properties.get('directness_m')))} m; "
                    f"maximum gradient: {escape(str(properties.get('maximum_gradient_pct')))}. "
                    f"Directness evidence state: {_reference_state(properties.get('directness'))}; "
                    "topography evidence state: "
                    f"{_reference_state(properties.get('topography'))}.</p>"
                ),
                "<h3>Uncertainty, evidence and change conditions</h3>",
                (
                    f"<p>Uncertainty state: {_reference_state(properties.get('uncertainty'))}. "
                    "Evidence references: "
                    f"{_reference_list(properties.get('evidence_fingerprints'))}. "
                    "Change conditions: "
                    f"{_reference_list(properties.get('change_conditions'))}.</p>"
                ),
                "<h3>Decision and critique provenance</h3>",
                _reference_mapping_html(
                    decision_summary,
                    preferred=(
                        "mode",
                        "decision_action",
                        "request_id",
                        "selected_option_id",
                        "decision_evidence_ids",
                        "critique_finding",
                        "critique_evidence_ids",
                        "resolved_challenge_fingerprints",
                    ),
                ),
                f'<p class="caveat">{escape(str(properties["disclaimer"]))}</p>',
                "</details>",
            ]
        )
    parts.extend(
        [
            "</div>",
            *[f'<p class="caveat">{escape(item)}</p>' for item in record.disclaimer],
        ]
    )
    return "\n".join(parts)


def _reference_population_html(value: object) -> str:
    if not isinstance(value, dict) or value.get("status") != "available":
        return "<p>Population evidence is unavailable.</p>"
    rows = []
    for radius, label in (("500m", "500 m"), ("1000m", "1 km")):
        result = value.get(radius)
        if not isinstance(result, dict):
            rows.append(f"<li>{label}: unavailable</li>")
            continue
        rows.append(
            f"<li>{label}: {escape(str(result.get('resident_count')))} residents; "
            f"shared {escape(str(result.get('shared_residents')))}; option-exclusive "
            f"{escape(str(result.get('option_exclusive_residents')))}; rank "
            f"{escape(str(result.get('rank')))}; sensitivity "
            f"{escape(str(result.get('sensitive')))}; within tolerance "
            f"{escape(str(result.get('within_tolerance')))}; ordering flips from 500 m "
            f"{escape(str(result.get('ordering_flips_from_500m')))}.</li>"
        )
    return "<ul>" + "".join(rows) + "</ul>"


def _reference_mapping_html(value: object, *, preferred: tuple[str, ...]) -> str:
    if not isinstance(value, dict):
        return "<p>Evidence unavailable.</p>"
    rows = []
    for key in preferred:
        if key not in value:
            continue
        rows.append(
            f"<dt>{escape(key.replace('_', ' ').title())}</dt>"
            f"<dd>{escape(_reference_text(value[key]))}</dd>"
        )
    return "<dl>" + "".join(rows) + "</dl>" if rows else "<p>Evidence unavailable.</p>"


def _reference_state(value: object) -> str:
    return escape(str(value.get("state", "unknown"))) if isinstance(value, dict) else "unknown"


def _reference_list(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "none recorded"
    return escape(", ".join(str(item) for item in value))


def _reference_text(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none recorded"
    if value is None:
        return "unknown"
    return str(value)


def _strategic_candidate_record(records: object, candidate_id: object) -> dict[str, object]:
    if not isinstance(records, list):
        return {}
    return next(
        (
            item
            for item in records
            if isinstance(item, dict) and item.get("candidate_id") == candidate_id
        ),
        {},
    )


def _strategic_population_html(criteria: object, candidate_id: object) -> str:
    if not isinstance(criteria, dict) or not isinstance(criteria.get("population"), dict):
        return "<p>Population reach evidence: unavailable.</p>"
    population = criteria["population"]
    assessment = population.get("assessment")
    summaries = assessment.get("summaries", []) if isinstance(assessment, dict) else []
    sensitivities = assessment.get("sensitivities", []) if isinstance(assessment, dict) else []
    rows = []
    for key, radius, label in (
        ("headline_500m", 500, "500 m"),
        ("sensitivity_1000m", 1000, "1 km"),
    ):
        finding = _strategic_candidate_record(population.get(key), candidate_id)
        summary = next(
            (
                item
                for item in summaries
                if isinstance(item, dict)
                and item.get("option_id") == candidate_id
                and float(item.get("corridor_distance_m", -1)) == radius
            ),
            {},
        )
        sensitivity = next(
            (
                item
                for item in sensitivities
                if isinstance(item, dict) and float(item.get("corridor_distance_m", -1)) == radius
            ),
            {},
        )
        rows.append(
            f'<li data-radius-m="{radius}"><strong>{label}</strong>: '
            f"{escape(_reference_text(finding.get('resident_count')))} residents; "
            f"shared {escape(_reference_text(summary.get('shared_residents')))}; "
            "option-exclusive "
            f"{escape(_reference_text(summary.get('option_exclusive_residents')))}; "
            f"rank {escape(_reference_text(finding.get('rank')))}; "
            f"sensitive {escape(_reference_text(sensitivity.get('sensitive')))}; "
            "ordering changed from the headline radius "
            f"{escape(_reference_text(sensitivity.get('ordering_flips_from_first_distance')))}."
            "</li>"
        )
    return '<ul class="strategic-population-evidence">' + "".join(rows) + "</ul>"


def _strategic_route_validity(
    candidate: dict[str, object],
    completeness: dict[str, object],
    comparison: dict[str, object],
    precomparison: dict[str, object],
) -> str:
    """Render the selection hard-gate validity without inventing a fallback.

    Selection records are authoritative when they contain a validity result.
    Older or selected records can omit it, so rederive the same four-state
    hard gate used by ``alignment_selection._derive_selection`` from the
    published candidate topology and education-completeness evidence.  Route
    directness is intentionally not a validity gate.
    """

    for record in (comparison, precomparison):
        value = record.get("validity")
        if isinstance(value, str) and value in CandidateValidity._value2member_map_:
            return value
    topology = candidate.get("topology_state")
    education = completeness.get("state")
    if topology == CriterionState.UNKNOWN.value or education == CriterionState.UNKNOWN.value:
        return CandidateValidity.UNKNOWN_HARD_GATE.value
    if topology == CriterionState.UNSATISFIED.value:
        return CandidateValidity.INVALID_TOPOLOGY.value
    if education == CriterionState.UNSATISFIED.value:
        return CandidateValidity.EDUCATION_INCOMPLETE.value
    return CandidateValidity.VALID.value


def _strategic_candidate_evidence_html(
    candidate: dict[str, object],
    candidate_set: dict[str, object],
    selection: dict[str, object],
    resolved: dict[str, object],
) -> str:
    candidate_id = candidate.get("candidate_id")
    criteria = selection.get("criteria")
    criteria = criteria if isinstance(criteria, dict) else {}
    selected = selection.get("selected_candidate_id") == candidate_id
    complementary = candidate_id in selection.get("complementary_candidate_ids", [])
    disposition = "selected" if selected else "complementary" if complementary else "rejected"
    comparison = _strategic_candidate_record(selection.get("comparison_dispositions"), candidate_id)
    precomparison = _strategic_candidate_record(
        selection.get("precomparison_rejections"), candidate_id
    )
    rationale = (
        comparison.get("rationale")
        or precomparison.get("rationale")
        or selection.get("decision_action")
        or "no separate rationale recorded"
    )
    education = criteria.get("education")
    education = education if isinstance(education, dict) else {}
    completeness = _strategic_candidate_record(education.get("completeness"), candidate_id)
    independent = _strategic_candidate_record(
        education.get("independent_travel_opportunity"), candidate_id
    )
    directness = _strategic_candidate_record(criteria.get("directness"), candidate_id)
    gradient = _strategic_candidate_record(criteria.get("gradient"), candidate_id)
    uncertainty = _strategic_candidate_record(criteria.get("uncertainty"), candidate_id)
    existing = criteria.get("existing_alignment")
    existing = existing if isinstance(existing, dict) else {}
    existing_proof = existing.get("proof")
    existing_proof = existing_proof if isinstance(existing_proof, dict) else {}
    existing_comparison = _strategic_candidate_record(
        existing.get("comparison", {}).get("advantages")
        if isinstance(existing.get("comparison"), dict)
        else None,
        candidate_id,
    )
    if existing:
        existing_html = _reference_mapping_html(
            {
                "proof_id": existing_proof.get("proof_id"),
                "near_equivalent_after_mandatory_gates": existing_proof.get(
                    "near_equivalent_after_mandatory_gates"
                ),
                "matched_share": existing_comparison.get("matched_share"),
                "recognised_current_share": existing_comparison.get("recognised_current_share"),
                "reusable_asset_share": existing_comparison.get("reusable_asset_share"),
                "unknown_length_m": existing_comparison.get("unknown_length_m"),
                "unknown_reasons": existing_comparison.get("unknown_reasons"),
                "evidence_fingerprint": existing_comparison.get("evidence_fingerprint"),
            },
            preferred=(
                "proof_id",
                "near_equivalent_after_mandatory_gates",
                "matched_share",
                "recognised_current_share",
                "reusable_asset_share",
                "unknown_length_m",
                "unknown_reasons",
                "evidence_fingerprint",
            ),
        )
    else:
        existing_html = (
            "<p>Existing-alignment assessment: unknown; no governed assessment was bound.</p>"
        )
    mandatory_access = set(candidate_set.get("mandatory_access_obligation_ids", []))
    served_access = set(candidate.get("served_access_obligation_ids", []))
    mandatory_destinations = set(candidate_set.get("mandatory_strategic_destination_ids", []))
    served_destinations = set(candidate.get("served_strategic_destination_ids", []))
    envelope = resolved.get("accepted_decision_envelope")
    envelope = envelope if isinstance(envelope, dict) else {}
    response = envelope.get("response")
    response = response if isinstance(response, dict) else {}
    critique = envelope.get("critique")
    critique = critique if isinstance(critique, dict) else {}
    change_conditions = list(selection.get("change_conditions", []))
    route_validity = _strategic_route_validity(
        candidate,
        completeness,
        comparison,
        precomparison,
    )
    for item in (comparison, precomparison):
        for condition in item.get("change_conditions", []):
            if condition not in change_conditions:
                change_conditions.append(condition)
    open_attribute = " open" if selected or complementary else ""
    candidate_label = escape(str(candidate_id or "unknown"))
    return "\n".join(
        (
            (
                f'<details class="strategic-option strategic-option-{disposition}"'
                f' data-candidate-id="{candidate_label}"{open_attribute}>'
            ),
            (
                f"<summary>{escape(disposition.title())}: {candidate_label} "
                f"({escape(_reference_text(candidate.get('source_class')))}) — "
                f"{escape(_reference_text(candidate_set.get('network_role')))}</summary>"
            ),
            "<dl>",
            f"<dt>Decision rationale</dt><dd>{escape(_reference_text(rationale))}</dd>",
            (
                "<dt>Topology and route validity</dt><dd>"
                f"{escape(_reference_text(candidate.get('topology_state')))}; "
                f"{escape(_reference_text(route_validity))}</dd>"
            ),
            (
                "<dt>Route directness</dt><dd>"
                f"{escape(_reference_text(directness.get('state')))}</dd>"
            ),
            "</dl>",
            "<h4>Population reach</h4>",
            _strategic_population_html(criteria, candidate_id),
            (
                '<p class="caveat">Straight-line whole-Output-Area corridor evidence; '
                "not demand, a walking-time claim, or population actually connected.</p>"
            ),
            "<h4>Education and destinations</h4>",
            (
                f"<p>Education completeness: {escape(_reference_text(completeness.get('state')))}. "
                "Independent-Travel Opportunity count: "
                f"{escape(_reference_text(independent.get('opportunity_count')))} "
                f"({escape(_reference_text(independent.get('state')))}). "
                f"Access obligations served: {_reference_list(sorted(served_access))}; "
                f"missed: {_reference_list(sorted(mandatory_access - served_access))}. "
                f"Strategic destinations served: {_reference_list(sorted(served_destinations))}; "
                f"missed: {_reference_list(sorted(mandatory_destinations - served_destinations))}."
                "</p>"
            ),
            (
                '<p class="caveat">Independent-Travel Opportunity is not a finding '
                "that this route is safe, suitable, feasible, lawful, funded, adopted, "
                "or independently accessible.</p>"
            ),
            "<h4>Existing-alignment evidence</h4>",
            existing_html,
            "<h4>Directness, gradient and uncertainty</h4>",
            (
                f"<p>Route length/directness measure: "
                f"{escape(_reference_text(candidate.get('directness_m')))} m "
                f"({escape(_reference_text(directness.get('state')))}). "
                f"Maximum gradient: "
                f"{escape(_reference_text(candidate.get('maximum_gradient_pct')))} "
                f"({escape(_reference_text(gradient.get('state')))}). "
                f"Uncertainty: {escape(_reference_text(uncertainty.get('state')))}.</p>"
            ),
            "<h4>Decision provenance and change conditions</h4>",
            (
                f"<p>Resolution action: "
                f"{escape(_reference_text(resolved.get('resolution_action')))}; "
                f"accepted finite option: {escape(_reference_text(response.get('option_id')))}; "
                f"independent critique: {escape(_reference_text(critique.get('finding')))}. "
                f"Change conditions: {_reference_list(change_conditions)}.</p>"
            ),
            "</details>",
        )
    )


def _strategic_reference_review_html(payload: dict[str, object]) -> str:
    """Semantic strategic-only review summary, usable without JavaScript."""

    plan = payload.get("application_plan", {})
    bindings = plan.get("bindings", []) if isinstance(plan, dict) else []
    selected = [
        item
        for item in bindings
        if isinstance(item, dict) and item.get("application_disposition") == "selected-substitute"
    ]
    complementary = [
        item
        for item in bindings
        if isinstance(item, dict)
        and item.get("application_disposition") == "complementary-required"
    ]
    selected_text = (
        ", ".join(escape(str(item.get("selected_candidate_id", "unknown"))) for item in selected)
        or "none"
    )
    complementary_text = (
        ", ".join(
            escape(str(item.get("selected_candidate_id", "unknown"))) for item in complementary
        )
        or "none"
    )
    profile = (
        escape(str(plan.get("profile_fingerprint", "unknown")))
        if isinstance(plan, dict)
        else "unknown"
    )
    evidence = (
        escape(str(plan.get("evidence_snapshot_fingerprint", "unknown")))
        if isinstance(plan, dict)
        else "unknown"
    )
    # The application plan is the exact adopted Scenario record.  Retaining
    # its canonical identifiers in semantic HTML makes provenance inspectable
    # with JavaScript unavailable, while bounded details keeps alternatives
    # keyboard-accessible and collapsed by default.
    reference = plan.get("reference", {}) if isinstance(plan, dict) else {}
    scenario = reference.get("scenario", {}) if isinstance(reference, dict) else {}
    scenario_json = escape(json.dumps(scenario, sort_keys=True, indent=2))
    option_cards = []
    selections = scenario.get("selections", []) if isinstance(scenario, dict) else []
    resolved = scenario.get("resolved_selections", []) if isinstance(scenario, dict) else []
    for candidate_set in scenario.get("candidate_sets", []) if isinstance(scenario, dict) else []:
        if not isinstance(candidate_set, dict):
            continue
        candidate_set_id = candidate_set.get("candidate_set_id")
        selection = next(
            (
                item
                for item in selections
                if isinstance(item, dict)
                and isinstance(item.get("candidate_set"), dict)
                and item["candidate_set"].get("candidate_set_id") == candidate_set_id
            ),
            {},
        )
        resolved_selection = next(
            (
                item
                for item in resolved
                if isinstance(item, dict)
                and isinstance(item.get("compiler_selection"), dict)
                and isinstance(item["compiler_selection"].get("candidate_set"), dict)
                and item["compiler_selection"]["candidate_set"].get("candidate_set_id")
                == candidate_set_id
            ),
            {},
        )
        for candidate in candidate_set.get("candidates", []):
            if not isinstance(candidate, dict) or not isinstance(selection, dict):
                continue
            option_cards.append(
                _strategic_candidate_evidence_html(
                    candidate,
                    candidate_set,
                    selection,
                    resolved_selection if isinstance(resolved_selection, dict) else {},
                )
            )
    rows = (
        '<section id="strategic-reference-review" aria-labelledby="strategic-reference-title">',
        '<h2 id="strategic-reference-title">Strategic Reference review</h2>',
        '<p><span class="strategic-role-label">Selected interurban alignment</span>: '
        f"{selected_text}. <strong>Complementary destination access:</strong> "
        f"{complementary_text}. These are distinct, labelled roles.</p>",
        f"<p>Profile fingerprint: {profile}; evidence snapshot: {evidence}. "
        "Population evidence is preserved in the adopted Scenario, including "
        "500m and 1km reach, shared/exclusive coverage and sensitivity.</p>",
        "<p>Education served/missed conclusions remain role-scoped. "
        "Independent-travel opportunity is not a safety, suitability, feasibility, "
        "funding, lawfulness or independent-access finding. Existing-alignment "
        "evidence may be unknown; directness, gradient and evidence validity are "
        "review inputs.</p>",
        "<p>Agent and critique provenance are in the published decision records. "
        "This Reference is not a final design or delivery decision.</p>",
        '<div class="strategic-option-evidence">' + "".join(option_cards) + "</div>",
        "<details><summary>Complete governed Scenario record</summary>",
        f"<pre>{scenario_json}</pre></details>",
        "</section>",
    )
    return "\n".join(rows)


def _write_review_map(
    review: Path,
    config: AreaConfig,
    compiled: CompiledNetwork,
    reference_publication: _ValidatedReferencePublication | None = None,
    strategic_reference_publication: _ValidatedStrategicReferencePublication | None = None,
) -> None:
    if compiled.reference_satn_publication is not None and reference_publication is None:
        raise ValueError("Reference map serialization requires compiler-bound publication evidence")
    if (
        compiled.strategic_reference_publication is not None
        and strategic_reference_publication is None
    ):
        raise ValueError("strategic Reference map serialization requires compiler-bound evidence")
    reference_record = reference_publication.record if reference_publication is not None else None
    reference_options = (
        reference_publication.options() if reference_publication is not None else None
    )
    asset_root = files("satn.assets")
    asset_output = review / "assets"
    asset_output.mkdir()
    fingerprinted_assets: dict[str, str] = {}
    for name in (
        "maplibre-gl.js",
        "maplibre-gl.css",
        "MAPLIBRE-LICENSE.txt",
        "review-map.js",
        "review-map.css",
    ):
        content = (asset_root / name).read_bytes()
        (asset_output / name).write_bytes(content)
        if name.startswith("review-map."):
            path = Path(name)
            digest = hashlib.sha256(content).hexdigest()[:12]
            fingerprinted_name = f"{path.stem}.{digest}{path.suffix}"
            (asset_output / fingerprinted_name).write_bytes(content)
            fingerprinted_assets[name] = fingerprinted_name
    template = (asset_root / "review-map.html").read_text(encoding="utf-8")
    atm_state = "" if compiled.atm_reference is not None else "disabled"
    atm_status = (
        "A governed ATM reference is bundled locally; toggle it to compare before/after."
        if compiled.atm_reference is not None
        else "ATM geometry is not published. Load a governed local GeoJSON to compare it."
    )
    html = (
        template.replace("__TITLE__", escape(config.publication.title))
        .replace("__DISCLAIMER__", DISCLAIMER)
        .replace("__REVIEW_MAP_CSS__", fingerprinted_assets["review-map.css"])
        .replace("__REVIEW_MAP_JS__", fingerprinted_assets["review-map.js"])
        .replace("__ATM_STATE__", atm_state)
        .replace("__ATM_STATUS__", atm_status)
        .replace(
            "__REFERENCE_SATN_STATE__",
            "" if reference_record is not None else "hidden",
        )
        .replace(
            "__REFERENCE_SATN_EVIDENCE__",
            (
                _reference_option_evidence_html(reference_options, reference_record)
                if reference_record is not None and reference_options is not None
                else ""
            ),
        )
        .replace(
            "__GENTLE_MAX_PCT__",
            f"{config.compilation.topography.gentle_max_pct:g}",
        )
        .replace(
            "__NOTICEABLE_MAX_PCT__",
            f"{config.compilation.topography.noticeable_max_pct:g}",
        )
        .replace(
            "__STEEP_MAX_PCT__",
            f"{config.compilation.topography.steep_max_pct:g}",
        )
        .replace(
            "__VERY_STEEP_MAX_PCT__",
            f"{config.compilation.topography.very_steep_max_pct:g}",
        )
    )
    if strategic_reference_publication is not None:
        # Native details provides keyboard operation and a complete no-JS
        # audit trail without changing ordinary map template/assets.
        html = html.replace(
            "</main>",
            _strategic_reference_review_html(strategic_reference_publication.payload()) + "</main>",
        )
        for name in ("strategic-reference.css", "strategic-reference.js"):
            content = (asset_root / name).read_bytes()
            (asset_output / name).write_bytes(content)
        html = html.replace(
            "</head>",
            '<link rel="stylesheet" href="assets/strategic-reference.css">\n</head>',
        ).replace(
            f'<script src="assets/{fingerprinted_assets["review-map.js"]}"></script>',
            '<script src="assets/strategic-reference.js"></script>\n'
            f'<script src="assets/{fingerprinted_assets["review-map.js"]}"></script>',
        )
    (review / "index.html").write_text(html, encoding="utf-8")
    data = {
        "network": _network_collection(compiled),
        "places": {
            "type": "FeatureCollection",
            "features": _features(compiled.places, "place"),
        },
        "criteria": {
            section: {criterion: status.value for criterion, status in values.items()}
            for section, values in compiled.criteria.items()
        },
        "disclaimer": DISCLAIMER,
        "layer_counts": _layer_counts(compiled),
    }
    if reference_record is not None and reference_options is not None:
        data["reference_satn"] = reference_publication.payload()
        data["reference_satn_options"] = reference_options
    if strategic_reference_publication is not None:
        data["strategic_reference"] = _strategic_publication_view(
            compiled, strategic_reference_publication
        )
    (review / "data.js").write_text(
        f"window.SATN_DATA = {json.dumps(data).replace('</', '<\\/')};\n",
        encoding="utf-8",
    )
    (review / "network.geojson").write_text(
        json.dumps(_network_collection(compiled), indent=2), encoding="utf-8"
    )
    (review / "agent-records.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "disclaimer": DISCLAIMER,
                "records": [record.model_dump(mode="json") for record in compiled.agent_records],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (review / "divergence-records.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "disclaimer": DISCLAIMER,
                "records": [
                    record.model_dump(mode="json") for record in compiled.divergence_records
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (review / "human-intervention-requests.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "disclaimer": DISCLAIMER,
                "records": [
                    request.model_dump(mode="json")
                    for request in compiled.human_intervention_requests
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    shutil.copy2(
        review.parent / "backbone-comparison.json",
        review / "backbone-comparison.json",
    )
    (review / "README.txt").write_text(
        f"{DISCLAIMER}\n{OSM_ATTRIBUTION}\n{NCN_ATTRIBUTION}\n", encoding="utf-8"
    )


def _zip_review_map(path: Path, review: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(path for path in review.rglob("*") if path.is_file()):
            archive.write(item, arcname=f"review-map/{item.relative_to(review)}")


def _write_pdf(path: Path, config: AreaConfig, compiled: CompiledNetwork) -> None:
    page_sizes = {"A2": A2, "A3": A3, "A4": A4}
    requested = config.publication.pdf_page_size.upper()
    if requested not in page_sizes:
        raise ValueError(f"unsupported PDF page size: {requested}")
    width, height = landscape(page_sizes[requested])
    canvas = Canvas(str(path), pagesize=(width, height), pageCompression=1)
    canvas.setTitle(config.publication.title)
    canvas.setFillColor(HexColor("#17202a"))
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(42, height - 34, config.publication.title)
    canvas.setFillColor(HexColor("#566573"))
    canvas.setFont("Helvetica", 9)
    canvas.drawString(
        42,
        height - 50,
        f"Experimental backbone review | {compiled.connection_count} connections | "
        f"Compiled {datetime.now(UTC).date().isoformat()}",
    )
    _draw_legend(canvas, width, height, compiled.atm_reference is not None)
    if not compiled.boundary.empty:
        boundary = compiled.boundary.to_crs(3857)
        boundary_shape = boundary.geometry.union_all()
        min_x, min_y, max_x, max_y = boundary.total_bounds
        padding = max(max_x - min_x, max_y - min_y) * 0.025
        min_x, min_y = min_x - padding, min_y - padding
        max_x, max_y = max_x + padding, max_y + padding
        map_left, map_bottom = 42.0, 58.0
        map_right, map_top = width - 42.0, height - 76.0
        map_width, map_height = map_right - map_left, map_top - map_bottom
        scale = min(map_width / (max_x - min_x), map_height / (max_y - min_y))
        origin_x = map_left + (map_width - (max_x - min_x) * scale) / 2
        origin_y = map_bottom + (map_height - (max_y - min_y) * scale) / 2
        clip_shape = boundary_shape.buffer(1200)

        canvas.setFillColor(HexColor("#f5f3eb"))
        canvas.setStrokeColor(HexColor("#7b8794"))
        canvas.setLineWidth(0.9)
        _draw_geometry(canvas, boundary_shape, min_x, min_y, scale, origin_x, origin_y, fill=True)
        canvas.setStrokeColor(HexColor("#59636e"))
        canvas.setLineWidth(1.1)
        for authority_boundary in boundary.geometry:
            _draw_geometry(
                canvas,
                authority_boundary,
                min_x,
                min_y,
                scale,
                origin_x,
                origin_y,
                fill=False,
            )

        roads = compiled.road_context.to_crs(3857)
        context_mask = roads.get("highway", pd.Series(index=roads.index, dtype=object)).map(
            _is_pdf_context_road
        )
        road_geometries = [
            geometry.intersection(boundary_shape)
            for geometry in roads.loc[context_mask].geometry
            if geometry is not None and not geometry.is_empty
        ]
        canvas.setStrokeColor(HexColor("#d2d5d8"))
        canvas.setLineWidth(0.32)
        _draw_line_collection(canvas, road_geometries, min_x, min_y, scale, origin_x, origin_y)

        canvas.setStrokeColor(HexColor("#c56a1a"))
        canvas.setLineWidth(2.4)
        _draw_line_collection(
            canvas,
            _clipped_linework(compiled.a_road_spines, clip_shape),
            min_x,
            min_y,
            scale,
            origin_x,
            origin_y,
        )

        canvas.setStrokeColor(HexColor("#187aa5"))
        canvas.setLineWidth(1.25)
        canvas.setDash(5, 3)
        _draw_line_collection(
            canvas,
            _clipped_linework(compiled.ncn_routes, clip_shape),
            min_x,
            min_y,
            scale,
            origin_x,
            origin_y,
        )
        canvas.setDash()

        if compiled.atm_reference is not None:
            canvas.setStrokeColor(HexColor("#7b61a8"))
            canvas.setLineWidth(1.1)
            canvas.setDash(2, 2)
            _draw_line_collection(
                canvas,
                _clipped_linework(compiled.atm_reference, clip_shape),
                min_x,
                min_y,
                scale,
                origin_x,
                origin_y,
            )
            canvas.setDash()

        school_mask = compiled.spine_access_connections["obligation_kind"] == "school"
        for frame, colour in (
            (compiled.spine_access_connections[~school_mask], "#08783f"),
            (compiled.spine_access_connections[school_mask], "#7d3c98"),
            (compiled.branch_meeting_connections, "#d47b00"),
        ):
            _draw_pdf_role_linework(
                canvas,
                _clipped_linework(frame, clip_shape),
                colour,
                min_x,
                min_y,
                scale,
                origin_x,
                origin_y,
            )

        _draw_pdf_places(
            canvas,
            compiled.label_places,
            min_x,
            min_y,
            max_x,
            max_y,
            scale,
            origin_x,
            origin_y,
            boundary_shape,
        )
        if not compiled.crossing_warnings.empty:
            canvas.setStrokeColor(HexColor("#7d5100"))
            canvas.setFillColor(HexColor("#f4b942"))
            for point in compiled.crossing_warnings.to_crs(3857).geometry:
                px, py = _page_point(point.x, point.y, min_x, min_y, scale, origin_x, origin_y)
                canvas.circle(px, py, 2.8, stroke=1, fill=1)
        _draw_scale(canvas, scale, origin_x, origin_y)

    _draw_pdf_footer(canvas, width)
    _draw_edge_register(canvas, width, height, compiled)
    canvas.save()


def _draw_legend(canvas: Canvas, width: float, height: float, include_atm: bool) -> None:
    entries = [
        ("#c56a1a", "A-road corridor"),
        ("#08783f", "Spine Access Connection"),
        ("#7d3c98", "School Access Connection"),
        ("#d47b00", "Branch Meeting / Cross-Spine"),
        ("#187aa5", "National Cycle Network"),
        ("#f4b942", "Crossing warning"),
    ]
    if include_atm:
        entries.append(("#7b61a8", "ATM reference"))
    x = width - 430
    y = height - 31
    canvas.setFillColor(HexColor("#17202a"))
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawRightString(x - 10, y, "Legend")
    canvas.setFont("Helvetica", 8)
    for index, (colour, label) in enumerate(entries):
        column = index % 2
        row = index // 2
        item_x = x + column * 190
        item_y = y - row * 16
        canvas.setStrokeColor(HexColor(colour))
        canvas.setLineWidth(3)
        canvas.line(item_x, item_y + 2, item_x + 22, item_y + 2)
        canvas.setFillColor(HexColor("#17202a"))
        canvas.drawString(item_x + 28, item_y, label)


def _draw_pdf_role_linework(
    canvas: Canvas,
    geometries: list[object],
    colour: str,
    min_x: float,
    min_y: float,
    scale: float,
    origin_x: float,
    origin_y: float,
) -> None:
    canvas.setStrokeColor(HexColor("#ffffff"))
    canvas.setLineWidth(3.4)
    _draw_line_collection(canvas, geometries, min_x, min_y, scale, origin_x, origin_y)
    canvas.setStrokeColor(HexColor(colour))
    canvas.setLineWidth(1.8)
    _draw_line_collection(canvas, geometries, min_x, min_y, scale, origin_x, origin_y)


def _draw_pdf_footer(canvas: Canvas, width: float) -> None:
    canvas.setFillColor(HexColor("#566573"))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(
        42,
        24,
        "Sources: OpenStreetMap contributors (ODbL); Walk Wheel Cycle Trust NCN (OGL v3.0).",
    )
    canvas.drawRightString(width - 42, 24, DISCLAIMER)


def _draw_edge_register(
    canvas: Canvas,
    width: float,
    height: float,
    compiled: CompiledNetwork,
) -> None:
    """Append the stable identifiers and authoritative roles represented on the map."""
    entries = (
        [
            (
                str(row.access_connection_id),
                str(row.network_role),
                f"{row.place_name} -> {row.parent_target_name}",
            )
            for row in compiled.spine_access_connections.itertuples()
        ]
        + [
            (
                str(row.meeting_connection_id),
                str(row.network_role),
                f"{row.from_place_name} -> {row.to_place_name}",
            )
            for row in compiled.branch_meeting_connections.itertuples()
        ]
        + [
            (
                str(row.cross_spine_connector_id),
                str(row.network_role),
                f"{row.from_root_spine_name} -> {row.to_root_spine_name}",
            )
            for row in compiled.cross_spine_connectors.itertuples()
        ]
    )
    if not entries:
        return
    entries.sort()
    rows_per_page = max(1, int((height - 112) // 11))
    for offset in range(0, len(entries), rows_per_page):
        canvas.showPage()
        page_entries = entries[offset : offset + rows_per_page]
        canvas.setFillColor(HexColor("#17202a"))
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(42, height - 42, "Authoritative edge register")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(HexColor("#566573"))
        canvas.drawString(
            42,
            height - 58,
            "Stable identifier | feature role | represented connection",
        )
        y = height - 78
        for identifier, role, description in page_entries:
            canvas.setFillColor(HexColor("#17202a"))
            canvas.drawString(42, y, f"{identifier} | {role} | {description}"[:180])
            y -= 11
        _draw_pdf_footer(canvas, width)


def _is_pdf_context_road(value: object) -> bool:
    text = str(value).lower()
    return any(
        road_class in text
        for road_class in (
            "trunk",
            "primary",
            "secondary",
            "tertiary",
            "residential",
            "unclassified",
        )
    )


def _clipped_linework(frame: gpd.GeoDataFrame, clip_shape: object) -> list[object]:
    if frame.empty:
        return []
    return [
        geometry.intersection(clip_shape)
        for geometry in frame.to_crs(3857).geometry
        if geometry is not None and not geometry.is_empty and geometry.intersects(clip_shape)
    ]


def _line_coordinate_sets(geometry: object) -> list[object]:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry.coords]
    if geometry.geom_type == "MultiLineString":
        return [part.coords for part in geometry.geoms]
    if hasattr(geometry, "geoms"):
        return [
            coordinates for part in geometry.geoms for coordinates in _line_coordinate_sets(part)
        ]
    return []


def _page_point(
    x: float,
    y: float,
    min_x: float,
    min_y: float,
    scale: float,
    origin_x: float,
    origin_y: float,
) -> tuple[float, float]:
    return origin_x + (x - min_x) * scale, origin_y + (y - min_y) * scale


def _draw_line_collection(
    canvas: Canvas,
    geometries: list[object],
    min_x: float,
    min_y: float,
    scale: float,
    origin_x: float,
    origin_y: float,
) -> None:
    path_obj = canvas.beginPath()
    drew_line = False
    for geometry in geometries:
        for coordinates in _line_coordinate_sets(geometry):
            for index, (x, y) in enumerate(coordinates):
                px, py = _page_point(x, y, min_x, min_y, scale, origin_x, origin_y)
                (path_obj.moveTo if index == 0 else path_obj.lineTo)(px, py)
            drew_line = True
    if drew_line:
        canvas.drawPath(path_obj, stroke=1, fill=0)


def _draw_pdf_places(
    canvas: Canvas,
    places: gpd.GeoDataFrame,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    scale: float,
    origin_x: float,
    origin_y: float,
    boundary_shape: object,
) -> None:
    projected = places.to_crs(3857)
    if "kind" in projected.columns:
        projected = projected[
            projected["kind"].isin(["community", "cross_boundary_gateway"])
        ].copy()
    else:
        projected = projected.copy()
        projected["kind"] = "community"
        projected["place_class"] = projected.get("place", "village")
    if "place_class" not in projected.columns:
        projected["place_class"] = projected["kind"].map(
            {"cross_boundary_gateway": "gateway", "community": "village"}
        )
    projected = projected[
        projected["name"].notna()
        & ~projected["name"].astype(str).str.startswith("Towards ")
        & projected["place_class"].ne("hamlet")
    ].copy()
    projected["geometry"] = projected.geometry.representative_point()
    projected = projected.cx[min_x:max_x, min_y:max_y]
    projected = projected[projected.geometry.map(boundary_shape.covers)]
    projected = projected.drop_duplicates("name")
    priorities = {
        "city": 0,
        "gateway": 1,
        "town": 2,
        "quarter": 3,
        "neighbourhood": 4,
        "village": 5,
        "suburb": 6,
    }
    projected["_priority"] = projected["place_class"].map(priorities).fillna(9)
    projected = projected.sort_values(["_priority", "name"])

    canvas.setStrokeColor(HexColor("#34495e"))
    for _, place in projected.iterrows():
        px, py = _page_point(
            place.geometry.x,
            place.geometry.y,
            min_x,
            min_y,
            scale,
            origin_x,
            origin_y,
        )
        radius = 2.8 if place["place_class"] in {"city", "town", "gateway"} else 1.45
        canvas.setFillColor(HexColor("#ffffff"))
        canvas.circle(px, py, radius, stroke=1, fill=1)

    occupied: list[tuple[float, float, float, float]] = []
    label_count = 0
    map_right = origin_x + (max_x - min_x) * scale
    map_top = origin_y + (max_y - min_y) * scale
    for _, place in projected.iterrows():
        if label_count >= 48:
            break
        name = str(place["name"])
        place_class = str(place["place_class"])
        font_size = (
            8.4 if place_class == "city" else 7.2 if place_class in {"town", "gateway"} else 5.8
        )
        font_name = "Helvetica-Bold" if place_class in {"city", "town", "gateway"} else "Helvetica"
        width = stringWidth(name, font_name, font_size)
        px, py = _page_point(
            place.geometry.x,
            place.geometry.y,
            min_x,
            min_y,
            scale,
            origin_x,
            origin_y,
        )
        candidates = (
            (px + 3.5, py + 1.5),
            (px + 3.5, py - font_size - 1),
            (px - width - 3.5, py + 1.5),
            (px - width - 3.5, py - font_size - 1),
        )
        selected: tuple[float, float, float, float] | None = None
        for label_x, label_y in candidates:
            box = (label_x - 1, label_y - 1, label_x + width + 1, label_y + font_size + 1)
            inside = (
                box[0] >= origin_x
                and box[1] >= origin_y
                and box[2] <= map_right
                and box[3] <= map_top
            )
            overlaps = any(
                box[0] < other[2] + 2
                and box[2] + 2 > other[0]
                and box[1] < other[3] + 2
                and box[3] + 2 > other[1]
                for other in occupied
            )
            if inside and not overlaps:
                selected = box
                break
        if selected is None:
            continue
        canvas.setFillColor(HexColor("#ffffff"))
        canvas.rect(
            selected[0],
            selected[1],
            selected[2] - selected[0],
            selected[3] - selected[1],
            stroke=0,
            fill=1,
        )
        canvas.setFillColor(HexColor("#263238"))
        canvas.setFont(font_name, font_size)
        canvas.drawString(selected[0] + 1, selected[1] + 1, name)
        occupied.append(selected)
        label_count += 1


def _draw_scale(
    canvas: Canvas,
    map_scale: float,
    origin_x: float,
    origin_y: float,
) -> None:
    distance_km = min((0.5, 1, 2, 5, 10), key=lambda value: abs(value * 1000 * map_scale - 120))
    pixels = distance_km * 1000 * map_scale
    canvas.setStrokeColor(HexColor("#17202a"))
    canvas.setLineWidth(1.2)
    y = origin_y + 7
    x = origin_x + 8
    canvas.line(x, y, x + pixels, y)
    canvas.line(x, y - 3, x, y + 3)
    canvas.line(x + pixels, y - 3, x + pixels, y + 3)
    canvas.setFillColor(HexColor("#17202a"))
    canvas.setFont("Helvetica", 7)
    canvas.drawString(x, y + 5, f"{distance_km:g} km scale")


def _draw_geometry(
    canvas: Canvas,
    geometry: object,
    min_x: float,
    min_y: float,
    scale: float,
    origin_x: float,
    origin_y: float,
    *,
    fill: bool = False,
) -> None:
    if geometry is None or geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        coordinate_sets = [geometry.exterior.coords]
    elif geometry.geom_type == "MultiPolygon":
        coordinate_sets = [part.exterior.coords for part in geometry.geoms]
    elif geometry.geom_type == "LineString":
        coordinate_sets = [geometry.coords]
    elif geometry.geom_type == "MultiLineString":
        coordinate_sets = [part.coords for part in geometry.geoms]
    else:
        return
    for coordinates in coordinate_sets:
        path_obj = canvas.beginPath()
        for index, (x, y) in enumerate(coordinates):
            px, py = _page_point(x, y, min_x, min_y, scale, origin_x, origin_y)
            (path_obj.moveTo if index == 0 else path_obj.lineTo)(px, py)
        if fill:
            path_obj.close()
        canvas.drawPath(path_obj, stroke=1, fill=int(fill))


def _offset_linework(geometry: object, distance: float) -> object:
    """Apply a print-only cartographic offset without changing governed geometry."""
    if geometry.geom_type == "LineString":
        return geometry.offset_curve(distance)
    if geometry.geom_type == "MultiLineString":
        return MultiLineString([part.offset_curve(distance) for part in geometry.geoms])
    return geometry


def _validate_strategic_artifacts(
    output: Path, strategic: dict[str, object], geojson: dict[str, object]
) -> None:
    strategic_registry = (
        strategic.get("authoritative_features") if isinstance(strategic, dict) else None
    )
    if not isinstance(strategic, dict) or not isinstance(strategic_registry, list):
        raise ValueError("strategic publication manifest is malformed")
    record_payload = strategic.get("record")
    if not isinstance(record_payload, dict):
        raise ValueError("strategic publication record is malformed")
    record = StrategicReferencePublicationRecord.from_publication_payload(record_payload)
    if record.publication_payload() != record_payload:
        raise ValueError("strategic publication manifest is not a canonical round-trip")
    replay = strategic.get("replay")
    integrity = strategic.get("cross_artifact_integrity")
    if not isinstance(integrity, dict):
        raise ValueError("strategic cross-artifact integrity report is malformed")
    if not isinstance(replay, dict):
        raise ValueError("strategic replay composite is malformed")
    if replay.get("diagnostics") != record_payload.get("replay_diagnostics"):
        raise ValueError("strategic replay diagnostics differ from the publication record")
    plan = record.publication_payload()["application_plan"]
    projection = dict(strategic)
    projection.pop("cross_artifact_integrity", None)
    if integrity != _strategic_integrity_report(projection):
        raise ValueError("strategic cross-artifact integrity report differs from view")
    if strategic.get("alignment_options") != _strategic_alignment_options(plan):
        raise ValueError("strategic alignment options differ from governed scenario")
    bindings = plan.get("bindings", []) if isinstance(plan, dict) else []
    if (
        not isinstance(bindings, list)
        or not bindings
        or not all(isinstance(item, dict) and item.get("binding_fingerprint") for item in bindings)
    ):
        raise ValueError("strategic publication bindings are malformed")
    bindings_by_id = {str(item["binding_fingerprint"]): item for item in bindings}
    binding_ids = set(bindings_by_id)
    if len(bindings_by_id) != len(bindings):
        raise ValueError("strategic publication binding identities are not unique")
    replay_features = []
    for name, role in (
        ("interurban_connections", "interurban-spine"),
        ("destination_access_connections", "strategic-destination-access"),
    ):
        collection = replay.get(name)
        if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
            raise ValueError("strategic replay collection is malformed")
        features = collection.get("features")
        expected_role_bindings = {
            str(item.get("binding_fingerprint"))
            for item in bindings
            if isinstance(item, dict) and item.get("unit_role") == role
        }
        if not isinstance(features, list) or len(features) != len(expected_role_bindings):
            raise ValueError("strategic replay collection has incomplete bindings")
        for feature in features:
            properties = feature.get("properties", {}) if isinstance(feature, dict) else {}
            if properties.get("network_role") != role or not feature.get("geometry"):
                raise ValueError("strategic replay role or geometry is stale")
            for field in (
                "routing_edge_ids",
                "reverse_routing_edge_ids",
                "source_ids",
                "evidence_ids",
                "generation_strategies",
            ):
                if not isinstance(properties.get(field), list):
                    raise ValueError("strategic replay arrays are not typed")
            binding_id = str(properties.get("binding_id"))
            binding = bindings_by_id.get(binding_id)
            if binding is None:
                raise ValueError("strategic replay contains a foreign binding")
            if _strategic_binding_identity(properties) != _strategic_binding_identity(
                binding, application_plan=True
            ):
                raise ValueError("strategic replay properties differ from the application plan")
            for field in (
                "routing_edge_ids",
                "reverse_routing_edge_ids",
                "source_ids",
                "evidence_ids",
                "generation_strategies",
            ):
                if properties.get(field) != binding.get(field):
                    raise ValueError("strategic replay lineage differs from the application plan")
            endpoints = binding.get("endpoint_binding", {})
            if role == "interurban-spine":
                actual_endpoints = [
                    properties.get("from_network_place_id"),
                    properties.get("to_network_place_id"),
                ]
                expected_endpoints = endpoints.get("network_place_ids")
            else:
                actual_endpoints = [properties.get("from_network_place_id")]
                expected_endpoints = endpoints.get("network_place_ids")
            if actual_endpoints != expected_endpoints:
                raise ValueError("strategic replay endpoints differ from application plan")
            if role == "strategic-destination-access" and [
                properties.get("strategic_destination_id")
            ] != endpoints.get("strategic_destination_ids"):
                raise ValueError("strategic destination endpoints differ from application plan")
            for field in (
                "mandatory_network_place_ids",
                "mandatory_access_obligation_ids",
                "mandatory_strategic_destination_ids",
                "served_network_place_ids",
                "served_access_obligation_ids",
                "served_strategic_destination_ids",
                "endpoint_binding",
            ):
                if properties.get(field) != binding.get(field):
                    raise ValueError("strategic replay obligations differ from application plan")
            replay_features.append(feature)
    replay_binding_ids = [str(item["properties"].get("binding_id")) for item in replay_features]
    if set(replay_binding_ids) != binding_ids or len(replay_binding_ids) != len(binding_ids):
        raise ValueError("strategic replay binding coverage differs from record")
    if (
        len(strategic_registry) != len(bindings)
        or {str(item.get("binding_id")) for item in strategic_registry if isinstance(item, dict)}
        != binding_ids
        or not all(isinstance(item, dict) for item in strategic_registry)
    ):
        raise ValueError("strategic authoritative registry coverage differs from record")
    registry_by_binding = {str(item["binding_id"]): item for item in strategic_registry}
    destination_features = replay["destination_access_connections"]["features"]
    interurban_features = replay["interurban_connections"]["features"]
    typed_destinations = [
        feature
        for feature in geojson["features"]
        if feature["properties"].get("feature_type") == "strategic-destination-access-connection"
    ]
    top_spines = {
        str(feature["id"]): feature
        for feature in geojson["features"]
        if feature["properties"].get("feature_type") == "strategic-spine"
    }
    if any(
        feature["properties"].get("feature_type") == "strategic-interurban-connection"
        for feature in geojson["features"]
    ):
        raise ValueError("strategic interurban replay was duplicated in top-level GeoJSON")
    destination_registry_ids = {
        str(item["feature_id"])
        for item in strategic_registry
        if item.get("published_as") == "strategic-destination-access-connection"
    }
    if {str(item["id"]) for item in typed_destinations} != destination_registry_ids or len(
        typed_destinations
    ) != len(destination_features):
        raise ValueError("strategic destination access registry differs from GeoJSON")
    layers = set(gpd.list_layers(output / "network.gpkg")["name"])
    destination_layer = "strategic_destination_access_connections"
    if destination_features:
        if destination_layer not in layers:
            raise ValueError("GeoPackage is missing strategic destination access layer")
        gpkg = gpd.read_file(output / "network.gpkg", layer=destination_layer)
        if len(gpkg) != len(destination_features) or set(
            gpkg["strategic_connection_id"].astype(str)
        ) != {str(feature["id"]) for feature in destination_features}:
            raise ValueError("GeoPackage destination access coverage differs from replay")
    elif destination_layer in layers:
        raise ValueError("GeoPackage has an unbound strategic destination access layer")
    typed_by_id = {str(item["id"]): item for item in typed_destinations}
    for destination in destination_features:
        destination_id = str(destination["id"])
        properties = destination["properties"]
        binding_id = str(properties["binding_id"])
        registry = registry_by_binding.get(binding_id)
        top_destination = typed_by_id.get(destination_id)
        if (
            registry is None
            or registry.get("feature_id") != destination_id
            or registry.get("published_as") != "strategic-destination-access-connection"
            or top_destination is None
            or top_destination.get("geometry") != destination.get("geometry")
            or any(
                top_destination.get("properties", {}).get(field) != properties.get(field)
                for field in (
                    "binding_id",
                    "candidate_id",
                    "physical_alignment_id",
                    "geometry_fingerprint",
                    "network_role",
                )
            )
        ):
            raise ValueError("top-level destination access differs from replay")
        gpkg_rows = gpkg.loc[gpkg["strategic_connection_id"].astype(str) == destination_id]
        if len(gpkg_rows) != 1:
            raise ValueError("GeoPackage destination access identity is ambiguous")
        gpkg_row = gpkg_rows.iloc[0]
        for field in (
            "binding_id",
            "candidate_id",
            "physical_alignment_id",
            "geometry_fingerprint",
            "network_role",
        ):
            if str(gpkg_row[field]) != str(properties[field]):
                raise ValueError("GeoPackage destination properties differ from replay")
        projected = gpd.GeoSeries([gpkg_row.geometry], crs=gpkg.crs).to_crs(4326).iloc[0]
        replay_geometry = shape(destination["geometry"])
        if (
            projected.is_empty
            or replay_geometry.is_empty
            or projected.hausdorff_distance(replay_geometry) > 1e-8
            or not math.isclose(
                projected.length, replay_geometry.length, rel_tol=1e-8, abs_tol=1e-8
            )
        ):
            raise ValueError("GeoPackage destination geometry differs from replay")
    for interurban in interurban_features:
        properties = interurban["properties"]
        binding_id = str(properties["binding_id"])
        registry = registry_by_binding.get(binding_id)
        if (
            registry is None
            or registry.get("published_as") != "strategic-spine"
            or registry.get("feature_id") not in top_spines
        ):
            raise ValueError("strategic interurban registry has no published spine")
        spine = top_spines[str(registry["feature_id"])]
        replay_ids = spine["properties"].get("replay_binding_ids", [])
        if isinstance(replay_ids, str):
            try:
                replay_ids = json.loads(replay_ids)
            except json.JSONDecodeError as error:
                raise ValueError("published strategic spine binding list is malformed") from error
        if (
            not isinstance(replay_ids, list)
            or binding_id not in replay_ids
            or spine["properties"].get("physical_alignment_id")
            != properties.get("physical_alignment_id")
            or (
                spine["properties"].get("geometry_fingerprint") is not None
                and spine["properties"].get("geometry_fingerprint")
                != properties.get("geometry_fingerprint")
            )
        ):
            raise ValueError("published strategic spine differs from replay binding")
        spine_geometry = shape(spine["geometry"])
        replay_geometry = shape(interurban["geometry"])
        if spine_geometry.hausdorff_distance(replay_geometry) > 1e-8 or not math.isclose(
            spine_geometry.length,
            replay_geometry.length,
            rel_tol=1e-8,
            abs_tol=1e-8,
        ):
            raise ValueError("published strategic spine geometry differs from replay")
    for registry in strategic_registry:
        feature = next(
            (
                item
                for item in replay_features
                if item["properties"].get("binding_id") == registry.get("binding_id")
            ),
            None,
        )
        if feature is None or any(
            feature["properties"].get(name) != registry.get(name)
            for name in (
                "candidate_id",
                "physical_alignment_id",
                "geometry_fingerprint",
                "network_role",
            )
        ):
            raise ValueError("strategic registry differs from replay binding")
    data_text = (output / "review-map" / "data.js").read_text(encoding="utf-8")
    prefix = "window.SATN_DATA = "
    if not data_text.startswith(prefix) or not data_text.rstrip().endswith(";"):
        raise ValueError("review-map data is not a SATN JSON assignment")
    data = json.loads(data_text[len(prefix) :].strip().removesuffix(";"))
    if data.get("strategic_reference") != strategic:
        raise ValueError("review-map strategic composite differs from run")
    strategic_assets = (
        output / "review-map" / "assets" / "strategic-reference.css",
        output / "review-map" / "assets" / "strategic-reference.js",
    )
    if not all(path.is_file() for path in strategic_assets):
        raise ValueError("strategic review map is missing its presentation assets")
    review_html = (output / "review-map" / "index.html").read_text(encoding="utf-8")
    if (
        "strategic-reference.css" not in review_html
        or "strategic-reference.js" not in review_html
        or "Strategic Reference review" not in review_html
    ):
        raise ValueError("strategic review map omits its accessible evidence")


def _validate_artifacts(output: Path, config: AreaConfig) -> None:
    required = (
        "network.gpkg",
        "network.geojson",
        "asset-accounting.json",
        "asset-accounting.geojson",
        "run.json",
        "agent-records.json",
        "divergence-records.json",
        "human-intervention-requests.json",
        "backbone-comparison.json",
        "review-map/index.html",
        "review-map/data.js",
        "review-map/network.geojson",
        "review-map/asset-accounting.json",
        "review-map/asset-accounting.geojson",
        "review-map/agent-records.json",
        "review-map/assets/maplibre-gl.js",
        "review-map/assets/maplibre-gl.css",
        "review-map/assets/review-map.js",
        "review-map/assets/review-map.css",
        "review-map/backbone-comparison.json",
        "review-map.zip",
        "network-map.pdf",
    )
    missing = [name for name in required if not (output / name).exists()]
    if missing:
        raise ValueError(f"publication incomplete: {', '.join(missing)}")
    expected_top_level = {Path(name).parts[0] for name in required} | {OWNER_MARKER_NAME}
    unexpected = sorted(
        path.name for path in output.iterdir() if path.name not in expected_top_level
    )
    if unexpected:
        raise ValueError(f"publication contains unexpected artifacts: {', '.join(unexpected)}")
    metadata = gpd.read_file(output / "network.gpkg", layer="metadata")
    if set(metadata["disclaimer"]) != {DISCLAIMER}:
        raise ValueError("GeoPackage metadata disclaimer mismatch")
    geojson = json.loads((output / "network.geojson").read_text(encoding="utf-8"))
    if geojson.get("disclaimer") != DISCLAIMER:
        raise ValueError("GeoJSON disclaimer mismatch")
    accounting = json.loads((output / "asset-accounting.json").read_text(encoding="utf-8"))
    accounting_geojson_payload = json.loads(
        (output / "asset-accounting.geojson").read_text(encoding="utf-8")
    )
    if (
        accounting.get("disclaimer") != DISCLAIMER
        or accounting.get("contract") != "satn-asset-accounting/v1"
        or accounting.get("asset_count") != len(accounting.get("records", []))
    ):
        raise ValueError("asset accounting manifest is malformed")
    if (
        accounting_geojson_payload.get("type") != "FeatureCollection"
        or accounting_geojson_payload.get("disclaimer") != DISCLAIMER
    ):
        raise ValueError("asset accounting GeoJSON is malformed")
    accounting_ids = {
        str(record.get("asset_id"))
        for record in accounting.get("records", [])
        if isinstance(record, dict)
    }
    accounting_geojson_ids = {
        str(feature.get("id"))
        for feature in accounting_geojson_payload.get("features", [])
        if isinstance(feature, dict)
    }
    if accounting_ids != accounting_geojson_ids:
        raise ValueError("asset accounting JSON and GeoJSON identities differ")
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    if run.get("disclaimer") != DISCLAIMER or run.get("network_model") != "backbone-outward":
        raise ValueError("run manifest does not describe the current publication")
    accounting_manifest = run.get("asset_accounting", {})
    if accounting_manifest.get("asset_count") != len(accounting_ids) or accounting_manifest.get(
        "excluded_observation_count"
    ) != len(accounting.get("excluded_observations", [])):
        raise ValueError("run manifest asset accounting count mismatch")
    strategic = run.get("strategic_reference")
    if strategic is not None:
        _validate_strategic_artifacts(output, strategic, geojson)
    else:
        if any(
            feature["properties"].get("feature_type") == "strategic-destination-access-connection"
            for feature in geojson["features"]
        ):
            raise ValueError("ordinary publication contains strategic destination access")
        if "strategic_destination_access_connections" in set(
            gpd.list_layers(output / "network.gpkg")["name"]
        ):
            raise ValueError("ordinary GeoPackage contains a strategic replay layer")
        if any(
            (output / "review-map" / "assets" / name).exists()
            for name in ("strategic-reference.css", "strategic-reference.js")
        ):
            raise ValueError("ordinary publication contains strategic presentation assets")
    try:
        input_ledger = canonical_decision_ledger_payload(run["decision_ledger_input"])
        accepted_ledger = canonical_decision_ledger_payload(
            {
                "decision_contract": run["decision_contract"],
                "responses": run["accepted_decisions"],
            }
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("run manifest has an invalid decision provenance contract") from error
    if input_ledger.decision_contract != run["decision_contract"] or (
        accepted_ledger.model_dump(mode="json")["responses"] != run["accepted_decisions"]
    ):
        raise ValueError("run manifest has a non-canonical decision provenance contract")
    authoritative_count = sum(
        feature["properties"].get("feature_type")
        in {
            "spine-access-connection",
            "school-access-connection",
            "branch-meeting-connection",
        }
        for feature in geojson["features"]
    )
    if run.get("connection_count") != authoritative_count:
        raise ValueError("authoritative connection count differs between artifacts")
    authoritative_types = {
        "spine-access-connection",
        "school-access-connection",
        "branch-meeting-connection",
        "cross-spine-connector",
    }
    geojson_registry = _unique_authoritative_feature_registry(
        [
            (str(feature["id"]), str(feature["properties"]["network_role"]))
            for feature in geojson["features"]
            if feature["properties"].get("feature_type") in authoritative_types
        ],
        "GeoJSON",
    )
    run_registry = _unique_authoritative_feature_registry(
        [
            (str(record["feature_id"]), str(record["network_role"]))
            for record in run.get("authoritative_features", [])
        ],
        "run manifest",
    )
    if run_registry != geojson_registry:
        raise ValueError("authoritative feature identifiers or roles differ in run manifest")
    spatial_layer_names = set(gpd.list_layers(output / "network.gpkg")["name"])
    geopackage_entries: list[tuple[str, str]] = []
    geopackage_decisions: dict[str, dict[str, object]] = {}
    if "spine_access_connections" in spatial_layer_names:
        access_rows = gpd.read_file(output / "network.gpkg", layer="spine_access_connections")
        geopackage_entries.extend(
            zip(
                access_rows["access_connection_id"].astype(str),
                access_rows["network_role"].astype(str),
                strict=True,
            )
        )
        geopackage_decisions.update(
            {
                str(row.agent_decision_request_id): row._asdict()
                for row in access_rows.itertuples()
                if pd.notna(row.agent_decision_request_id)
            }
        )
    if "branch_meeting_connections" in spatial_layer_names:
        meeting_rows = gpd.read_file(output / "network.gpkg", layer="branch_meeting_connections")
        geopackage_entries.extend(
            zip(
                meeting_rows["meeting_connection_id"].astype(str),
                meeting_rows["network_role"].astype(str),
                strict=True,
            )
        )
        geopackage_decisions.update(
            {
                str(row.agent_decision_request_id): row._asdict()
                for row in meeting_rows.itertuples()
                if pd.notna(row.agent_decision_request_id)
            }
        )
    if "gaps" in spatial_layer_names:
        gap_rows = gpd.read_file(output / "network.gpkg", layer="gaps")
        geopackage_decisions.update(
            {
                str(row.agent_decision_request_id): row._asdict()
                for row in gap_rows.itertuples()
                if pd.notna(row.agent_decision_request_id)
            }
        )
    if "cross_spine_connectors" in spatial_layer_names:
        connector_rows = gpd.read_file(output / "network.gpkg", layer="cross_spine_connectors")
        geopackage_entries.extend(
            zip(
                connector_rows["cross_spine_connector_id"].astype(str),
                connector_rows["network_role"].astype(str),
                strict=True,
            )
        )
    geopackage_registry = _unique_authoritative_feature_registry(
        geopackage_entries,
        "GeoPackage",
    )
    if geopackage_registry != geojson_registry:
        raise ValueError("authoritative feature identifiers or roles differ in GeoPackage")
    agent_payload = json.loads((output / "agent-records.json").read_text(encoding="utf-8"))
    agent_records = [AgentRecord.model_validate(record) for record in agent_payload["records"]]
    divergence_payload = json.loads(
        (output / "divergence-records.json").read_text(encoding="utf-8")
    )
    divergence_records = [
        DivergenceRecord.model_validate(record) for record in divergence_payload["records"]
    ]
    expected_review_summary = _agent_review_summary(
        config,
        [*agent_records, *divergence_records],
    )
    if run.get("agent_review") != expected_review_summary:
        raise ValueError("agent review summary differs from decision records")
    bounded_choice_records = [
        record
        for record in [*agent_records, *divergence_records]
        if record.responder_mode in {"caller", "direct-runtime"}
    ]
    accepted_decisions = AgentDecisionLedger.model_validate(
        {
            "decision_contract": run["decision_contract"],
            "responses": [
                {
                    "request_id": record.decision_request.request_id,
                    "dependency_fingerprint": record.decision_request.dependency_fingerprint,
                    "choice_id": record.selected_choice_id,
                }
                for record in bounded_choice_records
                if record.decision_request is not None
            ],
        }
    ).model_dump(mode="json")["responses"]
    if run.get("decision_contract") != "agent-decision-menu/v1":
        raise ValueError("run manifest decision contract is unsupported")
    if run.get("accepted_decisions") != accepted_decisions:
        raise ValueError("run manifest accepted choices differ from decision records")
    # Publications generated before runtime governance existed are retained as
    # legacy, unclassified artefacts.  A newly present manifest is an exact
    # trust boundary and must never be accepted after a provider/model/ledger
    # claim has been altered.
    if "runtime_governance" in run:
        runtime_governance = run["runtime_governance"]
        if not isinstance(runtime_governance, dict):
            raise ValueError("run manifest runtime governance must be an object")
        validate_runtime_governance(
            runtime_governance,
            config.compilation.agent,
            [*agent_records, *divergence_records],
            decision_ledger_input=run["decision_ledger_input"],
            accepted_decisions=run["accepted_decisions"],
        )
    public_features = geojson.get("features")
    if not isinstance(public_features, list):
        raise ValueError("GeoJSON has no feature collection for withheld connector validation")
    validate_cross_spine_publication(
        agent_records,
        public_features,
        geojson_registry,
    )
    agent_registry = _accepted_agent_authoritative_feature_registry(
        agent_records,
    )
    if agent_registry != geojson_registry:
        raise ValueError("authoritative feature identifiers or roles differ in agent records")
    review_agent_payload = json.loads(
        (output / "review-map" / "agent-records.json").read_text(encoding="utf-8")
    )
    if review_agent_payload != agent_payload:
        raise ValueError("review map agent records differ from publication audit records")
    review_network = json.loads(
        (output / "review-map" / "network.geojson").read_text(encoding="utf-8")
    )
    review_registry = _unique_authoritative_feature_registry(
        [
            (str(feature["id"]), str(feature["properties"]["network_role"]))
            for feature in review_network["features"]
            if feature["properties"].get("feature_type") in authoritative_types
        ],
        "review-map GeoJSON",
    )
    if review_registry != geojson_registry:
        raise ValueError("authoritative feature identifiers or roles differ in review map")
    review_features = review_network.get("features")
    if not isinstance(review_features, list):
        raise ValueError(
            "review-map GeoJSON has no feature collection for withheld connector validation"
        )
    validate_cross_spine_publication(
        agent_records,
        public_features,
        geojson_registry,
        review_features,
    )
    geojson_decisions = {
        str(feature["properties"]["agent_decision_request_id"]): feature["properties"]
        for feature in geojson["features"]
        if feature["properties"].get("agent_decision_request_id")
    }
    review_decisions = {
        str(feature["properties"]["agent_decision_request_id"]): feature["properties"]
        for feature in review_network["features"]
        if feature["properties"].get("agent_decision_request_id")
    }
    for record in bounded_choice_records:
        if record.decision_request is None or record.mapped_action is None:
            raise ValueError("bounded choice record omits its request or mapped action")
        request_id = record.decision_request.request_id
        if request_id not in geojson_decisions:
            if isinstance(record, AgentRecord) and record.decision == "accept":
                raise ValueError("accepted bounded choice is absent from spatial artifacts")
            continue
        expected = {
            "agent_decision_request_id": request_id,
            "agent_decision_choice_id": record.selected_choice_id,
            "agent_decision_action": record.mapped_action.kind,
            "agent_decision_responder_mode": record.responder_mode,
        }
        for name, value in expected.items():
            if geojson_decisions[request_id].get(name) != value:
                raise ValueError("GeoJSON decision audit differs from decision record")
            if review_decisions.get(request_id, {}).get(name) != value:
                raise ValueError("review map decision audit differs from decision record")
            if geopackage_decisions.get(request_id, {}).get(name) != value:
                raise ValueError("GeoPackage decision audit differs from decision record")
    layer_types = {
        "strategic_spines": ("strategic-spine",),
        "access_obligations": ("access-obligation", "school-access-obligation"),
        "spine_access_connections": (
            "spine-access-connection",
            "school-access-connection",
        ),
        "spine_access_branches": ("spine-access-branch",),
        "branch_meeting_connections": ("branch-meeting-connection",),
        "cross_spine_connectors": ("cross-spine-connector",),
        "gaps": ("gap", "school-access-gap"),
        "a_road_spines": ("a-road-spine",),
        "ncn_routes": (
            "ncn-route",
            "ncn-link",
            "declassified-ncn-route",
            "greenway-cycleway",
        ),
        "urban_spines": ("urban-spine",),
        "urban_classification_unknowns": ("urban-classification-unknown",),
        "candidate_low_traffic_areas": ("low-traffic-area",),
        "low_traffic_area_portals": ("low-traffic-area-portal",),
        "schools": ("school",),
        "school_street_assessments": ("school-street-assessment",),
        "topography_profiles": ("topography-profile",),
        "gradient_sections": ("gradient-section",),
        "population_display_sections": ("population-display-section",),
        "elevation_corroboration": ("elevation-corroboration",),
        "retail_centres": ("retail-centre",),
        "healthcare": ("healthcare",),
    }
    for layer_name, feature_types in layer_types.items():
        expected_count = run.get("layer_counts", {}).get(layer_name, 0)
        actual_count = sum(
            feature["properties"].get("feature_type") in feature_types
            for feature in geojson["features"]
        )
        if actual_count != expected_count:
            raise ValueError(f"{layer_name} count differs between run and GeoJSON")
        if expected_count and layer_name not in spatial_layer_names:
            raise ValueError(f"GeoPackage is missing populated layer: {layer_name}")
    profile_features = {
        feature["id"]: feature
        for feature in geojson["features"]
        if feature["properties"].get("feature_type") == "topography-profile"
    }
    if profile_features:
        profiles = gpd.read_file(output / "network.gpkg", layer="topography_profiles")
        profile_rows = profiles.set_index("profile_id", drop=False)
        if set(profile_features) != set(profile_rows.index):
            raise ValueError("Topography Profile identifiers differ between artifacts")
        for profile_id, feature in profile_features.items():
            row = profile_rows.loc[profile_id]
            properties = feature["properties"]
            for field in (
                "edge_id",
                "edge_type",
                "evidence_status",
                "evidence_rationale",
                "distance_m",
                "forward_ascent_m",
                "forward_descent_m",
                "reverse_ascent_m",
                "reverse_descent_m",
                "steepest_sustained_gradient_pct",
                "steepest_sustained_gradient_rationale",
                "gradient_section_ids",
                "elevation_evidence_ids",
                "elevation_source_ids",
            ):
                if not _artifact_values_equal(properties.get(field), row[field]):
                    raise ValueError(f"Topography Profile {profile_id} differs for {field}")
        generated_edge_types = {
            "strategic-spine",
            "spine-access-connection",
            "school-access-connection",
            "branch-meeting-connection",
            "cross-spine-connector",
            "urban-spine",
        }
        for feature in geojson["features"]:
            if feature["properties"].get("feature_type") not in generated_edge_types:
                continue
            profile_id = feature["properties"].get("topography_profile_id")
            profile = profile_features.get(profile_id)
            if profile is None or profile["properties"].get("edge_id") != feature["id"]:
                raise ValueError(
                    f"generated edge {feature['id']} has inconsistent Topography Profile"
                )
    topography_run = run.get("topography", {})
    if topography_run.get("profile_count") != len(profile_features):
        raise ValueError("Topography Profile count differs between run and GeoJSON")
    unavailable_count = sum(
        feature["properties"].get("evidence_status") == "evidence-unavailable"
        for feature in profile_features.values()
    )
    if topography_run.get("evidence_unavailable_count") != unavailable_count:
        raise ValueError("Topography evidence-unavailable count differs between artifacts")
    section_features = {
        feature["id"]: feature
        for feature in geojson["features"]
        if feature["properties"].get("feature_type") == "gradient-section"
    }
    if topography_run.get("gradient_section_count") != len(section_features):
        raise ValueError("Gradient Section count differs between run and GeoJSON")
    if section_features:
        sections = gpd.read_file(output / "network.gpkg", layer="gradient_sections")
        section_rows = sections.set_index("section_id", drop=False)
        if set(section_features) != set(section_rows.index):
            raise ValueError("Gradient Section identifiers differ between artifacts")
        for section_id, feature in section_features.items():
            row = section_rows.loc[section_id]
            for field in (
                "profile_id",
                "edge_id",
                "edge_type",
                "start_distance_m",
                "end_distance_m",
                "length_m",
                "forward_gradient_pct",
                "absolute_gradient_pct",
                "gradient_band",
                "uphill_direction",
                "sustained",
                "sustained_rationale",
                "elevation_evidence_ids",
            ):
                if not _artifact_values_equal(feature["properties"].get(field), row[field]):
                    raise ValueError(f"Gradient Section {section_id} differs for {field}")
    population_display_features = {
        feature["id"]: feature
        for feature in geojson["features"]
        if feature["properties"].get("feature_type") == "population-display-section"
    }
    if population_display_features:
        population_sections = gpd.read_file(
            output / "network.gpkg",
            layer="population_display_sections",
        )
        population_rows = population_sections.set_index("section_id", drop=False)
        if set(population_display_features) != set(population_rows.index):
            raise ValueError("Population Display Section identifiers differ between artifacts")
        for section_id, feature in population_display_features.items():
            row = population_rows.loc[section_id]
            for field in (
                "candidate_group_id",
                "alignment_id",
                "section_order",
                "start_distance_m",
                "end_distance_m",
                "length_m",
                "alignment_length_m",
                "network_scope",
                "capture_radius_m",
                "total_residents",
                "inside_area_residents",
                "outside_area_residents",
                "captured_oa_ids",
            ):
                published_value = row[field]
                if field == "captured_oa_ids" and isinstance(published_value, str):
                    try:
                        published_value = json.loads(published_value)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            f"Population Display Section {section_id} has invalid captured OA IDs"
                        ) from error
                if not _artifact_values_equal(feature["properties"].get(field), published_value):
                    raise ValueError(
                        f"Population Display Section {section_id} differs for {field}"
                    )
    for filename in (
        "agent-records.json",
        "divergence-records.json",
        "human-intervention-requests.json",
    ):
        record_file = json.loads((output / filename).read_text(encoding="utf-8"))
        if record_file.get("disclaimer") != DISCLAIMER:
            raise ValueError(f"{filename} disclaimer mismatch")
    comparison = json.loads((output / "backbone-comparison.json").read_text(encoding="utf-8"))
    if (
        comparison.get("disclaimer") != DISCLAIMER
        or comparison.get("comparison_role") != "superseded-reference-not-ground-truth"
    ):
        raise ValueError("backbone comparison governance metadata mismatch")
    html = (output / "review-map" / "index.html").read_text(encoding="utf-8")
    if DISCLAIMER not in html:
        raise ValueError("review map disclaimer missing")
    for control in (
        "layer-strategic-network",
        "layer-spine-access-connections",
        "layer-cross-spine-connectors",
        "layer-urban-spines",
        "layer-low-traffic-areas",
        "layer-schools",
        "layer-school-streets",
        "layer-gradient-sections",
        "layer-population-display-sections",
        "layer-retail-centres",
        "layer-healthcare",
        "layer-atm",
        "atm-upload",
    ):
        if f'id="{control}"' not in html:
            raise ValueError(f"review map control missing: {control}")
    _validate_review_map_zip(output / "review-map.zip", output / "review-map")
    if not (output / "network-map.pdf").read_bytes().startswith(b"%PDF"):
        raise ValueError("invalid PDF output")
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(output / "network-map.pdf").pages
    )
    for required_text in (
        config.publication.title,
        DISCLAIMER,
        "Legend",
        "scale",
        "Compiled",
    ):
        if required_text not in pdf_text:
            raise ValueError(f"PDF is missing required text: {required_text}")
    for connection_id, network_role in geojson_registry.items():
        if f"{connection_id} | {network_role}" not in pdf_text:
            raise ValueError(
                f"PDF edge register differs for authoritative feature: {connection_id}"
            )


def _validate_review_map_zip(archive_path: Path, review_directory: Path) -> None:
    """Require the portable map archive to be a safe byte-for-byte copy."""
    expected = {
        f"review-map/{item.relative_to(review_directory).as_posix()}": item
        for item in review_directory.rglob("*")
        if item.is_file()
    }
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise ValueError("review-map ZIP differs from the static directory")
            for info in infos:
                pure = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                expected_path = expected[info.filename]
                if (
                    info.is_dir()
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or not pure.parts
                    or (mode and stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                ):
                    raise ValueError("review-map ZIP contains an unsafe member")
                if info.file_size != expected_path.stat().st_size:
                    raise ValueError(
                        "review-map ZIP member size differs from static map"
                    )
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size > REVIEW_MAP_ZIP_MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("review-map ZIP exceeds compression ratio budget")
            for info in infos:
                with archive.open(info) as archived, expected[info.filename].open("rb") as static:
                    while True:
                        archived_chunk = archived.read(64 * 1024)
                        static_chunk = static.read(64 * 1024)
                        if archived_chunk != static_chunk:
                            raise ValueError("review-map ZIP member bytes differ from static map")
                        if not archived_chunk:
                            break
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("review-map ZIP is invalid") from error


def _unique_authoritative_feature_registry(
    entries: list[tuple[str, str]],
    artifact_name: str,
) -> dict[str, str]:
    """Return an exact feature registry without silently collapsing duplicates."""
    registry: dict[str, str] = {}
    for feature_id, network_role in entries:
        if not feature_id.strip():
            raise ValueError(f"{artifact_name} authoritative feature has a blank identifier")
        existing_role = registry.get(feature_id)
        if existing_role is not None:
            if existing_role != network_role:
                raise ValueError(
                    f"{artifact_name} authoritative feature has conflicting identifier roles: "
                    f"{feature_id}"
                )
            raise ValueError(
                f"{artifact_name} authoritative feature has duplicate identifier: {feature_id}"
            )
        registry[feature_id] = network_role
    return registry


def _accepted_agent_authoritative_feature_registry(
    records: list[AgentRecord],
) -> dict[str, str]:
    """Prove each accepted direct or derived feature appears in the registry once."""
    entries: list[tuple[str, str]] = []
    for record in records:
        if record.decision != "accept":
            continue
        entries.append((str(record.connection_id), str(record.network_role)))
        entries.extend(
            (str(reference.feature_id), str(reference.network_role))
            for reference in record.derived_features
        )
    return _unique_authoritative_feature_registry(entries, "accepted agent records")
