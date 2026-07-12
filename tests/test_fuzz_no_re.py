"""The fuzz module must never import Python's ``re``.

Payloads are strings, not regex objects. Compiling or executing them from
Python would be both pointless (the Swift detector is the target) and a
footgun (accidental catastrophic backtracking during artifact generation).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FUZZ_DIR = Path(__file__).parent.parent / "src" / "resecta_data" / "fuzz"


@pytest.mark.parametrize("py_file", sorted(FUZZ_DIR.glob("*.py")))
def test_fuzz_module_does_not_import_re(py_file: Path) -> None:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re" or alias.name.startswith("re."):
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module == "re":
            offenders.append(f"from re import {[a.name for a in node.names]}")
    assert not offenders, f"{py_file}: forbidden imports {offenders}"
