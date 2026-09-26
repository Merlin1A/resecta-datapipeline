"""Tests for the G8 synthetic corpus builder.

Determinism tests use reduced per-doctype counts to keep the suite fast
while still exercising every template. The full-count build runs in the
slow-marked determinism suite via make verify.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

import pytest

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.corpus import build_g8_corpus
from resecta_data.corpus._spans import CONTEXT_CLASSES, REDACTED_NAME_PLACEHOLDER
from resecta_data.corpus.generate import _MAX_BUILD_WORKERS
from resecta_data.vectors._checksum import luhn_mod10
from resecta_data.vectors.ein import _VALID_EIN_PREFIXES
from resecta_data.vectors.itin import _yy_is_valid
from resecta_data.vectors.routing_number import _aba_checksum, _is_valid_prefix

_SCHEMAS = Path(__file__).parent.parent / "schemas"

# Reduced counts: one doc per doctype per demographic bucket. Exercises
# every emitter + bucket combination without generating 1000 documents.
_MIN_COUNTS = {
    "court": 5,
    "medical": 5,
    "financial": 5,
    "foia": 5,
    "generic": 5,
}

# Enough documents per doctype for every 50/50 branch and every 25-30 % decoy
# roll to be taken at least once (used by the 17/17 coverage tests).
_COVERAGE_COUNTS = dict.fromkeys(_MIN_COUNTS, 40)

_MIN_SPANS = 5
_MAX_SPANS = 18


def test_schema_validates_small(tmp_build_dir: Path) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    dest = tmp_build_dir / "g8_corpus.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_corpus")


def test_deterministic_small() -> None:
    a = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    b = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    assert a == b


def test_document_count_matches_counts() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    assert len(payload["documents"]) == sum(_MIN_COUNTS.values())
    for doctype, count in _MIN_COUNTS.items():
        assert payload["counts_by_doctype"][doctype] == count


def test_span_count_within_bounds() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    for doc in payload["documents"]:
        assert _MIN_SPANS <= len(doc["pii_spans"]) <= _MAX_SPANS


def test_span_offsets_align_with_text() -> None:
    """Every ground-truth span's [start, end) must return its recorded value."""
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    for doc in payload["documents"]:
        text = doc["text"]
        for span in doc["pii_spans"]:
            start, end = span["start"], span["end"]
            assert text[start:end] == span["value"], (
                f"{doc['id']}: span {span['category']} [{start}, {end}) does not match text."
            )


def test_ids_unique_and_well_formed() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    ids = [d["id"] for d in payload["documents"]]
    assert len(set(ids)) == len(ids)
    for doc_id in ids:
        doctype, index_str = doc_id.split("_", maxsplit=1)
        assert doctype in _MIN_COUNTS
        assert index_str.isdigit()
        assert len(index_str) == 6


def test_demographic_stratification_balanced() -> None:
    """With counts divisible by 5, each bucket should receive exactly count/5 docs."""
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    # counts_by_demographic has 5 primary buckets; with 5 docs per doctype,
    # each bucket receives exactly 1 doc per doctype == 5 total.
    for bucket in ("white", "black", "hispanic", "asian", "ai_an"):
        assert payload["counts_by_demographic"][bucket] == len(_MIN_COUNTS)


def test_documents_sorted_by_doctype_then_id() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    keys = [(d["doctype"], d["id"]) for d in payload["documents"]]
    assert keys == sorted(keys)


def test_adversarial_spans_marked_suppress() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    for doc in payload["documents"]:
        for span in doc["pii_spans"]:
            if span.get("adversarial") and span["category"] in ("ssn", "dob", "npi"):
                assert span["expected_outcome"] in ("suppress", "flag")


@pytest.mark.determinism
def test_parallel_matches_serial_bytes(tmp_build_dir: Path) -> None:
    """Parallel dispatch must produce the same canonical JSON as serial.

    Compares the ``dump_canonical_json`` bytes — not the in-memory dict —
    so any non-determinism that leaks into encoding (dict ordering, float
    formatting, adversarial_tags set order) is caught.
    ``workers=2`` exercises the pool path without 12-process fork overhead.
    """
    serial_payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=False)
    parallel_payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=True, workers=2)

    serial_path = tmp_build_dir / "serial.json"
    parallel_path = tmp_build_dir / "parallel.json"
    dump_canonical_json(serial_payload, serial_path)
    dump_canonical_json(parallel_payload, parallel_path)

    assert serial_path.read_bytes() == parallel_path.read_bytes()


@pytest.mark.slow
def test_full_corpus_determinism() -> None:
    """Full-count build is deterministic too — runs under the slow marker."""
    a = build_g8_corpus(CANONICAL_SEED)
    b = build_g8_corpus(CANONICAL_SEED)
    assert a == b


@pytest.mark.slow
def test_full_corpus_schema_and_shape(tmp_build_dir: Path) -> None:
    payload = build_g8_corpus(CANONICAL_SEED)
    dest = tmp_build_dir / "g8_corpus_full.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_corpus")
    expected_total = 300 + 250 + 300 + 150 + 100
    assert len(payload["documents"]) == expected_total
    # The full corpus must carry at least 100 W-2 shaped financial docs.
    tax_docs = [d for d in payload["documents"] if d.get("sub_template") == "financial_tax"]
    assert len(tax_docs) >= 100


# ---- Corpus fixes ---------------------------------------------------------


def test_financial_tax_split_and_shape() -> None:
    """The top third of the financial index range emits the W-2 shape."""
    counts = {**_MIN_COUNTS, "financial": 9}
    payload = build_g8_corpus(CANONICAL_SEED, counts=counts)
    financial = [d for d in payload["documents"] if d["doctype"] == "financial"]
    tax = [d for d in financial if d.get("sub_template") == "financial_tax"]
    invoice = [d for d in financial if "sub_template" not in d]
    assert len(tax) == 3
    assert len(invoice) == 6
    assert sorted(d["id"] for d in tax) == [f"financial_{i:06d}" for i in (6, 7, 8)]
    for doc in tax:
        assert "Employee's social security number" in doc["text"]
        assert "Employer identification number" in doc["text"]
        categories = {s["category"] for s in doc["pii_spans"]}
        assert {"ssn", "ein", "address"} <= categories
        # No routing numbers in the tax shape.
        assert "routingNumber" not in categories


def test_tax_ein_values_structurally_valid() -> None:
    counts = {**_MIN_COUNTS, "financial": 9}
    payload = build_g8_corpus(CANONICAL_SEED, counts=counts)
    ein_spans = [
        span
        for doc in payload["documents"]
        for span in doc["pii_spans"]
        if span["category"] == "ein"
    ]
    assert ein_spans
    for span in ein_spans:
        assert re.fullmatch(r"[0-9]{2}-[0-9]{7}", span["value"])
        assert int(span["value"][:2]) in _VALID_EIN_PREFIXES


def test_invoice_routing_spans_aba_valid() -> None:
    """Routing spans are labeled routingNumber and pass prefix + checksum."""
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    routing_spans = [
        span
        for doc in payload["documents"]
        for span in doc["pii_spans"]
        if span["category"] == "routingNumber"
    ]
    assert routing_spans
    for span in routing_spans:
        digits = [int(c) for c in span["value"]]
        assert len(digits) == 9
        assert _is_valid_prefix(digits)
        assert _aba_checksum(digits) == 0


def test_name_sparse_fraction_and_purity() -> None:
    """~30% of docs carry zero name spans; sparse docs use the placeholder."""
    counts = dict.fromkeys(_MIN_COUNTS, 60)
    payload = build_g8_corpus(CANONICAL_SEED, counts=counts)
    docs = payload["documents"]
    sparse = [d for d in docs if not any(s["category"] == "name" for s in d["pii_spans"])]
    fraction = len(sparse) / len(docs)
    assert 0.20 <= fraction <= 0.40, f"name-sparse fraction {fraction:.3f} outside [0.20, 0.40]"
    for doc in sparse:
        assert REDACTED_NAME_PLACEHOLDER in doc["text"]
    # Dense docs must still always carry at least one name span.
    for doc in docs:
        if doc not in sparse:
            assert any(s["category"] == "name" for s in doc["pii_spans"])


# ---- worker-count knobs (RESECTA_BUILD_WORKERS, PYTEST_XDIST_WORKER) -----


class _RecordingPool:
    """Test double for ProcessPoolExecutor that records ``max_workers``."""

    captured: ClassVar[dict[str, int]] = {}

    def __init__(self, max_workers: int) -> None:
        type(self).captured["max_workers"] = max_workers

    def __enter__(self) -> _RecordingPool:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def map(self, fn: Any, *iterables: Any, chunksize: int = 1) -> Any:
        return (fn(*args) for args in zip(*iterables, strict=True))

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        return None


def test_corpus_build_honors_build_workers_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RESECTA_BUILD_WORKERS`` caps the corpus pool when ``workers`` is default."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "2")
    _RecordingPool.captured = {}
    monkeypatch.setattr(
        "resecta_data.corpus.generate.ProcessPoolExecutor",
        _RecordingPool,
    )
    build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=True)
    assert _RecordingPool.captured.get("max_workers") == 2


def test_corpus_build_workers_env_clamped_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized env value still clamps to ``_MAX_BUILD_WORKERS``."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "100")
    _RecordingPool.captured = {}
    monkeypatch.setattr(
        "resecta_data.corpus.generate.ProcessPoolExecutor",
        _RecordingPool,
    )
    build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=True)
    assert _RecordingPool.captured.get("max_workers", 0) <= _MAX_BUILD_WORKERS


def test_corpus_build_byte_identical_with_env_capped_workers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_build_dir: Path,
) -> None:
    """Env-capped workers must produce the same canonical bytes as the
    serial path (parallel/serial equivalence is asserted in
    test_parallel_matches_serial_bytes; here we additionally ensure the
    env knob does not bleed into the artifact)."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    serial = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "2")
    capped = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=True)
    serial_path = tmp_build_dir / "serial.json"
    capped_path = tmp_build_dir / "capped.json"
    dump_canonical_json(serial, serial_path)
    dump_canonical_json(capped, capped_path)
    assert serial_path.read_bytes() == capped_path.read_bytes()


def test_corpus_build_respects_pytest_xdist_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under xdist with workers=None, the parent must take the serial path.

    Mirrors test_parse_sources_parallel_respects_pytest_xdist_env from
    test_bloom_corpus_ingest.py — change #7 in the OOM-freeze fix plan.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "ProcessPoolExecutor must not be instantiated under PYTEST_XDIST_WORKER"
        )

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setattr(
        "resecta_data.corpus.generate.ProcessPoolExecutor",
        _boom,
    )
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, parallel=True)
    assert len(payload["documents"]) == sum(_MIN_COUNTS.values())


# ---- 1.2 T1.1 (C12-25): the five added categories + the tier bridge ------

_ALL_17_CATEGORIES = {
    "ssn",
    "npi",
    "dea",
    "dob",
    "address",
    "account",
    "mrn",
    "name",
    "phone",
    "email",
    "routingNumber",
    "ein",
    "itin",
    "creditCard",
    "driversLicense",
    "passport",
    "licensePlate",
}
_NEW_DECOY_TAGS = {
    "luhn_failed_card_number",
    "itin_yy_out_of_range",
    "dl_shape_no_jurisdiction",
    "passport_shape_no_issuer",
    "business_registration_plate_label",
}
_TIERS = {"must", "should", "watch", "must_not"}
_CARD_PREFIX_OK = re.compile(r"^(?:4|5[1-5]|6011)")
# The engine's ITIN profile positives (PIIDetector.itinProfile) and the plate
# profile positives (LicensePlateContextKeywords.profile); the scorer matches
# SUBSTRINGS, so the window text is checked the same way.
_ITIN_KEYWORDS = (
    "itin",
    "individual taxpayer identification",
    "individual taxpayer id",
    "tax identification number",
    "w-7",
    "taxpayer identification",
    "tin",
)
_PLATE_KEYWORDS = (
    "vehicle",
    "car",
    "truck",
    "motorcycle",
    "dmv",
    "registration",
    "vin",
    "make",
    "model",
    "driver",
    "owner",
)


def _all_spans(payload: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(doc, span) for doc in payload["documents"] for span in doc["pii_spans"]]


def test_all_seventeen_categories_have_span_ground_truth() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    seen = {span["category"] for _, span in _all_spans(payload)}
    assert seen == _ALL_17_CATEGORIES
    tags = {tag for doc in payload["documents"] for tag in doc.get("adversarial_tags", [])}
    assert tags >= _NEW_DECOY_TAGS
    # Every added category also has its negative twin (a must_not span).
    decoyed = {span["category"] for _, span in _all_spans(payload) if span["tier"] == "must_not"}
    assert decoyed >= {"itin", "creditCard", "driversLicense", "passport", "licensePlate"}


def test_coverage_counts_schema_validates(tmp_build_dir: Path) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    dest = tmp_build_dir / "g8_corpus_coverage.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_corpus")


def test_tier_bridge_is_total_and_consistent() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    for _, span in _all_spans(payload):
        tier = span["tier"]
        assert tier in _TIERS
        assert (tier == "must_not") == (span["expected_outcome"] == "suppress")
        assert (tier == "watch") == (span["expected_outcome"] == "flag")
        if tier == "should":
            assert span["expected_outcome"] == "redact"
            assert span["adversarial"] is False
    tiers = {span["tier"] for _, span in _all_spans(payload)}
    # No template emits a `flag` outcome today, so `watch` is legitimately absent.
    assert {"must", "should", "must_not"} <= tiers


def test_new_category_values_structurally_valid() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    for _, span in _all_spans(payload):
        category, value = span["category"], span["value"]
        decoy = span["expected_outcome"] == "suppress"
        if category == "itin":
            assert re.fullmatch(r"9\d{2}-\d{2}-\d{4}", value)
            # Valid YY group iff not the out-of-range decoy.
            assert _yy_is_valid(int(value[4:6])) is not decoy
        elif category == "creditCard":
            assert re.fullmatch(r"\d{4}( \d{4}){3}", value)
            digits = value.replace(" ", "")
            assert _CARD_PREFIX_OK.match(digits)
            assert luhn_mod10(digits) is not decoy
        elif category == "driversLicense":
            assert re.fullmatch(r"[A-Z]\d{14}" if decoy else r"[A-Z]\d{7,8}", value)
        elif category == "passport":
            assert re.fullmatch(r"[A-Z]\d{6}" if decoy else r"[A-Z]\d{8}", value)
        elif category == "licensePlate":
            assert re.fullmatch(r"\d{4}-[A-Z]{2}-\d{5}" if decoy else r"[A-Z]{3}[- ]?\d{4}", value)


def _window_text(text: str, start: int, end: int, radius: int) -> str:
    """The +-radius whitespace tokens around [start, end), joined and lowercased."""
    before = text[:start].split()
    after = text[end:].split()
    return " ".join(before[-radius:] + after[:radius]).lower()


def test_should_tier_surfaces_are_keyword_starved() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    should = [(doc, span) for doc, span in _all_spans(payload) if span["tier"] == "should"]
    assert should
    for doc, span in should:
        if span["category"] == "itin":
            window = _window_text(doc["text"], span["start"], span["end"], radius=8)
            assert not any(kw in window for kw in _ITIN_KEYWORDS), doc["id"]
            # The +-100-char reading of the window must be starved too.
            chars = doc["text"][max(0, span["start"] - 100) : span["end"] + 100].lower()
            assert not any(kw in chars for kw in _ITIN_KEYWORDS), doc["id"]
        elif span["category"] == "licensePlate":
            window = _window_text(doc["text"], span["start"], span["end"], radius=5)
            assert not any(kw in window for kw in _PLATE_KEYWORDS), doc["id"]
        else:
            raise AssertionError(f"unexpected should-tier category {span['category']}")
    assert {span["category"] for _, span in should} == {"itin", "licensePlate"}


def test_span_cap_still_binds_all_templates() -> None:
    """The 15 -> 18 ceiling is tight: no document exceeds it at coverage counts."""
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    assert max(len(doc["pii_spans"]) for doc in payload["documents"]) <= _MAX_SPANS


# ---------------------------------------------------------------------------
# The context annotation: every span carries a context_class, every document a
# furniture array, and the name slots' classes are pinned on the canonical corpus.
# ---------------------------------------------------------------------------

_CONTEXT_CLASSES = frozenset(CONTEXT_CLASSES)

# The name slots the shipped templates emit, by left-context class, on the canonical
# 1,100-document corpus (seed 20260416): 2,837 name spans. Court = caption pair +
# PLAINTIFF: / DEFENDANT: / Counsel of record: / Witness; medical = Patient: + two Dr.
# titles; financial = Bill to: / AP Contact: (invoice) + Employee's name: (W-2); foia =
# From: / Re: / the line after Sincerely,; generic = the first line / To: / Dear /
# the line after Regards,. A change here is a template change, never annotation drift.
_PINNED_NAME_CLASS_COUNTS: dict[str, int] = {
    "caption_left": 215,
    "caption_right": 215,
    "role_label": 1596,
    "title_label": 382,
    "closing_line": 180,
    "subject_line": 111,
    "salutation": 69,
    "document_initial": 69,
}
_PINNED_NAME_CLASS_COUNTS_BY_DOCTYPE: dict[str, dict[str, int]] = {
    "court": {"caption_left": 215, "caption_right": 215, "role_label": 860},
    "medical": {"role_label": 191, "title_label": 382},
    "financial": {"role_label": 365},
    "foia": {"role_label": 111, "subject_line": 111, "closing_line": 111},
    "generic": {"document_initial": 69, "role_label": 69, "salutation": 69, "closing_line": 69},
}


def test_every_span_carries_a_context_class_and_every_document_a_furniture_array() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    for doc in payload["documents"]:
        assert doc["furniture"] == [], f"{doc['id']}: furniture is planted by no template yet"
    for doc, span in _all_spans(payload):
        assert span["context_class"] in _CONTEXT_CLASSES, (doc["id"], span["context_class"])
        if span["category"] == "name":
            # Every name the templates emit sits in a named slot.
            assert span["context_class"] != "none", (doc["id"], span["start"])
        else:
            assert span["context_class"] == "none", (doc["id"], span["category"])


def test_name_context_classes_pinned_on_the_canonical_corpus() -> None:
    payload = build_g8_corpus(CANONICAL_SEED)
    assert payload["version"] == 2
    by_class: Counter[str] = Counter()
    by_doctype: dict[str, Counter[str]] = {}
    for doc, span in _all_spans(payload):
        if span["category"] != "name":
            continue
        by_class[span["context_class"]] += 1
        by_doctype.setdefault(doc["doctype"], Counter())[span["context_class"]] += 1
    assert dict(by_class) == _PINNED_NAME_CLASS_COUNTS
    assert sum(by_class.values()) == 2837
    assert {dt: dict(c) for dt, c in by_doctype.items()} == _PINNED_NAME_CLASS_COUNTS_BY_DOCTYPE


# ---------------------------------------------------------------------------
# Generator profiles (1.2 C12-95 Spec-C / Spec-D; C12-29 (b)): the g8 profile
# is the corpus as furnished, byte for byte; every other profile is the same
# documents with the name slots re-rendered and / or furniture planted.
# ---------------------------------------------------------------------------

from resecta_data.corpus._names import (  # noqa: E402
    BUCKETS,
    FORM_ALL_CAPS,
    FORM_FULL,
    FORM_INITIAL,
    FORM_LAST_FIRST,
    FORM_PARTICLED,
    FORM_SUFFIX,
    NAME_FORMS,
    PARTICLES,
    SUFFIXES,
)
from resecta_data.corpus._profiles import (  # noqa: E402
    COURT_ROLE_NOUNS,
    COURT_ROLE_NOUNS_PER_DOC,
    FURNITURE_KINDS,
    LABELS_PER_DOC,
    LOCALE_ES_MX,
    LOCALES,
    MEDICAL_ROLE_NOUNS,
    MEDICAL_ROLE_NOUNS_PER_DOC,
    PROFILE_G8,
    PROFILE_SPEC_A,
    PROFILE_SPEC_AGH,
    PROFILE_SPEC_C,
    PROFILE_SPEC_CD,
    PROFILE_SPEC_D,
    PROFILE_SPEC_G,
    PROFILE_SPEC_H,
    PROFILES,
    ROLE_WORDS,
)
from resecta_data.corpus.generate import _sub_seed_and_locale  # noqa: E402

# Spec-C / Spec-D (the context and furniture profiles) and the Spec-A / G / H
# profiles that ride the same axis; every profile-generic test runs on all seven.
_SPEC_PROFILES = (PROFILE_SPEC_C, PROFILE_SPEC_D, PROFILE_SPEC_CD)
_AGH_PROFILES = (PROFILE_SPEC_A, PROFILE_SPEC_G, PROFILE_SPEC_H, PROFILE_SPEC_AGH)
_ALL_SPEC_PROFILES = _SPEC_PROFILES + _AGH_PROFILES
# The categories whose VALUE a profile moves by design: Spec-A the name (its
# form is the experiment), Spec-G the address of a document whose locale
# moved. Every other value is identical to g8 under every profile.
_MOVABLE_CATEGORIES: dict[str, frozenset[str]] = {
    PROFILE_SPEC_C: frozenset(),
    PROFILE_SPEC_D: frozenset(),
    PROFILE_SPEC_CD: frozenset(),
    PROFILE_SPEC_A: frozenset({"name"}),
    PROFILE_SPEC_G: frozenset({"address"}),
    PROFILE_SPEC_H: frozenset(),
    PROFILE_SPEC_AGH: frozenset({"name", "address"}),
}
# What a sparse slot renders: the literal placeholder, or under Spec-H a role
# phrase (capitalised when it opens a line).
_SPARSE_MARKERS = (
    REDACTED_NAME_PLACEHOLDER,
    *ROLE_WORDS,
    *(w[0].upper() + w[1:] for w in ROLE_WORDS),
)
_NEW_CONTEXT_CLASSES = frozenset({"table_cell", "body_prose", "header"})
_ROLE_NOUN_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(re.escape(w) for w in COURT_ROLE_NOUNS + MEDICAL_ROLE_NOUNS)
    + r")(?![A-Za-z])"
)


def _span_signature(span: dict[str, Any], movable: frozenset[str] = frozenset()) -> tuple[Any, ...]:
    """The PAIR identity of a span: its category, value (unless the profile
    moves that category's value by design), tier, adversarial flag and
    outcome. ``form`` / ``locale`` are annotations beside the identity."""
    return (
        span["category"],
        None if span["category"] in movable else span["value"],
        span["tier"],
        span.get("adversarial", False),
        span.get("expected_outcome"),
    )


def _sparse_slot_in(segment: str) -> bool:
    return any(marker in segment for marker in _SPARSE_MARKERS)


def _neighbour_tokens(doc: dict[str, Any], span: dict[str, Any]) -> tuple[str | None, str | None]:
    """The whitespace token right before and right after a non-name span, as
    the structured-family guard reads them: a side is ``None`` (exempt) when a
    NAME slot sits on that side of the same line (the slot's rendering is what
    a profile moves) or when the neighbour lies on another line (a planted
    line or the next slot's label, never this span's own context)."""
    text = doc["text"]
    line_start = text.rfind("\n", 0, span["start"]) + 1
    line_end = text.find("\n", span["end"])
    line_end = len(text) if line_end < 0 else line_end
    names = [(s["start"], s["end"]) for s in doc["pii_spans"] if s["category"] == "name"]
    # A name slot on this side of the line: a name span, or the sparse
    # placeholder the slot renders instead of one.
    name_before = any(ne <= span["start"] and ns >= line_start for ns, ne in names) or (
        _sparse_slot_in(text[line_start : span["start"]])
    )
    name_after = any(ns >= span["end"] and ne <= line_end for ns, ne in names) or (
        _sparse_slot_in(text[span["end"] : line_end])
    )
    tokens = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text)]
    before = [t for t in tokens if t[1] <= span["start"]][-1:]
    after = [t for t in tokens if t[0] >= span["end"]][:1]
    before_word = before[0][2] if before and before[0][0] >= line_start else None
    after_word = after[0][2] if after and after[0][1] <= line_end else None
    return (None if name_before else before_word, None if name_after else after_word)


def test_profiles_are_named_and_g8_is_the_default() -> None:
    assert PROFILES == (
        "g8",
        "g8-specC",
        "g8-specD",
        "g8-specCD",
        "g8-specA",
        "g8-specG",
        "g8-specH",
        "g8-specAGH",
    )
    default = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    explicit = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, profile=PROFILE_G8)
    assert default == explicit
    assert "profile" not in default
    with pytest.raises(PipelineError, match="unknown corpus profile"):
        build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, profile="g8-specZ")


@pytest.mark.parametrize("profile", _ALL_SPEC_PROFILES)
def test_profile_builds_are_deterministic_named_and_distinct(profile: str) -> None:
    a = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    b = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    assert a == b
    assert a["profile"] == profile
    g8 = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)
    assert a != g8
    # Same documents, same order, same counts; the schema still validates.
    assert [d["id"] for d in a["documents"]] == [d["id"] for d in g8["documents"]]
    assert a["counts_by_doctype"] == g8["counts_by_doctype"]


@pytest.mark.parametrize("profile", _ALL_SPEC_PROFILES)
def test_profile_schema_validates(profile: str, tmp_build_dir: Path) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, profile=profile)
    dest = tmp_build_dir / f"g8_corpus_{profile}.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_corpus")


@pytest.mark.parametrize("profile", _ALL_SPEC_PROFILES)
def test_profiles_keep_every_ground_truth_value_and_every_non_name_slot(profile: str) -> None:
    """The PAIR invariant: a profile document is the g8 document with its name
    slots re-rendered and furniture planted -- every span keeps its category,
    value (except the categories the profile moves by design: Spec-A the
    name, Spec-G the address), tier and outcome IN ORDER, every span's
    offsets still return its value, and every NON-name span keeps the tokens
    on both sides of it (the structured-family guard; name tokens masked)."""
    movable = _MOVABLE_CATEGORIES[profile]
    g8 = {d["id"]: d for d in build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)["documents"]}
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    for doc in payload["documents"]:
        twin = g8[doc["id"]]
        assert [_span_signature(s, movable) for s in doc["pii_spans"]] == [
            _span_signature(s, movable) for s in twin["pii_spans"]
        ], doc["id"]
        for span in doc["pii_spans"]:
            assert doc["text"][span["start"] : span["end"]] == span["value"], doc["id"]
        for span, twin_span in zip(doc["pii_spans"], twin["pii_spans"], strict=True):
            if span["category"] == "name":
                continue
            # Compare only the sides the guard does not exempt on EITHER twin.
            ours, theirs = _neighbour_tokens(doc, span), _neighbour_tokens(twin, twin_span)
            for mine, its in zip(ours, theirs, strict=True):
                if mine is not None and its is not None:
                    assert mine == its, (doc["id"], span["category"], ours, theirs)
        # Name-sparse documents stay name-free under every profile.
        if not any(s["category"] == "name" for s in twin["pii_spans"]):
            assert not any(s["category"] == "name" for s in doc["pii_spans"])
            assert _sparse_slot_in(doc["text"])


def test_spec_c_renders_the_classes_the_shipped_corpus_lacks() -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=PROFILE_SPEC_C)
    classes: Counter[str] = Counter()
    title_outside_medical = 0
    for doc in payload["documents"]:
        assert doc["furniture"] == [], "Spec-C plants no furniture"
        for span in doc["pii_spans"]:
            assert span["context_class"] in _CONTEXT_CLASSES
            if span["category"] != "name":
                assert span["context_class"] == "none"
                continue
            assert span["context_class"] != "none"
            classes[span["context_class"]] += 1
            if span["context_class"] == "title_label" and doc["doctype"] != "medical":
                title_outside_medical += 1
    # Every doctype draws the new classes (C12-29 (b): table cells, body prose,
    # running headers, Title-labels beyond Dr.).
    assert set(classes) >= _NEW_CONTEXT_CLASSES
    assert title_outside_medical > 0
    for doctype in _MIN_COUNTS:
        seen = {
            s["context_class"]
            for d in payload["documents"]
            if d["doctype"] == doctype
            for s in d["pii_spans"]
            if s["category"] == "name"
        }
        assert seen & _NEW_CONTEXT_CLASSES, doctype


@pytest.mark.parametrize("profile", (PROFILE_SPEC_D, PROFILE_SPEC_CD))
def test_spec_d_plants_furniture_at_the_pre_registered_rates(profile: str) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    for doc in payload["documents"]:
        text = doc["text"]
        kinds = Counter(f["kind"] for f in doc["furniture"])
        assert set(kinds) <= set(FURNITURE_KINDS), doc["id"]
        for region in doc["furniture"]:
            assert 0 <= region["start"] < region["end"] <= len(text)
            # Never on a ground-truth span.
            assert not any(
                region["start"] < s["end"] and s["start"] < region["end"] for s in doc["pii_spans"]
            ), doc["id"]
            if region["kind"] == "role_noun":
                # One region = one sentence = exactly one role noun.
                assert len(_ROLE_NOUN_RE.findall(text[region["start"] : region["end"]])) == 1
        labels = kinds["label"] + kinds["plate_label"]
        court_lo, court_hi = COURT_ROLE_NOUNS_PER_DOC
        medical_lo, medical_hi = MEDICAL_ROLE_NOUNS_PER_DOC
        if doc["doctype"] == "court":
            assert court_lo <= kinds["role_noun"] <= court_hi
            assert LABELS_PER_DOC[0] <= labels <= LABELS_PER_DOC[1]
        elif doc["doctype"] == "medical":
            assert medical_lo <= kinds["role_noun"] <= medical_hi
            assert labels == 0
        elif doc["doctype"] == "foia":
            assert kinds["role_noun"] == 0
            assert LABELS_PER_DOC[0] <= labels <= LABELS_PER_DOC[1]
            assert kinds["salutation"] == 1 and kinds["closing"] == 1
        elif doc["doctype"] == "generic":
            assert kinds["role_noun"] == 0 and labels == 0
            assert kinds["closing"] == 1
            # The salutation cue is furniture unless the slot drew the
            # label-shaped "Attention:" variant (Spec-CD only).
            attention = profile == PROFILE_SPEC_CD and kinds["salutation"] == 0
            assert kinds["salutation"] == 1 or attention
        else:  # financial (invoice + W-2): neither a pleading nor a letter
            assert doc["furniture"] == [], doc["id"]


def test_spec_c_alone_and_g8_plant_no_furniture() -> None:
    for profile in (PROFILE_G8, PROFILE_SPEC_C, *_AGH_PROFILES):
        payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, profile=profile)
        assert all(doc["furniture"] == [] for doc in payload["documents"]), profile


@pytest.mark.parametrize("profile", _ALL_SPEC_PROFILES)
def test_should_tier_surfaces_stay_keyword_starved_under_every_profile(profile: str) -> None:
    """The keyword-starved plate / ITIN spans keep their starved windows: a
    planted label line or a re-rendered name slot never feeds them."""
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    for doc, span in _all_spans(payload):
        if span["tier"] != "should":
            continue
        if span["category"] == "itin":
            window = _window_text(doc["text"], span["start"], span["end"], radius=8)
            assert not any(kw in window for kw in _ITIN_KEYWORDS), doc["id"]
            chars = doc["text"][max(0, span["start"] - 100) : span["end"] + 100].lower()
            assert not any(kw in chars for kw in _ITIN_KEYWORDS), doc["id"]
        elif span["category"] == "licensePlate":
            window = _window_text(doc["text"], span["start"], span["end"], radius=5)
            assert not any(kw in window for kw in _PLATE_KEYWORDS), doc["id"]


# The canonical (full-count, seed 20260416) profile builds, pinned: a change
# here is a template or profile change, never drift. The g8 twin is pinned
# above (_PINNED_NAME_CLASS_COUNTS); these are the three profiles' censuses.
_PINNED_PROFILE_NAME_CLASS_COUNTS: dict[str, dict[str, int]] = {
    PROFILE_SPEC_C: {
        "body_prose": 408,
        "caption_left": 84,
        "caption_right": 84,
        "closing_line": 159,
        "document_initial": 16,
        "header": 242,
        "role_label": 603,
        "salutation": 40,
        "subject_line": 50,
        "table_cell": 550,
        "title_label": 601,
    },
    PROFILE_SPEC_D: dict(_PINNED_NAME_CLASS_COUNTS),
    PROFILE_SPEC_CD: {
        "body_prose": 387,
        "caption_left": 86,
        "caption_right": 86,
        "closing_line": 146,
        "document_initial": 10,
        "header": 264,
        "role_label": 633,
        "salutation": 36,
        "subject_line": 51,
        "table_cell": 553,
        "title_label": 585,
    },
}
_PINNED_PROFILE_FURNITURE_COUNTS: dict[str, dict[str, int]] = {
    PROFILE_SPEC_C: {},
    PROFILE_SPEC_D: {
        "closing": 250,
        "label": 452,
        "plate_label": 449,
        "role_noun": 4226,
        "salutation": 250,
    },
    PROFILE_SPEC_CD: {
        "closing": 250,
        "label": 462,
        "plate_label": 444,
        "role_noun": 4199,
        "salutation": 221,
    },
}


@pytest.mark.parametrize("profile", _SPEC_PROFILES)
def test_profile_censuses_pinned_on_the_canonical_corpus(profile: str) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, profile=profile)
    assert payload["version"] == 2
    assert payload["profile"] == profile
    by_class: Counter[str] = Counter(
        span["context_class"] for _, span in _all_spans(payload) if span["category"] == "name"
    )
    assert dict(by_class) == _PINNED_PROFILE_NAME_CLASS_COUNTS[profile]
    assert sum(by_class.values()) == 2837
    kinds: Counter[str] = Counter(
        region["kind"] for doc in payload["documents"] for region in doc["furniture"]
    )
    assert dict(kinds) == _PINNED_PROFILE_FURNITURE_COUNTS[profile]


# ---------------------------------------------------------------------------
# Spec-A (name forms) / Spec-G (the locale axis) / Spec-H (the sparse
# placeholder) -- 1.2 C12-95, the same profile axis; C and D stay off.
# ---------------------------------------------------------------------------

_CAPTION_CLASSES = frozenset({"caption_left", "caption_right"})
_FORM_SHAPES: dict[str, re.Pattern[str]] = {
    FORM_FULL: re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+$"),
    FORM_INITIAL: re.compile(r"^[A-Z][a-z]+ [A-Z]\. [A-Z][a-z]+$"),
    FORM_LAST_FIRST: re.compile(r"^[A-Z][a-z]+, [A-Z][a-z]+$"),
    FORM_ALL_CAPS: re.compile(r"^[A-Z]+ [A-Z]+$"),
    FORM_SUFFIX: re.compile(
        r"^[A-Z][a-z]+ [A-Z][a-z]+ (?:" + "|".join(map(re.escape, SUFFIXES)) + r")$"
    ),
    FORM_PARTICLED: re.compile(
        r"^[A-Z][a-z]+ (?:[A-Z][a-z]+-[A-Z][a-z]+|(?:"
        + "|".join(map(re.escape, PARTICLES))
        + r")[A-Z][a-z]+)$"
    ),
}
# The pre-registered per-slot rates ([R09] Section 5): the fraction of the
# NON-caption name spans; the tolerance is three binomial standard deviations
# at the full count (2,407 slots), so a template change shows, noise does not.
_FORM_RATES: dict[str, float] = {
    FORM_INITIAL: 0.20,
    FORM_LAST_FIRST: 0.10,
    FORM_ALL_CAPS: 0.10,
    FORM_SUFFIX: 0.05,
    FORM_PARTICLED: 0.05,
    FORM_FULL: 0.50,
}
_MX_CP_RE = re.compile(r" \d{5}$")
_ZIP_PLUS_FOUR_RE = re.compile(r"\d{5}-\d{4}$")


def _base_locale(doc: dict[str, Any]) -> str:
    index = int(doc["id"].rsplit("_", 1)[1])
    return _sub_seed_and_locale(CANONICAL_SEED, doc["doctype"], index, doc["demographic_bucket"])[1]


@pytest.mark.parametrize("profile", (PROFILE_SPEC_A, PROFILE_SPEC_AGH))
def test_spec_a_records_a_form_on_every_name_span_that_matches_its_value(profile: str) -> None:
    g8 = {d["id"]: d for d in build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)["documents"]}
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    forms: Counter[str] = Counter()
    for doc, span in _all_spans(payload):
        if span["category"] != "name":
            assert "form" not in span, doc["id"]
            continue
        form = span["form"]
        assert form in NAME_FORMS, doc["id"]
        assert _FORM_SHAPES[form].match(span["value"]), (doc["id"], form, span["value"])
        if span["context_class"] in _CAPTION_CLASSES:
            # Not a Spec-A slot: the caption keeps its shipped rendering and
            # records the surface it carries; ALL-CAPS is its adversarial class.
            twin = next(
                s for s in g8[doc["id"]]["pii_spans"] if s["context_class"] == span["context_class"]
            )
            assert span["value"] == twin["value"], doc["id"]
            assert form == (FORM_ALL_CAPS if span.get("adversarial") else FORM_FULL), doc["id"]
        else:
            assert not span.get("adversarial", False), doc["id"]
            forms[form] += 1
    assert set(forms) == set(NAME_FORMS)


@pytest.mark.parametrize(
    "profile", (PROFILE_SPEC_C, PROFILE_SPEC_D, PROFILE_SPEC_CD, PROFILE_SPEC_G, PROFILE_SPEC_H)
)
def test_form_and_locale_keys_exist_only_on_their_profiles(profile: str) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS, profile=profile)
    for _, span in _all_spans(payload):
        assert "form" not in span
        assert ("locale" in span) == (profile == PROFILE_SPEC_G)
    g8 = build_g8_corpus(CANONICAL_SEED, counts=_MIN_COUNTS)
    assert not any("form" in s or "locale" in s for _, s in _all_spans(g8))


@pytest.mark.parametrize("profile", (PROFILE_SPEC_G, PROFILE_SPEC_AGH))
def test_spec_g_draws_the_locale_axis_and_moves_only_the_addresses_it_says(profile: str) -> None:
    """Every span carries the document's drawn locale; the address value is
    g8's exactly when the drawn locale equals the base locale and is not
    es_MX, and is re-drawn otherwise; an es_MX address ends in a five-digit
    codigo postal, never a ZIP+4 shape."""
    g8 = {d["id"]: d for d in build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)["documents"]}
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    seen: set[tuple[str, str]] = set()
    kept = redrawn = 0
    for doc in payload["documents"]:
        locales = {s["locale"] for s in doc["pii_spans"]}
        assert len(locales) == 1, doc["id"]
        drawn = locales.pop()
        assert drawn in LOCALES, doc["id"]
        seen.add((doc["demographic_bucket"], drawn))
        keep = drawn == _base_locale(doc) and drawn != LOCALE_ES_MX
        for span, twin in zip(doc["pii_spans"], g8[doc["id"]]["pii_spans"], strict=True):
            if span["category"] != "address":
                continue
            assert (span["value"] == twin["value"]) == keep, (doc["id"], drawn)
            kept += keep
            redrawn += not keep
            if drawn == LOCALE_ES_MX:
                assert _MX_CP_RE.search(span["value"]), span["value"]
                assert not _ZIP_PLUS_FOUR_RE.search(span["value"]), span["value"]
    assert kept and redrawn
    # The axis is crossed with the bucket: every bucket draws every locale.
    assert seen == {(b, loc) for b in BUCKETS for loc in LOCALES}


@pytest.mark.parametrize("profile", (PROFILE_SPEC_H, PROFILE_SPEC_AGH))
def test_spec_h_replaces_every_placeholder_with_a_role_phrase(profile: str) -> None:
    g8 = {d["id"]: d for d in build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS)["documents"]}
    payload = build_g8_corpus(CANONICAL_SEED, counts=_COVERAGE_COUNTS, profile=profile)
    sparse = 0
    for doc in payload["documents"]:
        assert REDACTED_NAME_PLACEHOLDER not in doc["text"], doc["id"]
        twin = g8[doc["id"]]
        if any(s["category"] == "name" for s in twin["pii_spans"]):
            if profile == PROFILE_SPEC_H:
                # Spec-H alone touches nothing but the sparse slots.
                assert doc["text"] == twin["text"], doc["id"]
                assert doc["pii_spans"] == twin["pii_spans"], doc["id"]
            continue
        sparse += 1
        assert twin["text"].count(REDACTED_NAME_PLACEHOLDER) == sum(
            doc["text"].count(w) + doc["text"].count(w[0].upper() + w[1:]) for w in ROLE_WORDS
        ), doc["id"]
    assert sparse


# The canonical (full-count, seed 20260416) Spec-A / G / H builds, pinned:
# the name-class census of every one of them equals g8's (no slot moves
# class), the form census (Spec-A; the caption's 344 full + 86 all_caps are
# the shipped caption, not draws), the per-document locale census (Spec-G)
# and the placeholder census (1,032 sparse slots as furnished; 0 literal
# placeholders under Spec-H). A change here is a template or profile change.
_PINNED_FORM_COUNTS: dict[str, dict[str, int]] = {
    PROFILE_SPEC_A: {
        "full": 1506,
        "initial": 490,
        "last_first": 263,
        "all_caps": 331,
        "suffix": 128,
        "particled": 119,
    },
    PROFILE_SPEC_AGH: {
        "full": 1522,
        "initial": 480,
        "last_first": 250,
        "all_caps": 332,
        "suffix": 117,
        "particled": 136,
    },
}
_PINNED_CAPTION_FORM_COUNTS: dict[str, int] = {"full": 344, "all_caps": 86}
_PINNED_LOCALE_DOC_COUNTS: dict[str, dict[str, int]] = {
    PROFILE_SPEC_G: {"en_US": 962, "es_MX": 57, "es_ES": 81},
    PROFILE_SPEC_AGH: {"en_US": 955, "es_MX": 75, "es_ES": 70},
}
_PINNED_MOVED_ADDRESSES: dict[str, int] = {PROFILE_SPEC_G: 297, PROFILE_SPEC_AGH: 317}
_PINNED_SPARSE_SLOTS = 1032


@pytest.mark.parametrize("profile", _AGH_PROFILES)
def test_spec_agh_censuses_pinned_on_the_canonical_corpus(profile: str) -> None:
    payload = build_g8_corpus(CANONICAL_SEED, profile=profile)
    g8 = {d["id"]: d for d in build_g8_corpus(CANONICAL_SEED)["documents"]}
    assert payload["version"] == 2
    assert payload["profile"] == profile
    docs = payload["documents"]
    by_class: Counter[str] = Counter(
        span["context_class"] for _, span in _all_spans(payload) if span["category"] == "name"
    )
    assert dict(by_class) == _PINNED_NAME_CLASS_COUNTS
    assert all(doc["furniture"] == [] for doc in docs)
    names = [span for _, span in _all_spans(payload) if span["category"] == "name"]
    if profile in _PINNED_FORM_COUNTS:
        assert dict(Counter(s["form"] for s in names)) == _PINNED_FORM_COUNTS[profile]
        assert (
            dict(Counter(s["form"] for s in names if s["context_class"] in _CAPTION_CLASSES))
            == _PINNED_CAPTION_FORM_COUNTS
        )
        slots = [s for s in names if s["context_class"] not in _CAPTION_CLASSES]
        for form, rate in _FORM_RATES.items():
            share = sum(s["form"] == form for s in slots) / len(slots)
            tolerance = 3 * (rate * (1 - rate) / len(slots)) ** 0.5
            assert abs(share - rate) <= tolerance, (form, share, rate)
    else:
        assert not any("form" in s for _, s in _all_spans(payload))
    if profile in _PINNED_LOCALE_DOC_COUNTS:
        locales = Counter(d["pii_spans"][0]["locale"] for d in docs)
        assert dict(locales) == _PINNED_LOCALE_DOC_COUNTS[profile]
        moved = sum(
            s["value"] != t["value"]
            for d in docs
            for s, t in zip(d["pii_spans"], g8[d["id"]]["pii_spans"], strict=True)
            if s["category"] == "address"
        )
        assert moved == _PINNED_MOVED_ADDRESSES[profile]
    else:
        assert not any("locale" in s for _, s in _all_spans(payload))
    placeholders = sum(d["text"].count(REDACTED_NAME_PLACEHOLDER) for d in docs)
    role_phrases = sum(
        d["text"].count(w) + d["text"].count(w[0].upper() + w[1:]) for d in docs for w in ROLE_WORDS
    )
    if profile in (PROFILE_SPEC_H, PROFILE_SPEC_AGH):
        assert (placeholders, role_phrases) == (0, _PINNED_SPARSE_SLOTS)
    else:
        assert (placeholders, role_phrases) == (_PINNED_SPARSE_SLOTS, 0)
