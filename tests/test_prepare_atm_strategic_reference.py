import sys
import unittest
from pathlib import Path

from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prepare_atm_strategic_reference import (
    ATTRIBUTION,
    SOURCE_URL,
    TO_WGS84,
    _build_strategic_network_ledger,
    build_strategic_network_ledger,
    prepare_collection,
    prepare_feature,
)


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

    def test_builds_scoped_geometry_ledger_without_invented_graph_bindings(self):
        first = TO_WGS84.transform(370800, 157027)
        last = TO_WGS84.transform(370900, 157027)
        boundary = box(first[0] + 0.0001, first[1] - 0.001, last[0] - 0.0001, last[1] + 0.001)
        center = boundary.centroid
        inside_line = [[center.x - 0.00005, center.y], [center.x + 0.00005, center.y]]
        crossing_line = [[boundary.bounds[0] - 0.001, center.y], [center.x, center.y]]
        outside_line = [[center.x + 1, center.y], [center.x + 1.001, center.y]]
        raw = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "final_february25.1",
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [[[370800, 157027], [370900, 157027]]],
                    },
                    "properties": {"fid": 1, "type_2": "Strategic"},
                },
                {
                    "type": "Feature",
                    "id": "final_february25.2",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[370800, 157027], [370900, 157027]],
                    },
                    "properties": {"fid": 2, "type_2": "Quiet"},
                },
            ],
        }
        prepared = prepare_collection(raw)
        network = {
            "type": "FeatureCollection",
            "features": [
                self._network_edge(1, 2, 101, crossing_line),
                self._network_edge(3, 4, 102, outside_line),
                self._network_edge(5, 6, 103, inside_line),
                self._network_edge(7, 8, 104, inside_line),
            ],
        }
        baseline = {
            "report": {
                "candidates": [{"path_edge_ids": ["edge:1:2:0:0:osm-101", "edge:3:4:0:1:osm-102"]}],
                "source_inventory": [
                    {"baseline_role": "a-road", "graph_edge_ids": ["edge:5:6:0:2:osm-103"]},
                    {"baseline_role": "current-ncn", "graph_edge_ids": ["edge:7:8:0:3:osm-104"]},
                ],
            }
        }

        ledger = build_strategic_network_ledger(
            prepared,
            {"type": "Feature", "geometry": boundary.__geo_interface__, "properties": {}},
            baseline,
            network,
        )

        strategic = ledger["strategic_network"]
        self.assertEqual(ledger["decisions"], [])
        self.assertEqual(strategic["selected_graph_edge_ids"], [])
        self.assertEqual(
            strategic["deselected_graph_edge_ids"],
            ["edge:1:2:0:0:osm-101", "edge:5:6:0:2:osm-103"],
        )
        self.assertNotIn("scoped_deselection_evidence", strategic)
        self.assertEqual(len(strategic["selected_alignments"]), 1)
        alignment = strategic["selected_alignments"][0]
        self.assertEqual(alignment["source_id"], "final_february25.1")
        self.assertEqual(set(alignment), {"source_id", "geometry"})
        self.assertEqual(len(alignment["geometry"]), 1)
        self.assertEqual(strategic["source_refs"], [SOURCE_URL])
        self.assertEqual(strategic["attribution"], ATTRIBUTION)
        self.assertIn("unresolved source-to-graph links", strategic["rationale"])
        self.assertEqual(strategic["scope_geometry"]["type"], "Polygon")

        _, evidence = _build_strategic_network_ledger(
            prepared,
            {"type": "Feature", "geometry": boundary.__geo_interface__, "properties": {}},
            baseline,
            network,
        )
        self.assertEqual(
            evidence,
            {
                "candidate_path_edge_id_count": 1,
                "a_road_target_edge_id_count": 1,
                "overlap_edge_id_count": 0,
                "boundary_crossing_edge_id_count": 1,
            },
        )

    @staticmethod
    def _network_edge(u, v, osmid, coordinates):
        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": {"u": u, "v": v, "key": 0, "osmid": osmid},
        }


if __name__ == "__main__":
    unittest.main()
