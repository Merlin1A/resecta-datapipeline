#!/usr/bin/env bash
# fetch_tiger_places.sh — fetch Census TIGER/Line PLACE per-state archives.
# §105 PD. Provenance and SHA-256 are recorded in SOURCES.md.
# Vintage pinned to TIGER2024 (verified 2026-04-27 against
# https://www2.census.gov/geo/tiger/). TIGER2025 was released 2025-09-23 but is
# deliberately NOT adopted here: rolling vintages downstream of A7 is a
# Jesse-decides change with rebuild blast-radius. F-15-style vintage discipline.
#
# Usage:
#   ./scripts/fetch_tiger_places.sh           # all 51 archives (50 states + DC)
#   ./scripts/fetch_tiger_places.sh <FIPS>    # single state by FIPS code (debug)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

VINTAGE="2024"
BASE_URL="https://www2.census.gov/geo/tiger/TIGER${VINTAGE}/PLACE"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/address_components/sources/tiger_places"

# 50 states + DC (FIPS 11). Excludes PR (72) and other territories per kickoff.
# Non-contiguous codes are intentional: 03/07/14/43/52 are unassigned in FIPS 5-2.
STATES=(01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 \
        31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56)

mkdir -p "$DEST_DIR"

if [ "$#" -eq 1 ]; then
    STATES=("$1")
fi

for fips in "${STATES[@]}"; do
    fname="tl_${VINTAGE}_${fips}_place.zip"
    url="${BASE_URL}/${fname}"
    dest="${DEST_DIR}/${fname}"
    fetch_lib::probe_url "$url"
    fetch_lib::download_with_sha "$url" "$dest"
    fetch_lib::append_sources_row \
        "src/resecta_data/gazetteers/address_components/sources/tiger_places/${fname}" \
        "Public Domain" \
        "$url" \
        "Census TIGER/Line ${VINTAGE} PLACE shapefile for FIPS state ${fips} (D-02); §105 PD; consumed by gazetteers/address_components.json (A7) cities list."
done

echo ""
echo "D-02 fetch complete. Archives written to: $DEST_DIR"
echo "Total: ${#STATES[@]} state archive(s)."
