"""Directional Topography Profiles derived from governed Elevation Evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import pairwise

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, substring

from satn.models import TrafficLight


@dataclass(frozen=True)
class GradientThresholds:
    """Adjustable trial thresholds for Gradient Section display bands."""

    gentle: float = 3.0
    noticeable: float = 5.0
    steep: float = 8.0
    very_steep: float = 12.5

    def band(self, absolute_gradient_pct: float) -> str:
        if absolute_gradient_pct <= self.gentle:
            return "gentle"
        if absolute_gradient_pct <= self.noticeable:
            return "noticeable"
        if absolute_gradient_pct <= self.steep:
            return "steep"
        if absolute_gradient_pct <= self.very_steep:
            return "very-steep"
        return "severe"


PROFILE_COLUMNS = [
    "profile_id",
    "edge_id",
    "edge_type",
    "evidence_status",
    "evidence_rationale",
    "criterion_elevation_evidence",
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
    "micro_gradient_capability",
    "micro_gradient_intervals",
    "geometry",
]

SECTION_COLUMNS = [
    "section_id",
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
    "geometry",
]

EDGE_PROFILE_COLUMNS = [
    "topography_profile_id",
    "topography_evidence_status",
    "topography_evidence_rationale",
    "topography_distance_m",
    "forward_ascent_m",
    "forward_descent_m",
    "reverse_ascent_m",
    "reverse_descent_m",
    "steepest_sustained_gradient_pct",
    "steepest_sustained_gradient_rationale",
    "gradient_section_ids",
]


def empty_elevation_evidence(crs: object) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=["evidence_id", "source_id", "elevation_m", "geometry"],
        geometry="geometry",
        crs=crs,
    )


def build_topography_profiles(
    edge_frames: list[tuple[str, str, gpd.GeoDataFrame]],
    elevation_evidence: gpd.GeoDataFrame | None,
    *,
    thresholds: GradientThresholds | None = None,
    evidence_tolerance_m: float = 5.0,
    maximum_sample_spacing_m: float = 250.0,
    minimum_sustained_spacing_m: float = 10.0,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Enrich generated edges and return their profiles and visible sections.

    Edge frames are deliberately mutated so every authoritative edge artifact carries
    the same directional summary and profile identifier as the dedicated profile layer.
    """

    thresholds = thresholds or GradientThresholds()
    crs = next((frame.crs for _, _, frame in edge_frames if frame.crs is not None), 4326)
    evidence = (
        elevation_evidence.copy()
        if elevation_evidence is not None
        else empty_elevation_evidence(crs)
    )
    if evidence.crs is None:
        evidence = evidence.set_crs(crs)
    metric_evidence = (
        evidence
        if evidence.crs is not None and evidence.crs.to_epsg() == 27700
        else evidence.to_crs(27700)
    )
    profile_rows: list[dict[str, object]] = []
    section_rows: list[dict[str, object]] = []
    for edge_type, id_column, frame in edge_frames:
        _ensure_edge_columns(frame)
        metric_geometry = (
            frame.geometry
            if frame.crs is not None and frame.crs.to_epsg() == 27700
            else frame.geometry.to_crs(27700)
        )
        for position, (index, edge) in enumerate(frame.iterrows()):
            edge_id = str(edge[id_column])
            profile, sections = _profile_edge(
                edge_id,
                edge_type,
                edge.geometry,
                frame.crs,
                metric_geometry.iloc[position],
                metric_evidence,
                thresholds,
                evidence_tolerance_m,
                maximum_sample_spacing_m,
                minimum_sustained_spacing_m,
            )
            profile = _apply_edge_micro_gradient_limitations(profile, edge)
            profile_rows.append(profile)
            section_rows.extend(sections)
            _apply_profile(frame, index, profile)
    profiles = gpd.GeoDataFrame(
        profile_rows,
        columns=PROFILE_COLUMNS,
        geometry="geometry",
        crs=crs,
    )
    sections = gpd.GeoDataFrame(
        section_rows,
        columns=SECTION_COLUMNS,
        geometry="geometry",
        crs=crs,
    )
    return profiles, sections


def _profile_edge(
    edge_id: str,
    edge_type: str,
    geometry: object,
    crs: object,
    metric_line: object,
    metric_evidence: gpd.GeoDataFrame,
    thresholds: GradientThresholds,
    tolerance_m: float,
    maximum_sample_spacing_m: float,
    minimum_sustained_spacing_m: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    profile_id = _stable_id("topography-profile", edge_type, edge_id)
    if isinstance(metric_line, MultiLineString):
        merged = linemerge(metric_line)
        if isinstance(merged, LineString):
            metric_line = merged
    if not isinstance(metric_line, LineString) or metric_line.is_empty:
        return _unavailable_profile(
            profile_id,
            edge_id,
            edge_type,
            geometry,
            "edge geometry is not a continuous usable line",
            distance_m=(float(metric_line.length) if hasattr(metric_line, "length") else None),
        ), []
    samples = _samples_on_line(metric_line, metric_evidence, tolerance_m)
    if len(samples) < 2:
        return _unavailable_profile(
            profile_id,
            edge_id,
            edge_type,
            geometry,
            "at least two usable Elevation Evidence samples are required",
            distance_m=metric_line.length,
        ), []
    if samples[0][0] > tolerance_m or metric_line.length - samples[-1][0] > tolerance_m:
        return _unavailable_profile(
            profile_id,
            edge_id,
            edge_type,
            geometry,
            "Elevation Evidence does not cover both ends of the edge",
            distance_m=metric_line.length,
        ), []
    largest_gap = max(right[0] - left[0] for left, right in pairwise(samples))
    if largest_gap > maximum_sample_spacing_m:
        return _unavailable_profile(
            profile_id,
            edge_id,
            edge_type,
            geometry,
            (
                f"Elevation Evidence has a {largest_gap:.1f} m interior gap; "
                f"maximum governed spacing is {maximum_sample_spacing_m:.1f} m"
            ),
            distance_m=metric_line.length,
        ), []
    observed_samples = _normalise_observed_samples(samples, minimum_sustained_spacing_m)
    sustained_samples = _sustained_samples(observed_samples, minimum_sustained_spacing_m)
    sustained_gradients = [
        abs((right[1] - left[1]) / (right[0] - left[0]) * 100)
        for left, right in pairwise(sustained_samples)
        if right[0] - left[0] >= minimum_sustained_spacing_m
    ]
    steepest_sustained = max(sustained_gradients) if sustained_gradients else None

    raw_sections: list[dict[str, object]] = []
    forward_ascent = 0.0
    forward_descent = 0.0
    for left, right in pairwise(observed_samples):
        start, start_elevation, start_id, _ = left
        end, end_elevation, end_id, _ = right
        length = end - start
        if length <= 0:
            continue
        change = end_elevation - start_elevation
        forward_ascent += max(change, 0.0)
        forward_descent += max(-change, 0.0)
        gradient = change / length * 100
        raw_sections.append(
            {
                "start_distance_m": start,
                "end_distance_m": end,
                "length_m": length,
                "forward_gradient_pct": gradient,
                "absolute_gradient_pct": abs(gradient),
                "gradient_band": thresholds.band(abs(gradient)),
                "uphill_direction": (
                    "forward" if gradient > 0 else "reverse" if gradient < 0 else "level"
                ),
                "elevation_evidence_ids": [start_id, end_id],
            }
        )
    if not raw_sections:
        return _unavailable_profile(
            profile_id,
            edge_id,
            edge_type,
            geometry,
            "Elevation Evidence contains no usable spaced samples",
            distance_m=metric_line.length,
        ), []
    merged = _merge_sections(raw_sections)
    for section in merged:
        section["sustained"] = (
            float(section["length_m"]) >= minimum_sustained_spacing_m
            and steepest_sustained is not None
            and float(section["absolute_gradient_pct"]) <= steepest_sustained + 1e-9
        )
        section["sustained_rationale"] = (
            f"Meets the {minimum_sustained_spacing_m:.1f} m governed sustained window."
            if section["sustained"]
            else (
                f"Short observed section below the {minimum_sustained_spacing_m:.1f} m "
                "sustained window; kept visible but excluded from the sustained statistic."
            )
            if float(section["length_m"]) < minimum_sustained_spacing_m
            else (
                "Observed local section is not corroborated by the governed sustained "
                "window; kept visible but excluded from the sustained statistic."
            )
        )
    section_rows = [
        _section_row(profile_id, edge_id, edge_type, metric_line, crs, section)
        for section in merged
    ]
    evidence_ids = sorted({item[2] for item in samples})
    source_ids = sorted({item[3] for item in samples if item[3]})
    micro_capability, micro_intervals = _micro_gradient_evidence(
        observed_samples,
        metric_line.length,
        thresholds,
        source_metadata=_elevation_source_metadata(metric_evidence, observed_samples),
    )
    return (
        {
            "profile_id": profile_id,
            "edge_id": edge_id,
            "edge_type": edge_type,
            "evidence_status": "available",
            "evidence_rationale": (
                f"{len(samples)} governed Elevation Evidence samples cover the edge."
            ),
            "criterion_elevation_evidence": TrafficLight.GREEN.value,
            "distance_m": metric_line.length,
            "forward_ascent_m": forward_ascent,
            "forward_descent_m": forward_descent,
            "reverse_ascent_m": forward_descent,
            "reverse_descent_m": forward_ascent,
            "steepest_sustained_gradient_pct": steepest_sustained,
            "steepest_sustained_gradient_rationale": (
                "Maximum gradient across intervals of at least "
                f"{minimum_sustained_spacing_m:.1f} m."
                if steepest_sustained is not None
                else (
                    "No interval meets the governed "
                    f"{minimum_sustained_spacing_m:.1f} m sustained window."
                )
            ),
            "gradient_section_ids": json.dumps([section["section_id"] for section in section_rows]),
            "elevation_evidence_ids": json.dumps(evidence_ids),
            "elevation_source_ids": json.dumps(source_ids),
            "micro_gradient_capability": json.dumps(micro_capability, sort_keys=True),
            "micro_gradient_intervals": json.dumps(micro_intervals, sort_keys=True),
            "geometry": geometry,
        },
        section_rows,
    )


def _samples_on_line(
    line: LineString,
    evidence: gpd.GeoDataFrame,
    tolerance_m: float,
) -> list[tuple[float, float, str, str]]:
    if evidence.empty or "elevation_m" not in evidence:
        return []
    samples: dict[float, tuple[float, float, str, str]] = {}
    candidate_positions = evidence.sindex.query(
        line.buffer(tolerance_m),
        predicate="intersects",
    )
    for index, row in evidence.iloc[candidate_positions].iterrows():
        if (
            row.geometry is None
            or row.geometry.is_empty
            or row.geometry.distance(line) > tolerance_m
        ):
            continue
        try:
            elevation = float(row["elevation_m"])
        except (TypeError, ValueError):
            continue
        if not pd.notna(elevation):
            continue
        distance = float(line.project(row.geometry))
        evidence_id = str(row.get("evidence_id") or index)
        source_id = str(row.get("source_id") or "")
        samples[round(distance, 6)] = (distance, elevation, evidence_id, source_id)
    return sorted(samples.values())


def _micro_gradient_evidence(
    samples: list[tuple[float, float, str, str]],
    distance_m: float,
    thresholds: GradientThresholds,
    *,
    windows_m: tuple[float, ...] = (20.0, 50.0),
    maximum_supported_spacing_m: float = 12.5,
    source_metadata: dict[str, float | None] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Return distance-aligned gradients only when the evidence supports their scale."""

    coverage_gaps = (
        [samples[0][0]]
        + [right[0] - left[0] for left, right in pairwise(samples)]
        + [distance_m - samples[-1][0]]
        if samples
        else []
    )
    observed_spacing = max(coverage_gaps, default=None)
    metadata = source_metadata or {}
    vertical_accuracy = metadata.get("vertical_accuracy_m")
    source_resolution = metadata.get("source_resolution_m")
    output_spacing = metadata.get("output_sample_spacing_m")
    evidence_quality_status = "available" if vertical_accuracy is not None else "uncertain"
    uncertainty = {
        "gradient_is_derived": True,
        "maximum_observed_sample_spacing_m": observed_spacing,
        "vertical_accuracy_m": vertical_accuracy,
        "source_resolution_m": source_resolution,
        "declared_output_sample_spacing_m": output_spacing,
        "status": evidence_quality_status,
        "caveat": (
            "Gradient is derived from governed elevation samples; source vertical "
            "accuracy is not declared, so values are analytical indicators rather "
            "than survey-grade measurements."
            if vertical_accuracy is None
            else "Gradient is derived from governed elevation samples with declared "
            "source vertical accuracy."
        ),
    }
    capability: dict[str, object] = {
        "status": "unavailable",
        "windows_m": list(windows_m),
        "sample_count": len(samples),
        "maximum_observed_sample_spacing_m": observed_spacing,
        "maximum_supported_sample_spacing_m": maximum_supported_spacing_m,
        "coverage_start_m": samples[0][0] if samples else None,
        "coverage_end_m": samples[-1][0] if samples else None,
        "evidence_quality_status": evidence_quality_status,
        "uncertainty": uncertainty,
    }
    if observed_spacing is None:
        capability["rationale"] = "At least two elevation samples are required."
        return capability, []
    if observed_spacing > maximum_supported_spacing_m + 1e-6:
        capability["rationale"] = (
            f"Micro-gradient evidence requires samples no more than "
            f"{maximum_supported_spacing_m:.1f} m apart; the largest observed "
            f"spacing is {observed_spacing:.1f} m."
        )
        return capability, []
    supported_windows = [window for window in windows_m if distance_m + 1e-6 >= window]
    if not supported_windows:
        capability["rationale"] = (
            f"The edge is shorter than the smallest {min(windows_m):.1f} m window."
        )
        return capability, []

    intervals: list[dict[str, object]] = []
    rolling_step_m = min(10.0, min(supported_windows))
    for window_m in supported_windows:
        start_m = 0.0
        while start_m + window_m <= distance_m + 1e-6:
            end_m = min(start_m + window_m, distance_m)
            start_elevation, start_ids = _interpolated_elevation(samples, start_m)
            end_elevation, end_ids = _interpolated_elevation(samples, end_m)
            gradient = (end_elevation - start_elevation) / (end_m - start_m) * 100
            intervals.append(
                {
                    "window_m": window_m,
                    "rolling_step_m": rolling_step_m,
                    "calculation_method": "rolling-endpoint-difference",
                    "start_distance_m": round(start_m, 3),
                    "end_distance_m": round(end_m, 3),
                    "forward_gradient_pct": round(gradient, 3),
                    "absolute_gradient_pct": round(abs(gradient), 3),
                    "gradient_band": thresholds.band(abs(gradient)),
                    "uphill_direction": (
                        "forward" if gradient > 0 else "reverse" if gradient < 0 else "level"
                    ),
                    "elevation_evidence_ids": list(dict.fromkeys([*start_ids, *end_ids])),
                    "uncertainty": uncertainty,
                    "status": "available",
                }
            )
            start_m += rolling_step_m

    capability["status"] = "available"
    capability["windows_m"] = supported_windows
    capability["rolling_step_m"] = rolling_step_m
    capability["calculation_method"] = "rolling-endpoint-difference"
    capability["rationale"] = (
        "Governed elevation samples support overlapping rolling micro-gradient windows."
        + (
            " Source vertical accuracy is not declared, so values remain analytical indicators."
            if evidence_quality_status == "uncertain"
            else ""
        )
    )
    return capability, intervals


def _elevation_source_metadata(
    evidence: gpd.GeoDataFrame,
    samples: list[tuple[float, float, str, str]],
) -> dict[str, float | None]:
    """Return conservative optional metadata shared by governed elevation samples."""

    evidence_ids = {sample[2] for sample in samples}
    selected = (
        evidence[evidence["evidence_id"].astype(str).isin(evidence_ids)]
        if "evidence_id" in evidence
        else evidence.iloc[0:0]
    )
    metadata: dict[str, float | None] = {}
    for field in (
        "vertical_accuracy_m",
        "source_resolution_m",
        "output_sample_spacing_m",
    ):
        values = pd.to_numeric(selected[field], errors="coerce") if field in selected else None
        metadata[field] = (
            float(values.max())
            if values is not None and len(values) == len(samples) and values.notna().all()
            else None
        )
    return metadata


def _apply_edge_micro_gradient_limitations(
    profile: dict[str, object],
    edge: pd.Series,
) -> dict[str, object]:
    """Suppress ground-model precision where an edge is carried above or below terrain."""

    limitations = [
        field
        for field in ("bridge", "tunnel")
        if str(edge.get(field, "")).strip().lower() not in {"", "0", "false", "no", "none", "nan"}
    ]
    if not limitations:
        return profile
    capability = json.loads(str(profile["micro_gradient_capability"]))
    capability["status"] = "unavailable"
    capability["rationale"] = (
        "Micro-gradient evidence is unavailable because the edge is tagged "
        f"{' and '.join(limitations)}; a ground terrain model does not establish "
        "the carried route surface."
    )
    uncertainty = dict(capability.get("uncertainty") or {})
    uncertainty["infrastructure_limitation"] = limitations
    capability["uncertainty"] = uncertainty
    return {
        **profile,
        "micro_gradient_capability": json.dumps(capability, sort_keys=True),
        "micro_gradient_intervals": "[]",
    }


def _interpolated_elevation(
    samples: list[tuple[float, float, str, str]],
    target_m: float,
) -> tuple[float, list[str]]:
    if target_m <= samples[0][0]:
        return samples[0][1], [samples[0][2]]
    if target_m >= samples[-1][0]:
        return samples[-1][1], [samples[-1][2]]
    for sample in samples:
        if abs(sample[0] - target_m) <= 1e-6:
            return sample[1], [sample[2]]
    for left, right in pairwise(samples):
        if left[0] <= target_m <= right[0]:
            fraction = (target_m - left[0]) / (right[0] - left[0])
            return left[1] + (right[1] - left[1]) * fraction, [left[2], right[2]]
    raise ValueError("micro-gradient target lies outside elevation evidence coverage")


def _sustained_samples(
    samples: list[tuple[float, float, str, str]],
    minimum_spacing_m: float,
) -> list[tuple[float, float, str, str]]:
    """Retain representative samples separated by the governed sustained window."""

    if samples[-1][0] - samples[0][0] <= minimum_spacing_m:
        return [samples[0], samples[-1]]
    sustained = [samples[0]]
    for sample in samples[1:-1]:
        if sample[0] - sustained[-1][0] >= minimum_spacing_m:
            sustained.append(sample)
    if samples[-1][0] - sustained[-1][0] < minimum_spacing_m and len(sustained) > 1:
        sustained[-1] = samples[-1]
    else:
        sustained.append(samples[-1])
    return sustained


def _normalise_observed_samples(
    samples: list[tuple[float, float, str, str]],
    minimum_spacing_m: float,
) -> list[tuple[float, float, str, str]]:
    """Collapse near-duplicate samples and suppress only corroborated local anomalies."""

    cluster_tolerance_m = min(0.5, minimum_spacing_m / 20)
    collapsed: list[tuple[tuple[float, float, str, str], int]] = []
    cluster: list[tuple[float, float, str, str]] = []
    cluster_start = samples[0][0]
    for sample in samples:
        if cluster and sample[0] - cluster_start > cluster_tolerance_m:
            collapsed.append((_median_sample(cluster), len(cluster)))
            cluster = []
            cluster_start = sample[0]
        cluster.append(sample)
    collapsed.append((_median_sample(cluster), len(cluster)))
    if len(collapsed) < 2:
        return [samples[0], samples[-1]]

    filtered = [sample for sample, _ in collapsed]
    for index in range(1, len(collapsed) - 1):
        left = collapsed[index - 1][0]
        current, cluster_size = collapsed[index]
        right = collapsed[index + 1][0]
        span = right[0] - left[0]
        if cluster_size < 2 or span <= 0:
            continue
        left_change = current[1] - left[1]
        right_change = right[1] - current[1]
        if left_change == 0 or right_change == 0 or left_change * right_change >= 0:
            continue
        fraction = (current[0] - left[0]) / span
        interpolated = left[1] + (right[1] - left[1]) * fraction
        filtered[index] = (current[0], interpolated, current[2], current[3])

    return filtered


def _median_sample(
    samples: list[tuple[float, float, str, str]],
) -> tuple[float, float, str, str]:
    ordered_distance = sorted(sample[0] for sample in samples)
    ordered_elevation = sorted(sample[1] for sample in samples)
    middle = len(samples) // 2
    if len(samples) % 2:
        distance = ordered_distance[middle]
        elevation = ordered_elevation[middle]
    else:
        distance = (ordered_distance[middle - 1] + ordered_distance[middle]) / 2
        elevation = (ordered_elevation[middle - 1] + ordered_elevation[middle]) / 2
    return (
        distance,
        elevation,
        "+".join(sorted({sample[2] for sample in samples})),
        "+".join(sorted({sample[3] for sample in samples if sample[3]})),
    )


def _merge_sections(sections: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for section in sections:
        if (
            merged
            and merged[-1]["gradient_band"] == section["gradient_band"]
            and merged[-1]["uphill_direction"] == section["uphill_direction"]
        ):
            previous = merged[-1]
            total_length = float(previous["length_m"]) + float(section["length_m"])
            total_change = (
                float(previous["forward_gradient_pct"]) * float(previous["length_m"])
                + float(section["forward_gradient_pct"]) * float(section["length_m"])
            ) / 100
            previous["end_distance_m"] = section["end_distance_m"]
            previous["length_m"] = total_length
            previous["forward_gradient_pct"] = total_change / total_length * 100
            previous["absolute_gradient_pct"] = max(
                float(previous["absolute_gradient_pct"]),
                float(section["absolute_gradient_pct"]),
            )
            previous["elevation_evidence_ids"] = list(
                dict.fromkeys(
                    [
                        *previous["elevation_evidence_ids"],
                        *section["elevation_evidence_ids"],
                    ]
                )
            )
        else:
            merged.append(section.copy())
    return merged


def _section_row(
    profile_id: str,
    edge_id: str,
    edge_type: str,
    metric_line: LineString,
    crs: object,
    section: dict[str, object],
) -> dict[str, object]:
    start = float(section["start_distance_m"])
    end = float(section["end_distance_m"])
    geometry = substring(metric_line, start, end)
    if gpd.GeoSeries([geometry], crs=27700).crs != crs:
        geometry = gpd.GeoSeries([geometry], crs=27700).to_crs(crs).iloc[0]
    section_id = _stable_id("gradient-section", profile_id, f"{start:.3f}", f"{end:.3f}")
    return {
        "section_id": section_id,
        "profile_id": profile_id,
        "edge_id": edge_id,
        "edge_type": edge_type,
        **{key: value for key, value in section.items() if key != "elevation_evidence_ids"},
        "elevation_evidence_ids": json.dumps(section["elevation_evidence_ids"]),
        "geometry": geometry,
    }


def _unavailable_profile(
    profile_id: str,
    edge_id: str,
    edge_type: str,
    geometry: object,
    rationale: str,
    *,
    distance_m: float | None = None,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "edge_id": edge_id,
        "edge_type": edge_type,
        "evidence_status": "evidence-unavailable",
        "evidence_rationale": rationale,
        "criterion_elevation_evidence": TrafficLight.GREY.value,
        "distance_m": distance_m,
        "forward_ascent_m": None,
        "forward_descent_m": None,
        "reverse_ascent_m": None,
        "reverse_descent_m": None,
        "steepest_sustained_gradient_pct": None,
        "steepest_sustained_gradient_rationale": (
            "No sustained-gradient statistic is available without usable Elevation Evidence."
        ),
        "gradient_section_ids": "[]",
        "elevation_evidence_ids": "[]",
        "elevation_source_ids": "[]",
        "micro_gradient_capability": json.dumps(
            {
                "status": "unavailable",
                "windows_m": [20.0, 50.0],
                "sample_count": 0,
                "maximum_observed_sample_spacing_m": None,
                "maximum_supported_sample_spacing_m": 12.5,
                "coverage_start_m": None,
                "coverage_end_m": None,
                "rationale": rationale,
                "uncertainty": {
                    "gradient_is_derived": True,
                    "maximum_observed_sample_spacing_m": None,
                    "vertical_accuracy_m": None,
                    "caveat": "No usable governed elevation profile is available.",
                },
            },
            sort_keys=True,
        ),
        "micro_gradient_intervals": "[]",
        "geometry": geometry,
    }


def _ensure_edge_columns(frame: gpd.GeoDataFrame) -> None:
    for column in EDGE_PROFILE_COLUMNS:
        if column not in frame:
            frame[column] = pd.Series(index=frame.index, dtype=object)


def _apply_profile(
    frame: gpd.GeoDataFrame,
    index: object,
    profile: dict[str, object],
) -> None:
    values = {
        "topography_profile_id": profile["profile_id"],
        "topography_evidence_status": profile["evidence_status"],
        "topography_evidence_rationale": profile["evidence_rationale"],
        "topography_distance_m": profile["distance_m"],
        "forward_ascent_m": profile["forward_ascent_m"],
        "forward_descent_m": profile["forward_descent_m"],
        "reverse_ascent_m": profile["reverse_ascent_m"],
        "reverse_descent_m": profile["reverse_descent_m"],
        "steepest_sustained_gradient_pct": profile["steepest_sustained_gradient_pct"],
        "steepest_sustained_gradient_rationale": profile["steepest_sustained_gradient_rationale"],
        "gradient_section_ids": profile["gradient_section_ids"],
    }
    for column, value in values.items():
        frame.at[index, column] = value


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"
