"""Create the ignored Pages release archive from generated Area Deployments."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from satn.pages_packaging import DEFAULT_MAXIMUM_BYTES, RELEASE_ARTIFACT_NAME, package_pages

PROJECT = Path(__file__).parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalogue", type=Path, default=PROJECT / "deployments" / "catalogue.yaml"
    )
    parser.add_argument("--deployments-root", type=Path, default=PROJECT / "build" / "deployments")
    parser.add_argument("--destination", type=Path, default=PROJECT / "build" / "pages")
    parser.add_argument(
        "--release-artifact",
        type=Path,
        default=PROJECT / "build" / RELEASE_ARTIFACT_NAME,
    )
    parser.add_argument(
        "--maximum-bytes",
        type=int,
        default=int(os.environ.get("SATN_PAGES_MAX_BYTES", DEFAULT_MAXIMUM_BYTES)),
    )
    args = parser.parse_args()
    result = package_pages(
        args.catalogue,
        args.deployments_root,
        args.destination,
        args.release_artifact,
        maximum_bytes=args.maximum_bytes,
    )
    print(
        f"{result.pages_directory} ({result.pages_size_bytes} bytes); "
        f"{result.release_artifact} ({result.release_size_bytes} bytes)"
    )


if __name__ == "__main__":
    main()
