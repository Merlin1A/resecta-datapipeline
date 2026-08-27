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
    generate_email_local,
    generate_localized_address,
    generate_phone,
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

    text, spans = sb.finalize()
    return text, spans, []
