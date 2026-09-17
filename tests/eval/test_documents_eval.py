"""Hand-computed-metric checks for the H1.3 document eval.

Builds tiny synthetic occurrence lists and harness-run payloads inline (no
real Swift output needed) and pins the Option-C join verdicts, the tier ->
denominator mapping, the DetEval merge credit, the strict/relaxed value-text
rule, the Wilson interval, the leg routing, and the clean-twin miss
attribution against values computed by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.documents import (
    _attribute_misses,
    _end_slack_match,
    _gt_leg_for,
    _normalizer_destroyed,
    evaluate_run,
    join_occurrence,
    wilson_ci,
)

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"


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


# ---------------------------------------------------------------------------
# Join rules (union of same-category detections vs the best single detection)
# ---------------------------------------------------------------------------


class TestJoinRules:
    def _split_hits(self) -> list[dict[str, Any]]:
        # A name box [0.1, 0.1, 0.5, 0.15] surfaced as two adjacent name hits,
        # each covering about 0.45 of the box: neither alone reaches 0.5.
        return [
            _hit([0.1, 0.1, 0.18, 0.05], "name", text="Delia"),
            _hit([0.3, 0.1, 0.18, 0.05], "name", text="Hartwell"),
        ]

    def test_union_credits_token_split_names_single_does_not(self) -> None:
        occ = _occ("o1", "name", [0.1, 0.1, 0.5, 0.15], value="Delia Hartwell")
        single = join_occurrence(occ, self._split_hits(), "single")
        union = join_occurrence(occ, self._split_hits(), "union")
        assert not single["covered"] and single["cover_detections"] == 1
        assert single["cover_fraction"] == 0.45
        assert union["covered"] and union["cover_detections"] == 2
        assert union["cover_fraction"] == 0.9
        assert union["cover_category"] == "name"
        assert union["cover_text"] == "Delia"  # the best single hit's text
        # IoU stays single-best under both rules.
        assert union["iou_matched"] == single["iou_matched"]

    def test_union_keeps_the_single_winner_on_a_tie(self) -> None:
        # A full-box ssn hit (first) and a full-box address hit: both cover
        # 1.0; union must not re-label the tie away from the single winner.
        occ = _occ("o1", "ssn", [0.1, 0.1, 0.3, 0.15])
        hits = [_hit([0.1, 0.1, 0.2, 0.05], "ssn"), _hit([0.0, 0.0, 1.0, 1.0], "address")]
        for rule in ("single", "union"):
            v = join_occurrence(occ, hits, rule)
            assert v["cover_category"] == "ssn" and v["cover_detections"] == 1
            assert v["cover_fraction"] == 1.0

    def test_union_never_mixes_categories(self) -> None:
        occ = _occ("o1", "name", [0.1, 0.1, 0.5, 0.15])
        hits = [
            _hit([0.1, 0.1, 0.18, 0.05], "name"),
            _hit([0.3, 0.1, 0.18, 0.05], "address"),
        ]
        v = join_occurrence(occ, hits, "union")
        assert not v["covered"] and v["cover_detections"] == 1

    def test_union_area_is_exact_on_overlapping_hits(self) -> None:
        occ = _occ("o1", "ssn", [0.0, 0.0, 1.0, 1.0])
        hits = [_hit([0.0, 0.0, 0.6, 1.0]), _hit([0.4, 0.0, 0.6, 1.0])]
        v = join_occurrence(occ, hits, "union")
        assert v["cover_fraction"] == 1.0 and v["cover_detections"] == 2

    def test_default_rule_is_union_and_unknown_rule_is_an_error(self) -> None:
        occ = _occ("o1", "name", [0.1, 0.1, 0.5, 0.15])
        assert join_occurrence(occ, self._split_hits())["covered"]
        with pytest.raises(PipelineError, match="unknown join rule"):
            join_occurrence(occ, [], "best")

    def test_verdict_rows_surface_the_cover_fields(self) -> None:
        occs = [_occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15], value="555-12-3456")]
        run = _run([_hit([0.1, 0.1, 0.2, 0.05], text="555-12-3456")])
        v = evaluate_run(occs, run, rule="single")["verdicts"]["mf1"]
        assert v["cover_category"] == "ssn" and v["cover_text"] == "555-12-3456"
        assert v["cover_fraction"] == 1.0 and v["cover_detections"] == 1


# ---------------------------------------------------------------------------
# Miss attribution: own text leg first, then any text leg carrying the id;
# normalizer_destroyed read from the OCR line dump, unavailable without one.
# ---------------------------------------------------------------------------


def _leg(verdicts: dict[str, dict[str, Any]], run_index: int = 2) -> dict[str, Any]:
    return {"median_run_index": run_index, "median": {"verdicts": verdicts}}


def _miss(page: int = 0) -> dict[str, Any]:
    return {"leg": "ocr", "expectation": "must_fire", "covered": False, "strict": False}


def _hit_verdict() -> dict[str, Any]:
    return {"leg": "text", "expectation": "must_fire", "covered": True, "strict": True}


class TestAttribution:
    def test_twins_and_classes(self, tmp_path: Path) -> None:
        per_document = {
            # The master ran a text leg: its own ocr-forced misses twin to it.
            "master": {
                "variant": None,
                "text": _leg({"a": _hit_verdict(), "b": {**_hit_verdict(), "strict": False}}),
                "ocr-forced": _leg({"a": _miss(), "b": _miss(), "z": _miss()}),
            },
            # A derived scan shares the master's ids and has no text leg.
            "scan": {"variant": "scan-sim", "ocr": _leg({"a": _miss()})},
        }
        values: dict[str, dict[str, tuple[str, int | None]]] = {
            "master": {"a": ("555-12-3456", 0), "b": ("Delia", 0), "z": ("12345", 0)},
            "scan": {"a": ("555-12-3456", 0)},
        }
        # The master's median ocr-forced run has a dump where the value survived
        # recognition intact but the normalizer letterized it; the scan has none.
        dump_canonical_json(
            {
                "schema_version": 1,
                "doc_id": "master",
                "run_index": 2,
                "pages": [
                    {
                        "page": 0,
                        "lines": [{"text": "SSN 555-12-3456", "normalized": "SSN SSS-12-3456"}],
                    }
                ],
            },
            tmp_path / "master" / "ocr-lines-run-2.json",
        )
        out = _attribute_misses(per_document, values, tmp_path)
        assert out["text_legs_present"] == ["master"]
        master = out["documents"]["master"]["ocr-forced"]
        assert master["clean_twins"] == {"master/text/median": 2}
        assert master["normalizer_destroyed"] == ["a"]
        assert master["ocr_induced"] == []
        assert master["detector_induced"] == ["b"]
        assert master["unattributed"] == ["z"]
        assert master["strict_miss_total"] == 3
        assert master["normalizer_check"] == "ocr-lines dump"
        scan = out["documents"]["scan"]["ocr"]
        assert scan["clean_twins"] == {"master/text/median": 1}
        assert scan["ocr_induced"] == ["a"]
        assert scan["normalizer_destroyed"] is None
        assert scan["normalizer_destroyed_count"] is None
        assert scan["normalizer_check"] == "unavailable"

    def test_normalizer_rule_needs_digits_intact_in_raw_and_gone_in_normalized(self) -> None:
        lines = {0: [("SSN 555-12-3456", "SSN SSS-12-3456"), ("DOB 08/11/2026", "DOB 08/11/2026")]}
        assert _normalizer_destroyed("555-12-3456", 0, lines)
        assert not _normalizer_destroyed("08/11/2026", 0, lines)  # survived both
        assert not _normalizer_destroyed("999-99-9999", 0, lines)  # never recognized
        assert not _normalizer_destroyed("Delia Hartwell", 0, lines)  # no digits
        assert not _normalizer_destroyed("555-12-3456", 3, lines)  # other page


class TestCli:
    def _write_run_dir(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        gt_root = tmp_path / "gt"
        gt_root.mkdir()
        manifest = [
            {
                "id": "doc",
                "path": "doc.pdf",
                "sha256": "0" * 64,
                "gt": "doc-gt.json",
                "leg_applicability": ["text", "ocr"],
                "source": "bundled",
            },
        ]
        dump_canonical_json(manifest, gt_root / "documents.manifest.json")
        dump_canonical_json(
            {"occurrences": [_occ("mf1", "ssn", [0.1, 0.1, 0.3, 0.15])]},
            gt_root / "doc-gt.json",
        )
        hits = tmp_path / "hits"
        run = {
            **_run([_hit([0.1, 0.1, 0.2, 0.05])]),
            "site": "siteB",
            "leg": "natural",
        }
        dump_canonical_json(run, hits / "doc" / "natural-run-1.json")
        dump_canonical_json({**run, "run_index": 2}, hits / "doc" / "natural-run-2.json")
        return gt_root, hits, tmp_path / "out"

    def test_eval_documents_validates_and_names_the_rule(self, tmp_path: Path) -> None:
        gt_root, hits, out = self._write_run_dir(tmp_path)
        runner = CliRunner()
        for rule in ("single", "union"):
            result = runner.invoke(
                cli_main,
                [
                    "build",
                    "eval-documents",
                    "--manifest",
                    str(gt_root / "documents.manifest.json"),
                    "--gt-root",
                    str(gt_root),
                    "--hits-dir",
                    str(hits),
                    "--out-dir",
                    str(out / rule),
                    "--join-rule",
                    rule,
                    "--schemas-dir",
                    str(_SCHEMAS),
                ],
            )
            assert result.exit_code == 0, result.output
            payload = load_json(out / rule / "documents_eval.json")
            assert payload["match_rule"]["join_rule"] == rule
            validate_file(out / rule / "documents_eval.json", _SCHEMAS, "documents_eval")
