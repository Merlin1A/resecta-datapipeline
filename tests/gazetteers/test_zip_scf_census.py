"""Tests for the Census ZCTA-to-Tract adapter (C11).

Covers the Census substitute for the HUD crosswalk. Confirms that the
full-file Census path supersedes the 32-row HUD bootstrap when present, and
that the build auto-detects by header sniff.
"""

from __future__ import annotations

from pathlib import Path

from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.zip_scf import build as build_zip_scf
from resecta_data.gazetteers.zip_scf.parse_census_zcta import (
    _FIPS_TO_USPS,
    parse_census_zcta_tract,
)

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
ZIP_SCF_SOURCES = REPO_ROOT / "src" / "resecta_data" / "gazetteers" / "zip_scf" / "sources"
CENSUS_ZCTA_SOURCE = ZIP_SCF_SOURCES / "census_zcta_tract_2020_20260419.txt"
HUD_BOOTSTRAP_SOURCE = ZIP_SCF_SOURCES / "hud_zip_crosswalk_bootstrap_20260416.csv"

# The HUD bootstrap is a hand-curated 32-row fixture. The Census ZCTA file
# covers every 5-digit ZCTA in the US — on the order of 33 500 rows, across
# 800+ SCF prefixes. The "supersedes" threshold only needs to assert
# meaningfully-more, not a tight bound.
_MIN_CENSUS_SCF_ROWS = 500


def test_parse_census_zcta_determinism() -> None:
    """Parsing the same file twice yields equal row lists."""
    first = parse_census_zcta_tract(CENSUS_ZCTA_SOURCE)
    second = parse_census_zcta_tract(CENSUS_ZCTA_SOURCE)
    assert first == second


def test_parse_census_zcta_rows_sorted() -> None:
    """Rows come out sorted by (zcta5, state)."""
    rows = parse_census_zcta_tract(CENSUS_ZCTA_SOURCE)
    keys = [(r.zip5, r.state) for r in rows]
    assert keys == sorted(keys)


def test_parse_census_zcta_ratios_sum_to_one_per_zcta() -> None:
    """Per-ZCTA ratios across states sum to 1.0 (within float tolerance)."""
    rows = parse_census_zcta_tract(CENSUS_ZCTA_SOURCE)
    totals: dict[str, float] = {}
    for r in rows:
        totals[r.zip5] = totals.get(r.zip5, 0.0) + r.res_ratio
    # Sanity: every accumulated total is within 1e-6 of 1.0.
    off = [(z, t) for z, t in totals.items() if abs(t - 1.0) > 1e-6]
    assert not off, f"{len(off)} ZCTAs have ratios not summing to 1.0: sample={off[:3]}"


def test_census_zcta_supersedes_bootstrap(tmp_build_dir: Path) -> None:
    """Census full-file build produces substantially more SCF rows than HUD bootstrap."""
    bootstrap_payload = build_zip_scf(
        20260416,
        source=HUD_BOOTSTRAP_SOURCE,
        retrieval_date="2026-04-16",
    )
    census_payload = build_zip_scf(
        20260416,
        source=CENSUS_ZCTA_SOURCE,
        retrieval_date="2026-04-19",
    )

    bootstrap_scf_rows = len(bootstrap_payload["scf_table"])
    census_scf_rows = len(census_payload["scf_table"])
    assert census_scf_rows >= _MIN_CENSUS_SCF_ROWS, (
        f"Census ZCTA produced {census_scf_rows} SCF rows; expected ≥ {_MIN_CENSUS_SCF_ROWS}"
    )
    assert census_scf_rows > bootstrap_scf_rows * 10, (
        f"Census ZCTA ({census_scf_rows} SCF rows) did not substantially supersede "
        f"HUD bootstrap ({bootstrap_scf_rows} SCF rows)"
    )


def test_census_zcta_build_schema(tmp_build_dir: Path) -> None:
    """Census-driven build still validates under the unchanged zip_scf_states schema."""
    payload = build_zip_scf(
        20260416,
        source=CENSUS_ZCTA_SOURCE,
        retrieval_date="2026-04-19",
    )
    dest = tmp_build_dir / "gazetteers" / "zip_scf_states.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "zip_scf_states")


def test_census_zcta_build_covers_all_states(tmp_build_dir: Path) -> None:
    """Every FIPS-mapped state code lands in the scf_table for the full file."""
    payload = build_zip_scf(
        20260416,
        source=CENSUS_ZCTA_SOURCE,
        retrieval_date="2026-04-19",
    )
    observed_states = set(payload["scf_table"].values()) | set(payload["overrides"].values())
    expected = set(_FIPS_TO_USPS.values())
    # The 50 states + DC must appear. Territories (AS/GU/MP/PR/VI) may or may
    # not have their own SCF prefixes depending on ZCTA coverage, so they
    # are not required.
    mainland = {code for code in expected if code not in {"AS", "GU", "MP", "PR", "VI"}}
    missing = mainland - observed_states
    assert not missing, f"expected all 50 states + DC in scf_table; missing {sorted(missing)}"
