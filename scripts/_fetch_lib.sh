#!/usr/bin/env bash
# scripts/_fetch_lib.sh — shared helpers for CC-SCRIPT fetchers.
# Sourced (not executed). See _fetch_lib.README.md for the fetcher API contract.
# Each fetcher performs a live HTTP probe (no silent degraded retrieve), validates
# SHA-256 against SOURCES.md, and appends one provenance row.
# Invariants: network-permitted only in `make sources`; sources are immutable.

# Refuse to be executed directly.
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    echo "_fetch_lib.sh is a sourced library, not an executable script." >&2
    exit 2
fi

set -euo pipefail

_FETCH_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_FETCH_LIB_REPO_ROOT="$(cd "$_FETCH_LIB_DIR/.." && pwd)"

# --- private helpers ---------------------------------------------------------

# _fetch_lib::compute_sha256 <path>
# Echo the lowercase 64-char hex SHA-256 of <path>. shasum first, sha256sum fallback.
_fetch_lib::compute_sha256() {
    local f="$1"
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$f" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$f" | awk '{print $1}'
    else
        echo "_fetch_lib::compute_sha256: neither shasum nor sha256sum on PATH" >&2
        return 2
    fi
}

# _fetch_lib::read_sidecar <path>
# Echo the SHA-256 stored in <path>.sha256. Reads first whitespace-separated
# field of the first line so the helper tolerates both the lib's own
# single-hex-line format and a sha256sum two-column format if hand-written.
_fetch_lib::read_sidecar() {
    local sidecar="$1.sha256"
    if [[ ! -f "$sidecar" ]]; then
        echo "_fetch_lib::read_sidecar: $sidecar missing" >&2
        return 1
    fi
    local sha
    sha="$(awk 'NR==1 {print $1; exit}' "$sidecar")"
    if [[ ! "$sha" =~ ^[0-9a-f]{64}$ ]]; then
        echo "_fetch_lib::read_sidecar: invalid SHA-256 in $sidecar: '$sha'" >&2
        return 1
    fi
    printf '%s\n' "$sha"
}

# _fetch_lib::write_sidecar <path>
# Compute SHA-256 of <path> and write it to <path>.sha256 (single hex line + LF).
_fetch_lib::write_sidecar() {
    local f="$1"
    local sha
    sha="$(_fetch_lib::compute_sha256 "$f")"
    printf '%s\n' "$sha" > "$f.sha256"
}

# --- public API --------------------------------------------------------------

# fetch_lib::probe_url <url>
# HEAD-probe <url>. Exit 0 on 200/206/301/302; exit 1 on 4xx/5xx/cert/DNS/timeout.
# 3xx redirects are followed by the real download via curl --location;
# the probe accepts 301/302 so a benign Location-header pivot doesn't halt
# the chain. The rule is "halt+report on 404/301/cert error (no silent
# degraded retrieve)" — the halt is for 4xx/5xx and connection failures.
fetch_lib::probe_url() {
    local url="$1"
    local code
    if ! code="$(curl --head --silent --show-error --max-time 30 \
                       --user-agent "Wget/1.21" \
                       --output /dev/null --write-out '%{http_code}' "$url" 2>&1)"; then
        echo "fetch_lib::probe_url: curl failed for $url (cert / DNS / network):" >&2
        echo "  $code" >&2
        return 1
    fi
    case "$code" in
        200|206|301|302)
            return 0
            ;;
        *)
            echo "fetch_lib::probe_url: $url returned HTTP $code (expected 200/206/301/302)" >&2
            return 1
            ;;
    esac
}

# fetch_lib::download_with_sha <url> <dest> [<ua>]
# Download <url> to <dest> via curl --fail --silent --show-error --location.
# Capture SHA-256 into <dest>.sha256 sidecar.
# Refuses to overwrite <dest>. On cache-hit, verifies the
# existing sidecar (or writes one if absent) and returns 0.
# Default UA = "Wget/1.21" (proven against ssa.gov).
fetch_lib::download_with_sha() {
    local url="$1"
    local dest="$2"
    local ua="${3:-Wget/1.21}"

    if [[ -f "$dest" ]]; then
        if [[ -f "$dest.sha256" ]]; then
            if fetch_lib::verify_sidecar "$dest"; then
                echo "fetch_lib::download_with_sha: $dest already cached (sidecar matches)" >&2
                return 0
            fi
            echo "fetch_lib::download_with_sha: $dest cached but sidecar mismatch — refuse to overwrite" >&2
            return 1
        fi
        echo "fetch_lib::download_with_sha: $dest cached without sidecar; computing now" >&2
        _fetch_lib::write_sidecar "$dest"
        return 0
    fi

    mkdir -p "$(dirname "$dest")"
    local tmp
    tmp="$(mktemp "${dest}.tmp.XXXXXX")"
    local rc=0
    curl --fail --silent --show-error --location \
        --max-time 600 \
        --user-agent "$ua" \
        --output "$tmp" "$url" || rc=$?
    if (( rc != 0 )); then
        rm -f "$tmp"
        echo "fetch_lib::download_with_sha: curl failed for $url (rc=$rc)" >&2
        return 1
    fi

    mv "$tmp" "$dest"
    _fetch_lib::write_sidecar "$dest"
}

# fetch_lib::write_dated_mirror <live_path> <mirror_dir> <prefix>
# Write a dated copy of <live_path> at <mirror_dir>/<prefix>-YYYY-MM-DD.<ext>
# (UTC date) plus a sidecar. Refuses to overwrite an existing dated mirror
# whose SHA-256 differs from <live_path> (Failure Mode #2 — drift between
# same-day re-fetches).
# Only the Federal Register agencies fetcher writes a dated mirror.
fetch_lib::write_dated_mirror() {
    local live_path="$1"
    local mirror_dir="$2"
    local prefix="$3"

    if [[ ! -f "$live_path" ]]; then
        echo "fetch_lib::write_dated_mirror: $live_path missing" >&2
        return 1
    fi

    local date_utc
    date_utc="$(date -u +%Y-%m-%d)"
    local ext="${live_path##*.}"
    local mirror_path="$mirror_dir/${prefix}-${date_utc}.${ext}"

    mkdir -p "$mirror_dir"

    if [[ -f "$mirror_path" ]]; then
        local live_sha mirror_sha
        live_sha="$(_fetch_lib::compute_sha256 "$live_path")"
        mirror_sha="$(_fetch_lib::compute_sha256 "$mirror_path")"
        if [[ "$live_sha" != "$mirror_sha" ]]; then
            echo "fetch_lib::write_dated_mirror: $mirror_path exists with different SHA" >&2
            echo "  existing: $mirror_sha" >&2
            echo "  current:  $live_sha" >&2
            echo "  drift event — refuse to overwrite (same-day re-fetch produced a different SHA-256)" >&2
            return 1
        fi
        echo "fetch_lib::write_dated_mirror: $mirror_path already exists with matching SHA" >&2
        return 0
    fi

    cp "$live_path" "$mirror_path"
    _fetch_lib::write_sidecar "$mirror_path"
}

# fetch_lib::append_sources_row <repo_relative_path> <license> <url> <description>
# Atomically append a SOURCES.md row for <repo_relative_path>. Reads SHA-256
# from <repo_relative_path>.sha256 sidecar; uses today's UTC date as Retrieved.
# If a row for <repo_relative_path> already exists, verifies all columns match
# and returns 0 (no-op); on mismatch, prints diff and returns 1.
# Wraps read-modify-write in flock SOURCES.md.lock (30s timeout) so concurrent
# appenders serialise.
fetch_lib::append_sources_row() {
    local rel_path="$1"
    local license="$2"
    local url="$3"
    local description="$4"

    local sources_md="$_FETCH_LIB_REPO_ROOT/SOURCES.md"
    local lock_file="$_FETCH_LIB_REPO_ROOT/SOURCES.md.lock"
    local abs_path="$_FETCH_LIB_REPO_ROOT/$rel_path"

    if [[ ! -f "$abs_path" ]]; then
        echo "fetch_lib::append_sources_row: $abs_path missing" >&2
        return 1
    fi
    if [[ ! -f "$sources_md" ]]; then
        echo "fetch_lib::append_sources_row: SOURCES.md missing at $sources_md" >&2
        return 1
    fi

    local sha
    sha="$(_fetch_lib::read_sidecar "$abs_path")" || return 1

    local retrieved
    retrieved="$(date -u +%Y-%m-%d)"

    # Description column may contain pipes — escape to keep the table well-formed.
    local desc_escaped="${description//|/\\|}"
    local new_row="| $rel_path | $license | $url | $retrieved | $sha | $desc_escaped |"

    local rc=0
    (
        flock --exclusive --timeout 30 9 || {
            echo "fetch_lib::append_sources_row: timed out waiting for $lock_file (30s)" >&2
            echo "  if a process died holding the lock, run: rm '$lock_file'" >&2
            exit 1
        }

        if grep -Fq "| $rel_path |" "$sources_md"; then
            local existing
            existing="$(grep -F "| $rel_path |" "$sources_md")"
            if [[ "$existing" == "$new_row" ]]; then
                echo "fetch_lib::append_sources_row: row for $rel_path already exists and matches" >&2
                exit 0
            fi
            echo "fetch_lib::append_sources_row: row mismatch for $rel_path" >&2
            echo "  existing: $existing" >&2
            echo "  new:      $new_row" >&2
            exit 1
        fi

        # Ensure the file ends with a newline before appending.
        if [[ -s "$sources_md" ]] && [[ "$(tail -c 1 "$sources_md")" != $'\n' ]]; then
            printf '\n' >> "$sources_md"
        fi
        printf '%s\n' "$new_row" >> "$sources_md"
        echo "fetch_lib::append_sources_row: appended row for $rel_path" >&2
    ) 9>"$lock_file" || rc=$?
    return $rc
}

# fetch_lib::verify_sidecar <path>
# Recompute SHA-256 of <path>, compare to <path>.sha256. Returns 0 on match,
# 1 on mismatch / missing file / missing sidecar; logs a diff on mismatch.
fetch_lib::verify_sidecar() {
    local f="$1"
    if [[ ! -f "$f" ]]; then
        echo "fetch_lib::verify_sidecar: $f missing" >&2
        return 1
    fi

    local expected computed
    expected="$(_fetch_lib::read_sidecar "$f")" || return 1
    computed="$(_fetch_lib::compute_sha256 "$f")"

    if [[ "$expected" != "$computed" ]]; then
        echo "fetch_lib::verify_sidecar: SHA-256 mismatch for $f" >&2
        echo "  expected (sidecar): $expected" >&2
        echo "  computed (on disk): $computed" >&2
        return 1
    fi
    return 0
}
