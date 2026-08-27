"""Tests for the per-category positive context-keyword gazetteer.

Covers schema conformance, determinism, the closed 9-category shipping set,
the honorifics drop (engine-side), the staging-fields strip, the bare DEA
gate, the IRSN literal flag, the DOB-family windowed-matching note, the
doctype vocabulary translation, the lifted rows' confidence backfill, the
Bates ``.legal``-scoped anchor surface, and fail-loud guards for missing or
divergent candidates files.
See ``src/resecta_data/gazetteers/context_keywords/build.py`` for the
builder and ``schemas/context_keywords.schema.json`` for the shape.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.context_keywords import build as build_context_keywords

REPO_ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
D11_CANDIDATES_PATH = (
    REPO_ROOT
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "context_keywords"
    / "sources"
    / "d11_lift_candidates.json"
)
D12_CANDIDATES_PATH = (
    REPO_ROOT / "src" / "resecta_data" / "context" / "sources" / "d12_candidates.json"
)
D16_CANDIDATES_PATH = (
    REPO_ROOT / "src" / "resecta_data" / "context" / "sources" / "d16_bates_anchors.json"
)

_CANONICAL_SEED = 20260416

_EXPECTED_SHIPPING_CATEGORIES = frozenset(
    {"bates", "dea", "dob", "ein", "itin", "licenseplate", "mrn", "name", "npi", "ssn"}
)

_EXPECTED_PER_CATEGORY = {
    "ssn": 15,  # +5 IRS TIN labels (search-and-redact release)
    "mrn": 13,
    "bates": 21,  # lifted base 11 + `.legal` anchors 10
    "licenseplate": 15,  # 11 lifted + 4 authored plate label words
    "dob": 26,
    "name": 31,  # +5 IRS 1099/W-2 labels; the four court role nouns are not positive anchors
    "npi": 29,
    "dea": 29,
    "itin": 28,
    "ein": 6,  # EIN category infrastructure (search-and-redact release)
}
_EXPECTED_TOTAL = sum(_EXPECTED_PER_CATEGORY.values())  # 213

# The Bates-anchor file contributes 10 ``.legal``-scoped anchors. They share
# the bates category with the lift's 11 doctype-unscoped baseline rows; the
# distinguisher on the wire is ``doctypes == ["court"]`` (after the
# translation of ``.legal`` → ``court``) versus the lift's ``doctypes == []``.
_EXPECTED_D16_BATES = 10

# Wire-format core keys present on every row.
_WIRE_CORE_KEYS = frozenset({"category", "confidence", "doctypes", "locale", "polarity", "term"})
# Wire-format optional keys present on subsets of rows (engine-routing flags).
_WIRE_OPTIONAL_KEYS = frozenset({"detector_note", "detector_requires_secondary"})
_WIRE_ALLOWED_KEYS = _WIRE_CORE_KEYS | _WIRE_OPTIONAL_KEYS

# Staging fields that must never reach the wire format.
_STAGING_KEYS = frozenset(
    {
        # lift staging
        "proposed_status",
        "non_literal_flag",
        "source_swift_file",
        "source_swift_line",
        # authored staging
        "proposed_doctypes",
        "fp_neighbors",
        "source_url",
        "synthetic_sample",
        "aliases",
        "notes",
        "primary_source_type",
        "license_posture",
        "cross_category",
    }
)

# Wire doctype enum (engine 5-class). Authored candidates use a dotted vocabulary
# (`.legal`, `.medical`, ...); the builder strips the leading `.` and renames
# `legal` → `court`.
_WIRE_DOCTYPE_ENUM = frozenset({"court", "financial", "foia", "generic", "medical"})


@pytest.fixture
def payload() -> dict[str, Any]:
    """Build the shipping payload once per test that needs it."""
    return build_context_keywords(_CANONICAL_SEED)


def test_artifact_exists_and_parses(payload: dict[str, Any], tmp_build_dir: Path) -> None:
    """Dumping via the canonical writer yields a valid JSON file."""
    dest = tmp_build_dir / "context" / "context_keywords.json"
    dump_canonical_json(payload, dest)
    assert dest.is_file()
    loaded = load_json(dest)
    assert loaded == payload


def test_schema_conformance(payload: dict[str, Any], tmp_build_dir: Path) -> None:
    """Payload validates against schemas/context_keywords.schema.json."""
    dest = tmp_build_dir / "context" / "context_keywords.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "context_keywords")


def test_determinism(tmp_build_dir: Path) -> None:
    """Two consecutive builds produce byte-identical output."""
    first = build_context_keywords(_CANONICAL_SEED)
    second = build_context_keywords(_CANONICAL_SEED)
    assert first == second

    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(first, path_a)
    dump_canonical_json(second, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_row_count_is_two_thirteen(payload: dict[str, Any]) -> None:
    """The three candidate files produce exactly 213 a21-shipping rows."""
    assert len(payload["entries"]) == _EXPECTED_TOTAL


def test_per_category_counts(payload: dict[str, Any]) -> None:
    """The per-category split sums to 213."""
    counts = dict(Counter(row["category"] for row in payload["entries"]))
    assert counts == _EXPECTED_PER_CATEGORY


def test_shipping_categories_closed(payload: dict[str, Any]) -> None:
    """Only the ten detector categories ship; honorifics stay engine-side."""
    actual = {row["category"] for row in payload["entries"]}
    assert actual == _EXPECTED_SHIPPING_CATEGORIES


def test_no_honorifics_shipped(payload: dict[str, Any]) -> None:
    """Honorifics never ship into the gazetteer."""
    categories = {row["category"] for row in payload["entries"]}
    assert "honorific" not in categories
    candidates = load_json(D11_CANDIDATES_PATH)
    honorifics = [e for e in candidates if e["category"] == "honorific"]
    assert len(honorifics) > 0, (
        "Candidates file no longer carries honorifics; honorific-drop guard is moot."
    )


def test_polarity_uniform_positive(payload: dict[str, Any]) -> None:
    """The gazetteer ships positives only; negatives live in negative_context.json."""
    for row in payload["entries"]:
        assert row["polarity"] == "positive"


def test_locale_uniform_en(payload: dict[str, Any]) -> None:
    """V1 ships English only; later locales bump version."""
    for row in payload["entries"]:
        assert row["locale"] == "en"


def test_sort_order_category_term(payload: dict[str, Any]) -> None:
    """Rows are emitted in ascending (category, term) order."""
    keys = [(row["category"], row["term"]) for row in payload["entries"]]
    assert keys == sorted(keys)


def test_no_staging_keys_leak(payload: dict[str, Any]) -> None:
    """Staging fields (lift + authored) never land on wire rows."""
    for row in payload["entries"]:
        leaked = _STAGING_KEYS & set(row)
        assert not leaked, f"row {row!r} leaks staging key(s): {leaked}"


def test_wire_format_row_shape(payload: dict[str, Any]) -> None:
    """Each row carries the six core keys plus a subset of optional engine flags."""
    for row in payload["entries"]:
        keys = set(row)
        missing_core = _WIRE_CORE_KEYS - keys
        assert not missing_core, f"row {row!r} missing core key(s): {missing_core}"
        unexpected = keys - _WIRE_ALLOWED_KEYS
        assert not unexpected, f"row {row!r} has unexpected key(s): {unexpected}"


def test_seed_recorded(payload: dict[str, Any]) -> None:
    """The CLI-supplied seed is recorded verbatim for reproducibility."""
    assert payload["seed"] == _CANONICAL_SEED


def test_version_is_one(payload: dict[str, Any]) -> None:
    """V1 ships at schema version 1."""
    assert payload["version"] == 1


def test_generated_by_identifies_builder(payload: dict[str, Any]) -> None:
    """generated_by names this builder for provenance."""
    assert "context_keywords" in payload["generated_by"]


def test_doctypes_translated_to_wire_vocabulary(payload: dict[str, Any]) -> None:
    """Candidates' dotted vocabulary (.legal/.medical/...) → wire enum.

    The wire format uses unprefixed engine doctype names (`court`, `medical`,
    `foia`, `financial`, `generic`). No row carries a leading-dot value or the
    candidates-vocabulary `legal` term (which renames to `court`).
    """
    for row in payload["entries"]:
        for value in row["doctypes"]:
            assert not value.startswith("."), (
                f"row {row['term']!r} has untranslated dotted doctype {value!r}"
            )
            assert value != "legal", (
                f"row {row['term']!r} has untranslated `legal`; expected `court`"
            )
            assert value in _WIRE_DOCTYPE_ENUM, (
                f"row {row['term']!r} has unexpected wire doctype {value!r}"
            )


def test_d11_rows_get_confidence_high_backfill(payload: dict[str, Any]) -> None:
    """Lifted rows have no candidates-file confidence; builder backfills `high`.

    Lifted baseline rows are doctype-unscoped (``doctypes == []``); the Bates
    Bates ``.legal`` anchors live in the same `bates` category but carry
    ``doctypes == ["court"]`` and preserve their candidate-side confidence
    (authored-style projection). Scope the assertion to the lifted surface by
    filtering out doctype-scoped rows.
    """
    d11_categories = {"bates", "licenseplate", "mrn", "ssn"}
    for row in payload["entries"]:
        if row["category"] in d11_categories and not row["doctypes"]:
            assert row["confidence"] == "high", (
                f"lifted row {row!r} should be backfilled to high confidence"
            )


def test_bare_dea_carries_secondary_gate(payload: dict[str, Any]) -> None:
    """The bare `DEA` row carries `detector_requires_secondary: true` + low confidence."""
    bare_dea = [r for r in payload["entries"] if r["category"] == "dea" and r["term"] == "DEA"]
    assert len(bare_dea) == 1, "expected exactly one bare-DEA row"
    row = bare_dea[0]
    assert row["detector_requires_secondary"] is True
    assert row["confidence"] == "low"


def test_irsn_carries_medium_flag_confidence(payload: dict[str, Any]) -> None:
    """The IRSN literal carries `confidence: medium (flag)` to the wire."""
    irsn = [r for r in payload["entries"] if r["term"] == "IRSN"]
    assert len(irsn) == 1, "expected exactly one IRSN row"
    assert irsn[0]["confidence"] == "medium (flag)"
    assert irsn[0]["category"] == "itin"


def test_dob_family_carries_detector_note(payload: dict[str, Any]) -> None:
    """The 13 DOB-family rows carry the windowed-matching detector_note.

    Per the candidates file, exactly 13 of the 26 DOB rows carry the `birth`
    head-noun adjacency note. The other DOB rows are explicit literals
    (`DOB`-shaped tokens with their own structure) and do not need the note.
    """
    dob_with_note = [
        r for r in payload["entries"] if r["category"] == "dob" and "detector_note" in r
    ]
    assert len(dob_with_note) == 13


def test_no_x12_segment_literal_in_wire(payload: dict[str, Any]) -> None:
    """No X12 segment mnemonic ships as a literal `term`.

    The X12 DMG*D8 surface is detected engine-side via the pattern-class regex
    ``(?<![A-Z0-9])[A-Z]{3}\\*[A-Z0-9]{2}(?![A-Z0-9])`` — the literal mnemonic
    never appears in the shipping artifact. This guard catches accidental
    re-introduction of any three-letter segment + qualifier literal.
    """
    pattern = re.compile(r"^[A-Z]{3}\*[A-Z0-9]{2}$")
    offenders = [r["term"] for r in payload["entries"] if pattern.match(r["term"])]
    assert not offenders, (
        f"X12 segment mnemonic literal(s) shipped to wire: {offenders!r}; "
        "X12 segment mnemonics ship as engine-side regex, not literal context-keywords."
    )


def test_only_known_optional_flags_appear(payload: dict[str, Any]) -> None:
    """No engine-routing flag appears on the wire that isn't in `_WIRE_OPTIONAL_KEYS`."""
    for row in payload["entries"]:
        extras = set(row) - _WIRE_CORE_KEYS
        assert extras <= _WIRE_OPTIONAL_KEYS, (
            f"row {row['term']!r} carries unexpected optional key(s): {extras}"
        )


def test_d12_authored_confidence_passthrough(payload: dict[str, Any]) -> None:
    """Authored rows preserve their candidates-file confidence verbatim (no backfill).

    The check is restricted to rows whose (category, term) key exists in the
    authored file. SSN rows that originated in the Swift lift are excluded;
    only the 5 authored SSN rows and 6 EIN rows are checked alongside the
    original authored rows (dea 29 + dob 26 + itin 28 + name 31 + npi 29
    = 143; ssn 5 + ein 6 = 11 later additions; total authored shipping
    rows = 154).
    """
    candidates = load_json(D12_CANDIDATES_PATH)
    by_term = {(c["category"], c["term"]): c["confidence"] for c in candidates}
    seen = 0
    for row in payload["entries"]:
        key = (row["category"], row["term"])
        if key in by_term:
            assert row["confidence"] == by_term[key], (
                f"row {row['term']!r}: expected confidence {by_term[key]!r},"
                f" got {row['confidence']!r}"
            )
            seen += 1
    assert seen == 154


def test_d16_bates_legal_anchors_ship(payload: dict[str, Any]) -> None:
    """The 10 ``.legal``-scoped Bates anchors ship with ``doctypes == ["court"]``.

    The candidates-side ``proposed_doctypes: [".legal"]`` translates to
    wire ``doctypes: ["court"]``. The anchors share the bates
    category with the lift's 11 baseline rows; doctype scoping is what
    distinguishes them on the wire. Confidence is preserved verbatim from
    the candidates file (authored-style projection) — no backfill applies to
    the anchor rows.
    """
    d16_rows = [r for r in payload["entries"] if r["category"] == "bates" and r["doctypes"]]
    assert len(d16_rows) == _EXPECTED_D16_BATES, (
        f"expected {_EXPECTED_D16_BATES} bates anchors, got {len(d16_rows)}"
    )
    candidates = load_json(D16_CANDIDATES_PATH)
    expected_terms = {c["term"] for c in candidates}
    assert {r["term"] for r in d16_rows} == expected_terms
    for row in d16_rows:
        assert row["doctypes"] == ["court"], (
            f"anchor row {row['term']!r} expected doctypes=['court'], got {row['doctypes']!r}"
        )
    by_term = {c["term"]: c["confidence"] for c in candidates}
    for row in d16_rows:
        assert row["confidence"] == by_term[row["term"]], (
            f"anchor row {row['term']!r}: candidate confidence "
            f"{by_term[row['term']]!r} should pass through verbatim"
        )


def test_d16_does_not_duplicate_d11_bates_terms(payload: dict[str, Any]) -> None:
    """Bates anchors are multi-word phrases that do not collide with the lifted baseline.

    The lifted baseline (``bates``, ``deposition``, ``discovery``, ``exhibit``,
    ``foia``, ``produced``, ``production``, ``response``, ``responsive``,
    ``stamp``, ``stamped``) is doctype-unscoped; the anchors are
    multi-word phrases scoped to ``[court]``. Guard against accidental
    re-introduction of an identical ``term`` in both sources, which would
    sort adjacently but ship as distinct rows.
    """
    d11_bates_terms = {
        r["term"] for r in payload["entries"] if r["category"] == "bates" and not r["doctypes"]
    }
    d16_bates_terms = {
        r["term"] for r in payload["entries"] if r["category"] == "bates" and r["doctypes"]
    }
    overlap = d11_bates_terms & d16_bates_terms
    assert not overlap, f"lifted and anchor bates rows share term(s): {overlap}"


def test_builder_raises_when_d11_candidates_missing(tmp_path: Path) -> None:
    """Fail-loud: a missing lifted-candidates file is a build error."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(PipelineError, match="missing"):
        build_context_keywords(_CANONICAL_SEED, d11_candidates_path=missing)


def test_builder_raises_when_d12_candidates_missing(tmp_path: Path) -> None:
    """Fail-loud: a missing authored-candidates file is a build error."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(PipelineError, match="missing"):
        build_context_keywords(_CANONICAL_SEED, d12_candidates_path=missing)


def test_builder_raises_when_d16_candidates_missing(tmp_path: Path) -> None:
    """Fail-loud: a missing Bates-anchor file is a build error."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(PipelineError, match="missing"):
        build_context_keywords(_CANONICAL_SEED, d16_candidates_path=missing)


def test_builder_raises_when_candidates_file_not_array(tmp_path: Path) -> None:
    """Both candidates files must be top-level JSON arrays."""
    bad = tmp_path / "wrong_shape.json"
    dump_canonical_json({"entries": []}, bad)
    with pytest.raises(PipelineError, match="malformed"):
        build_context_keywords(_CANONICAL_SEED, d11_candidates_path=bad)


def test_builder_raises_on_unknown_doctype_value(tmp_path: Path) -> None:
    """Translating an unknown doctype value fails loud (catches future vocabulary drift)."""
    candidates = load_json(D12_CANDIDATES_PATH)
    mutated = [dict(e) for e in candidates]
    mutated[0]["proposed_doctypes"] = [".unknown_class"]
    mutated_path = tmp_path / "candidates.json"
    dump_canonical_json(mutated, mutated_path)
    with pytest.raises(PipelineError, match="untranslatable"):
        build_context_keywords(_CANONICAL_SEED, d12_candidates_path=mutated_path)


def test_builder_raises_when_per_category_count_diverges(tmp_path: Path) -> None:
    """Reclassifying an ssn row as mrn keeps the total at 187 but skews the split."""
    candidates = load_json(D11_CANDIDATES_PATH)
    mutated = [dict(e) for e in candidates]
    first_ssn = next(e for e in mutated if e["category"] == "ssn")
    first_ssn["category"] = "mrn"
    mutated_path = tmp_path / "candidates.json"
    dump_canonical_json(mutated, mutated_path)
    with pytest.raises(PipelineError, match="per-category"):
        build_context_keywords(_CANONICAL_SEED, d11_candidates_path=mutated_path)


def test_builder_raises_when_total_count_diverges(tmp_path: Path) -> None:
    """Adding a duplicate a21-shipping row trips the total-count guard."""
    candidates = load_json(D11_CANDIDATES_PATH)
    extra = next(dict(e) for e in candidates if e["category"] == "ssn")
    extra["term"] = extra["term"] + "_dup_for_test"
    mutated_path = tmp_path / "candidates.json"
    dump_canonical_json([*candidates, extra], mutated_path)
    with pytest.raises(PipelineError, match="shipping rows"):
        build_context_keywords(_CANONICAL_SEED, d11_candidates_path=mutated_path)


# -----------------------------------------------------------------------------
# EIN category infrastructure (search-and-redact release)
# -----------------------------------------------------------------------------


def test_ein_category_in_schema() -> None:
    """'ein' appears in the context_keywords.schema.json category enum."""
    schema = json.loads((SCHEMAS_DIR / "context_keywords.schema.json").read_text())
    category_enum = schema["properties"]["entries"]["items"]["properties"]["category"]["enum"]
    assert "ein" in category_enum, f"'ein' not in schema enum: {category_enum}"


def test_ein_expected_count(payload: dict[str, Any]) -> None:
    """The built artifact contains exactly 6 EIN rows."""
    ein_rows = [e for e in payload["entries"] if e["category"] == "ein"]
    assert len(ein_rows) == 6, f"Expected 6 EIN rows in the built artifact, got {len(ein_rows)}"


def test_ein_candidates_count() -> None:
    """The authored candidates file contains exactly 6 EIN rows."""
    candidates = load_json(D12_CANDIDATES_PATH)
    ein_candidates = [
        c
        for c in candidates
        if c["category"] == "ein" and c.get("proposed_status") == "a21-shipping"
    ]
    assert len(ein_candidates) == 6, f"Expected 6 EIN candidates, got {len(ein_candidates)}"


def test_ein_rows_financial_doctype(payload: dict[str, Any]) -> None:
    """All EIN rows ship with doctypes containing 'financial'."""
    ein_rows = [e for e in payload["entries"] if e["category"] == "ein"]
    for row in ein_rows:
        assert "financial" in row["doctypes"], (
            f"EIN row {row['term']!r} does not have 'financial' in doctypes: {row['doctypes']}"
        )
