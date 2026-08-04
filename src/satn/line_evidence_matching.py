"""Provider-neutral matching and aggregation of governed line evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from pyproj import CRS
from shapely import wkt
from shapely.geometry import LineString

from satn.evidence_contracts import evidence_fingerprint, evidence_geometry_fingerprint


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _sha256(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _crs_identity(value: object) -> str:
    try:
        crs = CRS.from_user_input(value)
    except Exception as error:
        raise ValueError("line evidence requires an explicit valid CRS") from error
    authority = crs.to_authority()
    if authority is not None:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_wkt(version="WKT2_2019", pretty=False)


def _millimetres(value_m: float) -> int:
    if not math.isfinite(value_m) or value_m < 0:
        raise ValueError("line measurements must be finite and non-negative")
    return round(value_m * 1_000)


def _millionths(value: float) -> int:
    return round(max(0.0, min(1.0, value)) * 1_000_000)


def _input_line_fingerprint(
    *,
    kind: str,
    record_id: str,
    geometry_wkt: str,
    geometry_crs: str,
    record_evidence_fingerprint: str,
) -> str:
    return evidence_fingerprint(
        {
            "contract": "satn-input-line-identity/v1",
            "kind": kind,
            "record_id": record_id,
            "geometry_wkt": geometry_wkt,
            "geometry_crs": geometry_crs,
            "evidence_fingerprint": record_evidence_fingerprint,
        }
    )


@dataclass(frozen=True)
class LineEvidenceRecord:
    source_id: str
    geometry_wkt: str
    geometry_crs: str
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _required_text(self.source_id, "source id")
        _required_text(self.geometry_wkt, "source geometry WKT")
        object.__setattr__(self, "geometry_crs", _crs_identity(self.geometry_crs))
        _sha256(self.evidence_fingerprint, "source evidence fingerprint")


@dataclass(frozen=True)
class TargetLineRecord:
    target_id: str
    geometry_wkt: str
    geometry_crs: str
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _required_text(self.target_id, "target id")
        _required_text(self.geometry_wkt, "target geometry WKT")
        object.__setattr__(self, "geometry_crs", _crs_identity(self.geometry_crs))
        _sha256(self.evidence_fingerprint, "target evidence fingerprint")


@dataclass(frozen=True)
class LineMatchProfile:
    profile_id: str
    version: int
    canonical_crs: str
    distance_tolerance_m: float
    bearing_tolerance_degrees: float
    minimum_shared_length_m: float
    orientation_policy: Literal["insensitive", "same-direction"]
    ambiguity_policy: Literal["retain-conflict"]

    def __post_init__(self) -> None:
        _required_text(self.profile_id, "profile id")
        if self.version < 1:
            raise ValueError("profile version must be positive")
        object.__setattr__(self, "canonical_crs", _crs_identity(self.canonical_crs))
        if self.canonical_crs != "EPSG:27700":
            raise ValueError("line matching canonical CRS must be EPSG:27700")
        if self.orientation_policy not in {"insensitive", "same-direction"}:
            raise ValueError("orientation policy must be insensitive or same-direction")
        if self.ambiguity_policy != "retain-conflict":
            raise ValueError("ambiguity policy must retain conflicts")
        if not 0 <= self.bearing_tolerance_degrees <= 90:
            raise ValueError("bearing tolerance must be between 0 and 90 degrees")
        object.__setattr__(
            self,
            "distance_tolerance_m",
            _millimetres(self.distance_tolerance_m) / 1_000,
        )
        object.__setattr__(
            self,
            "minimum_shared_length_m",
            _millimetres(self.minimum_shared_length_m) / 1_000,
        )
        object.__setattr__(
            self,
            "bearing_tolerance_degrees",
            round(self.bearing_tolerance_degrees * 1_000) / 1_000,
        )

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(
            {
                "contract": "satn-line-match-profile/v1",
                "profile_id": self.profile_id,
                "version": self.version,
                "canonical_crs": self.canonical_crs,
                "distance_tolerance_mm": _millimetres(self.distance_tolerance_m),
                "bearing_tolerance_millidegrees": round(self.bearing_tolerance_degrees * 1_000),
                "minimum_shared_length_mm": _millimetres(self.minimum_shared_length_m),
                "orientation_policy": self.orientation_policy,
                "ambiguity_policy": self.ambiguity_policy,
            }
        )


@dataclass(frozen=True)
class LineMatchRecord:
    source_id: str
    target_id: str | None
    state: Literal["accepted", "ambiguous", "conflicting", "unmatched"]
    distance_mm: int
    bearing_difference_millidegrees: int
    source_length_mm: int
    target_length_mm: int
    shared_length_mm: int
    source_coverage_millionths: int
    target_coverage_millionths: int
    orientation: Literal["same", "reversed", "not-applicable"]
    reason: str
    source_geometry_fingerprint: str | None
    target_geometry_fingerprint: str | None
    source_evidence_fingerprint: str
    target_evidence_fingerprint: str | None
    profile_fingerprint: str

    @property
    def distance_m(self) -> float:
        return self.distance_mm / 1_000

    @property
    def bearing_difference_degrees(self) -> float:
        return self.bearing_difference_millidegrees / 1_000

    @property
    def source_length_m(self) -> float:
        return self.source_length_mm / 1_000

    @property
    def target_length_m(self) -> float:
        return self.target_length_mm / 1_000

    @property
    def shared_length_m(self) -> float:
        return self.shared_length_mm / 1_000

    @property
    def source_coverage_fraction(self) -> float:
        return self.source_coverage_millionths / 1_000_000

    @property
    def target_coverage_fraction(self) -> float:
        return self.target_coverage_millionths / 1_000_000

    def canonical_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "state": self.state,
            "distance_mm": self.distance_mm,
            "bearing_difference_millidegrees": self.bearing_difference_millidegrees,
            "source_length_mm": self.source_length_mm,
            "target_length_mm": self.target_length_mm,
            "shared_length_mm": self.shared_length_mm,
            "source_coverage_millionths": self.source_coverage_millionths,
            "target_coverage_millionths": self.target_coverage_millionths,
            "orientation": self.orientation,
            "reason": self.reason,
            "source_geometry_fingerprint": self.source_geometry_fingerprint,
            "target_geometry_fingerprint": self.target_geometry_fingerprint,
            "source_evidence_fingerprint": self.source_evidence_fingerprint,
            "target_evidence_fingerprint": self.target_evidence_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
        }


@dataclass(frozen=True)
class LineMatchResult:
    records: tuple[LineMatchRecord, ...]
    diagnostics: tuple[LineMatchDiagnostic, ...]
    canonical_crs: str
    profile_fingerprint: str
    result_fingerprint: str

    @property
    def accepted(self) -> tuple[LineMatchRecord, ...]:
        return tuple(record for record in self.records if record.state == "accepted")

    @property
    def ambiguous(self) -> tuple[LineMatchRecord, ...]:
        return tuple(record for record in self.records if record.state == "ambiguous")

    @property
    def conflicting(self) -> tuple[LineMatchRecord, ...]:
        return tuple(record for record in self.records if record.state == "conflicting")

    @property
    def unmatched(self) -> tuple[LineMatchRecord, ...]:
        return tuple(record for record in self.records if record.state == "unmatched")


@dataclass(frozen=True)
class _ParsedLine:
    record_id: str
    geometry: LineString
    geometry_fingerprint: str
    evidence_fingerprint: str


@dataclass(frozen=True)
class LineMatchDiagnostic:
    code: Literal[
        "invalid-source-geometry",
        "invalid-target-geometry",
        "duplicate-source-id",
        "duplicate-target-id",
    ]
    record_id: str
    message: str


def match_line_evidence(
    sources: tuple[LineEvidenceRecord, ...],
    targets: tuple[TargetLineRecord, ...],
    profile: LineMatchProfile,
) -> LineMatchResult:
    """Match every source independently and retain every disposition."""

    diagnostics: list[LineMatchDiagnostic] = []
    duplicate_target_ids = {
        target_id
        for target_id in {target.target_id for target in targets}
        if sum(target.target_id == target_id for target in targets) > 1
    }
    diagnostics.extend(
        LineMatchDiagnostic(
            code="duplicate-target-id",
            record_id=target_id,
            message="target identity appears more than once",
        )
        for target_id in sorted(duplicate_target_ids)
    )
    parsed_targets_list: list[_ParsedLine] = []
    for target in sorted(targets, key=lambda item: item.target_id):
        if target.target_id in duplicate_target_ids:
            continue
        try:
            parsed_targets_list.append(
                _parse_line(
                    target.target_id,
                    target.geometry_wkt,
                    target.geometry_crs,
                    target.evidence_fingerprint,
                    profile,
                )
            )
        except ValueError as error:
            diagnostics.append(
                LineMatchDiagnostic(
                    code="invalid-target-geometry",
                    record_id=target.target_id,
                    message=str(error),
                )
            )
    parsed_targets = tuple(parsed_targets_list)
    records: list[LineMatchRecord] = []
    duplicate_source_ids = {
        source_id
        for source_id in {source.source_id for source in sources}
        if sum(source.source_id == source_id for source in sources) > 1
    }
    diagnostics.extend(
        LineMatchDiagnostic(
            code="duplicate-source-id",
            record_id=source_id,
            message="source identity appears more than once",
        )
        for source_id in sorted(duplicate_source_ids)
    )
    handled_duplicate_sources: set[str] = set()
    for source in sorted(sources, key=lambda item: item.source_id):
        if source.source_id in duplicate_source_ids:
            if source.source_id not in handled_duplicate_sources:
                duplicate_rows = tuple(
                    item for item in sources if item.source_id == source.source_id
                )
                records.append(_duplicate_source_record(duplicate_rows, profile))
                handled_duplicate_sources.add(source.source_id)
            continue
        try:
            parsed_source = _parse_line(
                source.source_id,
                source.geometry_wkt,
                source.geometry_crs,
                source.evidence_fingerprint,
                profile,
            )
        except ValueError as error:
            diagnostics.append(
                LineMatchDiagnostic(
                    code="invalid-source-geometry",
                    record_id=source.source_id,
                    message=str(error),
                )
            )
            records.append(_invalid_source_record(source, profile))
            continue
        evaluations = tuple(
            _evaluate_match(parsed_source, target, profile) for target in parsed_targets
        )
        accepted = tuple(record for record in evaluations if record.state == "accepted")
        if len(accepted) == 1:
            records.extend(accepted)
        elif len(accepted) > 1:
            records.extend(
                LineMatchRecord(
                    **{
                        **record.__dict__,
                        "state": "ambiguous",
                        "reason": "multiple-targets-within-profile",
                    }
                )
                for record in accepted
            )
        elif evaluations:
            records.append(
                min(
                    evaluations,
                    key=lambda item: (
                        item.distance_mm,
                        item.bearing_difference_millidegrees,
                        item.target_id or "",
                    ),
                )
            )
        else:
            records.append(_no_target_record(parsed_source, profile))
    canonical = tuple(
        sorted(
            records,
            key=lambda item: (item.source_id, item.target_id or "", item.state, item.reason),
        )
    )
    canonical_diagnostics = tuple(
        sorted(diagnostics, key=lambda item: (item.code, item.record_id, item.message))
    )
    result_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-line-match-result/v1",
            "canonical_crs": profile.canonical_crs,
            "profile_fingerprint": profile.fingerprint,
            "records": [record.canonical_payload() for record in canonical],
            "diagnostics": [item.__dict__ for item in canonical_diagnostics],
        }
    )
    return LineMatchResult(
        records=canonical,
        diagnostics=canonical_diagnostics,
        canonical_crs=profile.canonical_crs,
        profile_fingerprint=profile.fingerprint,
        result_fingerprint=result_fingerprint,
    )


def _parse_line(
    record_id: str,
    geometry_wkt: str,
    geometry_crs: str,
    record_evidence_fingerprint: str,
    profile: LineMatchProfile,
) -> _ParsedLine:
    if geometry_crs != profile.canonical_crs:
        raise ValueError(f"line {record_id} CRS does not match the active profile")
    try:
        geometry = wkt.loads(geometry_wkt)
    except Exception as error:
        raise ValueError(f"line {record_id} has invalid WKT") from error
    if not isinstance(geometry, LineString) or geometry.is_empty or not geometry.is_valid:
        raise ValueError(f"line {record_id} requires one nonempty valid LineString")
    if len(geometry.coords) < 2 or geometry.length <= 0:
        raise ValueError(f"line {record_id} must have positive length")
    return _ParsedLine(
        record_id=record_id,
        geometry=geometry,
        geometry_fingerprint=evidence_geometry_fingerprint(geometry, profile.canonical_crs),
        evidence_fingerprint=record_evidence_fingerprint,
    )


def _evaluate_match(
    source: _ParsedLine,
    target: _ParsedLine,
    profile: LineMatchProfile,
) -> LineMatchRecord:
    distance_m = source.geometry.distance(target.geometry)
    orientation, bearing_degrees = _orientation_and_bearing(source.geometry, target.geometry)
    shared_length_m = _shared_length(source.geometry, target.geometry)
    source_length_m = source.geometry.length
    target_length_m = target.geometry.length
    distance_mm = _millimetres(distance_m)
    bearing_millidegrees = round(bearing_degrees * 1_000)
    shared_length_mm = _millimetres(shared_length_m)
    source_length_mm = _millimetres(source_length_m)
    target_length_mm = _millimetres(target_length_m)
    reason = "within-profile"
    state: Literal["accepted", "ambiguous", "conflicting", "unmatched"] = "accepted"
    if distance_mm > _millimetres(profile.distance_tolerance_m):
        state, reason = "unmatched", "distance-outside-tolerance"
    elif bearing_millidegrees > round(profile.bearing_tolerance_degrees * 1_000):
        state, reason = "unmatched", "bearing-outside-tolerance"
    elif profile.orientation_policy == "same-direction" and orientation == "reversed":
        state, reason = "unmatched", "orientation-outside-policy"
    elif shared_length_mm < _millimetres(profile.minimum_shared_length_m):
        state, reason = "unmatched", "shared-length-below-minimum"
    return LineMatchRecord(
        source_id=source.record_id,
        target_id=target.record_id,
        state=state,
        distance_mm=distance_mm,
        bearing_difference_millidegrees=bearing_millidegrees,
        source_length_mm=source_length_mm,
        target_length_mm=target_length_mm,
        shared_length_mm=shared_length_mm,
        source_coverage_millionths=_millionths(shared_length_mm / source_length_mm),
        target_coverage_millionths=_millionths(shared_length_mm / target_length_mm),
        orientation=orientation,
        reason=reason,
        source_geometry_fingerprint=source.geometry_fingerprint,
        target_geometry_fingerprint=target.geometry_fingerprint,
        source_evidence_fingerprint=source.evidence_fingerprint,
        target_evidence_fingerprint=target.evidence_fingerprint,
        profile_fingerprint=profile.fingerprint,
    )


def _orientation_and_bearing(
    source: LineString, target: LineString
) -> tuple[Literal["same", "reversed", "not-applicable"], float]:
    source_start, source_end = source.coords[0], source.coords[-1]
    target_start, target_end = target.coords[0], target.coords[-1]
    source_vector = (source_end[0] - source_start[0], source_end[1] - source_start[1])
    target_vector = (target_end[0] - target_start[0], target_end[1] - target_start[1])
    source_norm = math.hypot(*source_vector)
    target_norm = math.hypot(*target_vector)
    if source_norm == 0 or target_norm == 0:
        return "not-applicable", 180.0
    cosine = max(
        -1.0,
        min(
            1.0,
            (source_vector[0] * target_vector[0] + source_vector[1] * target_vector[1])
            / (source_norm * target_norm),
        ),
    )
    orientation: Literal["same", "reversed", "not-applicable"] = (
        "same" if cosine >= 0 else "reversed"
    )
    return orientation, math.degrees(math.acos(abs(cosine)))


def _shared_length(source: LineString, target: LineString) -> float:
    start = target.project(source.boundary.geoms[0])
    end = target.project(source.boundary.geoms[-1])
    return min(source.length, abs(end - start), target.length)


def _no_target_record(source: _ParsedLine, profile: LineMatchProfile) -> LineMatchRecord:
    return LineMatchRecord(
        source_id=source.record_id,
        target_id=None,
        state="unmatched",
        distance_mm=0,
        bearing_difference_millidegrees=0,
        source_length_mm=_millimetres(source.geometry.length),
        target_length_mm=0,
        shared_length_mm=0,
        source_coverage_millionths=0,
        target_coverage_millionths=0,
        orientation="not-applicable",
        reason="no-target-lines",
        source_geometry_fingerprint=source.geometry_fingerprint,
        target_geometry_fingerprint=None,
        source_evidence_fingerprint=source.evidence_fingerprint,
        target_evidence_fingerprint=None,
        profile_fingerprint=profile.fingerprint,
    )


def _invalid_source_record(
    source: LineEvidenceRecord, profile: LineMatchProfile
) -> LineMatchRecord:
    return LineMatchRecord(
        source_id=source.source_id,
        target_id=None,
        state="unmatched",
        distance_mm=0,
        bearing_difference_millidegrees=0,
        source_length_mm=0,
        target_length_mm=0,
        shared_length_mm=0,
        source_coverage_millionths=0,
        target_coverage_millionths=0,
        orientation="not-applicable",
        reason="invalid-source-geometry",
        source_geometry_fingerprint=None,
        target_geometry_fingerprint=None,
        source_evidence_fingerprint=source.evidence_fingerprint,
        target_evidence_fingerprint=None,
        profile_fingerprint=profile.fingerprint,
    )


def _duplicate_source_record(
    sources: tuple[LineEvidenceRecord, ...], profile: LineMatchProfile
) -> LineMatchRecord:
    source_id = sources[0].source_id
    combined_evidence_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-duplicate-line-evidence/v1",
            "source_id": source_id,
            "row_fingerprints": tuple(
                sorted(
                    _input_line_fingerprint(
                        kind="source",
                        record_id=source.source_id,
                        geometry_wkt=source.geometry_wkt,
                        geometry_crs=source.geometry_crs,
                        record_evidence_fingerprint=source.evidence_fingerprint,
                    )
                    for source in sources
                )
            ),
        }
    )
    return LineMatchRecord(
        source_id=source_id,
        target_id=None,
        state="conflicting",
        distance_mm=0,
        bearing_difference_millidegrees=0,
        source_length_mm=0,
        target_length_mm=0,
        shared_length_mm=0,
        source_coverage_millionths=0,
        target_coverage_millionths=0,
        orientation="not-applicable",
        reason="duplicate-source-id",
        source_geometry_fingerprint=None,
        target_geometry_fingerprint=None,
        source_evidence_fingerprint=combined_evidence_fingerprint,
        target_evidence_fingerprint=None,
        profile_fingerprint=profile.fingerprint,
    )


BANES_LINE_MATCH_TRIAL_V1 = LineMatchProfile(
    profile_id="banes-line-match-trial-v1",
    version=1,
    canonical_crs="EPSG:27700",
    distance_tolerance_m=15,
    bearing_tolerance_degrees=35,
    minimum_shared_length_m=10,
    orientation_policy="insensitive",
    ambiguity_policy="retain-conflict",
)

BANES_LINE_MATCH_SENSITIVITY_V1 = tuple(
    LineMatchProfile(
        profile_id=f"banes-line-match-{distance_m}m-sensitivity-v1",
        version=1,
        canonical_crs="EPSG:27700",
        distance_tolerance_m=distance_m,
        bearing_tolerance_degrees=35,
        minimum_shared_length_m=10,
        orientation_policy="insensitive",
        ambiguity_policy="retain-conflict",
    )
    for distance_m in (5, 15, 25)
)


AggregationLaw = Literal[
    "extensive",
    "intensive",
    "maximum",
    "minimum",
    "categorical-proportion",
]


def _decimal_text(value: str, name: str) -> str:
    _required_text(value, name)
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a canonical finite decimal") from error
    if not parsed.is_finite():
        raise ValueError(f"{name} must be a canonical finite decimal")
    normalized = format(parsed.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


@dataclass(frozen=True)
class NumericObservation:
    observation_id: str
    source_id: str
    claim: str
    value_decimal: str
    evidence_fingerprint: str
    category: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.observation_id, "observation id")
        _required_text(self.source_id, "observation source id")
        _required_text(self.claim, "observation claim")
        object.__setattr__(
            self, "value_decimal", _decimal_text(self.value_decimal, "observation value")
        )
        if self.category is not None:
            _required_text(self.category, "observation category")
        _sha256(self.evidence_fingerprint, "observation evidence fingerprint")

    @property
    def value(self) -> Decimal:
        return Decimal(self.value_decimal)


@dataclass(frozen=True)
class LineAggregationProfile:
    profile_id: str
    version: int
    law: AggregationLaw
    claim: str
    category: str | None = None
    extrema_permitted: bool = False

    def __post_init__(self) -> None:
        _required_text(self.profile_id, "aggregation profile id")
        _required_text(self.claim, "aggregation claim")
        if self.version < 1:
            raise ValueError("aggregation profile version must be positive")
        if self.law not in {
            "extensive",
            "intensive",
            "maximum",
            "minimum",
            "categorical-proportion",
        }:
            raise ValueError("aggregation law is not declared")
        if self.law in {"maximum", "minimum"} and not self.extrema_permitted:
            raise ValueError("maximum/minimum require an explicitly permitted schema")
        if self.law == "categorical-proportion" and self.category is None:
            raise ValueError("categorical proportion requires one declared category")
        if self.category is not None:
            _required_text(self.category, "aggregation category")

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(
            {
                "contract": "satn-line-aggregation-profile/v1",
                "profile_id": self.profile_id,
                "version": self.version,
                "law": self.law,
                "claim": self.claim,
                "category": self.category,
                "extrema_permitted": self.extrema_permitted,
            }
        )


@dataclass(frozen=True)
class AggregationConflict:
    source_id: str
    claim: str
    observation_ids: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    value_decimals: tuple[str, ...]
    target_ids: tuple[str, ...]
    reason: Literal["contradictory-observations", "duplicate-observation-id"]


@dataclass(frozen=True)
class AggregatedTargetLineEvidence:
    target_id: str
    state: Literal["available", "conflicting", "no-data"]
    value_decimal: str | None
    observation_ids: tuple[str, ...]
    observation_evidence_fingerprints: tuple[str, ...]
    source_ids: tuple[str, ...]
    shared_length_mm: int
    target_length_mm: int
    reason: str

    @property
    def value(self) -> Decimal | None:
        return Decimal(self.value_decimal) if self.value_decimal is not None else None

    def canonical_payload(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "state": self.state,
            "value_decimal": self.value_decimal,
            "observation_ids": self.observation_ids,
            "observation_evidence_fingerprints": self.observation_evidence_fingerprints,
            "source_ids": self.source_ids,
            "shared_length_mm": self.shared_length_mm,
            "target_length_mm": self.target_length_mm,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AggregatedLineEvidence:
    targets: tuple[AggregatedTargetLineEvidence, ...]
    conflicts: tuple[AggregationConflict, ...]
    canonical_crs: str
    match_result_fingerprint: str
    profile_fingerprint: str
    result_fingerprint: str


def aggregate_line_evidence(
    matches: LineMatchResult,
    observations: tuple[NumericObservation, ...],
    profile: LineAggregationProfile,
) -> AggregatedLineEvidence:
    """Aggregate accepted matches using one declared mathematical law."""

    relevant = tuple(
        sorted(
            (observation for observation in observations if observation.claim == profile.claim),
            key=lambda item: (
                item.source_id,
                item.observation_id,
                item.evidence_fingerprint,
            ),
        )
    )
    by_source: dict[str, list[NumericObservation]] = {}
    for observation in relevant:
        by_source.setdefault(observation.source_id, []).append(observation)
    accepted = tuple(record for record in matches.records if record.state == "accepted")
    target_ids_by_source: dict[str, tuple[str, ...]] = {}
    for source_id in sorted({record.source_id for record in accepted}):
        target_ids_by_source[source_id] = tuple(
            sorted(
                record.target_id
                for record in accepted
                if record.source_id == source_id and record.target_id is not None
            )
        )
    conflicts_list: list[AggregationConflict] = []
    for source_id, values in sorted(by_source.items()):
        observation_ids = tuple(item.observation_id for item in values)
        if len(set(observation_ids)) < len(observation_ids):
            reason: Literal["contradictory-observations", "duplicate-observation-id"] = (
                "duplicate-observation-id"
            )
        elif len({(item.value_decimal, item.category) for item in values}) > 1:
            reason = "contradictory-observations"
        else:
            continue
        conflicts_list.append(
            AggregationConflict(
                source_id=source_id,
                claim=profile.claim,
                observation_ids=observation_ids,
                evidence_fingerprints=tuple(item.evidence_fingerprint for item in values),
                value_decimals=tuple(sorted({item.value_decimal for item in values})),
                target_ids=target_ids_by_source.get(source_id, ()),
                reason=reason,
            )
        )
    conflicts = tuple(conflicts_list)
    conflict_sources = {conflict.source_id for conflict in conflicts}
    target_ids = tuple(
        sorted({record.target_id for record in accepted if record.target_id is not None})
    )
    target_results = tuple(
        _aggregate_target(
            target_id,
            accepted,
            by_source,
            conflict_sources,
            profile,
        )
        for target_id in target_ids
    )
    result_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-aggregated-line-evidence/v1",
            "canonical_crs": matches.canonical_crs,
            "match_result_fingerprint": matches.result_fingerprint,
            "profile_fingerprint": profile.fingerprint,
            "targets": [target.canonical_payload() for target in target_results],
            "conflicts": [conflict.__dict__ for conflict in conflicts],
        }
    )
    return AggregatedLineEvidence(
        targets=target_results,
        conflicts=conflicts,
        canonical_crs=matches.canonical_crs,
        match_result_fingerprint=matches.result_fingerprint,
        profile_fingerprint=profile.fingerprint,
        result_fingerprint=result_fingerprint,
    )


def _aggregate_target(
    target_id: str,
    matches: tuple[LineMatchRecord, ...],
    by_source: dict[str, list[NumericObservation]],
    conflict_sources: set[str],
    profile: LineAggregationProfile,
) -> AggregatedTargetLineEvidence:
    target_matches = tuple(record for record in matches if record.target_id == target_id)
    matched_conflicts = tuple(
        sorted({record.source_id for record in target_matches} & conflict_sources)
    )
    if matched_conflicts:
        observation_ids = tuple(
            item.observation_id for source_id in matched_conflicts for item in by_source[source_id]
        )
        evidence_fingerprints = tuple(
            item.evidence_fingerprint
            for source_id in matched_conflicts
            for item in by_source[source_id]
        )
        return AggregatedTargetLineEvidence(
            target_id=target_id,
            state="conflicting",
            value_decimal=None,
            observation_ids=observation_ids,
            observation_evidence_fingerprints=evidence_fingerprints,
            source_ids=matched_conflicts,
            shared_length_mm=sum(record.shared_length_mm for record in target_matches),
            target_length_mm=target_matches[0].target_length_mm,
            reason="conflicting-source-observations",
        )
    contributions = tuple(
        (record, by_source[record.source_id][0])
        for record in target_matches
        if record.source_id in by_source
    )
    if not contributions:
        return AggregatedTargetLineEvidence(
            target_id=target_id,
            state="no-data",
            value_decimal=None,
            observation_ids=(),
            observation_evidence_fingerprints=(),
            source_ids=(),
            shared_length_mm=0,
            target_length_mm=target_matches[0].target_length_mm,
            reason="no-observations-for-accepted-matches",
        )
    value = _aggregate_value(contributions, profile)
    provenance_observations = tuple(
        observation
        for record, _selected in contributions
        for observation in by_source[record.source_id]
    )
    return AggregatedTargetLineEvidence(
        target_id=target_id,
        state="available",
        value_decimal=_decimal_text(format(value, "f"), "aggregated value"),
        observation_ids=tuple(item.observation_id for item in provenance_observations),
        observation_evidence_fingerprints=tuple(
            item.evidence_fingerprint for item in provenance_observations
        ),
        source_ids=tuple(record.source_id for record, _item in contributions),
        shared_length_mm=sum(record.shared_length_mm for record, _item in contributions),
        target_length_mm=target_matches[0].target_length_mm,
        reason=f"aggregated-{profile.law}",
    )


def _aggregate_value(
    contributions: tuple[tuple[LineMatchRecord, NumericObservation], ...],
    profile: LineAggregationProfile,
) -> Decimal:
    if profile.law == "extensive":
        return sum(
            (
                observation.value
                * Decimal(record.shared_length_mm)
                / Decimal(record.target_length_mm)
                for record, observation in contributions
            ),
            Decimal(0),
        )
    if profile.law == "intensive":
        denominator = sum(
            (Decimal(record.shared_length_mm) for record, _item in contributions),
            Decimal(0),
        )
        return (
            sum(
                (
                    observation.value * Decimal(record.shared_length_mm)
                    for record, observation in contributions
                ),
                Decimal(0),
            )
            / denominator
        )
    values = tuple(observation.value for _record, observation in contributions)
    if profile.law == "maximum":
        return max(values)
    if profile.law == "minimum":
        return min(values)
    numerator = sum(
        (
            Decimal(record.shared_length_mm)
            for record, observation in contributions
            if observation.category == profile.category
        ),
        Decimal(0),
    )
    target_length = Decimal(contributions[0][0].target_length_mm)
    return numerator / target_length
