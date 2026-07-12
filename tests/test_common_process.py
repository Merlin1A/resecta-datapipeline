"""Tests for :mod:`resecta_data.common.process`.

Two concerns:

1. ``request_parent_death_signal`` is Linux-only and best-effort. We assert
   it is callable, returns ``None`` on every platform, and (on Linux)
   actually invokes the libc ``prctl`` symbol.

2. ``effective_workers`` consolidates worker-sizing logic across three
   ProcessPoolExecutor sites. Tests cover env override, hard cap,
   default-only, and the ``max(1, ...)`` floor.
"""

from __future__ import annotations

import signal
import sys
from typing import Any

import pytest

from resecta_data.common.process import (
    effective_workers,
    request_parent_death_signal,
)

# ---- request_parent_death_signal ------------------------------------------


def test_request_parent_death_signal_callable() -> None:
    """The helper is callable with and without a signal argument."""
    request_parent_death_signal()
    request_parent_death_signal(signal.SIGTERM)


def test_request_parent_death_signal_noop_on_non_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS / Windows the helper must be a silent no-op.

    Patches ``sys.platform`` to ``darwin`` and asserts the function does
    not touch libc. If a future refactor removed the platform guard the
    spy would be invoked and the test would fail.
    """
    called: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        called.append((args, kwargs))
        raise AssertionError("ctypes.CDLL must not be called on non-linux")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("resecta_data.common.process.ctypes.CDLL", _spy)
    request_parent_death_signal()
    assert called == []


@pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux-only")
def test_request_parent_death_signal_invokes_prctl_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux the helper must call libc.prctl with PR_SET_PDEATHSIG=1."""
    captured: dict[str, Any] = {}

    class _FakeLibc:
        def prctl(self, op: int, sig: int, *rest: int) -> int:
            captured["op"] = op
            captured["sig"] = sig
            captured["rest"] = rest
            return 0

    def _fake_cdll(name: str, use_errno: bool = False) -> _FakeLibc:
        captured["lib"] = name
        return _FakeLibc()

    monkeypatch.setattr("resecta_data.common.process.ctypes.CDLL", _fake_cdll)
    request_parent_death_signal(signal.SIGTERM)
    assert captured["op"] == 1  # PR_SET_PDEATHSIG
    assert captured["sig"] == signal.SIGTERM
    assert captured["lib"] == "libc.so.6"


@pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux-only")
def test_request_parent_death_signal_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError from libc loading is swallowed (best-effort guard)."""

    def _boom(name: str, use_errno: bool = False) -> Any:
        raise OSError("simulated seccomp restriction")

    monkeypatch.setattr("resecta_data.common.process.ctypes.CDLL", _boom)
    # Must not raise — failure is best-effort.
    request_parent_death_signal()


# ---- effective_workers ----------------------------------------------------


def test_effective_workers_default_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESECTA_BUILD_WORKERS", raising=False)
    assert effective_workers(default=4, hard_cap=8) == 4


def test_effective_workers_hard_cap_wins_over_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESECTA_BUILD_WORKERS", raising=False)
    assert effective_workers(default=12, hard_cap=4) == 4


def test_effective_workers_env_caps_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "2")
    assert effective_workers(default=8, hard_cap=16) == 2


def test_effective_workers_env_does_not_raise_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var is a *cap*; a higher env value never raises a lower default."""
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "100")
    assert effective_workers(default=4, hard_cap=8) == 4


def test_effective_workers_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "not-a-number")
    assert effective_workers(default=4, hard_cap=8) == 4


def test_effective_workers_zero_env_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "0")
    assert effective_workers(default=4, hard_cap=8) == 4


def test_effective_workers_floor_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESECTA_BUILD_WORKERS", raising=False)
    assert effective_workers(default=0, hard_cap=8) == 1
    assert effective_workers(default=4, hard_cap=0) == 1


def test_effective_workers_env_takes_precedence_when_lower_than_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "3")
    assert effective_workers(default=8, hard_cap=16) == 3
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "20")
    assert effective_workers(default=8, hard_cap=16) == 8
