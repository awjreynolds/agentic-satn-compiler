#!/usr/bin/env python3
"""Run a governed source-query replay benchmark fixture.

The factory target must be ``module:function``.  It receives no arguments and
returns one ``EvidenceReplayRequest``.  The replay module constructs the real
Local Evidence Store itself; a factory cannot supply both a fabricated oracle
and a matching backend.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable

from satn.evidence_replay import EvidenceReplayRequest, run_source_query_replay


def _factory(target: str) -> Callable[[], EvidenceReplayRequest]:
    module_name, separator, function_name = target.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError("factory target must be module:function")
    factory = getattr(importlib.import_module(module_name), function_name)
    if not callable(factory):
        raise ValueError("factory target is not callable")
    return factory


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the governed source-query replay gate."
    )
    parser.add_argument("factory", help="module:function returning EvidenceReplayRequest")
    arguments = parser.parse_args()
    request = _factory(arguments.factory)()
    if type(request) is not EvidenceReplayRequest:
        raise ValueError("factory must return exactly one EvidenceReplayRequest")
    result = run_source_query_replay(request)
    print(json.dumps(dict(result.manifest), sort_keys=True))
    return int(result.manifest["exit"]["code"])  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
