from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_planning_publication import _validated_output

from satn.planning_publication import publish_planning_output


@pytest.mark.browser
def test_planning_map_default_shows_departure_and_accessible_details(tmp_path: Path) -> None:
    output = _validated_output()
    output["departures"][0].update(
        {
            "decision_ref": "decision-1",
            "decision_class": "classifier",
            "decision_origin": "Jev",
            "history_origin": "recorded-history",
            "provider": "typesafe-provider",
            "model": "Jev",
        }
    )
    publication = publish_planning_output(
        output,
        tmp_path,
        {"history_ref": "history-1", "branch_id": "branch-main"},
    )
    page_url = (Path(publication["publication_dir"]) / "review-map" / "index.html").as_uri()
    screenshot = Path("/tmp/satn-planning-map-default.png")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(page_url)
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")

        assert page.locator("body.planning-mode").count() == 1
        assert page.locator("#planning-summary").is_visible()
        assert page.locator("#map-legend").is_visible()
        assert page.locator("#map-legend[open]").count() == 1
        assert page.locator("#planning-legend-unknown").is_visible()
        assert page.get_by_text(
            "Selected alignment — current/future status unknown", exact=True
        ).is_visible()
        assert page.get_by_text(
            "This proposed network shows validated decisions", exact=False
        ).is_visible()
        assert page.get_by_text(
            "branch: branch-main · history: history-1", exact=False
        ).is_visible()
        assert page.get_by_text("A-road corridor departure", exact=False).count() >= 1
        assert page.get_by_text("decision class classifier", exact=False).is_visible()
        assert page.get_by_text("decision origin Jev", exact=False).is_visible()
        assert page.get_by_text("history origin recorded-history", exact=False).is_visible()
        assert page.get_by_text("provider typesafe-provider", exact=False).is_visible()
        assert page.get_by_text("model Jev", exact=False).is_visible()
        assert page.get_by_text("selected alternative candidate-current", exact=False).count() >= 1
        assert page.get_by_text("Unknown facts and planning gaps", exact=True).count() == 1
        assert (
            page.get_by_text(
                "A-road corridor departure — affected source section", exact=True
            ).count()
            == 1
        )

        layer_state = page.evaluate(
            """() => ({
              departureVisible: window.SATN_REVIEW_MAP.getLayoutProperty(
                'planning-departures', 'visibility'
              ) !== 'none',
              selectedUnknownVisible: window.SATN_REVIEW_MAP.getLayoutProperty(
                'planning-selected-unknown', 'visibility'
              ) !== 'none',
              sourceVisible: window.SATN_REVIEW_MAP.getLayoutProperty(
                'planning-source', 'visibility'
              ) !== 'none',
              departureFeatures: window.SATN_REVIEW_MAP.getSource(
                'planning-output'
              )._data.features.filter(
                feature => feature.properties?.feature_type === 'planning-departure'
              ).length,
              selectedUnknownFeatures: window.SATN_REVIEW_MAP.getSource(
                'planning-output'
              )._data.features.filter(
                feature => feature.properties?.feature_type === 'planning-selected-unknown'
              ).length,
              sourceOnlyFeatures: window.SATN_REVIEW_MAP.getSource(
                'planning-output'
              )._data.features.filter(
                feature => feature.properties?.source_corridor_id === 'corridor-source-only'
              ).length
            })"""
        )
        assert layer_state == {
            "departureVisible": True,
            "selectedUnknownVisible": True,
            "sourceVisible": True,
            "departureFeatures": 2,
            "selectedUnknownFeatures": 1,
            "sourceOnlyFeatures": 1,
        }
        assert page.evaluate(
            """() => Array.from(document.querySelectorAll(
              '#map-legend .planning-only .map-key'
            )).filter((element) => !element.closest('[hidden]')).map((element) => {
              const box = element.getBoundingClientRect();
              return {width: box.width, height: box.height};
            }).every((box) => box.width > 0 && box.height > 0)"""
        )
        assert page.evaluate(
            """() => {
              const legendStyles = (id) => {
                const style = getComputedStyle(document.querySelector(`#${id} .map-key`));
                return {color: style.borderTopColor, lineStyle: style.borderTopStyle};
              };
              return {
                departure: {
                  legend: legendStyles('planning-legend-departure'),
                  map: {
                    color: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-departures', 'line-color'
                    ),
                    dash: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-departures', 'line-dasharray'
                    )
                  }
                },
                current: {
                  legend: legendStyles('planning-legend-current'),
                  map: {
                    color: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-selected-current', 'line-color'
                    ),
                    dash: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-selected-current', 'line-dasharray'
                    )
                  }
                },
                future: {
                  legend: legendStyles('planning-legend-future'),
                  map: {
                    color: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-selected-future', 'line-color'
                    ),
                    dash: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-selected-future', 'line-dasharray'
                    )
                  }
                },
                unknown: {
                  legend: legendStyles('planning-legend-unknown'),
                  map: {
                    color: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-selected-unknown', 'line-color'
                    ),
                    dash: window.SATN_REVIEW_MAP.getPaintProperty(
                      'planning-selected-unknown', 'line-dasharray'
                    )
                  }
                }
              };
            }"""
        ) == {
            "departure": {
                "legend": {"color": "rgb(183, 28, 28)", "lineStyle": "dashed"},
                "map": {"color": "#b71c1c", "dash": [1, 1]},
            },
            "current": {
                "legend": {"color": "rgb(27, 94, 32)", "lineStyle": "solid"},
                "map": {"color": "#1b5e20", "dash": None},
            },
            "future": {
                "legend": {"color": "rgb(21, 101, 192)", "lineStyle": "dashed"},
                "map": {"color": "#1565c0", "dash": [1.2, 1.2]},
            },
            "unknown": {
                "legend": {"color": "rgb(106, 27, 154)", "lineStyle": "dotted"},
                "map": {"color": "#6a1b9a", "dash": [0.5, 1.5]},
            },
        }
        page.screenshot(path=str(screenshot), full_page=True)
        browser.close()

    assert screenshot.exists()
