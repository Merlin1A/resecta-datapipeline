"""Build the adversarial pattern fixture JSON.

The builder is deterministic: given a seed, it returns the same payload. The
seed is recorded in the artifact for reproducibility but does not currently
alter content — the underlying templates are curated lists, not random
samples.
"""

from __future__ import annotations

from typing import Any, Final

from ._templates import (
    COLUMN_HEADER_ENTRIES,
    DETECTOR_SHAPE_FP,
    HOMOGLYPH_ENTRIES,
    INVISIBLE_STYLE_ENTRIES,
    KEYWORD_STUFFING_G5,
    MULTILINE_DATE_ENTRIES,
    WHITESPACE_INJECTION_ENTRIES,
)

_GENERATED_BY: Final[str] = "resecta-data/adversarial/patterns"
# Bumped to 2 when the invisible_style, column_header_label, and
# multiline_date_collision categories plus the optional bbox_context
# field were added.
_SCHEMA_VERSION: Final[int] = 2


def build(seed: int) -> dict[str, Any]:
    """Return the adversarial pattern payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/adversarial_patterns.schema.json``.
    """
    patterns: list[dict[str, Any]] = []

    for index, (text, expected_detector, expected_outcome, plan_ref) in enumerate(
        DETECTOR_SHAPE_FP
    ):
        patterns.append(
            {
                "id": f"fp_{index:03d}",
                "category": "detector_shape_fp",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
            }
        )

    for index, (text, expected_detector, expected_outcome, plan_ref) in enumerate(
        KEYWORD_STUFFING_G5
    ):
        patterns.append(
            {
                "id": f"stuff_{index:03d}",
                "category": "keyword_stuffing_g5",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
            }
        )

    for index, (text, expected_detector, expected_outcome, plan_ref) in enumerate(
        HOMOGLYPH_ENTRIES
    ):
        patterns.append(
            {
                "id": f"homo_{index:03d}",
                "category": "homoglyph",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
            }
        )

    for index, (text, expected_detector, expected_outcome, plan_ref) in enumerate(
        INVISIBLE_STYLE_ENTRIES
    ):
        patterns.append(
            {
                "id": f"inv_{index:03d}",
                "category": "invisible_style",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
            }
        )

    for index, (text, expected_detector, expected_outcome, plan_ref) in enumerate(
        WHITESPACE_INJECTION_ENTRIES
    ):
        patterns.append(
            {
                "id": f"ws_{index:03d}",
                "category": "whitespace_injection",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
            }
        )

    for index, (text, expected_detector, expected_outcome, plan_ref) in enumerate(
        MULTILINE_DATE_ENTRIES
    ):
        patterns.append(
            {
                "id": f"date_{index:03d}",
                "category": "multiline_date_collision",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
            }
        )

    for index, (
        text,
        expected_detector,
        expected_outcome,
        plan_ref,
        bboxes,
    ) in enumerate(COLUMN_HEADER_ENTRIES):
        patterns.append(
            {
                "id": f"col_{index:03d}",
                "category": "column_header_label",
                "text": text,
                "expected_detector": expected_detector,
                "expected_outcome": expected_outcome,
                "source_plan_ref": plan_ref,
                "bbox_context": [
                    {"x": x, "y": y, "w": w, "h": h, "role": role} for (x, y, w, h, role) in bboxes
                ],
            }
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "patterns": patterns,
    }
