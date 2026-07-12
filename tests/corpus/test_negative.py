"""Determinism + schema + PII-absence checks for the negative-text corpus.

The corpus is the S3 baseline document-level FP surface (CONTRACT.md
"Negative corpus fixture format"). It must contain NO ground-truth PII: any
detection the engine surfaces against it is a false positive. These tests pin
that invariant with regex guards mirroring ``resecta-sample-doc/verify.py``
(no SSN-shaped string, phones only in the reserved 555-01xx range, RFC-2606
domains only, 100% printable ASCII), plus the standard byte-determinism and
schema checks.

The corpus carries two flavors: keyword-only paragraphs with no numerals, and
number-shaped-but-invalid paragraphs that plant the family trigger-keywords
next to SHORT numeric content so the *structural* detectors (account / phone /
routing / EIN) are exercised on number-shaped NON-PII. The hard safety rule
that keeps the latter a true negative is mechanical: **no digit run reaches
length 5**, so no SSN (``\\d{3}\\d{2}\\d{4}``), phone (``\\d{10}``), account
(``\\d{10}``), routing (``\\d{9}`` + mod-10), EIN (``\\d{2}\\d{7}``), ITIN, or
credit-card shape can parse. Only runs of length 1-4 appear. ``test_no_digit_
run_of_length_5_or_more`` pins this, and ``test_short_digit_run_stressor_
present`` asserts the stressor is actually there.

The artifact is dev/eval only -- not installed to the Swift Resources path.
"""

from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.mechanism_language import scan_text
from resecta_data.common.schema import validate_file
from resecta_data.corpus.negative import build

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"

_EXPECTED_DOC_COUNT = 150
# 30 documents per doctype: 20 keyword-only + 10 number-shaped-but-invalid.
_EXPECTED_PER_DOCTYPE = 30
# At least this many documents must carry a short digit run (the structural-FP
# stressor). The 10 number-shaped paragraphs per doctype each contain one.
_MIN_DIGIT_STRESSOR_DOCS = 50
_DOCTYPES = ("court", "medical", "financial", "foia", "generic")

# --- PII-shape guards (mirror resecta-sample-doc/verify.py) ------------------

# SSN-shaped: NNN-NN-NNNN or NNN NN NNNN.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{3}\s\d{2}\s\d{4}\b")
# Any phone-shaped string (loose), so we can assert the reserved-range rule.
_PHONE_RE = re.compile(r"(?:1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
# Any domain-shaped token (label.tld), so we can assert RFC-2606 only.
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_RFC2606_DOMAINS = {"example.com", "example.net", "example.org", "example.edu"}


def _corpus_text(payload: dict[str, Any]) -> str:
    return "\n".join(doc["text"] for doc in payload["documents"])


def _digit_runs(text: str) -> list[str]:
    return re.findall(r"\d+", text)


# --- shape / structure -------------------------------------------------------


def test_doc_count_and_per_doctype_split() -> None:
    payload = build()
    docs = payload["documents"]
    assert len(docs) == _EXPECTED_DOC_COUNT
    counts = {dt: sum(1 for d in docs if d["doctype"] == dt) for dt in _DOCTYPES}
    # Even 30-per-doctype split across the five classes (20 keyword-only + 10
    # number-shaped).
    assert counts == dict.fromkeys(_DOCTYPES, _EXPECTED_PER_DOCTYPE)
    # Every document's doctype is one of the five.
    assert all(d["doctype"] in _DOCTYPES for d in docs)


def test_ids_well_formed_unique_and_sorted() -> None:
    payload = build()
    ids = [d["id"] for d in payload["documents"]]
    assert all(re.fullmatch(r"neg_\d{6}", i) for i in ids)
    assert len(set(ids)) == len(ids), "ids must be unique"
    assert ids == sorted(ids), "documents must be sorted by id"
    # Contiguous neg_000000 .. neg_000149.
    assert ids == [f"neg_{n:06d}" for n in range(_EXPECTED_DOC_COUNT)]


def test_top_level_fields() -> None:
    payload = build()
    assert payload["version"] == 1
    assert payload["generated_by"] == "resecta_data.corpus.negative.generate"
    assert payload["seed"] == CANONICAL_SEED


def test_paragraph_word_lengths_in_band() -> None:
    payload = build()
    for doc in payload["documents"]:
        n_words = len(doc["text"].split())
        assert 40 <= n_words <= 120, f"{doc['id']}: {n_words} words out of 40-120 band"


# --- determinism -------------------------------------------------------------


def test_byte_identical_across_invocations(tmp_build_dir: Path) -> None:
    a = build(CANONICAL_SEED)
    b = build(CANONICAL_SEED)
    dest_a = tmp_build_dir / "a.json"
    dest_b = tmp_build_dir / "b.json"
    dump_canonical_json(a, dest_a)
    dump_canonical_json(b, dest_b)
    assert dest_a.read_bytes() == dest_b.read_bytes()


def test_content_independent_of_seed() -> None:
    # The builder is a frozen literal; only the recorded seed field varies.
    base = build(CANONICAL_SEED)
    other = build(CANONICAL_SEED + 1)
    assert other["seed"] == CANONICAL_SEED + 1
    assert base["documents"] == other["documents"]


# --- schema ------------------------------------------------------------------


def test_schema_validates(tmp_build_dir: Path) -> None:
    payload = build()
    dest = tmp_build_dir / "negative_corpus.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "negative_corpus")


# --- PII-absence guards ------------------------------------------------------


def test_no_ssn_shaped_string() -> None:
    found = _SSN_RE.findall(_corpus_text(build()))
    assert not found, f"SSN-shaped string(s) present: {found}"


def test_no_digit_run_of_length_5_or_more() -> None:
    # HARD SAFETY RULE. The structural validators require specific lengths /
    # checksums, so any digit run of length >= 5 risks parsing as a real
    # identifier: SSN (\d{3}\d{2}\d{4}), phone (\d{10}), account (\d{10}),
    # routing (\d{9} + mod-10), EIN (\d{2}\d{7}), ITIN, credit-card. The
    # number-shaped flavor is kept a true negative by capping every run at
    # length 4 (years, invoice / room / page / case numbers, small counts,
    # percentages; comma-grouped thousands keep each group <= 3). This is the
    # single mechanical invariant that makes the structural-FP stressor safe.
    runs = [r for r in _digit_runs(_corpus_text(build())) if len(r) >= 5]
    assert not runs, f"digit run(s) of length >= 5 present: {runs}"
    # Belt and suspenders: the bare regex \d{5,} must not appear anywhere.
    assert re.search(r"\d{5,}", _corpus_text(build())) is None


def test_phones_only_in_reserved_range() -> None:
    bad: list[str] = []
    for m in _PHONE_RE.finditer(_corpus_text(build())):
        last10 = re.sub(r"\D", "", m.group())[-10:]
        # Reserved fictional space: 555-01xx line numbers (matches verify.py).
        if not re.fullmatch(r"\d{3}5550(1\d\d)", last10):
            bad.append(m.group())
    assert not bad, f"phone-shaped string(s) outside the reserved 555-01xx range: {bad}"


def test_domains_rfc2606_only() -> None:
    bad = [
        d for d in _DOMAIN_RE.findall(_corpus_text(build())) if d.lower() not in _RFC2606_DOMAINS
    ]
    assert not bad, f"non-RFC-2606 domain-shaped token(s) present: {bad}"


def test_all_printable_ascii() -> None:
    allowed = set(string.printable)
    for doc in build()["documents"]:
        offenders = sorted({c for c in doc["text"] if c not in allowed})
        assert not offenders, f"{doc['id']}: non-printable-ASCII char(s) {offenders!r}"


def test_short_digit_run_stressor_present() -> None:
    # The corpus's structural-FP value is that benign SHORT numbers sit next to
    # the family trigger-keywords. Assert the stressor is actually present: a
    # meaningful number of documents must carry at least one digit run (every
    # such run is length 1-4 by the rule above). A corpus with zero digits
    # cannot stress the account / phone / routing / EIN structural surface.
    docs_with_runs = sum(1 for d in build()["documents"] if _digit_runs(d["text"]))
    assert docs_with_runs >= _MIN_DIGIT_STRESSOR_DOCS, (
        f"only {docs_with_runs} doc(s) carry a short digit run; "
        f"expected >= {_MIN_DIGIT_STRESSOR_DOCS} (the structural-FP stressor)"
    )


def test_no_banned_mechanism_phrases() -> None:
    hits = scan_text(_corpus_text(build()))
    assert not hits, f"banned mechanism-language phrase(s) present: {[h.phrase for h in hits]}"


# --- the FP surface is actually seeded ---------------------------------------


@pytest.mark.parametrize(
    "keyword",
    ["account", "phone", "record", "routing", "medic"],
)
def test_family_trigger_keywords_seeded(keyword: str) -> None:
    # The corpus's value is that these context-mandatory trigger-keywords are
    # present in benign, non-PII contexts. Assert each appears widely.
    text = _corpus_text(build()).lower()
    assert text.count(keyword) >= 5, f"trigger-keyword {keyword!r} under-seeded"
