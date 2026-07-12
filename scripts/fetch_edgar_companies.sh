#!/usr/bin/env bash
# fetch_edgar_companies.sh — fetch the SEC EDGAR company_tickers.json feed.
# Spec: design 02 §6 (D3 pre-approved). §105 PD.
# Source: https://www.sec.gov/files/company_tickers.json
#
# SOURCES.md row description (avoid banned phrases):
#   SEC EDGAR company_tickers.json (ticker/CIK/company-name mapping); U.S. federal
#   government data non-copyrightable under 17 U.S.C. §105; top-N entries used by
#   the institution gazetteer as financial_institution and employer sources for
#   document-header anchoring.
#
# NOTE: This script is authored here but is designed to run on Linux where
# the _fetch_lib.sh row-append helper uses flock (absent on macOS). Jesse runs
# this script on a Linux host; see scripts/_fetch_lib.README.md.
#
# NOTE (Jesse Gate): The EDGAR top-500 curation criterion (keyword-classifier
# proxy for SIC 6000-6799) and the split into financial_institution vs. employer
# categories are parked for Jesse's approval before this script runs. See
# parse_edgar.py module docstring and design 02 §6 "Jesse Gates".
#
# SEC rate-limit guidance: the SEC requests a descriptive User-Agent identifying
# the application and contact email. Use the format:
#   company-name/app-name contact-email@example.com
# This is consistent with https://www.sec.gov/developer and avoids automated
# request throttling. We pass the User-Agent to both the probe and download.
#
# Usage:
#   ./scripts/fetch_edgar_companies.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/institutions/sources"

EDGAR_URL="https://www.sec.gov/files/company_tickers.json"
DATED_FILENAME="edgar_company_tickers_$(date -u +%Y%m%d).json"
DEST="$DEST_DIR/$DATED_FILENAME"

# SEC-requested descriptive User-Agent (see script header comment).
# Override with the SEC_UA env var to supply your own contact when self-hosting.
SEC_UA="${SEC_UA:-Resecta-DataPipeline/1.0 support@resecta.app}"

mkdir -p "$DEST_DIR"

fetch_lib::probe_url "$EDGAR_URL"
fetch_lib::download_with_sha "$EDGAR_URL" "$DEST" "$SEC_UA"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/institutions/sources/${DATED_FILENAME}" \
    "Public Domain (17 U.S.C. § 105)" \
    "$EDGAR_URL" \
    "SEC EDGAR company_tickers.json (ticker/CIK/company-name mapping, ~14k entries); U.S. federal government data non-copyrightable under 17 U.S.C. §105; top-N entries used by the institution gazetteer as financial_institution and employer sources for document-header anchoring."

echo ""
echo "EDGAR fetch complete. JSON written to: $DEST"
