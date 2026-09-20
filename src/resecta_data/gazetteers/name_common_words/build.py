"""Build the common-word curation list (``build/gazetteers/name_common_words.json``).

The multilingual surname filter (Census + Spanish + ParaNames + PopNames)
carries many ordinary English words as genuine inventory rows -- ``the``,
``no``, ``table`` are surnames somewhere. The filter's structural
false-positive rate sits at its 0.1 % design target; the MEMBERSHIP of an
English word list is a different quantity (about 101 of the 549 words below
are members, measured by the engine's ``BloomFilterFPRTests``). This
artifact is the curation the engine reads on top of the filter: a surname
candidate that is an exact member of the list receives no surname credit and
no fuzzy fallback, and the first tagger pass does not count its membership as
inventory support. The Bloom filters themselves are never edited (they are
signed, shared-format artifacts): demote, never strip.

Source: the in-estate list ``BloomFilterFPRTests.nonNames`` (549 authored
entries -- function words, connectives, pronouns and determiners, technical
vocabulary, legal and medical vocabulary, abstract nouns), transcribed into
``sources/name_common_words_v1.json`` with provenance. No external frequency
table is consulted; a frequency-scoped list from a public table is a canon
question for a canon session, not this builder's.

Determinism: entries are NFKC-normalized, lowercased, de-duplicated and
sorted; ``seed`` is recorded but not consumed. The authored count and the
unique count are pinned so a source edit surfaces loudly.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import load_json, sha256_file

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/name_common_words"
_SCHEMA_VERSION: Final[int] = 1
_SOURCE_NAME: Final[str] = "name_common_words_v1.json"
_DEFAULT_SOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "sources" / _SOURCE_NAME

# Pinned counts: the authored list (with its three duplicates) and the unique
# set the artifact ships. Drift means the source changed -- a curated asset
# change under an approved change plan, never a quiet edit.
EXPECTED_AUTHORED: Final[int] = 549
EXPECTED_UNIQUE: Final[int] = 546

# One lowercase ASCII word; an apostrophe or hyphen may appear after the first
# letter. Anything else is not a surname-candidate surface form and is rejected
# so the engine's NFKC-lowercase lookup key is exactly the stored string.
_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z'\-]*")


def normalize_entry(word: str) -> str:
    """NFKC-normalize and lowercase one entry (the engine's lookup key)."""
    return unicodedata.normalize("NFKC", word).lower()


def _load_source(path: Path) -> tuple[list[str], dict[str, Any]]:
    """Load the authored list and its provenance block. Fail loud on shape drift."""
    if not path.exists():
        raise MissingSourceError(f"name_common_words: source {path} is missing.")
    data = load_json(path)
    if not isinstance(data, dict) or "entries" not in data or "provenance" not in data:
        raise PipelineError(
            f"name_common_words: source {path} is malformed "
            "(expected an object with 'provenance' and 'entries')."
        )
    entries = data["entries"]
    provenance = data["provenance"]
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise PipelineError(
            f"name_common_words: source {path} 'entries' must be a list of strings."
        )
    if not isinstance(provenance, dict) or not isinstance(provenance.get("id"), str):
        raise PipelineError(f"name_common_words: source {path} 'provenance.id' must be a string.")
    return entries, provenance


def build(
    seed: int,
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Return the common-word curation payload.

    Args:
        seed: PRNG seed. Recorded for reproducibility; the builder is a
            deterministic normalize-dedupe-sort over the authored list.
        source_path: Override for the authored source JSON. Defaults to
            ``sources/name_common_words_v1.json`` beside this module; tests
            pass a tmp path.

    Returns:
        A payload dict conforming to ``schemas/name_common_words.schema.json``:
        ``entries`` sorted ascending, unique, NFKC-lowercased.

    Raises:
        MissingSourceError: If the source file is absent.
        PipelineError: If the source is malformed, an entry is not a plain
            lowercase word after normalization, or the pinned counts drift.
    """
    path = source_path if source_path is not None else _DEFAULT_SOURCE_PATH
    authored, provenance = _load_source(path)

    if len(authored) != EXPECTED_AUTHORED:
        raise PipelineError(
            f"name_common_words: expected {EXPECTED_AUTHORED} authored entries, "
            f"got {len(authored)}. A source change needs an approved change plan."
        )

    normalized = sorted({normalize_entry(w.strip()) for w in authored})
    bad = [w for w in normalized if not _ENTRY_RE.fullmatch(w)]
    if bad:
        raise PipelineError(
            f"name_common_words: {len(bad)} entries are not plain lowercase words "
            f"after normalization: {bad[:5]}"
        )
    if len(normalized) != EXPECTED_UNIQUE:
        raise PipelineError(
            f"name_common_words: expected {EXPECTED_UNIQUE} unique entries, got {len(normalized)}."
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "source": {
            "id": provenance["id"],
            "sha256": sha256_file(path),
            "authored_count": len(authored),
        },
        "entries": normalized,
    }
