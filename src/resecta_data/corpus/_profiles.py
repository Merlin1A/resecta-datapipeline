"""Generator profiles for the G8 corpus (1.2 C12-95 Spec-C / Spec-D; C12-29 (b)).

A profile is an AXIS on the shipped emitters, not a copy of them. ``g8`` is
the corpus as furnished: every template renders exactly the bytes it always
has, and the profile stream is never consulted. The other profiles keep every
document's BASE stream untouched (the per-document sub-seed still draws the
same names, values and decoy rolls, in the same order) and take their own
choices from a SECOND stream seeded on ``(master, doctype, index, profile)``:

- ``g8-specC`` -- name-context variety: every person-name slot is rendered in
  one of the shipped context plus four variants, drawn per slot from the
  profile stream, each tagged with its own ``context_class`` (the boxed
  "X Name:" label, a table row, a running header, body prose, ``Attn:`` /
  ``c/o`` labels, honorific and ``Esq.`` titles, a signature block).
- ``g8-specD`` -- furniture density: the court, medical and foia templates
  plant role nouns and labels at the pre-registered per-document rates and
  every letter carries a salutation and a closing, each region recorded in
  the document's ``furniture`` array with a kind, never as a ground-truth
  span.
- ``g8-specCD`` -- both.

So a profile document is the ``g8`` document with its name slots re-rendered
and furniture planted: every ground-truth value, every non-name slot and
every decoy are identical, which is what lets a profile be reported as a
PAIR against ``g8`` on the same 1,100 documents.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ._spans import ContextClass, SpanBuilder, append_name_or_placeholder

PROFILE_G8: Final[str] = "g8"
PROFILE_SPEC_C: Final[str] = "g8-specC"
PROFILE_SPEC_D: Final[str] = "g8-specD"
PROFILE_SPEC_CD: Final[str] = "g8-specCD"

PROFILES: Final[tuple[str, ...]] = (
    PROFILE_G8,
    PROFILE_SPEC_C,
    PROFILE_SPEC_D,
    PROFILE_SPEC_CD,
)

_SPEC_C_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_C, PROFILE_SPEC_CD})
_SPEC_D_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_D, PROFILE_SPEC_CD})

# Furniture kinds Spec-D plants (``furniture[].kind`` is a free string in the
# schema; these are the values this generator writes).
FURNITURE_ROLE_NOUN: Final[str] = "role_noun"
FURNITURE_LABEL: Final[str] = "label"
FURNITURE_PLATE_LABEL: Final[str] = "plate_label"
FURNITURE_SALUTATION: Final[str] = "salutation"
FURNITURE_CLOSING: Final[str] = "closing"
FURNITURE_KINDS: Final[tuple[str, ...]] = (
    FURNITURE_ROLE_NOUN,
    FURNITURE_LABEL,
    FURNITURE_PLATE_LABEL,
    FURNITURE_SALUTATION,
    FURNITURE_CLOSING,
)

# The pre-registered per-document rates ([R09] Section 5, D12-75): role nouns
# per court document, per medical document; label lines per court / foia
# document. Inclusive ranges drawn uniformly from the profile stream.
COURT_ROLE_NOUNS_PER_DOC: Final[tuple[int, int]] = (6, 12)
MEDICAL_ROLE_NOUNS_PER_DOC: Final[tuple[int, int]] = (4, 8)
LABELS_PER_DOC: Final[tuple[int, int]] = (1, 3)

# Running-header page furniture: "Page p of q" with 2 <= p <= q <= 6.
_HEADER_MAX_PAGES: Final[int] = 6
_HEADER_MIN_PAGE: Final[int] = 2
PAGE_MARK: Final[str] = "{page}"

# Every sentence carries EXACTLY ONE role noun, so k planted sentences are k
# role nouns; the noun is sentence-initial in half of them (the surface the
# stop-list and the legal-prefix pass see) and mid-sentence in the rest.
COURT_ROLE_NOUN_SENTENCES: Final[tuple[str, ...]] = (
    "Plaintiff demands a trial by jury on all issues so triable.",
    "Defendant denies each and every allegation not expressly admitted.",
    "Plaintiff incorporates the foregoing paragraphs by reference.",
    "Defendant reserves the right to amend this answer.",
    "Plaintiff is entitled to the relief requested herein.",
    "Defendant is without knowledge sufficient to form a belief.",
    "Counsel for Plaintiff appeared at the hearing.",
    "Service was accepted on behalf of Defendant.",
    "The motion filed by Plaintiff is granted in part.",
    "Discovery propounded on Defendant remains outstanding.",
    "Judgment is entered in favor of Plaintiff.",
    "Costs are assessed against Defendant.",
)
COURT_ROLE_NOUNS: Final[tuple[str, ...]] = ("Plaintiff", "Defendant")

MEDICAL_ROLE_NOUN_SENTENCES: Final[tuple[str, ...]] = (
    "Patient tolerated the procedure well.",
    "Provider reviewed the discharge instructions in full.",
    "Dr. to follow up in clinic in two weeks.",
    "Patient ambulating independently at discharge.",
    "Provider signature on file.",
    "Dr. on call was notified of the result.",
    "Patient verbalized understanding of the plan.",
    "Provider to reassess at the next visit.",
    "Dr. reviewed the imaging with the family.",
    "Patient denies chest pain or dyspnea.",
    "Instructions were given to the Patient in writing.",
    "The order was countersigned by the Provider.",
)
MEDICAL_ROLE_NOUNS: Final[tuple[str, ...]] = ("Patient", "Provider", "Dr.")

# Label lines: exactly one label token each; ``label`` = the registration
# class (the ``Reg`` stop token's surface), ``plate_label`` = the plate class.
LABEL_LINES: Final[tuple[tuple[str, str], ...]] = (
    ("Reg # on file with the clerk.", FURNITURE_LABEL),
    ("Registration attached as an exhibit.", FURNITURE_LABEL),
    ("Tag record attached.", FURNITURE_PLATE_LABEL),
    ("Plate photo attached.", FURNITURE_PLATE_LABEL),
    ("Reg # to be supplied.", FURNITURE_LABEL),
    ("Registration renewal pending.", FURNITURE_LABEL),
    ("Tag number as recorded on the citation.", FURNITURE_PLATE_LABEL),
    ("Plate as recorded on the citation.", FURNITURE_PLATE_LABEL),
)
LABEL_TOKENS: Final[tuple[str, ...]] = ("Reg #", "Registration", "Tag", "Plate")

HONORIFICS: Final[tuple[str, ...]] = ("Mr. ", "Ms. ", "Mrs. ")


@dataclass(frozen=True)
class Profile:
    """The active profile and its own random stream (never the base stream)."""

    name: str
    rng: random.Random

    def __post_init__(self) -> None:
        if self.name not in PROFILES:
            raise ValueError(f"unknown corpus profile {self.name!r}; choose from {PROFILES}")

    @property
    def spec_c(self) -> bool:
        return self.name in _SPEC_C_PROFILES

    @property
    def spec_d(self) -> bool:
        return self.name in _SPEC_D_PROFILES


def is_spec_c(profile: str) -> bool:
    return profile in _SPEC_C_PROFILES


def is_spec_d(profile: str) -> bool:
    return profile in _SPEC_D_PROFILES


def _spec_c(profile: Profile | None) -> bool:
    return profile is not None and profile.spec_c


def _spec_d(profile: Profile | None) -> bool:
    return profile is not None and profile.spec_d


def _if_spec_c(profile: Profile | None) -> Profile | None:
    """The profile when Spec-C is active, else None (a typed narrowing)."""
    return profile if profile is not None and profile.spec_c else None


def _if_spec_d(profile: Profile | None) -> Profile | None:
    """The profile when Spec-D is active, else None (a typed narrowing)."""
    return profile if profile is not None and profile.spec_d else None


# ---------------------------------------------------------------------------
# Spec-C: name-slot contexts
# ---------------------------------------------------------------------------

Cue = str | Callable[[random.Random], str]


@dataclass(frozen=True)
class NameContext:
    """One rendering of a name slot: the text before the name, its class, the
    text after. ``before`` / ``after`` may be a callable of the profile stream
    (an honorific drawn per slot); ``{page}`` in either is replaced by a
    "Page p of q" drawn per slot. ``before_kind`` names the furniture kind the
    cue is recorded under when Spec-D is active (a salutation or closing cue)."""

    context_class: ContextClass
    before: Cue = ""
    after: Cue = ""
    before_kind: str | None = None


def _resolve(cue: Cue, rng: random.Random) -> str:
    text = cue(rng) if callable(cue) else cue
    if PAGE_MARK in text:
        pages = rng.randint(_HEADER_MIN_PAGE, _HEADER_MAX_PAGES)
        page = rng.randint(_HEADER_MIN_PAGE, pages)
        text = text.replace(PAGE_MARK, f"Page {page} of {pages}")
    return text


def choose_context(
    profile: Profile | None, shipped: NameContext, variants: tuple[NameContext, ...]
) -> NameContext:
    """The shipped context, or under Spec-C one of shipped + variants, uniformly."""
    active = _if_spec_c(profile)
    if active is None:
        return shipped
    options = (shipped, *variants)
    return options[active.rng.randrange(len(options))]


def render_name_slot(
    sb: SpanBuilder,
    profile: Profile | None,
    full_name: str,
    *,
    name_sparse: bool,
    shipped: NameContext,
    variants: tuple[NameContext, ...],
) -> None:
    """Render one person-name slot in its shipped context or a Spec-C variant.

    Under ``g8`` (``profile`` None or ``g8``) this appends exactly the shipped
    bytes and touches no stream. Under Spec-C the context is drawn from the
    profile stream; the name (or the sparse placeholder) is appended with the
    chosen context's class.
    """
    ctx = choose_context(profile, shipped, variants)
    rng = profile.rng if profile is not None else random.Random(0)  # noqa: S311
    before = _resolve(ctx.before, rng)
    after = _resolve(ctx.after, rng)
    if ctx.before_kind is not None and _spec_d(profile):
        cue = before.rstrip()
        sb.append_furniture(cue, ctx.before_kind)
        sb.append(before[len(cue) :])
    else:
        sb.append(before)
    append_name_or_placeholder(
        sb, full_name, name_sparse=name_sparse, context_class=ctx.context_class
    )
    sb.append(after)


def honorific(rng: random.Random) -> str:
    return HONORIFICS[rng.randrange(len(HONORIFICS))]


def header_after() -> str:
    """The running-header tail: the name followed by page furniture."""
    return f" — {PAGE_MARK}"


# ---------------------------------------------------------------------------
# Spec-D: furniture
# ---------------------------------------------------------------------------


def plant_role_nouns(
    sb: SpanBuilder,
    profile: Profile | None,
    sentences: tuple[str, ...],
    rate: tuple[int, int],
) -> int:
    """Plant ``k`` role-noun sentences (one noun each), ``k`` drawn in ``rate``.

    Each sentence is one ``role_noun`` furniture region covering the WHOLE
    sentence, so a detection on the word after the noun (the legal-prefix
    pass's surface) still attributes to the furniture. Returns ``k``; 0 when
    Spec-D is off (nothing appended).
    """
    active = _if_spec_d(profile)
    if active is None:
        return 0
    count = active.rng.randint(*rate)
    # Distinct sentences within a document (the pool is at least as large as
    # the top of every rate), in the drawn order.
    for sentence in active.rng.sample(sentences, count):
        sb.append_furniture(sentence, FURNITURE_ROLE_NOUN)
        sb.append("\n")
    return count


def plant_labels(
    sb: SpanBuilder, profile: Profile | None, rate: tuple[int, int] = LABELS_PER_DOC
) -> int:
    """Plant ``k`` label lines (Reg # / Registration / Tag / Plate), ``k`` in ``rate``."""
    active = _if_spec_d(profile)
    if active is None:
        return 0
    count = active.rng.randint(*rate)
    for line, kind in active.rng.sample(LABEL_LINES, count):
        sb.append_furniture(line, kind)
        sb.append("\n")
    return count


def append_cue(sb: SpanBuilder, profile: Profile | None, text: str, kind: str) -> None:
    """Append a salutation / closing cue the template always emits, recording it
    as furniture of ``kind`` under Spec-D (the bytes are the same either way)."""
    if _spec_d(profile):
        sb.append_furniture(text, kind)
    else:
        sb.append(text)


def plant_salutation(sb: SpanBuilder, profile: Profile | None, text: str) -> None:
    """Plant a name-free salutation line (Spec-D only; nothing under g8)."""
    if not _spec_d(profile):
        return
    sb.append_furniture(text, FURNITURE_SALUTATION)
    sb.append("\n\n")
