"""Tests for the federal-agency institution gazetteer (C9).

Post-cc-derive-rebuild S1: the build is sourced from the Federal Register
agencies API feed (D-01) per STRAT §10.4 row §D1 (= A1 1:1). The legacy
GSA Federal Hierarchy Crosswalk parser is retained for the advisory
cutover-diff sidecar emitted alongside the rebuild artifact and exercised
by ``test_parse_gsa_*`` below.
"""

from __future__ import annotations

from pathlib import Path

from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.institutions import (
    build as build_institutions,
)
from resecta_data.gazetteers.institutions import (
    build_cutover_diff as build_institutions_cutover_diff,
)
from resecta_data.gazetteers.institutions.parse_federalregister import (
    parse_federalregister_agencies,
)
from resecta_data.gazetteers.institutions.parse_gsa import parse_gsa_agencies

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"
SOURCES_DIR = (
    Path(__file__).parent.parent.parent
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "institutions"
    / "sources"
)
GSA_SOURCE = SOURCES_DIR / "gsa_federal_agencies_20260419.csv"
FR_SOURCE = SOURCES_DIR / "federalregister_agencies.json"


def test_parse_gsa_determinism() -> None:
    """Parsing the same CSV twice yields byte-identical entries."""
    first = parse_gsa_agencies(GSA_SOURCE)
    second = parse_gsa_agencies(GSA_SOURCE)
    assert first == second
    assert [e.name for e in first] == [e.name for e in second]


def test_parse_gsa_sorted_by_name() -> None:
    """Entries are returned in ascending name order for determinism."""
    entries = parse_gsa_agencies(GSA_SOURCE)
    names = [e.name for e in entries]
    assert names == sorted(names)


def test_parse_federalregister_determinism() -> None:
    """Parsing the same Federal Register feed twice yields byte-identical entries."""
    first = parse_federalregister_agencies(FR_SOURCE)
    second = parse_federalregister_agencies(FR_SOURCE)
    assert first == second
    assert [e.name for e in first] == [e.name for e in second]


def test_parse_federalregister_sorted_by_name() -> None:
    """Entries are returned in ascending name order for determinism."""
    entries = parse_federalregister_agencies(FR_SOURCE)
    names = [e.name for e in entries]
    assert names == sorted(names)


def test_institutions_schema(tmp_build_dir: Path) -> None:
    """build() output validates against institutions.schema.json."""
    payload = build_institutions(20260416)
    dest = tmp_build_dir / "gazetteers" / "institutions.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "institutions")


def test_known_entries() -> None:
    """Well-known federal agencies land in the output with expected aliases."""
    payload = build_institutions(20260416)
    entries_by_name = {e["name"]: e for e in payload["entries"]}

    assert "Internal Revenue Service" in entries_by_name
    irs = entries_by_name["Internal Revenue Service"]
    assert irs["category"] == "federal_agency"
    assert irs["jurisdictions"] == ["US"]
    assert "IRS" in irs["aliases"]

    assert "Social Security Administration" in entries_by_name
    ssa = entries_by_name["Social Security Administration"]
    assert ssa["category"] == "federal_agency"
    assert ssa["jurisdictions"] == ["US"]
    assert "SSA" in ssa["aliases"]


def test_every_entry_is_federal_agency() -> None:
    """Phase 2 scope is federal-only; all rows must be federal_agency / US."""
    payload = build_institutions(20260416)
    for entry in payload["entries"]:
        assert entry["category"] == "federal_agency"
        assert entry["jurisdictions"] == ["US"]


def test_sources_record_matches_raw_sha() -> None:
    """The sources[] block records the D-01 SHA-256 pinned in SOURCES.md."""
    payload = build_institutions(20260416)
    fr = next(s for s in payload["sources"] if s["id"] == "federalregister_agencies")
    assert fr["sha256"] == ("6b5713fae71a5ae9fcfc8def7fc46ddab3afe115ee403d75a8620387bda109d6")
    assert fr["retrieval_date"] == "2026-04-27"


def test_cutover_diff_schema(tmp_build_dir: Path) -> None:
    """build_cutover_diff() output validates against cutover_diff.schema.json."""
    diff = build_institutions_cutover_diff()
    dest = tmp_build_dir / "gazetteers" / "institutions.cutover-diff.json"
    dump_canonical_json(diff, dest)
    validate_file(dest, SCHEMAS_DIR, "cutover_diff")
    assert diff["artifact"] == "gazetteers/institutions.json"
    summary = diff["summary"]
    assert summary["legacy_only_count"] == len(diff["legacy_only"])
    assert summary["rebuild_only_count"] == len(diff["rebuild_only"])
    assert summary["keyed_diff_count"] == len(diff["keyed_diff"])
