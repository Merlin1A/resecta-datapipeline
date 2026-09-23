"""Instrumentation: build bundle-size."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click

from resecta_data.common.determinism import assert_hash_seed_pinned
from resecta_data.common.io import dump_canonical_json
from resecta_data.instrumentation.bundle_size import (
    DEFAULT_SUB_DIRS as BUNDLE_SIZE_DEFAULT_SUB_DIRS,
)
from resecta_data.instrumentation.bundle_size import build as build_bundle_size
from resecta_data.instrumentation.bundle_size import build_meta as build_bundle_size_meta


def _git_head_short(repo_root: Path) -> str:
    """Return the short SHA of HEAD, or the literal 'unknown' if unavailable.

    The build-input commit -- not a wall-clock -- so it is determinism-safe.
    Failure paths (no git binary, detached state with no
    commits, non-repo) collapse to ``"unknown"`` rather than raising so the
    probe still produces a schema-valid artifact off-tree.
    """
    git_bin = shutil.which("git")
    if git_bin is None:
        return "unknown"
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [git_bin, "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            cwd=repo_root,
            text=True,
            # Bound the probe so a stuck git invocation (e.g. unresponsive
            # network-mounted repo, credential prompt on misconfigured
            # remote) does not block the CLI indefinitely.
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    sha = result.stdout.strip()
    return sha if sha else "unknown"


@click.command("bundle-size")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--sub-dir",
    "sub_dirs",
    multiple=True,
    default=BUNDLE_SIZE_DEFAULT_SUB_DIRS,
    show_default=True,
    help=(
        "Subdirectory under build/ to include. Repeat to add more; "
        "defaults to the shipping-asset scope."
    ),
)
def build_bundle_size_cmd(build_dir: Path, sub_dirs: tuple[str, ...]) -> None:
    """Build the bundle-size instrumentation probe.

    Walks the configured ``build/`` subdirectories and writes
    ``instrumentation/bundle_size.json``. Engineer-facing only; the Swift
    cold-start hook ships from the Mac side.
    """
    assert_hash_seed_pinned()

    repo_root = Path(__file__).resolve().parent.parent.parent
    git_head = _git_head_short(repo_root)
    payload = build_bundle_size(build_dir, sub_dirs=tuple(sub_dirs))
    meta_payload = build_bundle_size_meta(git_head=git_head)

    instrumentation_dir = build_dir / "instrumentation"
    dest = instrumentation_dir / "bundle_size.json"
    meta_dest = instrumentation_dir / "bundle_size.meta.json"
    dump_canonical_json(payload, dest)
    dump_canonical_json(meta_payload, meta_dest)
    click.echo(
        f"Wrote {dest} (total_build_size={payload['total_build_size']} bytes "
        f"across {len(payload['subdirectories'])} subdirectories; "
        f"git_head={git_head} → {meta_dest.name})"
    )


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_bundle_size_cmd)
