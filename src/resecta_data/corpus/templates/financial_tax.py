"""Tax-form (W-2 shaped) financial sub-template.

Emits W-2 Wage and Tax Statement structural text per design 03 §3.2:
box labels on the same line as PII (EIN/SSN adjacency so context
keywords fire), dense dollar-amount lines (negative context for DOB),
and no routing numbers (unlike the invoice template). Documents carry
``doctype="financial"`` with ``sub_template="financial_tax"``.

Two deviations from the design sketch, both deliberate:

- The EIN is labeled honestly as category ``"ein"`` (added to the corpus
  schema) instead of the design's "label it ssn" placeholder. EIN format
  ``XX-XXXXXXX`` does not match the SSN detector, so an ``"ssn"`` label
  would create 100 permanent false negatives and poison ssn recall.
  ``"ein"`` is not in the sweep's ``_CATEGORIES``, so it is sweep-inert.
- The employee name is one full-name line, not the design's split
  first/last boxes: the Swift detector emits full-name spans, so split
  single-token truth spans would systematically fail IoU-0.5 matching.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.corpus._names import NameSampler
from resecta_data.corpus._pii import (
    generate_ein,
    generate_invoice_number,
    generate_localized_address,
    generate_phone,
    generate_ssn,
)
from resecta_data.corpus._spans import (
    SpanBuilder,
    append_name_or_placeholder,
)

# Institution names are filler, not PII (matching the invoice template's
# "Acme Services LLC" treatment of organization names).
_EMPLOYER_NAMES: Final[tuple[str, ...]] = (
    "Granite Ridge Manufacturing Inc.",
    "Lakeside Logistics LLC",
    "Summit Field Services Corp.",
    "Harbor Point Hospitality Group",
    "Cedar Valley Distribution Co.",
    "Northgate Building Supply Inc.",
    "Bluebird Staffing Solutions LLC",
    "Ironwood Fabrication Works",
    "Prairie State Insurance Agency",
    "Riverbend Food Services Inc.",
)

_BOX_12A_CODES: Final[tuple[str, ...]] = ("D", "DD", "E", "W")


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
    employee = sampler.sample(bucket)

    ssn = generate_ssn(rng)
    ein = generate_ein(rng)
    employer_name = rng.choice(_EMPLOYER_NAMES)
    employer_address = generate_localized_address(rng, locale)
    control_number = generate_invoice_number(rng)
    employee_address = generate_localized_address(rng, locale)
    phone = generate_phone(rng)

    tax_year = rng.randint(2022, 2025)
    wages = rng.randint(18000, 142000)
    withheld = wages * rng.randint(8, 22) // 100
    ss_tax = wages * 62 // 1000
    code_12a = rng.choice(_BOX_12A_CODES)
    amount_12a = rng.randint(250, 9500)

    sb = SpanBuilder()
    sb.append("W-2 Wage and Tax Statement\n")
    sb.append(f"Tax Year: {tax_year}\n\n")

    sb.append("a. Employee's social security number: ")
    sb.append_pii(ssn, "ssn")
    sb.append("\n")
    sb.append("b. Employer identification number: ")
    sb.append_pii(ein, "ein")
    sb.append("\n")

    sb.append("c. Employer's name, address, and ZIP code:\n")
    sb.append(f"   {employer_name}\n   ")
    sb.append_pii(employer_address, "address")
    sb.append("\n")
    sb.append(f"d. Control number: {control_number}\n\n")

    sb.append("e. Employee's name: ")
    append_name_or_placeholder(sb, employee.full_name, name_sparse=name_sparse)
    sb.append("\n")
    sb.append("f. Employee's address and ZIP code: ")
    sb.append_pii(employee_address, "address")
    sb.append("\n\n")

    sb.append(f"1. Wages, tips, other compensation:  ${wages}.00\n")
    sb.append(f"2. Federal income tax withheld:      ${withheld}.00\n")
    sb.append(f"3. Social security wages:            ${wages}.00\n")
    sb.append(f"4. Social security tax withheld:     ${ss_tax}.00\n")
    sb.append(f"12a. {code_12a} ${amount_12a}.00\n\n")

    sb.append("Daytime phone: ")
    sb.append_pii(phone, "phone")
    sb.append("\n")

    text, spans = sb.finalize()
    return text, spans, []
