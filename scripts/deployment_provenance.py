"""Generate or verify a compact tracked deployment provenance lock."""

from __future__ import annotations

import argparse
from pathlib import Path

from satn.deployment_provenance import generate_lock, verify_lock
from satn.models import AreaDefinition


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("area_definition", type=Path)
    parser.add_argument("--deployment", type=Path)
    args = parser.parse_args()
    definition = AreaDefinition.from_yaml(args.area_definition)
    result = (
        generate_lock(definition, deployment=args.deployment)
        if args.command == "generate"
        else verify_lock(definition, deployment=args.deployment)
    )
    print(
        result
        if isinstance(result, Path)
        else definition.config_path.parent / "provenance-lock.json"
    )


if __name__ == "__main__":
    main()
