import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prepare_atm_strategic_reference import prepare_feature


class PrepareFeatureTest(unittest.TestCase):
    def test_transforms_live_strategic_point_and_keeps_source_identity(self):
        feature = {
            "type": "Feature",
            "id": "final_february25.712",
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[370864.9159, 157027.0912], [370942.3895, 157080.7901]]],
            },
            "properties": {"fid": 809, "name": None, "type_2": "Strategic"},
        }

        prepared = prepare_feature(feature)

        self.assertEqual(prepared["id"], "final_february25.712")
        longitude, latitude = prepared["geometry"]["coordinates"][0][0]
        self.assertAlmostEqual(longitude, -2.4193894462808476, places=9)
        self.assertAlmostEqual(latitude, 51.3116049754931, places=9)
        self.assertEqual(prepared["properties"]["source_feature_id"], "final_february25.712")
        self.assertEqual(prepared["properties"]["source_fid"], 809)
        self.assertEqual(prepared["properties"]["source_crs"], "EPSG:27700")
        self.assertNotIn("coordinates_epsg27700", prepared["properties"])

    def test_excludes_quiet_feature(self):
        feature = {
            "type": "Feature",
            "id": "final_february25.quiet",
            "geometry": {"type": "LineString", "coordinates": [[370000, 157000], [370100, 157100]]},
            "properties": {"fid": 810, "type_2": "Quiet"},
        }

        self.assertIsNone(prepare_feature(feature))


if __name__ == "__main__":
    unittest.main()
