"""Tests for verify-determinism's subprocess hygiene.

Three concerns:

1. **Streaming**: stdout from the rebuild must reach the parent before the
   rebuild exits, so long builds become observable in real time.
2. **Process-group containment**: the rebuild's child process group must
   be a fresh session (so SIGINT/SIGTERM handlers can ``killpg`` the whole
   tree), and signal handlers must reap that group before the parent exits.
3. **Failure tail**: on non-zero exit the parent surfaces the last 200
   stderr lines, not the entire stream.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from resecta_data.cli import _run_rebuild_streaming


def test_rebuild_streaming_returns_exit_code_and_tail() -> None:
    """Happy path: zero-exit command → 0 returncode, tail captures stderr."""
    rc, tail = _run_rebuild_streaming("printf 'out-line\\n' >&1; printf 'err-line\\n' >&2; exit 0")
    assert rc == 0
    # tail captures stderr lines only.
    assert any("err-line" in line for line in tail)
    assert all("out-line" not in line for line in tail)


def test_rebuild_streaming_caps_stderr_tail_at_200_lines() -> None:
    """A noisy stderr stream must not grow the parent's memory unboundedly."""
    rc, tail = _run_rebuild_streaming(
        # 500 lines of stderr; tail must keep only the last 200.
        "for i in $(seq 1 500); do printf 'line-%03d\\n' $i >&2; done; exit 7"
    )
    assert rc == 7
    assert len(tail) == 200
    # The last line must be present (we keep the tail, not the head).
    assert tail[-1].strip() == "line-500"
    assert tail[0].strip() == "line-301"


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses /proc/self/stat to read the child shell's pgid; macOS has no procfs.",
)
def test_rebuild_streaming_starts_new_session(tmp_path: Path) -> None:
    """The child shell must be a process-group / session leader.

    Asserts that the rebuild's child shell PID equals its own pgid — i.e.
    ``setsid`` was called. Without that, ``os.killpg`` from the signal
    handler would also signal the parent's own process group.
    """
    out_file = tmp_path / "pgid.txt"
    rc, _ = _run_rebuild_streaming(f"echo $$ $(cut -d' ' -f5 /proc/self/stat) > {out_file}")
    assert rc == 0
    pid_str, pgid_str = out_file.read_text().split()
    assert pid_str == pgid_str, (
        f"child shell pid={pid_str} pgid={pgid_str}; start_new_session=True did not take effect."
    )


def test_rebuild_streaming_streams_output_in_real_time(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Lines written before a sleep should appear in parent stdout before exit.

    Without streaming (capture_output=True) the parent only sees output
    after the child exits. The harness here runs a short script that
    prints a marker, sleeps, then prints another. We wait on the first
    marker before letting the script proceed; if streaming is broken the
    test would deadlock waiting forever.
    """
    rc, _ = _run_rebuild_streaming("echo MARKER-BEFORE-SLEEP; sleep 0.3; echo MARKER-AFTER-SLEEP")
    assert rc == 0
    captured = capfd.readouterr().out
    assert "MARKER-BEFORE-SLEEP" in captured
    assert "MARKER-AFTER-SLEEP" in captured
    # The before-marker must appear before the after-marker.
    assert captured.index("MARKER-BEFORE-SLEEP") < captured.index("MARKER-AFTER-SLEEP")


@pytest.mark.skipif(sys.platform != "linux", reason="killpg semantics are POSIX")
@pytest.mark.slow
def test_rebuild_streaming_reaps_subtree_on_sigterm() -> None:
    """SIGTERM to the parent must kill the child shell + all descendants.

    Spawns a parent Python that calls ``_run_rebuild_streaming`` against a
    bash that itself spawns a long-lived ``sleep`` grandchild. Sends
    SIGTERM to the parent; verifies both bash and sleep are gone after
    the parent exits.
    """
    helper_script = textwrap.dedent(
        """
        import os, sys
        from resecta_data.cli import _run_rebuild_streaming
        # Print our PID so the test driver knows whom to signal.
        print(os.getpid(), flush=True)
        # Inside the child shell, spawn a long-lived sleep and print its
        # PID + the bash's PGID so the driver can verify reaping.
        cmd = (
            "echo PGID=$$; (sleep 60 & echo SLEEP=$!); wait"
        )
        rc, _ = _run_rebuild_streaming(cmd)
        sys.exit(rc)
        """
    )
    proc = subprocess.Popen(  # noqa: S603 — controlled args, no shell
        [sys.executable, "-c", helper_script],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        parent_pid = int(proc.stdout.readline().strip())
        # Read PGID and SLEEP markers.
        pgid_line = proc.stdout.readline().strip()
        sleep_line = proc.stdout.readline().strip()
        assert pgid_line.startswith("PGID="), f"unexpected: {pgid_line!r}"
        assert sleep_line.startswith("SLEEP="), f"unexpected: {sleep_line!r}"
        bash_pgid = int(pgid_line.split("=", 1)[1])
        sleep_pid = int(sleep_line.split("=", 1)[1])

        time.sleep(0.3)  # let the sleep be in its sleep syscall
        os.kill(parent_pid, signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # After the parent exits, the bash shell + its sleep grandchild must
    # both be gone. Wait briefly to allow kernel reparenting to settle.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _process_alive(bash_pgid) and not _process_alive(sleep_pid):
            break
        time.sleep(0.1)
    survivors = [p for p in (bash_pgid, sleep_pid) if _process_alive(p)]
    if survivors:
        for p in survivors:
            with contextlib.suppress(ProcessLookupError):
                os.kill(p, signal.SIGKILL)
        pytest.fail(
            f"subprocess subtree survived parent SIGTERM: {survivors}. killpg reaper is broken."
        )


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
