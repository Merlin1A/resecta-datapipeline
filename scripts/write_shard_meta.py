#!/usr/bin/env python3
"""Emit a provenance sidecar for the ParaNames shard set.

Writes ``build/gazetteers/paranames_shards.meta.json`` recording the parent
file SHA-256, the git blob of ``scripts/shard_paranames.py`` at ``HEAD`` (the
sharding script's provenance stamp), the shard count and each shard's
decompressed SHA-256. The sidecar is a build-only artifact — never committed,
not shipped — but it is hash-locked: ``asset_hashes.lock`` pins it, and
``instrumentation/bundle_size.json`` inventories it in turn, so an edit to
``scripts/shard_paranames.py`` moves both rows and lands together with them,
re-pinned from a host that holds the corpus.

The schema is stable enough for downstream tooling to rely on the top-level
keys, but the file is regenerated every time the parent is re-sharded and is
listed in ``SOURCES.md`` as a derivation-step pointer rather than a source
row in its own right.

Usage:
    scripts/write_shard_meta.py --shard-dir PATH --parent PATH --output PATH
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SHARD_GLOB = "paranames_full_shard_*.tsv.gz"
_SHARD_SCRIPT_REL = "scripts/shard_paranames.py"


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    shard_dir: Path = args.shard_dir
    parent: Path = args.parent
    output: Path = args.output

    if not parent.is_file():
        print(f"ERROR: parent not found: {parent}", file=sys.stderr)
        return 1
    if not shard_dir.is_dir():
        print(f"ERROR: shard dir not found: {shard_dir}", file=sys.stderr)
        return 1

    shard_paths = sorted(shard_dir.glob(_SHARD_GLOB))
    if not shard_paths:
        print(f"ERROR: no shards matching {_SHARD_GLOB} under {shard_dir}", file=sys.stderr)
        return 1

    parent_sha256 = _sha256(parent)
    payload = {
        "parent_sha256": parent_sha256,
        "parent_path": str(parent),
        "script_commit": _resolve_script_commit(),
        "shard_count": len(shard_paths),
        "shards": [
            {"name": shard.name, "sha256_decompressed": _sha256_decompressed(shard)}
            for shard in shard_paths
        ],
    }

    _atomic_write_json(output, payload)
    print(
        f"Wrote {output} ({len(shard_paths)} shards, parent sha256 {parent_sha256[:16]}...)",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--shard-dir", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_decompressed(path: Path) -> str:
    """SHA-256 of gzip-decompressed body.

    The Bloom ingest downstream reads decompressed content via gzip.open(...),
    so this is the hash its determinism rests on. The raw .tsv.gz bytes are
    reproducible as well (scripts/shard_paranames.py writes each member with
    an empty FNAME field, mtime=0 and a pinned compresslevel), but the sidecar
    records the content digest so a change in gzip framing alone can never
    read as a change in what the filters were built from.
    """
    h = hashlib.sha256()
    with gzip.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_script_commit() -> str:
    """Return the git blob/commit SHA for the shard script, or ``working-tree``.

    We intentionally fall back to ``"working-tree"`` rather than erroring out
    when git is unavailable or the script has uncommitted edits, so that
    ``make paranames-shards`` keeps working during local iteration.
    """
    try:
        # Fixed argv, no shell; git deliberately resolves from PATH so the
        # script works wherever the checkout lives.
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", f"HEAD:{_SHARD_SCRIPT_REL}"],  # noqa: S607
            capture_output=True,
            check=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "working-tree"
    return result.stdout.strip() or "working-tree"


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
