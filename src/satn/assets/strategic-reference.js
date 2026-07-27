/* Strategic-only pre-script: preserve feature identity while exposing a map role. */
document.documentElement.classList.add("strategic-reference-present");
for (const feature of window.SATN_DATA?.network?.features || []) {
  if (feature.properties?.feature_type !== "strategic-destination-access-connection") continue;
  feature.properties.original_feature_type = feature.properties.feature_type;
  feature.properties.feature_type = "spine-access-connection";
  feature.properties.network_role_label = "Complementary strategic destination access";
  feature.properties.name = feature.properties.name || feature.properties.network_role_label;
}
