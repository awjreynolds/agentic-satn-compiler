/* Strategic-only pre-script: preserve feature identity while exposing a map role. */
document.documentElement.classList.add("strategic-reference-present");
const networkFeatures = window.SATN_DATA?.network?.features || [];
const alignmentOptions = window.SATN_DATA?.strategic_reference?.alignment_options || {
  type: "FeatureCollection",
  features: [],
};
const destinationFeatures = networkFeatures.filter(
  (feature) =>
    feature.properties?.feature_type === "strategic-destination-access-connection",
);
for (const feature of destinationFeatures) {
  feature.properties.original_feature_type = feature.properties.feature_type;
  feature.properties.network_role_label = "Complementary strategic destination access";
  feature.properties.name = feature.properties.name || feature.properties.network_role_label;
}
window.SATN_STRATEGIC_PRESENTATION = {
  destinationLayer: "strategic-destination-access",
  destinationLabel: "Complementary strategic destination access",
  optionsLayer: "strategic-alignment-options",
  optionsLabel: "Reviewed alignment options (hidden by default)",
};
const controls = document.querySelector("#layer-controls");
const legend = document.querySelector("#map-legend ul");
if (controls && destinationFeatures.length) {
  controls.insertAdjacentHTML(
    "beforeend",
    '<div class="layer-row"><label><input id="layer-strategic-destination-access" type="checkbox" checked aria-describedby="legend-strategic-destination-access"> Complementary destination access</label></div>',
  );
}
if (controls && alignmentOptions.features.length) {
  controls.insertAdjacentHTML(
    "beforeend",
    '<div class="layer-row"><label><input id="layer-strategic-alignment-options" type="checkbox" aria-describedby="legend-strategic-alignment-options"> Reviewed alignment options</label></div>',
  );
}
if (legend && destinationFeatures.length) {
  legend.insertAdjacentHTML(
    "beforeend",
    '<li id="legend-strategic-destination-access">Complementary strategic destination access</li>',
  );
}
if (legend && alignmentOptions.features.length) {
  legend.insertAdjacentHTML(
    "beforeend",
    '<li id="legend-strategic-alignment-options">Reviewed alignment options (hidden by default)</li>',
  );
}
let strategicLayersInstalled = false;
window.addEventListener("satn-map-ready", (event) => {
  const map = event.detail?.map;
  if (!map || strategicLayersInstalled) return;
  strategicLayersInstalled = true;
  if (destinationFeatures.length) {
    map.addLayer({
      id: "strategic-destination-access",
      type: "line",
      source: "network",
      filter: [
        "==",
        ["get", "feature_type"],
        "strategic-destination-access-connection",
      ],
      paint: {
        "line-color": "#6a1b9a",
        "line-width": 5,
        "line-dasharray": [2, 1],
      },
    });
    document
      .querySelector("#layer-strategic-destination-access")
      ?.addEventListener("change", (changeEvent) =>
        map.setLayoutProperty(
          "strategic-destination-access",
          "visibility",
          changeEvent.target.checked ? "visible" : "none",
        ),
      );
  }
  if (alignmentOptions.features.length) {
    map.addSource("strategic-alignment-options", {
      type: "geojson",
      data: alignmentOptions,
    });
    map.addLayer({
      id: "strategic-alignment-options",
      type: "line",
      source: "strategic-alignment-options",
      layout: { visibility: "none" },
      paint: {
        "line-color": [
          "match",
          ["get", "disposition"],
          "selected",
          "#1b5e20",
          "complementary",
          "#6a1b9a",
          "#455a64",
        ],
        "line-width": 3,
        "line-dasharray": [1, 2],
      },
    });
    document
      .querySelector("#layer-strategic-alignment-options")
      ?.addEventListener("change", (changeEvent) =>
        map.setLayoutProperty(
          "strategic-alignment-options",
          "visibility",
          changeEvent.target.checked ? "visible" : "none",
        ),
      );
  }
});
new MutationObserver(() => {
  const map = window.SATN_REVIEW_MAP;
  if (map && map.getSource("network")) {
    window.dispatchEvent(new CustomEvent("satn-map-ready", { detail: { map } }));
  }
}).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ["data-map-ready"],
});
