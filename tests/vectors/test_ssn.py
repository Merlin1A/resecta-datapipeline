"""Tests for the SSN structural vector builder.

Mirrors the Swift ``SSNStructuralValidator`` contract: reject area 000, area
666, area 900-999, group 00, serial 0000, the literal 078-05-1120, and any
all-same-digit SSN. Live-grep verified 2026-04-26 against
``src/resecta_data/vectors/ssn.py``.

F-9 ack: HYPOTHESIS_SEED=20260425 (per-test fixed seed via ``derandomize``).
Override via env ``HYPOTHESIS_SEED=<int>`` for broader local runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from hypothesis import given, settings
from hypothesis import strategies as st

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_ssn_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_SSN_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{3})-(\d{2})-(\d{4})$")
_LITERAL_078_05_1120: Final[str] = "078-05-1120"
_REJECTION_REASONS: Final[frozenset[str]] = frozenset(
    {
        "area_000",
        "area_666",
        "area_900_999",
        "group_00",
        "serial_0000",
        "literal_078_05_1120",
        "all_same_digit",
    },
)
_HYPOTHESIS = settings(derandomize=True, max_examples=300)


def _classify(ssn: str) -> str | None:
    """Mirror the Swift validator: return rejection reason, or None if valid.

    Rule precedence (must match builder + Swift): area_000 → area_666 →
    area_900_999 → group_00 → serial_0000 → literal_078_05_1120 →
    all_same_digit. None means structurally valid.
    """
    m = _SSN_RE.match(ssn)
    if m is None:
        return "malformed"
    area = int(m.group(1))
    group = int(m.group(2))
    serial = int(m.group(3))
    digits = ssn.replace("-", "")

    rules: tuple[tuple[bool, str], ...] = (
        (area == 0, "area_000"),
        (area == 666, "area_666"),
        (900 <= area <= 999, "area_900_999"),
        (group == 0, "group_00"),
        (serial == 0, "serial_0000"),
        (ssn == _LITERAL_078_05_1120, "literal_078_05_1120"),
        (len(set(digits)) == 1, "all_same_digit"),
    )
    for predicate, reason in rules:
        if predicate:
            return reason
    return None


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_ssn_vectors(CANONICAL_SEED)
    payload_b = build_ssn_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against ssn_structural_vectors.schema.json."""
    payload = build_ssn_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "ssn.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "ssn_structural_vectors")


def test_valid_rows_classify_as_valid() -> None:
    """Every ``valid: true`` row's structural rules all pass."""
    payload = build_ssn_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert _classify(vec["ssn"]) is None, (
                f"Valid vector classified as rejected: {vec['ssn']}"
            )
            assert vec["rejection_reason"] is None


def test_invalid_rows_classify_with_matching_reason() -> None:
    """Each invalid row's ``rejection_reason`` matches the validator's verdict."""
    payload = build_ssn_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if not vec["valid"]:
            verdict = _classify(vec["ssn"])
            assert verdict == vec["rejection_reason"], (
                f"Reason mismatch for {vec['ssn']}: "
                f"builder={vec['rejection_reason']}, validator={verdict}"
            )


def test_literal_woolworth_present() -> None:
    """The promotional Woolworth SSN appears with the dedicated rejection reason."""
    payload = build_ssn_vectors(CANONICAL_SEED)
    woolworth = [vec for vec in payload["vectors"] if vec["ssn"] == _LITERAL_078_05_1120]
    assert len(woolworth) == 1
    assert woolworth[0]["valid"] is False
    assert woolworth[0]["rejection_reason"] == "literal_078_05_1120"


def test_rejection_reason_coverage() -> None:
    """Every rejection-reason enum value appears at least once."""
    payload = build_ssn_vectors(CANONICAL_SEED)
    seen = {vec["rejection_reason"] for vec in payload["vectors"] if vec["rejection_reason"]}
    assert seen == _REJECTION_REASONS, f"Missing reasons: {_REJECTION_REASONS - seen}"


@_HYPOTHESIS
@given(
    area=st.integers(min_value=0, max_value=999),
    group=st.integers(min_value=0, max_value=99),
    serial=st.integers(min_value=0, max_value=9999),
)
def test_property_random_ssn_classification(area: int, group: int, serial: int) -> None:
    """Any (area, group, serial) triple is classified consistently with the rules.

    Validates the validator contract holistically: a structurally-valid SSN must
    satisfy all six positive predicates simultaneously.
    """
    ssn = f"{area:03d}-{group:02d}-{serial:04d}"
    verdict = _classify(ssn)
    if verdict is None:
        assert area not in (0, 666)
        assert not (900 <= area <= 999)
        assert group != 0
        assert serial != 0
        assert ssn != _LITERAL_078_05_1120
        assert len(set(ssn.replace("-", ""))) > 1
    else:
        assert verdict in _REJECTION_REASONS
