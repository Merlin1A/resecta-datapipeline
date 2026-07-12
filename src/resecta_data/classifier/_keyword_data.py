"""Hand-curated keyword dictionaries for the five doc-type classes.

Each list holds 30-200 lowercase keywords drawn from common English
vocabulary of the domain. None of the words are drawn from a specific
copyrighted source; they are general-purpose terms anyone working in the
field would recognize. This file does not require a SOURCES.md row because
it contains no licensed content.

Two Swift-side matching facts every entry must respect
(DocumentTypeClassifier.swift, S4 calibration finding 2026-06-11):

- Keywords match by exact single-token equality after lowercased-NFKC
  tokenization (split on non-alphanumeric except ``-`` and ``'``).
  Multi-word keywords therefore never match; prefer single tokens.
- Structural-bonus regexes run against the *lowercased* page text, so
  any pattern containing an uppercase literal or class is dead unless
  it carries ``(?i)``.

Structural bonuses are short regex fragments that mark format-level
signals (for example ``v\\.`` for case captions). Each pattern stays under
the 200-character cap and avoids nested quantifiers per
SEARCH_AND_REDACT.md §9.4.
"""

from __future__ import annotations

from typing import Final

# Court documents: pleadings, orders, glossary-level vocabulary.
COURT_KEYWORDS: Final[tuple[str, ...]] = (
    "affidavit",
    "allegation",
    "appeal",
    "appellant",
    "appellee",
    "arraignment",
    "bailiff",
    "brief",
    "case",
    "civil",
    "complaint",
    "counsel",
    "counterclaim",
    "court",
    "criminal",
    "damages",
    "defendant",
    "deposition",
    "discovery",
    "docket",
    "evidence",
    "exhibit",
    "filing",
    "grievance",
    "hearing",
    "indictment",
    "injunction",
    "judge",
    "judgment",
    "jurisdiction",
    "jury",
    "litigation",
    "magistrate",
    "motion",
    "oath",
    "objection",
    "order",
    "petition",
    "petitioner",
    "plaintiff",
    "plea",
    "pleading",
    "precedent",
    "ruling",
    "statute",
    "subpoena",
    "summons",
    "testimony",
    "verdict",
    "writ",
)

COURT_STRUCTURAL: Final[tuple[tuple[str, str, float, str], ...]] = (
    (
        "case_caption_v",
        r"(?i)\b[A-Z][A-Za-z'\-]+ v\. [A-Z][A-Za-z'\-]+\b",
        0.15,
        "Case-caption 'X v. Y' form is designed to signal a court document.",
    ),
    (
        "docket_number",
        r"(?i)\bNo\.\s*\d{2,}-[A-Z]{1,4}-\d{3,}\b",
        0.10,
        "Docket-number shape ('No. 23-CV-1234') is a common court-filing header.",
    ),
)


# Medical documents: clinical vocabulary, pharmacy, record headers.
# The list combines the original hand-curated clinical vocabulary with two
# federal / NLM-sourced additions introduced in C7:
#   - 25 physician-specialty titles derived from the BLS SOC 2018 structure
#     (29-1200 series "Physicians and Surgeons"), Public Domain per
#     17 U.S.C. § 105. Source file: gazetteers/institutions/sources/
#     bls_soc_2018.xlsx (SOURCES.md row bls_soc_2018).
#   - ~50 top-level MeSH-derived medical category words from the NLM MeSH
#     release. NLM MeSH is redistributable under the NLM MeSH Terms &
#     Conditions (pd-allowlist-clean per decision M19). The NOTICE
#     acknowledgment is routed to Jesse.
MEDICAL_KEYWORDS: Final[tuple[str, ...]] = (
    "admission",
    "allergy",
    "anatomy",
    "anesthesia",
    "anesthesiologist",
    "antibiotic",
    "antibody",
    "antigen",
    "assessment",
    "bacteria",
    "biochemistry",
    "biopsy",
    "blood pressure",
    "cancer",
    "carcinoma",
    "cardiologist",
    "cardiology",
    "cardiovascular",
    "chart",
    "clinic",
    "clinician",
    "dermatologist",
    "diagnosis",
    "discharge",
    "dna",
    "dosage",
    "dose",
    "emergency",
    "endocrine",
    "endocrinologist",
    "enzyme",
    "epidemiology",
    "examination",
    "fracture",
    "gastroenterologist",
    "gastrointestinal",
    "gene",
    "geneticist",
    "gland",
    "gynecologist",
    "hematologic",
    "hematologist",
    "hepatic",
    "history",
    "hormone",
    "hospital",
    "imaging",
    "immune",
    "immunologist",
    "immunology",
    "infection",
    "infectious",
    "inflammation",
    "injection",
    "inpatient",
    "intake",
    "laboratory",
    "lesion",
    "lymph",
    "malignant",
    "medication",
    "metabolic",
    "microbiology",
    "muscle",
    "musculoskeletal",
    "neonatologist",
    "neoplasm",
    "nephrologist",
    "neurological",
    "neurologist",
    "nurse",
    "obstetrician",
    "oncologist",
    "oncology",
    "operative",
    "ophthalmologist",
    "organ",
    "orthopedics",
    "orthopedist",
    "otolaryngologist",
    "outpatient",
    "pathology",
    "patient",
    "pediatrician",
    "pharmacology",
    "pharmacy",
    "physician",
    "physiology",
    "podiatrist",
    "prescription",
    "procedure",
    "progress note",
    "protein",
    "psychiatrist",
    "pulmonary",
    "pulmonologist",
    "radiologist",
    "radiology",
    "referral",
    "refill",
    "renal",
    "respiratory",
    "rheumatologist",
    "soap note",
    "specimen",
    "surgeon general",
    "surgery",
    "symptom",
    "syndrome",
    "tablet",
    "therapy",
    "tissue",
    "toxicology",
    "treatment",
    "triage",
    "tumor",
    "urologist",
    "vaccine",
    "virus",
    "vital signs",
    "vitals",
    "vitamin",
    "ward",
)

MEDICAL_STRUCTURAL: Final[tuple[tuple[str, str, float, str], ...]] = (
    (
        "icd_shape",
        r"(?i)\b[A-TV-Z]\d{2}(?:\.\d{1,4})?\b",
        0.10,
        "ICD-10-shaped code in running text is a clinical-document signal.",
    ),
    (
        "vitals_bp",
        r"(?i)\bBP\s?\d{2,3}/\d{2,3}\b",
        0.08,
        "Blood-pressure 'BP 120/80' form is a clinical-note signal.",
    ),
    (
        "drug_dosage",
        r"(?i)\b\d+(?:\.\d+)?\s*(?:mg|mcg|ng|g|ml|iu|units?)\b",
        0.08,
        "Drug-dosage form like '250mg' or '5 ml' is a clinical-prescription signal.",
    ),
    (
        "icd_3char_prefix",
        r"(?i)\b[A-TV-Z][0-9]{2}\b",
        0.10,
        "ICD-10-CM 3-character section prefix like 'E10' or 'I10' is a clinical-document signal.",
    ),
)


# Financial documents: invoices, statements, banking and accounting vocab,
# plus tax-form/payroll vocabulary (S4 2026-06-11: the original list was
# invoice-flavored, so W-2s lost to the generic class — see
# session-04b-finding-generic-absorption.md). Single tokens only; 'w-2'
# survives tokenization because '-' is kept in tokens.
FINANCIAL_KEYWORDS: Final[tuple[str, ...]] = (
    "account",
    "accrual",
    "amount",
    "audit",
    "balance",
    "bank",
    "billing",
    "checking",
    "compensation",
    "credit",
    "currency",
    "customer",
    "debit",
    "deduction",
    "deposit",
    "discount",
    "dividend",
    "due date",
    "earnings",
    "employee",
    "employer",
    "expense",
    "fee",
    "fiscal",
    "income",
    "interest",
    "invoice",
    "irs",
    "ledger",
    "loan",
    "merchant",
    "mortgage",
    "overdraft",
    "payable",
    "payment",
    "payroll",
    "principal",
    "purchase",
    "receipt",
    "receivable",
    "refund",
    "remittance",
    "revenue",
    "routing",
    "salary",
    "savings",
    "statement",
    "subtotal",
    "tax",
    "taxable",
    "total",
    "transaction",
    "transfer",
    "vendor",
    "w-2",
    "wages",
    "wire",
    "withdrawal",
    "withholding",
)

FINANCIAL_STRUCTURAL: Final[tuple[tuple[str, str, float, str], ...]] = (
    (
        "currency_amount",
        r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?",
        0.08,
        "US-dollar amount with optional thousands separators is a financial-document signal.",
    ),
    (
        "invoice_label",
        r"(?i)\b(?:Invoice|Statement|Receipt)\s*(?:No\.?|#)\s?\w+\b",
        0.10,
        "Invoice / Statement / Receipt header form is a financial-document signal.",
    ),
)


# FOIA / public-records documents: request correspondence vocabulary.
FOIA_KEYWORDS: Final[tuple[str, ...]] = (
    "agency",
    "appeal",
    "ask",
    "classified",
    "correspondence",
    "custodian",
    "declassified",
    "deliberative",
    "disclosure",
    "exemption",
    "fee waiver",
    "foia",
    "freedom of information",
    "glomar",
    "gov",
    "government",
    "information",
    "investigation",
    "law enforcement",
    "letter",
    "memorandum",
    "narrow",
    "officer",
    "open records",
    "personnel",
    "privacy",
    "privileged",
    "privilege log",
    "production",
    "public",
    "public records",
    "records",
    "redacted",
    "redaction",
    "release",
    "release in full",
    "release in part",
    "request",
    "requester",
    "responsive",
    "reviewed",
    "scope",
    "search",
    "sensitive",
    "sunshine",
    "transparency",
    "withheld",
    "withholding",
)

FOIA_STRUCTURAL: Final[tuple[tuple[str, str, float, str], ...]] = (
    (
        "exemption_citation",
        r"\b\(b\)\(\d\)\b",
        0.12,
        "FOIA exemption citation form like '(b)(6)' is a public-records signal.",
    ),
    (
        "foia_header",
        r"(?i)\bFOIA\s+(?:Request|Response|No\.)\b",
        0.10,
        "FOIA Request / Response / No. heading is a FOIA-document signal.",
    ),
)


# Generic class: correspondence / memo / office-circular vocabulary.
# S4 2026-06-11 rewrite (session-04b-finding-generic-absorption.md): the
# original list was universal document furniture ('address', 'date',
# 'email', 'name', 'number', 'page', 'phone', ...) that any header block
# matches, so generic capped out on nearly every document and absorbed
# all medical and W-2 docs. Terms here must be characteristic of plain
# correspondence and must NOT appear in routine headers of the other
# four classes. Structural bonus list stays deliberately empty.
GENERIC_KEYWORDS: Final[tuple[str, ...]] = (
    "acknowledge",
    "agenda",
    "announcement",
    "appreciate",
    "approve",
    "attachment",
    "author",
    "business",
    "client",
    "company",
    "confidential",
    "cordially",
    "courtesy",
    "department",
    "draft",
    "enclosed",
    "folder",
    "forward",
    "greetings",
    "inquiry",
    "invitation",
    "letter",
    "meeting",
    "memo",
    "message",
    "newsletter",
    "paragraph",
    "regarding",
    "regards",
    "reminder",
    "reply",
    "sincerely",
)

GENERIC_STRUCTURAL: Final[tuple[tuple[str, str, float, str], ...]] = ()


CANONICAL_CLASSES: Final[tuple[str, ...]] = (
    "court",
    "medical",
    "financial",
    "foia",
    "generic",
)

KEYWORDS_BY_CLASS: Final[dict[str, tuple[str, ...]]] = {
    "court": COURT_KEYWORDS,
    "medical": MEDICAL_KEYWORDS,
    "financial": FINANCIAL_KEYWORDS,
    "foia": FOIA_KEYWORDS,
    "generic": GENERIC_KEYWORDS,
}

STRUCTURAL_BY_CLASS: Final[dict[str, tuple[tuple[str, str, float, str], ...]]] = {
    "court": COURT_STRUCTURAL,
    "medical": MEDICAL_STRUCTURAL,
    "financial": FINANCIAL_STRUCTURAL,
    "foia": FOIA_STRUCTURAL,
    "generic": GENERIC_STRUCTURAL,
}
