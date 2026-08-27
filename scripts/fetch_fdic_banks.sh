#!/usr/bin/env bash
# fetch_fdic_banks.sh — fetch the FDIC institutions CSV export.
# §105 PD.
# Source: banks.data.fdic.gov institutions API — active institutions only.
#
# SOURCES.md row description (avoid banned phrases):
#   FDIC institutions API CSV export (active institutions); fields NAME,CERT,CITY,STNAME,ACTIVE;
#   U.S. federal government data non-copyrightable under 17 U.S.C. §105; used by the institution
#   gazetteer to classify financial_institution entries for document-header anchoring.
#
# NOTE: This script is authored here but is designed to run on Linux where
# the _fetch_lib.sh row-append helper uses flock (absent on macOS). Run this
# script on a Linux host — the row-append helper needs flock; see
# scripts/_fetch_lib.README.md.
#
# Usage:
#   ./scripts/fetch_fdic_banks.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/institutions/sources"

# FDIC institutions API endpoint.
# Fields: NAME, CERT, CITY, STNAME, ACTIVE (the ACTIVE filter is applied server-side).
# sort_by=NAME for deterministic field order across fetches.
# limit=10000 is the API maximum per request; the full active-institution set
# is typically ~4,500 rows so a single request is sufficient.
FDIC_URL="https://banks.data.fdic.gov/api/institutions?fields=NAME,CERT,CITY,STNAME,ACTIVE&filters=ACTIVE:1&limit=10000&offset=0&sort_by=NAME&sort_order=ASC&output=csv"
DATED_FILENAME="fdic_institutions_$(date -u +%Y%m%d).csv"
DEST="$DEST_DIR/$DATED_FILENAME"

mkdir -p "$DEST_DIR"

fetch_lib::probe_url "$FDIC_URL"
fetch_lib::download_with_sha "$FDIC_URL" "$DEST"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/institutions/sources/${DATED_FILENAME}" \
    "Public Domain (17 U.S.C. § 105)" \
    "$FDIC_URL" \
    "FDIC institutions API CSV export (active institutions, fields NAME/CERT/CITY/STNAME/ACTIVE); U.S. federal government data non-copyrightable under 17 U.S.C. §105; used by the institution gazetteer as a financial_institution source for document-header anchoring."

echo ""
echo "FDIC fetch complete. CSV written to: $DEST"
