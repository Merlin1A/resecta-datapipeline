"""DEA test vector builder.

Produces a deterministic catalog of DEA candidates spanning four validation
categories. Consumed by Swift detector tests to confirm that DEA
position-weighted checksum validation behaves the same on both sides of the
pipeline boundary.
"""

from __future__ import annotations

from typing import Any, Final

from resecta_data.common.determinism import seeded_context

from ._checksum import dea_check_digit

_GENERATED_BY: Final[str] = "resecta-data/vectors/dea"
_SCHEMA_VERSION: Final[int] = 1

# Ranges chosen so each category is independently interesting.
_VALID_COUNT: Final[int] = 100
_INVALID_CHECKSUM_COUNT: Final[int] = 50
_INVALID_PREFIX_COUNT: Final[int] = 20
_INVALID_LENGTH_COUNT: Final[int] = 20

# For invalid_prefix candidates: probability that the digit lands in position 0
# rather than position 1. 0.5 gives a uniform split.
_DIGIT_IN_FIRST_POSITION_PROB: Final[float] = 0.5

# A DEA candidate needs at least six digits to derive a check digit; shorter
# candidates in the invalid_length category cannot produce one meaningfully.
_MIN_DIGITS_FOR_CHECKSUM: Final[int] = 6


def _random_two_letter_prefix(rng_letter_bytes: list[int]) -> str:
    """Compose a two-letter uppercase prefix from two random bytes in [0, 25]."""
    return "".join(chr(ord("A") + b) for b in rng_letter_bytes[:2])


def build(seed: int) -> dict[str, Any]:
    """Return the DEA test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/dea_test_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid DEAs.
        for _ in range(_VALID_COUNT):
            prefix = _random_two_letter_prefix([rng.randint(0, 25) for _ in range(2)])
            first_six = "".join(str(rng.randint(0, 9)) for _ in range(6))
            check = dea_check_digit(first_six)
            dea = f"{prefix}{first_six}{check}"
            vectors.append(
                {
                    "dea": dea,
                    "valid": True,
                    "category": "valid",
                    "prefix": prefix,
                    "expected_check_digit": check,
                    "reason": "Two uppercase letters, seven digits, correct check digit.",
                }
            )

        # Invalid checksum.
        for _ in range(_INVALID_CHECKSUM_COUNT):
            prefix = _random_two_letter_prefix([rng.randint(0, 25) for _ in range(2)])
            first_six = "".join(str(rng.randint(0, 9)) for _ in range(6))
            good = dea_check_digit(first_six)
            bad = (good + rng.randint(1, 9)) % 10
            dea = f"{prefix}{first_six}{bad}"
            vectors.append(
                {
                    "dea": dea,
                    "valid": False,
                    "category": "invalid_checksum",
                    "prefix": prefix,
                    "expected_check_digit": good,
                    "reason": "Letters and length correct; trailing digit off by design.",
                }
            )

        # Invalid prefix letter: first two characters contain a digit instead
        # of a letter, so the structural requirement fails.
        for _ in range(_INVALID_PREFIX_COUNT):
            # At least one of the first two positions is a digit.
            if rng.random() < _DIGIT_IN_FIRST_POSITION_PROB:
                p0 = str(rng.randint(0, 9))
                p1 = chr(ord("A") + rng.randint(0, 25))
            else:
                p0 = chr(ord("A") + rng.randint(0, 25))
                p1 = str(rng.randint(0, 9))
            prefix = p0 + p1
            first_six = "".join(str(rng.randint(0, 9)) for _ in range(6))
            check = dea_check_digit(first_six)
            dea = f"{prefix}{first_six}{check}"
            vectors.append(
                {
                    "dea": dea,
                    "valid": False,
                    "category": "invalid_prefix_letter",
                    "prefix": prefix,
                    "expected_check_digit": check,
                    "reason": "Prefix contains a digit where a letter is required.",
                }
            )

        # Invalid length: shorter or longer than 9 characters.
        for _ in range(_INVALID_LENGTH_COUNT):
            length = rng.choice([7, 8, 10, 11])
            prefix = _random_two_letter_prefix([rng.randint(0, 25) for _ in range(2)])
            digits = "".join(str(rng.randint(0, 9)) for _ in range(length - 2))
            dea = f"{prefix}{digits}"
            # expected_check_digit is only meaningful when there are at least
            # six digits to derive it from; otherwise report 0 as a sentinel.
            ecd = (
                dea_check_digit(digits[:_MIN_DIGITS_FOR_CHECKSUM])
                if len(digits) >= _MIN_DIGITS_FOR_CHECKSUM
                else 0
            )
            vectors.append(
                {
                    "dea": dea,
                    "valid": False,
                    "category": "invalid_length",
                    "prefix": prefix,
                    "expected_check_digit": ecd,
                    "reason": f"Total length {len(dea)} — not a 9-character DEA.",
                }
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
