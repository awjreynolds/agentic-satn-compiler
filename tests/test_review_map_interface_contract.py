from pathlib import Path

ASSETS = Path(__file__).parents[1] / "src" / "satn" / "assets"


def test_gradient_inspection_interface_contract() -> None:
    html = (ASSETS / "review-map.html").read_text(encoding="utf-8")
    script = (ASSETS / "review-map.js").read_text(encoding="utf-8")
    for identifier in (
        "layer-rail",
        "deployment-context",
        "deployment-status",
        "layer-authority-boundaries",
        "gradient-path-start",
        "gradient-path-append",
        "gradient-path-remove",
        "gradient-path-reverse",
        "gradient-path-reset",
        "linear-evidence-panel",
        "evidence-panel-heading",
        "feature-details",
        "linear-evidence-view",
        "terrain-mode",
    ):
        assert f'id="{identifier}"' in html
    assert '<h2 id="evidence-panel-heading">Evidence Panel</h2>' in html
    assert '<h3 id="details-heading">Artifact evidence</h3>' in html
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
    assert "This legacy review map bundles its available evidence" in script
    assert "MapToolkit" not in script
    assert '"cross-spine-connector"' not in script.split(
        "const gradientPathTypes", maxsplit=1
    )[1].split("]);", maxsplit=1)[0]
