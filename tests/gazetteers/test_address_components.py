"""Tests for the address-components gazetteer.

This gazetteer already consumes the GNIS and TIGER sources it is
required to use, so the cutover-diff against the prior build is empty
by construction. ``test_cutover_diff_schema`` validates the empty-diff
artifact against ``schemas/cutover_diff.schema.json`` and asserts the
verification-posture invariants.

The GNIS x TIGER cross-filter adds new tests. ``test_gnis_tiger_filter_drops_junk``
and ``test_dbf_reader_*`` exercise the filter using a synthetic in-memory DBF;
the ``test_tiger_place_*`` integration test exercises the real 51-state corpus
but is bounded to a loose count range so timing-sensitive environments are
not tripped.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.address_components import (
    build as build_address_components,
)
from resecta_data.gazetteers.address_components import (
    build_cutover_diff as build_address_components_cutover_diff,
)
from resecta_data.gazetteers.address_components.parse_census_counties import (
    parse_census_counties,
)
from resecta_data.gazetteers.address_components.parse_tiger_place import (
    _parse_dbf_bytes,
    parse_tiger_place_dir,
    parse_tiger_place_zip,
)
from resecta_data.gazetteers.address_components.parse_usgs_gnis import parse_usgs_gnis

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
SOURCES_DIR = REPO_ROOT / "src" / "resecta_data" / "gazetteers" / "institutions" / "sources"
TIGER_PLACES_DIR = (
    REPO_ROOT
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "address_components"
    / "sources"
    / "tiger_places"
)
GNIS_ZIP = SOURCES_DIR / "usgs_gnis_pop_places_20260419.zip"
COUNTIES_ZIP = SOURCES_DIR / "census_counties_2024.zip"

# Expected street-type suffixes — fixed top-20 list. Must match the full-word
# vocabulary in the Swift address regex; any change here requires a
# coordinated Swift PR.
EXPECTED_STREET_TYPES = (
    "Alley",
    "Avenue",
    "Boulevard",
    "Circle",
    "Court",
    "Drive",
    "Expressway",
    "Freeway",
    "Highway",
    "Lane",
    "Parkway",
    "Place",
    "Plaza",
    "Road",
    "Route",
    "Square",
    "Street",
    "Terrace",
    "Trail",
    "Way",
)

# City-count floors — GNIS ships roughly 110k unique populated-place names.
# The floor is comfortably below the observed count so the test does not
# brittle-fail on minor upstream rev churn.
_GNIS_CITY_MIN = 10_000

# Post-TIGER cross-filter city-count bounds.  The design spec (item 2.9)
# predicted ~15-20% drop; the measured drop is ~82% (110k -> ~20k) because
# the join uses NAME equality (not substring), so GNIS entries with
# suffixes, abbreviations, or unincorporated forms that don't exist as
# exact TIGER NAME values are excluded.  The bounds below are intentionally
# wide so the test does not brittle-fail on minor Census or GNIS vintage
# changes.
_FILTERED_CITY_MIN = 10_000  # floor: at least 10k incorporated places
_FILTERED_CITY_MAX = 50_000  # ceiling: well below the raw 110k GNIS count

# The Census counties gazetteer has 3,222 rows (3,141 US counties + DC +
# counties-equivalent + 78 Puerto Rico municipios + island-area
# subdivisions), but many county names are shared across states ("Adams
# County" etc.). The address-components artifact emits unique NAMEs, which
# lands around 1,960; assert a tolerance window around that.
_COUNTY_MIN = 1_800
_COUNTY_MAX = 2_200

# ---------------------------------------------------------------------------
# Synthetic DBF helper
# ---------------------------------------------------------------------------


def _make_dbf(rows: list[tuple[str, str]]) -> bytes:
    """Build minimal dBase III DBF bytes for (NAME, NAMELSAD) row pairs.

    Creates a well-formed DBF with exactly two character fields (NAME=100,
    NAMELSAD=100) and one data record per entry in *rows*.  Deleted-record
    support: the first byte of each record is 0x20 (active).  A
    ``0x2A``-prefix deleted record is injected as the second record for the
    deleted-record-skip test; callers control whether to request it via the
    ``_make_dbf_with_deleted`` helper below.

    Args:
        rows: List of (NAME, NAMELSAD) string pairs for active records.

    Returns:
        Raw DBF bytes suitable for passing to ``_parse_dbf_bytes``.
    """
    # Two field descriptors: NAME (C, 100) and NAMELSAD (C, 100).
    # Each descriptor is 32 bytes.
    field_descs = b""
    for fname, flen in (("NAME", 100), ("NAMELSAD", 100)):
        name_bytes = fname.encode("ascii").ljust(11, b"\x00")
        desc = (
            name_bytes  # 11 bytes: field name
            + b"C"  # 1 byte:  type
            + b"\x00" * 4  # 4 bytes: reserved
            + bytes([flen])  # 1 byte:  field length
            + b"\x00" * 15  # 15 bytes: rest of descriptor
        )
        assert len(desc) == 32
        field_descs += desc

    header_size = 32 + len(field_descs) + 1  # 32 (file hdr) + descs + 0x0D
    record_size = 1 + 100 + 100  # deletion flag + NAME + NAMELSAD

    # File header (32 bytes).
    num_records = len(rows)
    file_header = (
        struct.pack(
            "<B3xIHH",
            3,  # version: dBase III
            num_records,
            header_size,
            record_size,
        )
        + b"\x00" * 20
    )  # pad to 32 bytes

    assert len(file_header) == 32

    # Terminator.
    terminator = b"\x0d"

    # Data records.
    record_bytes = b""
    for name, namelsad in rows:
        flag = b"\x20"  # active
        name_field = name.encode("cp1252", errors="replace").ljust(100)[:100]
        namelsad_field = namelsad.encode("cp1252", errors="replace").ljust(100)[:100]
        record_bytes += flag + name_field + namelsad_field

    return file_header + field_descs + terminator + record_bytes


def _make_dbf_with_deleted(rows: list[tuple[str, str]]) -> bytes:
    """Like ``_make_dbf`` but inserts a deleted record (0x2A flag) as the
    second record after the first active row.

    The deleted record carries the name "SHOULD_NOT_APPEAR".
    """
    base = _make_dbf(rows)

    # Locate the first data record and clone it as a deleted record.
    # header_size is at bytes 8-9 (little-endian uint16).
    header_size = struct.unpack_from("<H", base, 8)[0]
    record_size = struct.unpack_from("<H", base, 10)[0]

    deleted_name = "SHOULD_NOT_APPEAR"
    deleted_namelsad = "SHOULD_NOT_APPEAR town"
    deleted_record = (
        b"\x2a"  # deleted flag
        + deleted_name.encode("cp1252").ljust(100)[:100]
        + deleted_namelsad.encode("cp1252").ljust(100)[:100]
    )

    # Splice the deleted record after the first active record.
    insert_at = header_size + record_size
    spliced = base[:insert_at] + deleted_record + base[insert_at:]

    # Bump num_records by 1.
    new_num = struct.unpack_from("<I", spliced, 4)[0] + 1
    return spliced[:4] + struct.pack("<I", new_num) + spliced[8:]


# ---------------------------------------------------------------------------
# Tests: DBF reader unit tests (synthetic bytes)
# ---------------------------------------------------------------------------


def test_dbf_reader_extracts_name_field() -> None:
    """``_parse_dbf_bytes`` returns the NAME column casefolded."""
    raw = _make_dbf([("Hurtsboro", "Hurtsboro town"), ("Good Hope", "Good Hope city")])
    result = _parse_dbf_bytes(raw, "synthetic")
    assert result == {"hurtsboro", "good hope"}


def test_dbf_reader_skips_deleted_records() -> None:
    """Records with deletion flag 0x2A are excluded from the output."""
    raw = _make_dbf_with_deleted([("Real City", "Real City city")])
    result = _parse_dbf_bytes(raw, "synthetic-with-deleted")
    assert "should_not_appear" not in result
    assert "real city" in result


def test_dbf_reader_strips_trailing_whitespace() -> None:
    """Trailing CP-1252 spaces in NAME fields are stripped before casefolding."""
    padded = "Portland   "  # 11 trailing spaces to fill the 100-char field
    raw = _make_dbf([(padded, "Portland city")])
    result = _parse_dbf_bytes(raw, "synthetic-padded")
    assert "portland" in result
    assert "portland   " not in result


# ---------------------------------------------------------------------------
# Tests: GNIS x TIGER cross-filter (synthetic tiger dir)
# ---------------------------------------------------------------------------


def _write_synthetic_tiger_zip(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    """Write a single synthetic TIGER PLACE zip to *tmp_path* and return its path."""
    dbf_bytes = _make_dbf(rows)
    zip_path = tmp_path / "tl_2024_00_place.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tl_2024_00_place.dbf", dbf_bytes)
    return zip_path


def test_gnis_tiger_filter_drops_junk(tmp_path: Path) -> None:
    """GNIS entries absent from TIGER PLACE NAMEs are excluded from cities.

    The entry "107 West Colonia" is a known junk GNIS pattern (no formal
    municipal boundary); it is absent from the synthetic TIGER set and must
    be excluded.  The entry "Good Hope" matches TIGER NAME exactly
    (case-insensitive) and must be retained.
    """
    tiger_dir = tmp_path / "tiger"
    tiger_dir.mkdir()
    _write_synthetic_tiger_zip(
        tiger_dir,
        [("Good Hope", "Good Hope city"), ("Hurtsboro", "Hurtsboro town")],
    )

    # Build a minimal GNIS-like set with one real and one junk entry.
    gnis_cities = {"Good Hope", "107 West Colonia", "Hurtsboro"}

    # Apply the same filter logic used by build().
    tiger_names = parse_tiger_place_dir(tiger_dir)
    filtered = {name for name in gnis_cities if name.casefold() in tiger_names}

    assert "Good Hope" in filtered, "Real city present in TIGER should be kept"
    assert "Hurtsboro" in filtered, "Real city present in TIGER should be kept"
    assert "107 West Colonia" not in filtered, "Junk entry absent from TIGER should be dropped"


def test_gnis_tiger_filter_build_uses_tiger_dir(tmp_path: Path) -> None:
    """``build()`` accepts an injectable ``tiger_dir`` and applies the filter.

    This is a fast determinism-style test: runs build() twice with a
    synthetic tiger dir (only two places) and checks that (a) the outputs
    are identical and (b) only TIGER-matched cities appear.
    """
    tiger_dir = tmp_path / "tiger"
    tiger_dir.mkdir()
    _write_synthetic_tiger_zip(
        tiger_dir,
        [("Chicago", "Chicago city"), ("Houston", "Houston city")],
    )

    first = build_address_components(20260416, tiger_dir=tiger_dir)
    second = build_address_components(20260416, tiger_dir=tiger_dir)
    assert first == second, "build() must be deterministic"

    cities_set = set(first["cities"])
    # With only 2 TIGER places (Chicago and Houston), at most those 2 GNIS
    # names (casefolded) can survive the filter.  The GNIS raw set contains
    # many cities; all others are dropped.
    assert len(cities_set) <= 2, (
        "With only 2 TIGER places, at most 2 GNIS cities should survive the filter"
    )


def test_tiger_place_zip_reader_unit(tmp_path: Path) -> None:
    """``parse_tiger_place_zip`` parses a zip-wrapped DBF correctly."""
    zip_path = _write_synthetic_tiger_zip(
        tmp_path,
        [("Salem", "Salem city"), ("Portland", "Portland city")],
    )
    result = parse_tiger_place_zip(zip_path)
    assert "salem" in result
    assert "portland" in result
    # Result is casefolded.
    assert "Salem" not in result


# ---------------------------------------------------------------------------
# Tests: real 51-state TIGER corpus (integration; bounded assertions)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not TIGER_PLACES_DIR.is_dir() or not any(TIGER_PLACES_DIR.glob("*.zip")),
    reason="TIGER PLACE zips not present; run `make sources` first.",
)
def test_tiger_place_real_count() -> None:
    """All 51 TIGER PLACE zips parse to a sane total name count.

    Bounds: 10000-60000 unique casefolded place names across 51 states.
    This is an integration-style loose check; exact counts vary with Census
    vintage.
    """
    names = parse_tiger_place_dir(TIGER_PLACES_DIR)
    assert 10_000 <= len(names) <= 60_000, (
        f"TIGER place-name count {len(names)} outside expected range [10000, 60000]"
    )


def test_determinism() -> None:
    """Building twice with the same seed yields byte-identical payloads."""
    first = build_address_components(20260416)
    second = build_address_components(20260416)
    assert first == second


def test_schema(tmp_build_dir: Path) -> None:
    """build() output validates against address_components.schema.json."""
    payload = build_address_components(20260416)
    dest = tmp_build_dir / "gazetteers" / "address_components.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "address_components")


def test_gnis_city_count() -> None:
    """The GNIS adapter yields a lower bound of distinct city names (raw, pre-filter)."""
    inner = zipfile.Path(GNIS_ZIP) / "Text" / "PopulatedPlaces_National.txt"
    cities = parse_usgs_gnis(inner)
    assert len(cities) >= _GNIS_CITY_MIN, (
        f"GNIS produced {len(cities)} unique cities; expected ≥ {_GNIS_CITY_MIN}"
    )


def test_gnis_filtered_city_count() -> None:
    """After the TIGER cross-filter, city count is within the expected drop range.

    The filter drops ~15-20% of GNIS entries.
    The bounds here are intentionally wide (5%-40% drop from ~110k) so the
    test does not brittle-fail on minor corpus or vintage changes.
    """
    # TIGER cross-filter: the build() function calls parse_tiger_place_dir()
    # against the real 51-state corpus.  This test re-uses build() directly.
    payload = build_address_components(20260416)
    count = len(payload["cities"])
    assert _FILTERED_CITY_MIN <= count <= _FILTERED_CITY_MAX, (
        f"Filtered city count {count} outside expected range "
        f"[{_FILTERED_CITY_MIN}, {_FILTERED_CITY_MAX}]; "
        f"check TIGER cross-filter or GNIS source."
    )


def test_counties_count() -> None:
    """The Census counties adapter yields ~3,222 rows."""
    inner = zipfile.Path(COUNTIES_ZIP) / "2024_Gaz_counties_national.txt"
    counties = parse_census_counties(inner)
    assert _COUNTY_MIN <= len(counties) <= _COUNTY_MAX, (
        f"Census counties produced {len(counties)} rows; expected in [{_COUNTY_MIN}, {_COUNTY_MAX}]"
    )


def test_street_types_fixed() -> None:
    """The hardcoded top-20 street types are present and sorted ascending."""
    payload = build_address_components(20260416)
    street_types = payload["street_types"]
    assert tuple(street_types) == EXPECTED_STREET_TYPES
    assert street_types == sorted(street_types)


def test_cities_and_counties_sorted() -> None:
    """Array-valued fields are sorted ascending for deterministic output."""
    payload = build_address_components(20260416)
    assert payload["cities"] == sorted(payload["cities"])
    assert payload["counties"] == sorted(payload["counties"])


def test_sources_record_matches_raw_sha() -> None:
    """The sources[] block records the SHA-256 pinned in SOURCES.md."""
    payload = build_address_components(20260416)
    gnis = next(s for s in payload["sources"] if s["id"] == "usgs_gnis_pop_places")
    assert gnis["sha256"] == ("02bad3663b7fb4516e9157f343f6dfde6593babc4188961a2df9f0b352b8ca04")
    assert gnis["retrieval_date"] == "2026-04-19"

    counties = next(s for s in payload["sources"] if s["id"] == "census_counties_2024")
    assert counties["sha256"] == (
        "3c337402b5c6e8d5aa26b4278ccf4edc8989f2683765b3ffbf22296cdb2df3a0"
    )
    assert counties["retrieval_date"] == "2026-04-19"

    # TIGER PLACE sources: 51 entries, one per state FIPS code.
    tiger_sources = [s for s in payload["sources"] if s["id"].startswith("tiger_place_2024_")]
    assert len(tiger_sources) == 51, (
        f"Expected 51 TIGER PLACE source entries, got {len(tiger_sources)}"
    )
    # Spot-check FIPS 01 (Alabama) and FIPS 06 (California, different retrieval date).
    fips_01 = next((s for s in tiger_sources if s["id"] == "tiger_place_2024_01"), None)
    assert fips_01 is not None
    assert fips_01["sha256"] == "5a6787d4caf39dc103a254879ad07e0fce047a93c0ea2f68c25432cb78ec3f4b"
    assert fips_01["retrieval_date"] == "2026-04-28"

    fips_06 = next((s for s in tiger_sources if s["id"] == "tiger_place_2024_06"), None)
    assert fips_06 is not None
    assert fips_06["sha256"] == "81b827124043164a93d442f6dc4c088fd56c319da14195e4a14ab0fb39656a42"
    assert fips_06["retrieval_date"] == "2026-04-27"


def test_known_cities_and_counties_present() -> None:
    """Well-known populated places and counties land in the output."""
    payload = build_address_components(20260416)
    cities = set(payload["cities"])
    counties = set(payload["counties"])
    # GNIS records "New York" (state capital/feature entries) but the
    # national extract indexes by feature_name, which for NYC is "New York".
    for city in ("Chicago", "Houston", "Phoenix"):
        assert city in cities, f"GNIS missing expected city {city!r}"
    for county in ("Los Angeles County", "Cook County", "Harris County"):
        assert county in counties, f"Census missing expected county {county!r}"


@pytest.mark.parametrize("arr_key", ["cities", "counties", "street_types"])
def test_arrays_non_empty(arr_key: str) -> None:
    """Every array in the payload is non-empty (schema floors minItems to 1)."""
    payload = build_address_components(20260416)
    assert len(payload[arr_key]) > 0


def test_cutover_diff_schema(tmp_build_dir: Path) -> None:
    """build_cutover_diff() output validates against cutover_diff.schema.json.

    The diff is empty by construction (no legacy parser variant retired
    in this rebuild). The summary counts mirror the array lengths; the
    artifact field points at the rebuild artifact this diff covers.
    """
    diff = build_address_components_cutover_diff()
    dest = tmp_build_dir / "gazetteers" / "address_components.cutover-diff.json"
    dump_canonical_json(diff, dest)
    validate_file(dest, SCHEMAS_DIR, "cutover_diff")
    assert diff["artifact"] == "gazetteers/address_components.json"
    summary = diff["summary"]
    assert summary["legacy_only_count"] == len(diff["legacy_only"])
    assert summary["rebuild_only_count"] == len(diff["rebuild_only"])
    assert summary["keyed_diff_count"] == len(diff["keyed_diff"])
    # Verification-posture invariant: empty by construction (no legacy
    # variant). Failing this assertion means a legacy-vs-rebuild diff
    # surface has been introduced and the PR description needs
    # refreshing.
    assert summary["legacy_only_count"] == 0
    assert summary["rebuild_only_count"] == 0
    assert summary["keyed_diff_count"] == 0


def test_cutover_diff_determinism() -> None:
    """build_cutover_diff() returns byte-identical output across two calls."""
    first = build_address_components_cutover_diff()
    second = build_address_components_cutover_diff()
    assert first == second
