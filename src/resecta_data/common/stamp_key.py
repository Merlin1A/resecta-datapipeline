"""Content-keyed build stamps (speed plan #4).

The Makefile's sentinel stamps historically recorded only an mtime: any
prerequisite whose mtime moved — editor save, ``git checkout``, rebase —
re-fired the builder and, transitively, staled the determinism witness into
a ~40-minute rebuild even when no byte of input changed.

Each stamp now stores a *content manifest* of its own make prerequisite
closure (``$^``): one ``sha256  path`` line per tracked input, sorted by
path. A stamp recipe asks ``check`` before running its builder; on a match
the builder is skipped and the stamp is merely re-touched, on a miss the
builder runs and ``write`` records the new manifest. The determinism
witness uses the same mechanism over the 17 stamp files plus
``asset_hashes.lock``, so its key transitively covers every tracked input
of every builder.

Soundness direction: any malfunction here — unreadable stamp, missing
input, malformed manifest — reports "changed", which re-runs the builder.
The skip path fires only when every tracked input is byte-identical to the
state the work last ran against; rebuilding from byte-identical inputs is
byte-identical output by the determinism invariant, which
the determinism gate itself continues to verify on every content change.
``RESECTA_FORCE_DETERMINISM=1`` (the CI path) never consults this module.

Invoked from Makefile recipes as
``python -m resecta_data.common.stamp_key {check|write} STAMP FILE...``.
Imports are kept to stdlib so the ~17 invocations of a full re-key stay
interpreter-startup-cheap.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from .exceptions import PipelineError
from .io import sha256_file

_HEADER = "# resecta-stamp-key v1"


def compute_manifest(paths: list[str]) -> str:
    """Return the canonical content manifest for ``paths``.

    One ``<sha256>  <path>`` line per unique path, sorted by path, with a
    version header and trailing newline. Paths are recorded as given
    (make-relative), so the tracked file *list* is part of the key: adding
    or removing a prerequisite changes the manifest even if every shared
    file's content is unchanged.

    Raises:
        PipelineError: if any input file is missing or not a regular file
            (via ``sha256_file``). ``check`` translates this into "changed"
            (rebuild); ``write`` lets it propagate, because make guarantees
            prerequisites exist by the time a recipe runs — a missing file
            there is a real defect.
        OSError: if a file vanishes or turns unreadable mid-hash.
    """
    lines = [_HEADER]
    lines.extend(f"{sha256_file(Path(p))}  {p}" for p in sorted(set(paths)))
    return "\n".join(lines) + "\n"


def check(stamp: Path, paths: list[str]) -> bool:
    """Return True iff ``stamp`` holds the manifest ``paths`` compute to now.

    Any failure mode — absent stamp, legacy empty stamp, unreadable input,
    garbage content — returns False, which callers treat as "changed":
    the builder re-runs and rewrites the stamp through ``write``.
    """
    try:
        stored = stamp.read_text(encoding="utf-8")
        current = compute_manifest(paths)
    except (OSError, PipelineError):
        return False
    if stored == current:
        return True
    _explain_mismatch(stamp, stored, current)
    return False


def write(stamp: Path, paths: list[str]) -> None:
    """Atomically write the current manifest of ``paths`` into ``stamp``.

    tmp-file + ``os.replace`` so an interrupt can never leave a torn
    manifest that would wrongly satisfy a later ``check``.
    """
    data = compute_manifest(paths).encode("utf-8")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=stamp.parent, prefix=stamp.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp_name).replace(stamp)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _explain_mismatch(stamp: Path, stored: str, current: str) -> None:
    """Write the first differing manifest entry to stderr.

    When a verify unexpectedly pays the determinism rebuild, this line is
    the difference between a debuggable cause and an opaque 40-minute
    surprise. Stderr only; stdout stays clean for make's own output.
    """
    if not stored.startswith(_HEADER):
        sys.stderr.write(f"[stamp-key] {stamp}: no stored manifest (legacy or fresh stamp)\n")
        return
    stored_set = set(stored.splitlines())
    current_set = set(current.splitlines())
    changed = sorted(current_set - stored_set) + sorted(stored_set - current_set)
    if changed:
        sys.stderr.write(f"[stamp-key] {stamp}: first changed entry: {changed[0]}\n")


def main(argv: list[str]) -> int:
    """CLI entry point. Exit 0 = manifests match (check) / written (write)."""
    min_args = 2  # mode + stamp path; tracked files may legitimately be empty
    if len(argv) < min_args or argv[0] not in ("check", "write"):
        sys.stderr.write(
            "usage: python -m resecta_data.common.stamp_key {check|write} STAMP FILE...\n"
        )
        return 2
    mode, stamp, paths = argv[0], Path(argv[1]), argv[2:]
    if mode == "check":
        return 0 if check(stamp, paths) else 1
    write(stamp, paths)
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess tests
    sys.exit(main(sys.argv[1:]))
