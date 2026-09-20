"""Court-document template.

Emits synthetic pleading-style text with plaintiff/defendant parties,
counsel, one SSN, one DOB, and (30% of the time) adversarial decoys:
SSN-shaped case numbers, DOB-shaped filing dates, ALL-CAPS header names.

Generator profiles (1.2 C12-95): under Spec-C every name slot -- the caption
pair, ``PLAINTIFF:``, ``DEFENDANT:``, ``Counsel of record:``, ``Witness`` --
is rendered in its shipped context or one of four variants drawn from the
profile stream; under Spec-D the document plants 6-12 role-noun sentences
after the allegations and 1-3 label lines at its end, recorded as furniture.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.corpus._names import FORM_ALL_CAPS, FORM_FULL, NameSampler, Person
from resecta_data.corpus._pii import (
    generate_business_registration,
    generate_case_number,
    generate_dl_label,
    generate_dob,
    generate_drivers_license,
    generate_email_local,
    generate_filing_date,
    generate_license_plate,
    generate_phone,
    generate_plate_label,
    generate_ssn,
    generate_ssn_shaped_decoy,
)
from resecta_data.corpus._profiles import (
    COURT_ROLE_NOUN_SENTENCES,
    COURT_ROLE_NOUNS_PER_DOC,
    NameContext,
    Profile,
    header_after,
    honorific,
    plant_labels,
    plant_role_nouns,
    render_address,
    render_name_slot,
    sparse_placeholder,
)
from resecta_data.corpus._spans import ContextClass, SpanBuilder

_ADVERSARIAL_PROBABILITY = 0.30
_ALL_CAPS_PROBABILITY = 0.20
# 1.2 T1.1: the plate-label decoy rides its own unconditional draw.
_PLATE_DECOY_PROBABILITY = 0.30

# Spec-C caption shapes: the shipped one-line caption, then a multi-line
# caption (still the caption classes), a table, a running header and body
# prose. Drawn once per document for the pair.
_CAPTION_SHAPE_COUNT: Final[int] = 5

# Spec-C contexts per name slot (the shipped context first in each call).
_PLAINTIFF_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("title_label", "Plaintiff Name: "),
    NameContext("table_cell", "| Plaintiff | ", " |"),
    NameContext("role_label", "Attn: "),
    NameContext("body_prose", "In this matter the plaintiff ", " seeks relief."),
)
_DEFENDANT_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("title_label", "Defendant Name: "),
    NameContext("table_cell", "| Defendant | ", " |"),
    NameContext("role_label", "c/o "),
    NameContext("header", "", header_after()),
)
_COUNSEL_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("title_label", "Attorney: "),
    NameContext("title_label", "", ", Esq."),
    NameContext("table_cell", "| Counsel | ", " |"),
    NameContext("closing_line", "Respectfully submitted,\n"),
)
_WITNESS_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("title_label", "Witness Name: "),
    NameContext("body_prose", "At the hearing, "),
    NameContext("table_cell", "| Witness | ", " |"),
    NameContext("title_label", honorific),
)


def _party(
    sb: SpanBuilder,
    profile: Profile | None,
    text: str,
    context_class: ContextClass,
    *,
    adversarial: bool,
    name_sparse: bool,
    form: str | None,
) -> None:
    """A caption party: the name span (its surface form recorded under Spec-A),
    or the sparse placeholder (a role phrase under Spec-H)."""
    if name_sparse:
        sb.append(sparse_placeholder(sb, profile))
    else:
        sb.append_pii(text, "name", adversarial=adversarial, context_class=context_class, form=form)


def _append_caption(
    sb: SpanBuilder,
    rng: random.Random,
    profile: Profile | None,
    plaintiff: Person,
    defendant: Person,
    tags: list[str],
    *,
    name_sparse: bool,
) -> None:
    # Draw unconditionally so the rng stream is independent of name_sparse.
    all_caps = rng.random() < _ALL_CAPS_PROBABILITY
    left = plaintiff.all_caps if all_caps else plaintiff.full_name
    right = defendant.all_caps if all_caps else defendant.full_name
    if all_caps and not name_sparse:
        tags.append("all_caps_header_name")
    shape = 0
    if profile is not None and profile.spec_c:
        shape = profile.rng.randrange(_CAPTION_SHAPE_COUNT)
    # The caption is not a Spec-A slot (its ALL-CAPS is the adversarial
    # class); under Spec-A its spans record the surface they carry so the
    # per-span form join stays total.
    form: str | None = None
    if profile is not None and profile.spec_a:
        form = FORM_ALL_CAPS if all_caps else FORM_FULL
    kw: dict[str, Any] = {"adversarial": all_caps, "name_sparse": name_sparse, "form": form}
    if shape == 0:
        # The shipped caption: "X v. Y".
        _party(sb, profile, left, "caption_left", **kw)
        sb.append(" v. ")
        _party(sb, profile, right, "caption_right", **kw)
    elif shape == 1:
        # The multi-line caption of a real pleading; still the caption classes.
        _party(sb, profile, left, "caption_left", **kw)
        sb.append(",\n        Plaintiff,\n    v.\n")
        _party(sb, profile, right, "caption_right", **kw)
        sb.append(",\n        Defendant.")
    elif shape == 2:  # noqa: PLR2004 -- the table shape
        sb.append("| Plaintiff | ")
        _party(sb, profile, left, "table_cell", **kw)
        sb.append(" |\n| Defendant | ")
        _party(sb, profile, right, "table_cell", **kw)
        sb.append(" |")
    elif shape == 3:  # noqa: PLR2004 -- the running-header shape
        _party(sb, profile, left, "header", **kw)
        sb.append(" v. ")
        _party(sb, profile, right, "header", **kw)
        pages = profile.rng.randint(2, 6) if profile is not None else 2
        page = profile.rng.randint(2, pages) if profile is not None else 2
        sb.append(f" — Page {page} of {pages}")
    else:
        sb.append("This action is brought by ")
        _party(sb, profile, left, "body_prose", **kw)
        sb.append(" against ")
        _party(sb, profile, right, "body_prose", **kw)
        sb.append(".")


def _append_filing(
    sb: SpanBuilder,
    profile: Profile | None,
    plaintiff: Person,
    defendant: Person,
    filing_date: str,
    include_adversarial: bool,
    tags: list[str],
    *,
    name_sparse: bool,
) -> None:
    render_name_slot(
        sb,
        profile,
        plaintiff,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "PLAINTIFF: "),
        variants=_PLAINTIFF_VARIANTS,
    )
    sb.append("\n")
    render_name_slot(
        sb,
        profile,
        defendant,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "DEFENDANT: "),
        variants=_DEFENDANT_VARIANTS,
    )
    sb.append("\nFiled: ")
    if include_adversarial:
        sb.append_pii(
            filing_date,
            "dob",
            adversarial=True,
            expected_outcome="suppress",
        )
        tags.append("dob_shaped_filing_date")
    else:
        sb.append(filing_date)


def _append_decoy(
    sb: SpanBuilder,
    rng: random.Random,
    tags: list[str],
) -> None:
    decoy = generate_ssn_shaped_decoy(rng)
    sb.append("Associated matter, Case No. ")
    sb.append_pii(
        decoy,
        "ssn",
        adversarial=True,
        expected_outcome="suppress",
    )
    sb.append(".\n\n")
    tags.append("ssn_shaped_case_number")


def emit(
    rng: random.Random,
    sampler: NameSampler,
    bucket: str,
    *,
    locale: str = "en_US",
    name_sparse: bool = False,
    profile: Profile | None = None,
) -> tuple[str, list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    # All rng draws are unconditional so the stream is independent of
    # name_sparse; only what gets emitted differs. The profile stream (if
    # any) is drawn from separately and never touches this one.
    plaintiff = sampler.sample(bucket)
    defendant = sampler.sample(bucket)
    counsel = sampler.sample(bucket)
    witness = sampler.sample(bucket)

    case_no = generate_case_number(rng)
    filing_date = generate_filing_date(rng)
    dob = generate_dob(rng)
    plaintiff_ssn = generate_ssn(rng)
    defendant_address = render_address(rng, profile, locale)
    phone = generate_phone(rng)
    # Name-sparse docs must carry no person-name text anywhere, so the
    # email local switches to institution words.
    email_first = "docket" if name_sparse else counsel.given_name
    email_last = "clerk" if name_sparse else counsel.surname
    email = generate_email_local(rng, email_first, email_last)

    include_adversarial = rng.random() < _ADVERSARIAL_PROBABILITY
    tags: list[str] = []

    sb = SpanBuilder()
    sb.append(f"IN THE DISTRICT COURT\nCase No. {case_no}\n\n")
    _append_caption(sb, rng, profile, plaintiff, defendant, tags, name_sparse=name_sparse)
    sb.append("\n\n")

    _append_filing(
        sb,
        profile,
        plaintiff,
        defendant,
        filing_date,
        include_adversarial,
        tags,
        name_sparse=name_sparse,
    )
    sb.append("\n\n")

    sb.append("The plaintiff, residing at ")
    sb.append_pii(defendant_address, "address")
    sb.append(", alleges defendant (SSN ")
    sb.append_pii(plaintiff_ssn, "ssn")
    sb.append(") failed to perform as contracted.\n\n")

    # Spec-D: the role-noun boilerplate a real pleading carries, planted
    # between the allegations and the decoy / counsel block (no structured
    # family's window reaches here).
    if plant_role_nouns(sb, profile, COURT_ROLE_NOUN_SENTENCES, COURT_ROLE_NOUNS_PER_DOC):
        sb.append("\n")

    if include_adversarial:
        _append_decoy(sb, rng, tags)

    render_name_slot(
        sb,
        profile,
        counsel,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "Counsel of record: "),
        variants=_COUNSEL_VARIANTS,
    )
    sb.append(" (")
    sb.append_pii(phone, "phone")
    sb.append(", ")
    sb.append_pii(email, "email")
    sb.append(").\n")

    render_name_slot(
        sb,
        profile,
        witness,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "Witness "),
        variants=_WITNESS_VARIANTS,
    )
    sb.append(", DOB ")
    sb.append_pii(dob, "dob")
    sb.append(", testified under oath.\n")

    _append_vehicle_and_license(sb, rng, tags)

    # Spec-D: 1-3 registration / plate label lines at the very end, after the
    # plate decoy's own sentence (the M12-22 plate-label leg of the spec).
    plant_labels(sb, profile)

    text, spans = sb.finalize()
    return text, spans, tags, sb.furniture_sorted()


def _append_vehicle_and_license(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """1.2 T1.1 (C12-25): driversLicense + licensePlate + the plate-label decoy.

    Every draw here happens AFTER the last pre-existing draw and the text is
    appended at the END of the document, so every pre-existing span, offset
    and rng draw is byte-preserved. Draws are unconditional (the decoy roll
    is taken even when nothing is emitted) so the stream stays a pure
    function of the seed.
    """
    plate_label = generate_plate_label(rng)
    plate = generate_license_plate(rng)
    dl_label = generate_dl_label(rng)
    dl = generate_drivers_license(rng)
    include_plate_decoy = rng.random() < _PLATE_DECOY_PROBABILITY
    registration = generate_business_registration(rng)

    # "vehicle" sits inside the plate profile's +-5-token window -> fed (must).
    sb.append("\nThe vehicle bearing ")
    sb.append(plate_label)
    sb.append_pii(plate, "licensePlate")
    sb.append(" is registered to the defendant as owner.\n")
    sb.append("Defendant identification on file -- ")
    sb.append(dl_label)
    sb.append_pii(dl, "driversLicense")
    sb.append(".\n")
    if include_plate_decoy:
        # A corporate registration id behind the "Registration #" label the
        # plate regex accepts: not PII, so a fire is a label collision.
        sb.append("Plaintiff is a corporation, Business Registration # ")
        sb.append_pii(
            registration,
            "licensePlate",
            adversarial=True,
            expected_outcome="suppress",
        )
        sb.append(" on file with the secretary of state.\n")
        tags.append("business_registration_plate_label")
