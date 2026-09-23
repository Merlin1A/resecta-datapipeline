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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.corpus._spans import CONTEXT_CLASSES
from resecta_data.eval import spans as eval_spans
from resecta_data.eval.documents import wilson_ci

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
                        "context_class": "role_label",
                    },
                    {
                        "category": "ssn",
                        "start": 30,
                        "end": 41,
                        "expected_outcome": "redact",
                        "tier": "must",
                        "value": "555-12-3456",
                        "context_class": "none",
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
                        "context_class": "role_label",
                    },
                    {
                        "category": "licensePlate",
                        "start": 22,
                        "end": 27,
                        "expected_outcome": "suppress",
                        "tier": "must_not",
                        "value": "12345",
                        "context_class": "none",
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
        payload: Mapping[str, Any] = eval_spans.build_span_outcomes(
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
        # Any overlap credits both TPs; the all-tokens rule drops the partially covered one.
        assert (name["recall"], name["recall_all_tokens"]) == (1.0, 0.5)
        assert name["recall_all_tokens_wilson95"] == wilson_ci(1, 2)
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
        # The context annotation, read from the corpus at the join.
        descriptor = payload["context_class"]
        assert descriptor["present"] == ["none", "role_label"]
        assert descriptor["classed_ground_truth_rows"] == 4
        assert len(descriptor["vocabulary"]) == len(CONTEXT_CLASSES)
        by_class = payload["by_context_class"]
        assert set(by_class) == {"name", "ssn", "licensePlate"}
        assert (by_class["name"]["role_label"]["tp"], by_class["name"]["role_label"]["fn"]) == (
            2,
            0,
        )
        assert by_class["name"]["role_label"]["one_token_tp"] == 1
        assert by_class["ssn"]["none"]["fn"] == 1
        assert by_class["licensePlate"]["none"]["must_not_total"] == 1
        # fp is 0 by construction in the class tables (a detection-only row names no span).
        assert all(t["fp"] == 0 for fam in by_class.values() for t in fam.values())
        cells = payload["cells_by_context_class"]
        assert set(cells) == {
            "name_court_white_role_label",
            "ssn_court_white_none",
            "name_medical_asian_role_label",
            "licensePlate_medical_asian_none",
        }
        assert cells["name_court_white_role_label"]["context_class"] == "role_label"
        assert cells["name_court_white_role_label"]["tp"] == 1

    def test_unannotated_corpus_yields_null_descriptor_and_empty_class_tables(self) -> None:
        corpus = _corpus()
        for doc in corpus["documents"]:
            for span in doc["pii_spans"]:
                del span["context_class"]
        payload = eval_spans.build_span_outcomes(
            _rows(), corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
        )
        assert payload["context_class"] is None
        assert payload["by_context_class"] == {}
        assert payload["cells_by_context_class"] == {}
        # The class-agnostic aggregate is untouched by the annotation's absence.
        assert payload["per_family"]["name"]["tp"] == 2

    def test_furniture_join_attributes_detection_only_rows_by_kind(self) -> None:
        """A generator profile's furniture[] regions (1.2 C12-95 Spec-D): every
        detection-only fp row joins the kinds it overlaps; the rest are
        unattributed; ground-truth rows never join; the corpus as furnished
        (no furniture anywhere) yields a null descriptor and an empty table."""
        corpus = _corpus()
        doc_b = corpus["documents"][1]
        # "Patient " is a planted role noun; " is a decoy." a planted closing.
        doc_b["furniture"] = [
            {"start": 0, "end": 7, "kind": "role_noun"},
            {"start": 28, "end": 39, "kind": "closing"},
        ]
        rows = [
            *_rows(),
            # A name detection on the planted "Patient" (attributes to role_noun).
            {
                "doc_id": "doc_b",
                "family": "name",
                "start": 0,
                "end": 7,
                "tier": None,
                "outcome": "fp",
            },
            # A name detection inside the closing region.
            {
                "doc_id": "doc_b",
                "family": "name",
                "start": 33,
                "end": 39,
                "tier": None,
                "outcome": "fp",
            },
        ]
        payload: Mapping[str, Any] = eval_spans.build_span_outcomes(
            rows, corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
        )
        descriptor = payload["furniture"]
        assert descriptor["kinds_present"] == ["closing", "role_noun"]
        assert descriptor["regions"] == 2
        assert descriptor["documents_with_furniture"] == 1
        table = payload["by_furniture_kind"]
        # doc_a's phone fp overlaps no region (doc_a has no furniture) -> unattributed.
        assert table == {
            "name": {
                "closing": {"fp": 1, "by_doctype": {"medical": 1}},
                "role_noun": {"fp": 1, "by_doctype": {"medical": 1}},
            },
            "phone": {"unattributed": {"fp": 1, "by_doctype": {"court": 1}}},
        }
        # The class-agnostic counters see the two extra fp rows and nothing else moved.
        assert payload["row_counts"]["fp"] == 3
        assert payload["per_family"]["name"]["fp"] == 2
        assert payload["per_family"]["name"]["tp"] == 2
        # The tp rows (ground truth) never join furniture even where they overlap it.
        assert "unattributed" not in table["name"]

    def test_no_furniture_yields_null_descriptor_and_empty_table(self) -> None:
        payload = eval_spans.build_span_outcomes(
            _rows(), _corpus(), site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
        )
        assert payload["furniture"] is None
        assert payload["by_furniture_kind"] == {}
        assert payload["schema_version"] == 3

    def test_malformed_furniture_is_an_error(self) -> None:
        corpus = _corpus()
        corpus["documents"][0]["furniture"] = [{"start": 5, "end": 4, "kind": "label"}]
        with pytest.raises(PipelineError, match="malformed furniture region"):
            eval_spans.build_span_outcomes(
                _rows(), corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )
        corpus["documents"][0]["furniture"] = [{"start": 0, "end": 4, "kind": ""}]
        with pytest.raises(PipelineError, match="without a kind"):
            eval_spans.build_span_outcomes(
                _rows(), corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )

    def test_partial_or_unknown_annotation_is_an_error(self) -> None:
        corpus = _corpus()
        del corpus["documents"][0]["pii_spans"][1]["context_class"]
        with pytest.raises(PipelineError, match="annotation is partial"):
            eval_spans.build_span_outcomes(
                _rows(), corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )
        corpus = _corpus()
        corpus["documents"][0]["pii_spans"][0]["context_class"] = "footnote"
        with pytest.raises(PipelineError, match="unknown context_class 'footnote'"):
            eval_spans.build_span_outcomes(
                _rows(), corpus, site="siteB", spans_sha256="0" * 64, corpus_sha256="1" * 64
            )

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
