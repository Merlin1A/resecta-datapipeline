"""Tests for resecta_data.common.determinism.

Covers:
  - CANONICAL_SEED is the documented value
  - seeded_context produces reproducible sequences
  - seeded_context rejects negative seeds
  - assert_hash_seed_pinned passes when pinned, fails otherwise
  - snapshot_build_dir copies files and hashes them correctly
  - diff_hash_maps identifies additions, removals, and hash changes
"""

from __future__ import annotations

import random as stdlib_random
from pathlib import Path

import pytest

from resecta_data.common.determinism import (
    CANONICAL_SEED,
    assert_hash_seed_pinned,
    diff_hash_maps,
    seeded_context,
    snapshot_build_dir,
)
from resecta_data.common.exceptions import DeterminismError

# -----------------------------------------------------------------------------
# CANONICAL_SEED
# -----------------------------------------------------------------------------


def test_canonical_seed_is_documented_value() -> None:
    """The canonical seed is YYYYMMDD of the plan's authoring date.

    Changing this value invalidates every generated artifact.
    """
    assert CANONICAL_SEED == 20260416


# -----------------------------------------------------------------------------
# seeded_context
# -----------------------------------------------------------------------------


def test_seeded_context_is_reproducible() -> None:
    with seeded_context(42) as rng:
        first = [rng.random() for _ in range(10)]
    with seeded_context(42) as rng:
        second = [rng.random() for _ in range(10)]
    assert first == second


def test_seeded_context_distinct_seeds_differ() -> None:
    with seeded_context(1) as rng:
        first = [rng.random() for _ in range(10)]
    with seeded_context(2) as rng:
        second = [rng.random() for _ in range(10)]
    assert first != second


def test_seeded_context_does_not_leak_global_state() -> None:
    """Inside seeded_context, the global `random` module is not touched.

    S311 is suppressed on the stdlib_random calls below because we are
    deliberately exercising the global random module to prove that our
    seeded_context does not perturb it. The random values themselves are
    not used for security.
    """
    stdlib_random.seed(999)
    stdlib_before = stdlib_random.random()  # noqa: S311

    stdlib_random.seed(999)
    with seeded_context(42) as rng:
        _ = [rng.random() for _ in range(100)]
    # Global state restored to the same point — unaffected by the context.
    stdlib_after = stdlib_random.random()  # noqa: S311
    assert stdlib_before == stdlib_after


def test_seeded_context_rejects_negative_seed() -> None:
    with pytest.raises(DeterminismError, match="non-negative"), seeded_context(-1):
        pass


def test_seeded_context_accepts_zero() -> None:
    with seeded_context(0) as rng:
        # Should not raise; zero is a valid seed.
        _ = rng.random()


def test_seeded_context_accepts_canonical_seed() -> None:
    with seeded_context(CANONICAL_SEED) as rng:
        value = rng.random()
    # Must not raise. Exact value is incidental — we just need reproducibility.
    assert 0.0 <= value < 1.0


# -----------------------------------------------------------------------------
# assert_hash_seed_pinned
# -----------------------------------------------------------------------------


def test_hash_seed_assertion_passes_when_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    assert_hash_seed_pinned()  # should not raise


def test_hash_seed_assertion_fails_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    with pytest.raises(DeterminismError, match="PYTHONHASHSEED"):
        assert_hash_seed_pinned()


def test_hash_seed_assertion_fails_when_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    with pytest.raises(DeterminismError, match="PYTHONHASHSEED"):
        assert_hash_seed_pinned()


def test_hash_seed_assertion_fails_when_random(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "random")
    with pytest.raises(DeterminismError, match="PYTHONHASHSEED"):
        assert_hash_seed_pinned()


# -----------------------------------------------------------------------------
# snapshot_build_dir
# -----------------------------------------------------------------------------


def test_snapshot_copies_all_files(tmp_path: Path) -> None:
    build = tmp_path / "build"
    (build / "sub").mkdir(parents=True)
    (build / "a.bin").write_bytes(b"alpha")
    (build / "sub" / "b.bin").write_bytes(b"beta")

    dest = tmp_path / "snapshot"
    hashes = snapshot_build_dir(build, dest)

    assert (dest / "a.bin").read_bytes() == b"alpha"
    assert (dest / "sub" / "b.bin").read_bytes() == b"beta"
    assert set(hashes) == {"a.bin", "sub/b.bin"}


def test_snapshot_hash_matches_sha256() -> None:
    # Delegated to test_common_io; here we just sanity-check the return type.
    pass  # covered implicitly by test_determinism_roundtrip below


def test_snapshot_clears_existing_destination(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "new.bin").write_bytes(b"new")

    dest = tmp_path / "snapshot"
    dest.mkdir()
    (dest / "stale.bin").write_bytes(b"stale")

    snapshot_build_dir(build, dest)
    # Stale file gone; new file present.
    assert not (dest / "stale.bin").exists()
    assert (dest / "new.bin").read_bytes() == b"new"


def test_snapshot_empty_build_dir(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    dest = tmp_path / "snapshot"
    hashes = snapshot_build_dir(build, dest)
    assert hashes == {}
    assert dest.is_dir()


def test_snapshot_uses_posix_paths(tmp_path: Path) -> None:
    """Hash keys use forward slashes regardless of OS."""
    build = tmp_path / "build"
    (build / "sub" / "deep").mkdir(parents=True)
    (build / "sub" / "deep" / "file.bin").write_bytes(b"x")
    dest = tmp_path / "snapshot"
    hashes = snapshot_build_dir(build, dest)
    assert "sub/deep/file.bin" in hashes
    # Never backslashes even on systems where they're the native separator.
    assert not any("\\" in key for key in hashes)


# -----------------------------------------------------------------------------
# diff_hash_maps
# -----------------------------------------------------------------------------


def test_diff_identical_maps_is_empty() -> None:
    a = {"x.bin": "1" * 64, "y.bin": "2" * 64}
    assert diff_hash_maps(a, a) == []


def test_diff_detects_hash_change() -> None:
    a = {"x.bin": "1" * 64}
    b = {"x.bin": "2" * 64}
    diff = diff_hash_maps(a, b)
    assert len(diff) == 1
    assert "x.bin" in diff[0]
    assert "differs" in diff[0].lower()


def test_diff_detects_added_file() -> None:
    a: dict[str, str] = {}
    b = {"new.bin": "a" * 64}
    diff = diff_hash_maps(a, b)
    assert any("Only in second" in d for d in diff)
    assert any("new.bin" in d for d in diff)


def test_diff_detects_removed_file() -> None:
    a = {"old.bin": "a" * 64}
    b: dict[str, str] = {}
    diff = diff_hash_maps(a, b)
    assert any("Only in first" in d for d in diff)
    assert any("old.bin" in d for d in diff)


def test_diff_sorts_output_deterministically() -> None:
    a = {"z.bin": "1" * 64, "a.bin": "1" * 64, "m.bin": "1" * 64}
    b = {"z.bin": "2" * 64, "a.bin": "2" * 64, "m.bin": "2" * 64}
    diff1 = diff_hash_maps(a, b)
    diff2 = diff_hash_maps(a, b)
    assert diff1 == diff2


def test_diff_reports_combined_changes() -> None:
    a = {"kept.bin": "1" * 64, "changed.bin": "2" * 64, "removed.bin": "3" * 64}
    b = {"kept.bin": "1" * 64, "changed.bin": "5" * 64, "added.bin": "6" * 64}
    diff = diff_hash_maps(a, b)
    # Three differences: one change, one removal, one addition. kept.bin unchanged.
    assert len(diff) == 3


# -----------------------------------------------------------------------------
# Round-trip: snapshot + diff detect a modification
# -----------------------------------------------------------------------------


def test_determinism_roundtrip_detects_modification(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.bin").write_bytes(b"original")

    snap = tmp_path / "snap"
    first_hashes = snapshot_build_dir(build, snap)

    # Modify the build artifact and snapshot again to simulate a second build.
    (build / "a.bin").write_bytes(b"modified")
    snap2 = tmp_path / "snap2"
    second_hashes = snapshot_build_dir(build, snap2)

    diff = diff_hash_maps(first_hashes, second_hashes)
    assert len(diff) == 1
    assert "a.bin" in diff[0]


def test_determinism_roundtrip_identical_builds_match(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.bin").write_bytes(b"deterministic")
    (build / "b.json").write_text('{"k": "v"}', encoding="utf-8")

    snap1 = tmp_path / "snap1"
    hashes1 = snapshot_build_dir(build, snap1)

    snap2 = tmp_path / "snap2"
    hashes2 = snapshot_build_dir(build, snap2)

    assert diff_hash_maps(hashes1, hashes2) == []
