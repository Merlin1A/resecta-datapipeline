"""Verdict-logic, presence, determinism and schema tests for ``eval.compare_documents``.

A minimal synthetic ``documents_eval.json`` (two documents, two leg kinds,
the ``documents._metric_view`` headline shape) is compared against itself
and against degraded copies: identity is a clean no-regression; a strict
recall drop trips C3 on the right row only; a must-not-fire decoy that starts
firing trips C2 through the Option-C precision; a per-category strict
precision collapse trips the row's C4; a leg-kind pooled precision collapse
trips the aggregate's C4; a row on one side only is skipped, never evaluated.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.compare_documents import build_compare_documents

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"
_TH: dict[str, float] = {"delta_p": 0.05, "delta_f_rel": 0.30, "eps": 0.01, "delta_slice": 0.03}


def _view(support: int, hits: int, mnf_total: int, mnf_fired: int) -> dict[str, Any]:
    """A ``documents._metric_view`` block from Option-C integers."""
    recall = hits / support if support else 0.0
    denom = hits + mnf_fired
    precision = hits / denom if denom else 1.0
    return {
        "support": support,
        "low_support": support < 30,
        "region": {
            "recall": recall,
            "precision": precision,
            "f1": 0.0,
            "f2": 0.0,
            "recall_ci95": None,
        },
        "strict": {
            "recall": recall,
            "precision": precision,
            "f1": 0.0,
            "f2": 0.0,
            "recall_ci95": None,
        },
        "must_not_fire": {
            "total": mnf_total,
            "fired_as_category": mnf_fired,
            "region_covered": mnf_fired,
        },
        "should_fire": {"total": 0, "region_covered": 0},
        "iou": {"headline_rate": 0.0, "tight_rate": 0.0},
    }


def _leg(headline: dict[str, Any], per_category: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "n_runs": 1,
        "median_run_index": 1,
        "median": {
            "headline": headline,
            "per_category": per_category,
            "confusion": {},
            "surplus_fires": 0,
            "surplus_fires_per_page": 0.0,
            "surplus_fires_by_page": {},
            "verdicts": {},
        },
        "strict_recall_min_median_max": [0.0, 0.0, 0.0],
        "region_recall_min_median_max": [0.0, 0.0, 0.0],
    }


def _eval() -> dict[str, Any]:
    """packet: text + ocr-forced; scan-sim: ocr. Support >= 30 on the packet rows."""
    return {
        "schema_version": 1,
        "site": "siteB",
        "generated_by": "resecta_data.eval.documents",
        "match_rule": {},
        "per_document": {
            "packet": {
                "variant": None,
                "text": _leg(
                    _view(55, 53, 31, 1),
                    {"ssn": _view(20, 20, 5, 0), "name": _view(35, 33, 26, 1)},
                ),
                "ocr-forced": _leg(
                    _view(55, 46, 31, 1),
                    {"ssn": _view(20, 18, 5, 0), "name": _view(35, 28, 26, 1)},
                ),
            },
            "packet-scan-sim-150dpi": {
                "variant": "scan-sim",
                "ocr": _leg(
                    _view(55, 46, 31, 1),
                    {"ssn": _view(20, 18, 5, 0), "name": _view(35, 28, 26, 1)},
                ),
            },
        },
        "pools": {},
        "miss_attribution": {},
    }


def _row(verdict: dict[str, Any], name: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = verdict["rows"]
    for row in rows:
        if row["name"] == name:
            return row
    raise AssertionError(f"row {name!r} not in verdict")


def test_identity_is_no_regression_with_ordered_rows() -> None:
    before = _eval()
    verdict = build_compare_documents(before, before, _TH)
    assert verdict["regression"] is False
    assert [r["name"] for r in verdict["rows"]] == [
        "packet/text",
        "packet/ocr-forced",
        "packet-scan-sim-150dpi/ocr",
    ]
    for row in verdict["rows"]:
        assert row["regression"] is False
        assert row["win"] is False  # no uplift on identity
        assert row["non_regression_only"] is False
        assert [c["clause"] for c in row["clauses"]] == [
            "C1_precision",
            "C2_family_fpr",
            "C3_recall",
            "C4_slice_non_regression",
        ]
    assert verdict["aggregate"]["regression"] is False
    assert verdict["aggregate"]["rows_pooled"] == 3
    assert verdict["rows_skipped"] == []
    assert verdict["before_sha256"] == verdict["after_sha256"]


def test_row_cells_read_the_option_c_strict_headline() -> None:
    before = _eval()
    verdict = build_compare_documents(before, before, _TH)
    c1 = _row(verdict, "packet/text")["clauses"][0]
    assert c1["before"] == pytest.approx(53 / 54)  # hits / (hits + mnf fired)
    c3 = _row(verdict, "packet/text")["clauses"][2]
    assert c3["before"] == pytest.approx(53 / 55)
    # C2 reads the must-not-fire construction: 1 - strict precision.
    c2 = _row(verdict, "packet/text")["clauses"][1]
    assert c2["before"] == pytest.approx(1 - 53 / 54)


def test_recall_drop_trips_c3_on_the_right_row_only() -> None:
    before = _eval()
    after = copy.deepcopy(before)
    after["per_document"]["packet"]["text"]["median"]["headline"] = _view(55, 40, 31, 1)
    verdict = build_compare_documents(before, after, _TH)
    assert verdict["regression"] is True
    row = _row(verdict, "packet/text")
    assert row["regression"] is True
    assert "C3_recall" in row["regressed_clauses"]
    assert _row(verdict, "packet/ocr-forced")["regression"] is False
    assert _row(verdict, "packet-scan-sim-150dpi/ocr")["regression"] is False


def test_decoy_starting_to_fire_trips_c2_via_option_c_precision() -> None:
    before = _eval()
    after = copy.deepcopy(before)
    # Same hits, 6 more must-not-fire spans fired as the category.
    after["per_document"]["packet"]["text"]["median"]["headline"] = _view(55, 53, 31, 7)
    verdict = build_compare_documents(before, after, _TH)
    row = _row(verdict, "packet/text")
    assert row["regression"] is True
    assert "C2_family_fpr" in row["regressed_clauses"]
    assert "C1_precision" in row["regressed_clauses"]


def test_category_slice_collapse_trips_the_row_c4() -> None:
    before = _eval()
    after = copy.deepcopy(before)
    # Headline unchanged; the name category's strict precision collapses.
    after["per_document"]["packet"]["text"]["median"]["per_category"]["name"] = _view(
        35, 33, 26, 10
    )
    verdict = build_compare_documents(before, after, _TH)
    row = _row(verdict, "packet/text")
    assert row["regression"] is True
    assert row["regressed_clauses"] == ["C4_slice_non_regression"]
    c4 = row["clauses"][3]
    assert c4["regressed_slices"] == ["category:name"]


def test_leg_pool_collapse_trips_the_aggregate_c4() -> None:
    before = _eval()
    after = copy.deepcopy(before)
    # Both OCR rows lose precision: the ocr leg pool drops beyond delta_slice.
    after["per_document"]["packet-scan-sim-150dpi"]["ocr"]["median"]["headline"] = _view(
        55, 46, 31, 12
    )
    verdict = build_compare_documents(before, after, _TH)
    c4 = verdict["aggregate"]["clauses"][3]
    assert "leg:ocr" in c4["regressed_slices"]
    assert verdict["aggregate"]["regression"] is True
    assert verdict["regression"] is True


def test_one_sided_row_is_skipped_not_evaluated() -> None:
    before = _eval()
    after = copy.deepcopy(before)
    del after["per_document"]["packet-scan-sim-150dpi"]
    verdict = build_compare_documents(before, after, _TH)
    assert verdict["rows_skipped"] == ["packet-scan-sim-150dpi/ocr"]
    assert [r["name"] for r in verdict["rows"]] == ["packet/text", "packet/ocr-forced"]
    assert verdict["aggregate"]["rows_pooled"] == 2
    assert verdict["regression"] is False


def test_cli_is_byte_deterministic_and_schema_valid(tmp_path: Path) -> None:
    before_path, after_path = tmp_path / "before.json", tmp_path / "after.json"
    dump_canonical_json(_eval(), before_path)
    after = copy.deepcopy(_eval())
    after["per_document"]["packet"]["text"]["median"]["headline"] = _view(55, 40, 31, 1)
    dump_canonical_json(after, after_path)
    outs = [tmp_path / "a.json", tmp_path / "b.json"]
    for out in outs:
        result = CliRunner().invoke(
            cli_main,
            [
                "build",
                "eval-compare-documents",
                "--before",
                str(before_path),
                "--after",
                str(after_path),
                "--out",
                str(out),
            ],
            env={"PYTHONHASHSEED": "0"},
        )
        assert result.exit_code == 0, result.output
        assert "verdict=REGRESSION" in result.output
    assert outs[0].read_bytes() == outs[1].read_bytes()
    validate_file(outs[0], _SCHEMAS, "g8_compare_documents")
