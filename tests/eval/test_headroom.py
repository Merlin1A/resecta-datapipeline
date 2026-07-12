"""Determinism + schema + hand-computed checks for the M9 headroom probe.

Builds a small synthetic ``_raw_scores.json`` payload inline, runs
``build_headroom`` twice, asserts byte-identical emission, validates against
``schemas/g8_headroom.schema.json``, and pins the above/below counts, the
suppressible FP mass, the score gap, and the posterior arithmetic (which
mirrors the engine seam ``sigmoid(logit(raw) + logit(floor))``).

See CONTRACT.md File 2.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval.headroom import _posterior, build_headroom

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"

_FLOOR = 0.35


def _row(category: str, raw: float, gt_class: str) -> dict[str, Any]:
    return {
        "category": category,
        "doctype": "court",
        "bucket": "white",
        "raw": raw,
        "gt_class": gt_class,
    }


def _synthetic_raw_scores() -> dict[str, Any]:
    """Synthetic raw-score payload spanning a separable and an overlapping family.

    ssn (cutoff 0.60), cleanly separable:
      positives raw {0.80, 0.90}; FP-class raw {0.20 (none), 0.30 (suppress)}.
      -> fp_above_cutoff 0, tp_above 2, score_gap 0.80-0.30 = 0.50 (> 0).

    phone (cutoff 0.50), overlapping:
      positives raw {0.55, 0.95}; FP-class raw {0.40 (none), 0.70 (suppress)}.
      -> fp_above_cutoff 1 (the 0.70 suppress clears 0.50), tp_above 2,
         score_gap 0.55 - 0.70 = -0.15 (< 0, overlap).

    email has a cutoff in the map but no rows -> empty family, stable shape.
    """
    return {
        "schema_version": 1,
        "balanced_cutoffs": {"ssn": 0.60, "phone": 0.50, "email": 0.45},
        "absorbing_state_floor": _FLOOR,
        "rows": [
            _row("ssn", 0.80, "positive"),
            _row("ssn", 0.90, "positive"),
            _row("ssn", 0.20, "none"),
            _row("ssn", 0.30, "suppress"),
            _row("phone", 0.55, "positive"),
            _row("phone", 0.95, "positive"),
            _row("phone", 0.40, "none"),
            _row("phone", 0.70, "suppress"),
        ],
    }


def _run() -> dict[str, Any]:
    return build_headroom(_synthetic_raw_scores())


def test_top_level_shape() -> None:
    payload = _run()
    assert payload["schema_version"] == 1
    assert payload["generated_by"] == "resecta_data.eval.headroom"
    assert payload["absorbing_state_floor"] == pytest.approx(_FLOOR)
    # email is present (from the cutoff map) even with no rows.
    assert set(payload["per_family"]) == {"ssn", "phone", "email"}


def test_separable_family_counts() -> None:
    ssn = _run()["per_family"]["ssn"]
    assert ssn["cutoff"] == pytest.approx(0.60)
    assert ssn["positive_count"] == 2
    assert ssn["fp_count"] == 2
    assert ssn["fp_above_cutoff"] == 0
    assert ssn["fp_below_cutoff"] == 2
    assert ssn["tp_above"] == 2
    assert ssn["tp_below"] == 0
    assert ssn["suppressible_fp_mass"] == 0
    # min(TP)=0.80, max(FP)=0.30 -> gap 0.50, separable.
    assert ssn["score_gap"] == pytest.approx(0.50)


def test_overlapping_family_counts() -> None:
    phone = _run()["per_family"]["phone"]
    assert phone["cutoff"] == pytest.approx(0.50)
    # the 0.70 suppress clears the 0.50 cutoff; the 0.40 none does not.
    assert phone["fp_above_cutoff"] == 1
    assert phone["fp_below_cutoff"] == 1
    assert phone["suppressible_fp_mass"] == 1
    assert phone["tp_above"] == 2
    # min(TP)=0.55, max(FP)=0.70 -> gap -0.15, overlap.
    assert phone["score_gap"] == pytest.approx(-0.15)


def test_empty_family_stable_shape() -> None:
    email = _run()["per_family"]["email"]
    assert email["cutoff"] == pytest.approx(0.45)
    assert email["positive_count"] == 0
    assert email["fp_count"] == 0
    assert email["fp_above_cutoff"] == 0
    assert email["tp_above"] == 0
    assert email["score_gap"] is None
    assert email["posterior_summary"]["positive"] == {
        "count": 0,
        "min": None,
        "mean": None,
        "max": None,
    }
    assert email["raw_percentiles"]["positive"] == {
        "5": None,
        "25": None,
        "50": None,
        "75": None,
        "95": None,
    }


def test_posterior_matches_engine_seam_arithmetic() -> None:
    # sigmoid(logit(raw) + logit(floor)); hand-check at raw == floor == 0.5 -> 0.5.
    assert _posterior(0.5, 0.5) == pytest.approx(0.5)
    # General identity check against an independent computation.
    raw, floor = 0.80, _FLOOR
    expected = 1.0 / (1.0 + math.exp(-(math.log(raw / (1 - raw)) + math.log(floor / (1 - floor)))))
    assert _posterior(raw, floor) == pytest.approx(expected)
    # The emitted cutoff_posterior uses the same function.
    ssn = _run()["per_family"]["ssn"]
    assert ssn["posterior_summary"]["cutoff_posterior"] == pytest.approx(_posterior(0.60, _FLOOR))


def test_posterior_clamps_extreme_raw() -> None:
    # raw at the open-interval endpoints must stay finite (engine clamps too).
    assert 0.0 < _posterior(0.0, _FLOOR) < 1.0
    assert 0.0 < _posterior(1.0, _FLOOR) < 1.0


def test_raw_percentiles_nearest_rank() -> None:
    phone = _run()["per_family"]["phone"]
    # positives {0.55, 0.95}: nearest-rank p50 = ceil(0.5*2)=1 -> ordered[0]=0.55.
    assert phone["raw_percentiles"]["positive"]["50"] == pytest.approx(0.55)
    assert phone["raw_percentiles"]["positive"]["95"] == pytest.approx(0.95)


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
    dest = tmp_build_dir / "g8_headroom.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_headroom")


def test_null_cutoff_family_reports_zero_split() -> None:
    payload = build_headroom(
        {
            "balanced_cutoffs": {},  # no cutoffs at all
            "absorbing_state_floor": _FLOOR,
            "rows": [_row("mrn", 0.9, "positive"), _row("mrn", 0.1, "none")],
        }
    )
    mrn = payload["per_family"]["mrn"]
    assert mrn["cutoff"] is None
    assert mrn["fp_above_cutoff"] == 0
    assert mrn["tp_above"] == 0
    assert mrn["suppressible_fp_mass"] == 0
    assert mrn["posterior_summary"]["cutoff_posterior"] is None
