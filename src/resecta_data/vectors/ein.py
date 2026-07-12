"""EIN structural test vector builder.

Emits a deterministic catalog of Employer Identification Number candidates
exercising the Swift ``detectEINs`` regex ``\\d{2}-\\d{7}`` and its
length/shape rules. The Swift detector has no checksum; validation is
purely structural (regex + context-based confidence boost).
"""

from __future__ import annotations

from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/ein"
_SCHEMA_VERSION: Final[int] = 1

# Vector counts per category.
_VALID_COUNT: Final[int] = 25
_INVALID_LENGTH_COUNT: Final[int] = 8
_INVALID_SHAPE_COUNT: Final[int] = 7

# IRS-published valid EIN prefix ranges. Not all two-digit prefixes are
# assigned; the full list is at
# https://www.irs.gov/businesses/small-businesses-self-employed/how-eins-are-assigned-and-valid-ein-prefixes
_VALID_EIN_PREFIXES: Final[tuple[int, ...]] = (
    1,
    2,
    3,
    4,
    5,
    6,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    50,
    51,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
    59,
    60,
    61,
    62,
    63,
    64,
    65,
    66,
    67,
    68,
    71,
    72,
    73,
    74,
    75,
    76,
    77,
    80,
    81,
    82,
    83,
    84,
    85,
    86,
    87,
    88,
    90,
    91,
    92,
    93,
    94,
    95,
    98,
    99,
)


def build(seed: int) -> dict[str, Any]:
    """Return the EIN structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/ein_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid structural EINs: AA-BBBBBBB with AA drawn from IRS-assigned
        # prefixes. The Swift detector does not verify the prefix list, so
        # the ``valid`` flag tracks only the regex/length gate.
        for _ in range(_VALID_COUNT):
            prefix = rng.choice(_VALID_EIN_PREFIXES)
            suffix = rng.randint(0, 9999999)
            ein = f"{prefix:02d}-{suffix:07d}"
            vectors.append(
                {
                    "ein": ein,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": f"IRS-assigned prefix {prefix:02d}, 7-digit suffix.",
                },
            )

        # Invalid length: the Swift regex locks length at 10 (2 + hyphen + 7).
        # Anything shorter or longer fails the anchored pattern.
        length_variants = [
            (1, 5),  # AA-BBBBB  (9 chars)
            (2, 5),  # AA-BBBBB  (9 chars) — different prefix
            (1, 8),  # A-BBBBBBBB (10 chars but wrong shape)
            (2, 8),  # AA-BBBBBBBB (11 chars)
            (1, 6),  # A-BBBBBB (8 chars)
            (2, 9),  # AA-BBBBBBBBB (12 chars)
            (1, 7),  # A-BBBBBBB (9 chars)
            (2, 6),  # AA-BBBBBB (9 chars)
        ]
        for idx in range(_INVALID_LENGTH_COUNT):
            prefix_len, suffix_len = length_variants[idx]
            prefix_s = "".join(str(rng.randint(0, 9)) for _ in range(prefix_len))
            suffix_s = "".join(str(rng.randint(0, 9)) for _ in range(suffix_len))
            ein = f"{prefix_s}-{suffix_s}"
            vectors.append(
                {
                    "ein": ein,
                    "valid": False,
                    "rejection_reason": "invalid_length",
                    "notes": f"Prefix {prefix_len}d, suffix {suffix_len}d — not AA-BBBBBBB.",
                },
            )

        # Invalid shape: correct total digit count, wrong layout (no hyphen or
        # hyphen in wrong position). Guarantees the regex's explicit hyphen
        # slot at index 2 fails.
        for _ in range(_INVALID_SHAPE_COUNT):
            digits = "".join(str(rng.randint(0, 9)) for _ in range(9))
            # Place the hyphen somewhere other than after the second digit.
            hyphen_pos = rng.choice([1, 3, 4, 5, 6])
            candidate = digits[:hyphen_pos] + "-" + digits[hyphen_pos:]
            vectors.append(
                {
                    "ein": candidate,
                    "valid": False,
                    "rejection_reason": "invalid_shape",
                    "notes": "Hyphen not at position 2 — regex anchored shape rejects.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
