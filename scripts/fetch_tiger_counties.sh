#!/usr/bin/env bash
# fetch_tiger_counties.sh — fetch Census Gazetteer counties national file (D-03).
# §105 PD. Provenance and SHA-256 are recorded in SOURCES.md.
#
# NOTE: spec §1.4 references TIGER<YYYY>/COUNTY/ shapefiles. This script fetches
# the Census Gazetteer flat file (2024_Gaz_counties_national.zip) instead — same
# source authority (§105 PD Census), same county set, but tabular rather than
# geometric. The flat file is the form the address_components builder
# (src/resecta_data/gazetteers/address_components/build.py) actually consumes.
# Defer-note in d03-DONE.md asks Jesse to confirm the spec amendment.
#
# Vintage pinned to 2024 (verified 2026-04-27 against Census Gazetteer landing
# page — 2025 Gazetteer was published 2025-09-10 but is deliberately NOT adopted
# here: rolling vintages downstream of the address_components consumer is a
# Jesse-decides change with rebuild blast-radius. Mirrors D-02 vintage policy.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

VINTAGE="2024"
URL="https://www2.census.gov/geo/docs/maps-data/data/gazetteer/${VINTAGE}_Gazetteer/${VINTAGE}_Gaz_counties_national.zip"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/institutions/sources"
DEST_FILE="$DEST_DIR/census_counties_${VINTAGE}.zip"

mkdir -p "$DEST_DIR"
fetch_lib::probe_url "$URL"
fetch_lib::download_with_sha "$URL" "$DEST_FILE"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/institutions/sources/census_counties_${VINTAGE}.zip" \
    "Public Domain" \
    "$URL" \
    "Census ${VINTAGE} Gazetteer counties national file (D-03; spec §1.4 names TIGER<YYYY>/COUNTY/ but address_components builder consumes Gazetteer flat-file equivalent — same Census source authority §105 PD; 3,222 rows). Inner file ${VINTAGE}_Gaz_counties_national.txt is tab-delimited UTF-8 with BOM. Consumed by gazetteers/address_components.json county list (C11)."

echo ""
echo "D-03 fetch complete: $DEST_FILE"
echo "SHA-256: $(_fetch_lib::read_sidecar "$DEST_FILE")"
