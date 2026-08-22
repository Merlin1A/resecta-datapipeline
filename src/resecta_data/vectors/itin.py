"""ITIN structural test vector builder.

Emits a deterministic catalog of Individual Taxpayer Identification Number
candidates exercising both the 9XX area-code gate (Swift regex
``9\\d{2}-\\d{2}-\\d{4}``) and the IRS YY-bucket enforcement the Swift
detector tightens to in the paired PR.

Per M6 (tighten-swift-in-c3): IRS-issued ITINs carry YY in one of four
ranges: 50-65, 70-88, 90-92, 94-99. Every other YY is rejected. Vectors
ship with a ``yy_bucket`` field set to ``"valid"`` or ``"invalid"`` so
Swift tests can cross-check the detector's gate.
"""

from __future__ import annotations

from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/itin"
_SCHEMA_VERSION: Final[int] = 1

# IRS-published YY ranges. A YY is valid iff it falls in at least one.
_VALID_YY_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (50, 65),
    (70, 88),
    (90, 92),
    (94, 99),
)

# A two-digit YY field can hold values in [0, 99] inclusive.
_YY_MIN: Final[int] = 0
_YY_MAX: Final[int] = 99


def _yy_is_valid(yy: int) -> bool:
    """Return True iff ``yy`` lies in one of the four IRS ranges."""
    return any(low <= yy <= high for low, high in _VALID_YY_RANGES)


# Every boundary of the four ranges plus the YY immediately outside it.
# Covers ``test_yy_coverage``: one vector per ±1 neighbor of each endpoint.
_BOUNDARY_YY: Final[tuple[int, ...]] = (
    49,
    50,
    65,
    66,  # below 50; 50-65 endpoints; above 65
    69,
    70,
    88,
    89,  # in the 66-69 gap; 70-88 endpoints; above 88
    90,
    92,
    93,  # 90-92 endpoints; gap 93
    94,
    99,  # 94-99 endpoints
    0,
    100,  # 100 wraps in string formatting but tests the clamp
)
# 100 cannot appear in a two-digit YY field; the sample below uses 0 as
# the below-minimum case and skips the above-maximum case (the regex pattern
# forbids a three-digit YY anyway).


# Counts for the sampled-vector layers (in addition to boundary vectors).
_SAMPLED_VALID_COUNT: Final[int] = 25
_SAMPLED_INVALID_YY_COUNT: Final[int] = 15


def _format(area: int, yy: int, serial: int) -> str:
    """Format an ITIN candidate as ``9AA-YY-SSSS``."""
    return f"{area:03d}-{yy:02d}-{serial:04d}"


def build(seed: int) -> dict[str, Any]:
    """Return the ITIN structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/itin_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Boundary coverage: one vector per YY in the boundary set.
        # These are the vectors ``test_yy_coverage`` asserts against.
        for yy in _BOUNDARY_YY:
            if not (_YY_MIN <= yy <= _YY_MAX):
                continue
            area = rng.randint(900, 999)
            serial = rng.randint(1, 9999)
            itin = _format(area, yy, serial)
            bucket = "valid" if _yy_is_valid(yy) else "invalid"
            vectors.append(
                {
                    "itin": itin,
                    "valid": bucket == "valid",
                    "yy_bucket": bucket,
                    "rejection_reason": None if bucket == "valid" else "yy_out_of_range",
                    "notes": f"Boundary sample: YY={yy:02d}.",
                },
            )

        # Sampled valid YYs drawn from inside the IRS ranges. Covers the
        # general-case path rather than just endpoints.
        valid_yy_space = sorted(
            {yy for low, high in _VALID_YY_RANGES for yy in range(low, high + 1)},
        )
        for _ in range(_SAMPLED_VALID_COUNT):
            yy = rng.choice(valid_yy_space)
            area = rng.randint(900, 999)
            serial = rng.randint(1, 9999)
            itin = _format(area, yy, serial)
            vectors.append(
                {
                    "itin": itin,
                    "valid": True,
                    "yy_bucket": "valid",
                    "rejection_reason": None,
                    "notes": f"YY={yy:02d} inside an IRS range.",
                },
            )

        # Sampled invalid YYs from the gaps between ranges.
        invalid_yy_space = sorted(yy for yy in range(0, 100) if not _yy_is_valid(yy))
        for _ in range(_SAMPLED_INVALID_YY_COUNT):
            yy = rng.choice(invalid_yy_space)
            area = rng.randint(900, 999)
            serial = rng.randint(1, 9999)
            itin = _format(area, yy, serial)
            vectors.append(
                {
                    "itin": itin,
                    "valid": False,
                    "yy_bucket": "invalid",
                    "rejection_reason": "yy_out_of_range",
                    "notes": f"YY={yy:02d} outside the four IRS ranges.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
