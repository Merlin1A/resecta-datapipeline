"""Every Phase 2 schema description and public docstring must pass the
mechanism-language scanner. Builder modules inherit the same
rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.common.mechanism_language import scan_text

_PIPELINE_ROOT = Path(__file__).parent.parent
_SCHEMA_NAMES = (
    "gazetteer_manifest",
    "negative_context",
    "demographic_coverage",
)
_MODULE_DIRS = (
    _PIPELINE_ROOT / "src" / "resecta_data" / "bloom",
    _PIPELINE_ROOT / "src" / "resecta_data" / "gazetteers" / "negative_context",
    _PIPELINE_ROOT / "src" / "resecta_data" / "demographics",
)


@pytest.mark.parametrize("name", _SCHEMA_NAMES)
def test_schema_is_mechanism_safe(name: str) -> None:
    path = _PIPELINE_ROOT / "schemas" / f"{name}.schema.json"
    hits = scan_text(path.read_text(encoding="utf-8"))
    assert hits == [], f"{path}: banned phrases {hits!r}"


def test_phase2_modules_are_mechanism_safe() -> None:
    for module_dir in _MODULE_DIRS:
        for py in module_dir.rglob("*.py"):
            hits = scan_text(py.read_text(encoding="utf-8"))
            assert hits == [], f"{py}: banned phrases {hits!r}"
