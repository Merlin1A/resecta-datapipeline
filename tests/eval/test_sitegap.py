"""Arithmetic, presence, determinism and schema tests for ``eval.sitegap``.

Two derived baselines are built from synthetic cells with the real
``build_baseline`` (the detector side and the Site-B side), joined by
``build_site_gap``, and the per-family deltas are checked against hand
arithmetic -- including the P1.1 M12-02 shapes: an account family the
detector site cannot surface at all (raw gate) that Site B recovers, a phone
family Site B lifts on both axes, an npi family Site B over-fires on, and a
family that exists on one side only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.baseline import build_baseline
from resecta_data.eval.sitegap import build_site_gap

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"


def _cell(tp: int, fn: int, fp: int, **tiers: int) -> dict[str, int]:
    cell = {
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "adversarial_suppress_total": 0,
        "adversarial_suppress_fired": 0,
        "suppressed_by_negative_context": 0,
    }
    cell.update(tiers)
    return cell


def _detector() -> dict[str, Any]:
    return build_baseline(
        {
            "cells": {
                # account: every raw fire below the balanced cutoff -> 0 TP.
                "account_financial_white": _cell(
                    0, 300, 0, tier_must_total=300, tier_must_covered=0
                ),
                "phone_court_white": _cell(
                    569, 531, 0, tier_must_total=1100, tier_must_covered=569
                ),
                "npi_medical_white": _cell(250, 0, 0, tier_must_total=250, tier_must_covered=250),
                "ssn_court_white": _cell(1000, 0, 0, tier_must_total=1000, tier_must_covered=1000),
                # A doctype-gated family the detector site alone reports on.
                "licenseplate_court_white": _cell(
                    300, 0, 0, tier_must_total=300, tier_must_covered=300
                ),
            }
        }
    )


def _siteb() -> dict[str, Any]:
    return build_baseline(
        {
            "cells": {
                "account_financial_white": _cell(
                    200, 100, 0, tier_must_total=300, tier_must_covered=200
                ),
                "phone_court_white": _cell(
                    999, 101, 370, tier_must_total=1100, tier_must_covered=999
                ),
                "npi_medical_white": _cell(
                    250,
                    0,
                    5,
                    tier_must_total=250,
                    tier_must_covered=250,
                    tier_must_not_total=10,
                    tier_must_not_fired=1,
                ),
                "ssn_court_white": _cell(1000, 0, 0, tier_must_total=1000, tier_must_covered=1000),
            }
        }
    )


def _gap() -> dict[str, Any]:
    return build_site_gap(_detector(), _siteb())


def test_families_union_and_presence() -> None:
    gap = _gap()
    assert gap["families"] == ["account", "licenseplate", "npi", "phone", "ssn"]
    assert gap["per_family"]["licenseplate"]["present_in"] == ["detector"]
    assert gap["per_family"]["licenseplate"]["siteb"] is None
    assert gap["per_family"]["licenseplate"]["delta_siteb_minus_detector"] is None
    assert gap["per_family"]["account"]["present_in"] == ["detector", "siteb"]


def test_account_delta_is_the_composed_posterior_recovery() -> None:
    delta = _gap()["per_family"]["account"]["delta_siteb_minus_detector"]
    assert delta["true_positives"] == 200
    assert delta["false_positives"] == 0
    assert delta["recall"] == pytest.approx(200 / 300)
    # Detector precision is 0.0 by the zero-denominator rule; Site B is 1.0.
    assert delta["precision"] == pytest.approx(1.0)
    assert delta["per_tier"]["must_recall"] == pytest.approx(200 / 300)


def test_phone_and_npi_deltas_hand_computed() -> None:
    per_family = _gap()["per_family"]
    phone = per_family["phone"]["delta_siteb_minus_detector"]
    assert phone["true_positives"] == 430
    assert phone["recall"] == pytest.approx(999 / 1100 - 569 / 1100)
    assert phone["precision"] == pytest.approx(999 / 1369 - 1.0)
    npi = per_family["npi"]["delta_siteb_minus_detector"]
    assert npi["false_positives"] == 5
    assert npi["recall"] == 0.0
    assert npi["precision"] == pytest.approx(250 / 255 - 1.0)
    # Site B carries a must_not decoy the detector side had none of.
    assert npi["per_tier"]["must_not_fire_rate"] == pytest.approx(0.1)
    # Option-C precision moves with the decoy fire: 250/251 - 1.0.
    assert npi["per_tier"]["must_precision_option_c"] == pytest.approx(250 / 251 - 1.0)


def test_identical_family_has_no_gap_and_gap_list_is_exact() -> None:
    gap = _gap()
    ssn = gap["per_family"]["ssn"]["delta_siteb_minus_detector"]
    assert all(ssn[k] == 0 for k in ("precision", "recall", "f1", "f2", "true_positives"))
    assert all(v == 0 for v in ssn["per_tier"].values())
    assert gap["families_with_gap"] == ["account", "npi", "phone"]


def test_totals_row_present_on_both_sides() -> None:
    totals = _gap()["totals"]
    assert totals["present_in"] == ["detector", "siteb"]
    delta = totals["delta_siteb_minus_detector"]
    # TP: detector 0+569+250+1000+300 = 2119; Site B 200+999+250+1000 = 2449.
    assert delta["true_positives"] == 2449 - 2119


def test_byte_deterministic_and_schema_valid(tmp_build_dir: Path) -> None:
    a, b = _gap(), _gap()
    dest_a, dest_b = tmp_build_dir / "a.json", tmp_build_dir / "b.json"
    dump_canonical_json(a, dest_a)
    dump_canonical_json(b, dest_b)
    assert dest_a.read_bytes() == dest_b.read_bytes()
    validate_file(dest_a, _SCHEMAS, "g8_site_gap")
    assert len(a["detector_sha256"]) == 64
    assert a["detector_sha256"] != a["siteb_sha256"]


def test_cli_eval_sitegap(tmp_path: Path) -> None:
    detector_path = tmp_path / "detector.json"
    siteb_path = tmp_path / "siteb.json"
    dump_canonical_json(_detector(), detector_path)
    dump_canonical_json(_siteb(), siteb_path)
    out = tmp_path / "g8_site_gap.json"
    result = CliRunner().invoke(
        cli_main,
        [
            "build",
            "eval-sitegap",
            "--detector",
            str(detector_path),
            "--siteb",
            str(siteb_path),
            "--out",
            str(out),
        ],
        env={"PYTHONHASHSEED": "0"},
    )
    assert result.exit_code == 0, result.output
    assert "families with a gap: 3 of 5" in result.output
    validate_file(out, _SCHEMAS, "g8_site_gap")
