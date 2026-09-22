#!/usr/bin/env python3
"""Planning-identifier gate over the shipped Python tree.

Shipped source, docstrings and emitted strings describe mechanisms; they never
cite the private planning registers that scheduled the work. This gate scans
every ``.py`` file under ``src/`` and ``scripts/`` (tests excluded) for the
register-identifier shape those registers use (a short upper-case prefix such
as ``C12``, ``D12`` or ``M12``, a hyphen, digits -- the full prefix list is the
pattern below), subtracts the allowlist, and fails on any remaining line.

The same shape collides with public tokens -- the IRS ``W-`` form names --
which the allowlist carries as tokens. A line that must keep a match for a
reason (an emitted string a test and the hash lock both pin, for instance) is
allowlisted by path and a substring of the line, with the reason on the same
row.

Run from the repo root: ``python3 scripts/hygiene_gate.py`` (``make lint``
runs it, so every pull request does). Exit 0 = no unlisted hit; exit 1 = at
least one, each printed as ``path:line: TOKEN :: text``. ``--all`` prints
every raw hit, allowlist ignored, as the before/after count.

Allowlist rows (``scripts/hygiene_allowlist.txt``; ``#`` comments allowed;
anything after the row's second field is the reason):
  token <TOKEN>                     suppress every hit whose matched text is TOKEN
  text <path>:<substring>           suppress hits on lines of <path> containing <substring>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "scripts")
ALLOWLIST = REPO_ROOT / "scripts" / "hygiene_allowlist.txt"
SKIP_DIRS = frozenset({"__pycache__", "build", ".venv", "sources"})

PLANNING_IDS = re.compile(
    r"\b(UXC|RB|WU|CAT|DT|DC|SEC|GAP|KI|PB|REV|RR|ST|INV|VF|SR-[FD]|RW-F|UX-D|CR|FB|RS|RF|C12|D12|Q12|M12|L|H|F|B|S|W|D|C)-[0-9]+\b"
)


def _python_files() -> list[Path]:
    out: list[Path] = []
    for base in SCAN_DIRS:
        for path in sorted((REPO_ROOT / base).rglob("*.py")):
            if not SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts):
                out.append(path)
    return out


def _load_allowlist(path: Path) -> tuple[set[str], list[tuple[str, str]]]:
    tokens: set[str] = set()
    texts: list[tuple[str, str]] = []
    if not path.exists():
        return tokens, texts
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = raw.strip()
        if not row or row.startswith("#"):
            continue
        kind, _, rest = row.partition(" ")
        value = rest.split("  #", 1)[0].strip()
        if kind == "token":
            tokens.add(value)
        elif kind == "text":
            file_part, _, needle = value.partition(":")
            texts.append((file_part, needle))
        else:
            sys.exit(f"hygiene_gate: bad allowlist row: {raw!r}")
    return tokens, texts


def _allowed(
    rel: str, line: str, token: str, allow: tuple[set[str], list[tuple[str, str]]]
) -> bool:
    tokens, texts = allow
    if token in tokens:
        return True
    return any(rel == file_part and needle in line for file_part, needle in texts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--all", action="store_true", help="print every raw hit, allowlist ignored")
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    args = parser.parse_args()
    allow = (set[str](), []) if args.all else _load_allowlist(args.allowlist)
    hits = 0
    for path in _python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in PLANNING_IDS.finditer(line):
                if _allowed(rel, line, match.group(0), allow):
                    continue
                hits += 1
                print(f"{rel}:{lineno}: {match.group(0)} :: {line.strip()[:120]}")
    label = "raw hits" if args.all else "hits not covered by the allowlist"
    print(f"hygiene_gate: {hits} {label} in {len(SCAN_DIRS)} trees")
    return 1 if hits and not args.all else 0


if __name__ == "__main__":
    sys.exit(main())
