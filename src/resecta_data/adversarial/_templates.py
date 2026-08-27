"""Static seed data for the adversarial pattern builder.

Kept separate from the generator so that future additions are an append-only
diff rather than a code-plus-data change.
"""

from __future__ import annotations

from typing import Final

# Bounding-box tuple used by column-header-label entries.
# Layout: (x, y, w, h, role) where role is "header" or "data".
_BBox = tuple[float, float, float, float, str]

# Detector-shape false positives: strings that a naive shape-check would redact
# but SHOULD NOT be redacted.
#
# Each tuple is (text, expected_detector, expected_outcome, provenance).
DETECTOR_SHAPE_FP: Final[tuple[tuple[str, str, str, str], ...]] = (
    # Phone numbers shaped like SSNs (tel with dashes).
    (
        "Call 555-12-3456 for support.",
        "ssn",
        "suppress",
        "detector-shape false positive: phone number shaped like an SSN",
    ),
    (
        "Our office phone is 800-55-1212.",
        "ssn",
        "suppress",
        "detector-shape false positive: phone number shaped like an SSN",
    ),
    # Account numbers in financial contexts (9-digit sequences surrounded by
    # currency cues).
    (
        "Account 123456789 credit $1,200.00 posted.",
        "ssn",
        "suppress",
        "detector-shape false positive: account number in a currency context shaped like an SSN",
    ),
    # Ten-digit phone numbers shaped like NPIs.
    (
        "Please call (206) 555-0199 for questions.",
        "npi",
        "suppress",
        "detector-shape false positive: ten-digit phone number shaped like an NPI",
    ),
    (
        "Fax: 4155551234 (no dashes).",
        "npi",
        "suppress",
        "detector-shape false positive: ten-digit phone number shaped like an NPI",
    ),
    # License plate shaped like DEA (two letters + digits) but in a transport
    # context.
    (
        "Vehicle plate AB1234567 impounded at lot 4.",
        "dea",
        "suppress",
        "detector-shape false positive: license plate shaped like a DEA number",
    ),
    (
        "Tracking number US9876543 in transit.",
        "dea",
        "suppress",
        "detector-shape false positive: tracking number shaped like a DEA number",
    ),
    # Addresses: a ZIP-shaped five-digit number inside an order total.
    (
        "Order total 49201 items, ready to ship.",
        "address",
        "suppress",
        "detector-shape false positive: ZIP-shaped five-digit number inside an order total",
    ),
    (
        "Unit 12345 reporting: all clear.",
        "address",
        "suppress",
        "detector-shape false positive: five-digit unit number shaped like a ZIP",
    ),
    # Hexadecimal colors / identifiers shaped like nothing in particular but
    # historically triggered naive regex paths.
    (
        "Theme color: #A1B2C3 across the dashboard.",
        "none",
        "suppress",
        "detector-shape false positive: hexadecimal color identifier",
    ),
    # Docket numbers shaped like SSN in a legal context.
    (
        "Case 078-05-1120 docket entry 42.",
        "ssn",
        "flag",
        "docket number shaped like an SSN in a legal context; route to review",
    ),
)


# Classifier keyword-stuffing stanzas. Each row is a blob of
# keyword-dense text that tries to over-weight a single document class.
# The detector stack is meant to cap frequency weighting so one stanza cannot
# dominate classification.
#
# Each tuple is (text, expected_detector, expected_outcome, provenance).
# expected_detector is "none" for classifier fragments; expected_outcome is
# "flag" when we want the document routed to review rather than redacted.
KEYWORD_STUFFING_G5: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "docket plaintiff case docket docket docket court filing docket "
        "docket plaintiff case plaintiff docket motion docket plaintiff "
        "case docket plaintiff docket plaintiff case docket plaintiff",
        "none",
        "flag",
        "classifier keyword-stuffing stanza: keyword-dense text aimed at one class",
    ),
    (
        "patient diagnosis mrn patient diagnosis mrn patient diagnosis mrn "
        "patient diagnosis mrn patient diagnosis mrn patient diagnosis mrn "
        "patient patient patient diagnosis diagnosis diagnosis mrn mrn mrn",
        "none",
        "flag",
        "classifier keyword-stuffing stanza: keyword-dense text aimed at one class",
    ),
    (
        "invoice account balance invoice account balance invoice account "
        "balance invoice account balance invoice account balance invoice "
        "account balance transaction transaction transaction invoice",
        "none",
        "flag",
        "classifier keyword-stuffing stanza: keyword-dense text aimed at one class",
    ),
    (
        "foia request foia request foia request foia request foia request "
        "document released document released document released foia request "
        "foia request foia request redacted exemption foia request document",
        "none",
        "flag",
        "classifier keyword-stuffing stanza: keyword-dense text aimed at one class",
    ),
    (
        "patient docket invoice foia patient docket invoice foia patient "
        "docket invoice foia patient docket invoice foia patient docket "
        "invoice foia patient docket invoice foia patient docket invoice",
        "none",
        "flag",
        "classifier keyword-stuffing stanza: keyword-dense text aimed at one class",
    ),
)


# Homoglyph entries: labels or digit fields laced with visually-similar
# non-ASCII codepoints. Entries whose NFKD form reduces to pure ASCII are
# expected to still redact after normalization; entries where the homoglyph
# breaks the detectable shape are expected to suppress.
#
# Each tuple is (text, expected_detector, expected_outcome, provenance).
HOMOGLYPH_ENTRIES: Final[tuple[tuple[str, str, str, str], ...]] = (
    # Fullwidth digits (U+FF10..U+FF19) decompose to ASCII digits under NFKD.
    (
        "SSN: \uff11\uff12\uff13-\uff14\uff15-\uff16\uff17\uff18\uff19",
        "ssn",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    (
        "NPI: \uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10 registered.",
        "npi",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Mathematical bold digits (U+1D7CE..U+1D7D7) also NFKD-normalize to ASCII.
    (
        "Member SSN "
        "\U0001d7cf\U0001d7d0\U0001d7d1-\U0001d7d2\U0001d7d3"
        "-\U0001d7d4\U0001d7d5\U0001d7d6\U0001d7d7 on file.",
        "ssn",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Cyrillic capital letter Dze (U+0405) substituted for Latin S in the
    # label. Shape-only detectors should suppress because the label no longer
    # matches "SSN".
    (
        "S\u0405N: 555-00-1234 entry",
        "ssn",
        "suppress",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Cyrillic lowercase 'a' (U+0430) in the label word "account". Digits are
    # plain ASCII and shape-valid; detector should still redact.
    (
        "ITIN \u0430ccount 9XX-82-1234 closed.",
        "ssn",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Greek capital iota (U+0399) in the label; Woolworth sentinel in body.
    (
        "Greek \u0399-label 078-05-1120 noted.",
        "ssn",
        "flag",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Zero-width joiner between digits; greedy digit regex may split.
    (
        "SSN: 1\u200d23-45-6789 (ZWJ in digits)",
        "ssn",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Zero-width non-joiner inside DEA license.
    (
        "DEA #: AB1\u200c234567 (ZWNJ in license)",
        "dea",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Zero-width space inside NPI.
    (
        "NPI 12\u200b3456789\u200b0 registered.",
        "npi",
        "redact",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
    # Cyrillic capital A (U+0410) at DEA prefix position breaks ASCII-letter
    # shape; hardened detector should suppress.
    (
        "Reference \u0410b1234567 logged.",
        "dea",
        "suppress",
        "homoglyph fragment: label or digits laced with look-alike non-ASCII codepoints",
    ),
)


# Invisible-style entries: styled HTML fragments where the PII text is
# visually suppressed (white-on-white, zero opacity, tiny font). Detector
# operates on text content, so it should still redact.
#
# Each tuple is (text, expected_detector, expected_outcome, provenance).
INVISIBLE_STYLE_ENTRIES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        '<span style="color:#ffffff">SSN: 123-45-6789</span>',
        "ssn",
        "redact",
        "invisible-style fragment: PII text visually suppressed by styling but present as text",
    ),
    (
        '<div style="color: white; background: white">NPI: 1234567890</div>',
        "npi",
        "redact",
        "invisible-style fragment: PII text visually suppressed by styling but present as text",
    ),
    (
        '<p style="font-size:0.5pt">DEA AB1234567 pending.</p>',
        "dea",
        "redact",
        "invisible-style fragment: PII text visually suppressed by styling but present as text",
    ),
    (
        '<span style="opacity:0">Card 4111 1111 1111 1111 queued.</span>',
        "none",
        "flag",
        "invisible-style fragment: PII text visually suppressed by styling but present as text",
    ),
)


# Whitespace-injection entries: zero-width spaces or similar Unicode
# whitespace breaking a digit run. Hardened detector strips ZW-* before
# regex; naive detector would suppress.
#
# Each tuple is (text, expected_detector, expected_outcome, provenance).
WHITESPACE_INJECTION_ENTRIES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "SSN 1\u200b23-45-6789 on record.",
        "ssn",
        "redact",
        "whitespace-injection fragment: zero-width Unicode whitespace inside a digit run",
    ),
    (
        "NPI 12345\u200b67890 billed.",
        "npi",
        "redact",
        "whitespace-injection fragment: zero-width Unicode whitespace inside a digit run",
    ),
    # Woolworth sentinel with an injected ZW-space; should still route to
    # review via the sentinel path.
    (
        "Account 078\u200b-05-1120 on ledger.",
        "ssn",
        "flag",
        "whitespace-injection fragment: zero-width Unicode whitespace inside a digit run",
    ),
    # Phone digits with ZW-space shaped like SSN; suppress under context.
    (
        "Phone 800-55\u200b-1212 no redact.",
        "ssn",
        "suppress",
        "whitespace-injection fragment: zero-width Unicode whitespace inside a digit run",
    ),
)


# Multi-line date collision entries: adjacent date lines with matching
# MM/DD that can confuse the DOB detector into latching onto a filing
# date. Expected outcome is flag (route to review) for the ambiguous
# adjacencies.
#
# Each tuple is (text, expected_detector, expected_outcome, provenance).
MULTILINE_DATE_ENTRIES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "Filed: 03/15/2024\nDOB: 03/15/1984",
        "none",
        "flag",
        "multi-line date collision: adjacent date lines with matching MM/DD next to a DOB label",
    ),
    (
        "Application received 2024-01-15\nDate of Birth: 2024-01-15",
        "none",
        "flag",
        "multi-line date collision: adjacent date lines with matching MM/DD next to a DOB label",
    ),
    (
        "Signed 12/31/2023\nBirth 12/31/1955\nExpires 12/31/2028",
        "none",
        "flag",
        "multi-line date collision: adjacent date lines with matching MM/DD next to a DOB label",
    ),
    (
        "Order date 01/01/2025\nDOB 01/01/2000\nDue 02/01/2025",
        "none",
        "flag",
        "multi-line date collision: adjacent date lines with matching MM/DD next to a DOB label",
    ),
    (
        "Filing stamp 06-15-24\nPatient DOB 06-15-1960",
        "none",
        "flag",
        "multi-line date collision: adjacent date lines with matching MM/DD next to a DOB label",
    ),
)


# Column-header-label entries: text fragments paired with bbox metadata
# describing a table-header row and one or more data-row bboxes. The
# detector is expected to resolve the label from the header bbox and
# apply redaction policy to values in the data bbox(es).
#
# Each tuple is (text, expected_detector, expected_outcome, provenance,
# bbox_context). bbox_context is a tuple of (x, y, w, h, role).
# Bbox coordinates are in PDF points (72 dpi) relative to the page
# top-left; values are arbitrary but stable across rebuilds.
COLUMN_HEADER_ENTRIES: Final[tuple[tuple[str, str, str, str, tuple[_BBox, ...]], ...]] = (
    (
        "SSN\n123-45-6789",
        "ssn",
        "redact",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        ((0.0, 0.0, 80.0, 18.0, "header"), (0.0, 20.0, 80.0, 18.0, "data")),
    ),
    (
        "NPI\n1234567890",
        "npi",
        "redact",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        ((90.0, 0.0, 110.0, 18.0, "header"), (90.0, 20.0, 110.0, 18.0, "data")),
    ),
    (
        "DEA\nAB1234567",
        "dea",
        "redact",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        ((210.0, 0.0, 90.0, 18.0, "header"), (210.0, 20.0, 90.0, 18.0, "data")),
    ),
    (
        "ZIP\n94103",
        "address",
        "redact",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        ((310.0, 0.0, 60.0, 18.0, "header"), (310.0, 20.0, 60.0, 18.0, "data")),
    ),
    # "Amount" header over an SSN-shaped numeric; suppress because the
    # header disambiguates this as a currency value.
    (
        "Amount\n123-45-6789",
        "ssn",
        "suppress",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        ((0.0, 40.0, 100.0, 18.0, "header"), (0.0, 60.0, 100.0, 18.0, "data")),
    ),
    # Two stacked data rows under one header.
    (
        "Patient SSN\n123-45-6789\n987-65-4321",
        "ssn",
        "redact",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        (
            (0.0, 80.0, 120.0, 18.0, "header"),
            (0.0, 100.0, 120.0, 18.0, "data"),
            (0.0, 120.0, 120.0, 18.0, "data"),
        ),
    ),
    # Woolworth sentinel under an SSN header.
    (
        "SSN\n078-05-1120",
        "ssn",
        "flag",
        "column-header-label fragment: a table header row paired with data-row bounding boxes",
        ((0.0, 140.0, 80.0, 18.0, "header"), (0.0, 160.0, 80.0, 18.0, "data")),
    ),
)
