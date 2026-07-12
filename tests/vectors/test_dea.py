"""Tests for the DEA vector builder.

Mirrors the Swift DEA detector contract: two uppercase letters followed by
seven digits, with the seventh derived from the position-weighted checksum
``(d0+d2+d4 + 2*(d1+d3+d5)) mod 10``. The Python reference lives at
``resecta_data.vectors._checksum.{dea_check_digit, dea_is_valid}``;
live-grep verified 2026-04-26.

F-9 ack: HYPOTHESIS_SEED=20260425 (per-test fixed seed via ``derandomize``).
Override via env ``HYPOTHESIS_SEED=<int>`` for broader local runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from hypothesis import given, settings
from hypothesis import strategies as st

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_dea_vectors
from resecta_data.vectors._checksum import dea_check_digit, dea_is_valid

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_DEA_LENGTH: Final[int] = 9
_CATEGORY_ENUM: Final[frozenset[str]] = frozenset(
    {"valid", "invalid_checksum", "invalid_prefix_letter", "invalid_length"},
)
_HYPOTHESIS = settings(derandomize=True, max_examples=200)


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_dea_vectors(CANONICAL_SEED)
    payload_b = build_dea_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against dea_test_vectors.schema.json."""
    payload = build_dea_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "dea.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "dea_test_vectors")


def test_valid_rows_pass_dea_is_valid() -> None:
    """Every ``valid: true`` row passes ``dea_is_valid``."""
    payload = build_dea_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert dea_is_valid(vec["dea"]), f"Valid vector failed dea_is_valid: {vec['dea']}"


def test_invalid_checksum_rows_fail_dea_is_valid() -> None:
    """Every ``invalid_checksum`` row has length 9, valid prefix, bad check."""
    payload = build_dea_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "invalid_checksum":
            assert len(vec["dea"]) == _DEA_LENGTH
            assert vec["dea"][:2].isalpha() and vec["dea"][:2].isupper()
            assert vec["dea"][2:].isdigit()
            assert not dea_is_valid(vec["dea"]), (
                f"invalid_checksum vector unexpectedly valid: {vec['dea']}"
            )
            assert vec["valid"] is False


def test_invalid_prefix_letter_rows_fail_dea_is_valid() -> None:
    """Every ``invalid_prefix_letter`` row has a digit in position 0 or 1."""
    payload = build_dea_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "invalid_prefix_letter":
            assert any(ch.isdigit() for ch in vec["dea"][:2]), (
                f"invalid_prefix_letter row has all-letter prefix: {vec['dea']}"
            )
            assert not dea_is_valid(vec["dea"]), (
                f"invalid_prefix_letter vector unexpectedly valid: {vec['dea']}"
            )
            assert vec["valid"] is False


def test_invalid_length_rows_fail_dea_is_valid() -> None:
    """Every ``invalid_length`` row has length other than 9."""
    payload = build_dea_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "invalid_length":
            assert len(vec["dea"]) != _DEA_LENGTH, (
                f"invalid_length vector has 9 chars: {vec['dea']}"
            )
            assert not dea_is_valid(vec["dea"]), (
                f"invalid_length vector unexpectedly valid: {vec['dea']}"
            )
            assert vec["valid"] is False


def test_category_coverage() -> None:
    """At least one vector per category enum value."""
    payload = build_dea_vectors(CANONICAL_SEED)
    seen = {vec["category"] for vec in payload["vectors"]}
    assert seen == _CATEGORY_ENUM, f"Missing categories: {_CATEGORY_ENUM - seen}"


def test_expected_check_digit_matches_for_valid_rows() -> None:
    """For valid rows, ``expected_check_digit`` matches ``dea_check_digit(dea[2:8])``
    and equals the trailing digit ``dea[8]``."""
    payload = build_dea_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "valid":
            first_six = vec["dea"][2:8]
            assert vec["expected_check_digit"] == dea_check_digit(first_six)
            assert vec["expected_check_digit"] == int(vec["dea"][8])


@_HYPOTHESIS
@given(
    prefix=st.from_regex(r"^[A-Z]{2}$", fullmatch=True),
    digits=st.from_regex(r"^\d{7}$", fullmatch=True),
)
def test_property_random_dea_classification(prefix: str, digits: str) -> None:
    """Any letter-prefixed 9-char DEA is valid iff its check digit matches."""
    candidate = f"{prefix}{digits}"
    expected_check = dea_check_digit(digits[:6])
    expected_valid = int(digits[6]) == expected_check
    assert dea_is_valid(candidate) is expected_valid


@_HYPOTHESIS
@given(
    leading=st.from_regex(r"^[0-9][A-Z]$|^[A-Z][0-9]$", fullmatch=True),
    digits=st.from_regex(r"^\d{7}$", fullmatch=True),
)
def test_property_digit_in_prefix_always_invalid(leading: str, digits: str) -> None:
    """Any 9-char candidate with a digit in position 0 or 1 fails ``dea_is_valid``."""
    candidate = f"{leading}{digits}"
    assert not dea_is_valid(candidate)
