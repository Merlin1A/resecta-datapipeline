"""Tests for the nicknames sidecar builder.

Coverage:
- Schema validation of the built payload.
- Determinism: two builds from the same fixture are byte-identical.
- Inversion correctness: alias maps to all expected canonicals, sorted.
- Normalization: mixed-case and whitespace-surrounded input is normalized.
- Multi-canonical alias: "al" resolves to albert, alfred, allen.
- Missing source raises MissingSourceError.
- Multiple dated files raises PipelineError.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.nicknames import build as build_nicknames

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"
FIXTURE_CSV = Path(__file__).parent.parent / "fixtures" / "nicknames" / "nicknames_sample.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_from_fixture(tmp_source_dir: Path) -> dict:  # type: ignore[type-arg]
    """Copy the sample fixture into a temp sources dir and build."""
    tmp_source_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_source_dir / "nicknames_cc0_20260610.csv"
    shutil.copy(FIXTURE_CSV, dest)
    return build_nicknames(20260416, source_dir=tmp_source_dir)


# ---------------------------------------------------------------------------
# test_schema_valid
# ---------------------------------------------------------------------------


def test_schema_valid(tmp_path: Path, tmp_build_dir: Path) -> None:
    """build() output validates against schemas/nicknames.schema.json."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    dest = tmp_build_dir / "gazetteers" / "nicknames.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "nicknames")


# ---------------------------------------------------------------------------
# test_determinism
# ---------------------------------------------------------------------------


def test_determinism(tmp_path: Path, tmp_build_dir: Path) -> None:
    """Two builds from the same fixture produce byte-identical JSON."""
    source_dir = tmp_path / "sources"
    payload_a = _build_from_fixture(source_dir)
    payload_b = _build_from_fixture(source_dir)

    dest_a = tmp_build_dir / "gazetteers" / "nicknames_a.json"
    dest_b = tmp_build_dir / "gazetteers" / "nicknames_b.json"
    dump_canonical_json(payload_a, dest_a)
    dump_canonical_json(payload_b, dest_b)

    assert dest_a.read_bytes() == dest_b.read_bytes(), (
        "Two builds from the same source are not byte-identical — non-determinism in the builder."
    )


# ---------------------------------------------------------------------------
# Inversion correctness
# ---------------------------------------------------------------------------


def test_bill_resolves_to_william(tmp_path: Path) -> None:
    """'bill' alias resolves to ['william'] from the fixture."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    entries = payload["entries"]
    assert "bill" in entries, "'bill' should be a key in entries"
    assert "william" in entries["bill"], "'bill' must map to 'william'"


def test_bob_resolves_to_robert(tmp_path: Path) -> None:
    """'bob' alias resolves to ['robert'] from the fixture."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    entries = payload["entries"]
    assert "bob" in entries, "'bob' should be a key in entries"
    assert "robert" in entries["bob"], "'bob' must map to 'robert'"


def test_liz_resolves_to_elizabeth(tmp_path: Path) -> None:
    """'liz' alias resolves to ['elizabeth'] from the fixture."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    entries = payload["entries"]
    assert "liz" in entries, "'liz' should be a key in entries"
    assert "elizabeth" in entries["liz"], "'liz' must map to 'elizabeth'"


def test_multi_canonical_alias(tmp_path: Path) -> None:
    """'al' maps to albert, alfred, and allen — all three, sorted."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    entries = payload["entries"]
    assert "al" in entries, "'al' should be a key in entries"
    canonicals = entries["al"]
    for name in ("albert", "alfred", "allen"):
        assert name in canonicals, f"'al' must map to '{name}'"
    # Values must be sorted.
    assert canonicals == sorted(canonicals), "Canonicals must be in sorted order"


def test_canonicals_sorted(tmp_path: Path) -> None:
    """Every value list in entries is in sorted order."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    for alias, canonicals in payload["entries"].items():
        assert canonicals == sorted(canonicals), (
            f"Canonicals for alias '{alias}' are not sorted: {canonicals}"
        )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_mixed_case_normalized(tmp_path: Path) -> None:
    """Aliases from all-caps rows (MICHAEL → mike) are normalized to lowercase."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    entries = payload["entries"]
    # The fixture has a row "MICHAEL,MIKE,MICK,MICKY" — all caps.
    assert "mike" in entries, "'mike' should appear after normalizing the all-caps MICHAEL row"
    assert "michael" in entries["mike"], "'mike' must map to 'michael'"


def test_whitespace_normalized(tmp_path: Path) -> None:
    """Leading/trailing whitespace in tokens is stripped."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    entries = payload["entries"]
    # The fixture has "  katherine  ,  kate  ,  kathy  ,  kat  ,  kitty"
    assert "katherine" in entries, "'katherine' should appear after stripping whitespace"
    assert "kate" in entries, "'kate' should appear after stripping whitespace"
    assert "katherine" in entries["kate"], "'kate' must map to 'katherine'"


def test_no_empty_keys(tmp_path: Path) -> None:
    """Empty tokens after normalization are not inserted as keys or values."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    for key in payload["entries"]:
        assert key.strip(), f"Empty alias key found: {key!r}"
    for alias, canonicals in payload["entries"].items():
        for c in canonicals:
            assert c.strip(), f"Empty canonical under alias '{alias}': {c!r}"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_source_raises(tmp_path: Path) -> None:
    """build() raises MissingSourceError when no source CSV is present."""
    empty_dir = tmp_path / "empty_sources"
    empty_dir.mkdir()
    with pytest.raises(MissingSourceError):
        build_nicknames(20260416, source_dir=empty_dir)


def test_multiple_dated_files_raises(tmp_path: Path) -> None:
    """build() raises PipelineError when multiple dated CSVs are present."""
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    shutil.copy(FIXTURE_CSV, source_dir / "nicknames_cc0_20260610.csv")
    shutil.copy(FIXTURE_CSV, source_dir / "nicknames_cc0_20260611.csv")
    with pytest.raises(PipelineError):
        build_nicknames(20260416, source_dir=source_dir)


# ---------------------------------------------------------------------------
# Payload structure
# ---------------------------------------------------------------------------


def test_payload_metadata(tmp_path: Path) -> None:
    """Payload carries expected metadata fields."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    assert payload["version"] == 1
    assert payload["generated_by"] == "resecta-data/gazetteers/nicknames"
    assert payload["seed"] == 20260416
    assert len(payload["sources"]) == 1
    source_rec = payload["sources"][0]
    assert source_rec["id"] == "nicknames_cc0"
    assert source_rec["retrieval_date"] == "2026-06-10"
    # SHA-256 is a 64-char hex string.
    assert len(source_rec["sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in source_rec["sha256"])


def test_entries_nonempty(tmp_path: Path) -> None:
    """entries dict is non-empty (fixture has data)."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    assert len(payload["entries"]) > 0, "entries must not be empty"


def test_entries_canonical_json_roundtrip(tmp_path: Path, tmp_build_dir: Path) -> None:
    """Payload serializes and deserializes without data loss."""
    source_dir = tmp_path / "sources"
    payload = _build_from_fixture(source_dir)
    dest = tmp_build_dir / "gazetteers" / "nicknames_rt.json"
    dump_canonical_json(payload, dest)
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["entries"] == payload["entries"]
