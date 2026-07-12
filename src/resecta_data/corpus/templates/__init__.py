"""Per-doctype synthetic document emitters.

Each template module exposes ``emit(rng, name_sampler, bucket, *,
locale, name_sparse) -> (text, spans, adversarial_tags)``. All templates
are deterministic given the rng; ``locale`` selects the Faker locale
used for locale-sensitive content (addresses) and defaults to
``en_US``; ``name_sparse`` (design 03 §3.2) replaces every person-name
span with placeholder text so the document carries zero name spans.

``SUB_TEMPLATE_EMITTERS`` holds alternate shapes that share a doctype
label: ``financial_tax`` emits W-2 shaped docs labeled
``doctype="financial"``.
"""

from __future__ import annotations

from . import court, financial, financial_tax, foia, generic, medical

EMITTERS = {
    "court": court.emit,
    "medical": medical.emit,
    "financial": financial.emit,
    "foia": foia.emit,
    "generic": generic.emit,
}

SUB_TEMPLATE_EMITTERS = {
    "financial_tax": financial_tax.emit,
}
