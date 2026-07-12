"""Tests for the credit-card vector builder."""

from __future__ import annotations

from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_credit_card_vectors
from resecta_data.vectors._checksum import luhn_mod10

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_BRAND_ENUM = {"visa", "mastercard", "amex", "discover", "jcb", "unionpay"}


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_credit_card_vectors(CANONICAL_SEED)
    payload_b = build_credit_card_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_luhn_valid_set() -> None:
    """Every ``valid: true`` vector passes Luhn mod-10."""
    payload = build_credit_card_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert luhn_mod10(vec["pan"]), (
                f"Valid vector failed Luhn: {vec['pan']} ({vec['card_brand']})"
            )


def test_luhn_invalid_set() -> None:
    """Every ``valid: false`` vector with ``luhn_failed`` reason fails Luhn."""
    payload = build_credit_card_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if not vec["valid"] and vec["rejection_reason"] == "luhn_failed":
            assert not luhn_mod10(vec["pan"]), (
                f"Invalid vector unexpectedly passed Luhn: {vec['pan']}"
            )


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against credit_card_vectors.schema.json."""
    payload = build_credit_card_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "cc.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "credit_card_vectors")


def test_brand_coverage() -> None:
    """At least one vector per brand enum value."""
    payload = build_credit_card_vectors(CANONICAL_SEED)
    seen = {vec["card_brand"] for vec in payload["vectors"]}
    assert seen == _BRAND_ENUM, f"Missing coverage for brands: {_BRAND_ENUM - seen}"
