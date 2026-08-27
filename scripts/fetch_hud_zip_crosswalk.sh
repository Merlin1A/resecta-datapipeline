#!/usr/bin/env bash
#
# fetch_hud_zip_crosswalk.sh — obtain the HUD USPS ZIP-to-state crosswalk
# quarterly snapshot. This is the ONLY script in the pipeline that accesses
# the network, but HUD's crosswalk download requires a
# registered login/API key — it is not available at an unauthenticated URL
# (see https://www.huduser.gov/portal/datasets/usps_crosswalk.html).
#
# Two ways to supply the file:
#
#   1. HUD_CROSSWALK_LOCAL_FILE=<path>   (preferred for one-off runs)
#      Point at a CSV you already downloaded through HUD's web UI after
#      logging in. The file is copied into sources/ with the canonical
#      name; its SHA-256 is printed.
#
#   2. HUD_USPS_API_URL=<url>            (for scripted runs with API token)
#      Example (after registering at huduser.gov/apps/public/uspscrosswalk/register
#      and getting a token):
#        HUD_USPS_API_URL="https://www.huduser.gov/hudapi/public/usps?type=1&query=All"
#        HUD_USPS_TOKEN="<your token>"
#      The script fetches the API JSON and writes the raw body into sources/.
#      (Downstream parsing step will convert JSON→CSV if needed.)
#
# If neither env var is set this script prints manual-download instructions
# and exits 3. The master orchestrator reports this as a failed step — that
# is accurate, because HUD cannot be automated without credentials.

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <YYYY> <Qn>" >&2
    exit 2
fi

YEAR="$1"
QUARTER="$2"
LOWER_Q=$(echo "$QUARTER" | tr '[:upper:]' '[:lower:]')

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/zip_scf/sources"
DEST_FILE="$DEST_DIR/hud_zip_crosswalk_${YEAR}${LOWER_Q}.csv"

if [ -f "$DEST_FILE" ]; then
    echo "File already exists: $DEST_FILE" >&2
    echo "Refusing to overwrite. Source files are immutable." >&2
    exit 1
fi

mkdir -p "$DEST_DIR"

if [ -n "${HUD_CROSSWALK_LOCAL_FILE:-}" ]; then
    if [ ! -f "$HUD_CROSSWALK_LOCAL_FILE" ]; then
        echo "HUD_CROSSWALK_LOCAL_FILE=$HUD_CROSSWALK_LOCAL_FILE does not exist." >&2
        exit 4
    fi
    echo "Copying local HUD crosswalk: $HUD_CROSSWALK_LOCAL_FILE"
    cp "$HUD_CROSSWALK_LOCAL_FILE" "$DEST_FILE"
elif [ -n "${HUD_USPS_API_URL:-}" ]; then
    if [ -z "${HUD_USPS_TOKEN:-}" ]; then
        echo "HUD_USPS_API_URL is set but HUD_USPS_TOKEN is not." >&2
        echo "Register at https://www.huduser.gov/apps/public/uspscrosswalk/register" >&2
        echo "then export HUD_USPS_TOKEN=<the token shown after login>." >&2
        exit 4
    fi
    echo "Fetching $HUD_USPS_API_URL (via HUD API token)"
    curl --fail --silent --show-error --location \
        -H "Authorization: Bearer $HUD_USPS_TOKEN" \
        --output "$DEST_FILE" "$HUD_USPS_API_URL"
else
    cat <<EOF >&2
HUD USPS ZIP crosswalk cannot be fetched automatically — HUD requires a
registered account or an API token (see
https://www.huduser.gov/portal/datasets/usps_crosswalk.html).

To supply the file, pick one of the two options below and re-run this
script (or the master orchestrator):

  A) Manual download (web UI, one-time):
       1. Register at https://www.huduser.gov/apps/public/uspscrosswalk/register
       2. Log in at  https://www.huduser.gov/apps/public/uspscrosswalk/home
       3. Select "ZIP to State" for quarter ${QUARTER} ${YEAR}, export CSV.
       4. Save the file somewhere, then re-run this script with:
            HUD_CROSSWALK_LOCAL_FILE=/path/to/that.csv \\
              $0 ${YEAR} ${QUARTER}

  B) API access (scripted; one-time token registration):
       1. Register, then copy the API token shown after login.
       2. export HUD_USPS_TOKEN=<token>
          export HUD_USPS_API_URL="https://www.huduser.gov/hudapi/public/usps?type=1&query=All&year=${YEAR}&quarter=${LOWER_Q: -1}"
       3. Re-run this script.

Until one of those is provided, the pipeline will continue to use the
bootstrap sample at
  src/resecta_data/gazetteers/zip_scf/sources/hud_zip_crosswalk_bootstrap_20260416.csv
which is enough for tests but will not match full-state coverage.
EOF
    exit 3
fi

echo ""
echo "Wrote $DEST_FILE"
echo "SHA-256:"
shasum -a 256 "$DEST_FILE" | awk '{print $1}'
echo ""
echo "Next steps:"
echo "  1. Add a row to SOURCES.md with the SHA-256 above."
echo "  2. Update Makefile's ZIP_SCF_SOURCE to point at the new file (if changing default)."
echo "  3. Run \`make zip-scf && make verify\` to regenerate the derived artifact."
