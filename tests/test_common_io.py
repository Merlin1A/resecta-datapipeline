"""Tests for resecta_data.common.io.

Covers:
  - Canonical JSON round-trip stability
  - Atomic write behavior (no partial files on failure)
  - SHA-256 helpers against known vectors
  - Hash lockfile parse/write round-trip
  - iter_build_artifacts filtering and ordering
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import (
    atomic_write_bytes,
    dump_canonical_json,
    iter_build_artifacts,
    load_json,
    read_hash_lockfile,
    sha256_bytes,
    sha256_file,
    write_hash_lockfile,
)

# -----------------------------------------------------------------------------
# dump_canonical_json
# -----------------------------------------------------------------------------


def test_canonical_json_sorts_keys(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    # Insertion order deliberately non-alphabetical.
    dump_canonical_json({"z": 1, "a": 2, "m": 3}, out)
    text = out.read_text(encoding="utf-8")
    assert text.index('"a"') < text.index('"m"') < text.index('"z"')


def test_canonical_json_has_trailing_newline(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    dump_canonical_json({"k": "v"}, out)
    assert out.read_bytes().endswith(b"\n")


def test_canonical_json_uses_lf_line_endings(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    dump_canonical_json({"a": 1, "b": 2, "c": 3}, out)
    data = out.read_bytes()
    assert b"\r\n" not in data
    assert b"\r" not in data


def test_canonical_json_preserves_unicode(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    dump_canonical_json({"name": "José"}, out)
    text = out.read_text(encoding="utf-8")
    # ensure_ascii=False means the é is emitted literally, not as \u00e9.
    assert "José" in text
    assert "\\u" not in text


def test_canonical_json_is_byte_reproducible(tmp_path: Path) -> None:
    """Same payload → identical bytes across repeated dumps."""
    payload = {"nested": {"c": [3, 1, 2], "b": True, "a": None}, "top": 42}
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    dump_canonical_json(payload, first)
    dump_canonical_json(payload, second)
    assert first.read_bytes() == second.read_bytes()


def test_canonical_json_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "deeply" / "nested" / "out.json"
    dump_canonical_json({"ok": True}, out)
    assert out.is_file()


def test_canonical_json_raises_on_non_serializable(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    with pytest.raises(PipelineError, match="not JSON-serializable"):
        dump_canonical_json({"bad": {1, 2, 3}}, out)  # set is not JSON


def test_canonical_json_indent_is_two_spaces(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    dump_canonical_json({"outer": {"inner": 1}}, out)
    text = out.read_text(encoding="utf-8")
    # The inner key should be indented with exactly four spaces (2 x nesting).
    assert '\n    "inner"' in text


# -----------------------------------------------------------------------------
# load_json
# -----------------------------------------------------------------------------


def test_load_json_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    payload = {"a": [1, 2, 3], "b": {"c": None}}
    dump_canonical_json(payload, out)
    assert load_json(out) == payload


def test_load_json_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="not found"):
        load_json(tmp_path / "does_not_exist.json")


def test_load_json_raises_on_invalid(tmp_path: Path) -> None:
    out = tmp_path / "bad.json"
    out.write_text("this is not json", encoding="utf-8")
    with pytest.raises(PipelineError, match="Invalid JSON"):
        load_json(out)


# -----------------------------------------------------------------------------
# atomic_write_bytes
# -----------------------------------------------------------------------------


def test_atomic_write_basic(tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    atomic_write_bytes(out, b"hello")
    assert out.read_bytes() == b"hello"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    out.write_bytes(b"old content")
    atomic_write_bytes(out, b"new content")
    assert out.read_bytes() == b"new content"


def test_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    atomic_write_bytes(out, b"data")
    # No leftover .tmp files in the directory.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "new" / "dir" / "out.bin"
    atomic_write_bytes(out, b"data")
    assert out.is_file()


def test_atomic_write_handles_binary_data(tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    payload = bytes(range(256))
    atomic_write_bytes(out, payload)
    assert out.read_bytes() == payload


# -----------------------------------------------------------------------------
# SHA-256 helpers
# -----------------------------------------------------------------------------


def test_sha256_bytes_empty() -> None:
    # Known vector: SHA-256 of empty input.
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_bytes_known_vector() -> None:
    # Known vector: SHA-256 of "abc".
    assert (
        sha256_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_file_matches_bytes(tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    payload = b"The quick brown fox jumps over the lazy dog"
    out.write_bytes(payload)
    assert sha256_file(out) == sha256_bytes(payload)


def test_sha256_file_large_chunks(tmp_path: Path) -> None:
    """File larger than the 1 MiB read chunk hashes correctly."""
    out = tmp_path / "large.bin"
    payload = b"x" * (3 * (1 << 20) + 17)  # 3 MiB + change
    out.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert sha256_file(out) == expected


def test_sha256_file_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="Cannot hash non-file"):
        sha256_file(tmp_path / "missing")


def test_sha256_file_raises_on_directory(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="Cannot hash non-file"):
        sha256_file(tmp_path)


# -----------------------------------------------------------------------------
# Hash lockfile
# -----------------------------------------------------------------------------


def test_lockfile_round_trip(tmp_path: Path) -> None:
    lockfile = tmp_path / "asset_hashes.lock"
    entries = {
        "Gazetteers/names.bloom": "a" * 64,
        "Classifier/doctype_keywords.json": "b" * 64,
        "Classifier/preset_thresholds.json": "c" * 64,
    }
    write_hash_lockfile(lockfile, entries)
    assert read_hash_lockfile(lockfile) == entries


def test_lockfile_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_hash_lockfile(tmp_path / "nonexistent.lock") == {}


def test_lockfile_ignores_comments_and_blanks(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    lockfile.write_text(
        "# Header comment\n"
        "\n"
        "Gazetteers/names.bloom  " + ("a" * 64) + "\n"
        "\n"
        "# Another comment\n"
        "Classifier/doctype_keywords.json  " + ("b" * 64) + "\n",
        encoding="utf-8",
    )
    entries = read_hash_lockfile(lockfile)
    assert entries == {
        "Gazetteers/names.bloom": "a" * 64,
        "Classifier/doctype_keywords.json": "b" * 64,
    }


def test_lockfile_rejects_malformed_line(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    lockfile.write_text("not a valid entry\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="expected '<path>  <sha256>'"):
        read_hash_lockfile(lockfile)


def test_lockfile_rejects_short_hash(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    lockfile.write_text("some/path  abc123\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="invalid sha256"):
        read_hash_lockfile(lockfile)


def test_lockfile_rejects_uppercase_hash(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    lockfile.write_text("some/path  " + ("A" * 64) + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="invalid sha256"):
        read_hash_lockfile(lockfile)


def test_lockfile_rejects_non_hex_hash(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    lockfile.write_text("some/path  " + ("z" * 64) + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="invalid sha256"):
        read_hash_lockfile(lockfile)


def test_lockfile_written_with_sorted_paths(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    entries = {"zebra.bin": "f" * 64, "alpha.bin": "0" * 64, "mike.bin": "7" * 64}
    write_hash_lockfile(lockfile, entries)
    text = lockfile.read_text(encoding="utf-8")
    # Each path should appear in sorted order in the file.
    assert text.index("alpha.bin") < text.index("mike.bin") < text.index("zebra.bin")


def test_lockfile_is_byte_reproducible(tmp_path: Path) -> None:
    """Same entries → identical file bytes."""
    entries = {"b.bin": "1" * 64, "a.bin": "2" * 64}
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    write_hash_lockfile(first, entries)
    write_hash_lockfile(second, entries)
    assert first.read_bytes() == second.read_bytes()


def test_lockfile_has_trailing_newline(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    write_hash_lockfile(lockfile, {"a.bin": "0" * 64})
    assert lockfile.read_bytes().endswith(b"\n")


def test_lockfile_empty_is_valid(tmp_path: Path) -> None:
    lockfile = tmp_path / "lock"
    write_hash_lockfile(lockfile, {})
    # Empty lockfile is the Phase 0 baseline.
    assert read_hash_lockfile(lockfile) == {}


# -----------------------------------------------------------------------------
# iter_build_artifacts
# -----------------------------------------------------------------------------


def test_iter_artifacts_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    assert iter_build_artifacts(tmp_path / "build") == []


def test_iter_artifacts_missing_dir(tmp_path: Path) -> None:
    assert iter_build_artifacts(tmp_path / "nonexistent") == []


def test_iter_artifacts_excludes_stamps_and_gitkeeps(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / ".stamp").touch()
    (build / ".gitkeep").touch()
    (build / "real.json").write_text("{}", encoding="utf-8")
    found = iter_build_artifacts(build)
    assert len(found) == 1
    assert found[0].name == "real.json"


def test_iter_artifacts_recurses(tmp_path: Path) -> None:
    build = tmp_path / "build"
    (build / "Gazetteers").mkdir(parents=True)
    (build / "Classifier").mkdir()
    (build / "Gazetteers" / "names.bloom").write_bytes(b"\x00")
    (build / "Classifier" / "doctype.json").write_text("{}", encoding="utf-8")
    found = iter_build_artifacts(build)
    assert len(found) == 2


def test_iter_artifacts_returns_sorted(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    # Create in non-sorted order.
    for name in ("zebra.bin", "alpha.bin", "mike.bin"):
        (build / name).write_bytes(b"x")
    names = [p.name for p in iter_build_artifacts(build)]
    assert names == sorted(names)


def test_iter_artifacts_skips_directories(tmp_path: Path) -> None:
    build = tmp_path / "build"
    (build / "subdir").mkdir(parents=True)
    (build / "file.bin").write_bytes(b"x")
    found = iter_build_artifacts(build)
    assert len(found) == 1
    assert found[0].name == "file.bin"


def test_iter_artifacts_excludes_dotstamps_dir(tmp_path: Path) -> None:
    """Files under ``.stamps/`` are make sentinel files (Patch B sentinel-file
    builders), not shipping artifacts. Hash-check, schema-check, determinism-
    check, and install-assets must all skip them.
    """
    build = tmp_path / "build"
    build.mkdir()
    stamps = build / ".stamps"
    stamps.mkdir()
    (stamps / "vectors").touch()
    (stamps / "fuzz").touch()
    (stamps / "zip-scf").touch()
    (build / "real.json").write_text("{}", encoding="utf-8")
    found = iter_build_artifacts(build)
    assert len(found) == 1
    assert found[0].name == "real.json"


def test_iter_artifacts_excludes_diagnostics_dir(tmp_path: Path) -> None:
    """Files under ``diagnostics/`` are one-off analytical artifacts, not
    produced by ``make build``, and therefore not reproducible by the
    determinism rebuild-and-diff sweep.
    """
    build = tmp_path / "build"
    build.mkdir()
    diagnostics = build / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "s5_argmax_accuracy.json").write_text("{}", encoding="utf-8")
    (diagnostics / "s5_nll_vs_T.json").write_text("{}", encoding="utf-8")
    (build / "real.json").write_text("{}", encoding="utf-8")
    found = iter_build_artifacts(build)
    assert len(found) == 1
    assert found[0].name == "real.json"


def test_iter_artifacts_excludes_ingest_cache_dir(tmp_path: Path) -> None:
    """Files under ``_ingest_cache/`` are bloom-ingest parse caches (~428 MB
    each in a hydrated tree), internal memoization rather than shipping
    artifacts. Hash-check, schema-check, the determinism snapshot, and
    install-assets must all skip them. Parse-level determinism stays
    verified because the verify-determinism rebuild re-parses from an empty
    cache in its own build dir (test_determinism_cache_isolation.py).
    """
    build = tmp_path / "build"
    cache = build / "gazetteers" / "_ingest_cache"
    cache.mkdir(parents=True)
    (cache / "surnames.json").write_text('{"fingerprint": "abc"}', encoding="utf-8")
    (cache / "given-names.json").write_text('{"fingerprint": "def"}', encoding="utf-8")
    (build / "gazetteers" / "surnames.bloom").write_bytes(b"\x00bloom")
    found = iter_build_artifacts(build)
    assert [p.name for p in found] == ["surnames.bloom"]


def test_iter_artifacts_excludes_timings_jsonl(tmp_path: Path) -> None:
    """``_timings.jsonl`` is a profiling sidecar from ``make time-build``;
    its wall-time floats change every invocation, so it must not enter the
    determinism comparison.
    """
    build = tmp_path / "build"
    build.mkdir()
    (build / "_timings.jsonl").write_text(
        '{"label":"vectors","wall_seconds":1.234}\n', encoding="utf-8"
    )
    (build / "real.json").write_text("{}", encoding="utf-8")
    found = iter_build_artifacts(build)
    assert len(found) == 1
    assert found[0].name == "real.json"


# -----------------------------------------------------------------------------
# Cross-module integration: canonical JSON matches stdlib expectations
# -----------------------------------------------------------------------------


def test_canonical_json_matches_manual_format(tmp_path: Path) -> None:
    """The canonical form must equal what stdlib json produces with the same kwargs.

    This guards against accidental drift if we ever tweak dump_canonical_json.
    """
    out = tmp_path / "out.json"
    payload = {"b": 2, "a": 1, "c": [3, 1, 2]}
    dump_canonical_json(payload, out)
    expected = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
            ensure_ascii=False,
        )
        + "\n"
    )
    assert out.read_text(encoding="utf-8") == expected
