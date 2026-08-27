"""Deterministic, atomic I/O helpers.

Every artifact written under ``build/`` goes through this module. Direct calls
to ``open(...).write(...)`` or ``json.dump(...)`` in builder code are a defect.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Final

from .exceptions import PipelineError

logger = logging.getLogger(__name__)

# Canonical JSON serialization parameters.
# These match what the Swift side expects via JSONDecoder with default settings.
_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ": ")
_JSON_INDENT: Final[int] = 2

# Hash lockfile format constants.
_LOCKFILE_FIELDS: Final[int] = 2  # "<path>  <sha256>" → 2 fields after split("  ", 1)
_SHA256_HEX_LENGTH: Final[int] = 64
_SHA256_HEX_CHARS: Final[frozenset[str]] = frozenset("0123456789abcdef")

# Exclusion surface shared by every walker that enumerates build/ artifacts
# (iter_build_artifacts below, and instrumentation/bundle_size.py's size
# probe). Module-level so consumers import ONE definition instead of carrying
# private copies that drift — a stale private copy in bundle_size is exactly
# how the manifest-signing products leaked into locked bytes (a review finding
# N7). tests/test_bundle_size.py pins the sharing.
EXCLUDED_ARTIFACT_NAMES: Final[frozenset[str]] = frozenset(
    {".stamp", ".gitkeep", "_timings.jsonl", "bundle_size.meta.json"}
)
EXCLUDED_ARTIFACT_DIRS: Final[frozenset[str]] = frozenset(
    {".stamps", "diagnostics", "_ingest_cache"}
)


def dump_canonical_json(payload: Any, path: Path) -> None:
    """Write ``payload`` to ``path`` as canonical JSON.

    Canonical form: sorted keys, indent=2, ensure_ascii=False, trailing newline,
    LF line endings. Write is atomic via temp-file-then-replace.

    Args:
        payload: Any JSON-serializable value. Typically a dict.
        path: Destination path. Parent directories are created if missing.

    Raises:
        PipelineError: If ``payload`` is not JSON-serializable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            indent=_JSON_INDENT,
            separators=_JSON_SEPARATORS,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"Payload is not JSON-serializable for {path}: {exc}") from exc

    atomic_write_bytes(path, encoded.encode("utf-8") + b"\n")


def load_json(path: Path) -> Any:
    """Load a JSON file. Strict UTF-8, no BOM tolerance.

    Args:
        path: File to read.

    Returns:
        Parsed JSON value.

    Raises:
        PipelineError: If the file does not exist or cannot be parsed.
    """
    if not path.is_file():
        raise PipelineError(f"JSON file not found: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Invalid JSON in {path}: {exc}") from exc


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path``.

    Writes to a temp file in the same directory, fsyncs, then renames. This
    guarantees that a crash mid-write leaves either the old file or the new
    file, never a partial file — critical for reproducible builds.

    Args:
        path: Destination path. Parent must exist.
        data: Bytes to write.

    Raises:
        PipelineError: If the write fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd: int | None = None
    tmp_path: Path | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(tmp_fd, "wb") as fh:
            tmp_fd = None  # now owned by fh
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
        tmp_path = None
    except OSError as exc:
        raise PipelineError(f"Atomic write failed for {path}: {exc}") from exc
    finally:
        if tmp_fd is not None:
            with contextlib.suppress(OSError):
                os.close(tmp_fd)
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of ``path``'s contents.

    Args:
        path: File to hash. Must exist and be readable.

    Returns:
        64-character lowercase hex string.

    Raises:
        PipelineError: If the file cannot be read.
    """
    if not path.is_file():
        raise PipelineError(f"Cannot hash non-file: {path}")
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                hasher.update(chunk)
    except OSError as exc:
        raise PipelineError(f"Failed to hash {path}: {exc}") from exc
    return hasher.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def read_hash_lockfile(path: Path) -> dict[str, str]:
    """Parse ``asset_hashes.lock``.

    Format: one ``<relative_path>  <sha256>`` per line, hash-separated by two
    spaces. Blank lines and lines starting with ``#`` are ignored. The
    relative path is relative to ``build/``.

    Args:
        path: Lockfile path.

    Returns:
        Dict mapping relative path to hex SHA-256.

    Raises:
        PipelineError: If a line is malformed.
    """
    if not path.is_file():
        return {}
    entries: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("  ", 1)
            if len(parts) != _LOCKFILE_FIELDS:
                raise PipelineError(f"{path}:{lineno}: expected '<path>  <sha256>', got {line!r}")
            rel_path, digest = parts[0].strip(), parts[1].strip()
            if len(digest) != _SHA256_HEX_LENGTH or not all(c in _SHA256_HEX_CHARS for c in digest):
                raise PipelineError(f"{path}:{lineno}: invalid sha256 for {rel_path!r}: {digest!r}")
            entries[rel_path] = digest
    return entries


def write_hash_lockfile(path: Path, entries: dict[str, str]) -> None:
    """Write ``entries`` to ``path`` as a sorted hash lockfile.

    See :func:`read_hash_lockfile` for the format.
    """
    lines = [
        "# asset_hashes.lock — generated by resecta-data.",
        "# Entries are relative to build/.",
        "# An unexpected change usually signals a determinism defect — investigate first.",
        "",
    ]
    for rel_path in sorted(entries):
        lines.append(f"{rel_path}  {entries[rel_path]}")
    lines.append("")
    atomic_write_bytes(path, "\n".join(lines).encode("utf-8"))


def iter_build_artifacts(build_dir: Path) -> list[Path]:
    """Return a sorted list of every file under ``build_dir``, excluding stamps.

    Used by the hash/schema/determinism verifiers to iterate over artifacts in
    a stable order. Excludes:

    - Files named ``.stamp`` or ``.gitkeep`` (per-directory markers).
    - Anything under a ``.stamps/`` directory (sentinel-file targets used by
      the Makefile to skip warm builds; not shipping artifacts).
    - Anything under a ``diagnostics/`` directory (one-off analytical
      artifacts not produced by ``make build`` and therefore not reproducible
      by the determinism rebuild-and-diff sweep).
    - Anything under a ``_ingest_cache/`` directory (bloom-ingest parse
      caches, ~428 MB each in a hydrated tree — internal memoization, never
      shipped). Excluding them keeps hash-check / schema-check / the
      determinism snapshot from hashing and copying ~817 MB of cache, and
      keeps the lockfile honest. Parse-level determinism is still verified:
      the verify-determinism rebuild runs in a fresh build dir whose cache
      starts empty, forcing a full re-parse (guarded by
      tests/test_determinism_cache_isolation.py — do not share the warm
      cache into that rebuild).
    - ``_timings.jsonl`` (profiling sidecar written by ``make time-build``
      — wall-time floats change every invocation, so it is not part of the
      deterministic artifact set).
    - ``bundle_size.meta.json`` (the bundle-size probe's build-input metadata sidecar — carries
      the git HEAD short SHA which drifts every commit; this is the
      build-metadata sidecar pattern that breaks the regen-lock feedback
      loop).
    """
    if not build_dir.is_dir():
        return []
    return sorted(
        p
        for p in build_dir.rglob("*")
        if p.is_file()
        and p.name not in EXCLUDED_ARTIFACT_NAMES
        and EXCLUDED_ARTIFACT_DIRS.isdisjoint(p.relative_to(build_dir).parts)
    )
