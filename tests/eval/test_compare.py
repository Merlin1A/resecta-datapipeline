"""Verdict-logic, determinism, and schema tests for ``eval.compare``.

The comparator is pure arithmetic over two derived ``g8_detection_baseline.json``
dicts. These tests craft minimal synthetic baselines (most via the real
``build_baseline`` from synthetic ``_cells.json`` payloads, a couple hand-built
to isolate one field) and assert:

- determinism: the ``build eval-compare`` CLI run twice on the same inputs is
  byte-identical;
- schema: an emitted verdict validates against
  ``schemas/g8_compare_verdict.schema.json``;
- verdict logic: before==after is a clean no-regression; a degraded AFTER is a
  REGRESSION on the correct family AND clause; MRN/EIN are non-regression-only
  (a precision drop with still-0 FP is not flagged, an FP growth is); ITIN
  absence is tolerated; the C2_family_fpr clause reads ``precision_with_decoys``
  (not ``1-precision``); ``low_confidence`` is reported, not gated.

See ``src/resecta_data/eval/compare.py`` for the implementation and
``schemas/g8_compare_verdict.schema.json`` for the shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.baseline import build_baseline
from resecta_data.eval.compare import build_compare

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"

# Default predicate thresholds in the comparator's FRACTION units (the CLI takes
# delta_p / delta_slice in points and converts; build_compare takes fractions).
_TH: dict[str, float] = {"delta_p": 0.05, "delta_f_rel": 0.30, "eps": 0.01, "delta_slice": 0.03}


def _cell(
    tp: int,
    fn: int,
    fp: int,
    *,
    adv_total: int = 0,
    adv_fired: int = 0,
    neg_ctx: int = 0,
) -> dict[str, int]:
    """One raw join cell in the contract's File-1 shape."""
    return {
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "adversarial_suppress_total": adv_total,
        "adversarial_suppress_fired": adv_fired,
        "suppressed_by_negative_context": neg_ctx,
    }


def _baseline(cells: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Derive a full, schema-valid baseline from a synthetic cells map."""
    return build_baseline({"cells": cells})


def _before_cells() -> dict[str, dict[str, int]]:
    """A BEFORE corpus spanning the five scorer families (mrn key = medicalrecord).

    account P 0.500 / R 0.667 (200 TP, 200 FP); phone P 0.600 / R 0.600;
    medicalrecord (MRN) and ein are clean (0 FP). Support is kept >= 30 so the
    low_confidence advisory does not fire on these families.
    """
    return {
        "account_financial_white": _cell(200, 100, 200),
        "phone_court_white": _cell(60, 40, 40),
        "medicalrecord_medical_white": _cell(50, 0, 0),
        "ein_financial_white": _cell(40, 0, 0),
    }


def _family(verdict: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the family verdict record for ``name``."""
    families: list[dict[str, Any]] = verdict["families"]
    for fam in families:
        if fam["name"] == name:
            return fam
    raise AssertionError(f"family {name!r} not in verdict")


# --------------------------------------------------------------------------- #
# before == after  ->  clean no-regression
# --------------------------------------------------------------------------- #


def test_identity_is_no_regression() -> None:
    before = _baseline(_before_cells())
    verdict = build_compare(before, before, _TH)

    # The whole point: an identical AFTER never reads as a regression.
    assert verdict["regression"] is False
    assert verdict["aggregate"]["regression"] is False
    for fam in verdict["families"]:
        assert fam["regression"] is False


def test_identity_clean_families_win_uplift_families_do_not() -> None:
    before = _baseline(_before_cells())
    verdict = build_compare(before, before, _TH)

    # Uplift-required families do NOT "win" on identity (no precision uplift),
    # but they also do not regress -- the two senses are distinct.
    assert _family(verdict, "account")["win"] is False
    assert _family(verdict, "phone")["win"] is False
    assert verdict["aggregate"]["win"] is False

    # MRN/EIN are non-regression-only -> they "win" by simply not regressing.
    assert _family(verdict, "mrn")["non_regression_only"] is True
    assert _family(verdict, "ein")["non_regression_only"] is True
    assert _family(verdict, "mrn")["win"] is True
    assert _family(verdict, "ein")["win"] is True


def test_itin_absent_is_tolerated() -> None:
    before = _baseline(_before_cells())
    verdict = build_compare(before, before, _TH)
    itin = _family(verdict, "itin")
    assert itin["absent"] is True
    assert itin["regression"] is False
    # No KeyError, no fabricated clauses.
    assert "clauses" not in itin


# --------------------------------------------------------------------------- #
# a genuine WIN AFTER
# --------------------------------------------------------------------------- #


def test_win_after_clears_predicate_without_regression() -> None:
    before = _baseline(_before_cells())
    after_cells = _before_cells()
    # account: FP 200 -> 50 (P 0.5 -> 0.8, recall held).
    after_cells["account_financial_white"] = _cell(200, 100, 50)
    # phone: FP 40 -> 20 (P 0.6 -> 0.75 = +0.15; FPR 0.4 -> 0.25 = >30% cut; R held).
    after_cells["phone_court_white"] = _cell(60, 40, 20)
    after = _baseline(after_cells)

    verdict = build_compare(before, after, _TH)
    assert verdict["regression"] is False
    assert _family(verdict, "account")["win"] is True
    assert _family(verdict, "phone")["win"] is True
    assert verdict["aggregate"]["win"] is True


# --------------------------------------------------------------------------- #
# degraded AFTER  ->  REGRESSION on the right family + clause
# --------------------------------------------------------------------------- #


def test_recall_drop_regresses_c3_on_the_right_family() -> None:
    before = _baseline(_before_cells())
    after_cells = _before_cells()
    # phone recall 0.6 -> 0.4 (the over-suppression direction): TP 60 -> 40.
    after_cells["phone_court_white"] = _cell(40, 60, 20)
    after = _baseline(after_cells)

    verdict = build_compare(before, after, _TH)
    assert verdict["regression"] is True
    phone = _family(verdict, "phone")
    assert phone["regression"] is True
    assert "C3_recall" in phone["regressed_clauses"]
    # account is untouched -> not a regression (per-family isolation).
    assert _family(verdict, "account")["regression"] is False


def test_aggregate_pass_does_not_excuse_a_per_family_fail() -> None:
    # A tiny per-family recall drop that barely moves the grand totals: the
    # aggregate may not regress, but the family does, and overall MUST regress.
    before_cells = {
        "account_financial_white": _cell(2000, 0, 0),  # huge clean mass dominates totals
        "phone_court_white": _cell(60, 40, 0),  # P 1.0 R 0.6
    }
    before = _baseline(before_cells)
    after_cells = dict(before_cells)
    after_cells["phone_court_white"] = _cell(20, 80, 0)  # R 0.6 -> 0.2, big phone drop
    after = _baseline(after_cells)

    verdict = build_compare(before, after, _TH)
    # Aggregate recall barely moves (2020/2100 vs 2060/2100 before) -> may not
    # trip; but phone clearly regressed, so overall is a REGRESSION.
    assert _family(verdict, "phone")["regression"] is True
    assert verdict["regression"] is True


def test_slice_regression_trips_c4_on_aggregate() -> None:
    # Same family totals, but a doctype slice precision collapses > delta_slice.
    before_cells = {
        "account_financial_white": _cell(100, 0, 0),  # financial slice clean
        "account_court_white": _cell(100, 0, 0),  # court slice clean
    }
    before = _baseline(before_cells)
    after_cells = dict(before_cells)
    after_cells["account_court_white"] = _cell(100, 0, 100)  # court P 1.0 -> 0.5
    after = _baseline(after_cells)

    verdict = build_compare(before, after, _TH)
    agg = verdict["aggregate"]
    assert agg["regression"] is True
    assert "C4_slice_non_regression" in agg["regressed_clauses"]
    c4 = next(c for c in agg["clauses"] if c["clause"] == "C4_slice_non_regression")
    assert "doctype:court" in c4["regressed_slices"]
    assert verdict["regression"] is True


# --------------------------------------------------------------------------- #
# MRN/EIN non-regression-only honored both ways
# --------------------------------------------------------------------------- #


def test_mrn_grows_fp_is_flagged() -> None:
    before = _baseline(_before_cells())
    after_cells = _before_cells()
    after_cells["medicalrecord_medical_white"] = _cell(50, 0, 5)  # 0 FP -> 5 FP
    after = _baseline(after_cells)

    verdict = build_compare(before, after, _TH)
    mrn = _family(verdict, "mrn")
    assert mrn["regression"] is True
    assert "C2_family_fpr" in mrn["regressed_clauses"]
    assert verdict["regression"] is True


def test_mrn_precision_drop_with_zero_fp_is_not_flagged() -> None:
    # A precision drop with FP unchanged at 0 cannot arise from build_baseline
    # (precision = TP/(TP+FP) is always 1.0 when FP=0), so we hand-build two
    # baselines where ONLY the precision field differs, holding the decoy-FPR
    # (precision_with_decoys) and FP at the clean value. This isolates the rule:
    # the C1_precision clause must NOT gate MRN/EIN.
    before = _hand_baseline(mrn_precision=1.0)
    after = _hand_baseline(mrn_precision=0.70)  # precision "dropped", still 0 FP
    verdict = build_compare(before, after, _TH)
    mrn = _family(verdict, "mrn")
    # C1_precision is present and shows the drop, but it does NOT gate -> no regression.
    assert mrn["non_regression_only"] is True
    assert mrn["regression"] is False
    assert "C1_precision" not in mrn["regressed_clauses"]
    c1 = next(c for c in mrn["clauses"] if c["clause"] == "C1_precision")
    assert c1["regressed"] is True  # the drop is REPORTED, just not gated


# --------------------------------------------------------------------------- #
# C2_family_fpr reads precision_with_decoys, not 1 - precision
# --------------------------------------------------------------------------- #


def test_c2_reads_precision_with_decoys_not_precision() -> None:
    # account BEFORE: a decoy-clean state. AFTER: decoys begin to fire so
    # precision_with_decoys drops while raw precision stays 1.0. C2_family_fpr
    # must catch the decoy-FPR regression; a (wrong) 1-precision read would
    # see FPR 0->0 and miss it.
    before = _baseline({"account_financial_white": _cell(100, 0, 0)})
    after = _baseline({"account_financial_white": _cell(100, 0, 0, adv_total=20, adv_fired=20)})
    acc_before = before["per_family"]["account"]
    acc_after = after["per_family"]["account"]
    # Sanity: 1-precision is identical (0.0) on both, but 1-pwd differs.
    assert acc_before["precision"] == acc_after["precision"] == 1.0
    assert acc_before["precision_with_decoys"] == 1.0
    assert acc_after["precision_with_decoys"] < 1.0

    verdict = build_compare(before, after, _TH)
    acc = _family(verdict, "account")
    assert acc["regression"] is True
    assert "C2_family_fpr" in acc["regressed_clauses"]
    c2 = next(c for c in acc["clauses"] if c["clause"] == "C2_family_fpr")
    # The clause's measured before/after FPR are the decoy rates, not 0.0.
    assert c2["before"] == 0.0
    assert c2["after"] > 0.0


# --------------------------------------------------------------------------- #
# low_confidence is reported, not gated
# --------------------------------------------------------------------------- #


def test_low_confidence_is_reported_not_gated() -> None:
    # A low-support family (< 30 support) carries low_confidence True but is not
    # gated by it: an identical AFTER is still a clean no-regression.
    before = _baseline({"account_financial_white": _cell(5, 2, 1)})  # support 7 < 30
    verdict = build_compare(before, before, _TH)
    acc = _family(verdict, "account")
    assert acc["low_confidence"] is True
    assert acc["regression"] is False  # low_confidence does not gate
    assert verdict["regression"] is False


# --------------------------------------------------------------------------- #
# determinism + schema (via the CLI)
# --------------------------------------------------------------------------- #


def _write_baseline(d: Path, name: str, payload: dict[str, Any]) -> Path:
    path = d / name
    dump_canonical_json(payload, path)
    return path


def test_cli_eval_compare_is_byte_deterministic(tmp_path: Path) -> None:
    before = _baseline(_before_cells())
    after_cells = _before_cells()
    after_cells["phone_court_white"] = _cell(40, 60, 20)  # a regression, for content
    after = _baseline(after_cells)

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    before_path = _write_baseline(in_dir, "before.json", before)
    after_path = _write_baseline(in_dir, "after.json", after)

    runner = CliRunner()
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    for out in (out_a, out_b):
        result = runner.invoke(
            cli_main,
            [
                "build",
                "eval-compare",
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
    assert out_a.read_bytes() == out_b.read_bytes()


def test_cli_eval_compare_emits_schema_valid_verdict(tmp_path: Path) -> None:
    before = _baseline(_before_cells())
    after = _baseline(_before_cells())

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    before_path = _write_baseline(in_dir, "before.json", before)
    after_path = _write_baseline(in_dir, "after.json", after)
    out = tmp_path / "verdict.json"

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "build",
            "eval-compare",
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
    assert out.is_file()
    validate_file(out, _SCHEMAS, "g8_compare_verdict")
    # The CLI converts points -> fractions: default --delta-p 5 -> 0.05.
    verdict = build_compare(before, after, _TH)
    assert verdict["thresholds"]["delta_p"] == 0.05
    assert verdict["thresholds"]["delta_slice"] == 0.03


def test_build_compare_emits_both_input_shas() -> None:
    before = _baseline(_before_cells())
    after = _baseline(_before_cells())
    verdict = build_compare(before, after, _TH)
    assert len(verdict["before_sha256"]) == 64
    assert len(verdict["after_sha256"]) == 64
    # Same content both sides -> identical digests; a content change diverges.
    assert verdict["before_sha256"] == verdict["after_sha256"]
    after2_cells = _before_cells()
    after2_cells["phone_court_white"] = _cell(40, 60, 20)
    verdict2 = build_compare(before, _baseline(after2_cells), _TH)
    assert verdict2["before_sha256"] != verdict2["after_sha256"]


# --------------------------------------------------------------------------- #
# hand-built baselines (isolate one field the real builder cannot vary alone)
# --------------------------------------------------------------------------- #


def _aggregate_cell(
    *,
    precision: float = 1.0,
    recall: float = 1.0,
    precision_with_decoys: float = 1.0,
    fp: int = 0,
    fired: int = 0,
    low_confidence: bool = False,
) -> dict[str, Any]:
    """A minimal schema-valid aggregateCell with the chosen metric fields."""
    return {
        "true_positives": 100,
        "false_positives": fp,
        "false_negatives": 0,
        "adversarial_suppress_total": fired,
        "adversarial_suppress_fired": fired,
        "suppressed_by_negative_context": 0,
        "support_n": 100,
        "detections_n": 100 + fp,
        "precision": precision,
        "recall": recall,
        "f1": 0.0,
        "adversarial_suppression_fp_rate": 0.0,
        "family_false_positive_count": fp + fired,
        "precision_with_decoys": precision_with_decoys,
        "low_confidence": low_confidence,
    }


def _hand_baseline(*, mrn_precision: float) -> dict[str, Any]:
    """A hand-built baseline letting MRN's precision be set independently of FP.

    Holds MRN's FP and precision_with_decoys at the clean (0-FP) value so only
    the C1_precision input varies -- isolating the non-regression-only rule.
    """
    clean = _aggregate_cell()
    doctype = {d: _aggregate_cell() for d in ("court", "medical", "financial", "foia", "generic")}
    demographic = {b: _aggregate_cell() for b in ("white", "black", "hispanic", "asian", "ai_an")}
    return {
        "schema_version": 1,
        "generated_by": "resecta_data.eval.baseline",
        "metric": "g8_detection_baseline",
        "source_cells_sha256": "0" * 64,
        "per_family": {
            "account": clean,
            "phone": clean,
            # MRN clean on FP (0) but with the chosen precision value.
            "medicalrecord": _aggregate_cell(precision=mrn_precision),
            "ein": clean,
        },
        "per_doctype": doctype,
        "per_demographic": demographic,
        "per_cell": {"account_financial_white": clean},
        "totals": clean,
    }
