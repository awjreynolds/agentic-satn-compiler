"""Atomic publication of spatial, machine-readable and visual artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shlex
import shutil
import tempfile
import zipfile
from datetime import UTC, date, datetime
from html import escape
from importlib.resources import files
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A2, A3, A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from shapely.geometry import MultiLineString, mapping, shape

from satn.compiler import CompiledNetwork
from satn.constants import DISCLAIMER, SCHEMA_VERSION
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
from satn.reference_application import ReferenceSATNPublicationRecord
from satn.runtime_governance import classify_runtime_governance, validate_runtime_governance
from satn.sources import (
    EA_LIDAR_WECA_ACQUISITION_CONTRACT,
    EA_RETAINED_ROUTE_FILENAME,
    ELEVATION_EVIDENCE_FILENAME,
    NCN_ATTRIBUTION,
    OSM_ATTRIBUTION,
    _validate_canonical_retained_ea_evidence,
)

LOGGER = logging.getLogger(__name__)

EA_FIXED_POINT_CANDIDATE_SCHEMA_VERSION = "ea-fixed-point-candidate/v1"
EA_FIXED_POINT_CANDIDATE_DIRECTORY = ".satn-ea-fixed-point-candidates"
EA_FIXED_POINT_CANDIDATE_NETWORK = "network.geojson"
EA_FIXED_POINT_CANDIDATE_STATUS = "status.json"


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


def publication_artifacts(output: Path) -> dict[str, Path]:
    """Return the stable artifact contract for a validated publication directory."""
    return {
        "geopackage": output / "network.gpkg",
        "geojson": output / "network.geojson",
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
) -> dict[str, Path]:
    output = config.publication.output_dir
    LOGGER.info("Publication started temporary_parent=%s", output.parent)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        _write_geopackage(temporary / "network.gpkg", compiled)
        _write_geojson(temporary / "network.geojson", compiled)
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
        _write_json_records(temporary, config, compiled, run_id)
        _write_backbone_comparison(
            temporary / "backbone-comparison.json",
            compiled,
            config.publication.comparison_reference or output,
        )
        review = temporary / "review-map"
        review.mkdir()
        _write_review_map(review, config, compiled)
        _zip_review_map(temporary / "review-map.zip", review)
        _write_pdf(temporary / "network-map.pdf", config, compiled)
        _validate_artifacts(temporary, config)
        LOGGER.info("Publication artifacts validated temporary=%s", temporary)
        backup = output.with_name(f".{output.name}-previous")
        if backup.exists():
            shutil.rmtree(backup)
        if output.exists():
            output.replace(backup)
        try:
            temporary.replace(output)
        except Exception:
            if backup.exists() and not output.exists():
                backup.replace(output)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)
            LOGGER.info("Publication atomically replaced output=%s", output)
            if _uses_ea_lidar_weca_fixed_point(config):
                _remove_ea_fixed_point_candidate(config)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return publication_artifacts(output)


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
            for actual, pinned in zip(
                actual_extent, WECA_PINNED_ELIGIBLE_ROUTE_BBOX, strict=True
            )
        ):
            return _ea_fixed_point_repin_required(
                "candidate-extent-or-request-differs-from-pinned-survey-index"
            )
        if governed_survey_request_bbox(
            routes, routing_buffer_m=WECA_ROUTING_BUFFER_M
        ) != tuple(int(value) for value in WECA_SURVEY_REQUEST_BBOX):
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
    authority_boundaries = snapshot / "ea-authority-boundaries.geojson"
    survey_index = snapshot / "ea-survey-index.geojson"
    if any(
        not path.is_file() or path.is_symlink()
        for path in (authority_boundaries, survey_index)
    ):
        return _ea_fixed_point_repin_required("candidate-missing-pinned-survey-inputs")
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
        "elevation-corroboration": "corroboration_id",
        "spine-access-connection": "access_connection_id",
        "school-access-connection": "access_connection_id",
        "spine-access-branch": "branch_id",
        "branch-meeting-connection": "meeting_connection_id",
        "cross-spine-connector": "cross_spine_connector_id",
        "low-traffic-area-portal": "portal_id",
        "strategic-spine": "spine_id",
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


def _layer_counts(compiled: CompiledNetwork) -> dict[str, int]:
    return {
        "strategic_spines": len(compiled.strategic_spines),
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
        "elevation_corroboration": len(compiled.elevation_corroboration),
        "retail_centres": len(compiled.retail_centres),
        "healthcare": len(compiled.healthcare),
    }


def _write_json_records(
    output: Path,
    config: AreaConfig,
    compiled: CompiledNetwork,
    run_id: str,
) -> None:
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
        "network_units": compiled.network_units,
        "superseded_hypotheses": compiled.superseded_hypotheses,
        "atm_mode": config.atm.mode if config.atm.enabled else "disabled",
        "atm_geometry_included": compiled.atm_reference is not None,
        "disclaimer": DISCLAIMER,
    }
    if compiled.reference_satn_publication is not None:
        run["reference_satn"] = compiled.reference_satn_publication.model_dump(mode="json")
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
    features: list[dict[str, object]] = []
    for candidate_set in sorted(
        reference.scenario.candidate_sets,
        key=lambda item: item.candidate_set_id,
    ):
        selection = selections[candidate_set.candidate_set_id]
        criteria = selection.criteria.model_dump(mode="json")
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
                        "network_role": str(candidate.network_role),
                        "source_class": str(candidate.source_class),
                        "directness_m": candidate.directness_m,
                        "maximum_gradient_pct": candidate.maximum_gradient_pct,
                        "evidence_fingerprints": list(candidate.evidence_fingerprints),
                        "criteria": criteria,
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


def _write_review_map(
    review: Path,
    config: AreaConfig,
    compiled: CompiledNetwork,
) -> None:
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
    if compiled.reference_satn_publication is not None:
        data["reference_satn"] = compiled.reference_satn_publication.model_dump(mode="json")
        data["reference_satn_options"] = _reference_option_collection(
            compiled.reference_satn_publication
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


def _validate_artifacts(output: Path, config: AreaConfig) -> None:
    required = (
        "network.gpkg",
        "network.geojson",
        "run.json",
        "agent-records.json",
        "divergence-records.json",
        "human-intervention-requests.json",
        "backbone-comparison.json",
        "review-map/index.html",
        "review-map/data.js",
        "review-map/network.geojson",
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
    expected_top_level = {Path(name).parts[0] for name in required}
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
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    if run.get("disclaimer") != DISCLAIMER or run.get("network_model") != "backbone-outward":
        raise ValueError("run manifest does not describe the current publication")
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
        "layer-retail-centres",
        "layer-healthcare",
        "layer-atm",
        "atm-upload",
    ):
        if f'id="{control}"' not in html:
            raise ValueError(f"review map control missing: {control}")
    expected_zip_files = {
        f"review-map/{item.relative_to(output / 'review-map')}"
        for item in (output / "review-map").rglob("*")
        if item.is_file()
    }
    with zipfile.ZipFile(output / "review-map.zip") as archive:
        if set(archive.namelist()) != expected_zip_files:
            raise ValueError("review-map ZIP differs from the static directory")
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
