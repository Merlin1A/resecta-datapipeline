#!/usr/bin/env bash
# fetch_tiger_counties.sh — fetch Census Gazetteer counties national file.
# §105 PD. Provenance and SHA-256 are recorded in SOURCES.md.
#
# NOTE: TIGER also publishes <YYYY>/COUNTY/ shapefiles for this same data. This
# script fetches the Census Gazetteer flat file (2024_Gaz_counties_national.zip)
# instead — same source authority (§105 PD Census), same county set, but
# tabular rather than geometric. The flat file is the form the
# address_components builder (src/resecta_data/gazetteers/address_components/
# build.py) actually consumes.
#
# Vintage pinned to 2024 (verified 2026-04-27 against Census Gazetteer landing
# page — 2025 Gazetteer was published 2025-09-10 but is deliberately NOT adopted
# here: rolling vintages downstream of the address_components consumer
# changes only under an approved source plan (rebuild blast-radius). Mirrors
# the source-vintage rule: a vintage change is a new SOURCES.md row, never an
# in-place edit.

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
    "Census ${VINTAGE} Gazetteer counties national file (TIGER also publishes <YYYY>/COUNTY/ shapefiles for this data; address_components builder consumes the Gazetteer flat-file equivalent — same Census source authority §105 PD; 3,222 rows). Inner file ${VINTAGE}_Gaz_counties_national.txt is tab-delimited UTF-8 with BOM. Consumed by gazetteers/address_components.json county list."

echo ""
echo "Census counties fetch complete: $DEST_FILE"
echo "SHA-256: $(_fetch_lib::read_sidecar "$DEST_FILE")"
