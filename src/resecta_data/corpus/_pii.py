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
from resecta_data.vectors.credit_card import _complete_with_luhn, _flip_last_digit
from resecta_data.vectors.drivers_license import _LABELS as _DL_LABELS
from resecta_data.vectors.drivers_license import _SEPARATORS as _DL_SEPARATORS
from resecta_data.vectors.ein import _VALID_EIN_PREFIXES
from resecta_data.vectors.itin import _VALID_YY_RANGES, _yy_is_valid
from resecta_data.vectors.itin import _format as _format_itin
from resecta_data.vectors.license_plate import _LABELS as _PLATE_LABELS
from resecta_data.vectors.license_plate import _SEPARATORS as _PLATE_SEPARATORS
from resecta_data.vectors.passport import _LABELS as _PASSPORT_LABELS
from resecta_data.vectors.passport import _SEPARATORS as _PASSPORT_SEPARATORS
from resecta_data.vectors.routing_number import (
    _ABA_VALID_PREFIX_RANGES,
    _compute_check_digit,
)

_LOCALE_EN_US: Final[str] = "en_US"
_LOCALE_ES_MX: Final[str] = "es_MX"
# A Mexican codigo postal is five digits (01000-99999). Faker 25.9.2's es_MX
# ``postcode()`` returns a ZIP+4-shaped ``#####-####`` about half the time;
# the Spec-G generator profile replaces it with this draw.
_MX_CP_MIN: Final[int] = 1000
_MX_CP_MAX: Final[int] = 99999

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


def generate_localized_address(
    rng: random.Random, locale: str, *, es_mx_five_digit_cp: bool = False
) -> str:
    """Return a single-line address rendered in the given Faker locale.

    ``en_US`` delegates to :func:`generate_address` so the pre-locale rng
    sequence is preserved for docs that stay in the default locale. For
    other locales a fresh :class:`faker.Faker` is instantiated and seeded
    via ``rng.getrandbits(63)``; multi-line street outputs are collapsed
    to a single line to keep G8 span bookkeeping simple.

    ``es_mx_five_digit_cp`` (off by default, so the corpus as furnished is
    byte-preserved) replaces Faker's es_MX ``postcode()`` -- ZIP+4-shaped
    about half the time -- with a five-digit codigo postal drawn from
    ``rng`` after the Faker seed (the Spec-G profile's draw).
    """
    if locale == _LOCALE_EN_US:
        return generate_address(rng)
    fake = Faker(locale)
    fake.seed_instance(rng.getrandbits(63))
    street = fake.street_address().replace("\n", ", ")
    city = fake.city()
    if es_mx_five_digit_cp and locale == _LOCALE_ES_MX:
        postcode = f"{rng.randint(_MX_CP_MIN, _MX_CP_MAX):05d}"
    else:
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
    """Return an invoice number: an ``INV-`` prefix, a year, a six-digit sequence."""
    year = rng.randint(2022, 2025)
    seq = rng.randint(1, 999999)
    return f"INV-{year}-{seq:06d}"


def generate_request_id(rng: random.Random) -> str:
    """Return a FOIA-style request id like 'FOIA-2024-01234'."""
    year = rng.randint(2022, 2025)
    seq = rng.randint(1, 99999)
    return f"FOIA-{year}-{seq:05d}"


# ---------------------------------------------------------------------------
# The five categories that took the corpus from 12 to 17 families:
# itin, creditCard, driversLicense, passport, licensePlate.
#
# Validity rules are imported from the matching ``vectors/`` module (one
# source of truth with the structural test vectors, the EIN / routing
# precedent above). Each category also has a "negative twin" generator that
# emits the packet ground truth's must-not-fire class for it: a shape the
# regex accepts but a documented validation rule rejects (Luhn, the IRS YY
# ranges, the per-jurisdiction / per-issuer pattern gazetteers) -- or, for
# plates, a label the regex reads as a plate label on a non-plate value.
# ---------------------------------------------------------------------------

_UPPER: Final[str] = string.ascii_uppercase

# IRS-issued YY groups (the four ranges) and their complement inside [0, 99].
_ITIN_VALID_YY: Final[tuple[int, ...]] = tuple(
    yy for low, high in _VALID_YY_RANGES for yy in range(low, high + 1)
)
_ITIN_INVALID_YY: Final[tuple[int, ...]] = tuple(yy for yy in range(100) if not _yy_is_valid(yy))

# Card IIN families the engine's prefix gate accepts and that lay out as a
# 16-digit 4-4-4-4 PAN (Amex is 15 digits in 4-6-5 groups, which the engine's
# grouped-regex shape does not read, so it is not sampled here).
_CARD_PREFIXES: Final[tuple[str, ...]] = ("4", "51", "52", "53", "54", "55", "6011")
_CARD_LENGTH: Final[int] = 16
_CARD_GROUP: Final[int] = 4

# Driver's-license digit counts after the alpha prefix. Both shapes match a
# jurisdiction row of the shipped dl_patterns gazetteer (``[A-Z][0-9]{7}``,
# ``[A-Z][0-9]{8}``) as well as the engine's ``[A-Z]\d{4,14}`` capture.
_DL_DIGIT_COUNTS: Final[tuple[int, ...]] = (7, 8)
# 1 letter + 14 digits: inside the capture group's upper bound, longer than
# every jurisdiction row (13 chars max) -> the gazetteer gate suppresses it.
_DL_DECOY_DIGITS: Final[int] = 14

# Passport: 1 letter + 8 digits matches several issuer rows of the shipped
# passport_patterns gazetteer; 1 letter + 6 digits matches none (every row is
# 8-9 characters) -> the packet's must-not-fire class.
_PASSPORT_DIGITS: Final[int] = 8
_PASSPORT_DECOY_DIGITS: Final[int] = 6

_PLATE_LETTERS: Final[int] = 3
_PLATE_DIGITS: Final[int] = 4
_PLATE_INNER_SEPARATORS: Final[tuple[str, ...]] = ("-", " ", "")

# Plate labels that carry none of the plate context profile's positive
# keywords inside themselves ("Registration #" / "Reg #" / "Vehicle plate" /
# "Veh plate" do), so a template can draw a keyword-STARVED plate surface.
_PLATE_LABELS_KEYWORD_FREE: Final[tuple[str, ...]] = tuple(
    label for label in _PLATE_LABELS if not any(kw in label.lower() for kw in ("reg", "veh"))
)


def _upper_letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_UPPER) for _ in range(n))


def _digit_run(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _label_prefix(rng: random.Random, labels: tuple[str, ...], separators: tuple[str, ...]) -> str:
    """Return ``label + separator`` ready for the value to be appended.

    The label / separator tables are the vector modules' own, so every
    emitted label form is one the paired detector regex was verified to
    read. An empty separator becomes a single space.
    """
    label = rng.choice(labels)
    sep = rng.choice(separators)
    return f"{label}{sep}" if sep else f"{label} "


def generate_itin(rng: random.Random) -> str:
    """Return an IRS-issuable ITIN in ``9AA-YY-SSSS`` form (YY in a valid range)."""
    area = rng.randint(900, 999)
    yy = rng.choice(_ITIN_VALID_YY)
    serial = rng.randint(1, 9999)
    return _format_itin(area, yy, serial)


def generate_itin_yy_out_of_range(rng: random.Random) -> str:
    """Return an ITIN-shaped decoy whose YY group is outside every IRS range."""
    area = rng.randint(900, 999)
    yy = rng.choice(_ITIN_INVALID_YY)
    serial = rng.randint(1, 9999)
    return _format_itin(area, yy, serial)


def _card_pan(rng: random.Random) -> str:
    return _complete_with_luhn(rng.choice(_CARD_PREFIXES), _CARD_LENGTH, rng)


def _group_pan(pan: str) -> str:
    return " ".join(pan[i : i + _CARD_GROUP] for i in range(0, len(pan), _CARD_GROUP))


def generate_credit_card(rng: random.Random) -> str:
    """Return a Luhn-valid 16-digit PAN with an accepted IIN, spaced 4-4-4-4."""
    return _group_pan(_card_pan(rng))


def generate_luhn_failed_card(rng: random.Random) -> str:
    """Return a card-shaped decoy: accepted IIN, 16 digits, Luhn check broken."""
    return _group_pan(_flip_last_digit(_card_pan(rng), rng))


def generate_drivers_license(rng: random.Random) -> str:
    """Return a DL number: one letter + 7 or 8 digits (a jurisdiction-row shape)."""
    return _upper_letters(rng, 1) + _digit_run(rng, rng.choice(_DL_DIGIT_COUNTS))


def generate_dl_shaped_no_jurisdiction(rng: random.Random) -> str:
    """Return a DL-shaped decoy (1 letter + 14 digits) longer than every jurisdiction row."""
    return _upper_letters(rng, 1) + _digit_run(rng, _DL_DECOY_DIGITS)


def generate_dl_label(rng: random.Random) -> str:
    """Return a driver's-license label + separator from the vector module's table."""
    return _label_prefix(rng, _DL_LABELS, _DL_SEPARATORS)


def generate_passport(rng: random.Random) -> str:
    """Return a passport number: one letter + 8 digits (an issuer-row shape)."""
    return _upper_letters(rng, 1) + _digit_run(rng, _PASSPORT_DIGITS)


def generate_passport_shaped_no_issuer(rng: random.Random) -> str:
    """Return a passport-shaped decoy (1 letter + 6 digits) matching no issuer row."""
    return _upper_letters(rng, 1) + _digit_run(rng, _PASSPORT_DECOY_DIGITS)


def generate_passport_label(rng: random.Random) -> str:
    """Return a passport label + separator from the vector module's table."""
    return _label_prefix(rng, _PASSPORT_LABELS, _PASSPORT_SEPARATORS)


def generate_license_plate(rng: random.Random) -> str:
    """Return a plate value: 3 letters, an optional hyphen/space, 4 digits."""
    inner = rng.choice(_PLATE_INNER_SEPARATORS)
    return f"{_upper_letters(rng, _PLATE_LETTERS)}{inner}{_digit_run(rng, _PLATE_DIGITS)}"


def generate_plate_label(rng: random.Random, *, keyword_free: bool = False) -> str:
    """Return a plate label + separator from the vector module's table.

    ``keyword_free`` restricts the draw to labels that do not themselves
    contain a plate context keyword, for a keyword-starved surface.
    """
    labels = _PLATE_LABELS_KEYWORD_FREE if keyword_free else _PLATE_LABELS
    return _label_prefix(rng, labels, _PLATE_SEPARATORS)


def generate_business_registration(rng: random.Random) -> str:
    """Return a corporate registration id like '2019-CA-00871' (not a plate).

    The plate regex reads ``Registration #`` as a plate label, so the id
    behind it is the label-collision decoy: not PII, and shaped so the
    plate capture matches its leading year group.
    """
    year = rng.randint(2015, 2025)
    state = rng.choice(_STATES)
    seq = rng.randint(1, 99999)
    return f"{year}-{state}-{seq:05d}"
