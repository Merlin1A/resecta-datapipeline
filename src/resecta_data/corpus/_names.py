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
from dataclasses import dataclass
from typing import Final

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
