"""NPI test vector builder.

Produces a deterministic catalog of 10-digit NPI candidates spanning six
validation categories. Consumed by Swift detector tests to confirm that CMS
Luhn-with-80840-prefix validation behaves the same on both sides of the
pipeline boundary.
"""

from __future__ import annotations

from typing import Any, Final

from resecta_data.common.determinism import seeded_context

from ._checksum import cms_luhn, compute_npi_check_digit

_GENERATED_BY: Final[str] = "resecta-data/vectors/npi"
_SCHEMA_VERSION: Final[int] = 1

# How many vectors to emit per category. The counts are deliberately modest —
# each row is independently interesting, so coverage matters more than volume.
_VALID_INDIVIDUAL_COUNT: Final[int] = 50
_VALID_ORGANIZATION_COUNT: Final[int] = 50
_INVALID_CHECKSUM_COUNT: Final[int] = 50
_INVALID_PREFIX_COUNT: Final[int] = 20
_INVALID_LENGTH_COUNT: Final[int] = 20


def _synthesize_valid_npi(rng_digits: list[int], first_digit: int) -> str:
    """Compose a valid NPI that starts with ``first_digit``.

    The first nine digits are ``first_digit`` followed by eight rng digits,
    and the tenth is chosen to satisfy CMS Luhn.
    """
    body = f"{first_digit}{''.join(str(d) for d in rng_digits[:8])}"
    check = compute_npi_check_digit(body)
    return body + str(check)


def build(seed: int) -> dict[str, Any]:
    """Return the NPI test vector payload.

    Args:
        seed: PRNG seed. Different seeds produce different sample corpora;
            the canonical seed is recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/npi_test_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid individual (prefix 1).
        for _ in range(_VALID_INDIVIDUAL_COUNT):
            digits = [rng.randint(0, 9) for _ in range(8)]
            npi = _synthesize_valid_npi(digits, first_digit=1)
            vectors.append(
                {
                    "npi": npi,
                    "valid": True,
                    "category": "valid_individual",
                    "expected_luhn_remainder": 0,
                    "reason": "Prefix 1 (individual provider), correct CMS Luhn check digit.",
                }
            )

        # Valid organization (prefix 2).
        for _ in range(_VALID_ORGANIZATION_COUNT):
            digits = [rng.randint(0, 9) for _ in range(8)]
            npi = _synthesize_valid_npi(digits, first_digit=2)
            vectors.append(
                {
                    "npi": npi,
                    "valid": True,
                    "category": "valid_organization",
                    "expected_luhn_remainder": 0,
                    "reason": "Prefix 2 (organization provider), correct CMS Luhn check digit.",
                }
            )

        # Invalid checksum: valid prefix and length, check digit off by one.
        for _ in range(_INVALID_CHECKSUM_COUNT):
            first_digit = rng.choice([1, 2])
            digits = [rng.randint(0, 9) for _ in range(8)]
            body = f"{first_digit}{''.join(str(d) for d in digits)}"
            good_check = compute_npi_check_digit(body)
            # Offset by 1..9 so we never accidentally produce a valid NPI.
            bad_check = (good_check + rng.randint(1, 9)) % 10
            npi = body + str(bad_check)
            remainder = cms_luhn(npi)
            vectors.append(
                {
                    "npi": npi,
                    "valid": False,
                    "category": "invalid_checksum",
                    "expected_luhn_remainder": remainder,
                    "reason": "Valid prefix and length, deliberately wrong check digit.",
                }
            )

        # Invalid prefix: 10 digits but starts with something other than 1 or 2.
        for _ in range(_INVALID_PREFIX_COUNT):
            first_digit = rng.choice([0, 3, 4, 5, 6, 7, 8, 9])
            digits = [rng.randint(0, 9) for _ in range(8)]
            body = f"{first_digit}{''.join(str(d) for d in digits)}"
            check = compute_npi_check_digit(body)  # make Luhn pass so prefix is the sole fault
            npi = body + str(check)
            vectors.append(
                {
                    "npi": npi,
                    "valid": False,
                    "category": "invalid_prefix",
                    "expected_luhn_remainder": 0,
                    "reason": "Luhn-valid but prefix is not 1 or 2.",
                }
            )

        # Invalid length: 9 or 11 digits.
        for _ in range(_INVALID_LENGTH_COUNT):
            length = rng.choice([9, 11])
            npi = "".join(str(rng.randint(0, 9)) for _ in range(length))
            # Luhn remainder is only defined for 10-digit NPIs; mark with a
            # sentinel-zero (the value is informational here, and validity
            # fails on length before the checksum is ever checked).
            vectors.append(
                {
                    "npi": npi,
                    "valid": False,
                    "category": "invalid_length",
                    "expected_luhn_remainder": 0,
                    "reason": f"Length {length} — not a 10-digit NPI.",
                }
            )

        # Edge: all-same-digit.
        for digit in range(10):
            npi = str(digit) * 10
            # Valid iff prefix is 1 or 2 AND Luhn passes.
            remainder = cms_luhn(npi)
            prefix_ok = digit in (1, 2)
            is_valid = prefix_ok and remainder == 0
            vectors.append(
                {
                    "npi": npi,
                    "valid": is_valid,
                    "category": "edge_all_same_digit",
                    "expected_luhn_remainder": remainder,
                    "reason": f"All digits are {digit}.",
                }
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
