"""Verification and lockfile commands: validate-schemas, verify-determinism, verify-hashes,
regenerate-lockfile."""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Any

import click

from resecta_data.common.determinism import (
    OUT_OF_BAND_PREFIXES,
    assert_hash_seed_pinned,
    diff_hash_maps,
    is_out_of_band,
    snapshot_build_dir,
)
from resecta_data.common.exceptions import DeterminismError, SchemaValidationError
from resecta_data.common.io import (
    iter_build_artifacts,
    read_hash_lockfile,
    sha256_file,
    write_hash_lockfile,
)
from resecta_data.common.schema import validate_file
from resecta_data.routes import SCHEMA_ROUTES

# -----------------------------------------------------------------------------
# Schema validation
# -----------------------------------------------------------------------------


@click.command("validate-schemas")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing generated artifacts.",
)
@click.option(
    "--schemas-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing *.schema.json files.",
)
def validate_schemas_cmd(build_dir: Path, schemas_dir: Path) -> None:
    """Validate every build artifact against its routed schema.

    Artifacts without a routing entry in SCHEMA_ROUTES are skipped with a
    warning. Binary artifacts (e.g., .bloom) are always skipped.
    """
    artifacts = iter_build_artifacts(build_dir)
    if not artifacts:
        click.echo("No artifacts to validate. [Phase 0: expected]")
        return

    failures: list[str] = []
    validated = 0
    skipped: list[str] = []

    for path in artifacts:
        rel = path.relative_to(build_dir).as_posix()
        schema_name = _route_schema(rel)
        if schema_name is None:
            skipped.append(rel)
            continue
        try:
            validate_file(path, schemas_dir, schema_name)
            validated += 1
        except SchemaValidationError as exc:
            failures.append(f"{rel}:\n{exc}")

    click.echo(f"Validated: {validated}")
    if skipped:
        click.echo(f"Skipped (no schema route): {len(skipped)}")
        for s in skipped:
            click.echo(f"  - {s}")

    if failures:
        click.echo("", err=True)
        for f in failures:
            click.echo(f, err=True)
        sys.exit(1)


def _route_schema(relative_path: str) -> str | None:
    """Return the schema name for a given artifact path, or None if not routed.

    Routing is currently exact-match by relative path. Phase 0 has no routes.
    """
    return SCHEMA_ROUTES.get(relative_path)


# -----------------------------------------------------------------------------
# Determinism check
# -----------------------------------------------------------------------------


# The out-of-band prefix list and predicate live in common/determinism.py so
# the bundle-size probe shares them (N7 fix). Aliased here because the CLI is
# the historical home — doctor/regen tooling references cli._is_out_of_band.
_OUT_OF_BAND_PREFIXES = OUT_OF_BAND_PREFIXES
_is_out_of_band = is_out_of_band


# Number of stderr lines retained in memory for failure reporting. The
# rebuild emits tens of MB of output; we only want the tail when the
# rebuild fails so callers see the actionable error without holding the
# full stream.
_STDERR_TAIL_LINES = 200

# Grace period (seconds) between the first SIGTERM to the rebuild's
# process group and the SIGKILL escalation. Long enough for `make` to
# unwind its sub-recipes; short enough that an unresponsive child does
# not delay the parent's exit indefinitely.
_REBUILD_TERM_GRACE_SECONDS = 5.0


def _run_rebuild_streaming(full_command: str) -> tuple[int, deque[str]]:
    """Run the rebuild subprocess with process-group containment + streaming.

    Three properties differ from the previous ``subprocess.run`` invocation:

    1. ``start_new_session=True`` — the child shell becomes the leader of a
       new process group. SIGINT/SIGTERM handlers in the parent call
       ``os.killpg`` against that group so the entire subtree (bash → make
       → builder → pool workers) is reaped together. Without this, an
       interrupted determinism-check leaves orphaned ProcessPoolExecutor
       workers reparented to ``systemd --user``, holding multi-GB RSS
       until they finish — the host-OOM mechanism this guards against.

    2. **Streaming output** — instead of buffering the entire rebuild
       stdout/stderr into the parent's RAM via ``capture_output=True``,
       two daemon threads pump the child's pipes to the parent's
       ``sys.stdout`` / ``sys.stderr`` line by line. This makes long-
       running rebuilds visible in real time and bounds parent-side
       memory use to a small ring buffer.

    3. **Bounded failure context** — the stderr pump retains the final
       ``_STDERR_TAIL_LINES`` lines in a ``deque(maxlen=...)``. On a
       non-zero exit the caller prints just that tail, which is what an
       operator actually wants to see (a 12-minute build's stderr is
       mostly progress noise; the actionable error is at the end).

    Returns the rebuild's exit code and the stderr tail. Re-raises any
    exception from the subprocess pump unmodified after best-effort
    cleanup of the child group.
    """
    stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

    # S602 suppressed: full_command is operator-controlled via --rebuild-command,
    # not user-supplied from untrusted input. Shell execution is intentional so
    # operators can pass shell constructs (e.g. `make build BUILD_DIR=/tmp/foo`).
    proc = subprocess.Popen(  # noqa: S602
        full_command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    pgid = proc.pid

    def _pump(pipe: Any, sink: Any, tail: deque[str] | None) -> None:
        try:
            for line in iter(pipe.readline, ""):
                sink.write(line)
                sink.flush()
                if tail is not None:
                    tail.append(line)
        finally:
            with contextlib.suppress(Exception):
                pipe.close()

    stdout_thread = threading.Thread(
        target=_pump, args=(proc.stdout, sys.stdout, None), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_pump, args=(proc.stderr, sys.stderr, stderr_tail), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    def _terminate_group() -> None:
        # Best-effort: SIGTERM the whole group, give it a grace window,
        # then SIGKILL. Suppress ProcessLookupError because the group may
        # already be gone by the time we reach this branch on a clean exit.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=_REBUILD_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_REBUILD_TERM_GRACE_SECONDS)

    def _signal_handler(signum: int, _frame: object) -> None:
        # Make the interruption visible to the operator before the parent
        # exits so they understand why the rebuild stopped.
        click.echo(
            f"\nverify-determinism: received signal {signum}; terminating rebuild "
            f"process group {pgid}.",
            err=True,
        )
        _terminate_group()
        # Re-raise the signal with the default handler so the parent exits
        # with the conventional 128+signum status. Restoring SIG_DFL and
        # re-sending the same signal is the canonical CPython idiom.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    prev_sigint = signal.signal(signal.SIGINT, _signal_handler)
    prev_sigterm = signal.signal(signal.SIGTERM, _signal_handler)
    try:
        returncode = proc.wait()
        # Drain pumps — the pipes are closed by the child exiting; both
        # threads should observe EOF and finish quickly.
        stdout_thread.join(timeout=_REBUILD_TERM_GRACE_SECONDS)
        stderr_thread.join(timeout=_REBUILD_TERM_GRACE_SECONDS)
    except BaseException:
        # Anything from KeyboardInterrupt through unexpected exceptions
        # must still reap the child group before propagating.
        _terminate_group()
        raise
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        # Belt-and-suspenders: if the child somehow survived its own wait()
        # (e.g. the wait raised before completion), reap the group now.
        if proc.poll() is None:
            _terminate_group()

    return returncode, stderr_tail


@click.command("verify-determinism")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing the first build's artifacts.",
)
@click.option(
    "--rebuild-command",
    default="make build",
    show_default=True,
    help="Command to invoke for the second build.",
)
@click.option(
    "--rebuild-out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "When set, rebuild into this directory instead of wiping --build-dir. "
        "BUILD_DIR=<path> is appended to the rebuild command so make builds "
        "into the alternate location, and the original --build-dir is "
        "read-only across the run. The Makefile's verify recipe uses this "
        "to run determinism-check in parallel with verifiers that read "
        "build/."
    ),
)
def verify_determinism_cmd(
    build_dir: Path,
    rebuild_command: str,
    rebuild_out_dir: Path | None,
) -> None:
    """Rebuild and confirm bit-identical output.

    Two modes:

    1. **In-place** (``--rebuild-out-dir`` unset): snapshot ``--build-dir``,
       wipe it, run the rebuild in place, diff. Out-of-band artifacts
       (Swift-produced calibration dumps, the reviewed gazetteer) are
       restored across the wipe.

    2. **Side-by-side** (``--rebuild-out-dir`` set): snapshot ``--build-dir``,
       run the rebuild into ``--rebuild-out-dir`` (with
       ``BUILD_DIR=<path>`` appended to the rebuild command so ``make``
       writes there), diff snapshot vs the alternate directory. The
       original ``--build-dir`` is never modified, so out-of-band
       artifacts survive naturally and the determinism step can run in
       parallel with verifiers that read ``--build-dir``.

    Out-of-band artifacts are excluded from the comparison because the
    rebuild command does not regenerate them.

    """
    try:
        assert_hash_seed_pinned()
    except DeterminismError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)

    if rebuild_out_dir is not None and rebuild_out_dir.resolve() == build_dir.resolve():
        click.echo(
            "ERROR: --rebuild-out-dir resolves to the same path as --build-dir; "
            "they must differ for the side-by-side rebuild to make sense.",
            err=True,
        )
        sys.exit(2)

    artifacts = iter_build_artifacts(build_dir)
    if not artifacts:
        click.echo("No artifacts to check. [Phase 0: expected]")
        return

    with tempfile.TemporaryDirectory(prefix="resecta-determinism-") as tmp:
        snapshot_root = Path(tmp) / "snapshot"
        click.echo(f"Snapshotting {build_dir} -> {snapshot_root}")
        first_hashes = snapshot_build_dir(build_dir, snapshot_root)

        if rebuild_out_dir is None:
            # Mode 1: in-place wipe-and-rebuild.
            click.echo(f"Clearing {build_dir}")
            shutil.rmtree(build_dir)
            build_dir.mkdir(parents=True)

            # Restore out-of-band artifacts that the rebuild command does not
            # regenerate (Swift-produced calibration dumps, calibrated outputs,
            # the reviewed gazetteer). Preserving them here keeps the user's
            # calibration and staged files intact across the verify pipeline.
            preserved = 0
            for rel in first_hashes:
                if not _is_out_of_band(rel):
                    continue
                src = snapshot_root / rel
                dst = build_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                preserved += 1
            if preserved:
                click.echo(f"Preserved {preserved} out-of-band artifact(s) across rebuild")

            full_command = rebuild_command
            second_dir = build_dir
        else:
            # Mode 2: side-by-side rebuild. Original build_dir is read-only;
            # out-of-band artifacts in build_dir are not touched and so do
            # not need to be restored. Cmdline arg overrides Makefile := so
            # the rebuild writes to rebuild_out_dir.
            rebuild_out_dir.mkdir(parents=True, exist_ok=True)
            full_command = f"{rebuild_command} BUILD_DIR={shlex.quote(str(rebuild_out_dir))}"
            second_dir = rebuild_out_dir

        click.echo(f"Rebuilding: {full_command}")
        returncode, tail_stderr = _run_rebuild_streaming(full_command)
        if returncode != 0:
            click.echo(
                "Rebuild failed (last 200 stderr lines):\n" + "".join(tail_stderr),
                err=True,
            )
            sys.exit(returncode)

        second_hashes = {
            p.relative_to(second_dir).as_posix(): sha256_file(p)
            for p in iter_build_artifacts(second_dir)
        }

    # Exclude out-of-band artifacts (Swift-produced dumps, calibration
    # outputs, the reviewed gazetteer) from the determinism comparison.
    first_hashes = {k: v for k, v in first_hashes.items() if not _is_out_of_band(k)}
    second_hashes = {k: v for k, v in second_hashes.items() if not _is_out_of_band(k)}

    differences = diff_hash_maps(first_hashes, second_hashes)
    if differences:
        click.echo("Determinism check FAILED:", err=True)
        for d in differences:
            click.echo(f"  {d}", err=True)
        sys.exit(1)

    click.echo(f"Determinism check PASSED ({len(first_hashes)} artifacts)")


# -----------------------------------------------------------------------------
# Hash lockfile
# -----------------------------------------------------------------------------


@click.command("verify-hashes")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--lockfile",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--built-only",
    is_flag=True,
    default=False,
    help=(
        "Verify only artifacts present in the build tree; lockfile entries "
        "with no built file are reported as skipped instead of failing. "
        "Hash mismatches and unlisted artifacts still fail."
    ),
)
def verify_hashes_cmd(build_dir: Path, lockfile: Path, built_only: bool) -> None:
    """Verify every build artifact matches its lockfile entry.

    Fails if:
      - A lockfile entry has no corresponding file (unless --built-only).
      - A file has no lockfile entry.
      - A file's hash does not match the lockfile.

    With --built-only, lockfile entries absent from the build tree are
    listed as skipped and do not fail the check — for hosts that build a
    subset of the artifacts (a fresh checkout without the large fetched
    sources) while still proving every built byte matches the lock.
    """
    expected = read_hash_lockfile(lockfile)
    actual = {
        p.relative_to(build_dir).as_posix(): sha256_file(p) for p in iter_build_artifacts(build_dir)
    }
    # sign-manifest products are out-of-band relative to `make build`
    # (the rebuild scope hash-check runs against). They're not in the lockfile
    # by design; the iOS verifier is the source of truth for their integrity.
    actual = {k: v for k, v in actual.items() if not _is_out_of_band(k)}

    if not expected and not actual:
        click.echo("No artifacts and empty lockfile. [Phase 0: expected]")
        return

    problems: list[str] = []
    skipped: list[str] = []
    for rel, digest in sorted(expected.items()):
        if rel not in actual:
            if built_only:
                skipped.append(rel)
            else:
                problems.append(f"In lockfile but missing from build: {rel}")
        elif actual[rel] != digest:
            problems.append(
                f"Hash mismatch for {rel}: expected {digest[:12]}..., got {actual[rel][:12]}..."
            )
    for rel in sorted(set(actual) - set(expected)):
        problems.append(f"In build but missing from lockfile: {rel}")

    if problems:
        click.echo("Hash check FAILED:", err=True)
        for p in problems:
            click.echo(f"  {p}", err=True)
        click.echo("", err=True)
        click.echo("Investigate before updating the lockfile; see CONTRIBUTING.md.", err=True)
        sys.exit(1)

    if skipped:
        click.echo(f"Skipped {len(skipped)} lockfile entries absent from the build tree:")
        for rel in skipped:
            click.echo(f"  {rel}")
    click.echo(f"Hash check PASSED ({len(actual)} artifacts)")


@click.command("regenerate-lockfile")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--lockfile",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt. Use only when you understand the diff.",
)
def regenerate_lockfile_cmd(build_dir: Path, lockfile: Path, yes: bool) -> None:
    """Write a fresh asset_hashes.lock matching the current build.

    Use this only when you've verified that the artifact changes are intentional.
    Blind regeneration defeats the purpose of the lockfile.
    """
    if not yes:
        click.confirm(
            "This overwrites asset_hashes.lock with current build hashes. "
            "Have you reviewed the intended changes?",
            abort=True,
        )
    entries = {
        p.relative_to(build_dir).as_posix(): sha256_file(p) for p in iter_build_artifacts(build_dir)
    }
    # Exclude sign-manifest output (out-of-band relative to `make build`).
    entries = {k: v for k, v in entries.items() if not _is_out_of_band(k)}
    write_hash_lockfile(lockfile, entries)
    click.echo(f"Wrote {len(entries)} entries to {lockfile}")


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    main.add_command(validate_schemas_cmd)
    main.add_command(verify_determinism_cmd)
    main.add_command(verify_hashes_cmd)
    main.add_command(regenerate_lockfile_cmd)
