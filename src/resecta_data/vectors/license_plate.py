"""License-plate structural test vector builder.

Emits a deterministic catalog of license-plate candidates exercising the
Swift ``detectLicensePlate`` regex at PIIDetector.swift:795 (pattern
declared at :789-792):

    \\b(?:license\\s+plate|plate\\s+(?:no|number|#)\\.?
       |tag\\s+(?:no|number|#)\\.?
       |lp\\s*#
       |reg(?:istration)?\\s*#
       |veh(?:icle)?\\s+plate)
       [:#\\s]+[A-Z0-9]{2,3}[-\\s]?[A-Z0-9]{2,5}\\b

Case-insensitive. Plate value is two segments: 2-3 alphanumerics, optional
hyphen or single space, 2-5 alphanumerics.

Vectors cover every label variant crossed with every separator and every
plate-shape combination, plus structural rejections (no label, plate
segments out of range).
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/license_plate"
_SCHEMA_VERSION: Final[int] = 1

_LABELS: Final[tuple[str, ...]] = (
    "License plate",
    "license plate",
    "LICENSE PLATE",
    "Plate No",
    "Plate Number",
    "Plate #",
    "plate no.",
    "Tag No",
    "Tag Number",
    "Tag #",
    "LP #",
    "LP#",
    "Reg #",
    "Registration #",
    "Vehicle plate",
    "Veh plate",
)
_SEPARATORS: Final[tuple[str, ...]] = (": ", " ", "# ", ":", "  ")

_VALID_COUNT: Final[int] = 50
_INVALID_NO_LABEL_COUNT: Final[int] = 6
_INVALID_FIRST_SEG_COUNT: Final[int] = 5
_INVALID_SECOND_SEG_COUNT: Final[int] = 5
_INVALID_BAD_INNER_SEP_COUNT: Final[int] = 4

_ALPHANUM: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_INNER_SEPARATORS: Final[tuple[str, ...]] = ("-", " ", "")


def _alphanumeric(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_ALPHANUM) for _ in range(n))


def build(seed: int) -> dict[str, Any]:
    """Return the license-plate structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/license_plate_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid: label x separator x plate-shape.
        for _ in range(_VALID_COUNT):
            label = rng.choice(_LABELS)
            sep = rng.choice(_SEPARATORS)
            first_len = rng.randint(2, 3)
            second_len = rng.randint(2, 5)
            inner_sep = rng.choice(_INNER_SEPARATORS)
            plate = f"{_alphanumeric(rng, first_len)}{inner_sep}{_alphanumeric(rng, second_len)}"
            vectors.append(
                {
                    "text": f"{label}{sep}{plate}",
                    "plate": plate,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": (
                        f"Label ``{label}`` + {first_len}-{second_len} plate "
                        f"shape, inner sep ``{inner_sep!r}``."
                    ),
                },
            )

        # Invalid: no label.
        for _ in range(_INVALID_NO_LABEL_COUNT):
            plate = (
                f"{_alphanumeric(rng, rng.randint(2, 3))}-{_alphanumeric(rng, rng.randint(2, 5))}"
            )
            vectors.append(
                {
                    "text": plate,
                    "plate": plate,
                    "valid": False,
                    "rejection_reason": "no_label",
                    "notes": "License-plate regex requires a label prefix.",
                },
            )

        # Invalid: first segment outside [2, 3].
        for _ in range(_INVALID_FIRST_SEG_COUNT):
            label = rng.choice(_LABELS)
            first_len = rng.choice([1, 4, 5])
            plate = f"{_alphanumeric(rng, first_len)}-{_alphanumeric(rng, rng.randint(2, 5))}"
            vectors.append(
                {
                    "text": f"{label} {plate}",
                    "plate": plate,
                    "valid": False,
                    "rejection_reason": "first_segment_out_of_range",
                    "notes": "[A-Z0-9]{2,3} caps the first plate segment to [2, 3].",
                },
            )

        # Invalid: second segment outside [2, 5].
        for _ in range(_INVALID_SECOND_SEG_COUNT):
            label = rng.choice(_LABELS)
            second_len = rng.choice([1, 6, 7])
            plate = f"{_alphanumeric(rng, rng.randint(2, 3))}-{_alphanumeric(rng, second_len)}"
            vectors.append(
                {
                    "text": f"{label} {plate}",
                    "plate": plate,
                    "valid": False,
                    "rejection_reason": "second_segment_out_of_range",
                    "notes": "[A-Z0-9]{2,5} caps the second plate segment to [2, 5].",
                },
            )

        # Invalid: inner separator other than hyphen / single space.
        for idx in range(_INVALID_BAD_INNER_SEP_COUNT):
            label = rng.choice(_LABELS)
            bad_sep = "/._,"[idx % 4]
            plate = (
                f"{_alphanumeric(rng, rng.randint(2, 3))}{bad_sep}"
                f"{_alphanumeric(rng, rng.randint(2, 5))}"
            )
            vectors.append(
                {
                    "text": f"{label} {plate}",
                    "plate": plate,
                    "valid": False,
                    "rejection_reason": "invalid_inner_separator",
                    "notes": "[-\\s]? permits only hyphen or single whitespace.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
