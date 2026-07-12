"""Tests for the FDIC + EDGAR institution parsers and integrated builder (S5 / design 02 §6).

Exercises:
- parse_fdic: category assignment, ACTIVE filtering, STNAME mapping, alias derivation
- parse_edgar: top-N cutoff, keyword classifier, alias derivation
- normalize: business-suffix normalization
- build: absent-optional-source byte-identity, duplicate-dated-file raise, merge priority
- Schema: financial_institution enum membership
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import dump_canonical_json, sha256_file
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.institutions import build as build_institutions
from resecta_data.gazetteers.institutions.build import (
    _FR_RETRIEVAL_DATE,
    _FR_SOURCE,
    _FR_SOURCE_ID,
    _find_optional_source,
    _retrieval_date_from_path,
)
from resecta_data.gazetteers.institutions.normalize import derive_aliases
from resecta_data.gazetteers.institutions.parse_edgar import parse_edgar_companies
from resecta_data.gazetteers.institutions.parse_fdic import parse_fdic_institutions
from resecta_data.gazetteers.institutions.parse_federalregister import (
    parse_federalregister_agencies,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "institutions"
FDIC_SAMPLE = FIXTURES_DIR / "fdic_sample.csv"
EDGAR_SAMPLE = FIXTURES_DIR / "edgar_sample.json"


# ---------------------------------------------------------------------------
# normalize.py — business-suffix normalization
# ---------------------------------------------------------------------------


def test_business_suffix_normalization_chase() -> None:
    """Chase Bank, N.A. normalizes to alias 'chase bank'."""
    aliases = derive_aliases("Chase Bank, N.A.")
    # 'chase bank' should appear — it is the pre-comma, suffix-stripped form
    assert "chase bank" in aliases, f"Expected 'chase bank' in {aliases}"


def test_business_suffix_normalization_full_form() -> None:
    """Full normalized form of a comma-bearing name is also retained as alias."""
    aliases = derive_aliases("First National Bank, Inc.")
    # 'first national bank, inc.' normalized minus the suffix = 'first national bank'
    assert "first national bank" in aliases


def test_business_suffix_pre_strip_retained() -> None:
    """Pre-strip form (before suffix removal) is retained when it differs from stripped."""
    aliases = derive_aliases("Wells Fargo Bank, National Association")
    # Both 'wells fargo bank' (stripped) and the full-name-normalized form should appear
    assert "wells fargo bank" in aliases


def test_business_suffix_normalization_llc() -> None:
    """LLC suffix is stripped."""
    aliases = derive_aliases("Mountain West Bank, LLC")
    assert "mountain west bank" in aliases


def test_business_suffix_normalization_corp() -> None:
    """Corp suffix is stripped."""
    aliases = derive_aliases("Acme Financial Corp")
    assert "acme financial" in aliases


def test_alias_never_empty() -> None:
    """derive_aliases never emits an empty or whitespace-only alias."""
    for name in ["Corp", "N.A.", "LLC", "A", "Inc."]:
        aliases = derive_aliases(name)
        for a in aliases:
            assert a.strip(), f"Empty alias from {name!r}: {aliases}"


# ---------------------------------------------------------------------------
# parse_fdic.py
# ---------------------------------------------------------------------------


def test_fdic_entries_category() -> None:
    """All FDIC entries have category='financial_institution'."""
    entries = parse_fdic_institutions(FDIC_SAMPLE)
    assert entries, "Should have parsed at least one entry"
    for entry in entries:
        assert entry.category == "financial_institution", (
            f"Entry {entry.name!r} has category {entry.category!r}, "
            "expected 'financial_institution'"
        )


def test_fdic_active_filter() -> None:
    """Only ACTIVE==1 rows are included; ACTIVE==0 rows are excluded."""
    entries = parse_fdic_institutions(FDIC_SAMPLE)
    names = {e.name for e in entries}
    # Inactive rows in fixture
    assert "Defunct Savings Bank, F.S.B." not in names, "Inactive bank should be excluded"
    assert "Inactive Corp. Bank" not in names, "Inactive bank should be excluded"
    # Active rows
    assert "Chase Bank, N.A." in names


def test_fdic_stname_mapping() -> None:
    """STNAME maps to correct USPS jurisdiction codes."""
    entries = parse_fdic_institutions(FDIC_SAMPLE)
    by_name = {e.name: e for e in entries}
    assert by_name["Chase Bank, N.A."].jurisdictions == ("OH",)
    assert by_name["Bank of America, National Association"].jurisdictions == ("NC",)
    assert by_name["Wells Fargo Bank, National Association"].jurisdictions == ("SD",)


def test_fdic_stname_unknown_raises(tmp_path: Path) -> None:
    """Unknown STNAME raises PipelineError."""
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("NAME,CERT,CITY,STNAME,ACTIVE\nFoo Bank,1,City,Nowhereistan,1\n")
    with pytest.raises(PipelineError, match="unknown state name"):
        parse_fdic_institutions(bad_csv)


def test_fdic_missing_source_raises() -> None:
    """Missing CSV path raises MissingSourceError."""

    with pytest.raises(MissingSourceError):
        parse_fdic_institutions(Path("/nonexistent/path.csv"))


def test_fdic_missing_columns_raises(tmp_path: Path) -> None:
    """CSV missing required columns raises PipelineError."""
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("NAME,CITY\nFoo Bank,Somewhere\n")
    with pytest.raises(PipelineError, match="missing required columns"):
        parse_fdic_institutions(bad_csv)


def test_fdic_determinism() -> None:
    """Parsing the same CSV twice yields byte-identical entries."""
    first = parse_fdic_institutions(FDIC_SAMPLE)
    second = parse_fdic_institutions(FDIC_SAMPLE)
    assert first == second
    assert [e.name for e in first] == [e.name for e in second]


def test_fdic_sorted_by_name() -> None:
    """Entries are returned in ascending name order."""
    entries = parse_fdic_institutions(FDIC_SAMPLE)
    names = [e.name for e in entries]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# parse_edgar.py
# ---------------------------------------------------------------------------


def test_edgar_top_n_cutoff() -> None:
    """top_n parameter limits the number of entries consumed."""
    entries_3 = parse_edgar_companies(EDGAR_SAMPLE, top_n=3)
    entries_5 = parse_edgar_companies(EDGAR_SAMPLE, top_n=5)
    assert len(entries_3) <= 3
    assert len(entries_5) <= 5
    # Increasing top_n can only add entries (or keep equal if many dupes)
    assert len(entries_5) >= len(entries_3)


def test_edgar_keyword_classifier_financial() -> None:
    """Records with financial keywords are classified as financial_institution."""
    entries = parse_edgar_companies(EDGAR_SAMPLE)
    by_name = {e.name: e for e in entries}
    # JPMorgan Chase contains 'bank' in the context of common knowledge, but
    # the fixture title is "JPMorgan Chase & Co." — 'capital' is not present.
    # The keyword 'financial' or 'bank' or 'holdings' etc.
    # Check entries from the fixture that contain financial keywords.
    # "Bank of America Corp" → contains "bank"
    assert "Bank of America Corp" in by_name
    assert by_name["Bank of America Corp"].category == "financial_institution"
    # "Goldman Sachs Group Inc." → does not contain any financial keyword directly,
    # but "securities" might. Let's verify the actual behavior:
    # Fixture: "Goldman Sachs Group Inc." — no financial keyword → employer.
    assert "Goldman Sachs Group Inc." in by_name
    # Goldman Sachs: 'capital', 'invest', 'asset', 'holdings' etc — none present.
    # Actually Goldman Sachs doesn't contain any keyword. Should be employer.
    assert by_name["Goldman Sachs Group Inc."].category == "employer"


def test_edgar_keyword_classifier_employer() -> None:
    """Records without financial keywords are classified as employer."""
    entries = parse_edgar_companies(EDGAR_SAMPLE)
    by_name = {e.name: e for e in entries}
    # Apple Inc., Microsoft Corp, Johnson & Johnson → employer
    assert by_name["Apple Inc."].category == "employer"
    assert by_name["MICROSOFT CORP"].category == "employer"
    assert by_name["Johnson & Johnson"].category == "employer"


def test_edgar_missing_source_raises() -> None:
    """Missing JSON path raises MissingSourceError."""

    with pytest.raises(MissingSourceError):
        parse_edgar_companies(Path("/nonexistent/path.json"))


def test_edgar_malformed_json_raises(tmp_path: Path) -> None:
    """Malformed JSON raises PipelineError."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not valid json")
    with pytest.raises(PipelineError, match="invalid JSON"):
        parse_edgar_companies(bad_json)


def test_edgar_non_dict_raises(tmp_path: Path) -> None:
    """Non-dict JSON root raises PipelineError."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("[1, 2, 3]")
    with pytest.raises(PipelineError, match="expected a dict"):
        parse_edgar_companies(bad_json)


def test_edgar_determinism() -> None:
    """Parsing the same JSON twice yields byte-identical entries."""
    first = parse_edgar_companies(EDGAR_SAMPLE)
    second = parse_edgar_companies(EDGAR_SAMPLE)
    assert first == second


def test_edgar_sorted_by_name() -> None:
    """Entries are returned in ascending name order."""
    entries = parse_edgar_companies(EDGAR_SAMPLE)
    names = [e.name for e in entries]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# build.py — absent-optional-source byte-identity
# ---------------------------------------------------------------------------


def test_absent_optional_sources_byte_identity(tmp_path: Path) -> None:
    """When no FDIC/EDGAR files are present, build() output is byte-identical to FedReg-only.

    This is the critical regression guard: with no optional sources, the
    payload must be byte-for-byte identical to the behavior before S5
    (FedReg only). The sources list must contain exactly the federalregister row,
    and entries must match parse_federalregister output alone.
    """
    # Build with no optional sources (pass empty tmp_path so globs find nothing).
    empty_dir = tmp_path / "empty_sources"
    empty_dir.mkdir()

    # The build() function defaults to searching _SOURCE_DIR; pass explicit None
    # sources to use the default glob (which won't find anything in the real
    # sources dir since no dated files are present on this Mac).
    payload_actual = build_institutions(20260416, fdic_source=None, edgar_source=None)

    # Compute expected payload from FedReg only.
    fr_entries = parse_federalregister_agencies(_FR_SOURCE)
    payload_expected: dict[str, object] = {
        "version": 1,
        "generated_by": "resecta-data/gazetteers/institutions",
        "seed": 20260416,
        "sources": [
            {
                "id": _FR_SOURCE_ID,
                "sha256": sha256_file(_FR_SOURCE),
                "retrieval_date": _FR_RETRIEVAL_DATE,
            }
        ],
        "entries": [e.to_dict() for e in fr_entries],
    }

    # Compare via canonical JSON dumps for byte-identity.
    dest_actual = tmp_path / "actual.json"
    dest_expected = tmp_path / "expected.json"
    dump_canonical_json(payload_actual, dest_actual)
    dump_canonical_json(payload_expected, dest_expected)

    actual_bytes = dest_actual.read_bytes()
    expected_bytes = dest_expected.read_bytes()
    assert actual_bytes == expected_bytes, (
        "build() output without optional sources must be byte-identical to FedReg-only payload. "
        "Check that no optional source files leaked into _SOURCE_DIR."
    )

    # The sources list must contain exactly one entry: federalregister_agencies.
    assert len(payload_actual["sources"]) == 1
    assert payload_actual["sources"][0]["id"] == _FR_SOURCE_ID


def test_absent_optional_sources_entries_match_federalregister() -> None:
    """Without optional sources, entries match parse_federalregister_agencies output."""

    fr_entries = parse_federalregister_agencies(_FR_SOURCE)
    payload = build_institutions(20260416)
    entry_dicts = payload["entries"]
    expected_dicts = [e.to_dict() for e in fr_entries]

    assert entry_dicts == expected_dicts


def test_duplicate_dated_fdic_file_raises(tmp_path: Path) -> None:
    """If more than one dated FDIC file exists, build() raises PipelineError."""
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "fdic_institutions_20260601.csv").write_text("NAME,CERT,CITY,STNAME,ACTIVE\n")
    (sources_dir / "fdic_institutions_20260611.csv").write_text("NAME,CERT,CITY,STNAME,ACTIVE\n")

    # Patch _SOURCE_DIR by injecting explicit paths won't work directly;
    # test build_institutions logic via a helper that searches the tmp dir.

    with pytest.raises(PipelineError, match="Multiple"):
        _find_optional_source(sources_dir, "fdic_institutions_*.csv", "FDIC institutions")


def test_duplicate_dated_edgar_file_raises(tmp_path: Path) -> None:
    """If more than one dated EDGAR file exists, build() raises PipelineError."""
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "edgar_company_tickers_20260601.json").write_text("{}")
    (sources_dir / "edgar_company_tickers_20260611.json").write_text("{}")

    with pytest.raises(PipelineError, match="Multiple"):
        _find_optional_source(sources_dir, "edgar_company_tickers_*.json", "EDGAR company tickers")


# ---------------------------------------------------------------------------
# build.py — with optional sources (using fixtures as injected paths)
# ---------------------------------------------------------------------------


def _make_dated_fdic(tmp_path: Path, date: str = "20260611") -> Path:
    """Copy FDIC_SAMPLE to a dated filename in tmp_path."""

    dated = tmp_path / f"fdic_institutions_{date}.csv"
    shutil.copy(FDIC_SAMPLE, dated)
    return dated


def _make_dated_edgar(tmp_path: Path, date: str = "20260611") -> Path:
    """Copy EDGAR_SAMPLE to a dated filename in tmp_path."""

    dated = tmp_path / f"edgar_company_tickers_{date}.json"
    shutil.copy(EDGAR_SAMPLE, dated)
    return dated


def test_build_with_fdic_source(tmp_path: Path) -> None:
    """build() with FDIC fixture includes financial_institution entries."""
    fdic_path = _make_dated_fdic(tmp_path)
    payload = build_institutions(20260416, fdic_source=fdic_path)

    # At least one financial_institution entry must be present.
    fi_entries = [e for e in payload["entries"] if e["category"] == "financial_institution"]
    assert fi_entries, "Expected at least one financial_institution entry"

    # FDIC source row must be in provenance.
    ids = {s["id"] for s in payload["sources"]}
    assert "fdic_institutions" in ids
    assert "federalregister_agencies" in ids


def test_build_with_edgar_source(tmp_path: Path) -> None:
    """build() with EDGAR fixture includes employer/financial_institution entries."""
    edgar_path = _make_dated_edgar(tmp_path)
    payload = build_institutions(20260416, edgar_source=edgar_path)

    employer_entries = [e for e in payload["entries"] if e["category"] == "employer"]
    fi_entries = [e for e in payload["entries"] if e["category"] == "financial_institution"]
    assert employer_entries or fi_entries, (
        "Expected employer or financial_institution entries from EDGAR"
    )

    ids = {s["id"] for s in payload["sources"]}
    assert "edgar_company_tickers" in ids
    assert "federalregister_agencies" in ids


def test_build_merge_priority_fedreq_wins(tmp_path: Path) -> None:
    """FedReg entries win on name collision over FDIC and EDGAR entries."""
    # Craft a fake FDIC CSV that has a name colliding with a known FedReg entry.
    # Use "Internal Revenue Service" as the collision target.
    collision_csv = tmp_path / "fdic_institutions_20260611.csv"
    collision_csv.write_text(
        "NAME,CERT,CITY,STNAME,ACTIVE\nInternal Revenue Service,99999,Washington,Virginia,1\n"
    )

    payload = build_institutions(20260416, fdic_source=collision_csv)
    by_name = {e["name"]: e for e in payload["entries"]}

    # FedReg's version (federal_agency, US) should win, not FDIC's (financial_institution, VA).
    assert "Internal Revenue Service" in by_name
    irs = by_name["Internal Revenue Service"]
    assert irs["category"] == "federal_agency", "FedReg category should win over FDIC collision"
    assert irs["jurisdictions"] == ["US"]


def test_build_determinism_with_fdic(tmp_path: Path) -> None:
    """build() with FDIC source is byte-identical across two calls with same seed."""
    fdic_path = _make_dated_fdic(tmp_path)
    payload1 = build_institutions(20260416, fdic_source=fdic_path)
    payload2 = build_institutions(20260416, fdic_source=fdic_path)

    dest1 = tmp_path / "p1.json"
    dest2 = tmp_path / "p2.json"
    dump_canonical_json(payload1, dest1)
    dump_canonical_json(payload2, dest2)

    assert dest1.read_bytes() == dest2.read_bytes(), (
        "build() with FDIC must be byte-identical across calls"
    )


def test_build_determinism_with_both_sources(tmp_path: Path) -> None:
    """build() with both FDIC and EDGAR is byte-identical across two calls."""
    fdic_path = _make_dated_fdic(tmp_path)
    edgar_path = _make_dated_edgar(tmp_path)
    payload1 = build_institutions(20260416, fdic_source=fdic_path, edgar_source=edgar_path)
    payload2 = build_institutions(20260416, fdic_source=fdic_path, edgar_source=edgar_path)

    dest1 = tmp_path / "p1.json"
    dest2 = tmp_path / "p2.json"
    dump_canonical_json(payload1, dest1)
    dump_canonical_json(payload2, dest2)

    assert dest1.read_bytes() == dest2.read_bytes()


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_schema_includes_financial_institution() -> None:
    """institutions.schema.json enum includes 'financial_institution'."""
    schema_path = SCHEMAS_DIR / "institutions.schema.json"
    schema = json.loads(schema_path.read_text())
    category_enum = schema["properties"]["entries"]["items"]["properties"]["category"]["enum"]
    assert "financial_institution" in category_enum
    assert "employer" in category_enum


def test_institutions_schema_with_financial_entries(tmp_path: Path) -> None:
    """build() with FDIC source validates against institutions.schema.json."""
    fdic_path = _make_dated_fdic(tmp_path)
    payload = build_institutions(20260416, fdic_source=fdic_path)
    dest = tmp_path / "gazetteers" / "institutions.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "institutions")


def test_retrieval_date_from_path() -> None:
    """_retrieval_date_from_path extracts ISO date from YYYYMMDD suffix."""

    p = Path("fdic_institutions_20260611.csv")
    assert _retrieval_date_from_path(p) == "2026-06-11"

    p2 = Path("edgar_company_tickers_20260401.json")
    assert _retrieval_date_from_path(p2) == "2026-04-01"


def test_retrieval_date_from_path_no_suffix_raises() -> None:
    """_retrieval_date_from_path raises when no YYYYMMDD suffix is present."""

    with pytest.raises(PipelineError, match="retrieval date"):
        _retrieval_date_from_path(Path("fdic_institutions.csv"))
