#!/usr/bin/env bash
# fetch_gnis.sh — fetch USGS GNIS Populated Places national text extract.
# §105 PD. Provenance and SHA-256 are recorded in SOURCES.md.
#
# NOTE: USGS GNIS also publishes a broader national feature file
# (download-gnis-data portal). This script fetches the PopulatedPlaces extract
# instead — a per-class subset of the same source ("national feature
# file or per-class subset"). The per-class subset is what's already on disk
# and what the A7 consumer (src/resecta_data/gazetteers/address_components/
# build.py) actually reads.
#
# Vintage pinned via RETRIEVED constant in the destination filename — the
# upstream URL is stable (USGS publishes bi-monthly refreshes at the same
# path). Re-runs target the same on-disk filename and hit the cache;
# upstream republish would surface as drift on a fresh fetch.
# Do NOT bump RETRIEVED silently — rolling the vintage downstream of the
# A7 builder changes only under an approved source plan (rebuild blast-radius).
# Mirrors the source-vintage rule: a vintage change is a new SOURCES.md row,
# never an in-place edit.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

RETRIEVED="20260419"
URL="https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/Topical/PopulatedPlaces_National_Text.zip"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/institutions/sources"
DEST_FILE="$DEST_DIR/usgs_gnis_pop_places_${RETRIEVED}.zip"

mkdir -p "$DEST_DIR"
fetch_lib::probe_url "$URL"
fetch_lib::download_with_sha "$URL" "$DEST_FILE"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/institutions/sources/usgs_gnis_pop_places_${RETRIEVED}.zip" \
    "Public Domain" \
    "$URL" \
    "USGS GNIS Populated Places national text extract (a per-class subset of GNIS; §105 PD U.S. federal work). Inner file Text/PopulatedPlaces_National.txt is pipe-delimited UTF-8 with BOM (190,922 data rows). Consumed by gazetteers/address_components.json city list."

echo ""
echo "GNIS fetch complete: $DEST_FILE"
echo "SHA-256: $(_fetch_lib::read_sidecar "$DEST_FILE")"
