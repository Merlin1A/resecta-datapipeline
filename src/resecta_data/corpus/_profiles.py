"""Generator profiles for the G8 corpus (the Spec-C / Spec-D axes and their siblings).

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
- ``g8-specA`` -- name forms: every person-name slot the templates render
  through :func:`render_name_slot` draws one surface form from the profile
  stream at the pre-registered rates (a middle initial 20 %, "Last, First"
  10 %, ALL-CAPS 10 %, a suffix 5 %, a hyphenated / particled surname 5 %,
  the full form otherwise) and records it on the span as ``form``. The
  court caption is not a Spec-A slot: its ALL-CAPS is its own adversarial
  class and its spans record the surface they carry.
- ``g8-specG`` -- the locale axis: each document's Faker locale is drawn
  from the profile stream, crossed with every demographic bucket at the
  corpus-wide marginal of ``g8`` (6 % es_MX, 6 % es_ES, 88 % en_US) instead
  of being tied to the hispanic bucket, and an es_MX address carries a
  five-digit codigo postal. Every span records the document's ``locale``.
- ``g8-specH`` -- the sparse placeholder: the literal ``[REDACTED]`` a
  name-sparse document renders in place of each name becomes a neutral
  role phrase ("the applicant", "the deponent", ...) drawn per slot.
- ``g8-specAGH`` -- A, G and H together (Spec-C and Spec-D stay off).

So a profile document is the ``g8`` document with its name slots re-rendered
and furniture planted: every ground-truth value, every non-name slot and
every decoy are identical, which is what lets a profile be reported as a
PAIR against ``g8`` on the same 1,100 documents. Two profiles move a
VALUE by design and say so: Spec-A moves the name value (the form is the
experiment) and Spec-G moves the address value of a document whose drawn
locale differs from its base locale or is es_MX (the base stream still
consumes the ``g8`` draw, so every later value is untouched).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ._names import Form, Person, draw_form
from ._pii import generate_localized_address
from ._spans import (
    REDACTED_NAME_PLACEHOLDER,
    ContextClass,
    SpanBuilder,
    append_name_or_placeholder,
)

PROFILE_G8: Final[str] = "g8"
PROFILE_SPEC_C: Final[str] = "g8-specC"
PROFILE_SPEC_D: Final[str] = "g8-specD"
PROFILE_SPEC_CD: Final[str] = "g8-specCD"
PROFILE_SPEC_A: Final[str] = "g8-specA"
PROFILE_SPEC_G: Final[str] = "g8-specG"
PROFILE_SPEC_H: Final[str] = "g8-specH"
PROFILE_SPEC_AGH: Final[str] = "g8-specAGH"

PROFILES: Final[tuple[str, ...]] = (
    PROFILE_G8,
    PROFILE_SPEC_C,
    PROFILE_SPEC_D,
    PROFILE_SPEC_CD,
    PROFILE_SPEC_A,
    PROFILE_SPEC_G,
    PROFILE_SPEC_H,
    PROFILE_SPEC_AGH,
)

_SPEC_C_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_C, PROFILE_SPEC_CD})
_SPEC_D_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_D, PROFILE_SPEC_CD})
_SPEC_A_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_A, PROFILE_SPEC_AGH})
_SPEC_G_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_G, PROFILE_SPEC_AGH})
_SPEC_H_PROFILES: Final[frozenset[str]] = frozenset({PROFILE_SPEC_H, PROFILE_SPEC_AGH})

# The locale axis (Spec-G). The corpus as furnished ties the locale to the
# hispanic bucket (30 % es_MX / 30 % es_ES / 40 % en_US of that bucket, i.e.
# 6 % / 6 % / 88 % of all documents); Spec-G draws every document's locale
# at that same corpus-wide marginal so that only the bucket <-> locale
# coupling changes. One draw in [0, 50): 0-2 es_MX, 3-5 es_ES, the rest en_US.
LOCALE_EN_US: Final[str] = "en_US"
LOCALE_ES_MX: Final[str] = "es_MX"
LOCALE_ES_ES: Final[str] = "es_ES"
LOCALES: Final[tuple[str, ...]] = (LOCALE_EN_US, LOCALE_ES_MX, LOCALE_ES_ES)
_LOCALE_DRAW_MODULUS: Final[int] = 50
_LOCALE_ES_MX_MAX: Final[int] = 3
_LOCALE_ES_ES_MAX: Final[int] = 6

# The neutral role phrases Spec-H renders in place of ``[REDACTED]``: none is
# a name stop-list token, a Spec-D role noun, or a word of any family's shipped
# context-keyword phrase (``account holder`` / ``requester`` / ``insured``
# are, and would feed that family's scorer through the placeholder), so the
# swap tests the placeholder token alone. Drawn per slot from the profile
# stream; capitalised when the slot opens a line.
ROLE_WORDS: Final[tuple[str, ...]] = (
    "the applicant",
    "the deponent",
    "the claimant",
    "the undersigned",
    "the declarant",
    "the affiant",
    "the tenant",
    "the complainant",
)

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

# The pre-registered per-document rates (fixed before the first measurement): role nouns
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
    """The active profile and its own random stream (never the base stream).

    ``locale`` is the document's drawn locale under a Spec-G profile (set by
    the generator before the emitter runs, from the profile stream) and
    ``None`` otherwise.
    """

    name: str
    rng: random.Random
    locale: str | None = None

    def __post_init__(self) -> None:
        if self.name not in PROFILES:
            raise ValueError(f"unknown corpus profile {self.name!r}; choose from {PROFILES}")
        if self.spec_g and self.locale not in LOCALES:
            raise ValueError(f"profile {self.name!r} needs a drawn locale, got {self.locale!r}")
        if not self.spec_g and self.locale is not None:
            raise ValueError(f"profile {self.name!r} carries no locale axis")

    @property
    def spec_c(self) -> bool:
        return self.name in _SPEC_C_PROFILES

    @property
    def spec_d(self) -> bool:
        return self.name in _SPEC_D_PROFILES

    @property
    def spec_a(self) -> bool:
        return self.name in _SPEC_A_PROFILES

    @property
    def spec_g(self) -> bool:
        return self.name in _SPEC_G_PROFILES

    @property
    def spec_h(self) -> bool:
        return self.name in _SPEC_H_PROFILES


def is_spec_c(profile: str) -> bool:
    return profile in _SPEC_C_PROFILES


def is_spec_d(profile: str) -> bool:
    return profile in _SPEC_D_PROFILES


def is_spec_a(profile: str) -> bool:
    return profile in _SPEC_A_PROFILES


def is_spec_g(profile: str) -> bool:
    return profile in _SPEC_G_PROFILES


def is_spec_h(profile: str) -> bool:
    return profile in _SPEC_H_PROFILES


def draw_locale(rng: random.Random) -> str:
    """Draw a document locale at the corpus-wide marginal of ``g8`` (Spec-G)."""
    roll = rng.randrange(_LOCALE_DRAW_MODULUS)
    if roll < _LOCALE_ES_MX_MAX:
        return LOCALE_ES_MX
    if roll < _LOCALE_ES_ES_MAX:
        return LOCALE_ES_ES
    return LOCALE_EN_US


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


def _if_spec_a(profile: Profile | None) -> Profile | None:
    """The profile when Spec-A is active, else None (a typed narrowing)."""
    return profile if profile is not None and profile.spec_a else None


def _if_spec_g(profile: Profile | None) -> Profile | None:
    """The profile when Spec-G is active, else None (a typed narrowing)."""
    return profile if profile is not None and profile.spec_g else None


def _if_spec_h(profile: Profile | None) -> Profile | None:
    """The profile when Spec-H is active, else None (a typed narrowing)."""
    return profile if profile is not None and profile.spec_h else None


# ---------------------------------------------------------------------------
# Spec-A / Spec-H / Spec-G: the per-slot form, the sparse placeholder and
# the address value
# ---------------------------------------------------------------------------


def sparse_placeholder(sb: SpanBuilder, profile: Profile | None) -> str:
    """The plain text a name-sparse slot renders: ``[REDACTED]`` as furnished,
    or under Spec-H a role phrase drawn from the profile stream, capitalised
    when the slot opens a line of ``sb``."""
    active = _if_spec_h(profile)
    if active is None:
        return REDACTED_NAME_PLACEHOLDER
    phrase = ROLE_WORDS[active.rng.randrange(len(ROLE_WORDS))]
    return phrase[0].upper() + phrase[1:] if sb.at_line_start else phrase


def render_person(
    profile: Profile | None, person: Person, *, name_sparse: bool
) -> tuple[str, Form | None]:
    """The text a name slot renders for ``person`` and the form it records.

    Under ``g8`` (and every profile without Spec-A) the text is the full name
    and no form is recorded. Under Spec-A a form is drawn from the profile
    stream per rendered slot; a sparse slot draws nothing (it renders no
    name), so the profile stream position depends only on the slots that
    carry a span.
    """
    active = _if_spec_a(profile)
    if active is None or name_sparse:
        return person.full_name, None
    form = draw_form(active.rng)
    return person.render(form, active.rng), form


def render_address(rng: random.Random, profile: Profile | None, locale: str) -> str:
    """The address value of a document whose BASE locale is ``locale``.

    The base stream always draws the ``g8`` address in the base locale, so
    every later base value is untouched. Under Spec-G the document's drawn
    locale decides: when it equals the base locale and is not es_MX the
    ``g8`` bytes stand; otherwise the address is re-drawn from the profile
    stream in the drawn locale (es_MX with a five-digit codigo postal).
    """
    value = generate_localized_address(rng, locale)
    active = _if_spec_g(profile)
    if active is None:
        return value
    drawn = active.locale
    assert drawn is not None  # noqa: S101 -- Profile.__post_init__ guarantees it
    if drawn == locale and drawn != LOCALE_ES_MX:
        return value
    return generate_localized_address(active.rng, drawn, es_mx_five_digit_cp=True)


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
    person: Person,
    *,
    name_sparse: bool,
    shipped: NameContext,
    variants: tuple[NameContext, ...],
) -> None:
    """Render one person-name slot in its shipped context or a Spec-C variant.

    Under ``g8`` (``profile`` None or ``g8``) this appends exactly the shipped
    bytes and touches no stream. Under Spec-C the context is drawn from the
    profile stream; under Spec-A the name's form is; under Spec-H the sparse
    placeholder is. The name (or the placeholder) is appended with the
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
    text, form = render_person(profile, person, name_sparse=name_sparse)
    placeholder = sparse_placeholder(sb, profile) if name_sparse else REDACTED_NAME_PLACEHOLDER
    append_name_or_placeholder(
        sb,
        text,
        name_sparse=name_sparse,
        context_class=ctx.context_class,
        placeholder=placeholder,
        form=form,
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
