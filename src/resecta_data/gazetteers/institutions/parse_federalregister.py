"""Parser for the Federal Register agencies API feed (D-01 source).

Consumes the JSON feed at
``src/resecta_data/gazetteers/institutions/sources/federalregister_agencies.json``
(NARA Office of the Federal Register + GPO; 17 U.S.C. §105 public domain) and
emits ``InstitutionEntry`` rows scoped to ``federal_agency`` per
``schemas/institutions.schema.json``.

The feed is a flat list of agency records. Each record's ``name`` field is
already the canonical display form (no trailing parenthetical acronym, no
``US `` scope prefix); ``short_name`` carries the acronym alias when it
differs from ``name``. ``parent_id`` and ``child_ids`` describe the agency
hierarchy; both root agencies and sub-agencies are emitted as separate rows
so the Swift detector can match either surface form (e.g., "Internal Revenue
Service" under "Department of the Treasury").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError

from .parse_gsa import InstitutionEntry

_SOURCE_ENCODING: Final[str] = "utf-8"


def parse_federalregister_agencies(path: Path) -> list[InstitutionEntry]:
    """Parse the Federal Register agencies feed into federal-agency entries.

    Args:
        path: JSON path. Must exist; source is immutable.

    Returns:
        Entries in deterministic order, sorted by name. Each entry has
        ``category="federal_agency"`` and ``jurisdictions=("US",)``.

    Raises:
        MissingSourceError: If ``path`` does not exist.
        PipelineError: If the JSON is malformed or not a list of records.
    """
    if not path.is_file():
        raise MissingSourceError(
            f"Federal Register agencies feed not found at {path}. "
            "Run `make sources` to fetch, or check CLAUDE.md §1.4."
        )

    with path.open("r", encoding=_SOURCE_ENCODING) as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"{path}: invalid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise PipelineError(f"{path}: expected a list of agency records, got {type(raw).__name__}")

    entries: dict[str, InstitutionEntry] = {}
    for record in raw:
        if not isinstance(record, dict):
            continue
        name = (record.get("name") or "").strip()
        if not name or name in entries:
            continue
        short_name = (record.get("short_name") or "").strip()
        aliases: set[str] = set()
        if short_name and short_name != name:
            aliases.add(short_name)

        entries[name] = InstitutionEntry(
            name=name,
            aliases=tuple(sorted(aliases)),
            category="federal_agency",
            jurisdictions=("US",),
        )

    return sorted(entries.values(), key=lambda e: e.name)
