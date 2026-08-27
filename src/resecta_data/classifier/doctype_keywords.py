"""Build the doctype keywords artifact.

Shipped to ``Resources/Classifier/doctype-keywords.json`` and consumed by
``DocumentTypeClassifier.swift``. Changes here require a paired Swift PR.

The builder is deterministic: given a seed, it returns the same payload.
Curated lists live in ``_keyword_data``; the seed is recorded for
uniformity but does not currently alter content.
"""

from __future__ import annotations

from typing import Any, Final

from resecta_data.common.exceptions import PipelineError

from ._keyword_data import (
    CANONICAL_CLASSES,
    KEYWORDS_BY_CLASS,
    STRUCTURAL_BY_CLASS,
)

_MODULE_NAME: Final[str] = "resecta_data.classifier.doctype_keywords"
_SCHEMA_VERSION: Final[int] = 1

# Per-term hit cap. The Swift classifier counts each term's hits up to
# this cap, so a page cannot be tilted by stuffing one term. Raised from
# the binary-presence cap of 1 (the A4 / G5 hardening decision) to 5 in
# 5 at the calibration pass: cap 1 made logits nearly flat (max raw evidence
# ~= vocabulary overlap), starving the temperature fit; cap 5 admits
# repeated-vocabulary evidence while keeping the stuffing bound.
_TERM_CAP_PER_DOC: Final[int] = 5

_KEYWORD_MIN: Final[int] = 30
_KEYWORD_MAX: Final[int] = 200


def _build_class(name: str) -> dict[str, Any]:
    keywords = KEYWORDS_BY_CLASS[name]
    if not (_KEYWORD_MIN <= len(keywords) <= _KEYWORD_MAX):
        raise PipelineError(
            f"Class {name!r}: {len(keywords)} keywords, expected [{_KEYWORD_MIN}, {_KEYWORD_MAX}]."
        )

    sorted_keywords = sorted(set(keywords))
    if len(sorted_keywords) != len(keywords):
        raise PipelineError(f"Class {name!r} contains duplicate keywords.")

    bonuses = STRUCTURAL_BY_CLASS[name]
    bonus_entries = [
        {
            "id": ident,
            "pattern": pattern,
            "bonus": bonus,
            "rationale": rationale,
        }
        for (ident, pattern, bonus, rationale) in bonuses
    ]

    return {
        "name": name,
        "keywords": sorted_keywords,
        "structural_bonuses": bonus_entries,
    }


def build(seed: int) -> dict[str, Any]:
    """Return the doctype keywords payload.

    Args:
        seed: Recorded in the artifact for reproducibility; does not alter
            content since the underlying lists are curated.

    Returns:
        A payload dict conforming to ``schemas/doctype_keywords.schema.json``.

    Raises:
        PipelineError: If a class fails its invariants (keyword count,
            uniqueness).
    """
    classes = [_build_class(name) for name in CANONICAL_CLASSES]

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "term_cap_per_doc": _TERM_CAP_PER_DOC,
        "classes": classes,
    }
