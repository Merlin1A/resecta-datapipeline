"""Build the bundle-size instrumentation probe.

Walks a configured set of ``build/`` subdirectories and emits a per-artifact
size + sha256-short summary. Engineer-facing only: no Resecta UI surface in
V1; the artifact feeds V1.1+ threshold decisions for Resecta cold-start
budgets. Two ``engine_load_ms`` / ``first_detection_ready_ms`` Swift metrics
are the companion half and ship from the Mac side.

Determinism: no wall-clock content, sorted directory
iteration. The build-input git HEAD lives in a sibling
``bundle_size.meta.json`` sidecar built by :func:`build_meta` and excluded
from the hash lock -- the body emitted by :func:`build`
is byte-stable across lockfile-regen cycles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from resecta_data.common.determinism import is_out_of_band
from resecta_data.common.io import (
    EXCLUDED_ARTIFACT_DIRS,
    EXCLUDED_ARTIFACT_NAMES,
    sha256_file,
)

_MODULE_NAME = "resecta_data.instrumentation.bundle_size"
_SCHEMA_VERSION = "v1"
_SHA256_SHORT_LEN = 12

# Walks the pipeline-built `build/` subdirectories. `calibration/` is
# excluded — it holds Swift-produced dumps (a curated surface) that
# are not built by this pipeline and don't ship into the
# app, so their bytes don't belong in the cold-start bundle-size budget.
# Their presence on disk also breaks `make determinism-check`, which
# rebuilds into a tmp dir without those dumps. Configurable at the CLI
# layer (default = this tuple) so V1.1+ extensions can add / drop dirs
# without touching the builder.
DEFAULT_SUB_DIRS: tuple[str, ...] = (
    "context",
    "gazetteers",
    "rules",
    "vectors",
)


def _walk_subdir(build_dir: Path, sub_dir: str) -> list[Path]:
    """Return a sorted list of artifact files under ``build_dir/sub_dir``.

    Returns an empty list if the subdirectory does not exist on this host
    (e.g. a fresh checkout where the relevant builder hasn't run yet).

    The walk shares ``common/io.py``'s exclusion surface
    (``EXCLUDED_ARTIFACT_NAMES`` / ``EXCLUDED_ARTIFACT_DIRS``) and the
    out-of-band predicate from ``common/determinism.py``, so the probe can
    never list bytes the verify/determinism walkers don't. A private stale
    copy of the name set here is how the signed-manifest products
    (``gazetteer_manifest.sig`` / ``manifest_public_key.pem``, dropped into
    ``gazetteers/`` by ``make sign-manifest``) got baked into locked bytes,
    making a signed canonical tree unmatchable by the unsigned determinism
    rebuild. Out-of-band files and the ingest cache are also not shipped
    bytes, so excluding them makes ``total_build_size`` the honest
    cold-start budget figure.
    """
    root = build_dir / sub_dir
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.name not in EXCLUDED_ARTIFACT_NAMES
        and EXCLUDED_ARTIFACT_DIRS.isdisjoint(p.relative_to(build_dir).parts)
        and not is_out_of_band(p.relative_to(build_dir).as_posix())
    )


def build(
    build_dir: Path,
    *,
    sub_dirs: tuple[str, ...] = DEFAULT_SUB_DIRS,
) -> dict[str, Any]:
    """Walk ``sub_dirs`` under ``build_dir`` and return the probe body.

    The body excludes the build-input ``git_head`` -- that lives in the
    sibling ``bundle_size.meta.json`` sidecar produced by :func:`build_meta`
    so the body stays byte-stable across lockfile-regen cycles (the
    standard build-metadata sidecar pattern).

    Args:
        build_dir: Pipeline build root (typically ``build/``).
        sub_dirs: Subdirectories under ``build_dir`` to include in the
            probe. Defaults to :data:`DEFAULT_SUB_DIRS`.

    Returns:
        A JSON-serializable dict matching ``schemas/bundle_size.schema.json``.
    """
    subdirectories: dict[str, dict[str, Any]] = {}
    total_build_size = 0

    for sub_dir in sorted(sub_dirs):
        files_payload: list[dict[str, Any]] = []
        sub_total = 0
        for path in _walk_subdir(build_dir, sub_dir):
            size = path.stat().st_size
            files_payload.append(
                {
                    "path": path.relative_to(build_dir).as_posix(),
                    "sha256_short": sha256_file(path)[:_SHA256_SHORT_LEN],
                    "size_bytes": size,
                }
            )
            sub_total += size
        subdirectories[sub_dir] = {
            "file_count": len(files_payload),
            "files": files_payload,
            "total_bytes": sub_total,
        }
        total_build_size += sub_total

    return {
        "_meta": {
            "generated_by": _MODULE_NAME,
            "schema_version": _SCHEMA_VERSION,
        },
        "subdirectories": subdirectories,
        "total_build_size": total_build_size,
    }


def build_meta(*, git_head: str) -> dict[str, Any]:
    """Return the ``bundle_size.meta.json`` sidecar payload.

    Carries the build-input ``git_head`` (a build-metadata
    surface). Excluded from the hash lock by ``common/io.py`` so the
    sidecar's per-commit drift doesn't trigger a regen-lock feedback
    loop. Captured by the CLI via ``git rev-parse --short HEAD`` so this
    function stays test-pinnable.
    """
    return {
        "generated_by": _MODULE_NAME,
        "git_head": git_head,
        "schema_version": _SCHEMA_VERSION,
    }
