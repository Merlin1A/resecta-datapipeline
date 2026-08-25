"""Financial-document template (invoice sub-shape).

Emits invoice/statement-style text with account numbers, routing, a tax
ID (SSN), billing name/address, and no adversarial decoys by default.
The W-2 shaped sibling lives in :mod:`financial_tax`.
"""

from __future__ import annotations

import random
from typing import Any

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_account_number,
    generate_credit_card,
    generate_email_local,
    generate_invoice_number,
    generate_itin,
    generate_localized_address,
    generate_luhn_failed_card,
    generate_phone,
    generate_routing_number,
    generate_ssn,
)
from resecta_data.corpus._spans import (
    SpanBuilder,
    append_name_or_placeholder,
)

# 1.2 T1.1: the Luhn-broken card decoy rides its own unconditional draw.
_CARD_DECOY_PROBABILITY = 0.25


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
    customer = sampler.sample(bucket)
    ap_contact = sampler.sample(bucket)

    invoice = generate_invoice_number(rng)
    account = generate_account_number(rng)
    routing = generate_routing_number(rng)
    address = generate_localized_address(rng, locale)
    phone = generate_phone(rng)
    # Name-sparse docs must carry no person-name text anywhere (a
    # detector hit on an email local would count as an unmatched name
    # candidate), so the email switches to institution words.
    email_first = "accounts" if name_sparse else ap_contact.given_name
    email_last = "payable" if name_sparse else ap_contact.surname
    email = generate_email_local(rng, email_first, email_last)
    tax_ssn = generate_ssn(rng)

    subtotal = rng.randint(100, 9999)
    tax = subtotal // 10
    total = subtotal + tax

    sb = SpanBuilder()
    sb.append("Acme Services LLC — Invoice ")
    sb.append(invoice)
    sb.append("\n\n")

    sb.append("Bill to: ")
    append_name_or_placeholder(sb, customer.full_name, name_sparse=name_sparse)
    sb.append("\n")
    sb.append_pii(address, "address")
    sb.append("\n\n")

    sb.append("Account Number: ")
    sb.append_pii(account, "account")
    sb.append("\n")
    sb.append("Routing: ")
    sb.append_pii(routing, "routingNumber")
    sb.append("\n\n")

    sb.append(f"Subtotal: ${subtotal}.00\n")
    sb.append(f"Tax:      ${tax}.00\n")
    sb.append(f"Total:    ${total}.00\n\n")

    sb.append("Tax ID (SSN): ")
    sb.append_pii(tax_ssn, "ssn")
    sb.append("\n")

    sb.append("AP Contact: ")
    append_name_or_placeholder(sb, ap_contact.full_name, name_sparse=name_sparse)
    sb.append(" — ")
    sb.append_pii(phone, "phone")
    sb.append(" / ")
    sb.append_pii(email, "email")
    sb.append("\n")

    tags: list[str] = []
    _append_itin_and_card(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags


def _append_itin_and_card(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """1.2 T1.1 (C12-25): itin + creditCard + the Luhn-broken card decoy.

    Append-only after the last pre-existing draw (see court.py for the
    byte-preservation rule); every draw is unconditional.
    """
    itin = generate_itin(rng)
    card = generate_credit_card(rng)
    include_card_decoy = rng.random() < _CARD_DECOY_PROBABILITY
    card_decoy = generate_luhn_failed_card(rng)

    sb.append("\nVendor ITIN (Form W-9 on file): ")
    sb.append_pii(itin, "itin")
    sb.append("\nPaid by card: ")
    sb.append_pii(card, "creditCard")
    sb.append("\n")
    if include_card_decoy:
        # Accepted IIN, 16 digits, Luhn check broken: the triple gate rejects it.
        sb.append("Card declined at checkout, number as keyed: ")
        sb.append_pii(
            card_decoy,
            "creditCard",
            adversarial=True,
            expected_outcome="suppress",
        )
        sb.append("\n")
        tags.append("luhn_failed_card_number")
