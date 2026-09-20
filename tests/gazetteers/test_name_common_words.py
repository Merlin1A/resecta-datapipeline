"""Tests for the common-word curation list builder.

Coverage:
- Schema validation of the built payload.
- Determinism: two builds are byte-identical.
- Shape: unique, sorted, NFKC-lowercased entries; the pinned counts.
- The G8 corpus name pools are disjoint from the list (the engine pins the
  same fact against the shipped fixture) -- the pair's TP-held prediction
  rests on it.
- Duplicates in the authored list collapse; a non-word entry fails loud.
- Missing or malformed source raises.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.corpus._names import _GIVEN_NAMES, _SURNAMES_BY_BUCKET
from resecta_data.gazetteers.name_common_words import build as build_name_common_words
from resecta_data.gazetteers.name_common_words.build import (
    EXPECTED_AUTHORED,
    EXPECTED_UNIQUE,
    normalize_entry,
)

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"
SOURCE_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "name_common_words"
    / "sources"
    / "name_common_words_v1.json"
)
_SEED = 20260416


def _write_source(path: Path, entries: list[str]) -> Path:
    payload = {"provenance": {"id": "test-source"}, "entries": entries}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_schema_valid(tmp_build_dir: Path) -> None:
    payload = build_name_common_words(_SEED)
    out = tmp_build_dir / "gazetteers" / "name_common_words.json"
    dump_canonical_json(payload, out)
    validate_file(out, SCHEMAS_DIR, "name_common_words")


def test_deterministic(tmp_build_dir: Path) -> None:
    a = tmp_build_dir / "a.json"
    b = tmp_build_dir / "b.json"
    dump_canonical_json(build_name_common_words(_SEED), a)
    dump_canonical_json(build_name_common_words(_SEED), b)
    assert a.read_bytes() == b.read_bytes()


def test_shape_and_pins() -> None:
    payload = build_name_common_words(_SEED)
    entries = payload["entries"]
    assert len(entries) == EXPECTED_UNIQUE == 546
    assert payload["source"]["authored_count"] == EXPECTED_AUTHORED == 549
    assert entries == sorted(set(entries))
    assert all(e == normalize_entry(e) for e in entries)
    assert all(e == unicodedata.normalize("NFKC", e).lower() for e in entries)
    assert payload["source"]["id"] == "bloomfilter_fpr_tests_nonnames_v1"
    # A few of the words the engine's tests name.
    for w in ("the", "table", "employer", "river"):
        assert w in entries
    # Dictionary words that are common surnames stay OUT (the list is not a
    # dictionary gate): the engine keeps their boost.
    for w in ("brown", "park", "hill", "bill"):
        assert w not in entries


def test_g8_name_pools_disjoint() -> None:
    """No G8 given name or surname is a list member (the TP-held invariant)."""
    entries = set(build_name_common_words(_SEED)["entries"])
    pool = {normalize_entry(n) for n in _GIVEN_NAMES}
    for names in _SURNAMES_BY_BUCKET.values():
        pool |= {normalize_entry(n) for n in names}
    assert pool, "the G8 name pools must not be empty"
    assert not (pool & entries), sorted(pool & entries)


def _alpha_word(i: int) -> str:
    """Letters-only filler word number ``i`` (the entry pattern admits no digits)."""
    letters = ""
    n = i
    while True:
        letters = chr(ord("a") + n % 26) + letters
        n //= 26
        if n == 0:
            break
    return "w" + letters


def test_duplicates_collapse_and_normalize(tmp_path: Path) -> None:
    filler = [_alpha_word(i) for i in range(EXPECTED_AUTHORED - 6)]
    authored = [*filler, "Alpha", "alpha", "ALPHA", "Beta", "beta", "\ufb01le"]
    src = _write_source(tmp_path / "src.json", authored)
    payload = build_name_common_words(_SEED, source_path=src)
    entries = payload["entries"]
    assert entries.count("alpha") == 1
    assert entries.count("beta") == 1
    assert "file" in entries  # NFKC folds the fi ligature
    # 543 filler + alpha + beta + file = the pinned unique count.
    assert len(entries) == EXPECTED_UNIQUE
    assert entries == sorted(set(entries))


def test_rejects_non_word_entry(tmp_path: Path) -> None:
    authored = [_alpha_word(i) for i in range(EXPECTED_AUTHORED - 1)] + ["two words"]
    src = _write_source(tmp_path / "src.json", authored)
    with pytest.raises(PipelineError, match="not plain lowercase words"):
        build_name_common_words(_SEED, source_path=src)


def test_rejects_authored_count_drift(tmp_path: Path) -> None:
    src = _write_source(tmp_path / "src.json", ["the", "and"])
    with pytest.raises(PipelineError, match="authored entries"):
        build_name_common_words(_SEED, source_path=src)


def test_missing_source(tmp_path: Path) -> None:
    with pytest.raises(MissingSourceError):
        build_name_common_words(_SEED, source_path=tmp_path / "absent.json")


def test_malformed_source(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    src.write_text(json.dumps(["the", "and"]), encoding="utf-8")
    with pytest.raises(PipelineError, match="malformed"):
        build_name_common_words(_SEED, source_path=src)


def test_committed_source_matches_pins() -> None:
    data = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    assert len(data["entries"]) == EXPECTED_AUTHORED
    assert len({normalize_entry(e) for e in data["entries"]}) == EXPECTED_UNIQUE
