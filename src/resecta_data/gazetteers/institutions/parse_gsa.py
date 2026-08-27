"""Parser for the GSA Federal Hierarchy Crosswalk.

The crosswalk is distributed as a CSV with classic-Mac (CR-only) line
terminators and CP1252 encoding; both are preserved as-published to keep the
on-disk SHA-256 stable. We consume it through
``csv.reader`` with ``newline=''`` so the stdlib sniffs the CR terminator
correctly.

Scope is US federal agencies, bureaus, and independent establishments. We
keep rows whose root branch is one of Federal Legislative / Judicial /
Executive or the Independent Federal Agencies bucket, and whose entity type
is Agency, Ind Agency, Bureau, or GSE. International, nonprofit, and
state/local rows in the same file are dropped.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError

_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {"GSA SFP Name", "GSA SFP Entity Type", "Parent"}
)
_SOURCE_ENCODING: Final[str] = "cp1252"

_FEDERAL_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "Federal Legislative Branch",
        "Federal Judicial Branch",
        "Federal Executive Branch",
        "Independent Federal Agencies",
    }
)
_INCLUDE_ENTITY_TYPES: Final[frozenset[str]] = frozenset({"Agency", "Ind Agency", "Bureau", "GSE"})
_ROOT_PARENTS: Final[frozenset[str]] = frozenset({"[Branch]", "[Bucket]", ""})

# Strip a trailing " (ACRONYM)" parenthetical where the contents are an
# acronym-shaped token. Keeps natural parentheticals (e.g. "(disestablished)")
# in place if they ever appear.
_ACRONYM_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<stem>.+?)\s*\((?P<acronym>[A-Z0-9][A-Za-z0-9&/\- ]{0,31})\)\s*$"
)
_MAX_PARENT_DEPTH: Final[int] = 32

_US_PREFIX: Final[str] = "US "


@dataclass(frozen=True, slots=True)
class InstitutionEntry:
    """One institution gazetteer entry.

    Attributes:
        name: Canonical display name (no trailing acronym, no leading "US ").
        aliases: Sorted, de-duplicated alternative surface forms.
        category: One of the institutions.schema.json category enum values.
            Phase 2 always emits ``federal_agency``.
        jurisdictions: ISO-like jurisdiction codes. Federal rows are ``["US"]``.
    """

    name: str
    aliases: tuple[str, ...]
    category: str
    jurisdictions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-ready shape used by the aggregator."""
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "category": self.category,
            "jurisdictions": list(self.jurisdictions),
        }


def _split_acronym(raw_name: str) -> tuple[str, str | None]:
    """Split a ``Name (ACRONYM)`` pair into (stem, acronym)."""
    match = _ACRONYM_SUFFIX_RE.match(raw_name)
    if match is None:
        return raw_name.strip(), None
    return match.group("stem").strip(), match.group("acronym").strip()


def _canonical_name(stem: str) -> str:
    """Drop a leading ``US `` scope prefix so federal names match common usage."""
    if stem.startswith(_US_PREFIX) and len(stem) > len(_US_PREFIX):
        return stem[len(_US_PREFIX) :]
    return stem


def _root_branch(
    name: str,
    parent_by_name: dict[str, str],
) -> str:
    """Walk parent links to the root-level branch name.

    The crosswalk uses ``[Branch]`` and ``[Bucket]`` as synthetic roots;
    treat an empty or bracketed parent as terminal.
    """
    current = name
    for _ in range(_MAX_PARENT_DEPTH):
        parent = parent_by_name.get(current, "")
        if parent in _ROOT_PARENTS or parent not in parent_by_name:
            return current
        current = parent
    return current


def parse_gsa_agencies(path: Path) -> list[InstitutionEntry]:
    """Parse the GSA Federal Hierarchy Crosswalk into federal-agency entries.

    Args:
        path: CSV path. Must exist; source is immutable.

    Returns:
        Entries in deterministic order, sorted by name. Each entry has
        ``category="federal_agency"`` and ``jurisdictions=("US",)``.

    Raises:
        MissingSourceError: If ``path`` does not exist.
        PipelineError: If the CSV header is missing required columns.
    """
    if not path.is_file():
        raise MissingSourceError(
            f"GSA federal agency crosswalk not found at {path}. Run `make sources` to fetch."
        )

    with path.open("r", encoding=_SOURCE_ENCODING, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = _REQUIRED_COLUMNS - set(header)
        if missing:
            raise PipelineError(
                f"{path}: missing required columns {sorted(missing)}; header was {header!r}"
            )
        raw_rows = list(reader)

    parent_by_name: dict[str, str] = {}
    for row in raw_rows:
        name = (row.get("GSA SFP Name") or "").strip()
        parent = (row.get("Parent") or "").strip()
        if name:
            parent_by_name[name] = parent

    entries: dict[str, InstitutionEntry] = {}
    for row in raw_rows:
        raw_name = (row.get("GSA SFP Name") or "").strip()
        entity_type = (row.get("GSA SFP Entity Type") or "").strip()
        if not raw_name or entity_type not in _INCLUDE_ENTITY_TYPES:
            continue
        if _root_branch(raw_name, parent_by_name) not in _FEDERAL_ROOTS:
            continue

        stem, acronym = _split_acronym(raw_name)
        canonical = _canonical_name(stem)
        if not canonical or canonical in entries:
            continue

        aliases: set[str] = set()
        if acronym:
            aliases.add(acronym)
        if stem != canonical:
            aliases.add(stem)
        if raw_name not in (canonical, stem):
            aliases.add(raw_name)

        entries[canonical] = InstitutionEntry(
            name=canonical,
            aliases=tuple(sorted(aliases)),
            category="federal_agency",
            jurisdictions=("US",),
        )

    return sorted(entries.values(), key=lambda e: e.name)
