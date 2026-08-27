#!/usr/bin/env python3
"""Detect (and optionally reap) orphaned ``resecta-data`` worker processes.

Defence-in-depth against the host-OOM mode where an interrupted build can leave
``ProcessPoolExecutor`` workers reparented to the user's session reaper
(``systemd --user`` on Ubuntu desktop, PID 1 elsewhere), holding multi-GB
RSS until they finish their current task. The in-tree fix (PR_SET_PDEATHSIG
+ process-group containment in verify-determinism) prevents *new* orphans;
this script cleans up any *prior* orphans that a developer accumulated
before the fix landed.

Two modes:

- **Default (read-only):** scan ``/proc/*/comm`` for ``resecta-data`` /
  ``python -m resecta_data.cli`` processes whose immediate parent is the
  user-session subreaper AND whose age exceeds 10 minutes. Print a
  human-readable table; exit 0.
- **--reap:** send SIGTERM to each match, wait up to 5 seconds, then
  escalate to SIGKILL for survivors.

Linux-only. The script is invoked from the Makefile by ``make doctor``
(read-only) and ``make reap-orphans`` (with ``--reap``); it does not
prompt — the prompt lives in the Makefile recipe so direct invocations
stay scriptable. Pure stdlib (no psutil dependency).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# A process is treated as orphaned only if its immediate parent has been
# the session reaper for at least this many seconds. Bounds against
# false positives during a normal cold build (workers there have parent
# = the resecta-data builder, not the reaper).
_ORPHAN_MIN_AGE_SECONDS = 600

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600

# After the ")" in /proc/<pid>/stat, fields are 0-indexed from "state";
# starttime is the 22nd field overall (man 5 proc) -> index 19 here.
_STARTTIME_INDEX = 19

_CMDLINE_DISPLAY_WIDTH = 60

# Targets we recognise as in-tree workers. Anything else is left alone.
_TARGET_COMMS = frozenset({"resecta-data", "python", "python3", "python3.12"})

# Substrings inside the cmdline that confirm a Python interpreter is in
# fact running this project. Used together with _TARGET_COMMS so we do
# not pick up unrelated Python processes.
_TARGET_CMDLINE_HINTS = ("resecta_data", "resecta-data", "src/resecta_data/")


@dataclass(frozen=True)
class _OrphanRow:
    pid: int
    ppid: int
    rss_kb: int
    age_seconds: float
    comm: str
    cmdline: str


def _read_status(pid: int) -> dict[str, str] | None:
    """Read ``/proc/<pid>/status`` into a dict; return None if the pid is gone."""
    try:
        text = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(":\t")
        out[key] = value.strip()
    return out


def _read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ""
    # /proc/<pid>/cmdline uses \0 separators; collapse to spaces for display.
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _process_age_seconds(pid: int, btime: float) -> float | None:
    """Process age (seconds since fork). Reads ``/proc/<pid>/stat`` field 22."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # stat format: "pid (comm) state ppid pgrp ..." — comm may contain
    # spaces and parens, so split off the trailing portion after the
    # final ')'.
    rparen = stat.rfind(")")
    if rparen < 0:
        return None
    fields = stat[rparen + 2 :].split()
    if len(fields) <= _STARTTIME_INDEX:
        return None
    try:
        starttime_clk = int(fields[_STARTTIME_INDEX])
    except ValueError:
        return None
    clk_tck = os.sysconf("SC_CLK_TCK") or 100
    start_time = btime + starttime_clk / clk_tck
    return max(0.0, time.time() - start_time)


def _read_btime() -> float:
    """Boot time in epoch seconds, from ``/proc/stat``."""
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise RuntimeError("/proc/stat is missing 'btime'")


def _find_session_reaper_pids() -> set[int]:
    """The PIDs that are valid 'reaper' parents for orphans.

    On Ubuntu desktop the user's session reaper is ``systemd --user``;
    on hosts without a user-systemd it is PID 1 (init). Both are
    captured. We filter by current uid because we should never touch a
    process that is not ours.
    """
    reapers: set[int] = {1}
    uid = os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        status = _read_status(pid)
        if status is None:
            continue
        # Uid line is "Real Effective Saved FS" — first column is real uid.
        proc_uid = status.get("Uid", "").split()[:1]
        if not proc_uid or proc_uid[0] != str(uid):
            continue
        if status.get("Name", "") == "systemd":
            cmd = _read_cmdline(pid)
            if "--user" in cmd:
                reapers.add(pid)
    return reapers


def _is_target_process(pid: int, status: dict[str, str]) -> bool:
    comm = status.get("Name", "")
    if comm not in _TARGET_COMMS and "resecta-data" not in comm:
        return False
    if "resecta-data" in comm:
        return True
    cmdline = _read_cmdline(pid)
    return any(hint in cmdline for hint in _TARGET_CMDLINE_HINTS)


def find_orphans() -> list[_OrphanRow]:
    """Return the current list of orphan rows (no side effects).

    Heuristic: process matches the resecta-data binary or a Python
    interpreter running the project, AND its immediate parent is in the
    set of session-reaper PIDs, AND its age exceeds ``_ORPHAN_MIN_AGE_SECONDS``.
    """
    if sys.platform != "linux":
        return []
    # unreachable fires only on non-Linux mypy hosts (the early return above
    # is platform-conditional); unused-ignore covers the Linux run.
    btime = _read_btime()  # type: ignore[unreachable, unused-ignore]
    reapers = _find_session_reaper_pids()
    uid = os.getuid()
    rows: list[_OrphanRow] = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda p: p.name):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        status = _read_status(pid)
        if status is None:
            continue
        proc_uid = status.get("Uid", "").split()[:1]
        if not proc_uid or proc_uid[0] != str(uid):
            continue
        if not _is_target_process(pid, status):
            continue
        try:
            ppid = int(status.get("PPid", "0"))
        except ValueError:
            continue
        if ppid not in reapers:
            continue
        age = _process_age_seconds(pid, btime)
        if age is None or age < _ORPHAN_MIN_AGE_SECONDS:
            continue
        try:
            rss_kb = int(status.get("VmRSS", "0").split()[0])
        except (ValueError, IndexError):
            rss_kb = 0
        rows.append(
            _OrphanRow(
                pid=pid,
                ppid=ppid,
                rss_kb=rss_kb,
                age_seconds=age,
                comm=status.get("Name", ""),
                cmdline=_read_cmdline(pid),
            )
        )
    return rows


def _format_age(seconds: float) -> str:
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds:.0f}s"
    if seconds < _SECONDS_PER_HOUR:
        return f"{seconds / _SECONDS_PER_MINUTE:.0f}m"
    return f"{seconds / _SECONDS_PER_HOUR:.1f}h"


def print_orphans(rows: list[_OrphanRow]) -> None:
    """Print a human-readable table to stdout (always exit 0)."""
    if not rows:
        print(
            f"0 stale workers (parent = session reaper,"
            f" age > {_ORPHAN_MIN_AGE_SECONDS // _SECONDS_PER_MINUTE} min)"
        )
        return
    print(f"{len(rows)} stale workers detected:")
    print(f"  {'PID':>7}  {'PPID':>6}  {'RSS':>10}  {'AGE':>6}  COMM            CMDLINE")
    for row in rows:
        rss_mb = row.rss_kb / 1024
        if len(row.cmdline) <= _CMDLINE_DISPLAY_WIDTH:
            cmdline = row.cmdline
        else:
            cmdline = row.cmdline[: _CMDLINE_DISPLAY_WIDTH - 3] + "..."
        print(
            f"  {row.pid:>7}  {row.ppid:>6}  {rss_mb:>8.1f}MB  "
            f"{_format_age(row.age_seconds):>6}  {row.comm:<14}  {cmdline}"
        )
    print()
    print("To reap: make reap-orphans  (or scripts/reap_orphan_workers.py --reap)")


def reap(rows: list[_OrphanRow], grace_seconds: float = 5.0) -> int:
    """Send SIGTERM to each row, escalate to SIGKILL after ``grace_seconds``.

    Returns the number of survivors after escalation (0 on full success).
    """
    if not rows:
        return 0
    for row in rows:
        try:
            os.kill(row.pid, signal.SIGTERM)
            print(f"SIGTERM -> {row.pid}")
        except ProcessLookupError:
            print(f"already gone: {row.pid}")
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        live = [r for r in rows if _alive(r.pid)]
        if not live:
            return 0
        time.sleep(0.2)
    survivors = [r for r in rows if _alive(r.pid)]
    for row in survivors:
        try:
            os.kill(row.pid, signal.SIGKILL)
            print(f"SIGKILL -> {row.pid} (escalation)")
        except ProcessLookupError:
            print(f"already gone: {row.pid}")
    time.sleep(0.5)
    return sum(1 for r in survivors if _alive(r.pid))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect (and optionally reap) orphaned resecta-data workers."
    )
    parser.add_argument(
        "--reap",
        action="store_true",
        help="Send SIGTERM (escalating to SIGKILL after 5s) to detected orphans.",
    )
    args = parser.parse_args(argv)

    rows = find_orphans()
    print_orphans(rows)
    if args.reap and rows:
        survivors = reap(rows)
        if survivors:
            print(f"WARNING: {survivors} survivor(s) after SIGKILL escalation.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
