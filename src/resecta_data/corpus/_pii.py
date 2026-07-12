"""Deterministic PII value generators for the synthetic corpus.

Every function takes a seeded ``random.Random`` and returns a formatted
string. The generated values are structurally valid (NPI Luhn, DEA
checksum, SSN per SSA rules) so the Swift detectors under test will not
false-reject them.
"""

from __future__ import annotations

import random
import string
from typing import Final

from faker import Faker

from resecta_data.vectors._checksum import compute_npi_check_digit, dea_check_digit
from resecta_data.vectors.ein import _VALID_EIN_PREFIXES
from resecta_data.vectors.routing_number import (
    _ABA_VALID_PREFIX_RANGES,
    _compute_check_digit,
)

_LOCALE_EN_US: Final[str] = "en_US"

_SSN_FORBIDDEN_AREAS: Final[frozenset[int]] = frozenset(
    {0, 666, *range(900, 1000)},
)
_SSN_LITERAL_WOOLWORTH: Final[str] = "078-05-1120"

# Valid DEA first letters. Per DEA policy the first letter encodes
# registrant type (A/B/F/M for physicians, etc.); we restrict to these
# common ones so the synthetic values resemble real registrations.
_DEA_FIRST_LETTERS: Final[tuple[str, ...]] = ("A", "B", "F", "M")


def generate_ssn(rng: random.Random) -> str:
    """Return a structurally valid SSN in ``AAA-GG-SSSS`` form."""
    while True:
        area = rng.randint(1, 899)
        if area in _SSN_FORBIDDEN_AREAS:
            continue
        group = rng.randint(1, 99)
        serial = rng.randint(1, 9999)
        ssn = f"{area:03d}-{group:02d}-{serial:04d}"
        if ssn == _SSN_LITERAL_WOOLWORTH:
            continue
        digits = ssn.replace("-", "")
        if len(set(digits)) == 1:
            continue
        return ssn


def generate_ssn_shaped_decoy(rng: random.Random) -> str:
    """Return an SSN-shaped string that is *not* a valid SSN.

    Used as an adversarial case-number decoy: the shape passes a loose
    regex but structural validation rejects it.
    """
    # Start in forbidden-area range so the real validator rejects.
    area = rng.randint(900, 999)
    group = rng.randint(0, 99)
    serial = rng.randint(0, 9999)
    return f"{area:03d}-{group:02d}-{serial:04d}"


def generate_npi(rng: random.Random) -> str:
    """Return a valid 10-digit NPI starting with 1 or 2."""
    first = rng.choice((1, 2))
    rest = "".join(str(rng.randint(0, 9)) for _ in range(8))
    first_nine = f"{first}{rest}"
    check = compute_npi_check_digit(first_nine)
    return f"{first_nine}{check}"


def generate_npi_shaped_phone(rng: random.Random) -> str:
    """Return a 10-digit phone-looking string that would fail NPI Luhn."""
    # Area-code-first layout; numbers beginning with 3-9 are not valid NPIs.
    area = rng.randint(300, 899)
    exchange = rng.randint(200, 999)
    line = rng.randint(0, 9999)
    return f"({area:03d}) {exchange:03d}-{line:04d}"


def generate_dea(rng: random.Random) -> str:
    """Return a valid DEA number (letter-letter + 7 digits with check)."""
    first = rng.choice(_DEA_FIRST_LETTERS)
    second = rng.choice(string.ascii_uppercase)
    first_six = "".join(str(rng.randint(0, 9)) for _ in range(6))
    check = dea_check_digit(first_six)
    return f"{first}{second}{first_six}{check}"


def generate_dob(rng: random.Random) -> str:
    """Return an MM/DD/YYYY date in the adult birth-year range."""
    month = rng.randint(1, 12)
    # Use 28 as the month cap to avoid per-month day-count logic; still a
    # valid real date for every month.
    day = rng.randint(1, 28)
    year = rng.randint(1935, 2005)
    return f"{month:02d}/{day:02d}/{year:04d}"


def generate_filing_date(rng: random.Random) -> str:
    """Return an MM/DD/YYYY date suitable as a court filing date.

    Looks like a DOB but semantically is a filing event; adversarial
    templates use it to test DOB-shape suppression in court contexts.
    """
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    year = rng.randint(2018, 2025)
    return f"{month:02d}/{day:02d}/{year:04d}"


def generate_account_number(rng: random.Random) -> str:
    """Return a 10-digit account number."""
    return "".join(str(rng.randint(0, 9)) for _ in range(10))


def generate_routing_number(rng: random.Random) -> str:
    """Return an ABA-valid 9-digit routing number.

    Valid first-two-digit prefix (Federal Reserve / thrift / ACH / 80
    ranges) plus the 3-7-1 mod-10 check digit, so the Swift
    ``RoutingNumberDetector`` accepts the value structurally. Validity
    rules are imported from :mod:`resecta_data.vectors.routing_number`
    to keep a single source of truth with the test vectors.
    """
    lo, hi = rng.choice(_ABA_VALID_PREFIX_RANGES)
    prefix = rng.randint(lo, hi)
    inner = [rng.randint(0, 9) for _ in range(6)]
    first_eight = [prefix // 10, prefix % 10, *inner]
    check = _compute_check_digit(first_eight)
    return "".join(str(d) for d in first_eight) + str(check)


def generate_ein(rng: random.Random) -> str:
    """Return a structurally valid EIN in ``XX-XXXXXXX`` form.

    The two-digit prefix is drawn from the IRS-published valid prefix
    list (shared with :mod:`resecta_data.vectors.ein`).
    """
    prefix = rng.choice(_VALID_EIN_PREFIXES)
    serial = rng.randint(0, 9999999)
    return f"{prefix:02d}-{serial:07d}"


def generate_mrn(rng: random.Random) -> str:
    """Return an alpha-digit MRN of 8 characters.

    Medical record numbers have no standard format; this emits a common
    alpha-prefix + 6-digit layout.
    """
    prefix = "".join(rng.choice(string.ascii_uppercase) for _ in range(2))
    suffix = "".join(str(rng.randint(0, 9)) for _ in range(6))
    return f"{prefix}{suffix}"


def generate_phone(rng: random.Random) -> str:
    """Return a (AAA) XXX-XXXX phone number that is not NPI-shaped."""
    # Phones have a dash in the middle; no 10-digit contiguous run means
    # they don't trip the NPI shape detector.
    area = rng.randint(200, 899)
    exchange = rng.randint(200, 999)
    line = rng.randint(0, 9999)
    return f"({area:03d}) {exchange:03d}-{line:04d}"


def generate_email_local(rng: random.Random, first_name: str, last_name: str) -> str:
    """Return a local part 'first.last<digits>@example.com'."""
    first = first_name.lower().replace(" ", "").replace("-", "")
    last = last_name.lower().replace(" ", "").replace("-", "")
    tag = rng.randint(1, 999)
    return f"{first}.{last}{tag}@example.com"


_STREET_NAMES: Final[tuple[str, ...]] = (
    "Oak",
    "Maple",
    "Main",
    "Elm",
    "Cedar",
    "Park",
    "Washington",
    "Lake",
    "Hill",
    "Spring",
    "Pine",
    "Walnut",
    "Church",
    "Lincoln",
    "River",
    "Madison",
    "Jefferson",
    "Adams",
    "Central",
    "North",
)

_STREET_TYPES: Final[tuple[str, ...]] = (
    "St",
    "Ave",
    "Rd",
    "Blvd",
    "Ln",
    "Dr",
    "Ct",
    "Pl",
)

_CITIES: Final[tuple[str, ...]] = (
    "Springfield",
    "Riverside",
    "Georgetown",
    "Franklin",
    "Clinton",
    "Greenville",
    "Bristol",
    "Fairview",
    "Salem",
    "Madison",
    "Arlington",
    "Oakland",
    "Kingston",
    "Lexington",
    "Ashland",
)

# State abbreviations stay lowercase-safe; any is fine for synthetic text.
_STATES: Final[tuple[str, ...]] = (
    "CA",
    "NY",
    "TX",
    "FL",
    "IL",
    "PA",
    "OH",
    "GA",
    "NC",
    "MI",
    "NJ",
    "VA",
    "WA",
    "AZ",
    "MA",
)


def generate_address(rng: random.Random) -> str:
    """Return a single-line US-style street address."""
    number = rng.randint(1, 9999)
    street = rng.choice(_STREET_NAMES)
    kind = rng.choice(_STREET_TYPES)
    city = rng.choice(_CITIES)
    state = rng.choice(_STATES)
    zip_code = rng.randint(10000, 99999)
    return f"{number} {street} {kind}, {city}, {state} {zip_code:05d}"


def generate_localized_address(rng: random.Random, locale: str) -> str:
    """Return a single-line address rendered in the given Faker locale.

    ``en_US`` delegates to :func:`generate_address` so the pre-locale rng
    sequence is preserved for docs that stay in the default locale. For
    other locales a fresh :class:`faker.Faker` is instantiated and seeded
    via ``rng.getrandbits(63)``; multi-line street outputs are collapsed
    to a single line to keep G8 span bookkeeping simple.
    """
    if locale == _LOCALE_EN_US:
        return generate_address(rng)
    fake = Faker(locale)
    fake.seed_instance(rng.getrandbits(63))
    street = fake.street_address().replace("\n", ", ")
    city = fake.city()
    postcode = fake.postcode()
    return f"{street}, {city} {postcode}"


def generate_case_number(rng: random.Random) -> str:
    """Return a court-docket-style case number.

    Not PII — used as filler in court templates.
    """
    year = rng.randint(20, 25)
    kind = rng.choice(("CV", "CR", "FA", "PR"))
    seq = rng.randint(1, 9999)
    return f"{year:02d}-{kind}-{seq:04d}"


def generate_invoice_number(rng: random.Random) -> str:
    """Return an invoice number like 'INV-2024-001234'."""
    year = rng.randint(2022, 2025)
    seq = rng.randint(1, 999999)
    return f"INV-{year}-{seq:06d}"


def generate_request_id(rng: random.Random) -> str:
    """Return a FOIA-style request id like 'FOIA-2024-01234'."""
    year = rng.randint(2022, 2025)
    seq = rng.randint(1, 99999)
    return f"FOIA-{year}-{seq:05d}"
