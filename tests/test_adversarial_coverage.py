"""Adversarial pattern coverage assertions."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from resecta_data.adversarial import build
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.schema import load_schema, validate

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

_NEW_CATEGORIES = frozenset(
    {
        "homoglyph",
        "invisible_style",
        "whitespace_injection",
        "multiline_date_collision",
        "column_header_label",
    }
)


def test_detector_shape_fp_has_rows() -> None:
    payload = build(CANONICAL_SEED)
    categories = {p["category"] for p in payload["patterns"]}
    assert "detector_shape_fp" in categories
    fps = [p for p in payload["patterns"] if p["category"] == "detector_shape_fp"]
    # Every SSN/NPI/DEA/address shape should have at least one FP fragment.
    fp_detectors = {p["expected_detector"] for p in fps}
    assert {"ssn", "npi", "dea", "address"} <= fp_detectors


def test_keyword_stuffing_g5_has_rows() -> None:
    payload = build(CANONICAL_SEED)
    stuff = [p for p in payload["patterns"] if p["category"] == "keyword_stuffing_g5"]
    assert len(stuff) >= 1
    for p in stuff:
        assert p["expected_detector"] == "none"
        assert p["expected_outcome"] == "flag"
        assert p["source_plan_ref"].startswith("classifier keyword-stuffing stanza")


def test_ids_are_unique() -> None:
    payload = build(CANONICAL_SEED)
    ids = [p["id"] for p in payload["patterns"]]
    assert len(ids) == len(set(ids))


def test_new_categories_schema() -> None:
    """Every new-category entry validates against the extended schema."""
    payload = build(CANONICAL_SEED)
    schema = load_schema(SCHEMAS_DIR, "adversarial_patterns")
    # Full-payload validation first.
    validate(payload, schema, context="adversarial build()")
    # Then assert each new-category family is present.
    per_category: dict[str, int] = {}
    for entry in payload["patterns"]:
        per_category[entry["category"]] = per_category.get(entry["category"], 0) + 1
    for expected in _NEW_CATEGORIES:
        assert per_category.get(expected, 0) >= 1, (
            f"Expected at least one {expected} entry, got {per_category.get(expected, 0)}"
        )


def test_homoglyph_normalization() -> None:
    """A fullwidth-digit SSN entry's NFKD form matches the plain-ASCII regex.

    The detector stack normalizes text with NFKD before regex matching.
    This pins the invariant that at least one homoglyph fixture still
    resolves to an ASCII SSN shape after normalization.
    """
    payload = build(CANONICAL_SEED)
    homoglyphs = [p for p in payload["patterns"] if p["category"] == "homoglyph"]
    ssn_regex = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    # Find the fullwidth-digit SSN entry (id homo_000).
    matches = [p for p in homoglyphs if p["id"] == "homo_000"]
    assert len(matches) == 1, "fullwidth-digit SSN entry homo_000 must exist"
    entry = matches[0]
    normalized = unicodedata.normalize("NFKD", entry["text"])
    assert ssn_regex.search(normalized), (
        f"NFKD({entry['text']!r}) = {normalized!r} did not match the SSN regex"
    )


def test_bbox_context_only_on_column_header() -> None:
    """bbox_context appears only on column_header_label entries, and every
    such entry carries at least one header bbox plus at least one data bbox."""
    payload = build(CANONICAL_SEED)
    for entry in payload["patterns"]:
        has_bbox = "bbox_context" in entry
        if entry["category"] == "column_header_label":
            assert has_bbox, f"{entry['id']} missing bbox_context"
            roles = {bb["role"] for bb in entry["bbox_context"]}
            assert "header" in roles, f"{entry['id']} has no header bbox"
            assert "data" in roles, f"{entry['id']} has no data bbox"
        else:
            assert not has_bbox, (
                f"{entry['id']} (category={entry['category']}) must not carry bbox_context"
            )


def test_new_category_entry_counts() -> None:
    """Per-family row counts fall within the planned ranges."""
    payload = build(CANONICAL_SEED)
    counts: dict[str, int] = {}
    for entry in payload["patterns"]:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    assert 8 <= counts["homoglyph"] <= 12
    # Invisible-text family splits across invisible_style + whitespace_injection.
    invisible_total = counts["invisible_style"] + counts["whitespace_injection"]
    assert 6 <= invisible_total <= 8
    assert 6 <= counts["column_header_label"] <= 8
    assert 4 <= counts["multiline_date_collision"] <= 6
