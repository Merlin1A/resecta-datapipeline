"""Tests for the DOB vector builder."""

from __future__ import annotations

from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_dob_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_dob_vectors(CANONICAL_SEED)
    payload_b = build_dob_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against dob_vectors.schema.json."""
    payload = build_dob_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "dob.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "dob_vectors")


def test_month_day_validation() -> None:
    """Feb 30 / Apr 31 / Sep 31 etc. appear as invalid vectors with
    ``month_day_overflow`` reason; no such combination is marked valid."""
    payload = build_dob_vectors(CANONICAL_SEED)

    overflow_patterns = {
        ("2", "30"),
        ("4", "31"),
        ("6", "31"),
        ("9", "31"),
        ("11", "31"),
    }

    seen_overflow_combinations: set[tuple[str, str]] = set()
    for vec in payload["vectors"]:
        parts = vec["date_part"].replace("-", "/").split("/")
        if len(parts) != 3:
            continue  # textual format
        month, day, _year = parts
        if (month, day) in overflow_patterns:
            seen_overflow_combinations.add((month, day))
            assert vec["valid"] is False, f"Overflow date {vec['date_part']} marked valid"
            assert vec["rejection_reason"] == "month_day_overflow"

    # At least one representative of each overflow case must exist.
    missing = overflow_patterns - seen_overflow_combinations
    assert not missing, f"Missing overflow cases: {missing}"


def test_feb_29_leap_year_rules() -> None:
    """Feb 29 is valid in leap years and invalid in non-leap years."""
    payload = build_dob_vectors(CANONICAL_SEED)
    leap = {"2000", "2004", "2020", "2024"}
    non_leap = {"1900", "2021", "2023", "2100"}

    for vec in payload["vectors"]:
        parts = vec["date_part"].split("/")
        if len(parts) != 3 or parts[0] != "2" or parts[1] != "29":
            continue
        year = parts[2]
        if year in leap:
            assert vec["valid"] is True, f"Feb 29 {year} should be valid (leap)"
        if year in non_leap:
            assert vec["valid"] is False, f"Feb 29 {year} should be invalid (non-leap)"
            assert vec["rejection_reason"] == "non_leap_feb_29"
