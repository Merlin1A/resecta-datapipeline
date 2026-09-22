"""Tests for the credit-card vector builder."""

from __future__ import annotations

import random
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_credit_card_vectors
from resecta_data.vectors._checksum import luhn_mod10
from resecta_data.vectors.credit_card import _complete_with_luhn, _flip_last_digit

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


_PREFIX = st.from_regex(r"^[1-9]\d{0,5}$", fullmatch=True)
_LENGTH = st.integers(min_value=13, max_value=19)
_SEED = st.integers(min_value=0, max_value=2**32 - 1)


@settings(deadline=None)
@given(prefix=_PREFIX, length=_LENGTH, seed=_SEED)
def test_luhn_completion_is_sized_prefixed_and_valid(prefix: str, length: int, seed: int) -> None:
    """For every prefix, PAN length and seed the completion keeps the prefix, has exactly
    ``length`` digits and passes Luhn."""
    pan = _complete_with_luhn(prefix, length, random.Random(seed))  # noqa: S311 — determinism
    assert len(pan) == length
    assert pan.startswith(prefix)
    assert luhn_mod10(pan)


@settings(deadline=None)
@given(prefix=_PREFIX, length=_LENGTH, seed=_SEED)
def test_flipped_last_digit_always_fails_luhn(prefix: str, length: int, seed: int) -> None:
    """The decoy construction is sound: offsetting the undoubled final digit by 1..9 always
    breaks the Luhn sum."""
    rng = random.Random(seed)  # noqa: S311 — determinism, not security
    pan = _complete_with_luhn(prefix, length, rng)
    assert not luhn_mod10(_flip_last_digit(pan, rng))


def test_brand_coverage() -> None:
    """At least one vector per brand enum value."""
    payload = build_credit_card_vectors(CANONICAL_SEED)
    seen = {vec["card_brand"] for vec in payload["vectors"]}
    assert seen == _BRAND_ENUM, f"Missing coverage for brands: {_BRAND_ENUM - seen}"
