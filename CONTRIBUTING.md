# Contributing to resecta-data

Thanks for your interest. This is the build-time data pipeline for the Resecta
iOS app: it produces name Bloom filters, gazetteers, classifier dictionaries,
calibration vectors, and test corpora, then copies them into the engine's
`Resources/` tree. This document covers the workflow and the invariants that
keep generated artifacts license-clean and reproducible, including the
changes that need a written plan approved by the maintainer before the edit —
see "Plan-sign-off changes" below for the full list.

## Setup

Python 3.12 is required.

```sh
scripts/bootstrap.sh     # create .venv, install hash-pinned deps from both lockfiles
```

On macOS, install GNU Make 4.x (`brew install make`) and invoke targets as
`gmake` — stock `/usr/bin/make` is 3.81 and the `verify` recipe needs make ≥ 4.

## Build and verify

```sh
gmake build      # generate all artifacts into build/ (no network)
gmake verify     # ruff, mypy, pytest, schema validation, hash + determinism check
gmake all        # build + verify + install-assets
```

`scripts/ci_verify.sh` runs the same sequence for a local smoke check. Remote
CI runs on every pull request (the hermetic `ci.yml` gate) and weekly (the
full verify with fetched sources); `gmake verify` (or `ci_verify.sh`) stays
the local gate before any change ships. A scheduled job refreshes a
`ci-keepalive` side branch when `main` has been quiet for ~50 days, so the
weekly workflows never lapse into the platform's scheduled-run auto-disable.

### Environment notes

`gmake doctor` prints a read-only health summary: the host and venv Python
versions, whether the ParaNames corpus is fetched (path, size, `hydrated:
yes|no`), the `asset_hashes.lock` mtime, the `build/` size, file count and
stamp state, the make and venv freshness, the determinism witness, the
lock / out-of-band and bundle-size parity checks, the calibration dumps, the
signing key, the ingest cache and any stale worker processes. It runs on
macOS as well as Linux (the size and mtime probes try BSD `stat -f` first,
then GNU `stat -c`).

The fetch chains (`scripts/fetch_*.sh`) need Linux: `scripts/_fetch_lib.sh`
serialises the `SOURCES.md` row append with `flock`, which macOS does not
ship — see `scripts/_fetch_lib.README.md`. Everything else, including the
full build and verify, runs on either.

`gmake verify`'s determinism check rebuilds every artifact and diffs it. A
cold run costs about 44–55 minutes on an M1 Pro laptop (the figure
`scripts/ci_verify.sh` carries; CI forces this mode). The witness stamp under
`build/.stamps/` records the checked input closure, so a warm re-run on
unchanged inputs takes seconds; `RESECTA_FORCE_DETERMINISM=1` bypasses it.

## Invariants

These are non-negotiable; the test suite enforces them.

- **Determinism.** Every artifact is byte-identical across machines and
  rebuilds. Seeds are explicit (canonical seed `20260416`); no wall-clock
  content; artifact JSON is written only through
  `common/io.py::dump_canonical_json`. Every new builder ships a determinism
  test.
- **License provenance.** Every file under `src/resecta_data/*/sources/` needs a
  row in `SOURCES.md` (license, URL, retrieval date, SHA-256). Adding a dataset
  whose license is not on `common/licensing.py`'s `ALLOWLIST` is a
  plan-sign-off change.
- **Zero-network builds.** `make build` never reaches the network; only
  `make sources` fetches, and it validates hashes. ParaNames is fetched on
  demand via `scripts/fetch_paranames.sh` (not committed; no Git-LFS), and the
  build degrades to the bootstrap sample when the full corpus is absent.
- **Mechanism-description language.** Any human-readable string this pipeline
  emits (docstrings, JSON `description` fields, `NOTICE.txt` rows, error
  messages) describes the mechanism, not an outcome. The banned-phrase list is
  in `common/mechanism_language.py`.

## Structure

`src/resecta_data/cli.py` holds the click registration only. A new subcommand
or eval stage lands as a module under `src/resecta_data/<package>/` — a
builder beside its siblings, an eval stage under `eval/` — and `cli.py` gains
the command that parses the options and calls it, not the stage's logic. The
three eval commands whose loading, wiring and reporting still sit inline in
`cli.py` (`build eval-documents`, `build eval-compare-documents`,
`build eval-sitegap`) are the counter-example, kept until the file is split.
`tests/test_cli.py::test_cli_line_count_does_not_grow` pins the file's line
count at the value it had when this rule landed; the pin is lowered when the
file is split, never raised.

## Commit format and sign-off

Commit subjects describe the mechanism a change introduces (verbs like `add`,
`extend`, `seed`, `amend`). Every commit needs a DCO sign-off under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):

```sh
git commit -s -m "add <thing>"
```

If `asset_hashes.lock` changes, the commit body must explain why — an
unexplained hash change usually signals a determinism defect, not a reason to
regenerate.

## Plan-sign-off changes

Curated context assets change only under a written change plan approved by
the maintainer before the edit — the asset, the rows or fields, the reason,
and the regeneration and verification steps. No row-by-row review afterwards.
The sidecar drift check stays as a mechanical tripwire the same change
re-stamps.

The same posture covers every change in this list:

- the negative-context candidates (`gazetteers/negative_context/sources/scope_rules_v1.json`)
  and the reviewed `negative_context.json` with its sidecar;
- the context-keyword candidates (`context/sources/d12_candidates.json`,
  `context/sources/d16_bates_anchors.json`,
  `gazetteers/context_keywords/sources/d11_lift_candidates.json`);
- the doctype-keyword seeds (`classifier/_keyword_data.py`);
- the three classifier quality assets — the context scorer, the doctype
  temperature and the preset thresholds — including the `calibrate-finalize`
  promotion;
- license-compatibility judgments and dependency additions;
- legal-text authorship (`NOTICE.txt` rows);
- any release or lockfile decision.

The PR review confirms the plan was carried out.

## Source hygiene

Shipped source, docstrings and emitted strings describe mechanisms — what a
profile is, what a clause computes — never the private planning documents
that scheduled the work. Register identifiers (`C12-nn`, `D12-nn`, `M12-nn`,
`F12-nn`, `RB12-nn`, spec-item labels such as `[Rnn]` and session names) do
not appear in `src/` or `scripts/`; cite the register in the pull-request
body instead. `scripts/hygiene_gate.py` enforces this in `make lint`, and so
on every pull request: it scans every `.py` file under `src/` and `scripts/`
for the identifier shape and fails on any hit not covered by
`scripts/hygiene_allowlist.txt`. The allowlist carries the public tokens the
shape collides with (IRS form names such as `W-2` and `W-9`) and, with a
reason on the row, the rare line that must keep one (an emitted string a test
and the hash lock both pin). Generator-profile names (`g8-specC`, the Spec-D
axis) are product vocabulary, not planning ids.

`make lint` also checks that the two generated README blocks are current: the
Makefile-targets block (`make readme-targets` regenerates it from `make help`)
and the ETL stage map (`make graph` regenerates it from the make database).

## Security

Vulnerability disclosure goes through [`SECURITY.md`](./SECURITY.md), not the
public issue tracker.
