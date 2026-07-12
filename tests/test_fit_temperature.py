"""Tests for the Phase 3b temperature fit."""

from __future__ import annotations

import math
from pathlib import Path

from calibration_fixtures import make_softmax_dump
from resecta_data.classifier import build_fit_temperature
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.mechanism_language import assert_safe
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


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    corpus_path = tmp_path / "g8_corpus.json"
    dump_canonical_json(corpus, corpus_path)

    dump = make_softmax_dump(corpus)
    dump_path = tmp_path / "softmax_dump.json"
    dump_canonical_json(dump, dump_path)
    return dump_path, corpus_path


def test_schema_validates(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    payload = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    dest = tmp_build_dir / "doctype_temperature.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "doctype_temperature")


def test_deterministic(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    a = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    b = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert a == b


def test_nll_does_not_regress(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    payload = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert payload["fit_metadata"]["nll_after"] <= payload["fit_metadata"]["nll_before"] + 1e-9


def test_temperature_is_finite_and_positive(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    payload = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    t = payload["temperature"]
    assert math.isfinite(t)
    assert t > 0


def test_status_is_calibrated(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    payload = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert payload["status"] == "calibrated"


def test_fit_metadata_records_dump_sha(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    payload = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    sha = payload["fit_metadata"]["input_dump_sha256"]
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_notes_field_is_mechanism_safe(tmp_build_dir: Path) -> None:
    dump_path, corpus_path = _setup(tmp_build_dir)
    payload = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=dump_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert_safe(payload["notes"], context="doctype_temperature notes")
