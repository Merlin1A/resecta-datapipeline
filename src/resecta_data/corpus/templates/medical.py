"""Medical-document template.

Emits discharge-summary-style text with patient demographics, MRN, DOB,
provider NPI, prescribing DEA, and (25% of the time) adversarial
NPI-shaped phone-number decoys.

Generator profiles (1.2 C12-95): under Spec-C the ``Patient:`` slot and the
two ``Dr.`` title slots render in their shipped context or one of four
variants; under Spec-D the document plants 4-8 role-noun sentences
(Patient / Provider / Dr.) after the clinical-furniture lines.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_credit_card,
    generate_dea,
    generate_dl_label,
    generate_dl_shaped_no_jurisdiction,
    generate_dob,
    generate_drivers_license,
    generate_email_local,
    generate_mrn,
    generate_npi,
    generate_npi_shaped_phone,
    generate_passport,
    generate_passport_label,
    generate_passport_shaped_no_issuer,
    generate_phone,
    generate_ssn,
)
from resecta_data.corpus._profiles import (
    MEDICAL_ROLE_NOUN_SENTENCES,
    MEDICAL_ROLE_NOUNS_PER_DOC,
    NameContext,
    Profile,
    header_after,
    plant_role_nouns,
    render_address,
    render_name_slot,
)
from resecta_data.corpus._spans import SpanBuilder

_ADVERSARIAL_PROBABILITY = 0.25
# 1.2 T1.1: the identity document verified at intake is a passport half the
# time and a driver's license otherwise.
_PASSPORT_ID_PROBABILITY = 0.50

_PATIENT_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("title_label", "Patient Name: "),
    NameContext("table_cell", "| Patient | ", " |"),
    NameContext("header", "", header_after()),
    NameContext("body_prose", "The patient, ", ", was admitted for observation."),
)
_PCP_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("role_label", "Primary care physician: "),
    NameContext("table_cell", "| Primary care | ", " |"),
    NameContext("body_prose", "Care was coordinated by "),
    NameContext("title_label", "Provider Name: "),
)
_PRESCRIBER_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("role_label", "Prescriber: "),
    NameContext("table_cell", "| Prescriber | ", " |"),
    NameContext("body_prose", "Medications were ordered by "),
    NameContext("title_label", "Prescriber Name: "),
)


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
    patient = sampler.sample(bucket)
    pcp = sampler.sample(bucket)
    prescriber = sampler.sample(bucket)

    dob = generate_dob(rng)
    mrn = generate_mrn(rng)
    address = render_address(rng, profile, locale)
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

    render_name_slot(
        sb,
        profile,
        patient,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "Patient: "),
        variants=_PATIENT_VARIANTS,
    )
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

    render_name_slot(
        sb,
        profile,
        pcp,
        name_sparse=name_sparse,
        shipped=NameContext("title_label", "Primary care: Dr. "),
        variants=_PCP_VARIANTS,
    )
    sb.append(", NPI ")
    sb.append_pii(npi, "npi")
    sb.append(".\n")
    render_name_slot(
        sb,
        profile,
        prescriber,
        name_sparse=name_sparse,
        shipped=NameContext("title_label", "Prescribing: Dr. "),
        variants=_PRESCRIBER_VARIANTS,
    )
    sb.append(", DEA ")
    sb.append_pii(dea, "dea")
    sb.append(".\n\n")

    # Clinical-furniture lines after the diagnosis (2026-06-11): real
    # discharge summaries carry vitals, a diagnosis
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

    # Spec-D: the Patient / Provider / Dr. role-noun lines a real summary
    # carries, planted after the clinical furniture and before the decoy.
    plant_role_nouns(sb, profile, MEDICAL_ROLE_NOUN_SENTENCES, MEDICAL_ROLE_NOUNS_PER_DOC)

    if include_adversarial:
        _append_callback_decoy(sb, rng, tags)

    _append_identity_and_card(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags, sb.furniture_sorted()


def _append_callback_decoy(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """The NPI-shaped phone-number decoy (drawn only when emitted, as before)."""
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


def _append_identity_and_card(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """1.2 T1.1 (C12-25): driversLicense / passport at intake + creditCard.

    Append-only after the last pre-existing draw (see court.py for the
    byte-preservation rule); every draw is unconditional.
    """
    id_is_passport = rng.random() < _PASSPORT_ID_PROBABILITY
    dl_label = generate_dl_label(rng)
    dl = generate_drivers_license(rng)
    passport_label = generate_passport_label(rng)
    passport = generate_passport(rng)
    card = generate_credit_card(rng)
    include_id_decoy = rng.random() < _ADVERSARIAL_PROBABILITY
    dl_decoy = generate_dl_shaped_no_jurisdiction(rng)
    passport_decoy = generate_passport_shaped_no_issuer(rng)

    sb.append("\nIdentity verified at intake -- ")
    if id_is_passport:
        sb.append(passport_label)
        sb.append_pii(passport, "passport")
    else:
        sb.append(dl_label)
        sb.append_pii(dl, "driversLicense")
    sb.append(".\nCopay card on file: ")
    sb.append_pii(card, "creditCard")
    sb.append(".\n")
    if include_id_decoy:
        # The packet's must-not-fire classes: a passport shape no issuer row
        # accepts (1L+6D) / a DL shape longer than every jurisdiction row
        # (1L+14D) -- the pattern gazetteers suppress both.
        if id_is_passport:
            sb.append("Prior ")
            sb.append(passport_label)
            sb.append_pii(
                passport_decoy,
                "passport",
                adversarial=True,
                expected_outcome="suppress",
            )
            tags.append("passport_shape_no_issuer")
        else:
            sb.append("Secondary ID on file -- ")
            sb.append(dl_label)
            sb.append_pii(
                dl_decoy,
                "driversLicense",
                adversarial=True,
                expected_outcome="suppress",
            )
            tags.append("dl_shape_no_jurisdiction")
        sb.append(" (superseded).\n")
