"""Tests for the doctype keywords builder."""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.classifier import build_doctype_keywords
from resecta_data.classifier._keyword_data import (
    CANONICAL_CLASSES,
    KEYWORDS_BY_CLASS,
    STRUCTURAL_BY_CLASS,
)
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file

_SCHEMAS = Path(__file__).parent.parent / "schemas"

_MIN_KEYWORDS = 30
_MAX_KEYWORDS = 200
_EXPECTED_CLASSES = 5

# C7 BLS-derived physician-specialty titles appended to MEDICAL.
_BLS_SPECIALTY_TITLES = frozenset(
    {
        "anesthesiologist",
        "cardiologist",
        "dermatologist",
        "endocrinologist",
        "gastroenterologist",
        "geneticist",
        "gynecologist",
        "hematologist",
        "immunologist",
        "neonatologist",
        "nephrologist",
        "neurologist",
        "obstetrician",
        "oncologist",
        "ophthalmologist",
        "orthopedist",
        "otolaryngologist",
        "pediatrician",
        "podiatrist",
        "psychiatrist",
        "pulmonologist",
        "radiologist",
        "rheumatologist",
        "surgeon general",
        "urologist",
    }
)

# Sample of the MeSH-derived top-level headings appended to MEDICAL (C7, M19a).
_MESH_SAMPLE_HEADINGS = frozenset(
    {
        "anatomy",
        "bacteria",
        "cancer",
        "carcinoma",
        "cardiovascular",
        "immunology",
        "microbiology",
        "neoplasm",
        "oncology",
        "pharmacology",
        "physiology",
        "syndrome",
        "toxicology",
        "vaccine",
        "virus",
    }
)


def test_schema_validates(tmp_build_dir: Path) -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    dest = tmp_build_dir / "doctype_keywords.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "doctype_keywords")


def test_deterministic() -> None:
    a = build_doctype_keywords(CANONICAL_SEED)
    b = build_doctype_keywords(CANONICAL_SEED)
    assert a == b


def test_five_classes_in_canonical_order() -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    names = [c["name"] for c in payload["classes"]]
    assert names == list(CANONICAL_CLASSES)
    assert len(names) == _EXPECTED_CLASSES


def test_per_class_keyword_count() -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    for cls in payload["classes"]:
        assert _MIN_KEYWORDS <= len(cls["keywords"]) <= _MAX_KEYWORDS
        assert len(set(cls["keywords"])) == len(cls["keywords"])


def test_keywords_sorted_lowercase() -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    for cls in payload["classes"]:
        assert cls["keywords"] == sorted(cls["keywords"])
        for kw in cls["keywords"]:
            assert kw == kw.lower()


def test_structural_patterns_compile() -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    for cls in payload["classes"]:
        for bonus in cls["structural_bonuses"]:
            # Compiles in Python — Swift NSRegularExpression is ICU but the
            # syntax we use is a conservative common subset.
            re.compile(bonus["pattern"])
            assert 0 < bonus["bonus"] <= 1


def test_term_cap_within_schema_bounds() -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    assert 1 <= payload["term_cap_per_doc"] <= 10


def test_seed_recorded() -> None:
    payload = build_doctype_keywords(CANONICAL_SEED)
    assert payload["seed"] == CANONICAL_SEED


def test_medical_keyword_additions() -> None:
    """C7: the 25 BLS SOC 29-1200 specialty titles are present in MEDICAL."""
    medical = set(KEYWORDS_BY_CLASS["medical"])
    missing = _BLS_SPECIALTY_TITLES - medical
    assert not missing, f"missing BLS specialty titles: {sorted(missing)}"


def test_mesh_keywords_present() -> None:
    """C7 (M19a): MeSH-derived top-level headings are present in MEDICAL."""
    medical = set(KEYWORDS_BY_CLASS["medical"])
    missing = _MESH_SAMPLE_HEADINGS - medical
    assert not missing, f"missing MeSH headings: {sorted(missing)}"


def test_drug_dosage_regex() -> None:
    """C7: drug_dosage bonus matches common dosage forms but not generic units."""
    bonuses = {b[0]: b[1] for b in STRUCTURAL_BY_CLASS["medical"]}
    assert "drug_dosage" in bonuses
    pattern = re.compile(bonuses["drug_dosage"])

    positives = ["250mg", "5.5 ml", "100 IU", "10 mcg", "2 units", "1 unit"]
    for sample in positives:
        assert pattern.search(sample), f"expected match: {sample!r}"

    negatives = ["500 lbs", "12 oz", "3 kg"]
    for sample in negatives:
        assert not pattern.search(sample), f"unexpected match: {sample!r}"


def test_medical_bonus_count() -> None:
    """C7: MEDICAL gains drug_dosage, for 3 bonuses (icd_shape, vitals_bp, drug_dosage)."""
    ids = {b[0] for b in STRUCTURAL_BY_CLASS["medical"]}
    assert {"icd_shape", "vitals_bp", "drug_dosage"} <= ids
    assert len(STRUCTURAL_BY_CLASS["medical"]) >= 2


def test_icd_3char_prefix_regex() -> None:
    """C8: icd_3char_prefix bonus matches 3-char ICD-10-CM section shape."""
    bonuses = {b[0]: b[1] for b in STRUCTURAL_BY_CLASS["medical"]}
    assert "icd_3char_prefix" in bonuses
    pattern = re.compile(bonuses["icd_3char_prefix"])

    positives = ["A00", "J45", "Z99", "N18", "C50"]
    for sample in positives:
        assert pattern.search(sample), f"expected match: {sample!r}"

    negatives = ["U07", "123", "AB1", "A1"]
    for sample in negatives:
        assert not pattern.search(sample), f"unexpected match: {sample!r}"

    # Word-boundary isolation: " A00 " matches the 3-char form, but " A001 "
    # does not expose a 3-char-only match because the trailing digit breaks
    # the right \b anchor.
    assert pattern.search(" A00 ")
    assert not pattern.search(" A001 ")
    assert not pattern.search(" ABCD ")


def test_medical_bonus_count_includes_icd_3char_prefix() -> None:
    """C8: MEDICAL structural-bonus id set is exactly the four expected ids."""
    ids = {b[0] for b in STRUCTURAL_BY_CLASS["medical"]}
    assert ids == {"icd_shape", "vitals_bp", "drug_dosage", "icd_3char_prefix"}


def test_doctype_keywords_byte_deterministic(tmp_build_dir: Path) -> None:
    """C7: canonical JSON form of doctype_keywords is byte-identical across rebuilds."""
    payload_a = build_doctype_keywords(CANONICAL_SEED)
    payload_b = build_doctype_keywords(CANONICAL_SEED)
    path_a = tmp_build_dir / "a_doctype_keywords.json"
    path_b = tmp_build_dir / "b_doctype_keywords.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()
