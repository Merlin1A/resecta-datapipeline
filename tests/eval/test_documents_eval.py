"""Hand-computed-metric checks for the H1.3 document eval.

Builds tiny synthetic occurrence lists and harness-run payloads inline (no
real Swift output needed) and pins the Option-C join verdicts, the tier ->
denominator mapping, the DetEval merge credit, the strict/relaxed value-text
rule, the Wilson interval, the leg routing, and the clean-twin miss
attribution against values computed by hand.
"""

from __future__ import annotations

from typing import Any

from resecta_data.eval.documents import (
    _end_slack_match,
    _gt_leg_for,
    evaluate_run,
    join_occurrence,
    wilson_ci,
)


def _occ(
    occ_id: str,
    category: str,
    bbox: list[float],
    *,
    page: int = 0,
    expectation: str = "must_fire",
    legs: list[str] | None = None,
    value: str = "555-12-3456",
    spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": occ_id,
        "category": category,
        "page": page,
        "bbox": bbox,
        "expectation": expectation,
        "leg_applicability": legs or ["text", "ocr"],
        "value": value,
        "spans": spans if spans is not None else [{"page": page, "bbox": bbox}],
    }


def _hit(
    rect: list[float], category: str = "ssn", *, page: int = 0, text: str = "555-12-3456"
) -> dict[str, Any]:
    return {
        "page": page,
        "category": category,
        "rect": rect,
        "text": text,
        "confidence": 0.9,
        "source": "text",
        "ocr_confidence": None,
    }


def _run(
    hits: list[dict[str, Any]],
    *,
    leg: str = "natural",
    statuses: list[str] | None = None,
    run_index: int = 1,
) -> dict[str, Any]:
    return {
        "leg": leg,
        "run_index": run_index,
        "page_count": 1,
        "text_layer_status": statuses or ["rich"],
        "hits": hits,
        "diagnostics": {},
    }


class TestJoin:
    def test_exact_overlap_is_covered_and_strict_shaped(self) -> None:
        occ = _occ("o1", "ssn", [0.1, 0.1, 0.3, 0.15])
        v = join_occurrence(occ, [_hit([0.1, 0.1, 0.2, 0.05])])
        assert v["covered"] and v["iou_matched"] and v["iou_tight"]
        assert v["cover_category"] == "ssn"

    def test_no_overlap_is_uncovered(self) -> None:
        occ = _occ("o1", "ssn", [0.1, 0.1, 0.3, 0.15])
        v = join_occurrence(occ, [_hit([0.6, 0.6, 0.2, 0.05])])
        assert not v["covered"] and not v["iou_matched"]

    def test_deteval_merge_credit_on_multispan(self) -> None:
        # Two spans, each individually covered by its own detection; the
        # whole-bbox IoU is diluted by the inter-span gap.
        occ = _occ(
            "o1",
            "address",
            [0.1, 0.1, 0.3, 0.4],
            spans=[
                {"page": 0, "bbox": [0.1, 0.1, 0.3, 0.15]},
                {"page": 0, "bbox": [0.1, 0.35, 0.3, 0.4]},
            ],
        )
        dets = [_hit([0.1, 0.1, 0.2, 0.05], "address"), _hit([0.1, 0.35, 0.2, 0.05], "address")]
        v = join_occurrence(occ, dets)
        assert v["covered"] and v["iou_matched"]
        assert v["cover_category"] == "address"

    def test_dob_alias_canonicalizes(self) -> None:
        occ = _occ("o1", "dateOfBirth", [0.1, 0.1, 0.3, 0.15])
        v = join_occurrence(occ, [_hit([0.1, 0.1, 0.2, 0.05], "dob")])
        assert v["cover_category"] == "dateOfBirth"


class TestEndSlack:
    def test_exact_and_whitespace_fold(self) -> None:
        assert _end_slack_match("555-12-3456", "555-12-3456")
        assert _end_slack_match("Delia  R. Hartwell", "delia r. hartwell")

    def test_two_chars_each_end_pass_three_fail(self) -> None:
        assert _end_slack_match("ab555-12-3456cd", "555-12-3456")
        assert not _end_slack_match("abc555-12-3456", "555-12-3456")


class TestWilson:
    def test_hand_computed_interval(self) -> None:
        ci = wilson_ci(8, 10)
        assert ci is not None
        lo, hi = ci
        # Wilson 95% for 8/10: ~[0.4902, 0.9433]
        assert abs(lo - 0.4902) < 0.001 and abs(hi - 0.9433) < 0.001

    def test_zero_n_is_none(self) -> None:
        assert wilson_ci(0, 0) is None


class TestLegRouting:
    def test_natural_rich_is_text_and_sparse_is_ocr(self) -> None:
        occ = _occ("o1", "ssn", [0.1, 0.1, 0.3, 0.15])
        assert _gt_leg_for(occ, _run([], statuses=["rich"])) == "text"
        assert _gt_leg_for(occ, _run([], statuses=["none"])) == "ocr"

    def test_forced_is_ocr_and_leg_applicability_filters(self) -> None:
        occ_text_only = _occ("o1", "ssn", [0.1, 0.1, 0.3, 0.15], legs=["text"])
        assert _gt_leg_for(occ_text_only, _run([], leg="ocr-forced")) is None
        occ_both = _occ("o2", "ssn", [0.1, 0.1, 0.3, 0.15])
        assert _gt_leg_for(occ_both, _run([], leg="ocr-forced")) == "ocr"


class TestEvaluateRun:
    def test_tier_mapping_and_hand_computed_metrics(self) -> None:
        occs = [
            _occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15]),  # hit, strict
            _occ("mf2", "ssn", [0.5, 0.5, 0.7, 0.55]),  # missed
            _occ("mnf1", "ssn", [0.1, 0.7, 0.3, 0.75], expectation="must_not_fire"),  # fired
            _occ("sf1", "ssn", [0.5, 0.7, 0.7, 0.75], expectation="should_fire"),  # missed
            _occ("w1", "ssn", [0.5, 0.9, 0.7, 0.95], expectation="watch"),
        ]
        run = _run(
            [
                _hit([0.1, 0.1, 0.2, 0.05]),  # covers mf1
                _hit([0.1, 0.7, 0.2, 0.05]),  # covers mnf1 (a strict FP)
            ]
        )
        block = evaluate_run(occs, run)
        h = block["headline"]
        # recall = 1/2; precision = 1/(1+1) = 0.5; F1 = 0.5; F2 = 0.5.
        assert h["strict"]["recall"] == 0.5
        assert h["strict"]["precision"] == 0.5
        assert h["strict"]["f2"] == 0.5
        assert h["must_not_fire"]["fired_as_category"] == 1
        assert h["should_fire"] == {"total": 1, "region_covered": 0}
        assert block["surplus_fires"] == 0
        assert block["verdicts"]["mf1"]["strict"] is True
        assert block["verdicts"]["mf2"]["strict"] is False

    def test_category_ratchet_demotes_wrong_label(self) -> None:
        occs = [_occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15])]
        run = _run([_hit([0.1, 0.1, 0.2, 0.05], category="phone")])
        block = evaluate_run(occs, run)
        h = block["headline"]
        assert h["region"]["recall"] == 1.0
        assert h["strict"]["recall"] == 0.0
        assert block["confusion"] == {"ssn->phone": 1}

    def test_value_text_strict_vs_relaxed(self) -> None:
        occs = [_occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15], value="555-12-3456")]
        run = _run([_hit([0.1, 0.1, 0.2, 0.05], text="x555-12-3456")])
        block = evaluate_run(occs, run)
        vt = block["headline"]["value_text"]
        assert vt == {"denom": 1, "strict_rate": 0.0, "relaxed_rate": 1.0}

    def test_surplus_fire_counted(self) -> None:
        occs = [_occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15])]
        run = _run([_hit([0.1, 0.1, 0.2, 0.05]), _hit([0.6, 0.6, 0.2, 0.05])])
        block = evaluate_run(occs, run)
        assert block["surplus_fires"] == 1
        assert block["surplus_fires_by_page"] == {"0": 1}

    def test_carried_boxes_suppress_surplus(self) -> None:
        occs = [_occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15])]
        carried = [_occ("c1", "phone", [0.6, 0.6, 0.8, 0.65], expectation="must_fire")]
        run = _run([_hit([0.1, 0.1, 0.2, 0.05]), _hit([0.6, 0.6, 0.2, 0.05])])
        block = evaluate_run(occs, run, carried)
        assert block["surplus_fires"] == 0
