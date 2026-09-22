"""Generic-letter template.

Emits a plain correspondence with sender name/address/phone/email, a
recipient name/address block, and a single account reference. Used as
the neutral fallback class.

Generator profiles: under Spec-C the document-initial sender
line, ``To:``, ``Dear`` and the closing-line slot render in their shipped
context or one of four variants; under Spec-D the salutation and closing
cues (``Dear`` / ``Regards,``) are recorded as furniture.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_account_number,
    generate_credit_card,
    generate_email_local,
    generate_itin,
    generate_itin_yy_out_of_range,
    generate_phone,
)
from resecta_data.corpus._profiles import (
    FURNITURE_CLOSING,
    FURNITURE_SALUTATION,
    NameContext,
    Profile,
    append_cue,
    choose_context,
    header_after,
    render_address,
    render_name_slot,
)
from resecta_data.corpus._spans import SpanBuilder

# 1.2 T1.1: half the letters label their ITIN (fed -> must); the other half
# draw it keyword-starved (should). The YY-range decoy rides its own draw.
_ITIN_LABELED_PROBABILITY = 0.50
_ITIN_DECOY_PROBABILITY = 0.25

_SENDER_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("role_label", "From: "),
    NameContext("header", "", header_after()),
    NameContext("table_cell", "| Sender | ", " |"),
    NameContext("title_label", "Sender Name: "),
)
_RECIPIENT_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("role_label", "Attn: "),
    NameContext("table_cell", "| To | ", " |"),
    NameContext("title_label", "Recipient Name: "),
    NameContext("body_prose", "This letter is addressed to "),
)
# The salutation slot: the cue is recorded as salutation furniture under
# Spec-D; the honorific variant is a Title-label beyond ``Dr.``.
_SALUTATION_SHIPPED: Final[NameContext] = NameContext(
    "salutation", "Dear ", before_kind=FURNITURE_SALUTATION
)
_SALUTATION_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("salutation", "Hello ", before_kind=FURNITURE_SALUTATION),
    NameContext("salutation", "Greetings, ", before_kind=FURNITURE_SALUTATION),
    NameContext("role_label", "Attention: "),
    NameContext("title_label", "Dear Ms. ", before_kind=FURNITURE_SALUTATION),
)
_CLOSING_VARIANTS: Final[tuple[NameContext, ...]] = (
    NameContext("closing_line", "/s/ "),
    NameContext("closing_line", "", "\nCustomer Relations"),
    NameContext("table_cell", "| Signature | ", " |"),
    NameContext("header", "", header_after()),
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
    sender = sampler.sample(bucket)
    recipient = sampler.sample(bucket)

    sender_address = render_address(rng, profile, locale)
    sender_phone = generate_phone(rng)
    # Name-sparse docs must carry no person-name text anywhere, so the
    # email local switches to institution words.
    email_first = "front" if name_sparse else sender.given_name
    email_last = "office" if name_sparse else sender.surname
    sender_email = generate_email_local(rng, email_first, email_last)
    # Recipient address block keeps name-sparse docs at the 5-span floor.
    recipient_address = render_address(rng, profile, locale)
    account = generate_account_number(rng)

    sb = SpanBuilder()
    render_name_slot(
        sb,
        profile,
        sender,
        name_sparse=name_sparse,
        shipped=NameContext("document_initial"),
        variants=_SENDER_VARIANTS,
    )
    sb.append("\n")
    sb.append_pii(sender_address, "address")
    sb.append("\n")
    sb.append_pii(sender_phone, "phone")
    sb.append(" | ")
    sb.append_pii(sender_email, "email")
    sb.append("\n\n")

    render_name_slot(
        sb,
        profile,
        recipient,
        name_sparse=name_sparse,
        shipped=NameContext("role_label", "To: "),
        variants=_RECIPIENT_VARIANTS,
    )
    sb.append("\n")
    sb.append_pii(recipient_address, "address")
    sb.append("\n\n")

    if name_sparse:
        # The shipped sparse salutation keeps its cue (recorded as furniture
        # under Spec-D) and names nobody; the context draw still happens so
        # the profile stream position does not depend on the sparse outcome.
        ctx = choose_context(profile, _SALUTATION_SHIPPED, _SALUTATION_VARIANTS)
        cue = str(ctx.before)
        if ctx.before_kind is not None and profile is not None and profile.spec_d:
            sb.append_furniture(cue.rstrip(), ctx.before_kind)
            sb.append(cue[len(cue.rstrip()) :])
        else:
            sb.append(cue)
        sb.append("Sir or Madam")
    else:
        render_name_slot(
            sb,
            profile,
            recipient,
            name_sparse=False,
            shipped=_SALUTATION_SHIPPED,
            variants=_SALUTATION_VARIANTS,
        )
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
        "\n\n"
    )
    append_cue(sb, profile, "Regards,", FURNITURE_CLOSING)
    sb.append("\n")
    render_name_slot(
        sb,
        profile,
        sender,
        name_sparse=name_sparse,
        shipped=NameContext("closing_line"),
        variants=_CLOSING_VARIANTS,
    )
    sb.append("\n")

    tags: list[str] = []
    _append_card_and_itin(sb, rng, tags)

    text, spans = sb.finalize()
    return text, spans, tags, sb.furniture_sorted()


def _append_card_and_itin(sb: SpanBuilder, rng: random.Random, tags: list[str]) -> None:
    """The 17-family extension: creditCard + itin (labeled or keyword-starved) + decoy.

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
