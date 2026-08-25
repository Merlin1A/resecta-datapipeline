"""FOIA / public-records template.

Emits request/response letter text referencing a requester, a subject of
interest (name + DOB + SSN), and a FOIA exemption citation.
"""

from __future__ import annotations

import random
from typing import Any

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_dob,
    generate_email_local,
    generate_license_plate,
    generate_localized_address,
    generate_passport,
    generate_passport_label,
    generate_passport_shaped_no_issuer,
    generate_phone,
    generate_plate_label,
    generate_request_id,
    generate_ssn,
)
from resecta_data.corpus._spans import (
    SpanBuilder,
    append_name_or_placeholder,
)

# 1.2 T1.1: the no-issuer passport decoy rides its own unconditional draw.
_PASSPORT_DECOY_PROBABILITY = 0.25


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
    requester = sampler.sample(bucket)
    subject = sampler.sample(bucket)

    request_id = generate_request_id(rng)
    requester_address = generate_localized_address(rng, locale)
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

    sb.append("From: ")
    append_name_or_placeholder(sb, requester.full_name, name_sparse=name_sparse)
    sb.append("\n")
    sb.append_pii(requester_address, "address")
    sb.append("\n")
    sb.append_pii(phone, "phone")
    sb.append(" | ")
    sb.append_pii(email, "email")
    sb.append("\n\n")

    sb.append("Re: Records pertaining to ")
    append_name_or_placeholder(sb, subject.full_name, name_sparse=name_sparse)
    sb.append(" (DOB ")
    sb.append_pii(subject_dob, "dob")
    sb.append(", SSN ")
    sb.append_pii(subject_ssn, "ssn")
    sb.append(").\n\n")

    sb.append(
        "Pursuant to the Freedom of Information Act, I request records "
        "concerning the above-named individual. Redactions under exemption "
        "(b)(6) are designed to protect personal privacy.\n\n"
    )

    sb.append("Sincerely,\n")
    append_name_or_placeholder(sb, requester.full_name, name_sparse=name_sparse)
    sb.append("\n")

    tags: list[str] = []
    _append_passport_and_plate(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags


def _append_passport_and_plate(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """1.2 T1.1 (C12-25): passport + a keyword-STARVED licensePlate + the decoy.

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
