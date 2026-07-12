# `_fetch_lib.sh` — CC-SCRIPT fetcher API

Shared bash library sourced by every `scripts/fetch_*.sh` wrapper.
It distils the patterns every fetcher implements:

- live HTTP probe (no silent degraded retrieve)
- SHA-256 capture-and-commit
- dated mirror writer (D-01 only in V1)
- `SOURCES.md` row appender (atomic via `flock`)
- idempotency / re-fetch-as-no-op guard

## Source pattern (top of every CC-SCRIPT fetcher)

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

D-01 also calls `fetch_lib::write_dated_mirror` between download and append,
per F-19. Other chains pin via vintage in URL or via SOURCES.md
retrieved-date alone.

## Public API

### `fetch_lib::probe_url <url>`
HEAD-probe `<url>`. Returns 0 on HTTP 200/206/301/302; returns 1 on 4xx/5xx,
cert error, DNS failure, or 30-second timeout. The 3xx codes pass because the
real download follows redirects via `curl --location`. Per spec §2.1
"halt+report on 404/301/cert error (no silent degraded retrieve)".

### `fetch_lib::download_with_sha <url> <dest> [<ua>]`
Downloads `<url>` to `<dest>` via `curl --fail --silent --show-error
--location`. Captures SHA-256 into `<dest>.sha256` (single 64-char hex line,
LF-terminated) per F-1. Refuses to overwrite `<dest>` —
on cache-hit, calls `verify_sidecar` (or writes a sidecar if absent) and
returns 0. Default UA = `Wget/1.21` (proven against ssa.gov per
`fetch_ssa_baby_names.sh:42-46`).

### `fetch_lib::write_dated_mirror <live_path> <mirror_dir> <prefix>`
Writes a dated copy of `<live_path>` at
`<mirror_dir>/<prefix>-YYYY-MM-DD.<ext>` (UTC date) plus a sidecar.
Refuses to overwrite an existing dated mirror with a different SHA-256
(Failure Mode #2 — drift between same-day re-fetches). Per F-19 generalised:
**only D-01 calls this in V1**.

### `fetch_lib::append_sources_row <rel_path> <license> <url> <description>`
Atomically appends a 6-column row to `SOURCES.md`:
`| <rel_path> | <license> | <url> | <YYYY-MM-DD UTC> | <sha256> | <desc> |`.
Reads SHA-256 from `<rel_path>.sha256`. If a row for `<rel_path>` already
exists and matches: returns 0 (no-op). On mismatch: prints diff and returns 1.
Wraps read-modify-write in `flock SOURCES.md.lock` (30 s timeout) so
concurrent appenders serialise. The lock file is
gitignored; if a stale lock blocks the helper, `rm SOURCES.md.lock` clears it.
Pipes inside `<description>` are escaped to `\|` so the markdown table
remains well-formed.

### `fetch_lib::verify_sidecar <path>`
Recomputes SHA-256 of `<path>`; compares to `<path>.sha256`. Returns 0 on
match, 1 on mismatch / missing file / missing sidecar / malformed sidecar.
Used by re-runs to catch on-disk corruption or upstream drift (Failure
Mode #2) before `append_sources_row`.

## Exit-code conventions

All public helpers return 0 on success and 1 on a recoverable failure with a
log line on stderr. `_fetch_lib.sh` itself returns 2 if a caller `bash`-runs
it instead of sourcing it.

## Citation discipline

The `<description>` and `<url>` arguments to `append_sources_row` cite
**ingestion-of-record only** — the upstream the build pipeline actually
fetches. Do not cite original-of-original sources or historical-derivation
lineages; `SOURCES.md` records the ingestion-of-record source only.

## Hand-review files

This lib never touches `negative_context.json`, `preset_thresholds.json`,
`doctype_temperature.json`, `calibration/*`, or `asset_hashes.lock`. Those
remain hand-review-only and are not in any fetcher's path.
