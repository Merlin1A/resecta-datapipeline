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
    generate_email_local,
    generate_invoice_number,
    generate_localized_address,
    generate_phone,
    generate_routing_number,
    generate_ssn,
)
from resecta_data.corpus._spans import (
    SpanBuilder,
    append_name_or_placeholder,
)


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

    text, spans = sb.finalize()
    return text, spans, []
