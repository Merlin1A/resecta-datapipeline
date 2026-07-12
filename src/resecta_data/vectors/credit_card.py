"""Credit-card test vector builder.

Emits a deterministic catalog of PAN candidates covering every brand the
Swift ``detectCreditCards`` triple gate (regex -> Luhn -> prefix) accepts:
Visa, MasterCard (51-55 and 2221-2720), Amex, Discover (6011 and 65), JCB,
UnionPay. See PIIDetector.swift detectCreditCards + hasValidCardPrefix.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

from ._checksum import luhn_mod10

_GENERATED_BY: Final[str] = "resecta-data/vectors/credit_card"
_SCHEMA_VERSION: Final[int] = 1

# Brand-canonical PAN lengths. The Swift regex accepts 13-19 total digits;
# these are the plausible payment-industry defaults we sample for each brand.
_VISA_LENGTH: Final[int] = 16
_VISA_LEGACY_LENGTH: Final[int] = 13
_MC_LENGTH: Final[int] = 16
_AMEX_LENGTH: Final[int] = 15
_DISCOVER_LENGTH: Final[int] = 16
_JCB_LENGTH: Final[int] = 16
_UNIONPAY_LENGTH: Final[int] = 16

# Vector counts per brand.
_VISA_VALID_COUNT: Final[int] = 10
_VISA_INVALID_COUNT: Final[int] = 5
_MC_1X_VALID_COUNT: Final[int] = 8  # 51-55 prefix space
_MC_2X_VALID_COUNT: Final[int] = 4  # 2221-2720 prefix space
_MC_INVALID_COUNT: Final[int] = 5
_AMEX_VALID_COUNT: Final[int] = 6
_AMEX_INVALID_COUNT: Final[int] = 4
_DISCOVER_6011_VALID_COUNT: Final[int] = 4
_DISCOVER_65_VALID_COUNT: Final[int] = 4
_DISCOVER_INVALID_COUNT: Final[int] = 3
_JCB_VALID_COUNT: Final[int] = 6
_JCB_INVALID_COUNT: Final[int] = 3
_UNIONPAY_VALID_COUNT: Final[int] = 4
_UNIONPAY_INVALID_COUNT: Final[int] = 2


def _complete_with_luhn(prefix_digits: str, length: int, rng: random.Random) -> str:
    """Fill ``prefix_digits`` with random digits and a final Luhn check digit.

    The output is exactly ``length`` digits long and passes ``luhn_mod10``.
    """
    padding = length - len(prefix_digits) - 1
    body = prefix_digits + "".join(str(rng.randint(0, 9)) for _ in range(padding))
    # Try each candidate check digit; exactly one makes Luhn pass.
    for check in range(10):
        candidate = body + str(check)
        if luhn_mod10(candidate):
            return candidate
    # Unreachable: Luhn is surjective over a final digit.
    raise AssertionError("No Luhn-valid check digit found")


def _flip_last_digit(pan: str, rng: random.Random) -> str:
    """Return a PAN with the final digit offset so Luhn now fails."""
    last = int(pan[-1])
    offset = rng.randint(1, 9)
    return pan[:-1] + str((last + offset) % 10)


def _valid_record(pan: str, brand: str, notes: str) -> dict[str, Any]:
    return {
        "pan": pan,
        "valid": True,
        "card_brand": brand,
        "rejection_reason": None,
        "notes": notes,
    }


def _invalid_record(
    pan: str,
    brand: str,
    reason: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "pan": pan,
        "valid": False,
        "card_brand": brand,
        "rejection_reason": reason,
        "notes": notes,
    }


def _emit_visa(rng: random.Random, out: list[dict[str, Any]]) -> None:
    """Visa — prefix 4, 16 digits (modern) plus one legacy 13-digit."""
    for _ in range(_VISA_VALID_COUNT - 1):
        pan = _complete_with_luhn("4", _VISA_LENGTH, rng)
        out.append(_valid_record(pan, "visa", "Visa prefix 4, Luhn-valid."))
    legacy_pan = _complete_with_luhn("4", _VISA_LEGACY_LENGTH, rng)
    out.append(_valid_record(legacy_pan, "visa", "Legacy 13-digit Visa, Luhn-valid."))
    for _ in range(_VISA_INVALID_COUNT):
        pan = _flip_last_digit(_complete_with_luhn("4", _VISA_LENGTH, rng), rng)
        out.append(_invalid_record(pan, "visa", "luhn_failed", "Visa prefix, Luhn-broken."))


def _emit_mastercard(rng: random.Random, out: list[dict[str, Any]]) -> None:
    """MasterCard — 51-55 classic range and 2221-2720 BIN-expansion range."""
    for _ in range(_MC_1X_VALID_COUNT):
        prefix = f"5{rng.randint(1, 5)}"
        pan = _complete_with_luhn(prefix, _MC_LENGTH, rng)
        out.append(_valid_record(pan, "mastercard", f"MC prefix {prefix}, Luhn-valid."))
    for _ in range(_MC_2X_VALID_COUNT):
        prefix = str(rng.randint(2221, 2720))
        pan = _complete_with_luhn(prefix, _MC_LENGTH, rng)
        out.append(
            _valid_record(pan, "mastercard", f"MC 2-series prefix {prefix}, Luhn-valid."),
        )
    for _ in range(_MC_INVALID_COUNT):
        prefix = f"5{rng.randint(1, 5)}"
        pan = _flip_last_digit(_complete_with_luhn(prefix, _MC_LENGTH, rng), rng)
        out.append(
            _invalid_record(pan, "mastercard", "luhn_failed", "MC prefix, Luhn-broken."),
        )


def _emit_amex(rng: random.Random, out: list[dict[str, Any]]) -> None:
    """Amex — 34 or 37 prefix, 15 digits."""
    for _ in range(_AMEX_VALID_COUNT):
        prefix = rng.choice(["34", "37"])
        pan = _complete_with_luhn(prefix, _AMEX_LENGTH, rng)
        out.append(_valid_record(pan, "amex", f"Amex prefix {prefix}, Luhn-valid."))
    for _ in range(_AMEX_INVALID_COUNT):
        prefix = rng.choice(["34", "37"])
        pan = _flip_last_digit(_complete_with_luhn(prefix, _AMEX_LENGTH, rng), rng)
        out.append(_invalid_record(pan, "amex", "luhn_failed", "Amex prefix, Luhn-broken."))


def _emit_discover(rng: random.Random, out: list[dict[str, Any]]) -> None:
    """Discover — 6011 and 65 prefixes."""
    for _ in range(_DISCOVER_6011_VALID_COUNT):
        pan = _complete_with_luhn("6011", _DISCOVER_LENGTH, rng)
        out.append(_valid_record(pan, "discover", "Discover prefix 6011, Luhn-valid."))
    for _ in range(_DISCOVER_65_VALID_COUNT):
        pan = _complete_with_luhn("65", _DISCOVER_LENGTH, rng)
        out.append(_valid_record(pan, "discover", "Discover prefix 65, Luhn-valid."))
    for _ in range(_DISCOVER_INVALID_COUNT):
        prefix = rng.choice(["6011", "65"])
        pan = _flip_last_digit(_complete_with_luhn(prefix, _DISCOVER_LENGTH, rng), rng)
        out.append(
            _invalid_record(
                pan,
                "discover",
                "luhn_failed",
                f"Discover prefix {prefix}, Luhn-broken.",
            ),
        )


def _emit_jcb(rng: random.Random, out: list[dict[str, Any]]) -> None:
    """JCB — 3528-3589 prefix, 16 digits."""
    for _ in range(_JCB_VALID_COUNT):
        prefix = str(rng.randint(3528, 3589))
        pan = _complete_with_luhn(prefix, _JCB_LENGTH, rng)
        out.append(_valid_record(pan, "jcb", f"JCB prefix {prefix}, Luhn-valid."))
    for _ in range(_JCB_INVALID_COUNT):
        prefix = str(rng.randint(3528, 3589))
        pan = _flip_last_digit(_complete_with_luhn(prefix, _JCB_LENGTH, rng), rng)
        out.append(
            _invalid_record(pan, "jcb", "luhn_failed", f"JCB prefix {prefix}, Luhn-broken."),
        )


def _emit_unionpay(rng: random.Random, out: list[dict[str, Any]]) -> None:
    """UnionPay — 62 prefix, 16 digits."""
    for _ in range(_UNIONPAY_VALID_COUNT):
        pan = _complete_with_luhn("62", _UNIONPAY_LENGTH, rng)
        out.append(_valid_record(pan, "unionpay", "UnionPay prefix 62, Luhn-valid."))
    for _ in range(_UNIONPAY_INVALID_COUNT):
        pan = _flip_last_digit(_complete_with_luhn("62", _UNIONPAY_LENGTH, rng), rng)
        out.append(
            _invalid_record(pan, "unionpay", "luhn_failed", "UnionPay prefix 62, Luhn-broken."),
        )


def build(seed: int) -> dict[str, Any]:
    """Return the credit-card test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/credit_card_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []
    with seeded_context(seed) as rng:
        _emit_visa(rng, vectors)
        _emit_mastercard(rng, vectors)
        _emit_amex(rng, vectors)
        _emit_discover(rng, vectors)
        _emit_jcb(rng, vectors)
        _emit_unionpay(rng, vectors)

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
