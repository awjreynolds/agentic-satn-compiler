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
const spineFeatures = networkFeatures.filter(
  (feature) => feature.properties?.feature_type === "strategic-spine",
);
for (const feature of destinationFeatures) {
  feature.properties.original_feature_type = feature.properties.feature_type;
  feature.properties.network_role_label = "Complementary strategic destination access";
  feature.properties.name = feature.properties.name || feature.properties.network_role_label;
}
window.SATN_STRATEGIC_PRESENTATION = {
  spineLayer: "strategic-reference-spine",
  spineLabel: "Reference SATN interurban spine",
  destinationLayer: "strategic-destination-access",
  destinationLabel: "Complementary strategic destination access",
  optionsLayer: "strategic-alignment-options",
  optionsLabel: "Reviewed alignment options (hidden by default)",
};
const controls = document.querySelector("#layer-controls");
const legend = document.querySelector("#map-legend ul");
if (controls && spineFeatures.length) {
  controls.insertAdjacentHTML(
    "beforeend",
    '<div class="layer-row"><label><input id="layer-strategic-reference-spine" type="checkbox" checked aria-describedby="legend-strategic-reference-spine"> Reference SATN interurban spine</label></div>',
  );
}
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
    '<li id="legend-strategic-destination-access"><span class="map-key line" aria-hidden="true"></span>Purple dashed complementary destination access</li>',
  );
}
if (legend && spineFeatures.length) {
  legend.insertAdjacentHTML(
    "beforeend",
    '<li id="legend-strategic-reference-spine"><span class="map-key line" aria-hidden="true"></span>Solid selected authoritative Reference SATN interurban spine</li>',
  );
}
if (legend && alignmentOptions.features.length) {
  legend.insertAdjacentHTML(
    "beforeend",
    '<li id="legend-strategic-alignment-options" hidden><span class="map-key line" aria-hidden="true"></span>Green dashed selected, purple dashed complementary, grey dashed rejected alignment options; review-only</li>',
  );
}
let strategicLayersInstalled = false;
window.addEventListener("satn-map-ready", (event) => {
  const map = event.detail?.map;
  if (!map || strategicLayersInstalled) return;
  strategicLayersInstalled = true;
  const rawStrategicNetworkControl = document.querySelector(
    "#layer-strategic-network",
  );
  const rawStrategicNetworkLegend = document.querySelector(
    "#legend-strategic-network",
  );
  if (map.getLayer("strategic-network")) {
    map.setLayoutProperty("strategic-network", "visibility", "none");
  }
  if (rawStrategicNetworkControl) rawStrategicNetworkControl.checked = false;
  if (rawStrategicNetworkLegend) rawStrategicNetworkLegend.hidden = true;
  if (spineFeatures.length) {
    map.addLayer({
      id: "strategic-reference-spine",
      type: "line",
      source: "network",
      filter: ["==", ["get", "feature_type"], "strategic-spine"],
      paint: {
        "line-color": "#1b5e20",
        "line-width": 6,
      },
    });
    document
      .querySelector("#layer-strategic-reference-spine")
      ?.addEventListener("change", (changeEvent) => {
        const visible = changeEvent.target.checked;
        map.setLayoutProperty(
          "strategic-reference-spine",
          "visibility",
          visible ? "visible" : "none",
        );
        document.querySelector("#legend-strategic-reference-spine").hidden = !visible;
      });
  }
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
      ?.addEventListener("change", (changeEvent) => {
        const visible = changeEvent.target.checked;
        map.setLayoutProperty(
          "strategic-destination-access",
          "visibility",
          visible ? "visible" : "none",
        );
        document.querySelector("#legend-strategic-destination-access").hidden = !visible;
      });
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
      ?.addEventListener("change", (changeEvent) => {
        const visible = changeEvent.target.checked;
        map.setLayoutProperty(
          "strategic-alignment-options",
          "visibility",
          visible ? "visible" : "none",
        );
        document.querySelector("#legend-strategic-alignment-options").hidden = !visible;
      });
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
