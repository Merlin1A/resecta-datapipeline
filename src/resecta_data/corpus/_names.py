"""Demographically-stratified name sampler for the synthetic corpus.

Uses small curated name pools drawn from public, commonly-known names.
These pools are intentionally minimal — the G8 corpus measures detector
behavior per demographic bucket, so the sampler needs per-bucket
distributions, but it does not need a licensed dataset. The Phase 2
Bloom filter is where full-scale demographic coverage lives; this module
is a small reference pool for Phase 3 synthetic documents.

The AI/AN bucket relies on the smaller ``_AI_AN_SURNAMES`` pool; this is a
known residual-risk limitation around coverage.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from typing import Final, Literal

# Gender-neutral-ish shared given-name pool. US first-name distributions
# are less bucket-specific than surnames, so a shared pool is adequate for
# the synthetic corpus.
_GIVEN_NAMES: Final[tuple[str, ...]] = (
    "Alex",
    "Amanda",
    "Andre",
    "Angela",
    "Carlos",
    "Chen",
    "Chris",
    "Daniel",
    "David",
    "Ellen",
    "Emily",
    "Frank",
    "Grace",
    "Hassan",
    "Jamal",
    "James",
    "Jennifer",
    "Jin",
    "Jose",
    "Karen",
    "Keisha",
    "Kenji",
    "Linda",
    "Maria",
    "Mark",
    "Michael",
    "Michelle",
    "Min",
    "Nia",
    "Priya",
    "Rachel",
    "Robert",
    "Rosa",
    "Sarah",
    "Sofia",
    "Stephen",
    "Tamara",
    "Thomas",
    "Tomas",
    "William",
)

# Surnames curated by bucket from public, commonly-known names.
_WHITE_SURNAMES: Final[tuple[str, ...]] = (
    "Anderson",
    "Brown",
    "Carter",
    "Clark",
    "Davis",
    "Foster",
    "Harris",
    "Johnson",
    "Jones",
    "Miller",
    "Moore",
    "Murphy",
    "Palmer",
    "Parker",
    "Robinson",
    "Smith",
    "Taylor",
    "Thompson",
    "Walker",
    "Williams",
)

_BLACK_SURNAMES: Final[tuple[str, ...]] = (
    "Banks",
    "Booker",
    "Bryant",
    "Cameron",
    "Charles",
    "Cooper",
    "Dixon",
    "Fields",
    "Gaines",
    "Grant",
    "Hamilton",
    "Hayes",
    "Jackson",
    "Jefferson",
    "Mack",
    "Pierce",
    "Powell",
    "Simmons",
    "Washington",
    "Wright",
)

_HISPANIC_SURNAMES: Final[tuple[str, ...]] = (
    "Aguilar",
    "Alvarez",
    "Castillo",
    "Castro",
    "Cruz",
    "Delgado",
    "Flores",
    "Fuentes",
    "Garcia",
    "Hernandez",
    "Lopez",
    "Martinez",
    "Mendoza",
    "Morales",
    "Perez",
    "Ramirez",
    "Reyes",
    "Rivera",
    "Rodriguez",
    "Sanchez",
)

_ASIAN_SURNAMES: Final[tuple[str, ...]] = (
    "Chan",
    "Chen",
    "Chou",
    "Gupta",
    "Huang",
    "Kim",
    "Kumar",
    "Lee",
    "Lin",
    "Liu",
    "Nakamura",
    "Nguyen",
    "Park",
    "Patel",
    "Singh",
    "Tran",
    "Wang",
    "Wong",
    "Yamamoto",
    "Yang",
)

# AI/AN curated pool is shorter — no broadly permissive dataset exists.
# These are widely-known surnames
# associated with Native American public figures and communities.
_AI_AN_SURNAMES: Final[tuple[str, ...]] = (
    "Begay",
    "Bird",
    "Blackhorse",
    "Deerinwater",
    "Greyeyes",
    "Hawk",
    "Lonewolf",
    "Redcloud",
    "Whitefeather",
    "Yellowtail",
)

_SURNAMES_BY_BUCKET: Final[dict[str, tuple[str, ...]]] = {
    "white": _WHITE_SURNAMES,
    "black": _BLACK_SURNAMES,
    "hispanic": _HISPANIC_SURNAMES,
    "asian": _ASIAN_SURNAMES,
    "ai_an": _AI_AN_SURNAMES,
}

BUCKETS: Final[tuple[str, ...]] = (
    "white",
    "black",
    "hispanic",
    "asian",
    "ai_an",
)


# Name FORMS (1.2 C12-95 Spec-A): the surface a person-name slot renders.
# The corpus as furnished renders every slot as ``full`` (``Given Surname``;
# the court caption's ALL-CAPS is its own adversarial class). The Spec-A
# generator profile draws one form per rendered slot at the pre-registered
# rates below and records it on the span as ``form``.
Form = Literal["full", "initial", "last_first", "all_caps", "suffix", "particled"]

FORM_FULL: Final[Form] = "full"
FORM_INITIAL: Final[Form] = "initial"
FORM_LAST_FIRST: Final[Form] = "last_first"
FORM_ALL_CAPS: Final[Form] = "all_caps"
FORM_SUFFIX: Final[Form] = "suffix"
FORM_PARTICLED: Final[Form] = "particled"

NAME_FORMS: Final[tuple[str, ...]] = (
    FORM_FULL,
    FORM_INITIAL,
    FORM_LAST_FIRST,
    FORM_ALL_CAPS,
    FORM_SUFFIX,
    FORM_PARTICLED,
)

# The pre-registered per-span rates ([R09] Section 5; 1.2 D12-75 / D12-129):
# middle initial 20 %, "Last, First" 10 %, ALL-CAPS 10 %, suffix 5 %,
# hyphenated / particled 5 %, the remaining 50 % the full form. Cumulative
# thresholds on one draw in [0, 100). The rates are pre-registered guesses,
# not population statistics (no public name-form table exists).
_FORM_THRESHOLDS: Final[tuple[tuple[int, Form], ...]] = (
    (20, FORM_INITIAL),
    (30, FORM_LAST_FIRST),
    (40, FORM_ALL_CAPS),
    (45, FORM_SUFFIX),
    (50, FORM_PARTICLED),
)
_FORM_DRAW_MODULUS: Final[int] = 100

SUFFIXES: Final[tuple[str, ...]] = ("Jr.", "Sr.", "II", "III", "IV")
# The particled variants: index 0 is the hyphenated double surname (a second
# surname from the same bucket pool), the rest prefix the surname.
PARTICLES: Final[tuple[str, ...]] = ("de la ", "van ", "O'")
_PARTICLED_VARIANTS: Final[int] = 1 + len(PARTICLES)


def draw_form(rng: random.Random) -> Form:
    """Draw one name form at the pre-registered rates from ``rng``."""
    roll = rng.randrange(_FORM_DRAW_MODULUS)
    for threshold, form in _FORM_THRESHOLDS:
        if roll < threshold:
            return form
    return FORM_FULL


@dataclass(frozen=True, slots=True)
class Person:
    """A synthetic person drawn from one demographic bucket."""

    given_name: str
    surname: str
    bucket: str

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.surname}"

    @property
    def all_caps(self) -> str:
        return self.full_name.upper()

    def render(self, form: Form, rng: random.Random) -> str:
        """Render this person in ``form``; the form's own draws come from ``rng``.

        ``initial`` draws the middle initial, ``suffix`` the suffix,
        ``particled`` one of the hyphenated / particle variants (and, for the
        hyphenated one, a second surname from this person's bucket pool that
        differs from the first). ``full``, ``last_first`` and ``all_caps``
        draw nothing.
        """
        if form not in NAME_FORMS:
            raise ValueError(f"unknown name form {form!r}; choose from {NAME_FORMS}")
        if form == FORM_INITIAL:
            letter = string.ascii_uppercase[rng.randrange(len(string.ascii_uppercase))]
            text = f"{self.given_name} {letter}. {self.surname}"
        elif form == FORM_LAST_FIRST:
            text = f"{self.surname}, {self.given_name}"
        elif form == FORM_ALL_CAPS:
            text = self.all_caps
        elif form == FORM_SUFFIX:
            text = f"{self.full_name} {SUFFIXES[rng.randrange(len(SUFFIXES))]}"
        elif form == FORM_PARTICLED:
            text = self._particled(rng)
        else:
            text = self.full_name
        return text

    def _particled(self, rng: random.Random) -> str:
        variant = rng.randrange(_PARTICLED_VARIANTS)
        if variant == 0:
            pool = [s for s in _SURNAMES_BY_BUCKET[self.bucket] if s != self.surname]
            second = pool[rng.randrange(len(pool))]
            return f"{self.given_name} {self.surname}-{second}"
        return f"{self.given_name} {PARTICLES[variant - 1]}{self.surname}"


class NameSampler:
    """Sample persons with per-bucket surname distributions.

    Deterministic given the passed ``random.Random``. Given names are
    drawn from the shared pool; surnames are drawn from the bucket pool.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def sample(self, bucket: str) -> Person:
        """Return a :class:`Person` from ``bucket``."""
        if bucket not in _SURNAMES_BY_BUCKET:
            raise ValueError(f"Unknown bucket {bucket!r}; expected one of {BUCKETS}")
        given = self._rng.choice(_GIVEN_NAMES)
        surname = self._rng.choice(_SURNAMES_BY_BUCKET[bucket])
        return Person(given_name=given, surname=surname, bucket=bucket)
