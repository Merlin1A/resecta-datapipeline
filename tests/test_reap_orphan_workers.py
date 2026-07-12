"""Tests for ``scripts/reap_orphan_workers.py``.

Pure unit tests around the heuristic — no real process kills. The
script's reaping path is exercised end-to-end indirectly via
``tests/test_worker_pdeathsig.py``; here we focus on the detection rules
(parent must be a session reaper, age must exceed the threshold,
target comm/cmdline filters), the formatting helpers, and the CLI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_reap_module() -> object:
    """Load ``scripts/reap_orphan_workers.py`` as an importable module.

    Registers the module in ``sys.modules`` before ``exec_module`` so the
    ``@dataclass`` decorator can resolve its host module via
    ``cls.__module__`` lookup.
    """
    if "reap_orphan_workers" in sys.modules:
        return sys.modules["reap_orphan_workers"]
    repo_root = Path(__file__).parent.parent
    script = repo_root / "scripts" / "reap_orphan_workers.py"
    spec = importlib.util.spec_from_file_location("reap_orphan_workers", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["reap_orphan_workers"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reap_mod() -> object:
    return _load_reap_module()


def test_format_age_seconds(reap_mod: object) -> None:
    assert reap_mod._format_age(0) == "0s"  # type: ignore[attr-defined]
    assert reap_mod._format_age(45) == "45s"  # type: ignore[attr-defined]
    assert reap_mod._format_age(120) == "2m"  # type: ignore[attr-defined]
    assert reap_mod._format_age(3600) == "1.0h"  # type: ignore[attr-defined]
    assert reap_mod._format_age(7200) == "2.0h"  # type: ignore[attr-defined]


def test_orphan_row_dataclass(reap_mod: object) -> None:
    row = reap_mod._OrphanRow(  # type: ignore[attr-defined]
        pid=12345,
        ppid=2303,
        rss_kb=4_500_000,
        age_seconds=1800.0,
        comm="resecta-data",
        cmdline="resecta-data build bloom",
    )
    assert row.pid == 12345
    assert row.rss_kb == 4_500_000


def test_print_orphans_empty_message(reap_mod: object, capsys: pytest.CaptureFixture[str]) -> None:
    reap_mod.print_orphans([])  # type: ignore[attr-defined]
    captured = capsys.readouterr()
    assert "0 stale workers" in captured.out


def test_print_orphans_nonempty_format(
    reap_mod: object, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        reap_mod._OrphanRow(  # type: ignore[attr-defined]
            pid=12345,
            ppid=2303,
            rss_kb=4_500_000,
            age_seconds=1800.0,
            comm="resecta-data",
            cmdline="resecta-data build bloom --build-dir build",
        ),
    ]
    reap_mod.print_orphans(rows)  # type: ignore[attr-defined]
    captured = capsys.readouterr()
    assert "1 stale workers detected" in captured.out
    assert "12345" in captured.out
    assert "2303" in captured.out
    assert "make reap-orphans" in captured.out


def test_main_default_mode_returns_zero(reap_mod: object) -> None:
    """No --reap means no kills; exit code is always 0."""
    rc = reap_mod.main([])  # type: ignore[attr-defined]
    assert rc == 0


def test_main_with_reap_no_orphans_returns_zero(reap_mod: object) -> None:
    """--reap on an empty detection list still exits 0."""
    rc = reap_mod.main(["--reap"])  # type: ignore[attr-defined]
    assert rc == 0


@pytest.mark.skipif(sys.platform != "linux", reason="/proc-based heuristic is Linux-only")
def test_find_orphans_finds_no_unrelated_processes(reap_mod: object) -> None:
    """Smoke: the heuristic must not pick up unrelated user processes.

    We do not assert that the returned list is empty (a developer could
    have a real orphan on the host while running the test suite), but we
    do assert that every returned row matches the documented heuristic:
    its ``comm`` is in the target set or contains 'resecta-data', and
    its ``ppid`` is one of the session-reaper PIDs the helper found.
    """
    rows = reap_mod.find_orphans()  # type: ignore[attr-defined]
    reapers = reap_mod._find_session_reaper_pids()  # type: ignore[attr-defined]
    target_comms = reap_mod._TARGET_COMMS  # type: ignore[attr-defined]
    for row in rows:
        assert row.ppid in reapers, f"row {row.pid} ppid {row.ppid} not a reaper"
        assert row.comm in target_comms or "resecta-data" in row.comm, (
            f"row {row.pid} comm {row.comm!r} did not match target set"
        )
        assert row.age_seconds >= reap_mod._ORPHAN_MIN_AGE_SECONDS, (  # type: ignore[attr-defined]
            f"row {row.pid} age {row.age_seconds} below threshold"
        )
