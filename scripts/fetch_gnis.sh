#!/usr/bin/env bash
# fetch_gnis.sh — fetch USGS GNIS Populated Places national text extract (D-04).
# §105 PD. Provenance and SHA-256 are recorded in SOURCES.md.
#
# NOTE: Spec §1.5 references the broader USGS GNIS national feature file
# (download-gnis-data portal). This script fetches the PopulatedPlaces extract
# instead — a per-class subset spec §1.5 explicitly permits ("national feature
# file or per-class subset"). The per-class subset is what's already on disk
# and what the A7 consumer (src/resecta_data/gazetteers/address_components/
# build.py) actually reads. Defer-note in d04-DONE.md asks Jesse to confirm
# the spec amendment.
#
# Vintage pinned via RETRIEVED constant in the destination filename — the
# upstream URL is stable (USGS publishes bi-monthly refreshes at the same
# path). Re-runs target the same on-disk filename and hit the cache;
# upstream republish would surface as F-2-style drift on a fresh fetch.
# Do NOT bump RETRIEVED silently — rolling the vintage downstream of the
# A7 builder is a Jesse-decides change with rebuild blast-radius. Mirrors
# D-03 vintage policy.

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
    "USGS GNIS Populated Places national text extract (D-04; per-class subset of GNIS per spec §1.5; §105 PD U.S. federal work). Inner file Text/PopulatedPlaces_National.txt is pipe-delimited UTF-8 with BOM (190,922 data rows). Consumed by gazetteers/address_components.json city list (C11)."

echo ""
echo "D-04 fetch complete: $DEST_FILE"
echo "SHA-256: $(_fetch_lib::read_sidecar "$DEST_FILE")"
