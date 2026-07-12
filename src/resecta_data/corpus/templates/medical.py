"""Medical-document template.

Emits discharge-summary-style text with patient demographics, MRN, DOB,
provider NPI, prescribing DEA, and (25% of the time) adversarial
NPI-shaped phone-number decoys.
"""

from __future__ import annotations

import random
from typing import Any

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_dea,
    generate_dob,
    generate_email_local,
    generate_localized_address,
    generate_mrn,
    generate_npi,
    generate_npi_shaped_phone,
    generate_phone,
    generate_ssn,
)
from resecta_data.corpus._spans import (
    SpanBuilder,
    append_name_or_placeholder,
)

_ADVERSARIAL_PROBABILITY = 0.25


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
    patient = sampler.sample(bucket)
    pcp = sampler.sample(bucket)
    prescriber = sampler.sample(bucket)

    dob = generate_dob(rng)
    mrn = generate_mrn(rng)
    address = generate_localized_address(rng, locale)
    npi = generate_npi(rng)
    dea = generate_dea(rng)
    ssn = generate_ssn(rng)
    phone = generate_phone(rng)
    # Name-sparse docs must carry no person-name text anywhere, so the
    # email local switches to institution words.
    email_first = "records" if name_sparse else patient.given_name
    email_last = "office" if name_sparse else patient.surname
    email = generate_email_local(rng, email_first, email_last)

    include_adversarial = rng.random() < _ADVERSARIAL_PROBABILITY
    tags: list[str] = []

    sb = SpanBuilder()
    sb.append("Community Medical Center — Discharge Summary\n\n")

    sb.append("Patient: ")
    append_name_or_placeholder(sb, patient.full_name, name_sparse=name_sparse)
    sb.append("\nDOB: ")
    sb.append_pii(dob, "dob")
    sb.append("\nMRN: ")
    sb.append_pii(mrn, "mrn")
    sb.append("\nAddress: ")
    sb.append_pii(address, "address")
    sb.append("\nPhone: ")
    sb.append_pii(phone, "phone")
    sb.append("\nEmail: ")
    sb.append_pii(email, "email")
    sb.append("\nSSN on file: ")
    sb.append_pii(ssn, "ssn")
    sb.append("\n\n")

    sb.append("Primary care: Dr. ")
    append_name_or_placeholder(sb, pcp.full_name, name_sparse=name_sparse)
    sb.append(", NPI ")
    sb.append_pii(npi, "npi")
    sb.append(".\n")
    sb.append("Prescribing: Dr. ")
    append_name_or_placeholder(sb, prescriber.full_name, name_sparse=name_sparse)
    sb.append(", DEA ")
    sb.append_pii(dea, "dea")
    sb.append(".\n\n")

    # Clinical-furniture lines after the diagnosis (S4 2026-06-11, finding
    # fix direction 2): real discharge summaries carry vitals, a diagnosis
    # code, and a medication line, which exercise all four medical
    # structural bonuses and surface more of the class vocabulary —
    # 'vitals', 'medication', 'refill'. Static text: no rng draws, no PII,
    # no person names, and the values match no detector shape (not
    # SSN/MRN/NPI/DEA/account/routing/date), so detector dumps stay clean.
    sb.append(
        "Diagnosis: stable post-procedure. Followup at 2 weeks.\n"
        "Discharge vitals: BP 122/78, pulse 71, afebrile.\n"
        "Primary diagnosis code: I10.\n"
        "Medication: lisinopril 10 mg daily; refill at next visit.\n"
    )

    if include_adversarial:
        decoy = generate_npi_shaped_phone(rng)
        sb.append("Callback line (patient services): ")
        sb.append_pii(
            decoy,
            "npi",
            adversarial=True,
            expected_outcome="suppress",
        )
        sb.append(".\n")
        tags.append("npi_shaped_phone_number")

    text, spans = sb.finalize()
    return text, spans, tags
