"""Determinism + schema + shape checks for the G8 bucket-recall builder.

Point estimate + sample size, no CI band. The artifact is dev/CI only
-- not installed to the Swift Resources path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resecta_data.bloom.filter import BloomFilter
from resecta_data.bloom.normalize import nfkc_lower
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json, sha256_bytes
from resecta_data.common.schema import validate_file
from resecta_data.demographics.g8_bucket_recall import build as build_g8_bucket_recall

_SCHEMAS = Path(__file__).parent.parent / "schemas"

_BLOOM_M = 4096
_BLOOM_K = 4
_BLOOM_SEED = 20260416


def _serialize_bloom_with(known_surnames: list[str]) -> bytes:
    """Build a tiny RSBF-serialized Bloom from ``known_surnames``."""
    bf = BloomFilter.empty(m=_BLOOM_M, k=_BLOOM_K, seed=_BLOOM_SEED)
    for name in known_surnames:
        bf.insert(nfkc_lower(name))
    # The 32-byte source_hash field is opaque from the recall builder's
    # perspective; any constant works for tests.
    return bf.serialize(source_hash=b"\x00" * 32)


def _doc(doc_id: str, bucket: str, names: list[str]) -> dict[str, Any]:
    spans = [
        {
            "adversarial": False,
            "category": "name",
            "end": 10,
            "expected_outcome": "redact",
            "start": 0,
            "value": value,
        }
        for value in names
    ]
    return {
        "adversarial_tags": [],
        "demographic_bucket": bucket,
        "doctype": "court",
        "id": doc_id,
        "pii_spans": spans,
    }


def _synthetic_corpus() -> dict[str, Any]:
    return {
        "documents": [
            # 'white' bucket: 2 names, both in bloom -> 100% recall, sample=2
            _doc("court_000000", "white", ["Alice Smith", "Bob Jones"]),
            # 'black' bucket: 2 names, 1 hit (Powell) + 1 miss (Zzz) -> 50%, sample=2
            _doc("court_000001", "black", ["Keisha Powell", "Foo Zzz"]),
            # 'hispanic': 1 name, 0 hits -> 0%, sample=1
            _doc("court_000002", "hispanic", ["Daniel NotInBloom"]),
            # 'asian': 1 name, 1 hit -> 100%, sample=1
            _doc("court_000003", "asian", ["Kenji Nakamura"]),
            # 'ai_an': 1 name, 1 hit -> 100%, sample=1
            _doc("court_000004", "ai_an", ["Jose Yellowtail"]),
        ],
    }


def _run_builder() -> dict[str, Any]:
    bloom_bytes = _serialize_bloom_with(["smith", "jones", "powell", "nakamura", "yellowtail"])
    return build_g8_bucket_recall(
        CANONICAL_SEED,
        corpus=_synthetic_corpus(),
        surnames_bloom_bytes=bloom_bytes,
        bloom_version_sha256=sha256_bytes(bloom_bytes),
    )


def test_recall_per_bucket_matches_known_inputs() -> None:
    payload = _run_builder()
    buckets = payload["buckets"]
    assert buckets["white"] == {"recall_pts": 100.0, "sample_size": 2}
    assert buckets["black"] == {"recall_pts": 50.0, "sample_size": 2}
    assert buckets["hispanic"] == {"recall_pts": 0.0, "sample_size": 1}
    assert buckets["asian"] == {"recall_pts": 100.0, "sample_size": 1}
    assert buckets["ai_an"] == {"recall_pts": 100.0, "sample_size": 1}


def test_top_level_fields() -> None:
    payload = _run_builder()
    assert payload["metric"] == "g8_bucket_stratified_recall"
    assert payload["version"] == "v1"
    assert payload["seed"] == CANONICAL_SEED
    assert payload["ci_band"] is None
    assert "F-10" in payload["ci_band_note"]
    assert len(payload["bloom_version_sha256"]) == 64


def test_byte_identical_across_invocations(tmp_build_dir: Path) -> None:
    a = _run_builder()
    b = _run_builder()
    dest_a = tmp_build_dir / "a.json"
    dest_b = tmp_build_dir / "b.json"
    dump_canonical_json(a, dest_a)
    dump_canonical_json(b, dest_b)
    assert dest_a.read_bytes() == dest_b.read_bytes()


def test_schema_validates(tmp_build_dir: Path) -> None:
    payload = _run_builder()
    dest = tmp_build_dir / "g8_bucket_recall_v1.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "g8_bucket_recall")


def test_canonical_json_roundtrip(tmp_build_dir: Path) -> None:
    payload = _run_builder()
    dest = tmp_build_dir / "g8_bucket_recall_v1.json"
    dump_canonical_json(payload, dest)
    reloaded = json.loads(dest.read_text(encoding="utf-8"))
    assert reloaded == payload
