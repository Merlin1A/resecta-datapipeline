"""Swift<->Python context-feature parity (Python side).

The C1 scorer's 13 features must be computed identically wherever they are
produced: the Swift seam builder `contextFeatures(match:doctype:effectiveDoctype:
pageText:)` (RedactionEngine `Detection/Scoring/ContextFeatures.swift`), the
File-5 fire dump, and any Python feature reconstruction. This module ports that
Swift builder over UTF-16 code units: loc, length, and every distance are
NSString offsets, and `_u16` expands a supplementary scalar to its two surrogate
units, so for ASCII input the two ports agree by construction and for non-ASCII
input they still agree on `nearest_*_distance`, `at_line_start`, and the window.
The GOLDEN vectors below are the language-agnostic feature contract; the
canonical unit is Swift/UTF-16 (the device is ground truth).

The iOS suite `ContextFeatureParityTests` asserts the SAME cases produce the
SAME golden vectors by calling the real Swift `contextFeatures(...)`. Both sides
green ⇒ Swift and Python agree on the 13-feature contract; a half-updated pair
reds one suite. Keyword sets are copied VERBATIM from the shipped Swift detector
profiles (already lowercased in ContextFeatureKeywords.sets via .lowercased()).
"""

from __future__ import annotations

import unicodedata

import pytest

FEATURE_ORDER = (
    "kw_positive_window",
    "kw_negative_window",
    "nearest_positive_distance",
    "nearest_negative_distance",
    "digit_run_length",
    "has_separator",
    "left_is_label",
    "at_line_start",
    "doctype_is_court",
    "doctype_is_medical",
    "doctype_is_financial",
    "doctype_is_foia",
    "doctype_is_generic",
)
_DOCTYPES = ("court", "medical", "financial", "foia", "generic")

# Verbatim from the shipped Swift profiles (AccountDetector.positiveKeywords;
# PIIDetector.phoneContextKeywords / phoneNegativeKeywords / einProfile /
# itinProfile; MRNContextKeywords.profile), lowercased to match
# ContextFeatureKeywords.sets(for:).
_KEYWORDS: dict[str, tuple[list[str], list[str]]] = {
    "account": (["account", "account number", "account #", "acct", "acct #", "acct.", "a/c"], []),
    "phone": (
        [
            "phone",
            "tel",
            "fax",
            "call",
            "contact",
            "mobile",
            "cell",
            "dial",
            "sms",
            "text",
            "reach",
            "voicemail",
            "ext",
            "extension",
            "number",
        ],
        [
            "case no",
            "case #",
            "case number",
            "docket no",
            "docket #",
            "ref #",
            "ref no",
            "reference no",
            "reference #",
            "reference number",
            "claim no",
            "claim #",
            "invoice no",
            "invoice #",
            "order no",
            "order #",
            "account no",
            "account #",
            "policy no",
            "policy #",
            "file no",
            "file #",
        ],
    ),
    "mrn": (
        [
            "patient",
            "medical record",
            "mrn",
            "mr#",
            "chart",
            "chart number",
            "dob",
            "date of birth",
            "admission",
            "discharge",
            "diagnosis",
            "physician",
            "hospital",
        ],
        [
            "invoice",
            "order",
            "order no",
            "order #",
            "receipt",
            "transaction",
            "purchase",
            "sku",
            "item",
            "shipping",
            "tracking",
        ],
    ),
    "ein": (
        [
            "ein",
            "employer identification",
            "employer id",
            "federal tax id",
            "fein",
            "federal ein",
            "payer's tin",
            "payer tin",
            "recipient tin",
            "box b",
            "employer's ein",
            "taxpayer id",
            "tax id number",
            "irs form",
            "w-2",
            "1099",
            "schedule c",
            "employer",
        ],
        [],
    ),
    "itin": (
        [
            "itin",
            "individual taxpayer identification",
            "individual taxpayer id",
            "tax identification number",
            "w-7",
            "irs form w-7",
            "taxpayer identification",
            "tin",
        ],
        [],
    ),
}


def _u16(s: str) -> list[str]:
    # One element per UTF-16 code unit (a supplementary scalar -> its two surrogate
    # halves), so indices into "".join(_u16(s)) equal the NSString `.location`
    # values the Swift builder measures with.
    units = s.encode("utf-16-le")
    return [units[i : i + 2].decode("utf-16-le", "surrogatepass") for i in range(0, len(units), 2)]


def _is_swift_whitespace(ch: str) -> bool:
    # Swift `CharacterSet.whitespaces` = Unicode general category "Zs" + CHARACTER
    # TABULATION (U+0009). NBSP (U+00A0) is "Zs" and so counts; newlines are NOT in
    # this set (at_line_start tests them separately, as Swift does).
    return ch == "\t" or unicodedata.category(ch) == "Zs"


def _window(u16: str, loc: int, length: int, radius: int = 5) -> str:
    # u16 is the UTF-16-unit-indexed text (see _u16); loc/length are UTF-16 offsets.
    before = u16[max(0, loc - 200) : loc]
    relevant_before = " ".join(before.split()[-radius:])
    after_start = loc + length
    after_len = min(200, len(u16) - after_start)
    after = u16[after_start : after_start + after_len]
    relevant_after = " ".join(after.split()[:radius])
    return relevant_before + " " + relevant_after


def _nearest(nbhd_lower: str, match_start: int, match_end: int, keywords: list[str]) -> float:
    # nbhd_lower is UTF-16-unit indexed, so .find offsets and len are UTF-16 units,
    # exactly as the Swift NSString.range / location arithmetic measures the gap.
    best: int | None = None
    for kw in keywords:
        if not kw:
            continue
        i = 0
        while i < len(nbhd_lower):
            idx = nbhd_lower.find(kw, i)
            if idx == -1:
                break
            kw_start, kw_end = idx, idx + len(kw)
            if kw_end <= match_start:
                gap = match_start - kw_end
            elif kw_start >= match_end:
                gap = kw_start - match_end
            else:
                gap = 0
            if best is None or gap < best:
                best = gap
            i = kw_end if kw_end > i else i + 1
    if best is None:
        return 0.0
    return 1.0 / (1.0 + best / 10.0)


def context_features(
    text: str, loc: int, length: int, family: str, eff_doctype: str
) -> list[float]:
    """Faithful port of Swift `contextFeatures(...)`.

    loc/length are NSString UTF-16 offsets. The text is expanded to one element
    per UTF-16 code unit (_u16) and rejoined, so every slice / search / back-walk
    below indexes UTF-16 units — matching the Swift builder bit-for-bit on
    non-ASCII input as well as ASCII.
    """
    u16 = "".join(_u16(text))
    positives, negatives = _KEYWORDS.get(family, ([], []))
    window = _window(u16, loc, length).lower()
    kw_pos = 1.0 if any(p in window for p in positives) else 0.0
    kw_neg = 1.0 if any(n in window for n in negatives) else 0.0
    nb_start = max(0, loc - 200)
    nb_end = min(len(u16), loc + length + 200)
    nbhd = u16[nb_start:nb_end].lower()
    ms, me = loc - nb_start, loc + length - nb_start
    near_pos = _nearest(nbhd, ms, me, positives)
    near_neg = _nearest(nbhd, ms, me, negatives)
    match_text = u16[loc : loc + length]
    digit_run = float(sum(1 for c in match_text if c in "0123456789"))
    has_sep = 1.0 if any(not (c.isalpha() or c.isdigit()) for c in match_text) else 0.0
    left = u16[max(0, loc - 200) : loc]
    left_label = 0.0
    if left:
        toks = left.split()
        if toks and (toks[-1].endswith(":") or toks[-1].endswith("#")):
            left_label = 1.0
    i, at_line_start = loc - 1, 1.0
    while i >= 0:
        ch = u16[i]
        if ch in ("\n", "\r"):
            at_line_start = 1.0
            break
        if _is_swift_whitespace(ch):
            i -= 1
            continue
        at_line_start = 0.0
        break
    one_hot = [1.0 if eff_doctype == d else 0.0 for d in _DOCTYPES]
    return [
        kw_pos,
        kw_neg,
        near_pos,
        near_neg,
        digit_run,
        has_sep,
        left_label,
        at_line_start,
        *one_hot,
    ]


# name, text, match-loc, match-len, family (wireName), effectiveDoctype, golden 13-vector.
# Golden = ground truth shared byte-for-byte with the iOS ContextFeatureParityTests.
_P = 0.8333333333333334  # 1/(1 + 2/10)
_Q = 0.9090909090909091  # 1/(1 + 1/10)
_R = 0.7142857142857143  # 1/(1 + 4/10)
CASES = [
    (
        "account/financial",
        "Account: 1234567890",
        9,
        10,
        "account",
        "financial",
        [1.0, 0.0, _P, 0.0, 10.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
    ),
    (
        "phone/generic-neg",
        "Case No: 5551234567 filed",
        9,
        10,
        "phone",
        "generic",
        [0.0, 1.0, 0.0, _P, 10.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ),
    (
        "mrn/medical-linestart-sep",
        "Patient chart\nMR-9988776",
        14,
        10,
        "mrn",
        "medical",
        [1.0, 0.0, _Q, 0.0, 7.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0],
    ),
    (
        "account/court",
        "Acct #: 0001112223",
        8,
        10,
        "account",
        "court",
        [1.0, 0.0, _P, 0.0, 10.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
    ),
    (
        "ein/foia-sep",
        "EIN: 12-3456789",
        5,
        10,
        "ein",
        "foia",
        [1.0, 0.0, _P, 0.0, 9.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    ),
    # D09-pipeline-parity-F2 — non-ASCII parity cases. loc/length are NSString
    # UTF-16 offsets (reproduced by _u16); goldens are byte-identical to the iOS
    # ContextFeatureParityTests A1/A2/A3 (a half-updated pair reds one suite).
    # A1: BMP accent + em-dash AFTER the match — offsets stay codepoint-aligned.
    (
        "account/bmp-accent",
        "Acct: 1234567890 \u2014 M\u00fcller",
        6,
        10,
        "account",
        "generic",
        [1.0, 0.0, _P, 0.0, 10.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ),
    # A2: NBSP (U+00A0) between a newline and the match — at_line_start = 1
    # (CharacterSet.whitespaces includes NBSP; a (" ","\t")-only port would read 0).
    (
        "phone/nbsp-linestart",
        "Tel\n\u00a05551234567",
        5,
        10,
        "phone",
        "generic",
        [1.0, 0.0, _P, 0.0, 10.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ),
    # A3: a supplementary scalar (U+10437, 2 UTF-16 units) inside the keyword->match
    # gap — nearest_positive = 1/(1+4/10); a codepoint-indexed port mis-measures it.
    (
        "account/supplementary",
        "Acct: \U000104371234567890",
        8,
        10,
        "account",
        "financial",
        [1.0, 0.0, _R, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
    ),
]


@pytest.mark.parametrize(
    "name,text,loc,length,family,doctype,golden", CASES, ids=[c[0] for c in CASES]
)
def test_context_feature_parity(
    name: str, text: str, loc: int, length: int, family: str, doctype: str, golden: list[float]
) -> None:
    assert text[loc : loc + length], name  # the slice is the intended match span
    got = context_features(text, loc, length, family, doctype)
    assert len(got) == len(FEATURE_ORDER) == 13
    for g, want, feat in zip(got, golden, FEATURE_ORDER, strict=True):
        assert g == pytest.approx(want, abs=1e-9), f"{name}:{feat}"
