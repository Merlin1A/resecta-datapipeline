"""Round-trip + determinism tests for ``scripts/shard_paranames.py``.

Synthetic-fixture tests: no dependency on the ~954 MB LFS-tracked
``paranames_full.tsv.gz``. The full-file smoke run is captured in the PR
body once, not re-run by CI.

Invariants asserted:
  * the multiset of PER rows emitted across shards equals the input's
    PER row multiset (non-PER rows are filtered);
  * every ``wikidata_id`` appears in exactly one shard (the parallel
    ingest relies on this to preserve ``parse_paranames_full``'s
    sort-invariant check);
  * re-running the script against the same input yields the same
    decompressed shard content across runs — which is the determinism
    the bloom pipeline actually consumes. The raw gzip bytes are NOT
    byte-identical because the current script passes a tempfile path
    to ``gzip.GzipFile(filename=...)``, which leaks the tempfile's
    random basename into the gzip FNAME header. Fixing that is a
    separate follow-up against ``scripts/shard_paranames.py``.
"""

from __future__ import annotations

import gzip
import hashlib
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HEADER = b"wikidata_id\tlabel\tlanguage\ttype\n"
_SHARD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "shard_paranames.py"


def _build_synthetic_fixture(path: Path) -> tuple[bytes, list[bytes]]:
    """Write a small synthetic ParaNames TSV. Returns (all_bytes, per_rows).

    Layout: 5 wids x 3 languages x 2-4 alt-name variations, plus 6 non-PER
    rows interleaved. Rows for a single wid are contiguous (matches the
    upstream sort invariant the shard script relies on).
    """
    per_rows: list[bytes] = []
    blocks: list[bytes] = []
    wids = ["Q100", "Q101", "Q102", "Q103", "Q104"]
    langs = ["en", "fr", "de"]
    for i, wid in enumerate(wids):
        # Emit a non-PER row before each wid's PER block to prove filtering.
        blocks.append(f"LOCX{i}\tPlaceholder {i}\ten\tLOC\n".encode())
        # 2-4 name variations per language
        n_variants = 2 + (i % 3)
        for lang in langs:
            for v in range(n_variants):
                row = f"{wid}\tName{i}_{lang}_{v}\t{lang}\tPER\n".encode()
                blocks.append(row)
                per_rows.append(row)
    # Trailing non-PER row.
    blocks.append(b"LOCZ\tTrailing\ten\tLOC\n")

    body = _HEADER + b"".join(blocks)
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=0, compresslevel=6) as gz:
        gz.write(body)
    return body, per_rows


def _read_shard_per_rows(shard: Path) -> list[bytes]:
    """Return the non-header PER rows in a shard, as raw byte lines."""
    with gzip.open(shard, "rb") as fh:
        header = fh.readline()
        assert header == _HEADER, f"unexpected header in {shard}: {header!r}"
        return [line for line in fh if line]


def _wid_of(row: bytes) -> str:
    return row.split(b"\t", 1)[0].decode("ascii")


def _run_shard_script(fixture: Path, out_dir: Path, n_shards: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 — sys.executable + literal script path are trusted
        [
            sys.executable,
            str(_SHARD_SCRIPT),
            "--input",
            str(fixture),
            "--output-dir",
            str(out_dir),
            "--shards",
            str(n_shards),
        ],
        check=True,
        capture_output=True,
    )


def _decompressed_bytes(path: Path) -> bytes:
    with gzip.open(path, "rb") as fh:
        return fh.read()


def _sha256_decompressed(path: Path) -> str:
    return hashlib.sha256(_decompressed_bytes(path)).hexdigest()


def test_shard_round_trip(tmp_path: Path) -> None:
    fixture = tmp_path / "mini.tsv.gz"
    _, per_rows = _build_synthetic_fixture(fixture)
    out_dir = tmp_path / "shards"

    _run_shard_script(fixture, out_dir, n_shards=3)

    expected_names = [f"paranames_full_shard_{i:02d}.tsv.gz" for i in range(3)]
    shard_paths = [out_dir / name for name in expected_names]
    for shard in shard_paths:
        assert shard.is_file(), f"missing shard: {shard}"

    rejoined: list[bytes] = []
    for shard in shard_paths:
        rejoined.extend(_read_shard_per_rows(shard))
    assert Counter(rejoined) == Counter(per_rows)


def test_shard_wid_boundaries_respected(tmp_path: Path) -> None:
    fixture = tmp_path / "mini.tsv.gz"
    _, _per_rows = _build_synthetic_fixture(fixture)
    out_dir = tmp_path / "shards"

    _run_shard_script(fixture, out_dir, n_shards=3)

    wid_shards: dict[str, set[int]] = defaultdict(set)
    for idx in range(3):
        shard = out_dir / f"paranames_full_shard_{idx:02d}.tsv.gz"
        for row in _read_shard_per_rows(shard):
            wid_shards[_wid_of(row)].add(idx)
    for wid, shard_indices in wid_shards.items():
        assert len(shard_indices) == 1, (
            f"wid {wid!r} split across shards {sorted(shard_indices)}; the parallel "
            "ingest's sort-invariant check requires each wid to live in one shard."
        )


def test_shard_output_deterministic(tmp_path: Path) -> None:
    fixture = tmp_path / "mini.tsv.gz"
    _build_synthetic_fixture(fixture)

    out_a = tmp_path / "shards_a"
    out_b = tmp_path / "shards_b"
    _run_shard_script(fixture, out_a, n_shards=3)
    _run_shard_script(fixture, out_b, n_shards=3)

    for idx in range(3):
        name = f"paranames_full_shard_{idx:02d}.tsv.gz"
        assert _sha256_decompressed(out_a / name) == _sha256_decompressed(out_b / name), (
            f"shard {name} decompressed content is not identical across runs; "
            "this would break the bloom pipeline's byte-identity guarantee (I1)."
        )
