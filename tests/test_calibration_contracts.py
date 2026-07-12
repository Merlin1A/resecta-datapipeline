"""Contract tests for the Phase 3b Swift-dump input schemas.

These assert that the schemas themselves are load-able and that the
synthesised fixtures validate against them. They do not exercise the
calibration builders — see ``test_fit_temperature`` and
``test_sweep_thresholds`` for those.
"""

from __future__ import annotations

from pathlib import Path

from calibration_fixtures import make_score_dump, make_softmax_dump
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.corpus import build_g8_corpus

_SCHEMAS = Path(__file__).parent.parent / "schemas"
_MINI_COUNTS = {
    "court": 10,
    "medical": 10,
    "financial": 10,
    "foia": 10,
    "generic": 10,
}


def test_softmax_dump_fixture_validates(tmp_build_dir: Path) -> None:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    dump = make_softmax_dump(corpus)
    dest = tmp_build_dir / "softmax_dump.json"
    dump_canonical_json(dump, dest)
    validate_file(dest, _SCHEMAS, "doctype_softmax_dump")


def test_score_dump_fixture_validates(tmp_build_dir: Path) -> None:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    dump = make_score_dump(corpus)
    dest = tmp_build_dir / "detector_score_dump.json"
    dump_canonical_json(dump, dest)
    validate_file(dest, _SCHEMAS, "detector_score_dump")


def test_softmax_dump_has_one_entry_per_document() -> None:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    dump = make_softmax_dump(corpus)
    dump_ids = {d["doc_id"] for d in dump["documents"]}
    corpus_ids = {d["id"] for d in corpus["documents"]}
    assert dump_ids == corpus_ids


def test_score_dump_has_one_entry_per_document() -> None:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    dump = make_score_dump(corpus)
    dump_ids = {d["doc_id"] for d in dump["documents"]}
    corpus_ids = {d["id"] for d in corpus["documents"]}
    assert dump_ids == corpus_ids


def test_softmax_dump_class_order_matches_schema() -> None:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    dump = make_softmax_dump(corpus)
    assert dump["classes"] == ["court", "medical", "financial", "foia", "generic"]
