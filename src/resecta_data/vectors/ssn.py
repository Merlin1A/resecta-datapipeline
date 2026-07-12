"""SSN structural test vector builder.

Emits a deterministic catalog of SSN candidates that exercise every SSA
structural rejection rule mirrored by the Swift ``SSNStructuralValidator``.
Rules: reject area 000, area 666, area 900-999, group 00, serial 0000,
literal 078-05-1120, and all-same-digit patterns.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/ssn"
_SCHEMA_VERSION: Final[int] = 1

# Vector counts per category.
_VALID_COUNT: Final[int] = 50
_REJECT_AREA_000_COUNT: Final[int] = 10
_REJECT_AREA_666_COUNT: Final[int] = 10
_REJECT_AREA_900_999_COUNT: Final[int] = 20
_REJECT_GROUP_00_COUNT: Final[int] = 15
_REJECT_SERIAL_0000_COUNT: Final[int] = 15

# Literals and forbidden area ranges.
_LITERAL_078_05_1120: Final[str] = "078-05-1120"
_AREA_666: Final[int] = 666
_FORBIDDEN_AREAS: Final[frozenset[int]] = frozenset(
    {0, _AREA_666, *range(900, 1000)},
)
# The all-same-digit ssn formed by digit ``d`` has area ``ddd``. The area
# rule fires for d=0 (000), d=6 (666), and d=9 (999, in the 900-999 reserved
# range); other digits fall through to the all-same-digit rule.
_ALL_SAME_DIGIT_TRIGGERS_AREA_666: Final[int] = _AREA_666 // 111
_ALL_SAME_DIGIT_TRIGGERS_AREA_900_999: Final[int] = 9


def _valid_area(rng: random.Random) -> int:
    """Return an SSN area code drawn uniformly from [1, 899] \\ {666}."""
    while True:
        area = rng.randint(1, 899)
        if area not in _FORBIDDEN_AREAS:
            return area


def _format(area: int, group: int, serial: int) -> str:
    """Format a numerically represented SSN as ``AAA-GG-SSSS``."""
    return f"{area:03d}-{group:02d}-{serial:04d}"


def _all_same_digit(ssn: str) -> bool:
    """Check whether every digit in an AAA-GG-SSSS SSN is the same."""
    digits = ssn.replace("-", "")
    return len(set(digits)) == 1


def build(seed: int) -> dict[str, Any]:
    """Return the SSN structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/ssn_structural_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid SSNs: area 1-899 (excluding 666), group 01-99, serial 0001-9999,
        # no all-same-digit, and not the literal 078-05-1120.
        produced = 0
        while produced < _VALID_COUNT:
            area = _valid_area(rng)
            group = rng.randint(1, 99)
            serial = rng.randint(1, 9999)
            ssn = _format(area, group, serial)
            if ssn == _LITERAL_078_05_1120 or _all_same_digit(ssn):
                continue
            vectors.append(
                {
                    "ssn": ssn,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Passes every SSA structural rule.",
                }
            )
            produced += 1

        # Area 000.
        for _ in range(_REJECT_AREA_000_COUNT):
            group = rng.randint(1, 99)
            serial = rng.randint(1, 9999)
            vectors.append(
                {
                    "ssn": _format(0, group, serial),
                    "valid": False,
                    "rejection_reason": "area_000",
                    "notes": "Area code 000 is never issued.",
                }
            )

        # Area 666.
        for _ in range(_REJECT_AREA_666_COUNT):
            group = rng.randint(1, 99)
            serial = rng.randint(1, 9999)
            vectors.append(
                {
                    "ssn": _format(666, group, serial),
                    "valid": False,
                    "rejection_reason": "area_666",
                    "notes": "Area code 666 is never issued.",
                }
            )

        # Area 900-999.
        for _ in range(_REJECT_AREA_900_999_COUNT):
            area = rng.randint(900, 999)
            group = rng.randint(1, 99)
            serial = rng.randint(1, 9999)
            vectors.append(
                {
                    "ssn": _format(area, group, serial),
                    "valid": False,
                    "rejection_reason": "area_900_999",
                    "notes": "Area codes 900-999 are reserved for ITINs, not SSNs.",
                }
            )

        # Group 00.
        for _ in range(_REJECT_GROUP_00_COUNT):
            area = _valid_area(rng)
            serial = rng.randint(1, 9999)
            vectors.append(
                {
                    "ssn": _format(area, 0, serial),
                    "valid": False,
                    "rejection_reason": "group_00",
                    "notes": "Group code 00 is never issued.",
                }
            )

        # Serial 0000.
        for _ in range(_REJECT_SERIAL_0000_COUNT):
            area = _valid_area(rng)
            group = rng.randint(1, 99)
            vectors.append(
                {
                    "ssn": _format(area, group, 0),
                    "valid": False,
                    "rejection_reason": "serial_0000",
                    "notes": "Serial 0000 is never issued.",
                }
            )

        # Literal Woolworth promotional SSN.
        vectors.append(
            {
                "ssn": _LITERAL_078_05_1120,
                "valid": False,
                "rejection_reason": "literal_078_05_1120",
                "notes": "Promotional Woolworth wallet SSN — never valid by SSA policy.",
            }
        )

        # All-same-digit: one vector per digit 0..9. The validator evaluates
        # area checks before the all-same rule, so 000-00-0000 fails as area_000
        # and 666-66-6666 as area_666 to preserve the single-reason contract.
        for digit in range(10):
            ssn = f"{digit}{digit}{digit}-{digit}{digit}-{digit}{digit}{digit}{digit}"
            if digit == 0:
                reason = "area_000"
            elif digit == _ALL_SAME_DIGIT_TRIGGERS_AREA_666:
                reason = "area_666"
            elif digit == _ALL_SAME_DIGIT_TRIGGERS_AREA_900_999:
                reason = "area_900_999"
            else:
                reason = "all_same_digit"
            vectors.append(
                {
                    "ssn": ssn,
                    "valid": False,
                    "rejection_reason": reason,
                    "notes": f"Every digit is {digit}.",
                }
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
