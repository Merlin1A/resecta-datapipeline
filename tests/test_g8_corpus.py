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
