"""SSN structural rule coverage.

Every rejection category defined in the Swift validator must have at least
one vector in the Phase 1 artifact, and every valid row must actually pass
the Python-side mirror rules.
"""

from __future__ import annotations

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.vectors import build_ssn_vectors

REJECTION_CATEGORIES = {
    "area_000",
    "area_666",
    "area_900_999",
    "group_00",
    "serial_0000",
    "literal_078_05_1120",
    "all_same_digit",
}


def _digits(ssn: str) -> tuple[int, int, int]:
    parts = ssn.split("-")
    return int(parts[0]), int(parts[1]), int(parts[2])


def test_every_rejection_category_has_coverage() -> None:
    payload = build_ssn_vectors(CANONICAL_SEED)
    seen = {v["rejection_reason"] for v in payload["vectors"] if v["rejection_reason"] is not None}
    assert seen >= REJECTION_CATEGORIES


def test_valid_rows_satisfy_every_rule() -> None:
    payload = build_ssn_vectors(CANONICAL_SEED)
    for v in payload["vectors"]:
        if not v["valid"]:
            continue
        area, group, serial = _digits(v["ssn"])
        assert area != 0
        assert area != 666
        assert area < 900
        assert group != 0
        assert serial != 0
        assert v["ssn"] != "078-05-1120"
        digits = v["ssn"].replace("-", "")
        assert len(set(digits)) > 1


def test_rejected_rows_carry_matching_reason() -> None:
    """Spot-check that each rejection category's rows actually match the rule."""
    payload = build_ssn_vectors(CANONICAL_SEED)
    for v in payload["vectors"]:
        if v["valid"]:
            continue
        reason = v["rejection_reason"]
        area, group, serial = _digits(v["ssn"])
        if reason == "area_000":
            assert area == 0
        elif reason == "area_666":
            assert area == 666
        elif reason == "area_900_999":
            assert 900 <= area <= 999
        elif reason == "group_00":
            assert group == 0
        elif reason == "serial_0000":
            assert serial == 0
        elif reason == "literal_078_05_1120":
            assert v["ssn"] == "078-05-1120"
        elif reason == "all_same_digit":
            assert len(set(v["ssn"].replace("-", ""))) == 1
