from __future__ import annotations

from pathlib import Path

from satn.deployment_catalogue import load_deployment_catalogue
from satn.models import AreaDefinition

PROJECT = Path(__file__).parents[1]
DEFINITION = PROJECT / "deployments" / "torbay-south-devon" / "area.yaml"


def test_torbay_south_devon_definition_is_a_clean_cross_boundary_poc() -> None:
    definition = AreaDefinition.from_yaml(DEFINITION)

    assert definition.area_id == "torbay-south-devon"
    assert definition.deployment_slug == "torbay-south-devon"
    assert definition.source.boundary_queries == (
        "Torbay, England, United Kingdom",
        "Teignbridge, Devon, England, United Kingdom",
        "South Hams, Devon, England, United Kingdom",
    )
    assert definition.source.snapshot_id == "torbay-south-devon-osm-2026-07-30"
    assert definition.compilation.agent.provider == "fake"
    assert definition.compilation.agent.response_mode == "direct-runtime"
    assert definition.atm.enabled is False
    assert definition.publication.output_dir == (
        PROJECT / "build" / "compiled" / "torbay-south-devon"
    )
    assert not (DEFINITION.parent / "scenarios.yaml").exists()


def test_torbay_south_devon_is_listed_as_one_area_deployment() -> None:
    catalogue = load_deployment_catalogue(PROJECT / "deployments" / "catalogue.yaml")
    deployment = next(
        item for item in catalogue.deployments if item.deployment_id == "torbay-south-devon"
    )

    assert deployment.area_id == "torbay-south-devon"
    assert deployment.area_definition == "torbay-south-devon/area.yaml"
    assert deployment.deployment_path == "deployments/torbay-south-devon/"
