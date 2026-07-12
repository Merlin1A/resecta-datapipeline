"""Medical Record Number structural test vector builder.

Emits a deterministic catalog of MRN candidates exercising the three Swift
patterns at PIIDetector.swift:707 (``detectMedicalRecords``); the patterns
themselves are declared at :686-702:

  - labeled (``mrn.labeled``): ``\\bMR[N]?[:#\\s]+[A-Z0-9]{5,12}\\b``
        Examples: ``MRN: 12345678``, ``MR# AB12345``.
  - patient_id (``mrn.patientID``): ``\\bPatient\\s+ID[:#\\s]+[A-Z0-9]{5,12}\\b``
        Example: ``Patient ID: 87654321``.
  - institution (``mrn.institution``): ``\\b[A-Z]{2,5}-\\d{6,10}\\b``
        Example: ``HOSPITAL-1234567`` (label-prefixed institution shape).

Each variant ships with ~30 vectors covering the alphanumeric / digit-only
shape space plus a handful of structural rejections, for ~90 valid + a
small rejection tail across all three. The ``pattern_variant`` enum tag is
the schema-required dispatch field per M13 / claude.md C4.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/mrn"
_SCHEMA_VERSION: Final[int] = 1

_LABELED_VALID_COUNT: Final[int] = 30
_PATIENT_ID_VALID_COUNT: Final[int] = 30
_INSTITUTION_VALID_COUNT: Final[int] = 30

_LABELED_INVALID_COUNT: Final[int] = 4
_PATIENT_ID_INVALID_COUNT: Final[int] = 4
_INSTITUTION_INVALID_COUNT: Final[int] = 4

_ALPHANUM: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_LETTERS: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_LABELED_PREFIXES: Final[tuple[str, ...]] = ("MRN", "MR", "mrn", "Mr#", "MRN#", "MR")
_LABELED_SEPARATORS: Final[tuple[str, ...]] = (": ", " ", "# ", ":", "#", "  ")

_INSTITUTION_PREFIXES: Final[tuple[str, ...]] = (
    "HOSPITAL",
    "CLINIC",
    "MED",
    "REGNL",
    "STJ",
    "UCSF",
    "MGH",
    "JHU",
    "MAYO",
    "KP",
)


def _alphanumeric(rng: random.Random, length: int) -> str:
    """Return an ``[A-Z0-9]{length}`` string."""
    return "".join(rng.choice(_ALPHANUM) for _ in range(length))


def _digits(rng: random.Random, length: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(length))


def build(seed: int) -> dict[str, Any]:
    """Return the MRN structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/mrn_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # ---- mrn.labeled ----
        for _ in range(_LABELED_VALID_COUNT):
            prefix = rng.choice(_LABELED_PREFIXES)
            sep = rng.choice(_LABELED_SEPARATORS)
            value = _alphanumeric(rng, rng.randint(5, 12))
            vectors.append(
                {
                    "text": f"{prefix}{sep}{value}",
                    "value": value,
                    "pattern_variant": "labeled",
                    "valid": True,
                    "rejection_reason": None,
                    "notes": f"Labeled MRN with prefix ``{prefix}``.",
                },
            )

        # Labeled rejections.
        for _ in range(_LABELED_INVALID_COUNT // 2):
            value = _alphanumeric(rng, rng.randint(5, 12))
            vectors.append(
                {
                    "text": value,
                    "value": value,
                    "pattern_variant": "labeled",
                    "valid": False,
                    "rejection_reason": "no_label",
                    "notes": "Labeled MRN regex requires the MR / MRN prefix.",
                },
            )
        for _ in range(_LABELED_INVALID_COUNT - _LABELED_INVALID_COUNT // 2):
            value = _alphanumeric(rng, rng.choice([3, 4, 13, 14]))
            vectors.append(
                {
                    "text": f"MRN: {value}",
                    "value": value,
                    "pattern_variant": "labeled",
                    "valid": False,
                    "rejection_reason": "value_length_out_of_range",
                    "notes": "[A-Z0-9]{5,12} caps value length to [5, 12].",
                },
            )

        # ---- mrn.patientID ----
        for _ in range(_PATIENT_ID_VALID_COUNT):
            sep = rng.choice(_LABELED_SEPARATORS)
            value = _alphanumeric(rng, rng.randint(5, 12))
            vectors.append(
                {
                    "text": f"Patient ID{sep}{value}",
                    "value": value,
                    "pattern_variant": "patient_id",
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Patient ID label with alphanumeric value.",
                },
            )

        # Patient ID rejections.
        for _ in range(_PATIENT_ID_INVALID_COUNT // 2):
            value = _alphanumeric(rng, rng.randint(5, 12))
            vectors.append(
                {
                    "text": value,
                    "value": value,
                    "pattern_variant": "patient_id",
                    "valid": False,
                    "rejection_reason": "no_label",
                    "notes": "Patient ID regex requires the ``Patient ID`` prefix.",
                },
            )
        for _ in range(_PATIENT_ID_INVALID_COUNT - _PATIENT_ID_INVALID_COUNT // 2):
            value = _alphanumeric(rng, rng.choice([3, 4, 13, 14]))
            vectors.append(
                {
                    "text": f"Patient ID: {value}",
                    "value": value,
                    "pattern_variant": "patient_id",
                    "valid": False,
                    "rejection_reason": "value_length_out_of_range",
                    "notes": "[A-Z0-9]{5,12} caps value length to [5, 12].",
                },
            )

        # ---- mrn.institution ----
        for _ in range(_INSTITUTION_VALID_COUNT):
            prefix_len = rng.randint(2, 5)
            prefix = "".join(rng.choice(_LETTERS) for _ in range(prefix_len))
            digits = _digits(rng, rng.randint(6, 10))
            value = f"{prefix}-{digits}"
            vectors.append(
                {
                    "text": value,
                    "value": value,
                    "pattern_variant": "institution",
                    "valid": True,
                    "rejection_reason": None,
                    "notes": f"Institution shape: {prefix_len}-letter prefix + digits.",
                },
            )

        # Institution rejections.
        for _ in range(_INSTITUTION_INVALID_COUNT // 2):
            prefix = "".join(rng.choice(_LETTERS) for _ in range(rng.choice([1, 6, 7])))
            digits = _digits(rng, rng.randint(6, 10))
            value = f"{prefix}-{digits}"
            vectors.append(
                {
                    "text": value,
                    "value": value,
                    "pattern_variant": "institution",
                    "valid": False,
                    "rejection_reason": "prefix_length_out_of_range",
                    "notes": "[A-Z]{2,5} caps the institution prefix to [2, 5] letters.",
                },
            )
        for _ in range(_INSTITUTION_INVALID_COUNT - _INSTITUTION_INVALID_COUNT // 2):
            prefix = "".join(rng.choice(_LETTERS) for _ in range(rng.randint(2, 5)))
            digits = _digits(rng, rng.choice([4, 5, 11, 12]))
            value = f"{prefix}-{digits}"
            vectors.append(
                {
                    "text": value,
                    "value": value,
                    "pattern_variant": "institution",
                    "valid": False,
                    "rejection_reason": "digit_length_out_of_range",
                    "notes": "\\d{6,10} caps the digit run to [6, 10].",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
