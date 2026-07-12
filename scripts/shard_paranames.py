#!/usr/bin/env python3
"""Offline pre-sharding of paranames_full.tsv.gz for parallel ingest.

Splits the gzipped ParaNames dump into N shards along ``wikidata_id`` (wid)
boundaries so that each shard is independently parseable by
``parse_paranames_full`` without breaking its sort-invariant check. The
runtime ingest in ``resecta_data/cli.py`` auto-discovers shards under
``<sources>/paranames/shards/`` and fans the parse across them when present.

This is a one-off operation per upstream refresh; ``make build`` never
invokes it. Without shards the build still works — it uses the monolithic
single-worker path.

Determinism: gzip is written with ``mtime=0`` and a pinned compresslevel so
shard bytes are reproducible across runs and machines given the same input.

Usage:
    scripts/shard_paranames.py [--shards N] [--input PATH] [--output-dir PATH]

Defaults:
    --shards 8
    --input  src/resecta_data/gazetteers/sources/paranames/paranames_full.tsv.gz
    --output-dir <input parent>/shards
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

# Reproducible gzip settings. Level 6 matches the default the upstream
# ParaNames dump uses and balances compression/speed.
_GZIP_LEVEL = 6
_GZIP_MTIME = 0

# Header is the first line of the .tsv — preserved verbatim in every shard
# so each shard is a valid standalone DictReader input.
_EXPECTED_HEADER_FIELDS = {"wikidata_id", "label", "language", "type"}


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    input_path: Path = args.input
    output_dir: Path = args.output_dir
    n_shards: int = args.shards

    if not input_path.is_file():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 1
    if n_shards < 1:
        print(f"ERROR: --shards must be ≥1, got {n_shards}", file=sys.stderr)
        return 1

    print(f"Counting PER rows in {input_path} ...", file=sys.stderr)
    per_total = _count_per_rows(input_path)
    if per_total == 0:
        print("ERROR: no PER rows found in input", file=sys.stderr)
        return 1
    print(f"Found {per_total:,} PER rows; sharding into {n_shards} parts.", file=sys.stderr)

    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale_shards(output_dir)

    target_per_shard = per_total // n_shards
    shard_paths = _write_shards(
        input_path,
        output_dir,
        n_shards=n_shards,
        target_per_shard=target_per_shard,
    )

    print(file=sys.stderr)
    print("Shards written:", file=sys.stderr)
    for shard_path in shard_paths:
        digest = _sha256(shard_path)
        size_mb = shard_path.stat().st_size / (1024 * 1024)
        print(f"  {shard_path.name}  {digest[:16]}...  {size_mb:.1f} MB", file=sys.stderr)

    print(file=sys.stderr)
    print("Done. The Python ingest will pick these up automatically.", file=sys.stderr)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent.parent
    default_input = (
        repo_root / "src/resecta_data/gazetteers/sources/paranames/paranames_full.tsv.gz"
    )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--input", type=Path, default=default_input)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.output_dir is None:
        args.output_dir = args.input.parent / "shards"
    return args


def _count_per_rows(input_path: Path) -> int:
    count = 0
    for _wid, _line in _iter_per_rows(input_path):
        count += 1
    return count


def _iter_per_rows(input_path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(wikidata_id, raw_line_bytes)`` for each PER row.

    Returns raw line bytes (terminator included) so shards re-emit
    byte-for-byte what upstream wrote — no re-encoding.
    """
    with gzip.open(input_path, "rb") as fh:
        header_line = fh.readline()
        if not header_line:
            raise RuntimeError(f"{input_path}: empty file")
        fields = header_line.decode("utf-8").rstrip("\n\r").split("\t")
        missing = _EXPECTED_HEADER_FIELDS - set(fields)
        if missing:
            raise RuntimeError(
                f"{input_path}: header missing columns {sorted(missing)}; got {fields!r}"
            )
        wid_idx = fields.index("wikidata_id")
        type_idx = fields.index("type")
        for raw in fh:
            # Split on raw bytes by \t; only decode the columns we need.
            parts = raw.rstrip(b"\n\r").split(b"\t")
            if len(parts) <= max(wid_idx, type_idx):
                continue
            if parts[type_idx] != b"PER":
                continue
            wid = parts[wid_idx].decode("utf-8", errors="replace")
            if not wid:
                continue
            yield wid, raw


def _write_shards(
    input_path: Path,
    output_dir: Path,
    *,
    n_shards: int,
    target_per_shard: int,
) -> list[Path]:
    """Stream PER rows into N gzipped shards, cutting only at wid transitions.

    Each shard file starts with the original TSV header followed by a
    contiguous run of PER rows. Shard boundaries are chosen to be close to
    ``target_per_shard`` rows but never inside a ``wikidata_id`` run, so
    ``parse_paranames_full``'s sort-invariant check holds per shard.
    """
    with gzip.open(input_path, "rb") as fh:
        header_line = fh.readline()

    shard_paths: list[Path] = []
    shard_idx = 0
    rows_in_shard = 0
    current_wid: str | None = None
    current_fh: gzip.GzipFile | None = None
    current_tmp: Path | None = None

    def _open_shard(idx: int) -> tuple[gzip.GzipFile, Path]:
        final_name = f"paranames_full_shard_{idx:02d}.tsv.gz"
        final_path = output_dir / final_name
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{final_name}.", suffix=".tmp", dir=output_dir
        )
        tmp = Path(tmp_name)
        # Close the low-level fd; GzipFile manages its own file object.
        import os as _os

        _os.close(tmp_fd)
        # mtime=0 + compresslevel pinned → reproducible bytes.
        gz = gzip.GzipFile(
            filename=str(tmp),
            mode="wb",
            compresslevel=_GZIP_LEVEL,
            mtime=_GZIP_MTIME,
        )
        gz.write(header_line)
        return gz, tmp

    def _close_shard(gz: gzip.GzipFile, tmp: Path, idx: int) -> Path:
        gz.close()
        final_path = output_dir / f"paranames_full_shard_{idx:02d}.tsv.gz"
        tmp.replace(final_path)
        return final_path

    try:
        current_fh, current_tmp = _open_shard(shard_idx)
        for wid, raw_line in _iter_per_rows(input_path):
            # Cut only at wid transitions, and only when we're at or past
            # the target size and still have shards to fill.
            if (
                wid != current_wid
                and rows_in_shard >= target_per_shard
                and shard_idx + 1 < n_shards
            ):
                assert current_fh is not None and current_tmp is not None
                shard_paths.append(_close_shard(current_fh, current_tmp, shard_idx))
                shard_idx += 1
                rows_in_shard = 0
                current_fh, current_tmp = _open_shard(shard_idx)
            current_fh.write(raw_line)
            current_wid = wid
            rows_in_shard += 1

        assert current_fh is not None and current_tmp is not None
        shard_paths.append(_close_shard(current_fh, current_tmp, shard_idx))
    except Exception:
        # Clean up the in-flight temp file on failure.
        if current_fh is not None:
            with _suppress_error():
                current_fh.close()
        if current_tmp is not None and current_tmp.exists():
            with _suppress_error():
                current_tmp.unlink()
        raise

    return shard_paths


class _suppress_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return True


def _clear_stale_shards(output_dir: Path) -> None:
    for path in output_dir.glob("paranames_full_shard_*.tsv.gz"):
        path.unlink()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
