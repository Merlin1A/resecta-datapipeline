# `_fetch_lib.sh` — the shared fetcher library

Shared bash library sourced by the ten `scripts/fetch_*.sh` wrappers that
append `SOURCES.md` rows. The other five run without it: `fetch_paranames.sh`
and the HUD and court-glossary fetchers, whose rows are pinned by hand, and
two that download nothing (`fetch_census_spanish.sh` prints instructions;
`fetch_finra_members.sh` is a parked stub). It distils the patterns the ten
implement:

- live HTTP probe (no silent degraded retrieve)
- SHA-256 capture-and-commit
- dated mirror writer (used by `fetch_gsa_agencies.sh` only)
- `SOURCES.md` row appender (atomic via `flock(1)`, which macOS lacks)
- idempotency guard on the `SOURCES.md` row (same-day only — see `append_sources_row`)

## Source pattern (top of every fetcher built on the library)

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"
```

The lib refuses to be executed directly (`exit 2`) — it must be sourced.

## End-of-fetcher pattern

```bash
fetch_lib::probe_url "$URL"
fetch_lib::download_with_sha "$URL" "$DEST_FILE"
fetch_lib::append_sources_row "<rel-path>" "<license>" "$URL" "<description>"
```

`fetch_gsa_agencies.sh` also calls `fetch_lib::write_dated_mirror` between
download and append. Other chains pin via vintage in URL or via
SOURCES.md retrieved-date alone.

## Public API

### `fetch_lib::probe_url <url>`
HEAD-probe `<url>` over HTTPS. Returns 0 on HTTP 200/206/301/302; returns 1 on
any other status, a cert error, a DNS failure or the 30-second timeout. The
two 3xx codes pass because the real download follows redirects. The rule is
halt and report — no silent degraded retrieve.

### `fetch_lib::download_with_sha <url> <dest> [<ua>]`
Downloads `<url>` to `<dest>` with `curl --fail --location`, HTTPS only
including every redirect (`--proto '=https' --proto-redir '=https'`), with a
600-second cap. Captures SHA-256 into `<dest>.sha256` (single 64-char hex line,
LF-terminated). Refuses to overwrite `<dest>`: on a cache hit it returns
`verify_sidecar`'s result (0 on a match, 1 on a mismatch), or writes a sidecar
if absent and returns 0. Default UA = `Wget/1.21` (the one checked against the
SSA host).

### `fetch_lib::write_dated_mirror <live_path> <mirror_dir> <prefix>`
Writes a dated copy of `<live_path>` at
`<mirror_dir>/<prefix>-YYYY-MM-DD.<ext>` (UTC date) plus a sidecar.
Refuses to overwrite an existing dated mirror with a different SHA-256
(same-day drift).

### `fetch_lib::append_sources_row <rel_path> <license> <url> <description>`
Atomically appends a 6-column row to `SOURCES.md`:
`| <rel_path> | <license> | <url> | <YYYY-MM-DD UTC> | <sha256> | <desc> |`.
Reads SHA-256 from `<rel_path>.sha256`. If a row for `<rel_path>` already
exists and matches byte-for-byte: returns 0 (no-op). The new row carries
today's UTC date, so a re-fetch is a no-op only on the row's Retrieved date;
on a later day it reports a mismatch (prints both rows) and returns 1.
Wraps read-modify-write in `flock SOURCES.md.lock` (30 s timeout) so
concurrent appenders serialise; a held lock times the next appender out and
frees when its holder exits. The lock file is gitignored.
Pipes inside `<description>` are escaped to `\|` so the markdown table
remains well-formed.

### `fetch_lib::verify_sidecar <path>`
Recomputes SHA-256 of `<path>`; compares to `<path>.sha256`. Returns 0 on
match, 1 on mismatch / missing file / missing sidecar / malformed sidecar.
Used on a cache hit to surface on-disk corruption before
`append_sources_row`; upstream drift shows up there, as a row mismatch.

## Exit-code conventions

All public helpers return 0 on success and 1 on a recoverable failure with a
log line on stderr. `_fetch_lib.sh` itself exits 2 if a caller `bash`-runs
it instead of sourcing it.

## Citation discipline

The `<description>` and `<url>` arguments to `append_sources_row` cite
**ingestion-of-record only** — the upstream the build pipeline actually
fetches. Do not cite original-of-original sources or historical-derivation
lineages.

## Manually curated files

This lib never touches the reviewed `negative_context.json`, the calibrated
`preset_thresholds.json` and `doctype_temperature.json`, the Swift dumps under
`build/calibration/`, or `asset_hashes.lock`. None of them is in any fetcher's
path.
