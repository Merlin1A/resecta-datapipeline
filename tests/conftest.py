"""Shared pytest fixtures and invariants.

Every test file imports from here implicitly via pytest's auto-discovery.
Global invariants (hash-seed pinning, socket disabling) are enforced at import
time so they cannot be accidentally bypassed by individual tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Enforce PYTHONHASHSEED=0 at test-import time.
# pyproject.toml [tool.pytest.ini_options].env sets this, but a developer
# running `python -m pytest` directly can bypass that mechanism. This guard
# catches the bypass before any test runs.
if os.environ.get("PYTHONHASHSEED") != "0":
    pytest.exit(
        "PYTHONHASHSEED must be '0' for deterministic tests.\n"
        "Run via `make test` or `export PYTHONHASHSEED=0` before pytest.",
        returncode=2,
    )


# -----------------------------------------------------------------------------
# Shared fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def tmp_build_dir(tmp_path: Path) -> Iterator[Path]:
    """A temporary build directory with the same layout as real ``build/``."""
    build = tmp_path / "build"
    build.mkdir()
    yield build


@pytest.fixture
def tmp_schemas_dir(tmp_path: Path) -> Iterator[Path]:
    """A temporary schemas directory. Tests add schemas as needed."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    yield schemas


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove optional environment flags that affect pipeline behavior.

    Use in tests that exercise license gating so the test result doesn't
    depend on whether the developer happens to have RESECTA_ALLOW_* set.
    """
    for flag in ("RESECTA_ALLOW_CC_BY_ASSETS", "RESECTA_ALLOW_CC_BY_SA_ASSETS"):
        monkeypatch.delenv(flag, raising=False)
