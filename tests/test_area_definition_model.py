"""Canonical regional inputs retain a narrow compatibility seam for councils."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import yaml

from satn import compile
from satn.models import AreaDefinition, CouncilConfig
from satn.sources import load_snapshot, snapshot

PROJECT = Path(__file__).parents[1]


def _fixture_root(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    return fixture


def test_weca_is_a_canonical_area_definition_with_canonical_serialized_identity() -> None:
    definition = AreaDefinition.from_yaml(PROJECT / "deployments" / "weca" / "area.yaml")

    assert type(definition) is AreaDefinition
    assert AreaDefinition is not CouncilConfig
    assert definition.area_id == "west-of-england"
    assert definition.area_name == "West of England Combined Authority area"
    assert definition.deployment_slug == "weca"
    assert definition.model_dump(mode="json")["area_id"] == "west-of-england"
    assert "council_id" not in definition.model_dump(mode="json")


def test_legacy_council_definition_uses_the_facade_and_preserves_legacy_serialization(
    tmp_path: Path,
) -> None:
    path = _fixture_root(tmp_path) / "council.yaml"

    parsed_by_public_loader = AreaDefinition.from_yaml(path)
    council = CouncilConfig.from_yaml(path)

    assert type(parsed_by_public_loader) is CouncilConfig
    assert council.council_id == council.area_id == "tiny-council"
    assert council.council_name == council.area_name == "Tiny Council"
    assert council.model_dump(mode="json")["council_id"] == "tiny-council"
    assert "area_id" not in council.model_dump(mode="json")


def test_legacy_council_facade_retains_real_pydantic_council_fields() -> None:
    raw = yaml.safe_load((PROJECT / "examples" / "fixture" / "council.yaml").read_text())
    assert isinstance(raw, dict)
    council = CouncilConfig(config_path=Path("/legacy/council.yaml"), **raw)

    assert council.model_dump(mode="json")["council_id"] == "tiny-council"
    assert '"council_name":"Tiny Council"' in council.model_dump_json()
    schema = CouncilConfig.model_json_schema()
    assert "description" not in schema
    assert {key: value for key, value in schema.items() if key != "$defs"} == {
        "properties": {
            "atm": {"$ref": "#/$defs/ATMConfig"},
            "compilation": {"$ref": "#/$defs/CompilationConfig"},
            "config_path": {
                "format": "path",
                "title": "Config Path",
                "type": "string",
            },
            "council_id": {"title": "Council Id", "type": "string"},
            "council_name": {"title": "Council Name", "type": "string"},
            "deployment_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Deployment Id",
            },
            "publication": {"$ref": "#/$defs/PublicationConfig"},
            "source": {"$ref": "#/$defs/SourceConfig"},
        },
        "required": [
            "config_path",
            "council_id",
            "council_name",
            "source",
            "publication",
        ],
        "title": "CouncilConfig",
        "type": "object",
    }
    assert {"council_id", "council_name"} <= set(schema["properties"])
    assert council.model_dump(include={"council_id", "council_name"}) == {
        "council_id": "tiny-council",
        "council_name": "Tiny Council",
    }
    assert "council_id" not in council.model_dump(exclude={"council_id"})

    council.council_id = "assigned-council"
    copied = council.model_copy(update={"council_id": "copied-council"})
    assert council.area_id == "assigned-council"
    assert copied.council_id == copied.area_id == "copied-council"


def test_legacy_council_facade_ignores_extras_and_retains_legacy_fingerprint() -> None:
    raw = yaml.safe_load((PROJECT / "examples" / "fixture" / "council.yaml").read_text())
    assert isinstance(raw, dict)
    raw["harmless_legacy_extra"] = "ignored"
    council = CouncilConfig(config_path=Path("/legacy/council.yaml"), **raw)

    assert "harmless_legacy_extra" not in council.model_dump()
    assert hashlib.sha256(council.model_dump_json().encode()).hexdigest() == (
        "cffd26c154089a73e1f3686bb761d5838250cd71533cf17528f2e50cee82fcdd"
    )


def test_single_and_multi_boundary_definitions_enter_public_snapshot_and_compile_interfaces(
    tmp_path: Path,
) -> None:
    fixture = _fixture_root(tmp_path)
    legacy = CouncilConfig.from_yaml(fixture / "council.yaml")
    assert snapshot(legacy).is_dir()
    assert "network" in load_snapshot(legacy)

    raw = yaml.safe_load((fixture / "council.yaml").read_text(encoding="utf-8"))
    raw["area_id"] = raw.pop("council_id")
    raw["area_name"] = "Tiny Regional Area"
    raw.pop("council_name")
    raw["deployment_id"] = "tiny-region"
    raw["source"]["snapshot_id"] = "fixture-regional-001"
    raw["source"]["osm_place_queries"] = ["First constituent", "Second constituent"]
    regional_path = fixture / "regional-area.yaml"
    regional_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    regional = AreaDefinition.from_yaml(regional_path)

    assert type(regional) is AreaDefinition
    assert len(regional.source.boundary_queries) == 2
    assert snapshot(regional).is_dir()
    assert "network" in load_snapshot(regional)
    assert compile(regional).status == "complete"
