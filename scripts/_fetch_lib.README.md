# `_fetch_lib.sh` — the shared fetcher library

Shared bash library sourced by the ten `scripts/fetch_*.sh` wrappers that
append `SOURCES.md` rows; the other five (`fetch_paranames.sh` among them) pin
their rows by hand and run without it. It distils the patterns those fetchers
implement:

- live HTTP probe (no silent degraded retrieve)
- SHA-256 capture-and-commit
- dated mirror writer (used by `fetch_gsa_agencies.sh` only)
- `SOURCES.md` row appender (atomic via `flock`)
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
4xx/5xx, a cert error, a DNS failure or the 30-second timeout. The 3xx codes
pass because the real download follows redirects. The rule is halt and report
on 4xx/5xx, a cert error or a timeout — no silent degraded retrieve.

### `fetch_lib::download_with_sha <url> <dest> [<ua>]`
Downloads `<url>` to `<dest>` with `curl --fail --location`, HTTPS only
including every redirect (`--proto '=https' --proto-redir '=https'`), with a
600-second cap. Captures SHA-256 into `<dest>.sha256` (single 64-char hex line,
LF-terminated). Refuses to overwrite `<dest>`: on a cache hit it calls
`verify_sidecar`, or writes a sidecar if absent, and returns 0. Default UA =
`Wget/1.21` (the SSA host refuses curl's default).

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
concurrent appenders serialise. The lock file is
gitignored; if a stale lock blocks the helper, `rm SOURCES.md.lock` clears it.
Pipes inside `<description>` are escaped to `\|` so the markdown table
remains well-formed.

### `fetch_lib::verify_sidecar <path>`
Recomputes SHA-256 of `<path>`; compares to `<path>.sha256`. Returns 0 on
match, 1 on mismatch / missing file / missing sidecar / malformed sidecar.
Used by re-runs to surface on-disk corruption or upstream drift (same-day
drift) before `append_sources_row`.

## Exit-code conventions

All public helpers return 0 on success and 1 on a recoverable failure with a
log line on stderr. `_fetch_lib.sh` itself returns 2 if a caller `bash`-runs
it instead of sourcing it.

## Citation discipline

The `<description>` and `<url>` arguments to `append_sources_row` cite
**ingestion-of-record only** — the upstream the build pipeline actually
fetches. Do not cite original-of-original sources or historical-derivation
lineages.

## Manually curated files

This lib never touches `negative_context.json`, `preset_thresholds.json`,
`doctype_temperature.json`, `calibration/*`, or `asset_hashes.lock`. Those
are curated by hand and are not in any fetcher's path.
