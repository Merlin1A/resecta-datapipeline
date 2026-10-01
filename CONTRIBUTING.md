# Contributing to resecta-data

The build-time data pipeline for the Resecta iOS app; what it produces and how
it is verified is in [`README.md`](./README.md). Maintainer-run: issues are
welcome, outside pull requests are not expected in the near term. This file
lists the checks a change must pass and the changes that need a written plan.

## Setup

Python 3.12 and GNU make ≥ 4 (`brew install make` on macOS; invoke every
target as `gmake`). `scripts/bootstrap.sh` creates `.venv/` and installs the
dependencies hash-verified from `requirements.lock` and
`requirements-dev.lock`.

## Checks a change must pass

Changes reach `main` by pull request; the `gate` job of
`.github/workflows/ci.yml` is the required check. What it runs, what the
weekly workflow adds and what `gmake verify` does locally are described once,
in the README's "Verification" section. `install-assets` requires the full
`gmake verify`; `verify-fast` is for the dev loop only. Beyond that:

- `scripts/hygiene_gate.py` fails `make lint` on a planning-identifier shape in
  a `.py` file under `src/` or `scripts/` (exemptions, each with its reason:
  `scripts/hygiene_allowlist.txt`); other files are reviewed by hand.
- `gmake readme-targets` and `gmake graph` regenerate the two README blocks
  `make lint` checks for currency.
- `tests/test_cli.py` caps `src/resecta_data/cli.py` at 127 lines, never
  raised; `.venv/bin/python tests/test_cli_help_golden.py --write` refreshes
  the pinned `--help` text.
- The tests need `PYTHONHASHSEED=0` (`gmake test` sets it) and run with
  sockets to anything but loopback disabled.
- After a dependency change, run `scripts/freeze_deps.sh` and `uv lock`; CI's
  `uv lock --check` fails when `uv.lock` is stale against `pyproject.toml`.

## Invariants

- **Determinism.** Every `make build` artifact that `asset_hashes.lock` pins
  is byte-identical across machines and rebuilds: explicit seeds (canonical
  seed `20260416`), no wall-clock content, artifact JSON written only through
  `common/io.py::dump_canonical_json`. If `asset_hashes.lock` moves, the
  commit body says why. Enforced by `gmake verify`.
- **No network in builders or tests.** Raw inputs are fetched in a separate
  step (`SECURITY.md`, "Supply-chain posture"). pytest's socket ban enforces
  it for the tests; the builders import no network library.
- **License provenance.** A third-party raw file the builders read under
  `src/resecta_data/**/sources/` has a `SOURCES.md` row; the rules are
  `common/licensing.py`'s `ALLOWLIST` and `FORBIDDEN` sets and its `GATED`
  map. Checked in review.
- **Mechanism-description language.** The strings the pipeline emits
  (docstrings, JSON `description` fields, `NOTICE.txt` rows, error messages)
  describe the mechanism, not an outcome; the banned-phrase list is
  `common/mechanism_language.py`. Checked in review; the classifier,
  negative-corpus and eval builders also run the scanner on the free-form
  strings they emit, and `tests/test_phase2_mechanism_language.py` runs it
  over the schemas and modules it names.

## Structure

`src/resecta_data/cli.py` defines the click groups and registers each command
module; `src/resecta_data/commands/<family>.py` defines the options and calls
into the package module, where the logic lives beside its siblings.

## Plan-sign-off changes

Curated context assets change only under a written change plan approved by the
maintainer before the edit — the asset, the rows or fields, the reason, and
the regeneration and verification steps. The pull-request review checks that
the plan was carried out, not each row. The same posture covers (source paths
are under `src/resecta_data/`):

- the negative-context scope rules
  (`gazetteers/negative_context/sources/scope_rules_v1.json`) and the reviewed
  `negative_context.json` with its sidecar — `stage-reviewed-negctx`, an
  `install-assets` prerequisite, refuses when the built candidates no longer
  match the hash the sidecar records;
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

Fetchers built on `scripts/_fetch_lib.sh` need `flock` (Linux); the others,
`scripts/fetch_paranames.sh` among them, do not
(`scripts/_fetch_lib.README.md`).

## Sign-off and license

Apache-2.0. A DCO sign-off (`git commit -s`) is asked of external
contributions.

## Security

Vulnerability disclosure goes through [`SECURITY.md`](./SECURITY.md), not the
public issue tracker.
