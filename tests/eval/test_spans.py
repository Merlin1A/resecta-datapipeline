"""Hand-computed checks for the per-span outcome reader.

Builds a three-document corpus and a small sidecar inline (no Swift output
needed) and pins the row validation (offsets only, outcome vocabulary, tier
rules), the corpus join (family and tier cross-check, every span present
once), the token-coverage rule behind ``one_token_tp``, the per-cell
reconciliation against a trio ``_cells.json`` payload, the aggregate shape
against its schema, and the ``build eval-baseline --spans`` wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.eval import spans as eval_spans

_SCHEMAS = Path(__file__).parent.parent.parent / "schemas"


def _corpus() -> dict[str, Any]:
    return {
        "seed": 1,
        "documents": [
            {
                "id": "doc_a",
                "doctype": "court",
                "demographic_bucket": "white",
                "text": "Plaintiff Delia Hartwell, SSN 555-12-3456, filed.",
                "pii_spans": [
                    {
                        "category": "name",
                        "start": 10,
                        "end": 24,
                        "expected_outcome": "redact",
                        "tier": "must",
                        "value": "Delia Hartwell",
                    },
                    {
                        "category": "ssn",
                        "start": 30,
                        "end": 41,
                        "expected_outcome": "redact",
                        "tier": "must",
                        "value": "555-12-3456",
                    },
                ],
            },
            {
                "id": "doc_b",
                "doctype": "medical",
                "demographic_bucket": "asian",
                "text": "Patient Mei Chen. Reg 12345 is a decoy.",
                "pii_spans": [
                    {
                        "category": "name",
                        "start": 8,
                        "end": 16,
                        "expected_outcome": "redact",
                        "tier": "should",
                        "value": "Mei Chen",
                    },
                    {
                        "category": "licensePlate",
                        "start": 22,
                        "end": 27,
                        "expected_outcome": "suppress",
                        "tier": "must_not",
                        "value": "12345",
                    },
                ],
            },
        ],
    }


def _rows() -> list[dict[str, Any]]:
    return [
        # doc_a name: the detector covered only the first token (one-token tp).
        {
            "doc_id": "doc_a",
            "family": "name",
            "start": 10,
            "end": 24,
            "tier": "must",
            "outcome": "tp",
            "det_start": 10,
            "det_end": 15,
            "det_spans": [[10, 15]],
        },
        # doc_a ssn: missed.
        {
            "doc_id": "doc_a",
            "family": "ssn",
            "start": 30,
            "end": 41,
            "tier": "must",
            "outcome": "fn",
        },
        # doc_a: a phone detection overlapping no ground truth.
        {
            "doc_id": "doc_a",
            "family": "phone",
            "start": 30,
            "end": 41,
            "tier": None,
            "outcome": "fp",
        },
        # doc_b name: fully covered.
        {
            "doc_id": "doc_b",
            "family": "name",
            "start": 8,
            "end": 16,
            "tier": "should",
            "outcome": "tp",
            "det_start": 8,
            "det_end": 16,
            "det_spans": [[8, 11], [12, 16]],
        },
        # doc_b plate decoy stayed quiet.
        {
            "doc_id": "doc_b",
            "family": "licensePlate",
            "start": 22,
            "end": 27,
            "tier": "must_not",
            "outcome": "tn",
        },
    ]


def _cells() -> dict[str, Any]:
    zero = {
        "true_positives": 0,
        "false_negatives": 0,
        "false_positives": 0,
        "adversarial_suppress_total": 0,
        "adversarial_suppress_fired": 0,
        "suppressed_by_negative_context": 0,
        "tier_must_total": 0,
        "tier_must_covered": 0,
        "tier_should_total": 0,
        "tier_should_covered": 0,
        "tier_watch_total": 0,
        "tier_watch_covered": 0,
        "tier_must_not_total": 0,
        "tier_must_not_fired": 0,
    }
    return {
        "schema_version": 1,
        "generated_by": "G8BaselineHarness.sweepG8Corpus",
        "site": "siteB",
        "doc_count": 2,
        "cells": {
            "name_court_white": {
                **zero,
                "true_positives": 1,
                "tier_must_total": 1,
                "tier_must_covered": 1,
            },
            "ssn_court_white": {**zero, "false_negatives": 1, "tier_must_total": 1},
            "phone_court_white": {**zero, "false_positives": 1},
            "name_medical_asian": {
                **zero,
                "true_positives": 1,
                "tier_should_total": 1,
                "tier_should_covered": 1,
            },
            "licenseplate_medical_asian": {
                **zero,
                "adversarial_suppress_total": 1,
                "tier_must_not_total": 1,
            },
        },
    }


def _write_sidecar(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


class TestValidation:
    def test_text_key_is_rejected(self, tmp_path: Path) -> None:
        rows = _rows()
        rows[0]["text"] = "Delia"
        _write_sidecar(tmp_path / "s.jsonl", rows)
        with pytest.raises(PipelineError, match="unexpected keys"):
            eval_spans.read_sidecar(tmp_path / "s.jsonl")

    def test_unknown_outcome_and_family(self, tmp_path: Path) -> None:
        bad = _rows()
        bad[1]["outcome"] = "miss"
        _write_sidecar(tmp_path / "s.jsonl", bad)
        with pytest.raises(PipelineError, match="unknown outcome"):
            eval_spans.read_sidecar(tmp_path / "s.jsonl")
        bad = _rows()
        bad[1]["family"] = "surname"
        _write_sidecar(tmp_path / "s.jsonl", bad)
        with pytest.raises(PipelineError, match="unknown family"):
            eval_spans.read_sidecar(tmp_path / "s.jsonl")

    def test_det_offsets_only_on_covered_rows(self, tmp_path: Path) -> None:
        bad = _rows()
        del bad[0]["det_start"], bad[0]["det_end"], bad[0]["det_spans"]
        _write_sidecar(tmp_path / "s.jsonl", bad)
        with pytest.raises(PipelineError, match="det offsets are required on tp rows"):
            eval_spans.read_sidecar(tmp_path / "s.jsonl")
        bad = _rows()
        bad[2]["tier"] = "must"
        _write_sidecar(tmp_path / "s.jsonl", bad)
        with pytest.raises(PipelineError, match="fp rows carry tier null or must_not"):
            eval_spans.read_sidecar(tmp_path / "s.jsonl")

    def test_det_spans_must_be_ordered_overlapping_and_hulled(self, tmp_path: Path) -> None:
        cases: list[tuple[Any, str]] = [
            ([[12, 16], [8, 11]], "start order"),
            ([[8, 11]], "hull of det_spans"),
            ([[8, 11], [40, 44]], "does not overlap"),
            (None, "every overlapping detection"),
        ]
        for spans, message in cases:
            bad = _rows()
            if spans is None:
                del bad[3]["det_spans"]
            else:
                bad[3]["det_spans"] = spans
            _write_sidecar(tmp_path / "s.jsonl", bad)
            with pytest.raises(PipelineError, match=message):
                eval_spans.read_sidecar(tmp_path / "s.jsonl")

    def test_bridged_tier_matches_the_emitter(self) -> None:
        assert eval_spans.bridged_tier({"tier": "should"}) == "should"
        assert eval_spans.bridged_tier({"expected_outcome": "suppress"}) == "must_not"
        assert eval_spans.bridged_tier({"expected_outcome": "flag"}) == "watch"
        assert eval_spans.bridged_tier({"expected_outcome": "redact"}) == "must"


class TestJoin:
    def test_hand_computed_aggregate(self) -> None:
        payload = eval_spans.build_span_outcomes(
            _rows(),
            _corpus(),
            site="siteB",
            spans_sha256="0" * 64,
            corpus_sha256="1" * 64,
            cells_payload=_cells(),
        )
        assert payload["row_counts"] == {
            "total": 5,
            "ground_truth": 4,
            "corpus_spans": 4,
            "tp": 2,
            "fn": 1,
            "fp": 1,
            "tn": 1,
            "must_not_fired": 0,
            "one_token_tp": 1,
        }
        name = payload["per_family"]["name"]
        assert (name["tp"], name["fn"], name["one_token_tp"]) == (2, 0, 1)
        assert name["token_coverage"] == {"1/2": 1, "2/2": 1}
        assert name["detections_per_tp"] == {"1": 1, "2": 1}
        assert name["by_tier"] == {
            "must": {"total": 1, "covered": 1},
            "should": {"total": 1, "covered": 1},
            "watch": {"total": 0, "covered": 0},
        }
        assert payload["per_family"]["licensePlate"]["must_not_total"] == 1
        assert payload["cells"]["ssn_court_white"]["recall"] == 0.0
        assert payload["cells"]["ssn_court_white"]["context_class"] is None
        assert payload["cells_crosscheck"] == {"status": "identical", "cells_compared": 5}

    def test_family_or_tier_drift_from_the_corpus_is_an_error(self) -> None:
        rows = _rows()
        rows[1]["family"] = "itin"
        with pytest.raises(PipelineError, match="family 'itin' vs corpus 'ssn'"):
            eval_spans.build_span_outcomes(
                rows, _corpus(), site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )
        rows = _rows()
        rows[3]["tier"] = "must"
        with pytest.raises(PipelineError, match="tier 'must' vs corpus bridge 'should'"):
            eval_spans.build_span_outcomes(
                rows, _corpus(), site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )

    def test_bmp_text_joins_and_astral_text_is_refused(self) -> None:
        corpus = _corpus()
        # An accented name and an em dash: UTF-16 and code-point offsets agree.
        corpus["documents"][1]["text"] = "Patient Jos\u00e9 Ch\u00e9n \u2014 Reg 12345 is a decoy."
        corpus["documents"][1]["pii_spans"][0]["value"] = "Jos\u00e9 Ch\u00e9n"
        corpus["documents"][1]["pii_spans"][0]["end"] = 17
        corpus["documents"][1]["pii_spans"][1]["start"] = 24
        corpus["documents"][1]["pii_spans"][1]["end"] = 29
        rows = _rows()
        rows[3]["end"] = 17
        rows[3]["det_end"] = 12  # one token of two
        rows[3]["det_spans"] = [[8, 12]]
        rows[4]["start"], rows[4]["end"] = 24, 29
        payload = eval_spans.build_span_outcomes(
            rows, corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
        )
        assert payload["per_family"]["name"]["token_coverage"] == {"1/2": 2}
        corpus["documents"][1]["text"] = "Patient \U0001f600 " + corpus["documents"][1]["text"]
        with pytest.raises(PipelineError, match="beyond U\\+FFFF"):
            eval_spans.build_span_outcomes(
                rows, corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )

    def test_every_corpus_span_must_appear_once(self) -> None:
        rows = _rows()[:-1]
        with pytest.raises(PipelineError, match="3 ground-truth rows; the corpus has 4"):
            eval_spans.build_span_outcomes(
                rows, _corpus(), site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )

    def test_cells_mismatch_is_an_error(self) -> None:
        cells = _cells()
        cells["cells"]["ssn_court_white"]["false_negatives"] = 2
        with pytest.raises(PipelineError, match=r"ssn_court_white\.false_negatives"):
            eval_spans.build_span_outcomes(
                _rows(),
                _corpus(),
                site="siteB",
                spans_sha256="0" * 64,
                corpus_sha256="1" * 64,
                cells_payload=cells,
            )


class TestDriver:
    def test_main_writes_schema_valid_artifact_deterministically(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "g8_siteb_spans.jsonl"
        _write_sidecar(sidecar, _rows())
        corpus = tmp_path / "g8_corpus.json"
        dump_canonical_json(_corpus(), corpus)
        out_a = eval_spans.main(sidecar, corpus, tmp_path / "a", cells_payload=_cells())
        out_b = eval_spans.main(sidecar, corpus, tmp_path / "b", cells_payload=_cells())
        assert out_a.read_bytes() == out_b.read_bytes()
        validate_file(out_a, _SCHEMAS, "g8_span_outcomes")
        payload = json.loads(out_a.read_text(encoding="utf-8"))
        assert payload["site"] == "siteB"

    def test_cli_eval_baseline_with_spans(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "g8_siteb_spans.jsonl"
        _write_sidecar(sidecar, _rows())
        corpus = tmp_path / "g8_corpus.json"
        dump_canonical_json(_corpus(), corpus)
        cells = tmp_path / "g8_siteb_cells.json"
        dump_canonical_json(_cells(), cells)
        raw_scores = tmp_path / "g8_siteb_raw_scores.json"
        dump_canonical_json(
            {
                "schema_version": 1,
                "site": "siteB",
                "balanced_cutoffs": {},
                "absorbing_state_floor": 0.0,
                "rows": [],
            },
            raw_scores,
        )
        runner = CliRunner()
        result = runner.invoke(
            cli_main,
            [
                "build",
                "eval-baseline",
                "--cells",
                str(cells),
                "--raw-scores",
                str(raw_scores),
                "--out-dir",
                str(tmp_path / "out"),
                "--spans",
                str(sidecar),
                "--corpus",
                str(corpus),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "g8_span_outcomes.json").is_file()
        assert "g8_span_outcomes.json" in result.output
        # --spans without --corpus is refused.
        result = runner.invoke(
            cli_main,
            [
                "build",
                "eval-baseline",
                "--cells",
                str(cells),
                "--raw-scores",
                str(raw_scores),
                "--out-dir",
                str(tmp_path / "out2"),
                "--spans",
                str(sidecar),
            ],
        )
        assert result.exit_code != 0
