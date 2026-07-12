"""Tests for the MRN vector builder.

Mirrors the three Swift patterns at PIIDetector.swift:707 (``detectMedicalRecords``);
patterns declared at :686-702. Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_mrn_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_MRN_LABELED_RE = re.compile(r"\bMR[N]?[:#\s]+[A-Z0-9]{5,12}\b", re.IGNORECASE)
_MRN_PATIENT_ID_RE = re.compile(r"\bPatient\s+ID[:#\s]+[A-Z0-9]{5,12}\b", re.IGNORECASE)
_MRN_INSTITUTION_RE = re.compile(r"\b[A-Z]{2,5}-\d{6,10}\b")

_RE_BY_VARIANT = {
    "labeled": _MRN_LABELED_RE,
    "patient_id": _MRN_PATIENT_ID_RE,
    "institution": _MRN_INSTITUTION_RE,
}


def test_determinism(tmp_build_dir: Path) -> None:
    payload_a = build_mrn_vectors(CANONICAL_SEED)
    payload_b = build_mrn_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    payload = build_mrn_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "mrn.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "mrn_vectors")


def test_shape_compliance() -> None:
    """Each valid vector matches its variant's regex; each invalid does not."""
    payload = build_mrn_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        regex = _RE_BY_VARIANT[vec["pattern_variant"]]
        m = regex.search(vec["text"])
        if vec["valid"]:
            assert m is not None, (
                f"Valid {vec['pattern_variant']} vector {vec['text']!r} should match"
            )
        else:
            assert m is None, (
                f"Invalid {vec['pattern_variant']} vector {vec['text']!r} should NOT match"
            )


def test_pattern_variant_coverage() -> None:
    """Each of the three variants has at least 30 valid vectors."""
    payload = build_mrn_vectors(CANONICAL_SEED)
    by_variant: dict[str, int] = {"labeled": 0, "patient_id": 0, "institution": 0}
    for vec in payload["vectors"]:
        if vec["valid"]:
            by_variant[vec["pattern_variant"]] += 1
    for variant, count in by_variant.items():
        assert count >= 30, f"Variant {variant!r} has only {count} valid vectors; expected ≥30"
