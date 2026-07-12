"""End-to-end driver test: Swift JSONs in -> two derived artifacts out.

Writes synthetic ``_cells.json`` / ``_raw_scores.json`` to disk, runs
``eval.run.main`` twice into separate dirs, and asserts both emitted files are
byte-identical across runs and validate against their schemas. Also exercises
the Click ``build eval-baseline`` command via CliRunner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval import run as eval_run

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"


def _cells() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_by": "G8BaselineHarness.sweepG8Corpus",
        "doc_count": 1,
        "cells": {
            "ssn_court_white": {
                "true_positives": 4,
                "false_negatives": 1,
                "false_positives": 1,
                "adversarial_suppress_total": 1,
                "adversarial_suppress_fired": 0,
                "suppressed_by_negative_context": 0,
            },
        },
    }


def _raw_scores() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "balanced_cutoffs": {"ssn": 0.6},
        "absorbing_state_floor": 0.35,
        "rows": [
            {
                "category": "ssn",
                "doctype": "court",
                "bucket": "white",
                "raw": 0.8,
                "gt_class": "positive",
            },
            {
                "category": "ssn",
                "doctype": "court",
                "bucket": "white",
                "raw": 0.2,
                "gt_class": "none",
            },
        ],
    }


def _write_inputs(d: Path) -> tuple[Path, Path]:
    cells_path = d / "baseline_cells.json"
    raw_path = d / "baseline_raw_scores.json"
    dump_canonical_json(_cells(), cells_path)
    dump_canonical_json(_raw_scores(), raw_path)
    return cells_path, raw_path


def test_driver_emits_both_artifacts_and_validates(tmp_path: Path) -> None:
    cells_path, raw_path = _write_inputs(tmp_path / "in")
    out = tmp_path / "out"
    written = eval_run.main(cells_path, raw_path, out)
    assert written["baseline"].is_file()
    assert written["headroom"].is_file()
    validate_file(written["baseline"], _SCHEMAS, "g8_detection_baseline")
    validate_file(written["headroom"], _SCHEMAS, "g8_headroom")


def test_driver_is_byte_deterministic(tmp_path: Path) -> None:
    cells_path, raw_path = _write_inputs(tmp_path / "in")
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    wa = eval_run.main(cells_path, raw_path, out_a)
    wb = eval_run.main(cells_path, raw_path, out_b)
    assert wa["baseline"].read_bytes() == wb["baseline"].read_bytes()
    assert wa["headroom"].read_bytes() == wb["headroom"].read_bytes()


def test_cli_eval_baseline_command(tmp_path: Path) -> None:
    cells_path, raw_path = _write_inputs(tmp_path / "in")
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "build",
            "eval-baseline",
            "--cells",
            str(cells_path),
            "--raw-scores",
            str(raw_path),
            "--out-dir",
            str(out),
        ],
        env={"PYTHONHASHSEED": "0"},
    )
    assert result.exit_code == 0, result.output
    assert "grand-total precision=" in result.output
    assert (out / "g8_detection_baseline.json").is_file()
    assert (out / "g8_headroom.json").is_file()
