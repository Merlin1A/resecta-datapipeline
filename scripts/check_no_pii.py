#!/usr/bin/env python3
"""Pre-public PII guard for the datapipeline repo.

Fails if a personal-provider email address (protonmail, gmail, ...) appears in
tracked source/docs/config text — those must never ship in the public repo.
Project contacts (``@resecta.app``), GitHub noreply, and example/placeholder
addresses are allowed. Any other non-allowlisted address is reported as a
warning (test fixtures legitimately contain email-shaped strings), never a
failure.

Run from the repo root: ``python3 scripts/check_no_pii.py``.
Exit 0 = no personal-provider emails; exit 1 = at least one found.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PERSONAL_DOMAINS = frozenset(
    {
        "protonmail.com", "proton.me", "pm.me", "gmail.com", "googlemail.com",
        "icloud.com", "me.com", "mac.com", "outlook.com", "hotmail.com",
        "live.com", "yahoo.com", "ymail.com", "aol.com", "fastmail.com",
        "gmx.com", "zoho.com",
    }
)
_ALLOW_SUFFIX = ("@resecta.app", "@users.noreply.github.com")
_ALLOW_DOMAINS = frozenset({"example.com", "example.org", "example.net", "domain.tld"})
_SCAN_GLOBS = (
    "*.py", "*.sh", "*.bash", "*.md", "*.txt", "*.toml",
    "*.cfg", "*.ini", "*.yml", "*.yaml", "Makefile", "GNUmakefile",
)


def _tracked_files() -> list[str]:
    result = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--", *_SCAN_GLOBS],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in result.stdout.split("\0") if p]


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    for rel in _tracked_files():
        try:
            text = Path(rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _EMAIL.finditer(line):
                addr = match.group(0)
                low = addr.lower()
                if low.endswith(_ALLOW_SUFFIX):
                    continue
                domain = low.rsplit("@", 1)[-1]
                if domain in _ALLOW_DOMAINS:
                    continue
                entry = f"{rel}:{lineno}: {addr}"
                if domain in _PERSONAL_DOMAINS:
                    failures.append(entry)
                else:
                    warnings.append(entry)

    for warn in sorted(set(warnings)):
        sys.stderr.write(f"PII guard WARN (review): {warn}\n")
    if failures:
        sys.stderr.write("PII guard FAILED — personal-provider email(s) in tracked files:\n")
        for fail in sorted(set(failures)):
            sys.stderr.write(f"  {fail}\n")
        return 1
    sys.stdout.write("PII guard: clean (no personal-provider emails in tracked text).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
