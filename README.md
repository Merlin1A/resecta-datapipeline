# Resecta Data Pipeline

[![ci](https://github.com/Merlin1A/resecta-datapipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/Merlin1A/resecta-datapipeline/actions/workflows/ci.yml)
[![verify](https://github.com/Merlin1A/resecta-datapipeline/actions/workflows/verify.yml/badge.svg)](https://github.com/Merlin1A/resecta-datapipeline/actions/workflows/verify.yml)

Build-time Python tooling that produces the data assets shipped inside the Resecta iOS app — name Bloom filters, gazetteers, classifier keyword dictionaries, test corpora, and CI fixtures.

This repository is independent of the Xcode project. Nothing here is linked into the iOS binary; the pipeline's only coupling to the app is the `make install-assets` step that copies generated files into `../resecta/Packages/RedactionEngine/Sources/RedactionEngine/Resources/` (the sibling `resecta` iOS repository).

**Contributor workflow and invariants are in [`CONTRIBUTING.md`](./CONTRIBUTING.md). Read that before making changes.**

---

## Quickstart

```
# One-time setup
scripts/bootstrap.sh

# Build everything
make all

# Build and verify without copying into the Swift tree
make build verify

# Copy built artifacts into the engine Resources path
make install-assets

# Clean build outputs (leaves raw sources alone)
make clean
```

Python 3.12 is required. The bootstrap script creates a local venv at `.venv/`, installs pinned dependencies from `requirements.lock` and `requirements-dev.lock`, and installs this package in editable mode. On macOS, install GNU Make 4.x (`brew install make`) and invoke targets as `gmake` — stock `/usr/bin/make` (3.81) cannot run the `verify` recipe.

---

## Layout

```
resecta-datapipeline/
├── Makefile               all build targets
├── pyproject.toml         pinned dependencies, tool configs
├── requirements.lock      pip-compile output
├── asset_hashes.lock      sha256 of every generated artifact
├── schemas/               JSON Schema for every output file
├── scripts/
│   ├── bootstrap.sh       first-time setup
│   ├── freeze_deps.sh     regenerate both lockfiles
│   └── ci_verify.sh       local verify mirror (lint / type / test / build)
├── src/resecta_data/
│   ├── common/            io, determinism, licensing, exceptions
│   ├── bloom/             Bloom filter builder (Phase 2)
│   ├── gazetteers/        negative/positive context, ZIP→SCF
│   ├── classifier/        doctype keywords, temperature fit, threshold sweep
│   ├── corpus/            G8 synthetic corpus generator
│   ├── vectors/           NPI / DEA / SSN structural test vectors
│   ├── fuzz/              ReDoS payload generator
│   └── cli.py             click-based entry points
├── tests/                 pytest test suite
└── build/                 generated artifacts (git-ignored)
```

---

## Makefile targets

| Target | Purpose | Network |
|---|---|---|
| `bootstrap` | Create venv, install pinned deps | Yes (pip) |
| `sources` | Fetch raw datasets into `src/*/sources/` | Yes, validates SHA-256 |
| `build` | Generate all artifacts into `build/` | No |
| `verify` | Run ruff, mypy, pytest, schema validation, hash check, determinism check | No |
| `install-assets` | Copy artifacts from `build/` to Swift Resources path | No |
| `bloom` | Build only the Bloom filter (Phase 2) | No |
| `gazetteers` | Build only the gazetteers (Phase 2) | No |
| `classifier` | Build only classifier assets (Phase 3) | No |
| `corpus` | Build only the G8 test corpus (Phase 3) | No |
| `vectors` | Build only NPI/DEA/SSN test vectors (Phase 1) | No |
| `fuzz` | Build only ReDoS fuzz payloads (Phase 1) | No |
| `clean` | Remove `build/`, preserve `sources/` | No |
| `distclean` | Remove `build/`, `.venv/`, caches | No |

`make all` is equivalent to `make build verify install-assets`.

---

## ParaNames (fetch-on-demand)

The large ParaNames corpus (`paranames_full.tsv.gz`, ~953 MB) is **not committed**, and the repo uses **no Git-LFS**. `scripts/fetch_paranames.sh` downloads it on demand into `src/resecta_data/gazetteers/sources/paranames/` and validates its SHA-256 against `SOURCES.md`. When the full corpus is absent, the Bloom builders degrade to the committed bootstrap sample (`paranames_bootstrap_*.tsv`) so the build still runs; the full corpus is only needed to reproduce the shipped name filters exactly.

> Reproducing the shipped name filters: `gmake verify` hash-checks the built Bloom filters against `asset_hashes.lock`, whose hashes were produced from the full ParaNames corpus. Run `scripts/fetch_paranames.sh` before `gmake verify`. A bare clean-clone `gmake verify` is expected to fail hash-check (bootstrap-only Bloom != full-corpus lock) — this is the no-LFS / fetch-on-demand design, not a regression.

---

## Verification

Every pull request runs a hermetic gate on a hosted runner (`ci.yml`): `ruff check`, `ruff format --check`, `mypy`, `pytest`, the pure-code builders, schema validation, and a hash check of everything built. A weekly `verify.yml` run hydrates the large fetched sources (SHA-256-validated, cached) and runs the full verify sequence, and `security.yml` audits the locked dependency set. Locally, `make verify` (use `gmake` on macOS) remains the gate: it runs `ruff check`, `ruff format --check`, `mypy`, `pytest`, schema validation, hash-lock verification, and a determinism rebuild; `scripts/ci_verify.sh` runs the same sequence as a local smoke check.

---

## Licensing

Every dataset under `src/*/sources/` has a row in `SOURCES.md` with its license, retrieval URL, retrieval date, and SHA-256. The bundled `NOTICE.txt` (hand-maintained at the repo root, not generated by `make install-assets`) aggregates these for the iOS app.

See `common/licensing.py`'s `ALLOWLIST`/`GATED`/`FORBIDDEN` sets for the license allowlist and the datasets currently gated on legal review.
