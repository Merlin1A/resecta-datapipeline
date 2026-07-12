"""US-passport structural test vector builder.

Emits a deterministic catalog of passport candidates exercising the Swift
``detectPassports`` regex at PIIDetector.swift:668 (pattern declared at
:663-666):

    (?:Passport|PP|Passport\\s+No|Passport\\s+Number)\\s*[#:]?\\s*([A-Z]{1,2}\\d{6,9})

Case-insensitive. Capture group 1 is the actual passport number: 1-2
uppercase letters followed by 6-9 digits.

Vectors cover every label variant (Passport, PP, Passport No, Passport
Number) crossed with every separator (``:``, ``#``, bare space) and every
``[A-Z]{1,2}\\d{6,9}`` shape, plus structural rejections that violate the
shape (no label, lowercase letters in the number, too-few or too-many
digits, three-letter prefix).
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/passport"
_SCHEMA_VERSION: Final[int] = 1

_LABELS: Final[tuple[str, ...]] = (
    "Passport",
    "PP",
    "Passport No",
    "Passport Number",
)
_SEPARATORS: Final[tuple[str, ...]] = (":", "#", "", ": ", "# ")

_VALID_COUNT: Final[int] = 40
_INVALID_NO_LABEL_COUNT: Final[int] = 8
_INVALID_LOWERCASE_COUNT: Final[int] = 6
_INVALID_DIGITS_TOO_FEW_COUNT: Final[int] = 6
_INVALID_DIGITS_TOO_MANY_COUNT: Final[int] = 4
_INVALID_THREE_LETTER_PREFIX_COUNT: Final[int] = 4

_LETTER_SET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_LETTER_SET) for _ in range(n))


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def build(seed: int) -> dict[str, Any]:
    """Return the passport structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/passport_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid candidates across the label x separator x letter-count x digit-count cube.
        for _ in range(_VALID_COUNT):
            label = rng.choice(_LABELS)
            sep = rng.choice(_SEPARATORS)
            letter_count = rng.randint(1, 2)
            digit_count = rng.randint(6, 9)
            number = _letters(rng, letter_count) + _digits(rng, digit_count)
            text = f"{label}{sep}{number}" if sep else f"{label} {number}"
            vectors.append(
                {
                    "text": text,
                    "passport_number": number,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": (f"Label ``{label}`` with {letter_count}L+{digit_count}D number."),
                },
            )

        # Invalid: no label prefix.
        for _ in range(_INVALID_NO_LABEL_COUNT):
            number = _letters(rng, rng.randint(1, 2)) + _digits(rng, rng.randint(6, 9))
            vectors.append(
                {
                    "text": number,
                    "passport_number": number,
                    "valid": False,
                    "rejection_reason": "no_label",
                    "notes": "Passport regex requires a Passport / PP label prefix.",
                },
            )

        # Invalid: lowercase letters in the number group.
        for _ in range(_INVALID_LOWERCASE_COUNT):
            label = rng.choice(_LABELS)
            number = "".join(rng.choice("abcdefghij") for _ in range(rng.randint(1, 2)))
            number += _digits(rng, rng.randint(6, 9))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "passport_number": number,
                    "valid": False,
                    "rejection_reason": "lowercase_letters",
                    "notes": "[A-Z]{1,2} requires uppercase prefix letters.",
                },
            )

        # Invalid: too few digits (<6).
        for _ in range(_INVALID_DIGITS_TOO_FEW_COUNT):
            label = rng.choice(_LABELS)
            number = _letters(rng, rng.randint(1, 2)) + _digits(rng, rng.randint(2, 5))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "passport_number": number,
                    "valid": False,
                    "rejection_reason": "digits_too_few",
                    "notes": "Less than 6 digits; \\d{6,9} fails.",
                },
            )

        # Invalid: too many digits (>9).
        for _ in range(_INVALID_DIGITS_TOO_MANY_COUNT):
            label = rng.choice(_LABELS)
            number = _letters(rng, rng.randint(1, 2)) + _digits(rng, rng.randint(10, 12))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "passport_number": number,
                    "valid": False,
                    "rejection_reason": "digits_too_many",
                    "notes": "More than 9 digits; \\d{6,9} caps the trailing run.",
                },
            )

        # Invalid: three-letter prefix.
        for _ in range(_INVALID_THREE_LETTER_PREFIX_COUNT):
            label = rng.choice(_LABELS)
            number = _letters(rng, 3) + _digits(rng, rng.randint(6, 9))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "passport_number": number,
                    "valid": False,
                    "rejection_reason": "letter_prefix_too_long",
                    "notes": "[A-Z]{1,2} caps prefix at two letters.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
