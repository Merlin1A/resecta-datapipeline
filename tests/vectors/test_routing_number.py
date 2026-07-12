"""Tests for the ABA routing-number vector builder."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors.routing_number import (
    _ABA_VALID_PREFIX_RANGES,
    _ABA_WEIGHTS,
    _aba_checksum,
    _compute_check_digit,
    _is_valid_prefix,
    build,
)

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_NINE_DIGIT_PATTERN = re.compile(r"^\d{9}$")

# Design-doc §4 verified positives (sanity anchors).
_DESIGN_POSITIVES = [
    "021000021",
    "322271627",
    "124303120",
]


class TestChecksumMath:
    """Unit tests for the ABA checksum and prefix helpers."""

    def test_design_positive_021000021(self) -> None:
        digits = [int(c) for c in "021000021"]
        assert _aba_checksum(digits) == 0

    def test_design_positive_322271627(self) -> None:
        digits = [int(c) for c in "322271627"]
        assert _aba_checksum(digits) == 0

    def test_design_positive_124303120(self) -> None:
        digits = [int(c) for c in "124303120"]
        assert _aba_checksum(digits) == 0

    def test_invalid_checksum_021000020(self) -> None:
        """021000020: valid prefix, but last digit 0 instead of 1."""
        digits = [int(c) for c in "021000020"]
        assert _aba_checksum(digits) != 0

    def test_weights_are_3_7_1_repeating(self) -> None:
        assert _ABA_WEIGHTS == (3, 7, 1, 3, 7, 1, 3, 7, 1)

    def test_checksum_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError):
            _aba_checksum([1, 2, 3])

    def test_compute_check_digit_recovers_valid(self) -> None:
        """_compute_check_digit on first 8 digits of a known valid number produces the 9th."""
        for routing_num in _DESIGN_POSITIVES:
            digits = [int(c) for c in routing_num]
            d9 = _compute_check_digit(digits[:8])
            assert d9 == digits[8], f"{routing_num}: expected d9={digits[8]}, got {d9}"


class TestPrefixValidation:
    """Unit tests for ABA first-two-digit prefix enforcement."""

    def test_prefix_02_valid(self) -> None:
        assert _is_valid_prefix([0, 2, 1, 0, 0, 0, 0, 2, 1])

    def test_prefix_32_valid(self) -> None:
        assert _is_valid_prefix([3, 2, 2, 2, 7, 1, 6, 2, 7])

    def test_prefix_12_valid(self) -> None:
        assert _is_valid_prefix([1, 2, 4, 3, 0, 3, 1, 2, 0])

    def test_prefix_61_valid(self) -> None:
        assert _is_valid_prefix([6, 1, 0, 0, 0, 0, 0, 0, 0])

    def test_prefix_80_valid(self) -> None:
        assert _is_valid_prefix([8, 0, 0, 0, 0, 0, 0, 0, 0])

    def test_prefix_99_invalid(self) -> None:
        """Design-doc §4 adversarial: 999999999 — prefix 99 not in any range."""
        assert not _is_valid_prefix([9, 9, 9, 9, 9, 9, 9, 9, 9])

    def test_prefix_41_invalid(self) -> None:
        """Design-doc §4 adversarial: 411111111 — prefix 41 not in [01,12],[21,32],[61,72],80."""
        assert not _is_valid_prefix([4, 1, 1, 1, 1, 1, 1, 1, 1])

    def test_prefix_13_invalid(self) -> None:
        """Prefix 13 falls in the reserved gap 13-20."""
        assert not _is_valid_prefix([1, 3, 0, 0, 0, 0, 0, 0, 0])

    def test_prefix_00_invalid(self) -> None:
        assert not _is_valid_prefix([0, 0, 0, 0, 0, 0, 0, 0, 0])

    def test_prefix_ranges_cover_expected_values(self) -> None:
        valid = {p for lo, hi in _ABA_VALID_PREFIX_RANGES for p in range(lo, hi + 1)}
        # Spot-check a few known-invalid prefixes.
        for bad in (0, 13, 14, 20, 33, 60, 73, 79, 81, 99):
            assert bad not in valid, f"Prefix {bad} should not be valid"
        # Spot-check a few known-valid prefixes.
        for good in (1, 6, 12, 21, 32, 61, 72, 80):
            assert good in valid, f"Prefix {good} should be valid"


class TestBuilder:
    """Integration tests for the routing_number.build() function."""

    def test_determinism(self, tmp_build_dir: Path) -> None:
        """Two runs with the canonical seed must be byte-identical."""
        payload_a = build(CANONICAL_SEED)
        payload_b = build(CANONICAL_SEED)
        path_a = tmp_build_dir / "a.json"
        path_b = tmp_build_dir / "b.json"
        dump_canonical_json(payload_a, path_a)
        dump_canonical_json(payload_b, path_b)
        assert path_a.read_bytes() == path_b.read_bytes()

    def test_schema(self, tmp_build_dir: Path) -> None:
        """Output validates against routing_number_vectors.schema.json."""
        payload = build(CANONICAL_SEED)
        path = tmp_build_dir / "routing_number_vectors.json"
        dump_canonical_json(payload, path)
        validate_file(path, SCHEMAS_DIR, "routing_number_vectors")

    def test_design_positives_present(self) -> None:
        """Design-doc §4 verified positives must appear in the fixture."""
        payload = build(CANONICAL_SEED)
        numbers = {v["routing_number"] for v in payload["vectors"]}
        for routing_num in _DESIGN_POSITIVES:
            assert routing_num in numbers, (
                f"Design-positive {routing_num} missing from vector fixture"
            )

    def test_valid_vectors_pass_structural_gates(self) -> None:
        """Every valid:true vector has a valid prefix and checksum.

        `valid` carries detector semantics (structural gates only): a valid
        vector may have has_context False — the Swift detector still emits
        it, at base confidence (the W4 gate handles suppression).
        """
        payload = build(CANONICAL_SEED)
        for vec in payload["vectors"]:
            if vec["valid"]:
                digits = [int(c) for c in vec["routing_number"]]
                assert _is_valid_prefix(digits), (
                    f"valid vector {vec['routing_number']} failed prefix check"
                )
                assert _aba_checksum(digits) == 0, (
                    f"valid vector {vec['routing_number']} failed checksum check"
                )
                assert vec["rejection_reason"] is None

    def test_invalid_prefix_vectors_fail_prefix_gate(self) -> None:
        """Every invalid_prefix vector has a first-two-digit prefix outside all valid ranges."""
        payload = build(CANONICAL_SEED)
        for vec in payload["vectors"]:
            if vec["rejection_reason"] == "invalid_prefix":
                digits = [int(c) for c in vec["routing_number"]]
                assert not _is_valid_prefix(digits), (
                    f"invalid_prefix vector {vec['routing_number']} passed prefix check"
                )
                assert vec["valid"] is False

    def test_invalid_checksum_vectors_fail_checksum_gate(self) -> None:
        """Every invalid_checksum vector has a valid prefix but failing checksum."""
        payload = build(CANONICAL_SEED)
        for vec in payload["vectors"]:
            if vec["rejection_reason"] == "invalid_checksum":
                digits = [int(c) for c in vec["routing_number"]]
                assert _is_valid_prefix(digits), (
                    f"invalid_checksum vector {vec['routing_number']} failed prefix check "
                    "(expected valid prefix, invalid checksum only)"
                )
                assert _aba_checksum(digits) != 0, (
                    f"invalid_checksum vector {vec['routing_number']} passed checksum check"
                )
                assert vec["valid"] is False

    def test_no_context_vectors_are_structurally_valid(self) -> None:
        """No-context vectors are valid:true with no keyword (detector
        semantics: structurally valid, emitted at base confidence)."""
        payload = build(CANONICAL_SEED)
        no_context = [v for v in payload["vectors"] if v["valid"] and not v["has_context"]]
        assert no_context, "Expected at least one valid no-context vector"
        for vec in no_context:
            digits = [int(c) for c in vec["routing_number"]]
            assert _is_valid_prefix(digits), (
                f"no-context vector {vec['routing_number']} failed prefix check"
            )
            assert _aba_checksum(digits) == 0, (
                f"no-context vector {vec['routing_number']} failed checksum check"
            )
            assert vec["context_keyword"] is None
            assert vec["rejection_reason"] is None

    def test_all_routing_numbers_are_nine_digits(self) -> None:
        """All routing_number strings must be exactly 9 decimal digits."""
        payload = build(CANONICAL_SEED)
        for vec in payload["vectors"]:
            assert _NINE_DIGIT_PATTERN.match(vec["routing_number"]), (
                f"routing_number {vec['routing_number']!r} is not 9 digits"
            )

    def test_has_context_matches_keyword_presence(self) -> None:
        """has_context=True iff context_keyword is a non-null string."""
        payload = build(CANONICAL_SEED)
        for vec in payload["vectors"]:
            if vec["has_context"]:
                assert vec["context_keyword"] is not None and isinstance(
                    vec["context_keyword"], str
                ), f"has_context=True but context_keyword is null: {vec}"
            else:
                assert vec["context_keyword"] is None, (
                    f"has_context=False but context_keyword is set: {vec}"
                )

    def test_vector_counts(self) -> None:
        """Fixture must contain at least one vector per category."""
        payload = build(CANONICAL_SEED)
        vectors = payload["vectors"]
        valid_count = sum(1 for v in vectors if v["valid"])
        inv_prefix = sum(1 for v in vectors if v["rejection_reason"] == "invalid_prefix")
        inv_checksum = sum(1 for v in vectors if v["rejection_reason"] == "invalid_checksum")
        no_ctx = sum(1 for v in vectors if v["valid"] and not v["has_context"])
        assert valid_count >= 3, f"Expected ≥3 valid vectors, got {valid_count}"
        assert inv_prefix >= 1, "Expected ≥1 invalid_prefix vectors"
        assert inv_checksum >= 1, "Expected ≥1 invalid_checksum vectors"
        assert no_ctx >= 1, "Expected ≥1 no_context vectors"

    def test_generated_by_field(self) -> None:
        payload = build(CANONICAL_SEED)
        assert payload["generated_by"] == "resecta-data/vectors/routing_number"

    def test_seed_recorded(self) -> None:
        payload = build(CANONICAL_SEED)
        assert payload["seed"] == CANONICAL_SEED
