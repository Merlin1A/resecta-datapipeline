"""Subprocess and worker-process hygiene helpers.

Two concerns live here:

1. **Parent-death signalling.** :func:`request_parent_death_signal` uses
   ``prctl(PR_SET_PDEATHSIG)`` so a worker self-terminates when its
   immediate parent dies, instead of being reparented to ``systemd --user``
   and holding multi-GB RSS until it finishes its current task. Linux-only;
   no-op on macOS / other platforms.

2. **Pool sizing.** :func:`effective_workers` consolidates the
   ``RESECTA_BUILD_WORKERS`` env override + per-site hard cap that all three
   ``ProcessPoolExecutor`` sites in the pipeline need. Lifting it into one
   helper lets a single env var control bloom-ingest, bloom-filter, and
   corpus-generate worker counts (was: bloom-ingest only).

Together these mitigate the host-OOM mode where an interrupted build leaves
worker processes alive holding their full RSS, and a subsequent build
stacks against them until the kernel freezes.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from typing import Final

# Linux ``prctl`` operation code for "deliver this signal when my parent
# dies". See ``man 2 prctl``.
_PR_SET_PDEATHSIG: Final[int] = 1


def request_parent_death_signal(sig: int = signal.SIGTERM) -> None:
    """Ask the kernel to deliver ``sig`` to this process when its parent dies.

    Linux-only. On every other platform this is a no-op so that callers
    inside :class:`concurrent.futures.ProcessPoolExecutor` worker entry
    functions can call it unconditionally.

    The default ``signal.SIGTERM`` matches Python's default disposition
    (terminate without running ``atexit`` / ``util._exit_function``); pool
    workers hold no state worth flushing, so the lighter signal is
    sufficient and avoids the policy footgun of using ``SIGKILL`` (which
    leaves no chance to release shared semaphores).

    Failure (e.g. the syscall is unavailable in a restricted seccomp
    profile) is swallowed silently. This is best-effort defence against
    orphaned workers, not a correctness invariant — the build still works
    if the call returns ``-1``; it just leaves the OOM-prevention behaviour
    on the table.

    Args:
        sig: The signal number to register. Defaults to ``signal.SIGTERM``.

    Note:
        ``PR_SET_PDEATHSIG`` watches the *immediate* parent only. If a
        grandparent (e.g. the ``make`` invocation that spawned the
        builder) dies but the immediate Python parent survives long enough
        to start the workers, this guard does nothing. Process-group
        containment in :func:`subprocess.Popen` (start_new_session=True)
        covers the grandparent chain.
    """
    if sys.platform != "linux":
        return
    # unreachable fires only on non-Linux mypy hosts (the early return above
    # is platform-conditional); unused-ignore silences the unused-ignore
    # warning the same comment would otherwise raise on Linux.
    try:  # type: ignore[unreachable, unused-ignore]
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0)
    except OSError:
        # Swallowed by design — see the docstring. We do not log here
        # because the caller is a pool worker about to do real work; a log
        # line per worker would be noisy in the common (success) case.
        pass


def effective_workers(default: int, hard_cap: int) -> int:
    """Pick a worker count honouring ``RESECTA_BUILD_WORKERS`` and a hard cap.

    The env var lets a developer or CI run cap parallelism on memory-
    constrained hosts without editing code. The hard cap is the per-site
    constant (``_MAX_BUILD_WORKERS``) that bounds peak RSS for that
    specific pool's payload shape — e.g. 4 for the ingest pool whose
    workers return GB-sized row lists, 16 for the corpus pool whose
    workers return KB-sized doc dicts.

    Both bounds compose: the env wins when it asks for *fewer* workers
    than ``default`` would pick, and the hard cap wins regardless. The
    result is always at least 1.

    Args:
        default: The natural worker count for the call site (typically
            ``os.cpu_count()`` or a caller-supplied value).
        hard_cap: The per-site ``_MAX_BUILD_WORKERS`` constant.

    Returns:
        ``max(1, min(default, env_override or default, hard_cap))``.
    """
    n = default
    env = os.environ.get("RESECTA_BUILD_WORKERS")
    if env:
        try:
            v = int(env)
        except ValueError:
            v = 0
        if v >= 1:
            n = min(n, v)
    return max(1, min(n, hard_cap))
