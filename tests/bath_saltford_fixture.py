"""Small governed Bath-Saltford proving fixture support.

Names model the real decision question, but every spatial coordinate and
evidence assertion is deliberately synthetic/minimised.  Keep this fixture
outside production modules so it cannot be mistaken for a current B&NES or
WECA source.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from satn.models import CouncilConfig
from satn.network_selection import (
    GovernedEvidenceArtifactConfig,
    PopulationReachEvidenceConfig,
    SchoolRegisterEvidenceConfig,
    StrategicEducationDestinationAdmissionConfig,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "bath-saltford"


def _artifact(
    name: str,
    *,
    redistribution: str = "aggregate-only",
) -> GovernedEvidenceArtifactConfig:
    """Bind one immutable fixture file by its actual local content identity."""

    path = FIXTURE_ROOT / "evidence" / name
    source_ids = {
        "school-register.json": "synthetic-banes-school-register",
        "strategic-education-admissions.json": "synthetic-bath-spa-admissions",
    }
    return GovernedEvidenceArtifactConfig(
        source_id=source_ids.get(
            name,
            f"synthetic-bath-saltford-{name.removesuffix('.geojson').removesuffix('.json')}",
        ),
        path=path,
        release="Synthetic Bath-Saltford proving evidence 2026-07",
        effective_date=(
            date(2021, 3, 21) if "population" in name or "output" in name else date(2026, 7, 1)
        ),
        licence="Open Government Licence v3.0 (synthetic test record)",
        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        redistribution=redistribution,
    )


def configured_bath_saltford(tmp_path: Path) -> CouncilConfig:
    """Return a fixture config whose snapshot and publication stay under ``tmp_path``.

    The exact profile and source files are parsed from YAML.  Evidence remains
    static and is read through the production strict loaders; only volatile
    output locations are redirected for the test.
    """

    config = CouncilConfig.from_yaml(FIXTURE_ROOT / "bath-saltford.yaml")
    config.source.snapshot_dir = tmp_path / "snapshots"
    config.publication.output_dir = tmp_path / "output"
    config.source.population_reach_evidence = PopulationReachEvidenceConfig(
        output_area_geometry=_artifact("output-areas.geojson"),
        population_weighted_centroids=_artifact("population-weighted-centroids.geojson"),
        usual_resident_counts=_artifact("usual-resident-counts.json"),
    )
    config.source.school_register_evidence = SchoolRegisterEvidenceConfig(
        school_register=_artifact("school-register.json", redistribution="public")
    )
    config.source.strategic_education_destination_admissions = (
        StrategicEducationDestinationAdmissionConfig(
            admissions=_artifact("strategic-education-admissions.json", redistribution="public")
        )
    )
    config.source.network_selection_as_at = date(2026, 7, 27)
    config.source.network_selection_school_register_max_age_days = 90
    config.source.network_selection_strategic_admissions_max_age_days = 90
    return config
