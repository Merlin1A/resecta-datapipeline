"""Generic-letter template.

Emits a plain correspondence with sender name/address/phone/email, a
recipient name/address block, and a single account reference. Used as
the neutral fallback class.
"""

from __future__ import annotations

import random
from typing import Any

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_account_number,
    generate_credit_card,
    generate_email_local,
    generate_itin,
    generate_itin_yy_out_of_range,
    generate_localized_address,
    generate_phone,
)
from resecta_data.corpus._spans import (
    SpanBuilder,
    append_name_or_placeholder,
)

# 1.2 T1.1: half the letters label their ITIN (fed -> must); the other half
# draw it keyword-starved (should). The YY-range decoy rides its own draw.
_ITIN_LABELED_PROBABILITY = 0.50
_ITIN_DECOY_PROBABILITY = 0.25


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
    sender = sampler.sample(bucket)
    recipient = sampler.sample(bucket)

    sender_address = generate_localized_address(rng, locale)
    sender_phone = generate_phone(rng)
    # Name-sparse docs must carry no person-name text anywhere, so the
    # email local switches to institution words.
    email_first = "front" if name_sparse else sender.given_name
    email_last = "office" if name_sparse else sender.surname
    sender_email = generate_email_local(rng, email_first, email_last)
    # Recipient address block keeps name-sparse docs at the 5-span floor.
    recipient_address = generate_localized_address(rng, locale)
    account = generate_account_number(rng)

    sb = SpanBuilder()
    append_name_or_placeholder(sb, sender.full_name, name_sparse=name_sparse)
    sb.append("\n")
    sb.append_pii(sender_address, "address")
    sb.append("\n")
    sb.append_pii(sender_phone, "phone")
    sb.append(" | ")
    sb.append_pii(sender_email, "email")
    sb.append("\n\n")

    sb.append("To: ")
    append_name_or_placeholder(sb, recipient.full_name, name_sparse=name_sparse)
    sb.append("\n")
    sb.append_pii(recipient_address, "address")
    sb.append("\n\n")

    sb.append("Dear ")
    if name_sparse:
        sb.append("Sir or Madam")
    else:
        sb.append_pii(recipient.full_name, "name")
    sb.append(",\n\n")

    # The letter body carries the generic-class keyword surface (2026-06-11
    # de-furniture: 'inquiry', 'appreciate', 'business',
    # 'forward', 'reply', 'newsletter', 'enclosed', 'regards' — the old
    # header-furniture terms no longer score).
    sb.append("Thank you for your recent inquiry. Your reference number ")
    sb.append_pii(account, "account")
    sb.append(
        " is on file. Please contact our office at the number above with "
        "any questions. We appreciate your business and look forward to "
        "your reply. A copy of our latest newsletter is enclosed."
        "\n\nRegards,\n"
    )
    append_name_or_placeholder(sb, sender.full_name, name_sparse=name_sparse)
    sb.append("\n")

    tags: list[str] = []
    _append_card_and_itin(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags


def _append_card_and_itin(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """1.2 T1.1 (C12-25): creditCard + itin (labeled or keyword-starved) + decoy.

    Append-only after the last pre-existing draw (see court.py for the
    byte-preservation rule); every draw is unconditional.
    """
    itin = generate_itin(rng)
    itin_labeled = rng.random() < _ITIN_LABELED_PROBABILITY
    card = generate_credit_card(rng)
    include_itin_decoy = rng.random() < _ITIN_DECOY_PROBABILITY
    itin_decoy = generate_itin_yy_out_of_range(rng)

    sb.append("\nPayment card on file: ")
    sb.append_pii(card, "creditCard")
    sb.append(".\n")
    if include_itin_decoy:
        # YY group outside the four IRS ranges: the bucket gate rejects it
        # even with the ITIN keyword adjacent.
        sb.append("ITIN as submitted on the application (returned as not assignable): ")
        sb.append_pii(
            itin_decoy,
            "itin",
            adversarial=True,
            expected_outcome="suppress",
        )
        sb.append(".\n")
        tags.append("itin_yy_out_of_range")
    if itin_labeled:
        sb.append("Your ITIN on file with our office: ")
        sb.append_pii(itin, "itin")
        sb.append(".\n")
    else:
        # Keyword-starved: a wall of >= 8 tokens (> 100 chars) on the keyword
        # side, vetted for the "tin" SUBSTRING (the scorer matches substrings,
        # so no routing / testing / continue / destination ...), so the
        # engine's own ITIN profile scores it at its 0.60 base under the 0.65
        # cutoff -> designed-marginal (should). Nothing follows it but the
        # closing line.
        sb.append(
            "The identifier below appears on our copy of the enrollment form and "
            "is provided for your review only.\n"
        )
        sb.append_pii(itin, "itin", tier="should")
        sb.append("\nPlease keep this letter for your records.\n")
