"""Unit tests for the content-keyed stamp mechanism (speed plan #4).

The keyer is gate machinery: a wrong "unchanged" answer would skip a builder
(and transitively the determinism rebuild) on real changes. These tests pin
the two soundness directions:

1. **Content sensitivity** — any byte change in any tracked file, and any
   change to the tracked file *list*, must flip the manifest.
2. **Failure direction** — every malfunction (absent stamp, legacy empty
   stamp, garbage content, missing input) must report "changed" (rebuild),
   never "unchanged" (skip).

The make-level wiring (recipes pass ``$@ $^``, prereq closures cover the
right input classes) is pinned by ``test_stamp_key_makefile.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.stamp_key import check, compute_manifest, main, write


@pytest.fixture
def tracked(tmp_path: Path) -> list[str]:
    """Two tracked input files, paths relative to cwd-independent tmp_path."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\n", encoding="utf-8")
    b.write_text("beta\n", encoding="utf-8")
    return [str(a), str(b)]


def test_write_then_check_matches(tmp_path: Path, tracked: list[str]) -> None:
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    assert check(stamp, tracked)


def test_mtime_churn_does_not_invalidate(tmp_path: Path, tracked: list[str]) -> None:
    """The whole point of #4: mtime-only churn must keep the key valid."""
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    for p in tracked:
        os.utime(p, (4102444800, 4102444800))  # far-future mtime, bytes untouched
    assert check(stamp, tracked)


def test_content_change_invalidates(tmp_path: Path, tracked: list[str]) -> None:
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    Path(tracked[0]).write_text("alpha CHANGED\n", encoding="utf-8")
    assert not check(stamp, tracked)


def test_same_size_content_change_invalidates(tmp_path: Path, tracked: list[str]) -> None:
    """The F6 collision class: same-length edit must still flip the key."""
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    original = Path(tracked[0]).read_bytes()
    edited = b"X" + original[1:]
    assert len(edited) == len(original)
    Path(tracked[0]).write_bytes(edited)
    assert not check(stamp, tracked)


def test_file_list_is_part_of_the_key(tmp_path: Path, tracked: list[str]) -> None:
    """Adding or removing a tracked file changes the key even if shared
    files are byte-identical — a Makefile prereq-list edit re-keys."""
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    assert not check(stamp, tracked[:1])
    extra = tmp_path / "c.txt"
    extra.write_text("gamma\n", encoding="utf-8")
    assert not check(stamp, [*tracked, str(extra)])


def test_manifest_is_order_independent_and_deduplicated(tmp_path: Path, tracked: list[str]) -> None:
    """$^ order is make's business; the manifest must not depend on it."""
    assert compute_manifest(tracked) == compute_manifest(list(reversed(tracked)))
    assert compute_manifest(tracked) == compute_manifest(tracked + tracked[:1])


def test_absent_stamp_reports_changed(tmp_path: Path, tracked: list[str]) -> None:
    assert not check(tmp_path / "never-written", tracked)


def test_legacy_empty_stamp_reports_changed(tmp_path: Path, tracked: list[str]) -> None:
    """Pre-#4 stamps are empty `touch` artifacts — they must read as a miss
    so the first keyed run rebuilds and records a real manifest."""
    stamp = tmp_path / "stamp"
    stamp.touch()
    assert not check(stamp, tracked)


def test_garbage_stamp_reports_changed(tmp_path: Path, tracked: list[str]) -> None:
    stamp = tmp_path / "stamp"
    stamp.write_text("not a manifest\n", encoding="utf-8")
    assert not check(stamp, tracked)


def test_missing_input_reports_changed_for_check(tmp_path: Path, tracked: list[str]) -> None:
    """check() on a vanished input degrades to rebuild — the builder (or
    make itself) then surfaces the real error loudly."""
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    Path(tracked[0]).unlink()
    assert not check(stamp, tracked)


def test_missing_input_raises_for_write(tmp_path: Path, tracked: list[str]) -> None:
    """write() runs right after a successful build, where make guarantees
    prerequisites exist — a missing file there is a defect, not a miss."""
    Path(tracked[0]).unlink()
    with pytest.raises(PipelineError):
        write(tmp_path / "stamp", tracked)


def test_write_is_atomic_no_tmp_residue(tmp_path: Path, tracked: list[str]) -> None:
    stamp = tmp_path / "stamp"
    write(stamp, tracked)
    write(stamp, tracked)
    residue = [p for p in tmp_path.iterdir() if p.name.startswith("stamp.")]
    assert residue == []


def test_manifest_format_is_versioned_and_sorted(tmp_path: Path, tracked: list[str]) -> None:
    manifest = compute_manifest(tracked)
    lines = manifest.splitlines()
    assert lines[0] == "# resecta-stamp-key v1"
    paths = [line.split("  ", 1)[1] for line in lines[1:]]
    assert paths == sorted(paths)
    assert manifest.endswith("\n")


def test_cli_exit_codes(tmp_path: Path, tracked: list[str]) -> None:
    stamp = tmp_path / "stamp"
    assert main(["check", str(stamp), *tracked]) == 1  # absent → changed
    assert main(["write", str(stamp), *tracked]) == 0
    assert main(["check", str(stamp), *tracked]) == 0
    Path(tracked[1]).write_text("beta CHANGED\n", encoding="utf-8")
    assert main(["check", str(stamp), *tracked]) == 1
    assert main(["bogus-mode"]) == 2
    assert main([]) == 2
