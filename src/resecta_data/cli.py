"""CLI entry points for the data pipeline.

At Phase 0 the CLI provides verification commands only: schema validation,
determinism check, hash-lockfile check, and asset installation. Build
subcommands land in Phase 1+.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import click

from .commands import bloom, classifier, corpus, gazetteers, install, vectors, verify
from .commands import eval as eval_commands
from .commands.bloom import _INGEST_CACHE_SUBDIR, _ingest_surnames, _paranames_full_specs
from .commands.verify import _OUT_OF_BAND_PREFIXES, _is_out_of_band, _run_rebuild_streaming
from .common.determinism import assert_hash_seed_pinned
from .common.exceptions import (
    DeterminismError,
    HashMismatchError,
    PipelineError,
    SchemaValidationError,
)
from .common.io import dump_canonical_json
from .instrumentation.bundle_size import DEFAULT_SUB_DIRS as BUNDLE_SIZE_DEFAULT_SUB_DIRS
from .instrumentation.bundle_size import build as build_bundle_size
from .instrumentation.bundle_size import build_meta as build_bundle_size_meta
from .routes import INSTALL_ROUTES, SCHEMA_ROUTES, SHRINK_GUARDED_ROUTES

# The names importers read on this module besides ``main``: the routing tables (their
# historical home) and the private helpers the tests pin, each defined in the module that
# uses it.
__all__ = [
    "INSTALL_ROUTES",
    "SCHEMA_ROUTES",
    "SHRINK_GUARDED_ROUTES",
    "_INGEST_CACHE_SUBDIR",
    "_OUT_OF_BAND_PREFIXES",
    "_ingest_surnames",
    "_is_out_of_band",
    "_paranames_full_specs",
    "_run_rebuild_streaming",
    "main",
]


DEBUG_VERBOSITY = 2
"""Verbosity level at which we emit DEBUG-level logs."""


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v, -vv).")
@click.pass_context
def main(ctx: click.Context, verbose: int) -> None:
    """Resecta DataPipeline CLI."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= DEBUG_VERBOSITY:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)


# -----------------------------------------------------------------------------
# Build subcommands (Phase 1)
# -----------------------------------------------------------------------------


@main.group("build")
def build_group() -> None:
    """Generate Phase 1+ artifacts into build/."""


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


@build_group.command("bundle-size")
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


@build_group.group("calibrate")
def build_calibrate_group() -> None:
    """Phase 3b calibration artifacts produced from Swift-side dumps."""


verify.register(main, build_group, build_calibrate_group)
install.register(main, build_group, build_calibrate_group)
vectors.register(main, build_group, build_calibrate_group)
bloom.register(main, build_group, build_calibrate_group)
gazetteers.register(main, build_group, build_calibrate_group)
corpus.register(main, build_group, build_calibrate_group)
eval_commands.register(main, build_group, build_calibrate_group)
classifier.register(main, build_group, build_calibrate_group)


# -----------------------------------------------------------------------------
# Top-level error handler
# -----------------------------------------------------------------------------


def _install_exception_handler() -> None:
    """Convert PipelineError subclasses into clean CLI exits."""
    original_excepthook = sys.excepthook

    def handler(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        if isinstance(exc, HashMismatchError | DeterminismError | SchemaValidationError):
            click.echo(f"{exc_type.__name__}: {exc}", err=True)
            sys.exit(1)
        if isinstance(exc, PipelineError):
            click.echo(f"{exc_type.__name__}: {exc}", err=True)
            sys.exit(2)
        original_excepthook(exc_type, exc, tb)  # type: ignore[arg-type]

    sys.excepthook = handler


_install_exception_handler()


if __name__ == "__main__":
    main()
