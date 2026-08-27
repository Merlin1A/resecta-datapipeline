"""Driver's-license structural test vector builder.

Emits a deterministic catalog of generic driver's-license candidates
exercising the Swift ``detectDriversLicenses`` regex at PIIDetector.swift:648
(pattern declared at :643-646):

    (?:Driver(?:'?s)?\\s+Lic(?:ense)?|DL|D\\.?L\\.?)\\s*[:#]?\\s*
        ([A-Z]\\d{4,14}|\\d{6,12})

Case-insensitive. Capture group 1 is the actual DL number, which matches
either ``[A-Z]\\d{4,14}`` (one letter + 4-14 digits) OR ``\\d{6,12}``
(6-12 digits, no letter).

Per-state formats are out of scope. Vectors cover the generic
label x number-shape cube plus structural rejections (missing label,
short pure-digit run, two-letter prefix, lowercase letter prefix).
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/drivers_license"
_SCHEMA_VERSION: Final[int] = 1

_LABELS: Final[tuple[str, ...]] = (
    "Driver License",
    "Driver's License",
    "Drivers License",
    "Driver Lic",
    "Driver's Lic",
    "DL",
    "D.L.",
    "DL.",
    "D.L",
)
_SEPARATORS: Final[tuple[str, ...]] = (":", "#", "", ": ", "# ")

_VALID_LETTER_NUMERIC_COUNT: Final[int] = 25
_VALID_PURE_NUMERIC_COUNT: Final[int] = 20
_INVALID_NO_LABEL_COUNT: Final[int] = 6
_INVALID_PURE_DIGITS_TOO_SHORT_COUNT: Final[int] = 6
_INVALID_TWO_LETTER_PREFIX_COUNT: Final[int] = 5
_INVALID_LOWERCASE_PREFIX_COUNT: Final[int] = 4

_LETTER_SET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def build(seed: int) -> dict[str, Any]:
    """Return the driver's-license structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/drivers_license_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid letter+digits shape: [A-Z]\d{4,14}.
        for _ in range(_VALID_LETTER_NUMERIC_COUNT):
            label = rng.choice(_LABELS)
            sep = rng.choice(_SEPARATORS)
            letter = rng.choice(_LETTER_SET)
            digit_count = rng.randint(4, 14)
            number = f"{letter}{_digits(rng, digit_count)}"
            text = f"{label}{sep}{number}" if sep else f"{label} {number}"
            vectors.append(
                {
                    "text": text,
                    "license_number": number,
                    "shape": "letter_plus_digits",
                    "valid": True,
                    "rejection_reason": None,
                    "notes": f"[A-Z]\\d{{{digit_count}}} after label ``{label}``.",
                },
            )

        # Valid pure-numeric shape: \d{6,12}.
        for _ in range(_VALID_PURE_NUMERIC_COUNT):
            label = rng.choice(_LABELS)
            sep = rng.choice(_SEPARATORS)
            digit_count = rng.randint(6, 12)
            number = _digits(rng, digit_count)
            text = f"{label}{sep}{number}" if sep else f"{label} {number}"
            vectors.append(
                {
                    "text": text,
                    "license_number": number,
                    "shape": "pure_digits",
                    "valid": True,
                    "rejection_reason": None,
                    "notes": f"\\d{{{digit_count}}} after label ``{label}``.",
                },
            )

        # Invalid: no label prefix.
        for _ in range(_INVALID_NO_LABEL_COUNT):
            number = rng.choice(_LETTER_SET) + _digits(rng, rng.randint(4, 14))
            vectors.append(
                {
                    "text": number,
                    "license_number": number,
                    "shape": "letter_plus_digits",
                    "valid": False,
                    "rejection_reason": "no_label",
                    "notes": "DL regex requires a Driver License / DL label prefix.",
                },
            )

        # Invalid: pure-digits run shorter than 6.
        for _ in range(_INVALID_PURE_DIGITS_TOO_SHORT_COUNT):
            label = rng.choice(_LABELS)
            number = _digits(rng, rng.randint(2, 5))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "license_number": number,
                    "shape": "pure_digits",
                    "valid": False,
                    "rejection_reason": "pure_digits_too_short",
                    "notes": "The floor was widened to 6 digits for pure-numeric DLs.",
                },
            )

        # Invalid: two-letter prefix; the regex's alphabetic alternative is [A-Z] (1 letter).
        for _ in range(_INVALID_TWO_LETTER_PREFIX_COUNT):
            label = rng.choice(_LABELS)
            number = "".join(rng.choice(_LETTER_SET) for _ in range(2))
            number += _digits(rng, rng.randint(4, 14))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "license_number": number,
                    "shape": "letter_plus_digits",
                    "valid": False,
                    "rejection_reason": "letter_prefix_too_long",
                    "notes": "[A-Z] permits exactly one alphabetic prefix character.",
                },
            )

        # Invalid: lowercase prefix letter.
        for _ in range(_INVALID_LOWERCASE_PREFIX_COUNT):
            label = rng.choice(_LABELS)
            number = rng.choice("abcdefghij") + _digits(rng, rng.randint(4, 14))
            vectors.append(
                {
                    "text": f"{label} {number}",
                    "license_number": number,
                    "shape": "letter_plus_digits",
                    "valid": False,
                    "rejection_reason": "lowercase_prefix",
                    "notes": (
                        "Capture-group letter is [A-Z]. Label is case-insensitive; "
                        "the captured prefix is not."
                    ),
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
