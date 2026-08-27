"""Stage the reviewed negative-context gazetteer into ``build/``.

The reviewed file is committed at
``src/resecta_data/gazetteers/negative_context/reviewed/`` together with a
``negative_context.meta.json`` sidecar whose ``reviewed_version`` field is
the SHA-256 of ``build/gazetteers/negative_context_candidates.json`` as it
stood when the change that produced the reviewed file was approved.

Curated context assets change only under a written change plan approved by
the maintainer before the edit — the asset, the rows or fields, the reason,
and the regeneration and verification steps. No row-by-row review
afterwards. The sidecar drift check stays as a mechanical tripwire the same
change re-stamps: staging recomputes the live candidates hash and fails on
mismatch — a changed candidates file means the sidecar was not re-stamped by
the change that produced it. The sidecar is deliberately NOT the hash of the
reviewed file itself: that would self-verify trivially and never detect drift.

``build/gazetteers/negative_context.json`` is out-of-band (not produced by
``make build``; see common/determinism.py), so ``make clean`` destroys it.
This staging step makes the committed reviewed/ copy the durable source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import atomic_write_bytes, load_json, sha256_file

REVIEWED_DIR: Final[Path] = Path("src/resecta_data/gazetteers/negative_context/reviewed")

_REVIEWED_NAME: Final[str] = "negative_context.json"
_META_NAME: Final[str] = "negative_context.meta.json"
_CANDIDATES_REL: Final[str] = "gazetteers/negative_context_candidates.json"


def stage_reviewed(build_dir: Path, reviewed_dir: Path = REVIEWED_DIR) -> list[Path]:
    """Copy the reviewed gazetteer + meta sidecar into ``build/gazetteers/``.

    Args:
        build_dir: The build directory (holds the live candidates file and
            receives the staged copies).
        reviewed_dir: Directory holding the committed reviewed files.

    Returns:
        The staged destination paths.

    Raises:
        PipelineError: If the reviewed files are not committed yet, the
            candidates file is absent, the sidecar is malformed, or the
            live candidates hash does not match ``reviewed_version``.
    """
    reviewed = reviewed_dir / _REVIEWED_NAME
    meta = reviewed_dir / _META_NAME
    if not reviewed.is_file() or not meta.is_file():
        raise PipelineError(
            f"Reviewed negative-context files not committed yet under {reviewed_dir} "
            "(the reviewed gazetteer is staged from reviewed/, never built). Run "
            "`make gazetteers` to produce the candidates file, then commit "
            f"{_REVIEWED_NAME} plus {_META_NAME} to reviewed/ under an approved "
            "change plan."
        )

    candidates = build_dir / _CANDIDATES_REL
    if not candidates.is_file():
        raise PipelineError(
            f"{candidates} not found; run `make gazetteers` first so the live "
            "candidates hash can be checked against the reviewed_version sidecar."
        )

    meta_payload = load_json(meta)
    if not isinstance(meta_payload, dict):
        raise PipelineError(f"{meta}: sidecar root must be an object")
    reviewed_version = meta_payload.get("reviewed_version")
    if not isinstance(reviewed_version, str) or not reviewed_version:
        raise PipelineError(
            f"{meta}: missing or non-string 'reviewed_version' field; expected the "
            "SHA-256 of the candidates file the change was based on."
        )

    live = sha256_file(candidates)
    if live != reviewed_version:
        raise PipelineError(
            f"Candidates file changed since the sidecar was stamped: live sha256 {live} "
            f"!= reviewed_version {reviewed_version} ({meta}). Re-stamp the sidecar "
            "under an approved change plan before staging."
        )

    dest_dir = build_dir / "gazetteers"
    staged: list[Path] = []
    for src in (reviewed, meta):
        dest = dest_dir / src.name
        atomic_write_bytes(dest, src.read_bytes())
        staged.append(dest)
    return staged
