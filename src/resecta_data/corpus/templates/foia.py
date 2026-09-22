"""FOIA / public-records template.

Emits request/response letter text referencing a requester, a subject of
interest (name + DOB + SSN), and a FOIA exemption citation.

Generator profiles: under Spec-C the ``From:``, ``Re:`` and
closing-line slots render in their shipped context or one of four
variants; under Spec-D the letter gains a name-free salutation, 1-3 label
lines after the body, and its ``Sincerely,`` cue is recorded as closing
furniture.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_dob,
    generate_email_local,
    generate_license_plate,
    generate_passport,
    generate_passport_label,
    generate_passport_shaped_no_issuer,
    generate_phone,
    generate_plate_label,
    generate_request_id,
    generate_ssn,
)
from resecta_data.corpus._profiles import (
    FURNITURE_CLOSING,
    NameContext,
    Profile,
    append_cue,
    header_after,
    plant_labels,
    plant_salutation,
    render_address,
    render_name_slot,
)
from resecta_data.corpus._spans import SpanBuilder

# 1.2 T1.1: the no-issuer passport decoy rides its own unconditional draw.
_PASSPORT_DECOY_PROBABILITY = 0.25

_FROM_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("title_label", "Requester Name: "),
    NameContext("table_cell", "| From | ", " |"),
    NameContext("header", "", header_after()),
    NameContext("body_prose", "This request is submitted by "),
)
_SUBJECT_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("subject_line", "Subject: "),
    NameContext("table_cell", "| Subject | ", " |"),
    NameContext("body_prose", "I request all records concerning "),
    NameContext("title_label", "Subject Name: "),
)
_CLOSING_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("closing_line", "/s/ "),
    NameContext("closing_line", "", "\nRequester"),
    NameContext("table_cell", "| Signature | ", " |"),
    NameContext("header", "", header_after()),
)

_SALUTATION: Final[str] = "Dear Records Officer,"


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
    # name_sparse; only what gets emitted differs.
    requester = sampler.sample(bucket)
    subject = sampler.sample(bucket)

    request_id = generate_request_id(rng)
    requester_address = render_address(rng, profile, locale)
    phone = generate_phone(rng)
    # Name-sparse docs must carry no person-name text anywhere, so the
    # email local switches to institution words.
    email_first = "foia" if name_sparse else requester.given_name
    email_last = "office" if name_sparse else requester.surname
    email = generate_email_local(rng, email_first, email_last)
    subject_dob = generate_dob(rng)
    subject_ssn = generate_ssn(rng)

    sb = SpanBuilder()
    sb.append("FREEDOM OF INFORMATION ACT REQUEST\n")
    sb.append(f"Request No. {request_id}\n\n")

    render_name_slot(
        sb,
        profile,
        requester,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "From: "),
        variants=_FROM_VARIANTS,
    )
    sb.append("\n")
    sb.append_pii(requester_address, "address")
    sb.append("\n")
    sb.append_pii(phone, "phone")
    sb.append(" | ")
    sb.append_pii(email, "email")
    sb.append("\n\n")

    render_name_slot(
        sb,
        profile,
        subject,
        name_sparse=name_sparse,
        shipped=NameContext("subject_line", "Re: Records pertaining to "),
        variants=_SUBJECT_VARIANTS,
    )
    sb.append(" (DOB ")
    sb.append_pii(subject_dob, "dob")
    sb.append(", SSN ")
    sb.append_pii(subject_ssn, "ssn")
    sb.append(").\n\n")

    # Spec-D: every letter carries a salutation (this one has none as shipped).
    plant_salutation(sb, profile, _SALUTATION)

    sb.append(
        "Pursuant to the Freedom of Information Act, I request records "
        "concerning the above-named individual. Redactions under exemption "
        "(b)(6) are designed to protect personal privacy.\n\n"
    )

    # Spec-D: 1-3 registration / plate label lines, well clear of the
    # keyword-starved plate span appended at the end.
    if plant_labels(sb, profile):
        sb.append("\n")

    append_cue(sb, profile, "Sincerely,", FURNITURE_CLOSING)
    sb.append("\n")
    render_name_slot(
        sb,
        profile,
        requester,
        name_sparse=name_sparse,
        shipped=NameContext("closing_line"),
        variants=_CLOSING_VARIANTS,
    )
    sb.append("\n")

    tags: list[str] = []
    _append_passport_and_plate(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags, sb.furniture_sorted()


def _append_passport_and_plate(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """The 17-family extension: passport + a keyword-STARVED licensePlate + the decoy.

    Append-only after the last pre-existing draw (see court.py for the
    byte-preservation rule); every draw is unconditional.
    """
    passport_label = generate_passport_label(rng)
    passport = generate_passport(rng)
    plate_label = generate_plate_label(rng, keyword_free=True)
    plate = generate_license_plate(rng)
    include_passport_decoy = rng.random() < _PASSPORT_DECOY_PROBABILITY
    passport_decoy = generate_passport_shaped_no_issuer(rng)

    sb.append("\nScope: travel records associated with ")
    sb.append(passport_label)
    sb.append_pii(passport, "passport")
    sb.append(", and the incident report referencing ")
    sb.append(plate_label)
    # No plate context keyword within +-5 tokens: the engine's own profile
    # scores this surface at its 0.55 base, under the 0.65 balanced cutoff,
    # so it is designed-marginal -> should.
    sb.append_pii(plate, "licensePlate", tier="should")
    sb.append(".\n")
    if include_passport_decoy:
        # 1L+6D: the passport shape no issuer row accepts (gazetteer suppresses).
        sb.append("Prior ")
        sb.append(passport_label)
        sb.append_pii(
            passport_decoy,
            "passport",
            adversarial=True,
            expected_outcome="suppress",
        )
        sb.append(" (expired, for cross-reference).\n")
        tags.append("passport_shape_no_issuer")
