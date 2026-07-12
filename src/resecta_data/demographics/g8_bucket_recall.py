"""Build the G8 bucket-stratified recall artifact (D-24).

Measures, per Census demographic bucket, the recall of the surnames Bloom
filter against the synthetic G8 corpus. Replaces the 81.70 pt population-
composition figure in the V1 transparency copy with a measured number.

Per V1 spec §1.24 + F-10 ack option a: point estimate + sample size only;
no CI band in V1. V1.1+ W-K may refine with larger corpora.

The artifact is dev/CI only -- it is NOT installed to the Swift Resources
path; downstream consumers are the V1 README + Resecta UI transparency
copy.
"""

from __future__ import annotations

from typing import Any

from resecta_data.bloom.filter import BloomFilter
from resecta_data.bloom.packer import unpack

_MODULE_NAME = "resecta_data.demographics.g8_bucket_recall"
_SCHEMA_VERSION = "v1"
_METRIC = "g8_bucket_stratified_recall"

# Census buckets carried on G8 corpus documents (see schemas/g8_corpus.schema.json).
# Spec §1.24 enumerates {white, black, hispanic, api, other}; the actual G8
# corpus uses {white, black, hispanic, asian, ai_an}. The corpus partition is
# the source of truth (see cc-derive-d24-DONE.md spec-ambiguity defer-note).
_BUCKETS: tuple[str, ...] = ("white", "black", "hispanic", "asian", "ai_an")

_CI_BAND_NOTE = (
    "F-10 ack option a: V1 ships point estimate + sample size only; no CI "
    "band. V1.1+ W-K refines with larger demographically-stratified corpora."
)


def _surname_of(name_value: str) -> str | None:
    """Return the last whitespace-separated token of ``name_value``, or None.

    Empty / whitespace-only inputs return None so the caller can skip them.
    Mirrors the Swift NameDetector convention of treating the trailing token
    as the surname when no explicit surname slot is present.
    """
    parts = name_value.split()
    if not parts:
        return None
    return parts[-1]


def _bloom_from_bytes(rsbf_bytes: bytes) -> BloomFilter:
    """Reconstruct a queryable :class:`BloomFilter` from RSBF file bytes."""
    header, bit_array = unpack(rsbf_bytes)
    return BloomFilter(
        m=header.m,
        k=header.k,
        seed=header.seed,
        bit_array=bytearray(bit_array),
        row_count=header.row_count,
    )


def build(
    seed: int,
    *,
    corpus: dict[str, Any],
    surnames_bloom_bytes: bytes,
    bloom_version_sha256: str,
) -> dict[str, Any]:
    """Compute per-bucket recall and return the artifact payload.

    Args:
        seed: Recorded for uniformity; this builder is not randomized.
        corpus: Parsed G8 corpus dict (``schemas/g8_corpus.schema.json``).
        surnames_bloom_bytes: Raw RSBF bytes of ``build/gazetteers/surnames.bloom``.
        bloom_version_sha256: Lowercase hex SHA-256 of the bloom file the
            recall is measured against.

    Returns:
        A JSON-serializable dict matching ``schemas/g8_bucket_recall.schema.json``.
    """
    bloom = _bloom_from_bytes(surnames_bloom_bytes)

    hits = dict.fromkeys(_BUCKETS, 0)
    samples = dict.fromkeys(_BUCKETS, 0)

    for doc in corpus["documents"]:
        bucket = doc["demographic_bucket"]
        if bucket not in samples:
            # Skip any out-of-band bucket label rather than silently miscount.
            continue
        for span in doc["pii_spans"]:
            if span["category"] != "name":
                continue
            surname = _surname_of(span["value"])
            if surname is None:
                continue
            samples[bucket] += 1
            if bloom.contains(surname):
                hits[bucket] += 1

    buckets_payload: dict[str, dict[str, Any]] = {}
    for label in _BUCKETS:
        n = samples[label]
        recall_pts = (hits[label] / n) * 100.0 if n > 0 else 0.0
        buckets_payload[label] = {
            "recall_pts": recall_pts,
            "sample_size": n,
        }

    return {
        "metric": _METRIC,
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "bloom_version_sha256": bloom_version_sha256,
        "buckets": buckets_payload,
        "ci_band": None,
        "ci_band_note": _CI_BAND_NOTE,
    }
