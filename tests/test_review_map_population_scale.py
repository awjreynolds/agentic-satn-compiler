"""Focused contract checks for the review-map local population scale."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ASSET = Path(__file__).parents[1] / "src" / "satn" / "assets" / "review-map.js"


def test_population_scale_handles_large_whole_scenario_collection() -> None:
    """The fixed whole-scenario scale must not turn values into call arguments."""

    node = shutil.which("node")
    assert node is not None, "Node.js is required to check the shipped review-map asset"
    script = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  function populationDisplayScale(features) {");
const end = source.indexOf("\\n  function populationDisplayPaint", start);
if (start < 0 || end < 0) throw new Error("populationDisplayScale was not found");
const scaleSource = source.slice(start, end);
const populationDisplayScale = new Function(`${scaleSource}\nreturn populationDisplayScale;`)();
const features = Array.from({ length: 200_000 }, (_, index) => ({
  properties: {
    feature_type: index % 7 === 0 ? "other" : "population-display-section",
    total_residents: index % 19 === 0 ? "not-a-number" : index,
  },
}));
const scale = populationDisplayScale(features);
if (scale.maximum !== 199_999) throw new Error(`unexpected maximum ${scale.maximum}`);
if (JSON.stringify(scale.classes.map((item) => item.maximum)) !== "[66667,133333,199999]") {
  throw new Error(`unexpected classes ${JSON.stringify(scale.classes)}`);
}
console.log(JSON.stringify(scale));
"""
    result = subprocess.run(
        [node, "-e", script, str(ASSET)],
        check=True,
        capture_output=True,
        text=True,
    )
    scale = json.loads(result.stdout)
    assert scale["maximum"] == 199_999
    assert scale["classes"][-1]["maximum"] == 199_999
