"""Court-document template.

Emits synthetic pleading-style text with plaintiff/defendant parties,
counsel, one SSN, one DOB, and (30% of the time) adversarial decoys:
SSN-shaped case numbers, DOB-shaped filing dates, ALL-CAPS header names.
"""

from __future__ import annotations

import random
from typing import Any

from resecta_data.corpus._names import NameSampler, Person
from resecta_data.corpus._pii import (
    generate_business_registration,
    generate_case_number,
    generate_dl_label,
    generate_dob,
    generate_drivers_license,
    generate_email_local,
    generate_filing_date,
    generate_license_plate,
    generate_localized_address,
    generate_phone,
    generate_plate_label,
    generate_ssn,
    generate_ssn_shaped_decoy,
)
from resecta_data.corpus._spans import (
    REDACTED_NAME_PLACEHOLDER,
    SpanBuilder,
    append_name_or_placeholder,
)

_ADVERSARIAL_PROBABILITY = 0.30
_ALL_CAPS_PROBABILITY = 0.20
# 1.2 T1.1: the plate-label decoy rides its own unconditional draw.
_PLATE_DECOY_PROBABILITY = 0.30


def _append_caption(
    sb: SpanBuilder,
    rng: random.Random,
    plaintiff: Person,
    defendant: Person,
    tags: list[str],
    *,
    name_sparse: bool,
) -> None:
    # Draw unconditionally so the rng stream is independent of name_sparse.
    all_caps = rng.random() < _ALL_CAPS_PROBABILITY
    if name_sparse:
        sb.append(f"{REDACTED_NAME_PLACEHOLDER} v. {REDACTED_NAME_PLACEHOLDER}")
    elif all_caps:
        sb.append_pii(plaintiff.all_caps, "name", adversarial=True)
        sb.append(" v. ")
        sb.append_pii(defendant.all_caps, "name", adversarial=True)
        tags.append("all_caps_header_name")
    else:
        sb.append_pii(plaintiff.full_name, "name")
        sb.append(" v. ")
        sb.append_pii(defendant.full_name, "name")


def _append_filing(
    sb: SpanBuilder,
    plaintiff: Person,
    defendant: Person,
    filing_date: str,
    include_adversarial: bool,
    tags: list[str],
    *,
    name_sparse: bool,
) -> None:
    sb.append("PLAINTIFF: ")
    append_name_or_placeholder(sb, plaintiff.full_name, name_sparse=name_sparse)
    sb.append("\nDEFENDANT: ")
    append_name_or_placeholder(sb, defendant.full_name, name_sparse=name_sparse)
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
) -> tuple[str, list[dict[str, Any]], list[str]]:
    # All rng draws are unconditional so the stream is independent of
    # name_sparse; only what gets emitted differs.
    plaintiff = sampler.sample(bucket)
    defendant = sampler.sample(bucket)
    counsel = sampler.sample(bucket)
    witness = sampler.sample(bucket)

    case_no = generate_case_number(rng)
    filing_date = generate_filing_date(rng)
    dob = generate_dob(rng)
    plaintiff_ssn = generate_ssn(rng)
    defendant_address = generate_localized_address(rng, locale)
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
    _append_caption(sb, rng, plaintiff, defendant, tags, name_sparse=name_sparse)
    sb.append("\n\n")

    _append_filing(
        sb,
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

    if include_adversarial:
        _append_decoy(sb, rng, tags)

    sb.append("Counsel of record: ")
    append_name_or_placeholder(sb, counsel.full_name, name_sparse=name_sparse)
    sb.append(" (")
    sb.append_pii(phone, "phone")
    sb.append(", ")
    sb.append_pii(email, "email")
    sb.append(").\n")

    sb.append("Witness ")
    append_name_or_placeholder(sb, witness.full_name, name_sparse=name_sparse)
    sb.append(", DOB ")
    sb.append_pii(dob, "dob")
    sb.append(", testified under oath.\n")

    _append_vehicle_and_license(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags


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
