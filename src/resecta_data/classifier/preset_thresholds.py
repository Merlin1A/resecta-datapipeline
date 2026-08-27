"""Build the preset threshold candidates artifact.

Emits ``preset_thresholds_candidates.json`` with three placeholder vectors
(Conservative / Balanced / Aggressive) across the eight detector
categories. The file is deliberately named ``_candidates`` and stays in
``build/`` — final preset thresholds are produced by
the Phase 3b G9 sweep against Swift-side softmax dumps and promoted
manually.

Mechanism-description note: this module does not emit any phrasing about
the thresholds' real-world effect. The ``notes`` field is written to
comply with the mechanism-description rules in ``common/mechanism_language.py``.
"""

from __future__ import annotations

from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.mechanism_language import assert_safe

_MODULE_NAME: Final[str] = "resecta_data.classifier.preset_thresholds"
_SCHEMA_VERSION: Final[int] = 1

CATEGORIES: Final[tuple[str, ...]] = (
    "ssn",
    "npi",
    "dea",
    "dob",
    "address",
    "account",
    "mrn",
    "name",
    # ABA routing number.
    "routingNumber",
    # 8 previously ungated categories.
    # These categories are not swept by sweep_thresholds._CATEGORIES — the
    # score dump does not include them. Hand-set values carry through any
    # sweep+finalize round-trip unchanged (sweep skips categories not in
    # _CATEGORIES; finalize preserves all keys from the sweep_raw artifact).
    "ein",
    "itin",
    "creditCard",
    "email",
    "phone",
    "driversLicense",
    "passport",
    "licensePlate",
)

# Placeholder posterior thresholds. Each preset is a flat vector tuned to a
# rough design-time posture: Conservative leans toward higher thresholds
# (fewer redactions, fewer false positives), Aggressive toward lower.
# These values are intentionally round numbers — the G9 sweep will
# overwrite them with device-validated ones in Phase 3b.
#
# The 0.60 / 0.50 / 0.40 spread across presets is a placeholder posture,
# not a calibrated outcome.
_CONSERVATIVE: Final[dict[str, float]] = {
    "ssn": 0.60,
    "npi": 0.60,
    "dea": 0.60,
    "dob": 0.70,
    "address": 0.65,
    # Account max is exactly 0.75 (AccountDetector
    # scorer profile); 0.70 = max - 0.05 feasibility margin. The previous
    # 0.65 predated the envelope table; shipping and candidates now agree.
    "account": 0.70,
    "mrn": 0.70,
    "name": 0.65,
    # The detector envelope is
    # 0.50 base / 0.88 boosted; 0.70 requires a solid context keyword near
    # a checksum-valid number. Shipping and candidates agree on this row.
    "routingNumber": 0.70,
    # 8 previously ungated categories.
    # Values clamped to detector achievable-max - 0.05 where the design
    # table specified at-max values (email 0.90->0.85, phone 0.80->0.75,
    # DL/passport 0.80->0.75).
    "ein": 0.70,
    "itin": 0.78,
    "creditCard": 0.90,
    "email": 0.85,
    "phone": 0.75,
    "driversLicense": 0.75,
    "passport": 0.75,
    "licensePlate": 0.80,
}

_BALANCED: Final[dict[str, float]] = {
    "ssn": 0.50,
    "npi": 0.50,
    "dea": 0.50,
    "dob": 0.55,
    "address": 0.50,
    "account": 0.55,
    "mrn": 0.55,
    "name": 0.50,
    # 0.60 auto-gates no-context routing candidates (base
    # 0.50) while passing strong-context ones (0.88).
    "routingNumber": 0.60,
    # 8 previously ungated categories
    "ein": 0.55,
    "itin": 0.65,
    "creditCard": 0.88,
    "email": 0.83,
    "phone": 0.70,
    "driversLicense": 0.72,
    "passport": 0.72,
    "licensePlate": 0.65,
}

_AGGRESSIVE: Final[dict[str, float]] = {
    "ssn": 0.35,
    "npi": 0.40,
    "dea": 0.40,
    "dob": 0.40,
    "address": 0.35,
    "account": 0.40,
    "mrn": 0.40,
    "name": 0.35,
    # Matches the shipping aggressive row. 0.50 = detector
    # base, and the gate drops only strictly-below candidates — so at
    # aggressive even no-context candidates surface (intended posture).
    "routingNumber": 0.50,
    # 8 previously ungated categories
    "ein": 0.45,
    "itin": 0.50,
    "creditCard": 0.85,
    "email": 0.80,
    "phone": 0.55,
    "driversLicense": 0.65,
    "passport": 0.65,
    "licensePlate": 0.50,
}

_NOTES: Final[str] = (
    "Placeholder thresholds. The Phase 3b G9 sweep against Swift-side "
    "softmax dumps is designed to replace these values with calibrated "
    "ones; until then, the vectors reflect a rough conservative/balanced/"
    "aggressive posture rather than measured precision-recall behavior."
)


def _check_vector(preset: str, vector: dict[str, float]) -> dict[str, float]:
    if set(vector) != set(CATEGORIES):
        missing = sorted(set(CATEGORIES) - set(vector))
        extra = sorted(set(vector) - set(CATEGORIES))
        raise PipelineError(
            f"Preset {preset!r}: category mismatch (missing={missing}, extra={extra})."
        )
    for category, value in vector.items():
        if not (0.0 <= value <= 1.0):
            raise PipelineError(
                f"Preset {preset!r} category {category!r}: threshold {value} out of [0, 1]."
            )
    return dict(sorted(vector.items()))


def build(seed: int) -> dict[str, Any]:
    """Return the preset threshold candidates payload.

    Args:
        seed: Recorded for uniformity; the placeholder vectors are
            curated, not randomized.

    Returns:
        A payload dict conforming to ``schemas/preset_thresholds.schema.json``.
    """
    assert_safe(_NOTES, context="preset_thresholds_candidates.json notes")

    presets = {
        "conservative": _check_vector("conservative", _CONSERVATIVE),
        "balanced": _check_vector("balanced", _BALANCED),
        "aggressive": _check_vector("aggressive", _AGGRESSIVE),
    }

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "status": "placeholder",
        "notes": _NOTES,
        "categories": list(CATEGORIES),
        "presets": presets,
    }
