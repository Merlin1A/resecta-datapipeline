"""Determinism + schema + hand-computed-metric checks for the S3 baseline.

Builds a small synthetic ``_cells.json`` payload inline (no real Swift output
needed), runs ``build_baseline`` twice, and asserts the two emissions are
byte-identical via ``dump_canonical_json``; validates against
``schemas/g8_detection_baseline.schema.json``; and pins a few metric values
computed by hand (e.g. ssn family TP=8, FP=2 -> precision 0.8).

See CONTRACT.md File 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.baseline import build_baseline

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"


def _cell(
    tp: int,
    fn: int,
    fp: int,
    *,
    adv_total: int = 0,
    adv_fired: int = 0,
    neg_ctx: int = 0,
) -> dict[str, int]:
    """Return one raw join cell in the contract's File-1 shape."""
    return {
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "adversarial_suppress_total": adv_total,
        "adversarial_suppress_fired": adv_fired,
        "suppressed_by_negative_context": neg_ctx,
    }


def _synthetic_cells() -> dict[str, Any]:
    """A minimal but axis-spanning synthetic _cells.json payload.

    ssn family is constructed so its aggregate is exactly TP=8, FN=2, FP=2,
    adv_total=2, adv_fired=1 (split across two cells) -> precision 0.8,
    recall 0.8, f1 0.8, adversarial_suppression_fp_rate 0.5.

    The name family adds a deliberately low-support demographic (ai_an, total
    support 2) to exercise the fairness low_confidence flag, plus enough white
    support (>= 30) to leave that bucket NOT flagged.
    """
    return {
        "schema_version": 1,
        "generated_by": "G8BaselineHarness.sweepG8Corpus",
        "g8_corpus_seed": 20260416,
        "cutoff_preset": "balanced",
        "doc_count": 4,
        "cells": {
            # ssn: TP 5+3=8, FN 1+1=2, FP 1+1=2, adv_total 2, adv_fired 1.
            "ssn_court_white": _cell(5, 1, 1, adv_total=2, adv_fired=1),
            "ssn_medical_white": _cell(3, 1, 1, neg_ctx=1),
            # name white: high support (40) so this bucket is not low_confidence.
            "name_financial_white": _cell(36, 4, 2),
            # name ai_an: support 2 (< 30) -> low_confidence on the ai_an bucket
            # AND on the name family if its total support stays < 30 (it won't,
            # because white dominates) -- so this pins the bucket-level flag.
            "name_court_ai_an": _cell(1, 1, 3),
        },
    }


def _run() -> dict[str, Any]:
    return build_baseline(_synthetic_cells())


def test_top_level_shape() -> None:
    payload = _run()
    assert payload["schema_version"] == 1
    assert payload["generated_by"] == "resecta_data.eval.baseline"
    assert payload["metric"] == "g8_detection_baseline"
    assert len(payload["source_cells_sha256"]) == 64
    # All five doctypes and five buckets present even with zero support.
    assert set(payload["per_doctype"]) == {"court", "medical", "financial", "foia", "generic"}
    assert set(payload["per_demographic"]) == {"white", "black", "hispanic", "asian", "ai_an"}
    assert set(payload["per_family"]) == {"ssn", "name"}


def test_ssn_family_metrics_hand_computed() -> None:
    fam = _run()["per_family"]["ssn"]
    # TP=8, FP=2, FN=2.
    assert fam["true_positives"] == 8
    assert fam["false_positives"] == 2
    assert fam["false_negatives"] == 2
    assert fam["support_n"] == 10
    assert fam["detections_n"] == 10
    assert fam["precision"] == pytest.approx(0.8)
    assert fam["recall"] == pytest.approx(0.8)
    assert fam["f1"] == pytest.approx(0.8)
    # adversarial: fired 1 of total 2.
    assert fam["adversarial_suppress_total"] == 2
    assert fam["adversarial_suppress_fired"] == 1
    assert fam["adversarial_suppression_fp_rate"] == pytest.approx(0.5)
    # combined FP = FP + fired = 3; precision_with_decoys = 8/11.
    assert fam["family_false_positive_count"] == 3
    assert fam["precision_with_decoys"] == pytest.approx(8.0 / 11.0)
    # support 10 >= 30 floor? No -> low_confidence True (10 < 30).
    assert fam["low_confidence"] is True


def test_f1_harmonic_mean_when_pr_differ() -> None:
    # name family: TP 36+1=37, FP 2+3=5, FN 4+1=5.
    fam = _run()["per_family"]["name"]
    assert fam["true_positives"] == 37
    assert fam["false_positives"] == 5
    assert fam["false_negatives"] == 5
    precision = 37 / 42
    recall = 37 / 42
    assert fam["precision"] == pytest.approx(precision)
    assert fam["recall"] == pytest.approx(recall)
    assert fam["f1"] == pytest.approx(2 * precision * recall / (precision + recall))


def test_zero_denominator_metrics_are_zero() -> None:
    # An empty doctype (no cells) must report 0.0, not raise.
    foia = _run()["per_doctype"]["foia"]
    assert foia["support_n"] == 0
    assert foia["detections_n"] == 0
    assert foia["precision"] == 0.0
    assert foia["recall"] == 0.0
    assert foia["f1"] == 0.0
    assert foia["adversarial_suppression_fp_rate"] == 0.0
    assert foia["precision_with_decoys"] == 0.0
    assert foia["low_confidence"] is True  # zero support is below the floor


def test_low_confidence_fairness_flag_per_bucket() -> None:
    demo = _run()["per_demographic"]
    # white support: ssn_court_white 5+1=6, ssn_medical_white 3+1=4,
    # name_financial_white 36+4=40 -> 50 (>= 30, so not low_confidence).
    assert demo["white"]["support_n"] == 50
    assert demo["white"]["low_confidence"] is False
    # ai_an: only name_court_ai_an, support 1+1 = 2 < 30 -> flagged.
    assert demo["ai_an"]["support_n"] == 2
    assert demo["ai_an"]["low_confidence"] is True
    # an untouched bucket (black) has zero support -> flagged, not dropped.
    assert demo["black"]["support_n"] == 0
    assert demo["black"]["low_confidence"] is True


def test_totals_are_grand_sum() -> None:
    totals = _run()["totals"]
    # TP across all cells: 5+3+36+1 = 45; FP: 1+1+2+3 = 7; FN: 1+1+4+1 = 7.
    assert totals["true_positives"] == 45
    assert totals["false_positives"] == 7
    assert totals["false_negatives"] == 7
    assert totals["adversarial_suppress_fired"] == 1
    assert totals["family_false_positive_count"] == 7 + 1


def test_byte_identical_across_invocations(tmp_build_dir: Path) -> None:
    a = _run()
    b = _run()
    dest_a = tmp_build_dir / "a.json"
    dest_b = tmp_build_dir / "b.json"
    dump_canonical_json(a, dest_a)
    dump_canonical_json(b, dest_b)
    assert dest_a.read_bytes() == dest_b.read_bytes()


def test_schema_validates(tmp_build_dir: Path) -> None:
    payload = _run()
    dest = tmp_build_dir / "g8_detection_baseline.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_detection_baseline")


def test_malformed_cell_key_fails_loud() -> None:
    bad = {"cells": {"ssn_white": _cell(1, 0, 0)}}  # missing doctype token
    with pytest.raises(ValueError, match="does not end in a known"):
        build_baseline(bad)
