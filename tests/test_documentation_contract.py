from __future__ import annotations

import importlib.util
from pathlib import Path


def test_canonical_documentation_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "validate_docs.py"
    spec = importlib.util.spec_from_file_location("validate_docs", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.validate_documentation(root) == []
