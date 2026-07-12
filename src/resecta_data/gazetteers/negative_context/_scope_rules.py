"""Heuristics mapping a negative-context source keyword to (category, doctype, weight).

Scope assignment is deterministic and audit-friendly: a table of source_id →
(default category_scopes, default doctype_scope, default weight) plus a small
set of per-keyword overrides for terms that carry obviously different scope.

In addition to source-file-derived rows, this module also exposes four
hand-curated lists — ``_MANUAL_MEDICAL``, ``_MANUAL_GENERIC``,
``_MANUAL_AUDIT_PART3``, and ``_MANUAL_S3_FINANCIAL`` — whose entries are
not backed by a licensed corpus
file. They are returned via a parallel path (:func:`manual_entries`) and carry
synthetic ``source_id`` values so the build-time provenance for corpus-derived
rows stays clean.

The A5 audit (``research/negative-context/1.md``, 2026-04-27) re-curated the
three corpus pools by adding ``_AUDIT_REMOVE_PER_SOURCE`` to short-circuit
keywords with no plausible PII adjacency, modifying ``_KEYWORD_OVERRIDES`` per
Pool A/B exception lists, and re-scoping or re-weighting ``_MANUAL_MEDICAL``
and ``_MANUAL_GENERIC`` entries. The 47-entry ``_MANUAL_AUDIT_PART3`` fills
the originally-empty cells with primary-source-backed candidates.

This is a build-time suggestion surface; Jesse hand-reviews the candidate
artifact before anything installs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from resecta_data.common.exceptions import PipelineError


@dataclass(frozen=True, slots=True)
class SourceScope:
    """Default scoping applied to every keyword from a given source file."""

    doctype_scope: str
    category_scopes: tuple[str, ...]
    default_weight: float


@dataclass(frozen=True, slots=True)
class Entry:
    """A hand-curated negative-context keyword with explicit scoping.

    Unlike the source-file-derived rows produced by :func:`scope_keyword`,
    an :class:`Entry` carries its own ``category_scopes``, ``doctype_scope``,
    and ``weight`` directly. Aliases fan out to separate emitted rows so the
    (keyword, category, doctype) dedup key covers every surface form.

    ``source_id`` lets a single Entry override the bucket-level synthetic id
    with a primary-source citation (e.g. ``nucc_1500_v13``) so an auditor can
    grep the candidates JSON for the federal source backing each row. When
    ``source_id`` is ``None`` the bucket default wins (``manual_medical``,
    ``manual_generic``, or ``manual_audit_part3``).
    """

    keyword: str
    aliases: tuple[str, ...]
    category_scopes: tuple[str, ...]
    doctype_scope: str
    weight: float
    rationale: str
    source_id: str | None = None


# Source-level defaults. Keyword-level overrides refine these below.
_SOURCE_DEFAULTS: Final[dict[str, SourceScope]] = {
    "us_courts_glossary": SourceScope(
        doctype_scope="court",
        category_scopes=("ssn", "npi", "dea", "dob"),
        default_weight=0.45,
    ),
    "doj_legal_glossary": SourceScope(
        doctype_scope="foia",
        category_scopes=("ssn", "name", "address"),
        default_weight=0.40,
    ),
    "ubl_invoice_labels": SourceScope(
        doctype_scope="financial",
        category_scopes=("ssn", "npi"),
        default_weight=0.50,
    ),
}


# A5 audit (1.md, 2026-04-27): keywords whose presence in the bootstrap
# source files has no plausible PII adjacency on real documents and whose
# default scoping creates direct false-negative risk. ``scope_keyword``
# short-circuits to ``[]`` for any (source_id, keyword) pair listed here, so
# the keyword never enters the candidates payload.
#
# Keep lists per pool (not in the REMOVE set):
#   - us_courts_glossary: case number, docket, docket number (override),
#     defendant, plaintiff (override-rescoped to name).
#   - doj_legal_glossary: foia, freedom of information (source default at
#     0.40), exemption b6, exemption b7, redacted, redaction, responsive
#     records (overrides at FOIA-marker weights).
#   - ubl_invoice_labels: account number, routing number, invoice number,
#     po number, tax id, ein (overrides at SSN-confusable weights).
#
# The remaining keywords in each pool are listed here verbatim from the
# bootstrap .txt files (sources/negative_context/*_20260416.txt). The
# .txt source files are immutable; audit decisions live here as Python
# data.
_AUDIT_REMOVE_PER_SOURCE: Final[dict[str, frozenset[str]]] = {
    # Pool A: us_courts_glossary mass-removal per 1.md:36-55. The 41 keywords
    # below have no plausible adjacency to SSN/NPI/DEA/DOB on real federal
    # court filings; suppression on these terms creates direct FN risk per
    # the Conservative-preset adversarial model (1.md:21).
    "us_courts_glossary": frozenset(
        {
            "affidavit",
            "amicus curiae",
            "appellant",
            "appellate",
            "appellee",
            "arraignment",
            "bail",
            "bench trial",
            "bond",
            "brief",
            "case no",
            "chambers",
            "civil action",
            "clerk of court",
            "complaint",
            "court reporter",
            "criminal case",
            "deposition",
            "exhibit",
            "grand jury",
            "hearing",
            "indictment",
            "injunction",
            "judgment",
            "jury trial",
            "magistrate judge",
            "minute order",
            "motion for summary judgment",
            "motion to dismiss",
            "notice of appeal",
            "pro se",
            "ruling",
            "sentencing",
            "stipulation",
            "subpoena",
            "transcript",
            "venue",
            "verdict",
            "voir dire",
            "warrant",
            "writ of habeas corpus",
        }
    ),
    # Pool B: doj_legal_glossary FOIA narrowing per 1.md:57-74. The 25
    # process/component-name terms below sit far from PII on real DOJ FOIA
    # responses; only FOIA-exemption strings genuinely indicate redaction
    # adjacency.
    "doj_legal_glossary": frozenset(
        {
            "administrative law judge",
            "agency appeal",
            "agency records",
            "assistant us attorney",
            "attorney general",
            "bureau of prisons",
            "closed case",
            "criminal referral",
            "department of justice",
            "drug enforcement administration",
            "executive order",
            "federal bureau of investigation",
            "federal register",
            "grand jury subpoena",
            "immigration court",
            "investigative file",
            "memorandum opinion",
            "notice of action",
            "notice of hearing",
            "privacy act",
            "referral",
            "solicitor general",
            "special agent",
            "subpoena duces tecum",
            "us attorney",
        }
    ),
    # Pool C: ubl_invoice_labels narrowing per 1.md:77-99. Drop the 26 UBL
    # terms whose 9-digit-shape confusable rate is too low to defend against
    # a real SSN landing on the same invoice. ``due date`` is dropped here
    # but re-added in ``_MANUAL_AUDIT_PART3`` as (dob, financial, 0.45).
    "ubl_invoice_labels": frozenset(
        {
            "amount due",
            "balance due",
            "bill to",
            "billing address",
            "customer id",
            "due date",
            "invoice",
            "invoice date",
            "invoice no",
            "line item",
            "net 30",
            "net 60",
            "payment terms",
            "po no",
            "purchase order",
            "quantity",
            "remit to",
            "remittance",
            "ship to",
            "shipping address",
            "subtotal",
            "supplier",
            "tax rate",
            "total due",
            "unit price",
            "vat",
        }
    ),
}


# Per-keyword overrides. Each entry refines the default scope for a specific
# normalized (lowercase) keyword. Rationales are stored to aid human review.
# A5 audit verdicts (1.md, Pool A/B/C exception lists) are reflected here.
_KEYWORD_OVERRIDES: Final[dict[str, tuple[str, tuple[str, ...], float, str]]] = {
    # Court-doc trackers that routinely look like SSNs/NPIs/DEAs but are case IDs.
    "case number": (
        "court",
        ("ssn", "npi", "dea"),
        0.70,
        "Court case-number format per 28 U.S.C. § 1914 and FRCP Rule 10; "
        "canonical 9-digit-shape confusable for SSN/NPI/DEA on federal court "
        "dockets (1.md:87, 1.md:437).",
    ),
    "docket number": (
        "court",
        ("ssn", "npi", "dea"),
        0.70,
        "Court docket number; canonical confusable for SSN/NPI/DEA on captions "
        "(1.md:88, 1.md:439).",
    ),
    "docket": (
        "court",
        ("ssn",),
        0.50,
        "Standalone 'docket' token is weaker than 'docket number'; SSN-shape "
        "only per US Courts glossary (1.md:50, 1.md:438).",
    ),
    # Court-caption RESCOPE per 1.md:43-44 — defendant/plaintiff labels are
    # name-adjacent; FRCP 5.2 already requires SSN-redacted captions.
    "defendant": (
        "court",
        ("name",),
        0.30,
        "Defendant captions are name-adjacent; FRCP 5.2 requires SSN already "
        "redacted in caption blocks (1.md:43).",
    ),
    "plaintiff": (
        "court",
        ("name",),
        0.30,
        "Plaintiff captions are name-adjacent; FRCP 5.2 requires SSN already "
        "redacted in caption blocks (1.md:44).",
    ),
    # Invoice fields that are numeric id lookalikes (UBL Pool C exception list).
    "invoice number": (
        "financial",
        ("ssn", "npi"),
        0.75,
        "UBL cbc:ID invoice identifier; canonical sequential-9-digit "
        "confusable for SSN (1.md:90, 1.md:443).",
    ),
    "account number": (
        "financial",
        ("ssn",),
        0.70,
        "UBL cac:PayeeFinancialAccount/cbc:ID; checking account numbers "
        "commonly 9-12 digits, high SSN-shape confusable (1.md:91, 1.md:442).",
    ),
    "routing number": (
        "financial",
        ("ssn",),
        0.80,
        "ABA RTN is exactly 9 digits with check digit per Federal Reserve "
        "E-Payments Routing Directory; very high shape confusable "
        "(1.md:92, 1.md:444).",
    ),
    "po number": (
        "financial",
        ("ssn",),
        0.65,
        "UBL cac:OrderReference/cbc:ID; high SSN-shape confusable. NPI fanout "
        "dropped — UBL does not carry healthcare provider IDs (1.md:93, 1.md:441).",
    ),
    "tax id": (
        "financial",
        ("ssn",),
        0.35,
        "TIN field per IRS Form W-9 Part I and NUCC CMS-1500 Box 25; SSN/EIN "
        "alternative shares 9-digit shape (1.md:94, 1.md:445). De-weighted "
        "0.55 -> 0.35 at the S3 D1 gate (2026-06-11, Jesse-reviewed): a TIN "
        "field is the designated label for a sole proprietor's SSN, so at "
        "0.55 the suppression dropped label-boosted SSNs below the balanced "
        "cutoff; 0.35 is the design 02 section-2.3 ambiguous-term weight "
        "(boosted SSNs surface, bare 9-digit shapes still drop at balanced).",
    ),
    "ein": (
        "financial",
        ("ssn",),
        0.55,
        "EIN format XX-XXXXXXX shares 9-digit shape with SSN; IRS Form W-9 "
        "Part I provides as alternative TIN (1.md:95, 1.md:440).",
    ),
    # FOIA-specific: exemption codes surround redacted content.
    "exemption b6": (
        "foia",
        ("name", "address", "dob"),
        0.50,
        "5 U.S.C. § 552(b)(6) personal-privacy exemption; marking exists "
        "because PII has been redacted (1.md:63, 1.md:96).",
    ),
    "exemption b7": (
        "foia",
        ("name", "address"),
        0.50,
        "5 U.S.C. § 552(b)(7)(C) law-enforcement personal privacy (1.md:64, 1.md:97).",
    ),
    "redacted": (
        "foia",
        ("ssn", "name", "address", "dob"),
        0.35,
        "Literal token 'redacted' indicates redaction metadata (1.md:65, 1.md:98, 1.md:433).",
    ),
    # FOIA narrowing — KEEP_AT_LOWER_WEIGHT entries from 1.md Pool B that
    # were not previously in _KEYWORD_OVERRIDES.
    "redaction": (
        "foia",
        ("ssn", "name", "address"),
        0.35,
        "DOJ FOIA Reference Guide §IX redaction marker; on real FOIA "
        "responses sits next to redacted PII (1.md Pool B narrowing).",
    ),
    "responsive records": (
        "foia",
        ("ssn", "name"),
        0.30,
        "DOJ FOIA Reference Guide §IV; document-context indicator with weak "
        "direct PII adjacency (1.md:68, 1.md:434).",
    ),
}


# -----------------------------------------------------------------------------
# Hand-curated entries (non-source-derived).
#
# These three lists are the MEDICAL, GENERIC, and AUDIT_PART3 seed per A5
# audit findings (1.md). Every entry scoped to ``ssn`` (rather than the
# Swift-side ``medicalRecord``) preserves the M7 (2026-04-18) scope-to-ssn-only
# resolution; the schema enum lacks ``account``, so account-number-style
# entries route to ``ssn`` until Session 2 extends both schema and Swift in
# a paired PR.
#
# The synthetic source_ids below (``manual_medical``, ``manual_generic``,
# ``manual_audit_part3``) are emitted to the candidates artifact so Jesse's
# hand-review can tell these rows apart from the licensed-corpus rows. Each
# Entry may override the bucket id with a per-Entry primary-source citation
# (e.g. ``nucc_1500_v13``).
# -----------------------------------------------------------------------------

_MANUAL_MEDICAL_SOURCE_ID: Final[str] = "manual_medical"
_MANUAL_GENERIC_SOURCE_ID: Final[str] = "manual_generic"
_MANUAL_AUDIT_PART3_SOURCE_ID: Final[str] = "manual_audit_part3"
_MANUAL_S3_FINANCIAL_SOURCE_ID: Final[str] = "self_authored_20260610"


_MANUAL_MEDICAL: Final[list[Entry]] = [
    Entry(
        keyword="lot number",
        aliases=("lot #",),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.40,
        rationale=(
            "Drug lot numbers per 21 CFR 201.18 are alphanumeric pharmaceutical "
            "batch IDs. NPI fanout dropped — lot codes do not 10-digit-shape "
            "match NPIs (1.md:133, 1.md:230)."
        ),
        source_id="fda_drug_label_21cfr201",
    ),
    Entry(
        keyword="ndc",
        aliases=("ndc code",),
        category_scopes=("npi",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "10/11-digit NDC near 'NDC' is overwhelmingly NDC not NPI per FDA "
            "NDC Directory (1.md:134, 1.md:424)."
        ),
        source_id="fda_ndc_directory",
    ),
    Entry(
        keyword="visit number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "UB-04 visit identifier; HIPAA Safe Harbor 45 CFR 164.514(b)(2)(i)(J) "
            "account-numbers category (1.md:117, 1.md:464)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="room number",
        aliases=("room #",),
        category_scopes=("dob",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Room numbers 3-4 digits; weak DOB-shape confusable (e.g. 'room "
            "1234' looks like a DOB stem). Rescoped from (ssn,npi) per 1.md:135, "
            "1.md:232."
        ),
    ),
    Entry(
        keyword="specimen id",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.45,
        rationale=(
            "Lab specimen identifier; institutional non-PII per NUCC Box 23 "
            "CLIA context (1.md:122, 1.md:462)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="mrn",
        aliases=("medical record number",),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.65,
        rationale=(
            "Medical Record Number per HIPAA Safe Harbor 45 CFR 164.514(b)(2)(i)(H); "
            "per-institution 6-10 digit identifier with high SSN-shape "
            "confusable rate (1.md:114, 1.md:450)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="chart number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "Variant of MRN per HIPAA Safe Harbor 45 CFR 164.514(b)(2)(i)(H) (1.md:115, 1.md:454)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="account number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.65,
        rationale=(
            "NUCC CMS-1500 Box 26 Patient's Account No.; institutional account ID. "
            "Distinct from the (ssn, financial) override on the same keyword "
            "(1.md:116, 1.md:451)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="bed number",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Bed identifiers are short numeric tokens; weak DOB-shape "
            "confusable on census sheets. Rescoped from (ssn,) per 1.md:136, "
            "1.md:233."
        ),
    ),
    Entry(
        keyword="unit number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.45,
        rationale=(
            "Ward/unit IDs on hospital routing slips; A5 audit had no FN "
            "evidence either way, KEEP at current scope."
        ),
    ),
    Entry(
        keyword="claim number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.65,
        rationale=(
            "NUCC CMS-1500 Box 11b Other Claim ID; CMS ICN/DCN at UB-04 FL 37 (1.md:120, 1.md:455)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="encounter id",
        aliases=("encounter number",),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "Encounter identifier per HIPAA Safe Harbor (J) account-numbers "
            "category (1.md:118, 1.md:456)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="admission number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "UB-04 admission identifier per CMS Medicare Claims Processing "
            "Manual Pub. 100-04 Ch. 25 (1.md:119, 1.md:453)."
        ),
        source_id="cms_iom_pub100_04_ch1",
    ),
    Entry(
        keyword="order number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.45,
        rationale=(
            "Lab/imaging order number; institutional identifier. NPI fanout "
            "dropped per 1.md:123, 1.md:460."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="prescription number",
        aliases=("rx number",),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "Pharmacy fill ID near DEA-shaped string is overwhelmingly the "
            "pharmacy fill ID per 21 CFR 1306.05; not DEA registration. "
            "Rescoped from (ssn,npi) per 1.md:124, 1.md:397."
        ),
        source_id="dea_pharmacist_manual",
    ),
    Entry(
        keyword="pharmacy id",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.40,
        rationale=(
            "NCPDP pharmacy ID confusable with DEA when shape overlaps; per "
            "DEA Pharmacist's Manual. Rescoped from (npi,) per 1.md:125, 1.md:395."
        ),
        source_id="ncpdp_ref_via_dea",
    ),
    Entry(
        keyword="physician id",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Generic physician ID label may legitimately co-occur with DEA "
            "registration number; low suppression weight to preserve recall. "
            "Rescoped from (npi,) per 1.md:126, 1.md:396."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="provider id",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Generic provider ID label may co-occur with DEA; low weight "
            "preserves recall. Rescoped from (npi,) per 1.md:127, 1.md:398."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="accession number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.50,
        rationale=(
            "Lab specimen accession identifier per NUCC Box 23 CLIA context (1.md:121, 1.md:452)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="barcode",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Numeric label barcodes on specimens; (npi) scope rejected as "
            "barcode contents pattern-defined by GS1/HIBC, not directly NPI "
            "confusable. Lowered from 0.45 per 1.md:137, 1.md:234."
        ),
    ),
    Entry(
        keyword="inventory id",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.35,
        rationale=(
            "21 CFR 1304.04 inventory identifier is institution-internal, not "
            "DEA registration #. Rescoped from (ssn,) per 1.md:138, 1.md:393."
        ),
        source_id="dea_21cfr_1304_04",
    ),
    Entry(
        keyword="batch number",
        aliases=(),
        category_scopes=("npi",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Drug batch ID; near 10-digit string usually batch not NPI per "
            "21 CFR Part 207. SSN fanout dropped (1.md:139, 1.md:254)."
        ),
        source_id="fda_drug_label_21cfr201",
    ),
    Entry(
        keyword="billing code",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "CPT/HCPCS 5-digit not DEA-shaped; weak relevance. (ssn,npi) scope "
            "rejected as catastrophic FN risk per 1.md:128-129, 1.md:235."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="service date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "NUCC CMS-1500 Box 24A Date(s) of Service; date-shaped string near "
            "service-date label is overwhelmingly DOS not DOB. Scope strictly "
            "medical to avoid catastrophic FN on non-medical documents "
            "(1.md:130, 1.md:402)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="admission date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "NUCC CMS-1500 Box 18 admission date; HIPAA Safe Harbor (C) treats "
            "admission date as PHI but the label suppresses DOB-confusion only "
            "on medical doctype (1.md:131, 1.md:399)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="discharge date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "NUCC CMS-1500 Box 18 discharge date; same scoping as admission "
            "date (1.md:132, 1.md:400)."
        ),
        source_id="nucc_1500_v13",
    ),
]


_MANUAL_GENERIC: Final[list[Entry]] = [
    Entry(
        keyword="reference number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.40,
        rationale=(
            "Variable-length sequential identifier per UBL BuyerReference; "
            "common 9-digit shape. NPI fanout dropped per 1.md:145, 1.md:473."
        ),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="tracking number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.50,
        rationale=(
            "USPS IMpb 22-digit domestic / 13-char alphanumeric international "
            "per USPS Publication 199 (1.md:146, 1.md:479)."
        ),
        source_id="usps_pub199",
    ),
    Entry(
        keyword="order id",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.45,
        rationale=("E-commerce sequential ID per UBL OrderReference (1.md:147, 1.md:470)."),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="confirmation number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.50,
        rationale=(
            "Booking/order confirmation; variable-length sequential identifier "
            "(1.md:148, 1.md:467)."
        ),
        source_id="usps_pub199",
    ),
    Entry(
        keyword="receipt number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.45,
        rationale=("Variable-length point-of-sale identifier (1.md:149, 1.md:472)."),
        source_id="irs_form_1099",
    ),
    Entry(
        keyword="transaction id",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.45,
        rationale=(
            "Variable-length transaction identifier; Regulation E electronic "
            "fund transfer context (1.md:150, 1.md:480)."
        ),
        source_id="frb_reg_e",
    ),
    Entry(
        keyword="booking number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.45,
        rationale=(
            "Travel/reservation identifier; variable-length sequential (1.md:151, 1.md:466)."
        ),
        source_id="usps_pub199",
    ),
    Entry(
        keyword="customer id",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.45,
        rationale=(
            "Vendor-assigned customer identifier; variable-length. NPI fanout "
            "dropped per 1.md:152, 1.md:468."
        ),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="account reference",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.40,
        rationale=(
            "UBL BuyerReference / account-reference variant; sequential "
            "identifier (1.md:153, 1.md:465)."
        ),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="quote number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.40,
        rationale=("Sequential quote identifier (1.md:154, 1.md:471)."),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="ticket number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.40,
        rationale=("Support-system identifier; variable-length sequential (1.md:155, 1.md:478)."),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="support case",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.35,
        rationale=("Customer-support case identifier (1.md:156, 1.md:477)."),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="request id",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.35,
        rationale=("API/system request identifier; variable-length (1.md:157, 1.md:474)."),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="serial number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.40,
        rationale=(
            "Product serial number per HIPAA Safe Harbor (M) device serials "
            "category. NPI fanout dropped per 1.md:158, 1.md:476."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="model number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.30,
        rationale=(
            "Product model identifier; weaker shape match per HIPAA Safe "
            "Harbor (M) device serials (1.md:159, 1.md:469)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="reservation number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="generic",
        weight=0.40,
        rationale=("Booking variant; variable-length sequential (1.md:160, 1.md:475)."),
        source_id="usps_pub199",
    ),
]


# A5 audit Part 3 (1.md:164-310, JSON block 1.md:372-481): 47-ish
# primary-source-backed candidates filling 17 of 20 originally-empty cells.
# Entries are sorted by (category_scopes[0], doctype_scope, keyword) so a
# reviewer can scan by cell. The schema enum lacks ``account``; per the
# session boundary, Part 3 routes account-number-style candidates to (ssn,)
# instead. ``email`` and ``phone`` are deliberately not represented — those
# are zero-entry decisions reserved for Session 2's research-2 ingestion.
_MANUAL_AUDIT_PART3: Final[list[Entry]] = [
    # (address, court) — 1.md:258-263
    Entry(
        keyword="Clerk of Court",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="court",
        weight=0.45,
        rationale=(
            "c/o Clerk of Court address is a court office address, not a "
            "party-identifier address (1.md:262, 1.md:382)."
        ),
        source_id="uscourts_glossary",
    ),
    Entry(
        keyword="Courthouse",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="court",
        weight=0.40,
        rationale=(
            "Federal courthouse addresses are public locations, not party PII (1.md:263, 1.md:383)."
        ),
        source_id="uscourts_glossary",
    ),
    # (address, financial) — 1.md:273-278
    Entry(
        keyword="branch address",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="financial",
        weight=0.40,
        rationale=(
            "Bank branch address on receipts and statements; not customer PII (1.md:278, 1.md:380)."
        ),
        source_id="frb_routing_directory",
    ),
    Entry(
        keyword="merchant address",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="financial",
        weight=0.40,
        rationale=(
            "Merchant street address on receipts and 1099 PAYER address blocks; "
            "not customer PII (1.md:277, 1.md:381)."
        ),
        source_id="irs_form_1099",
    ),
    # (address, generic) — 1.md:280-285
    Entry(
        keyword="from",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="generic",
        weight=0.30,
        rationale=(
            "USPS Domestic Mail Manual sender block; letterhead From: addresses "
            "are typically business not natural-person PII (1.md:285, 1.md:384)."
        ),
        source_id="usps_dmm",
    ),
    Entry(
        keyword="return address",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="generic",
        weight=0.35,
        rationale=(
            "Return address per USPS DMM is the sender block; commonly business "
            "address (1.md:284, 1.md:385)."
        ),
        source_id="usps_dmm",
    ),
    # (address, medical) — 1.md:265-271
    Entry(
        keyword="billing address",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="medical",
        weight=0.35,
        rationale=(
            "NUCC CMS-1500 Box 33 billing-provider address; provider not "
            "patient address (1.md:269, 1.md:386)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="payer",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="medical",
        weight=0.35,
        rationale=(
            "Insurance company address in claim header; not patient PII (1.md:271, 1.md:387)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="service facility",
        aliases=(),
        category_scopes=("address",),
        doctype_scope="medical",
        weight=0.40,
        rationale=(
            "NUCC CMS-1500 Box 32 service-facility address; not patient PII (1.md:270, 1.md:388)."
        ),
        source_id="nucc_1500_v13",
    ),
    # (dea, foia) — 1.md:217-222
    Entry(
        keyword="Diversion Control",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="foia",
        weight=0.30,
        rationale=(
            "DEA Diversion Control Division FOIA Library publishes redacted "
            "MOAs and audit reports; suppression context (1.md:222, 1.md:389)."
        ),
        source_id="dea_diversion_foia",
    ),
    Entry(
        keyword="Form 224",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="foia",
        weight=0.35,
        rationale=(
            "DEA Form 224 registration application released through DEA FOIA "
            "carries DEA registration #; the form name signals suppression "
            "context (1.md:221, 1.md:390)."
        ),
        source_id="dea_form_224",
    ),
    # (dea, medical) — 1.md:205-213. prescription number, pharmacy id,
    # physician id, provider id, inventory id, billing code already updated
    # in _MANUAL_MEDICAL above; this list adds the three new tokens.
    Entry(
        keyword="NCPDP",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.50,
        rationale=(
            "NCPDP pharmacy ID is 7-digit, format-distinct from DEA 9-char "
            "alphanumeric. Suppresses DEA-shape false positives near pharmacy "
            "provider blocks (1.md:210, 1.md:394)."
        ),
        source_id="ncpdp_ref_via_dea",
    ),
    Entry(
        keyword="formulary",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "Formulary IDs and codes adjacent to medication lists are non-DEA "
            "institutional codes (1.md:211, 1.md:391)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="inventory",
        aliases=(),
        category_scopes=("dea",),
        doctype_scope="medical",
        weight=0.35,
        rationale=(
            "21 CFR 1304.04 inventory line items are non-DEA codes; DEA "
            "registration # appears in the document header, not on the "
            "inventory line (1.md:212, 1.md:392)."
        ),
        source_id="dea_21cfr_1304_04",
    ),
    # (dob, financial) — 1.md:296-303
    Entry(
        keyword="due date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.45,
        rationale=("UBL cbc:DueDate is invoice payment due date, not DOB (1.md:302, 1.md:403)."),
        source_id="oasis_ubl_2_1",
    ),
    Entry(
        keyword="statement period",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.40,
        rationale=("Bank statement period dates per Regulation E; not DOB (1.md:301, 1.md:404)."),
        source_id="frb_reg_e",
    ),
    Entry(
        keyword="transaction date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.45,
        rationale=(
            "Transaction date label per 1099 instructions; date-shaped string "
            "overwhelmingly transaction not DOB (1.md:300, 1.md:405)."
        ),
        source_id="irs_form_1099",
    ),
    # (dob, generic) — 1.md:306-310
    Entry(
        keyword="issue date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="generic",
        weight=0.35,
        rationale=(
            "Generic document issue date; date-shaped string near issue date "
            "label is overwhelmingly issue not DOB (1.md:310, 1.md:406)."
        ),
        source_id="iso_8601_via_nist",
    ),
    # (dob, medical) — 1.md:287-294. service date, admission date,
    # discharge date already updated in _MANUAL_MEDICAL; LMP is the new entry.
    Entry(
        keyword="LMP",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="medical",
        weight=0.45,
        rationale=(
            "NUCC CMS-1500 Box 14 last-menstrual-period date qualifier (1.md:294, 1.md:401)."
        ),
        source_id="nucc_1500_v13",
    ),
    # (name, court) — 1.md:226-233
    Entry(
        keyword="ABC Corp.",
        aliases=("LLC", "Inc.", "LLP", "Trust", "Estate of"),
        category_scopes=("name",),
        doctype_scope="court",
        weight=0.35,
        rationale=(
            "Legal-entity name suffixes per FRCP Rule 17 indicate corporate "
            "party not natural-person PII (1.md:232, 1.md:407, 1.md:409)."
        ),
        source_id="frcp_rule_5_2",
    ),
    Entry(
        keyword="Esq.",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="court",
        weight=0.50,
        rationale=(
            "Attorney signature blocks per FRCP and 28 U.S.C. § 1654; attorney "
            "names appear publicly in case captions (1.md:230, 1.md:408)."
        ),
        source_id="frcp_rule_5_2",
    ),
    Entry(
        keyword="SBN",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="court",
        weight=0.45,
        rationale=(
            "State Bar Number following attorney name in caption block; non-PII "
            "identifier (1.md:231, 1.md:410)."
        ),
        source_id="frcp_rule_5_2",
    ),
    Entry(
        keyword="v.",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="court",
        weight=0.30,
        rationale=("Case caption v. (versus) separates public party names (1.md:233, 1.md:411)."),
        source_id="uscourts_glossary",
    ),
    # (name, financial) — 1.md:244-250
    Entry(
        keyword="Branch",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.30,
        rationale=(
            "Bank branch name in transaction blocks; not customer PII (1.md:249, 1.md:412)."
        ),
        source_id="frb_routing_directory",
    ),
    Entry(
        keyword="Merchant",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.30,
        rationale=("Merchant name on receipts; not customer PII (1.md:250, 1.md:413)."),
        source_id="frb_routing_directory",
    ),
    Entry(
        keyword="N.A.",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.40,
        rationale=(
            "Bank legal-name suffix per Federal Reserve MDRM (e.g., Wells "
            "Fargo Bank, N.A.); institution not customer name "
            "(1.md:248, 1.md:414)."
        ),
        source_id="frb_mdrm_routing",
    ),
    # (name, generic) — 1.md:252-256
    Entry(
        keyword="From:",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="generic",
        weight=0.30,
        rationale=(
            "USPS Domestic Mail Manual From: block typically business not "
            "natural-person (1.md:256, 1.md:415)."
        ),
        source_id="usps_dmm",
    ),
    # (name, medical) — 1.md:235-242
    Entry(
        keyword="Care Plan",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="medical",
        weight=0.30,
        rationale=("Document-type label, not patient name (1.md:242, 1.md:416)."),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="Department of",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="medical",
        weight=0.35,
        rationale=(
            "Department name in hospital letterhead; not patient name (1.md:241, 1.md:417)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="Hospital",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="medical",
        weight=0.40,
        rationale=(
            "Hospital name in letterhead; institution not patient PII (1.md:239, 1.md:418)."
        ),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    Entry(
        keyword="Medical Center",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="medical",
        weight=0.40,
        rationale=("Medical center name; institution not patient PII (1.md:240, 1.md:419)."),
        source_id="hipaa_safe_harbor_45cfr_164_514",
    ),
    # (npi, foia) — 1.md:194-199
    Entry(
        keyword="OIG audit",
        aliases=(),
        category_scopes=("npi",),
        doctype_scope="foia",
        weight=0.30,
        rationale=(
            "DOJ OIG audit reports cite provider NPIs in published findings; "
            "partial confusable context (1.md:199, 1.md:420)."
        ),
        source_id="doj_oig_reports",
    ),
    Entry(
        keyword="covered entity",
        aliases=(),
        category_scopes=("npi",),
        doctype_scope="foia",
        weight=0.35,
        rationale=(
            "HHS OCR breach notices reference covered entity; 10-digit string "
            "in this context is typically entity-NPI cross-reference, but "
            "suppression weight reflects potential real NPI presence "
            "(1.md:198, 1.md:421)."
        ),
        source_id="hhs_ocr_breach_portal",
    ),
    # (npi, medical) — 1.md:184-192. NDC and lot number live in
    # _MANUAL_MEDICAL; CPT, HCPCS, UPIN are the three new tokens.
    Entry(
        keyword="CPT",
        aliases=(),
        category_scopes=("npi",),
        doctype_scope="medical",
        weight=0.30,
        rationale=(
            "CPT codes are 5-digit per NUCC Box 24D; weak 10-digit confusable "
            "but appears alongside NPI on CMS-1500 (1.md:190, 1.md:422)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="HCPCS",
        aliases=(),
        category_scopes=("npi",),
        doctype_scope="medical",
        weight=0.30,
        rationale=("HCPCS codes per NUCC Box 24D; same 5-digit shape as CPT (1.md:191, 1.md:423)."),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="UPIN",
        aliases=(),
        category_scopes=("npi",),
        doctype_scope="medical",
        weight=0.45,
        rationale=(
            "Legacy Unique Physician Identification Number per NUCC Box 17a "
            "qualifier 1G; appears on archival pre-2007 documents "
            "(1.md:192, 1.md:425)."
        ),
        source_id="nucc_1500_v13",
    ),
    # (ssn, foia) — 1.md:166-181 + 1.md:427-436. exemption b6/b7, redacted,
    # redaction, responsive records ride on overrides; six new tokens here.
    # (b)(6) and (b)(7)(C) fan to (ssn, name, address) per 1.md:427-428.
    Entry(
        keyword="(b)(6)",
        aliases=(),
        category_scopes=("ssn", "name", "address"),
        doctype_scope="foia",
        weight=0.50,
        rationale=(
            "5 U.S.C. § 552(b)(6) personal-privacy exemption marker indicates "
            "redaction has already occurred; suppression context "
            "(1.md:63, 1.md:427)."
        ),
        source_id="five_usc_552",
    ),
    Entry(
        keyword="(b)(7)(C)",
        aliases=(),
        category_scopes=("ssn", "name", "address"),
        doctype_scope="foia",
        weight=0.50,
        rationale=(
            "5 U.S.C. § 552(b)(7)(C) law-enforcement personal-privacy "
            "exemption (1.md:64, 1.md:428)."
        ),
        source_id="five_usc_552",
    ),
    Entry(
        keyword="law enforcement records",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="foia",
        weight=0.35,
        rationale=(
            "Exemption 7 threshold language per DOJ FOIA Guide chapter on "
            "Exemption 7 (1.md:69, 1.md:431)."
        ),
        source_id="doj_exemption_7c_guide",
    ),
    Entry(
        keyword="personal privacy",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="foia",
        weight=0.40,
        rationale=("Exemption 6 and 7(C) statutory phrase per DOJ FOIA Guide (1.md:66, 1.md:432)."),
        source_id="doj_exemption_6_guide",
    ),
    Entry(
        keyword="withheld in full",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="foia",
        weight=0.40,
        rationale=("DOJ FOIA Reference Guide §IX redaction notation (1.md:67, 1.md:435)."),
        source_id="doj_oip_reference_guide",
    ),
    Entry(
        keyword="withheld in part",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="foia",
        weight=0.40,
        rationale=("DOJ FOIA Reference Guide §IX redaction notation (1.md:67, 1.md:436)."),
        source_id="doj_oip_reference_guide",
    ),
    # (ssn, medical) — 1.md:168-180. mrn, chart number, account number
    # (medical), claim number, accession number, specimen id, order number,
    # encounter id, admission number, visit number live in _MANUAL_MEDICAL;
    # the nine entries below fill the rest of the cell.
    Entry(
        keyword="DCN",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.50,
        rationale=(
            "CMS Document Control Number; synonym for ICN at UB-04 FL 37 (1.md:179, 1.md:446)."
        ),
        source_id="cms_iom_pub100_04_ch1",
    ),
    Entry(
        keyword="FECA number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.50,
        rationale=(
            "NUCC CMS-1500 Box 11; 9-character alphanumeric per 5 U.S.C. § 8101 "
            "federal-employee work-injury claims (1.md:180, 1.md:447)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="ICN",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "CMS Internal Control Number; 13-digit Medicare claim ID at UB-04 "
            "FL 37 (1.md:178, 1.md:448)."
        ),
        source_id="cms_iom_pub100_04_ch1",
    ),
    Entry(
        keyword="MBI",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.65,
        rationale=(
            "Medicare Beneficiary Identifier; 11-char alphanumeric replacing "
            "SSN-based HICN since 2020-01-01 under MACRA 2015 SSNRI "
            "(1.md:177, 1.md:449)."
        ),
        source_id="cms_mbi_format",
    ),
    Entry(
        keyword="group number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "NUCC CMS-1500 Box 11 Insured's Policy, Group, or FECA Number (1.md:175, 1.md:457)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="insured's I.D. number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "NUCC CMS-1500 Box 1a; payer-assigned member ID, high SSN-shape "
            "confusable (1.md:172, 1.md:458)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="member ID",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=(
            "NUCC v13 Item 1a/Item 6 reference; payer-assigned identifier (1.md:173, 1.md:459)."
        ),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="policy number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.55,
        rationale=("NUCC CMS-1500 Box 9a, Box 11 (1.md:176, 1.md:461)."),
        source_id="nucc_1500_v13",
    ),
    Entry(
        keyword="subscriber ID",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="medical",
        weight=0.50,
        rationale=(
            "Industry variant of NUCC CMS-1500 Box 1a Insured's I.D. Number (1.md:174, 1.md:463)."
        ),
        source_id="nucc_1500_v13",
    ),
]


# search-impl S3 hand-curated additions -- design 02 sections 2.2-2.4.
# 22 new (keyword, category_scope, doctype_scope) cells across three
# financial-document buckets. Source id "self_authored_20260610" is set
# per-Entry so the build output carries it for Jesse's review diff.
# Do NOT re-add already-present cells: due date, statement period,
# transaction date (dob/financial); account number, ein, invoice number,
# po number, routing number, tax id (ssn/financial); branch, merchant,
# n.a. (name/financial).
_MANUAL_S3_FINANCIAL: Final[list[Entry]] = [
    # (dob, financial) — 8 new entries (§2.2)
    Entry(
        keyword="ex-dividend date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.55,
        rationale=(
            "Ex-dividend-date labels appear in brokerage statements;"
            " very specific to equity distributions."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="settlement date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.55,
        rationale="Settlement-date labels appear in brokerage and banking activity records.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="maturity date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.55,
        rationale="Maturity-date labels appear in fixed-income and CD account statements.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="expiration date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.45,
        rationale="Expiration-date labels appear on credit-card statements and benefits notices.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="effective date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.40,
        rationale="Effective-date labels mark when account terms or policies begin.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="issue date",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.35,
        rationale=(
            "Issue-date labels on financial docs mark document or instrument"
            " generation date; weight is lighter because 'issue date' is also"
            " found near personal records."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="tax year",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.50,
        rationale=(
            "Tax-year labels on 1040/W-2/1099 forms are designed to mark"
            " the filing year, not birth year."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="fiscal year",
        aliases=(),
        category_scopes=("dob",),
        doctype_scope="financial",
        weight=0.50,
        rationale="Fiscal-year labels on financial statements mark the accounting period.",
        source_id="self_authored_20260610",
    ),
    # (ssn, financial) — 10 new entries (§2.3)
    Entry(
        keyword="check number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.55,
        rationale="Check-number labels in bank statements hold sequential payment identifiers.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="loan number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.55,
        rationale=(
            "Loan-number labels in mortgage and credit statements hold"
            " account identifiers, not SSNs."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="member number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.50,
        rationale=(
            "Member-number labels in credit-union and insurance statements hold membership IDs."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="claim number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.55,
        rationale="Claim-number labels in insurance statements hold case-tracking IDs, not SSNs.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="control number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.50,
        rationale=(
            "Control-number labels on W-2 (box d) hold the employer-assigned"
            " payroll ID, not the recipient SSN."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="confirmation number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.45,
        rationale=(
            "Confirmation-number labels in transaction records hold transaction-tracking codes."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="employer's ein",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.70,
        rationale=(
            "Employer's EIN label (W-2 box b) is adjacent to the SSN box"
            " (box a); the 9-digit EIN in that position is not an SSN."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="transit number",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.60,
        rationale="Transit-number labels in banking hold ABA routing codes; 9-digit but not SSNs.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="amount due",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.40,
        rationale=(
            "Amount-due labels on billing statements mark payment totals;"
            " adjacent numbers are account identifiers, not SSNs."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="balance due",
        aliases=(),
        category_scopes=("ssn",),
        doctype_scope="financial",
        weight=0.40,
        rationale=(
            "Balance-due labels on billing statements mark outstanding totals;"
            " adjacent numbers are account identifiers, not SSNs."
        ),
        source_id="self_authored_20260610",
    ),
    # (name, financial) — 4 new entries (§2.4)
    Entry(
        keyword="issuer",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.35,
        rationale=(
            "Issuer labels on bond/securities statements hold the issuing"
            " entity's name, not a personal name."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="fund name",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.45,
        rationale="Fund-name labels on investment statements hold mutual fund or ETF names.",
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="institution name",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.45,
        rationale=(
            "Institution-name labels on financial forms identify the holding bank or brokerage."
        ),
        source_id="self_authored_20260610",
    ),
    Entry(
        keyword="plan name",
        aliases=(),
        category_scopes=("name",),
        doctype_scope="financial",
        weight=0.40,
        rationale="Plan-name labels on 401k/403b statements hold the retirement plan sponsor name.",
        source_id="self_authored_20260610",
    ),
]


def scope_keyword(
    keyword: str,
    source_id: str,
) -> list[tuple[str, str, float, str]]:
    """Produce one or more (category, doctype, weight, rationale) rows for a keyword.

    Args:
        keyword: Normalized lowercase keyword.
        source_id: Source identifier matching ``_SOURCE_DEFAULTS``.

    Returns:
        List of scoped rows — one per category_scope the keyword fans out to.
        Returns an empty list when ``keyword`` is in
        ``_AUDIT_REMOVE_PER_SOURCE[source_id]`` per A5 audit.

    Raises:
        PipelineError: If ``source_id`` is unknown.
    """
    if source_id not in _SOURCE_DEFAULTS:
        raise PipelineError(
            f"Unknown negative-context source_id {source_id!r}; known: {sorted(_SOURCE_DEFAULTS)}"
        )

    if keyword in _AUDIT_REMOVE_PER_SOURCE.get(source_id, frozenset()):
        return []

    override = _KEYWORD_OVERRIDES.get(keyword)
    if override is not None:
        doctype, categories, weight, rationale = override
        return [(category, doctype, weight, rationale) for category in categories]

    defaults = _SOURCE_DEFAULTS[source_id]
    rationale = (
        f"Source-default scoping for {source_id}: "
        f"doctype={defaults.doctype_scope}, weight={defaults.default_weight}."
    )
    return [
        (category, defaults.doctype_scope, defaults.default_weight, rationale)
        for category in defaults.category_scopes
    ]


def known_source_ids() -> frozenset[str]:
    """Return the set of accepted source_id values."""
    return frozenset(_SOURCE_DEFAULTS)


def manual_entries() -> list[tuple[str, str, str, float, str, str]]:
    """Yield hand-curated rows as (keyword, category, doctype, weight, source_id, rationale).

    Each :class:`Entry` fans out across its aliases and category_scopes, so
    a single Entry with two aliases and three category_scopes produces
    ``(1 + 2) * 3 = 9`` rows. The output is deterministic: the iteration
    order follows the module-level lists, then alias order, then
    category_scopes order; the build stage sorts the final payload.

    A per-Entry ``source_id`` overrides the bucket-level default
    (``manual_medical`` / ``manual_generic`` / ``manual_audit_part3``) so an
    auditor can grep the candidates JSON for the federal source backing each
    row.

    Returns:
        List of tuples sharing the emission shape used by :func:`scope_keyword`
        plus the keyword and a synthetic source_id for hand-curated rows.
    """
    rows: list[tuple[str, str, str, float, str, str]] = []
    for bucket_source_id, entries in (
        (_MANUAL_MEDICAL_SOURCE_ID, _MANUAL_MEDICAL),
        (_MANUAL_GENERIC_SOURCE_ID, _MANUAL_GENERIC),
        (_MANUAL_AUDIT_PART3_SOURCE_ID, _MANUAL_AUDIT_PART3),
        (_MANUAL_S3_FINANCIAL_SOURCE_ID, _MANUAL_S3_FINANCIAL),
    ):
        for entry in entries:
            surface_forms: tuple[str, ...] = (entry.keyword, *entry.aliases)
            row_source_id = entry.source_id if entry.source_id is not None else bucket_source_id
            for surface in surface_forms:
                for category in entry.category_scopes:
                    rows.append(
                        (
                            surface.lower(),
                            category,
                            entry.doctype_scope,
                            entry.weight,
                            row_source_id,
                            entry.rationale,
                        )
                    )
    return rows
