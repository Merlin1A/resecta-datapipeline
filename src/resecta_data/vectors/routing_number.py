"""ABA routing-number structural test vector builder.

Emits a deterministic catalog of 9-digit ABA routing number candidates
exercising the Swift ``RoutingNumberDetector`` pattern, ABA checksum
(weights 3-7-1-3-7-1-3-7-1, sum congruent to 0 mod 10), valid first-two-digit
prefix enforcement (ranges 01-12, 21-32, 61-72, 80), and routing context-keyword
requirement.

See design doc 01-detection-engine.md §4.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/routing_number"
_SCHEMA_VERSION: Final[int] = 1

# ABA routing-number checksum weights (3-7-1 pattern, applied to d1..d9).
# Formula: sum(w_i * d_i for i in 1..9) congruent to 0 (mod 10).
# Reference: ABA Routing Number Policy (2013).
_ABA_WEIGHTS: Final[tuple[int, ...]] = (3, 7, 1, 3, 7, 1, 3, 7, 1)

# ABA valid first-two-digit prefix ranges (as of ABA Routing Number Policy
# 2013, confirmed against Federal Reserve E-Payments Routing Directory 2024).
#   01-12: Federal Reserve districts 1-12 (paper)
#   21-32: Federal Reserve districts 1-12 (thrift/savings)
#   61-72: Electronic / ACH
#   80:    Traveler's cheques
_ABA_VALID_PREFIX_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (1, 12),
    (21, 32),
    (61, 72),
    (80, 80),
)

# Context keywords that the Swift RoutingNumberDetector requires within +-8
# whitespace-separated tokens of the number to boost confidence above the
# balanced preset threshold.
_ROUTING_CONTEXT_KEYWORDS: Final[tuple[str, ...]] = (
    "routing number",
    "ABA routing",
    "routing",
    "ACH",
    "direct deposit",
    "wire transfer",
    "bank routing",
    "transit number",
    "routing/transit",
)

# Vector counts per category.
_POSITIVE_COUNT: Final[int] = 12
_INVALID_PREFIX_COUNT: Final[int] = 6
_INVALID_CHECKSUM_COUNT: Final[int] = 6
_NO_CONTEXT_COUNT: Final[int] = 4

# Constant for the expected ABA routing number digit length.
_ABA_DIGIT_COUNT: Final[int] = 9
_ABA_PREFIX_DIGIT_COUNT: Final[int] = 2
_ABA_INNER_DIGITS: Final[int] = 6


def _is_valid_prefix(digits: list[int]) -> bool:
    """Return True when the first two digits form a valid ABA prefix."""
    if len(digits) < _ABA_PREFIX_DIGIT_COUNT:
        return False
    prefix = digits[0] * 10 + digits[1]
    return any(lo <= prefix <= hi for lo, hi in _ABA_VALID_PREFIX_RANGES)


def _aba_checksum(digits: list[int]) -> int:
    """Return the ABA mod-10 checksum for a 9-digit routing number.

    Weights: 3, 7, 1, 3, 7, 1, 3, 7, 1 applied to d1..d9.
    A valid routing number yields 0.
    """
    if len(digits) != _ABA_DIGIT_COUNT:
        raise ValueError(f"Expected {_ABA_DIGIT_COUNT} digits, got {len(digits)}")
    return sum(w * d for w, d in zip(_ABA_WEIGHTS, digits, strict=True)) % 10


def _compute_check_digit(first_eight: list[int]) -> int:
    """Return the 9th digit that makes the ABA checksum valid.

    Args:
        first_eight: The first 8 digits of the routing number.

    Returns:
        A digit in [0, 9]. The check digit is always deterministic:
        (10 - partial_sum mod 10) mod 10.
    """
    partial = sum(
        w * d for w, d in zip(_ABA_WEIGHTS[: _ABA_DIGIT_COUNT - 1], first_eight, strict=True)
    )
    return (10 - partial % 10) % 10


def _generate_valid_routing_number(rng: random.Random) -> str:
    """Return a valid 9-digit ABA routing number (valid prefix + checksum)."""
    # Pick a valid prefix range at random, then pick a prefix within that range.
    lo, hi = rng.choice(_ABA_VALID_PREFIX_RANGES)
    prefix = rng.randint(lo, hi)
    d1, d2 = prefix // 10, prefix % 10
    # Fill digits 3-8 randomly.
    middle = [rng.randint(0, 9) for _ in range(_ABA_INNER_DIGITS)]
    first_eight = [d1, d2, *middle]
    d9 = _compute_check_digit(first_eight)
    return "".join(str(d) for d in [*first_eight, d9])


def build(seed: int) -> dict[str, Any]:
    """Return the ABA routing-number structural test vector payload.

    Produces four classes of vectors:

    * **positive** -- valid ABA checksum AND valid first-two-digit prefix,
      paired with a routing context keyword.  Includes the three
      design-doc verified examples (021000021, 322271627, 124303120).
    * **invalid_prefix** -- structurally 9-digit, but first-two-digit prefix
      not in any ABA range (e.g. 99, 41).
    * **invalid_checksum** -- valid prefix, but checksum fails (last digit
      deliberately off-by-one).
    * **no_context** -- valid prefix and checksum, but no routing keyword
      supplied (base confidence 0.50, below the balanced preset gate 0.60).

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/routing_number_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    # Design-doc section 4 verified positive examples (included unconditionally to
    # provide stable regression anchors across seed changes).
    design_positives = [
        (
            "021000021",
            "routing number",
            "Design-doc section 4 sanity check: prefix 02 in [01,12], "
            "checksum 3*0+7*2+1*1+3*0+7*0+1*0+3*0+7*2+1*1=30, 30%10=0.",
        ),
        (
            "322271627",
            "ABA routing",
            "Design-doc section 4 sanity check: prefix 32 in [21,32], "
            "checksum 3*3+7*2+1*2+3*2+7*7+1*1+3*6+7*2+1*7=120, 120%10=0.",
        ),
        (
            "124303120",
            "routing/transit",
            "Design-doc section 4 sanity check: prefix 12 in [01,12], "
            "checksum 3*1+7*2+1*4+3*3+7*0+1*3+3*1+7*2+1*0=50, 50%10=0.",
        ),
    ]
    for routing_num, keyword, note in design_positives:
        digits = [int(c) for c in routing_num]
        assert _is_valid_prefix(digits), f"Design-positive {routing_num}: bad prefix"
        assert _aba_checksum(digits) == 0, f"Design-positive {routing_num}: bad checksum"
        vectors.append(
            {
                "routing_number": routing_num,
                "valid": True,
                "rejection_reason": None,
                "has_context": True,
                "context_keyword": keyword,
                "notes": note,
            }
        )

    remaining_positives = _POSITIVE_COUNT - len(design_positives)

    with seeded_context(seed) as rng:
        # Additional positive vectors: PRNG-generated, each paired with a
        # rotating routing context keyword.
        for i in range(remaining_positives):
            routing_num = _generate_valid_routing_number(rng)
            keyword = _ROUTING_CONTEXT_KEYWORDS[i % len(_ROUTING_CONTEXT_KEYWORDS)]
            digits = [int(c) for c in routing_num]
            vectors.append(
                {
                    "routing_number": routing_num,
                    "valid": True,
                    "rejection_reason": None,
                    "has_context": True,
                    "context_keyword": keyword,
                    "notes": (
                        f"Seeded-random valid ABA: prefix "
                        f"{digits[0] * 10 + digits[1]:02d}, "
                        f"checksum {_aba_checksum(digits)}."
                    ),
                }
            )

        # Invalid-prefix negatives: first two digits outside every valid range.
        # Covers the design-doc adversarial case (99x) and other ranges.
        invalid_prefix_pairs = [
            (99, "999999999 shape - prefix 99 not in any ABA range."),
            (41, "411111111 shape - prefix 41 not in range; design-doc adversarial case."),
            (13, "Prefix 13 is in the reserved gap (13-20)."),
            (55, "Prefix 55 is in the reserved gap (33-60)."),
            (75, "Prefix 75 is in the reserved gap (73-79)."),
            (0, "Prefix 00 is reserved."),
        ]
        for idx in range(_INVALID_PREFIX_COUNT):
            bad_prefix, note = invalid_prefix_pairs[idx]
            d1, d2 = bad_prefix // 10, bad_prefix % 10
            middle = [rng.randint(0, 9) for _ in range(_ABA_INNER_DIGITS)]
            first_eight = [d1, d2, *middle]
            # Give it a superficially valid checksum digit to confirm it is
            # the prefix check, not checksum, that rejects it.
            d9 = _compute_check_digit(first_eight)
            routing_num = "".join(str(d) for d in [*first_eight, d9])
            vectors.append(
                {
                    "routing_number": routing_num,
                    "valid": False,
                    "rejection_reason": "invalid_prefix",
                    "has_context": True,
                    "context_keyword": "routing number",
                    "notes": note,
                }
            )

        # Invalid-checksum negatives: valid prefix but last digit deliberately
        # altered so checksum is nonzero.
        for _ in range(_INVALID_CHECKSUM_COUNT):
            # Build a valid number, then corrupt the last digit.
            routing_num = _generate_valid_routing_number(rng)
            digits = [int(c) for c in routing_num]
            # Increment last digit mod 10; this always breaks the checksum
            # (changing any digit of a valid number by 1 never restores
            # validity because the weight on d9 is 1 -- so sum changes by
            # exactly +/-1 mod 10).
            bad_last = (digits[8] + 1) % 10
            bad_routing_num = routing_num[:8] + str(bad_last)
            bad_digits = [int(c) for c in bad_routing_num]
            vectors.append(
                {
                    "routing_number": bad_routing_num,
                    "valid": False,
                    "rejection_reason": "invalid_checksum",
                    "has_context": True,
                    "context_keyword": "routing",
                    "notes": (
                        f"Valid prefix {bad_digits[0] * 10 + bad_digits[1]:02d}, "
                        f"last digit incremented by 1: checksum "
                        f"{_aba_checksum(bad_digits)} not equal to 0."
                    ),
                }
            )

        # No-context rows: structurally valid (prefix + checksum pass) with
        # no routing keyword supplied. `valid` carries DETECTOR semantics
        # (mirrors ein_vectors: shape/structure gates only) — the detector
        # still emits these, at base confidence 0.50; suppression below the
        # balanced 0.60 cutoff happens at the orchestrator's W4 gate, not
        # in the detector. has_context=False is what pins the base-vs-boost
        # confidence expectation on the Swift side.
        for _ in range(_NO_CONTEXT_COUNT):
            routing_num = _generate_valid_routing_number(rng)
            digits = [int(c) for c in routing_num]
            vectors.append(
                {
                    "routing_number": routing_num,
                    "valid": True,
                    "rejection_reason": None,
                    "has_context": False,
                    "context_keyword": None,
                    "notes": (
                        f"Valid ABA (prefix {digits[0] * 10 + digits[1]:02d}, "
                        f"checksum 0) with no routing context keyword: the "
                        f"detector emits base confidence 0.50; the balanced "
                        f"preset's W4 gate (0.60) is what withholds it."
                    ),
                }
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
