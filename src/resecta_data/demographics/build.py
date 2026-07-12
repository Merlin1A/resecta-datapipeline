"""Build the demographic coverage report.

Summarizes how many keys in each Bloom filter are attributed to each Census
race/ethnicity bucket. Baseline for the Phase 4 G2 CI gate.

The report is a dev-time artifact: it stays in ``build/`` and is not installed
to the Swift Resources path. It is still schema-validated and hash-locked so
Phase 4 can compare quarterly.
"""

from __future__ import annotations

from typing import Any

from resecta_data.bloom.corpus_ingest import DEMOGRAPHIC_LABELS, IngestResult

_MODULE_NAME = "resecta_data.demographics.build"
_SCHEMA_VERSION = 1


def _buckets_for(ingest: IngestResult) -> list[dict[str, Any]]:
    """Return the six-row bucket list for a single filter's ingest result."""
    total = len(ingest.rows)
    if total == 0:
        shares = dict.fromkeys(DEMOGRAPHIC_LABELS, 0.0)
    else:
        shares = {
            label: ingest.per_demographic_counts[label] / total for label in DEMOGRAPHIC_LABELS
        }
    return [
        {
            "label": label,
            "count": ingest.per_demographic_counts[label],
            "share": shares[label],
        }
        for label in DEMOGRAPHIC_LABELS
    ]


def _parity_gap(filter_reports: list[dict[str, Any]]) -> float:
    """Max-minus-min share across labeled (non-unlabeled) buckets, in points.

    We consider all labeled buckets across all filters together; the gap is
    reported in percentage points (multiply share by 100) so the number is
    directly comparable to the Phase 4 ``<10 pt`` gate.
    """
    shares: list[float] = []
    for report in filter_reports:
        for bucket in report["buckets"]:
            if bucket["label"] == "unlabeled":
                continue
            shares.append(bucket["share"])
    if not shares:
        return 0.0
    return (max(shares) - min(shares)) * 100.0


def build(
    seed: int,
    *,
    ingests: dict[str, IngestResult],
    source_ids: list[str],
) -> dict[str, Any]:
    """Produce the coverage report payload.

    Args:
        seed: Recorded for uniformity; this builder is not randomized.
        ingests: Map from filter_name (``"surnames"`` / ``"given-names"``) to
            the :class:`IngestResult` produced by ``bloom.corpus_ingest.merge``.
        source_ids: Deduplicated list of source ids that contributed rows to
            either filter. Stored sorted.

    Returns:
        A JSON-serializable dict matching ``demographic_coverage.schema.json``.
    """
    filter_reports: list[dict[str, Any]] = []
    for filter_name in sorted(ingests):
        ingest = ingests[filter_name]
        filter_reports.append(
            {
                "filter_name": filter_name,
                "total_unique_keys": len(ingest.rows),
                "buckets": _buckets_for(ingest),
                "notes": (
                    "Buckets are plurality-labeled from Census race/ethnicity "
                    "shares; rows with no >=40% plurality are attributed to "
                    "'unlabeled'."
                ),
            }
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "filters": filter_reports,
        "parity_gap_points": _parity_gap(filter_reports),
        "generated_from_sources": sorted(set(source_ids)),
    }
