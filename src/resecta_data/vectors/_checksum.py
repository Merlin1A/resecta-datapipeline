"""Pure checksum math for NPI and DEA numbers.

No I/O, no randomness — every function is a pure transform over digit strings.
These helpers are reused by both the vector generators in this package and
(eventually) the Python-side reference implementations that cross-check the
Swift detector output.
"""

from __future__ import annotations

from typing import Final

# CMS prepends this fixed string before applying Luhn to the 10-digit NPI.
# See https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/nationalprovidentstand
_NPI_CMS_PREFIX: Final[str] = "80840"

_NPI_LENGTH: Final[int] = 10
_DEA_DIGIT_COUNT: Final[int] = 7  # six positional digits plus the check digit

# Luhn doubling wraps: any doubled digit greater than 9 has 9 subtracted
# (equivalent to summing its decimal digits, since the double of 0-9 peaks at 18).
_LUHN_WRAP_THRESHOLD: Final[int] = 9


def cms_luhn(npi: str) -> int:
    """Return the Luhn remainder of an NPI with the CMS 80840 prefix applied.

    A valid NPI yields 0. Any other value indicates a checksum failure.

    Args:
        npi: Exactly ten ASCII digits. No hyphens or spaces.

    Returns:
        An integer in ``range(10)``.

    Raises:
        ValueError: If ``npi`` is not ten digits.
    """
    if len(npi) != _NPI_LENGTH or not npi.isdigit():
        raise ValueError(f"NPI must be {_NPI_LENGTH} digits, got {npi!r}")

    digits = _NPI_CMS_PREFIX + npi
    total = 0
    # Luhn doubles every second digit from the right, which is every digit at
    # an odd index when iterating from the rightmost position.
    for i, char in enumerate(reversed(digits)):
        digit = int(char)
        if i % 2 == 1:
            digit *= 2
            if digit > _LUHN_WRAP_THRESHOLD:
                digit -= _LUHN_WRAP_THRESHOLD
        total += digit
    return total % 10


def luhn_mod10(digits: str) -> bool:
    """Return True iff ``digits`` pass the plain Luhn mod-10 check.

    Unlike ``cms_luhn``, no prefix is prepended — this is the standard
    Luhn used by credit-card PANs. Callers that need a different length
    gate must enforce it themselves; this helper accepts any non-empty
    digit string.

    Args:
        digits: ASCII digit characters, no separators. Length is not
            constrained here because credit-card PANs range from
            13 (legacy Visa) to 19 (UnionPay, Discover).

    Returns:
        True when the Luhn checksum remainder is zero.

    Raises:
        ValueError: If ``digits`` is empty or contains non-digit characters.
    """
    if not digits or not digits.isdigit():
        raise ValueError(f"Expected non-empty digit string, got {digits!r}")

    total = 0
    # Luhn doubles every second digit from the right, which is every digit at
    # an odd index when iterating from the rightmost position.
    for i, char in enumerate(reversed(digits)):
        digit = int(char)
        if i % 2 == 1:
            digit *= 2
            if digit > _LUHN_WRAP_THRESHOLD:
                digit -= _LUHN_WRAP_THRESHOLD
        total += digit
    return total % 10 == 0


def compute_npi_check_digit(first_nine: str) -> int:
    """Return the check digit that makes ``first_nine + check`` a valid NPI.

    Args:
        first_nine: The first nine digits of a candidate NPI.

    Returns:
        The single check digit (0-9).

    Raises:
        ValueError: If ``first_nine`` is not nine digits.
    """
    if len(first_nine) != _NPI_LENGTH - 1 or not first_nine.isdigit():
        raise ValueError(f"Expected 9 digits, got {first_nine!r}")

    # Apply Luhn with check digit zero, then compute what the check digit must
    # be to bring the remainder to zero.
    trial = cms_luhn(first_nine + "0")
    return (10 - trial) % 10


def dea_check_digit(first_six: str) -> int:
    """Return the DEA check digit for a given six-digit prefix.

    DEA number layout is ``XY#######`` where ``XY`` is a two-letter prefix and
    the seven digits that follow end with a check digit. This function takes
    the first six digits (everything except the check digit) and returns the
    check digit that a valid DEA would carry.

    Formula: ``(d0 + d2 + d4 + 2*(d1 + d3 + d5)) mod 10``.

    Args:
        first_six: Six ASCII digits, in the same order they appear in the DEA.

    Returns:
        The single check digit (0-9).

    Raises:
        ValueError: If ``first_six`` is not six digits.
    """
    if len(first_six) != _DEA_DIGIT_COUNT - 1 or not first_six.isdigit():
        raise ValueError(f"Expected 6 digits, got {first_six!r}")

    d = [int(c) for c in first_six]
    odd_sum = d[0] + d[2] + d[4]
    even_sum = d[1] + d[3] + d[5]
    return (odd_sum + 2 * even_sum) % 10
