(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.SATN_REVIEW_LENS_STATE = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const SelectionKind = Object.freeze({
    NONE: "none",
    PREVIEW: "preview",
    PINNED: "pinned",
  });
  const ComparisonKind = Object.freeze({
    NONE: "none",
    SEGMENTS: "segments",
  });
  const ActionType = Object.freeze({
    PREVIEW_ARTIFACT: "preview_artifact",
    TOGGLE_PIN_ARTIFACT: "toggle_pin_artifact",
    CLOSE: "close",
  });

  const SOURCE_ALIASES = Object.freeze({
    reviewable_network: "reviewable",
    reference_satn_options: "reference-satn-options",
  });

  function normalizedSourceId(sourceId) {
    return SOURCE_ALIASES[sourceId] || sourceId;
  }

  function stableArtifactId(feature) {
    if (!feature || typeof feature !== "object") return "";
    if (feature.id !== undefined && feature.id !== null && String(feature.id).trim()) {
      return String(feature.id);
    }
    const properties = feature.properties && typeof feature.properties === "object"
      ? feature.properties
      : {};
    const preferred = [
      "rendered_feature_id",
      "section_id",
      "profile_id",
      "place_id",
      "connection_id",
      "structure_id",
      "obligation_id",
      "option_id",
      "evidence_id",
    ];
    for (const key of preferred) {
      if (properties[key] !== undefined && properties[key] !== null && String(properties[key]).trim()) {
        return String(properties[key]);
      }
    }
    const fallback = Object.keys(properties).find((key) => key.endsWith("_id"));
    return fallback && properties[fallback] !== undefined && properties[fallback] !== null
      ? String(properties[fallback])
      : "";
  }

  function artifactRecord(feature, sourceId, layerId) {
    const id = stableArtifactId(feature);
    if (!id) return null;
    const source = normalizedSourceId(String(sourceId));
    return {
      key: `${source}:${id}`,
      id,
      sourceId: source,
      layerId: layerId || "unknown",
      feature,
    };
  }

  function featureList(value) {
    if (!value || typeof value !== "object") return [];
    if (value.type === "FeatureCollection" && Array.isArray(value.features)) {
      return value.features;
    }
    if (value.type === "Feature") return [value];
    return [];
  }

  function parseArtifactCatalog(data) {
    const payload = data && typeof data === "object" ? data : {};
    const sourceValues = {
      network: payload.network,
      reviewable: payload.reviewable_network || payload.reviewable,
      places: payload.places,
      "reference-satn-options": payload.reference_satn_options || payload.referenceSatnOptions,
    };
    const sources = {};
    const artifacts = [];
    const byKey = new Map();
    for (const [sourceId, value] of Object.entries(sourceValues)) {
      const sourceArtifacts = [];
      for (const feature of featureList(value)) {
        const artifact = artifactRecord(feature, sourceId, sourceId);
        if (!artifact || byKey.has(artifact.key)) continue;
        byKey.set(artifact.key, artifact);
        sourceArtifacts.push(artifact);
        artifacts.push(artifact);
      }
      sources[sourceId] = Object.freeze(sourceArtifacts);
    }
    return {
      sources: Object.freeze(sources),
      artifacts: Object.freeze(artifacts),
      find(sourceId, id) {
        const source = normalizedSourceId(String(sourceId));
        return byKey.get(`${source}:${String(id)}`) || null;
      },
    };
  }

  function comparableGeometry(feature) {
    return feature && feature.geometry && (feature.geometry.type === "LineString" || feature.geometry.type === "MultiLineString");
  }

  function canCompareArtifact(artifact) {
    return Boolean(artifact && comparableGeometry(artifact.feature));
  }

  function createInitialLensState() {
    return {
      selectionKind: SelectionKind.NONE,
      comparisonKind: ComparisonKind.NONE,
      pinned: null,
      pinnedArtifact: null,
      comparisonArtifacts: [],
      previewArtifact: null,
    };
  }

  function sameArtifact(left, right) {
    return Boolean(left && right && (left.key || `${left.sourceId}:${left.id}`) === (right.key || `${right.sourceId}:${right.id}`));
  }

  function reduceLens(previous, action) {
    const state = previous || createInitialLensState();
    const event = action || {};
    const artifact = event.artifact || null;
    if (event.type === ActionType.CLOSE || event.type === "close") {
      return createInitialLensState();
    }
    if (event.type === ActionType.PREVIEW_ARTIFACT || event.type === "preview") {
      if (state.pinnedArtifact) {
        return {
          ...state,
          previewArtifact: artifact,
        };
      }
      return {
        ...createInitialLensState(),
        selectionKind: artifact ? SelectionKind.PREVIEW : SelectionKind.NONE,
        previewArtifact: artifact,
      };
    }
    if (event.type !== ActionType.TOGGLE_PIN_ARTIFACT && event.type !== "toggle-pin") {
      return state;
    }
    if (!artifact) return state;
    if (sameArtifact(state.pinnedArtifact, artifact)) {
      return createInitialLensState();
    }
    if (state.pinnedArtifact && canCompareArtifact(state.pinnedArtifact) && canCompareArtifact(artifact)) {
      return {
        ...state,
        selectionKind: SelectionKind.PINNED,
        comparisonKind: ComparisonKind.SEGMENTS,
        pinned: artifact.sourceId === "network" ? artifact.id : null,
        pinnedArtifact: artifact,
        comparisonArtifacts: [state.pinnedArtifact, artifact],
        previewArtifact: null,
      };
    }
    return {
      ...state,
      selectionKind: SelectionKind.PINNED,
      comparisonKind: ComparisonKind.NONE,
      pinned: artifact.sourceId === "network" ? artifact.id : null,
      pinnedArtifact: artifact,
      comparisonArtifacts: canCompareArtifact(artifact) ? [artifact] : [],
      previewArtifact: null,
    };
  }

  function projectLensView(state, options) {
    const current = state || createInitialLensState();
    const inspectionPathLength = Number(options && options.inspectionPathLength) || 0;
    const visible = current.selectionKind !== SelectionKind.NONE;
    const isCompare = current.comparisonKind === ComparisonKind.SEGMENTS;
    const isPinned = current.selectionKind === SelectionKind.PINNED;
    return {
      visible,
      state: isCompare ? "compare" : isPinned ? "pinned" : "preview",
      label: isCompare ? "Segment comparison" : isPinned ? "Pinned review" : "Quick view",
      selectionKind: current.selectionKind,
      comparisonKind: current.comparisonKind,
      pinned: current.pinned,
      pinnedArtifact: current.pinnedArtifact,
      comparisonArtifacts: current.comparisonArtifacts,
      previewArtifact: current.previewArtifact,
      showGradientDetails: Boolean(isPinned && inspectionPathLength > 0),
    };
  }

  return Object.freeze({
    ActionType,
    SelectionKind,
    ComparisonKind,
    stableArtifactId,
    artifactRecord,
    parseArtifactCatalog,
    canCompareArtifact,
    createInitialLensState,
    reduceLens,
    projectLensView,
  });
});
