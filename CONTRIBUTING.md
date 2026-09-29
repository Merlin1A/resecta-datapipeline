# Contributing to resecta-data

The build-time data pipeline for the Resecta iOS app; what it produces and how
it is verified is in [`README.md`](./README.md). Maintainer-run: issues are
welcome, outside pull requests are not expected in the near term. This file
lists the checks a change must pass and the changes that need a written plan.

## Setup

Python 3.12 and GNU make ≥ 4 (`brew install make` on macOS; invoke every
target as `gmake`). `scripts/bootstrap.sh` creates `.venv/` from the two
hash-pinned lockfiles.

## Checks a change must pass

| When | What runs |
|---|---|
| Every pull request and push to `main` (`.github/workflows/ci.yml`) | `uv lock --check` · the personal-e-mail guard (`scripts/check_no_pii.py`) · `make lint` (ruff check and format · `scripts/hygiene_gate.py` · `scripts/readme_targets.py --check` · `scripts/etl_graph.py --check`) · `make typecheck` · `make test` · the pure-code builders · `make schema-check-only` · `make hash-check-built-only` |
| Locally, before anything ships | `gmake verify` — one build, then lint, types, tests, schema validation, the `asset_hashes.lock` check and the determinism rebuild; `install-assets` requires it (`verify-fast` skips the rebuild and is the dev loop only) |
| Weekly (`.github/workflows/verify.yml`) | the ParaNames corpus hydrated from its `SOURCES.md` row, then `scripts/ci_verify.sh` with the determinism rebuild forced |

- `scripts/hygiene_gate.py` fails `make lint` on a planning-identifier shape in
  a `.py` file under `src/` or `scripts/` (`scripts/hygiene_allowlist.txt`
  carries the public tokens the shape collides with); other files are reviewed
  by hand.
- `gmake readme-targets` and `gmake graph` regenerate the two README blocks
  `make lint` checks for currency.
- `tests/test_cli.py` caps `src/resecta_data/cli.py` at 127 lines, never
  raised; `python tests/test_cli_help_golden.py --write` refreshes the pinned
  `--help` text.
- After `scripts/freeze_deps.sh` regenerates the two pip lockfiles, run
  `uv lock`; CI checks `uv.lock` against them.

## Invariants

- **Determinism.** Every artifact is byte-identical across machines and
  rebuilds: explicit seeds (canonical seed `20260416`), no wall-clock content,
  artifact JSON written only through `common/io.py::dump_canonical_json`. If
  `asset_hashes.lock` moves, the commit body says why.
- **No network in builders or tests.** Raw inputs are fetched by hand with
  `scripts/fetch_*.sh`, which record each file's SHA-256 in `SOURCES.md` and
  refuse a later fetch whose bytes differ.
- **License provenance.** Every third-party raw file under
  `src/resecta_data/**/sources/` has a `SOURCES.md` row; the rules are
  `common/licensing.py`'s `ALLOWLIST`, `GATED` and `FORBIDDEN` sets.
- **Mechanism-description language.** Every string the pipeline emits
  (docstrings, JSON `description` fields, `NOTICE.txt` rows, error messages)
  describes the mechanism, not an outcome; the banned-phrase list is
  `common/mechanism_language.py`.

The build, `gmake verify` and the tests enforce the first two; license
provenance and mechanism language are checked in review against the two
modules named.

## Structure

`src/resecta_data/cli.py` registers only; `src/resecta_data/commands/<family>.py`
parses the options; the logic lives in the package module beside its siblings.

## Plan-sign-off changes

Curated context assets change only under a written change plan approved by the
maintainer before the edit — the asset, the rows or fields, the reason, and
the regeneration and verification steps. The pull-request review checks that
the plan was carried out, not each row. The same posture covers:

- the negative-context candidates
  (`gazetteers/negative_context/sources/scope_rules_v1.json`) and the reviewed
  `negative_context.json` with its sidecar — `stage-reviewed-negctx`, an
  `install-assets` prerequisite, refuses when the candidates drift from the
  hash the sidecar records;
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

## Fetching sources

Fetchers built on `scripts/_fetch_lib.sh` need Linux `flock`;
`scripts/fetch_paranames.sh` runs on macOS too (`scripts/_fetch_lib.README.md`).

## Sign-off and license

Apache-2.0. A DCO sign-off (`git commit -s`) is asked of external
contributions.

## Security

Vulnerability disclosure goes through [`SECURITY.md`](./SECURITY.md), not the
public issue tracker.
