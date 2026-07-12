"""Schema and shape checks for the demographic coverage report."""

from __future__ import annotations

from pathlib import Path

from resecta_data.bloom.corpus_ingest import (
    DEMOGRAPHIC_LABELS,
    IngestedRow,
    IngestResult,
    merge,
)
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.demographics import build as build_demographics

_SCHEMAS = Path(__file__).parent.parent / "schemas"
_SHARE_TOL = 1e-9


def _synthetic_ingest() -> IngestResult:
    rows = [
        IngestedRow("smith", "s", "white"),
        IngestedRow("jackson", "s", "black"),
        IngestedRow("garcia", "s", "hispanic"),
        IngestedRow("kim", "s", "asian"),
        IngestedRow("begay", "s", "ai_an"),
        IngestedRow("anonymous", "s", "unlabeled"),
    ]
    return merge(rows)


def test_schema_validates(tmp_build_dir: Path) -> None:
    ingest = _synthetic_ingest()
    payload = build_demographics(
        CANONICAL_SEED,
        ingests={"surnames": ingest, "given-names": ingest},
        source_ids=["s"],
    )
    dest = tmp_build_dir / "coverage_report.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "demographic_coverage")


def test_bucket_counts_sum_to_total() -> None:
    ingest = _synthetic_ingest()
    payload = build_demographics(
        CANONICAL_SEED,
        ingests={"surnames": ingest},
        source_ids=["s"],
    )
    report = payload["filters"][0]
    counts_sum = sum(b["count"] for b in report["buckets"])
    assert counts_sum == report["total_unique_keys"]


def test_shares_sum_to_one() -> None:
    ingest = _synthetic_ingest()
    payload = build_demographics(
        CANONICAL_SEED,
        ingests={"surnames": ingest},
        source_ids=["s"],
    )
    shares_sum = sum(b["share"] for b in payload["filters"][0]["buckets"])
    assert abs(shares_sum - 1.0) < _SHARE_TOL


def test_bucket_labels_are_canonical_and_ordered() -> None:
    ingest = _synthetic_ingest()
    payload = build_demographics(
        CANONICAL_SEED,
        ingests={"surnames": ingest},
        source_ids=["s"],
    )
    labels = [b["label"] for b in payload["filters"][0]["buckets"]]
    assert labels == list(DEMOGRAPHIC_LABELS)


def test_parity_gap_is_deterministic() -> None:
    ingest = _synthetic_ingest()
    a = build_demographics(
        CANONICAL_SEED,
        ingests={"surnames": ingest},
        source_ids=["s"],
    )
    b = build_demographics(
        CANONICAL_SEED,
        ingests={"surnames": ingest},
        source_ids=["s"],
    )
    assert a == b
