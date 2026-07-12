"""Build the negative-context candidate gazetteer.

Reads the three bootstrap source files under
``src/resecta_data/gazetteers/sources/negative_context/`` and emits a
deterministic list of candidate entries. The artifact lands in
``build/gazetteers/negative_context_candidates.json`` and is *not* installed —
Jesse hand-reviews before a renamed, reviewed
``negative_context.json`` is ever shipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from resecta_data.common.exceptions import MissingSourceError

from ._scope_rules import known_source_ids, manual_entries, scope_keyword

_SOURCE_FILES: dict[str, str] = {
    "us_courts_glossary": "us_courts_glossary_20260416.txt",
    "doj_legal_glossary": "doj_legal_glossary_20260416.txt",
    "ubl_invoice_labels": "ubl_invoice_labels_20260416.txt",
}

_MODULE_NAME = "resecta_data.gazetteers.negative_context.build"
_SCHEMA_VERSION = 1


def _read_keywords(path: Path) -> list[str]:
    """One keyword per non-comment non-empty line, lowercased and stripped."""
    if not path.is_file():
        raise MissingSourceError(f"Negative-context source missing: {path}")
    keywords: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        keywords.append(lowered)
    return keywords


def build(seed: int, *, source_dir: Path) -> dict[str, Any]:
    """Produce the candidate negative-context gazetteer.

    Args:
        seed: Recorded in the artifact for uniformity; the candidate-list
            generator is not randomized.
        source_dir: Directory containing the three bootstrap .txt files.

    Returns:
        A JSON-serializable dict matching ``negative_context.schema.json``.
    """
    assert known_source_ids() == set(_SOURCE_FILES), "scope rules and build sources diverged"

    # (keyword, category_scope, doctype_scope) deduplication — if multiple
    # sources propose the same triple, keep the first (source id, rationale).
    # Source-file-derived rows are added before hand-curated rows so that when
    # a keyword appears in both a licensed corpus and a manual list, the
    # corpus-derived provenance wins.
    seen: dict[tuple[str, str, str], tuple[float, str, str]] = {}
    for source_id, filename in _SOURCE_FILES.items():
        path = source_dir / filename
        for keyword in _read_keywords(path):
            for category, doctype, weight, rationale in scope_keyword(keyword, source_id):
                key = (keyword, category, doctype)
                if key in seen:
                    continue
                seen[key] = (weight, source_id, rationale)

    for keyword, category, doctype, weight, source_id, rationale in manual_entries():
        key = (keyword, category, doctype)
        if key in seen:
            continue
        seen[key] = (weight, source_id, rationale)

    entries = [
        {
            "keyword": keyword,
            "category_scope": category,
            "doctype_scope": doctype,
            "precedence_weight": weight,
            "source_id": source_id,
            "rationale": rationale,
        }
        for (keyword, category, doctype), (weight, source_id, rationale) in sorted(seen.items())
    ]

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "entries": entries,
    }
