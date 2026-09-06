from pathlib import Path

ASSETS = Path(__file__).parents[1] / "src" / "satn" / "assets"


def test_gradient_inspection_interface_contract() -> None:
    html = (ASSETS / "review-map.html").read_text(encoding="utf-8")
    script = (ASSETS / "review-map.js").read_text(encoding="utf-8")
    for identifier in (
        "layer-rail",
        "deployment-context",
        "compilation-status",
        "deployment-status",
        "layer-authority-boundaries",
        "gradient-path-start",
        "gradient-path-append",
        "gradient-path-remove",
        "gradient-path-reverse",
        "gradient-path-reset",
        "review-lens",
        "review-lens-close",
        "review-gradient-details",
        "feature-details",
        "linear-evidence-view",
        "terrain-mode",
    ):
        assert f'id="{identifier}"' in html
    assert 'id="linear-evidence-panel"' not in html
    assert 'id="review-lens" role="region" aria-label="Route review lens"' in html
    assert 'role="dialog"' not in html
    assert ">Show gradient details</button>" in html
    assert '<h3 id="linear-evidence-heading">Linear Evidence</h3>' in html
    assert 'id="linear-evidence-view" aria-labelledby="linear-evidence-heading" hidden' in html
    assert html.index('id="feature-details"') > html.index('<section class="workspace"')
    assert 'id="criteria-controls"' not in html
    assert 'id="criteria-panel"' not in html
    assert "tiles.mapterhorn.com/tilejson.json" in script
    assert 'event.sourceId === "mapterhorn-dem"' in script
    assert "Terrain provider timed out." in script
    assert "Gradient · ${windowMetres} m" in script
    assert "form a cycle or branch" in script
    assert "does not share its junction" in script
    assert "inspection-path-direction" in script
    assert "ensureEvidenceGroupLoaded" in script
    assert "topography_manifest_url" in script
    assert "profile_evidence_index_url" in script
    assert 'navigator.serviceWorker.register("service-worker.js")' in script
    assert "gradient-overview" in script
    assert "const loadingTopographyShards = new Map()" in script
    assert 'map.on("moveend"' in script
    assert "profileEvidenceIndexPromise" in script
    assert "loadingProfileChunks.has(chunk.path)" in script
    assert "isProgressiveDeployment" in script
    assert 'status.setAttribute("aria-live", "polite")' in script
    assert "Desktop is recommended" in html
    assert "Compiler timing unavailable." in html
    assert "Compiled ${completed} · compiler time ${duration}" in script
    assert "This deployment bundles its available evidence" in script
    assert "MapToolkit" not in script
    assert (
        '"cross-spine-connector"'
        not in script.split("const gradientPathTypes", maxsplit=1)[1].split("]);", maxsplit=1)[0]
    )


def test_reviewable_network_layer_defaults_and_semantics_are_explicit() -> None:
    html = (ASSETS / "review-map.html").read_text(encoding="utf-8")
    script = (ASSETS / "review-map.js").read_text(encoding="utf-8")
    css = (ASSETS / "review-map.css").read_text(encoding="utf-8")
    for control_id in ("layer-strategic-network", "layer-places"):
        assert f'id="{control_id}" type="checkbox" checked' in html
    assert 'id="layer-mapped-active-travel-assets" type="checkbox" checked' not in html
    assert 'id="layer-strategic-network" type="checkbox" checked' in html
    assert "> Strategic Main Network</label>" in html
    assert 'id="layer-access-support" type="checkbox"' in html
    assert 'id="layer-access-support" type="checkbox" checked' not in html
    assert 'id="layer-urban-spines" type="checkbox" checked' not in html
    for control_id in (
        "layer-alignment-review",
        "layer-reviewable-gaps",
        "layer-officer-divergences",
    ):
        assert f'id="{control_id}" type="checkbox" checked' not in html
    for control_id in (
        "layer-existing-assets",
        "layer-upgradeable-assets",
        "layer-unselected-candidates",
        "layer-dft-traffic",
        "layer-authority-boundaries",
        "layer-gaps-warnings",
    ):
        assert f'id="{control_id}" type="checkbox" checked' not in html
    assert "const reviewable = data.reviewable" in script
    assert "reviewable-strategic-network-halo" in script
    assert 'id: "reviewable-urban-strategic-network"' in script
    assert 'id: "reviewable-access-support"' in script
    assert '["==", ["get", "layer"], "Access Support"]' in script
    assert (
        '"community-access",\n        "school-access",\n        "strategic-destination-access"'
        in script
    )
    assert 'id: "reviewable-required-connections"' in script
    assert 'type: "symbol"' in script
    assert '"text-field": [' in script
    assert script.count('["literal", [') >= 7
    assert "primary_alignment_basis" in script
    assert '"strategic-reference", "#5e35b1"' in script
    assert ".map-key.basis-strategic-reference" in css
    assert "display_state" in script
    assert "reviewable-gap-endpoint" in script
    assert "Network coverage gap" in script
    assert "isAroadComponentGapMarker" in script
    assert "Disconnected A-road component (representative location)" in script
    assert "gap_marker_disclaimer" in script
    assert "coverage point ${properties.proof_point_position}" in script
    assert '"unknown endpoint"' not in script
    assert "reviewable-dft-traffic-points" in script
    assert "bounded-candidate-route-evidence-no-point" not in script
    assert "bounded candidate-route evidence" in html
    assert 'id="reviewable-findings"' in html
    assert "representative locations of disconnected official A-road components" in html
    assert "No direct connection between component markers is proposed" in html
    assert "endpoint geometry unavailable" in script
    assert "renderReviewableFindings()" in script
    artifact_role_source = script.split("function artifactRole", maxsplit=1)[1]
    assert artifact_role_source.index(
        'if (featureType === "reviewable-gap-endpoint")'
    ) < artifact_role_source.index('if (properties.layer === "Strategic Main Network")')
    assert "const hasBackboneAndAccessNetwork = network.features.some(" in script
    assert "const hasSemanticStrategicMainNetwork = reviewable.features.some(" in script
    assert "const hasSemanticAccessSupport = reviewable.features.some(" in script
    assert 'feature.properties?.layer === "Strategic Main Network"' in script
    assert 'feature.properties?.layer === "Access Support"' in script
    assert "!hasBackboneAndAccessNetwork && !hasReviewableRoutes" in script
    assert 'id: "reviewable-strategic-main-network"' in script
    main_layer = script.split('id: "reviewable-strategic-main-network"', maxsplit=1)[1]
    main_layer = main_layer.split('id: "reviewable-access-support"', maxsplit=1)[0]
    assert '"line-dasharray"' not in main_layer
    assert '["==", ["get", "layer"], "Strategic Main Network"]' in script
    assert '"layer-strategic-network": hasSemanticStrategicMainNetwork' in script
    assert (
        '"layer-strategic-network": hasSemanticStrategicMainNetwork\n'
        '      ? ["reviewable-strategic-main-network"]' in script
    )
    assert '"layer-access-support": hasSemanticAccessSupport' in script
    assert (
        '"layer-access-support": hasSemanticAccessSupport\n'
        '      ? ["reviewable-access-support"]' in script
    )
    assert '["strategic-spines", "reviewable-urban-strategic-network"]' in script
    assert 'id: "strategic-spines"' in script
    assert 'id: "mapped-active-travel-assets"' in script
    assert '"asset_kind"], "mapped-cycleway"' in script
    assert '"layer-mapped-active-travel-assets": ["mapped-active-travel-assets"]' in script
    assert "appendArtifactAppearance" in script
    assert 'summary.textContent = "Colour and line treatment"' in script
    assert "summary.textContent = `Technical details · All contextual properties (" in script
    assert 'addDefinition(list, "Stable ID", artifact.id)' in script
    assert 'addDefinition(list, "Raw type"' in script
    assert 'addDefinition(list, "Rendered layer"' in script
    assert (
        "if (hasDataValue(accessStatus)) "
        'addDefinition(list, "Access status", humanStatus(accessStatus));'
    ) in script
    assert 'map.addSource("review-lens-highlight"' in script
    assert "function setLensArtifactHighlight(artifact)" in script
    assert '"review-lens-highlight-line"' in script
    assert '"review-lens-highlight-point"' in script
    assert "presentationOnlyLayers" in script
    assert "loadDefaultEvidenceForCurrentView" in script
    assert 'dataset.defaultEvidenceReady = "true"' in script
    assert '"layer-alignment-review": [' in script
    assert '"reviewable-required-connections"' in script
    assert script.count("Object.entries(controlLayerGroups).forEach") == 2
    assert "function syncControlledLayerVisibility()" in script
    assert "const requested = owners.some(isControlChecked)" in script
    assert "topographyLayerIsVisible" in script
    assert "orderLayersForReadableMainNetwork" in script
    sync_start = script.index("  function syncControlledLayerVisibility()")
    sync_end = script.index("\n  function orderLayersForReadableMainNetwork", sync_start)
    sync_source = script[sync_start:sync_end]
    assert "ownersByLayer" in sync_source
    assert "owners.some(isControlChecked)" in sync_source
    bind_start = script.index("  function bindControls()")
    bind_end = script.index("\n  function extendBounds", bind_start)
    bind_source = script[bind_start:bind_end]
    assert "syncControlledLayerVisibility();" in bind_source
    assert "layers.forEach" not in bind_source
    assert '"line-color": "#c0392b"' in main_layer
    assert "layer-disclosure" in css
    assert "artifact-context" in css
    assert '#review-lens[data-state="pinned"]' in css
    assert 'layout: { visibility: usesReviewableStrategicFallback ? "visible" : "none" }' in script
    assert 'usesLegacyStrategicFallback ? ["strategic-network"] : []' in script
    assert 'visibility: usesLegacyStrategicFallback ? "visible" : "none"' in script
    for label in (
        "Existing provision",
        "Upgrade required",
        "Proposed new link",
        "Unresolved gap",
        "Undetermined",
        "Current NCN",
        "NCN link",
        "Reclassified NCN",
        "Greenway",
        "Mapped cycleway",
        "Cycle track",
        "Shared-use path",
        "Public footpath",
        "Public bridleway",
        "Restricted byway",
        "Byway open to all traffic",
        "PROW class unknown",
        "Former railway",
        "Local connector",
        "A road",
        "Adopted governed Strategic Reference alignment",
        "B road",
        "Classified unnumbered road",
        "Unclassified road",
        "Proposed new corridor",
    ):
        assert label in html
