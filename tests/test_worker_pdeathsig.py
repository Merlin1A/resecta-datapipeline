"""Integration regression: pool workers self-terminate on parent SIGKILL.

Linux-only. Spawns a Python child that creates a ``ProcessPoolExecutor``
whose worker entry function calls ``request_parent_death_signal`` (the
real entry function, via the production pool sites). SIGKILLs the child;
asserts the worker PIDs are gone within a short window.

Without ``PR_SET_PDEATHSIG`` the workers would survive parent death, get
reparented to ``systemd --user``, and hold their full RSS until they
finish their current task — the host-OOM mechanism this test guards against.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

# The harness uses ``ProcessPoolExecutor`` directly with the production
# worker entry function ``_hash_keys_into_bitset`` from bloom/filter.py.
# That function's first statement is ``request_parent_death_signal()``,
# so this test transitively asserts the wiring.
_CHILD_SCRIPT = textwrap.dedent(
    """
    import os, time, sys
    from concurrent.futures import ProcessPoolExecutor
    from resecta_data.bloom.filter import _hash_keys_into_bitset

    if __name__ == "__main__":
        # Big m so each chunk's hashing takes long enough to outlast the
        # parent's lifetime (we send SIGKILL before any chunk finishes).
        m = 1 << 24
        keys = [f"k{i}" for i in range(200_000)]
        with ProcessPoolExecutor(max_workers=2) as pool:
            futs = [
                pool.submit(_hash_keys_into_bitset, keys, m, 10, 0)
                for _ in range(2)
            ]
            time.sleep(2.0)  # let workers fully start + register prctl
            print(os.getpid(), flush=True)
            for child in pool._processes.values():
                print(child.pid, flush=True)
            time.sleep(60)  # parent stays alive until SIGKILL
    """
)


@pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux-only")
@pytest.mark.slow
def test_pool_workers_terminate_on_parent_sigkill(tmp_path: object) -> None:
    """End-to-end: workers must be gone shortly after parent SIGKILL.

    Marked slow because spawning a child Python + a 2-worker pool +
    waiting for kernel reparenting takes ~5-10 seconds. Run via
    ``make test`` (default) or directly with ``pytest -m slow``.
    """
    with subprocess.Popen(  # noqa: S603 — controlled args, no shell
        [sys.executable, "-c", _CHILD_SCRIPT],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        try:
            parent_pid = int(proc.stdout.readline().strip())
            worker_pids = [int(proc.stdout.readline().strip()) for _ in range(2)]
        except ValueError as exc:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError(f"child failed to start: {exc}") from exc

        # SIGKILL the parent only — handlers / atexit cannot run, so the only
        # thing that saves the workers is PR_SET_PDEATHSIG.
        os.kill(parent_pid, signal.SIGKILL)
        proc.wait(timeout=5)

    # Allow up to 5s for the kernel to deliver SIGTERM and the workers to
    # exit. In practice this happens within ~100ms on this host.
    deadline = time.monotonic() + 5.0
    survivors = list(worker_pids)
    while survivors and time.monotonic() < deadline:
        survivors = [pid for pid in survivors if _process_alive(pid)]
        if not survivors:
            break
        time.sleep(0.1)

    if survivors:
        # Defensive cleanup so a regression doesn't leak workers between
        # test runs.
        for pid in survivors:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        pytest.fail(
            f"Pool workers survived parent SIGKILL: {survivors}. "
            "PR_SET_PDEATHSIG wiring is broken or absent."
        )


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists, just not signalable by us.
    return True
