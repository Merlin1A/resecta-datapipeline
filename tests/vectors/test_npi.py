"""Tests for the NPI vector builder.

Mirrors the Swift NPI detector contract: ten digits, prefix in {1, 2}, CMS
Luhn with the 80840 prefix prepended. The Python reference checksum lives at
``resecta_data.vectors._checksum.cms_luhn``; live-grep verified 2026-04-26.

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
from resecta_data.vectors import build_npi_vectors
from resecta_data.vectors._checksum import cms_luhn

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_VALID_PREFIXES: Final[frozenset[str]] = frozenset({"1", "2"})
_NPI_LENGTH: Final[int] = 10
_CATEGORY_ENUM: Final[frozenset[str]] = frozenset(
    {
        "valid_individual",
        "valid_organization",
        "invalid_checksum",
        "invalid_prefix",
        "invalid_length",
        "edge_all_same_digit",
    },
)
_HYPOTHESIS = settings(derandomize=True, max_examples=200)


def _is_full_valid_npi(npi: str) -> bool:
    """Detector contract: ten digits, prefix in {1,2}, CMS Luhn passes."""
    if len(npi) != _NPI_LENGTH or not npi.isdigit():
        return False
    if npi[0] not in _VALID_PREFIXES:
        return False
    return cms_luhn(npi) == 0


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_npi_vectors(CANONICAL_SEED)
    payload_b = build_npi_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against npi_test_vectors.schema.json."""
    payload = build_npi_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "npi.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "npi_test_vectors")


def test_valid_rows_pass_full_npi_contract() -> None:
    """Every ``valid: true`` row passes the full detector contract."""
    payload = build_npi_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert _is_full_valid_npi(vec["npi"]), (
                f"Valid vector failed contract: {vec['npi']} ({vec['category']})"
            )


def test_invalid_checksum_rows_fail_cms_luhn() -> None:
    """Every ``invalid_checksum`` row has nonzero CMS Luhn remainder."""
    payload = build_npi_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "invalid_checksum":
            assert cms_luhn(vec["npi"]) != 0, (
                f"invalid_checksum vector unexpectedly Luhn-valid: {vec['npi']}"
            )
            assert vec["valid"] is False


def test_invalid_prefix_rows_have_bad_prefix() -> None:
    """Every ``invalid_prefix`` row's first digit is outside {1, 2}."""
    payload = build_npi_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "invalid_prefix":
            assert vec["npi"][0] not in _VALID_PREFIXES, (
                f"invalid_prefix vector has valid prefix: {vec['npi']}"
            )
            assert vec["valid"] is False


def test_invalid_length_rows_have_bad_length() -> None:
    """Every ``invalid_length`` row has length other than 10."""
    payload = build_npi_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "invalid_length":
            assert len(vec["npi"]) != _NPI_LENGTH, (
                f"invalid_length vector has 10 digits: {vec['npi']}"
            )
            assert vec["valid"] is False


def test_category_coverage() -> None:
    """At least one vector per category enum value."""
    payload = build_npi_vectors(CANONICAL_SEED)
    seen = {vec["category"] for vec in payload["vectors"]}
    assert seen == _CATEGORY_ENUM, f"Missing categories: {_CATEGORY_ENUM - seen}"


def test_expected_luhn_remainder_matches_cms_luhn() -> None:
    """For 10-digit rows, ``expected_luhn_remainder`` equals ``cms_luhn(npi)``."""
    payload = build_npi_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if len(vec["npi"]) == _NPI_LENGTH:
            assert vec["expected_luhn_remainder"] == cms_luhn(vec["npi"]), (
                f"Mismatch for {vec['npi']} ({vec['category']}): "
                f"expected {vec['expected_luhn_remainder']}, got {cms_luhn(vec['npi'])}"
            )


@_HYPOTHESIS
@given(npi=st.from_regex(r"^\d{10}$", fullmatch=True))
def test_property_random_npi_classification(npi: str) -> None:
    """Any 10-digit candidate is fully valid iff prefix in {1,2} AND Luhn passes.

    Crosses the detector contract end-to-end against random inputs, complementing
    the per-helper property tests in tests/test_checksums.py.
    """
    expected = npi[0] in _VALID_PREFIXES and cms_luhn(npi) == 0
    assert _is_full_valid_npi(npi) is expected
