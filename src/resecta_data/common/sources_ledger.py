"""The ``SOURCES.md`` ledger check that ``make lint`` runs.

Every row of ``SOURCES.md`` is checked for its license — ``licensing.py``'s
``ALLOWLIST``, ``GATED`` map and ``FORBIDDEN`` set, an ``AND`` expression
operand by operand — and for its file (present, SHA-256 equal), and every
git-tracked file under ``src/resecta_data/**/sources/`` is checked for a row.
The exceptions are read from the ledger's own "Ledger check" section, by path,
exactly as written: the fetch-on-demand rows (license checked, file check
skipped), the gated licenses allowed after the maintainer's review (path and
license exact) and the tracked files that have no row. A ``.sha256`` sidecar
beside a rowed or listed file is the one standing exemption.

Run from the repo root: ``python -m resecta_data.common.sources_ledger``
(``make lint`` runs it). Exit 0 = no offence; exit 1 = at least one, each
printed on its own line.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import LicenseError
from resecta_data.common.licensing import (
    ALLOWLIST,
    FORBIDDEN,
    GATED,
    SourceEntry,
    parse_sources_md,
    validate_source_file,
)

ON_DEMAND_HEADING: Final = "### Rows whose file is fetched on demand"
GATED_HEADING: Final = "### Gated licences allowed after the maintainer's review"
ROWLESS_HEADING: Final = "### Tracked files under `sources/` with no row"
_TICKS: Final = re.compile(r"`([^`]+)`")
_AND: Final = " AND "
_SIDECAR: Final = ".sha256"
_GATED_TOKENS: Final = 2  # the license, then the path


@dataclass(frozen=True, slots=True)
class LedgerAllow:
    """The three exception lists of the ledger section."""

    on_demand: frozenset[str]
    gated: frozenset[tuple[str, str]]
    rowless: frozenset[str]


def parse_allow(text: str) -> LedgerAllow:
    """Read the three lists from the ledger section's bullets (backticked tokens)."""
    section = ""
    on_demand: set[str] = set()
    gated: set[tuple[str, str]] = set()
    rowless: set[str] = set()
    for line in text.splitlines():
        if line.startswith("### "):
            section = line
            continue
        if not line.startswith("- `"):
            continue
        tokens = _TICKS.findall(line)
        if section.startswith(ON_DEMAND_HEADING):
            on_demand.add(tokens[0])
        elif section.startswith(GATED_HEADING) and len(tokens) >= _GATED_TOKENS:
            gated.add((tokens[1], tokens[0]))
        elif section.startswith(ROWLESS_HEADING):
            rowless.add(tokens[0])
    return LedgerAllow(frozenset(on_demand), frozenset(gated), frozenset(rowless))


def license_offences(entry: SourceEntry, allow: LedgerAllow) -> list[str]:
    """One line per operand of the row's License cell that the rules refuse."""
    offences: list[str] = []
    for operand in entry.license.split(_AND):
        if operand in FORBIDDEN:
            offences.append(f"{entry.relative_path}: license {operand!r} is forbidden")
        elif operand in GATED:
            if (entry.relative_path, operand) not in allow.gated:
                offences.append(
                    f"{entry.relative_path}: gated license {operand!r} is not listed as "
                    "reviewed in the ledger section of SOURCES.md"
                )
        elif operand not in ALLOWLIST:
            offences.append(
                f"{entry.relative_path}: license {operand!r} is not an allowlisted identifier"
            )
    return offences


def check(
    entries: list[SourceEntry], tracked: list[str], allow: LedgerAllow, root: Path
) -> list[str]:
    """The pure core: the rows, the tracked files and the allow lists in; the offences out."""
    offences: list[str] = []
    for entry in entries:
        offences.extend(license_offences(entry, allow))
        if entry.relative_path not in allow.on_demand:
            try:
                validate_source_file(entry, root)
            except LicenseError as err:
                offences.append(str(err))
    known = {entry.relative_path for entry in entries} | allow.rowless
    for path in tracked:
        beside = path[: -len(_SIDECAR)] if path.endswith(_SIDECAR) else None
        if path in known or beside in known:
            continue
        offences.append(
            f"{path}: tracked under sources/ with no SOURCES.md row and no listed exemption"
        )
    return offences


def tracked_source_files(root: Path) -> list[str]:
    """Every git-tracked path under ``src/resecta_data`` with a ``sources/`` segment."""
    git = shutil.which("git") or "git"
    listing = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [git, "-C", str(root), "ls-files", "--", "src/resecta_data"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [path for path in listing.splitlines() if "/sources/" in path]


def main(argv: list[str] | None = None) -> int:
    """Run the check over the repository at ``argv[0]`` (default: this checkout)."""
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[3]
    sources_md = root / "SOURCES.md"
    text = sources_md.read_text(encoding="utf-8")
    offences = check(
        parse_sources_md(sources_md), tracked_source_files(root), parse_allow(text), root
    )
    for line in offences:
        sys.stdout.write(line + "\n")
    summary = f"sources_ledger: {len(offences)} offence(s)" if offences else "sources_ledger: OK"
    sys.stdout.write(summary + "\n")
    return 1 if offences else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
