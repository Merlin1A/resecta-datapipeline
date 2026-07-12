"""Bates-stamp structural test vector builder.

Emits a deterministic catalog of Bates-stamp candidates exercising the
Swift ``detectBates`` regex at PIIDetector.swift:755 (pattern declared at
:748-751):

    \\b[A-Z]{1,10}[-_ ]?\\d{4,10}\\b

Bates stamps are short uppercase prefixes (1-10 letters) followed by 4-10
digits, optionally separated by ``-``, ``_``, or a single space. The Swift
detector is gated by ``runsBates(doctype:)`` and context-scored against
``BatesContextKeywords.profile``; vectors here cover only the regex shape.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/bates"
_SCHEMA_VERSION: Final[int] = 1

_VALID_COUNT: Final[int] = 50
_INVALID_LOWERCASE_COUNT: Final[int] = 6
_INVALID_PREFIX_TOO_LONG_COUNT: Final[int] = 5
_INVALID_DIGITS_TOO_FEW_COUNT: Final[int] = 5
_INVALID_DIGITS_TOO_MANY_COUNT: Final[int] = 4
_INVALID_BAD_SEPARATOR_COUNT: Final[int] = 4

_LETTERS: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SEPARATORS: Final[tuple[str, ...]] = ("-", "_", " ", "")


def _letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_LETTERS) for _ in range(n))


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def build(seed: int) -> dict[str, Any]:
    """Return the Bates-stamp structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/bates_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid: prefix x separator x digit-count cube.
        for _ in range(_VALID_COUNT):
            prefix_len = rng.randint(1, 10)
            digit_len = rng.randint(4, 10)
            sep = rng.choice(_SEPARATORS)
            stamp = f"{_letters(rng, prefix_len)}{sep}{_digits(rng, digit_len)}"
            vectors.append(
                {
                    "stamp": stamp,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": (
                        f"{prefix_len}-letter prefix + {digit_len}-digit run, "
                        f"separator ``{sep!r}``."
                    ),
                },
            )

        # Invalid: lowercase letters.
        for _ in range(_INVALID_LOWERCASE_COUNT):
            prefix = "".join(
                rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 6))
            )
            digits = _digits(rng, rng.randint(4, 10))
            vectors.append(
                {
                    "stamp": f"{prefix}-{digits}",
                    "valid": False,
                    "rejection_reason": "lowercase_prefix",
                    "notes": "[A-Z] requires uppercase letters; pattern is not case-insensitive.",
                },
            )

        # Invalid: prefix > 10 letters.
        for _ in range(_INVALID_PREFIX_TOO_LONG_COUNT):
            prefix = _letters(rng, rng.randint(11, 14))
            digits = _digits(rng, rng.randint(4, 10))
            vectors.append(
                {
                    "stamp": f"{prefix}-{digits}",
                    "valid": False,
                    "rejection_reason": "prefix_too_long",
                    "notes": "[A-Z]{1,10} caps the prefix at 10 letters.",
                },
            )

        # Invalid: digit run < 4.
        for _ in range(_INVALID_DIGITS_TOO_FEW_COUNT):
            prefix = _letters(rng, rng.randint(1, 10))
            digits = _digits(rng, rng.randint(1, 3))
            vectors.append(
                {
                    "stamp": f"{prefix}-{digits}",
                    "valid": False,
                    "rejection_reason": "digits_too_few",
                    "notes": "\\d{4,10} requires at least 4 trailing digits.",
                },
            )

        # Invalid: digit run > 10.
        for _ in range(_INVALID_DIGITS_TOO_MANY_COUNT):
            prefix = _letters(rng, rng.randint(1, 10))
            digits = _digits(rng, rng.randint(11, 14))
            vectors.append(
                {
                    "stamp": f"{prefix}-{digits}",
                    "valid": False,
                    "rejection_reason": "digits_too_many",
                    "notes": "\\d{4,10} caps the digit run at 10.",
                },
            )

        # Invalid: separator not in {-, _, space}.
        for idx in range(_INVALID_BAD_SEPARATOR_COUNT):
            prefix = _letters(rng, rng.randint(1, 10))
            digits = _digits(rng, rng.randint(4, 10))
            bad_sep = "/.,@"[idx % 4]
            vectors.append(
                {
                    "stamp": f"{prefix}{bad_sep}{digits}",
                    "valid": False,
                    "rejection_reason": "invalid_separator",
                    "notes": "[-_ ]? permits only hyphen, underscore, or single space.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
