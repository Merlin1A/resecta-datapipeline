"""Determinism + schema + hand-computed-metric checks for the baseline.

Builds a small synthetic ``_cells.json`` payload inline (no real Swift output
needed), runs ``build_baseline`` twice, and asserts the two emissions are
byte-identical via ``dump_canonical_json``; validates against
``schemas/g8_detection_baseline.schema.json``; and pins a few metric values
computed by hand (e.g. ssn family TP=8, FP=2 -> precision 0.8).

See ``src/resecta_data/eval/baseline.py`` for the implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.baseline import build_baseline
from resecta_data.eval.payloads import BaselinePayload

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"


def _cell(
    tp: int,
    fn: int,
    fp: int,
    *,
    adv_total: int = 0,
    adv_fired: int = 0,
    neg_ctx: int = 0,
    tiers: dict[str, int] | None = None,
) -> dict[str, int]:
    """Return one raw join cell in the contract's File-1 shape.

    ``tiers`` adds the additive ``tier_*`` counters (1.2 P1.10); a cell
    without them is the pre-extension shape and must still derive.
    """
    cell = {
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "adversarial_suppress_total": adv_total,
        "adversarial_suppress_fired": adv_fired,
        "suppressed_by_negative_context": neg_ctx,
    }
    if tiers:
        cell.update(tiers)
    return cell


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


def _run() -> BaselinePayload:
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


# ---- 1.2 P1.10: F2, Wilson intervals and the packet-tier bridge ----------


def test_f2_and_wilson_beside_legacy_metrics() -> None:
    fam: Mapping[str, Any] = _run()["per_family"]["ssn"]
    # P = R = 0.8 -> F2 = 5PR / (4P + R) = 0.8 as well.
    assert fam["f2"] == pytest.approx(0.8)
    lo, hi = fam["recall_wilson95"]
    assert 0.0 <= lo < 0.8 < hi <= 1.0
    lo_p, hi_p = fam["precision_wilson95"]
    assert 0.0 <= lo_p < 0.8 < hi_p <= 1.0
    # A zero-denominator slice carries null intervals, never a division.
    foia = _run()["per_doctype"]["foia"]
    assert foia["precision_wilson95"] is None
    assert foia["recall_wilson95"] is None
    assert foia["f2"] == 0.0


def test_pre_extension_cells_derive_an_all_zero_tier_block() -> None:
    """A cells file without tier_* counters (P1.1's) still derives, tiers zero."""
    per_tier = _run()["per_family"]["ssn"]["per_tier"]
    assert set(per_tier) == {"must", "should", "watch", "must_not"}
    assert per_tier["must"] == {
        "total": 0,
        "covered": 0,
        "recall": 0.0,
        "recall_wilson95": None,
        "precision_option_c": 1.0,  # nothing fired -> the Option-C 1.0
        "f1_option_c": 0.0,
        "f2_option_c": 0.0,
    }
    assert per_tier["must_not"]["fire_rate_wilson95"] is None
    assert per_tier["watch"]["covered_rate"] == 0.0


def _tiered_cells() -> dict[str, Any]:
    """Two itin cells whose tier counters are hand-summable.

    must 40/50 covered, should 6/10, watch 1/2, must_not 3 fired of 20.
    """
    return {
        "cells": {
            "itin_financial_white": _cell(
                30,
                10,
                4,
                adv_total=12,
                adv_fired=2,
                tiers={
                    "tier_must_total": 30,
                    "tier_must_covered": 25,
                    "tier_should_total": 4,
                    "tier_should_covered": 3,
                    "tier_watch_total": 1,
                    "tier_watch_covered": 1,
                    "tier_must_not_total": 12,
                    "tier_must_not_fired": 2,
                },
            ),
            "itin_generic_black": _cell(
                16,
                4,
                1,
                adv_total=8,
                adv_fired=1,
                tiers={
                    "tier_must_total": 20,
                    "tier_must_covered": 15,
                    "tier_should_total": 6,
                    "tier_should_covered": 3,
                    "tier_watch_total": 1,
                    "tier_watch_covered": 0,
                    "tier_must_not_total": 8,
                    "tier_must_not_fired": 1,
                },
            ),
        }
    }


def test_per_tier_hand_computed() -> None:
    fam = build_baseline(_tiered_cells())["per_family"]["itin"]
    tiers: Mapping[str, Any] = fam["per_tier"]
    must, should = tiers["must"], tiers["should"]
    assert (must["total"], must["covered"]) == (50, 40)
    assert must["recall"] == pytest.approx(0.8)
    # Option-C precision: covered / (covered + must_not fired) = 40 / 43.
    assert must["precision_option_c"] == pytest.approx(40 / 43)
    p, r = 40 / 43, 0.8
    assert must["f2_option_c"] == pytest.approx(5 * p * r / (4 * p + r))
    assert must["f1_option_c"] == pytest.approx(2 * p * r / (p + r))
    assert (should["total"], should["covered"]) == (10, 6)
    assert should["recall"] == pytest.approx(0.6)
    assert should["precision_option_c"] == pytest.approx(6 / 9)
    assert tiers["watch"] == {"total": 2, "covered": 1, "covered_rate": 0.5}
    must_not = tiers["must_not"]
    assert (must_not["total"], must_not["fired"]) == (20, 3)
    assert must_not["fire_rate"] == pytest.approx(0.15)
    lo, hi = must_not["fire_rate_wilson95"]
    assert lo < 0.15 < hi
    # The legacy block is untouched by the tier counters: TP 46 / FN 14 / FP 5.
    assert (fam["true_positives"], fam["false_negatives"], fam["false_positives"]) == (46, 14, 5)
    # The tier block rolls up the doctype / demographic / totals axes too.
    payload = build_baseline(_tiered_cells())
    assert payload["totals"]["per_tier"]["must"]["total"] == 50
    assert payload["per_doctype"]["generic"]["per_tier"]["should"]["covered"] == 3
    assert payload["per_demographic"]["white"]["per_tier"]["must_not"]["fired"] == 2


def test_tiered_payload_schema_validates(tmp_build_dir: Path) -> None:
    payload = build_baseline(_tiered_cells())
    dest = tmp_build_dir / "g8_detection_baseline_tiered.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_detection_baseline")
