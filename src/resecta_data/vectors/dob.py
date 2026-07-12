"""DOB structural test vector builder.

Emits a deterministic catalog of Date-of-Birth candidates exercising both
the labeled-prefix gate of Swift ``detectDOBs`` (DOB:/Date of Birth:/Born/
Birthdate prefix) and the per-month/leap-year structural validation in
``DOBDetector.isStructurallyValid``.

The Swift detector at PIIDetector.swift accepts month 1-12 and day 1-31
range checks; the dedicated ``DOBDetector`` class tightens that to full
Gregorian validation (leap years, 30-day months, Feb 29 rule). Vectors
encode the stricter contract.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/dob"
_SCHEMA_VERSION: Final[int] = 1

# Month length in a common (non-leap) year. February is 28.
_MONTH_DAYS_COMMON: Final[tuple[int, ...]] = (
    31,
    28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
)

# February is month 2; the leap-year exception applies only to this month.
_FEBRUARY: Final[int] = 2
_FEBRUARY_LEAP_DAYS: Final[int] = 29

_LABEL_PREFIXES: Final[tuple[str, ...]] = (
    "DOB: ",
    "D.O.B.: ",
    "Date of Birth: ",
    "Born: ",
    "Birthdate: ",
    "Birth Date: ",
)

_MONTH_NAMES: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_NUMERIC_VALID_COUNT: Final[int] = 15
_TEXTUAL_VALID_COUNT: Final[int] = 5
_LEAP_YEAR_VALID_COUNT: Final[int] = 4
_LEAP_YEAR_INVALID_COUNT: Final[int] = 4
_INVALID_MONTH_COUNT: Final[int] = 6
_INVALID_DAY_COUNT: Final[int] = 8
_INVALID_MONTH_SPECIFIC_COUNT: Final[int] = 8

_MIN_YEAR: Final[int] = 1920
_MAX_YEAR: Final[int] = 2024


def _is_leap(year: int) -> bool:
    """Gregorian leap-year rule."""
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def _days_in(month: int, year: int) -> int:
    """Return days in ``month`` of ``year`` respecting leap years."""
    if month == _FEBRUARY and _is_leap(year):
        return _FEBRUARY_LEAP_DAYS
    return _MONTH_DAYS_COMMON[month - 1]


def _format_numeric(month: int, day: int, year: int, sep: str = "/") -> str:
    """Format ``M/D/YYYY`` (no zero-padding matches the Swift regex)."""
    return f"{month}{sep}{day}{sep}{year}"


def _format_textual(month: int, day: int, year: int) -> str:
    """Format ``Month D, YYYY`` (matches the second Swift regex branch)."""
    return f"{_MONTH_NAMES[month - 1]} {day}, {year}"


def _valid_date(rng: random.Random) -> tuple[int, int, int]:
    """Draw a uniformly random valid (month, day, year) triple."""
    year = rng.randint(_MIN_YEAR, _MAX_YEAR)
    month = rng.randint(1, 12)
    day = rng.randint(1, _days_in(month, year))
    return month, day, year


def build(seed: int) -> dict[str, Any]:
    """Return the DOB structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/dob_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid numeric dates with random label prefix and separator.
        for _ in range(_NUMERIC_VALID_COUNT):
            month, day, year = _valid_date(rng)
            sep = rng.choice(["/", "-"])
            prefix = rng.choice(_LABEL_PREFIXES)
            date_part = _format_numeric(month, day, year, sep)
            vectors.append(
                {
                    "text": f"{prefix}{date_part}",
                    "date_part": date_part,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Numeric date with label prefix; passes per-month validation.",
                },
            )

        # Valid textual dates ("January 15, 1990").
        for _ in range(_TEXTUAL_VALID_COUNT):
            month, day, year = _valid_date(rng)
            prefix = rng.choice(_LABEL_PREFIXES)
            date_part = _format_textual(month, day, year)
            vectors.append(
                {
                    "text": f"{prefix}{date_part}",
                    "date_part": date_part,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Textual month-name date with label prefix.",
                },
            )

        # Feb 29 in leap years — accepted.
        leap_years = [2000, 2004, 2020, 2024]
        for year in leap_years[:_LEAP_YEAR_VALID_COUNT]:
            date_part = _format_numeric(2, 29, year)
            vectors.append(
                {
                    "text": f"DOB: {date_part}",
                    "date_part": date_part,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": f"Feb 29 {year} — leap year, valid.",
                },
            )

        # Feb 29 in non-leap years — rejected.
        non_leap_years = [1900, 2021, 2023, 2100]
        for year in non_leap_years[:_LEAP_YEAR_INVALID_COUNT]:
            date_part = _format_numeric(2, 29, year)
            vectors.append(
                {
                    "text": f"DOB: {date_part}",
                    "date_part": date_part,
                    "valid": False,
                    "rejection_reason": "non_leap_feb_29",
                    "notes": f"Feb 29 {year} — not a leap year.",
                },
            )

        # Month out of [1, 12].
        for _ in range(_INVALID_MONTH_COUNT):
            month = rng.choice([0, 13, 14, 15])
            day = rng.randint(1, 28)
            year = rng.randint(_MIN_YEAR, _MAX_YEAR)
            date_part = _format_numeric(month, day, year)
            vectors.append(
                {
                    "text": f"DOB: {date_part}",
                    "date_part": date_part,
                    "valid": False,
                    "rejection_reason": "invalid_month",
                    "notes": f"Month {month} outside 1..12.",
                },
            )

        # Day out of [1, 31].
        for _ in range(_INVALID_DAY_COUNT):
            day = rng.choice([0, 32, 33, 40])
            month = rng.randint(1, 12)
            year = rng.randint(_MIN_YEAR, _MAX_YEAR)
            date_part = _format_numeric(month, day, year)
            vectors.append(
                {
                    "text": f"DOB: {date_part}",
                    "date_part": date_part,
                    "valid": False,
                    "rejection_reason": "invalid_day",
                    "notes": f"Day {day} outside 1..31.",
                },
            )

        # Month-specific day caps: Feb 30, Apr 31, Jun 31, Sep 31, Nov 31, Feb 29 non-leap.
        specific_invalids = [
            (2, 30, 2020, "feb_30"),
            (4, 31, 2020, "apr_31"),
            (6, 31, 2020, "jun_31"),
            (9, 31, 2020, "sep_31"),
            (11, 31, 2020, "nov_31"),
            (2, 30, 2024, "feb_30_leap"),  # Still invalid even in leap year.
            (4, 31, 1995, "apr_31_alt"),
            (6, 31, 1985, "jun_31_alt"),
        ]
        for month, day, year, reason in specific_invalids[:_INVALID_MONTH_SPECIFIC_COUNT]:
            date_part = _format_numeric(month, day, year)
            vectors.append(
                {
                    "text": f"DOB: {date_part}",
                    "date_part": date_part,
                    "valid": False,
                    "rejection_reason": "month_day_overflow",
                    "notes": f"{reason}: day {day} exceeds {_MONTH_NAMES[month - 1]} length.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
