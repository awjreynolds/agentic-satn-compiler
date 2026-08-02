#!/usr/bin/env python3
"""Validate the canonical documentation contract without network access."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from satn.models import AreaDefinition

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOCS = (
    Path("README.md"),
    Path("docs/README.md"),
    Path("docs/compiler-architecture.md"),
    Path("docs/getting-started/agent-quickstart.md"),
    Path("docs/guides/reproduce-banes.md"),
    Path("docs/guides/build-a-new-area.md"),
    Path("docs/guides/publish-a-deployment.md"),
    Path("docs/concepts/feature-tour.md"),
    Path("docs/reference/area-definition.md"),
    Path("docs/reference/artifacts.md"),
    Path("docs/troubleshooting.md"),
    Path("docs/images/README.md"),
)
REQUIRED_FILES = (
    *CANONICAL_DOCS,
    Path("scripts/acquire_banes_example.py"),
    Path("examples/new-area/area.yaml"),
    Path("docs/images/banes-strategic-network.png"),
    Path("docs/images/banes-assets-and-candidates.png"),
)
LINK_PATTERN = re.compile(r"(!?)\[([^\]]*)\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
FORBIDDEN_FLAGSHIP_NAMES = ("oxfordshire", "torbay", "teignmouth")


def _heading_slug(heading: str) -> str:
    heading = re.sub(r"<[^>]+>", "", heading).strip().lower()
    heading = re.sub(r"[^\w\- ]", "", heading)
    return re.sub(r"[\s-]+", "-", heading).strip("-")


def _target_parts(raw_target: str) -> tuple[str, str | None]:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0]
    path, separator, anchor = target.partition("#")
    return path, anchor if separator else None


def validate_documentation(root: Path = PROJECT_ROOT) -> list[str]:
    """Return human-readable contract failures; an empty list means success."""
    failures: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).exists():
            failures.append(f"missing required file: {relative}")

    for relative in CANONICAL_DOCS:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for name in FORBIDDEN_FLAGSHIP_NAMES:
            if name in lowered:
                failures.append(f"{relative}: non-flagship example named: {name}")
        for image_marker, alt_text, raw_target in LINK_PATTERN.findall(text):
            target_path, anchor = _target_parts(raw_target)
            if image_marker and not alt_text.strip():
                failures.append(f"{relative}: image has empty alt text: {raw_target}")
            if not target_path or re.match(r"^[a-z][a-z0-9+.-]*:", target_path):
                continue
            resolved = (path.parent / target_path).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{relative}: local link escapes repository: {raw_target}")
                continue
            if not resolved.exists():
                failures.append(f"{relative}: broken local link: {raw_target}")
                continue
            if anchor and resolved.is_file() and resolved.suffix.lower() == ".md":
                headings = HEADING_PATTERN.findall(resolved.read_text(encoding="utf-8"))
                anchors = {_heading_slug(heading) for heading in headings}
                if anchor not in anchors:
                    failures.append(f"{relative}: missing heading anchor: {raw_target}")

    readme = (root / "README.md").read_text(encoding="utf-8")
    if len(readme.splitlines()) > 180:
        failures.append("README.md: exceeds the concise landing-page limit of 180 lines")
    quickstart = (root / "docs/getting-started/agent-quickstart.md").read_text(
        encoding="utf-8"
    )
    for command in ("satn snapshot", "satn compile", "satn proving"):
        if command not in readme and command not in quickstart:
            failures.append(f"quickstart command is undocumented: {command}")

    image_inventory = (root / "docs/images/README.md").read_text(encoding="utf-8")
    for image in (root / "docs/images").glob("*.png"):
        if image.name not in image_inventory:
            failures.append(f"docs/images/README.md: image is not inventoried: {image.name}")

    try:
        AreaDefinition.from_yaml(root / "examples/new-area/area.yaml")
    except Exception as error:
        failures.append(f"examples/new-area/area.yaml: invalid Area Definition: {error}")
    return failures


def main() -> None:
    failures = validate_documentation()
    if failures:
        print("documentation validation failed:")
        for failure in failures:
            print(f"- {failure}")
        sys.exit(1)
    print(f"documentation validation passed: {len(CANONICAL_DOCS)} canonical pages")


if __name__ == "__main__":
    main()
