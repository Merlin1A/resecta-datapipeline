# Resecta DataPipeline Makefile
#
# Build targets are offline. `make sources` is the only target permitted to fetch.

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

# Half-written sentinel files on recipe failure are worse than no sentinel at
# all — they would silently mark a builder as "current" the next time make
# runs. .DELETE_ON_ERROR removes the target's output file when the recipe
# exits non-zero. Cheap defense.
.DELETE_ON_ERROR:

# ---- GNU Make version guard (parse-time) ------------------------------------
# Stock macOS /usr/bin/make is GNU 3.81 (2006). Two things break there:
# --output-sync (4.0+) makes verify's inner make abort on an unrecognized
# option, and .SHELLFLAGS (3.82+) is silently ignored so every recipe runs
# WITHOUT `set -euo pipefail` — a correctness divergence from CI, not just a
# speed problem. The guard must fire at PARSE time: make does NOT order
# order-only prerequisites ahead of normal ones, so a `| check-make` prereq
# on `verify` runs only after the full `build` prereq chain (measured on
# 3.81 — it burned a build before any guard could fire). $(error) below
# aborts before any target runs, only when a guarded goal was requested;
# diagnostic-class targets (help, doctor, lint, test, …) still work on 3.81.
MAKE_GUARDED_GOALS := build build-fast verify verify-fast install-assets all
ifneq ($(filter $(MAKE_GUARDED_GOALS),$(MAKECMDGOALS)),)
ifeq ($(filter 4.% 5.%,$(MAKE_VERSION)),)
$(error GNU Make $(MAKE_VERSION) is too old for '$(filter $(MAKE_GUARDED_GOALS),$(MAKECMDGOALS))' (>= 4.0 required: --output-sync, .SHELLFLAGS pipefail). Install GNU make 4.x with 'brew install make', then invoke as 'gmake <target>')
endif
endif

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

PYTHON         := python3.12
VENV_DIR       := .venv
VENV_BIN       := $(VENV_DIR)/bin
PYTHON_VENV    := $(VENV_BIN)/python
PIP_VENV       := $(VENV_BIN)/pip
PYTEST         := $(VENV_BIN)/pytest
RUFF           := $(VENV_BIN)/ruff
MYPY           := $(VENV_BIN)/mypy
RESECTA_DATA   := $(VENV_BIN)/resecta-data

BUILD_DIR      := build

# -----------------------------------------------------------------------------
# Sentinel-file build infrastructure
# -----------------------------------------------------------------------------
# Each Phase 1 builder writes a sentinel under $(STAMP_DIR)/ via `touch $@`.
# The sentinel's prereqs are the source globs that, if changed, should
# re-trigger the builder; the phony alias (e.g. `vectors`) becomes a
# one-line wrapper pointing at the sentinel so public surface is unchanged.
# .stamps/ lives under $(BUILD_DIR) so `make clean` removes it along with
# artifacts. Declared here (above `build:`) because Make expands variable
# references in target/prereq lines at parse time.
STAMP_DIR := $(BUILD_DIR)/.stamps

# Coarse-grained common deps: any change to shared modules or the CLI entry
# point invalidates every sentinel. The warm-build no-op case is what matters;
# rebuilding all builders when common/io.py changes is acceptable since
# common/ rarely changes. Computed at parse time via $(shell find ...).
COMMON_DEPS := $(shell find src/resecta_data/common -name '*.py' 2>/dev/null) src/resecta_data/cli.py

# ---- Content-keyed stamps ---------------------------------------------------
# Stamps are no longer bare `touch` artifacts: each holds a content manifest
# (sha256 per tracked input) of its own prerequisite closure, computed over
# make's $^ so the covered file list can never drift below the prereq graph.
# When mtime churn (editor save, checkout, rebase) fires a stamp recipe, the
# keyer compares manifests and skips the builder on a byte-identical closure —
# the stamp is re-touched so make stops re-firing, but its CONTENT (which the
# determinism witness keys on, transitively) only changes when real bytes
# changed. Any keyer failure mode reports "changed", i.e. degrades to the old
# always-rebuild behavior, never to a wrong skip. CI's
# RESECTA_FORCE_DETERMINISM=1 path never consults stamp keys.
# See src/resecta_data/common/stamp_key.py.
STAMP_KEY := $(PYTHON_VENV) -m resecta_data.common.stamp_key

# $(call keyed_stamp,<label>,<single-line builder command>) — canned
# compare-and-skip recipe shared by all 17 builder stamps. The builder
# command must be a single logical line and contain no commas.
define keyed_stamp
@mkdir -p $(dir $@)
@if $(STAMP_KEY) check $@ $^; then \
	echo "[stamp] $(1): tracked inputs byte-identical to last build -- builder skipped"; \
else \
	echo "[stamp] $(1): tracked inputs changed -- running builder"; \
	$(2) \
	&& $(STAMP_KEY) write $@ $^; \
fi
@touch $@
endef

# Per-builder source globs (Phase 1).
VECTORS_PY     := $(shell find src/resecta_data/vectors -name '*.py' 2>/dev/null)
FUZZ_PY        := $(shell find src/resecta_data/fuzz -name '*.py' 2>/dev/null)
ZIP_SCF_PY     := $(shell find src/resecta_data/gazetteers/zip_scf -name '*.py' 2>/dev/null)
ADVERSARIAL_PY := $(shell find src/resecta_data/adversarial -name '*.py' 2>/dev/null)

# Per-builder source globs (Phase 2 + Phase 3). Cross-module note:
# gaz-address reads zips from institutions/sources/, and context reads from
# the sibling top-level context/sources/ package. Both are listed below so a
# touch on those inputs invalidates the right stamp without dragging in
# unrelated builders.
BLOOM_PY              := $(shell find src/resecta_data/bloom -name '*.py' 2>/dev/null)
BLOOM_NAME_CORPORA    := $(shell find src/resecta_data/gazetteers/sources/ssa_given_names src/resecta_data/gazetteers/sources/census_surnames src/resecta_data/gazetteers/sources/census_spanish src/resecta_data/gazetteers/sources/paranames src/resecta_data/gazetteers/sources/popnames -type f 2>/dev/null)
GAZ_NEGCTX_PY         := $(shell find src/resecta_data/gazetteers/negative_context -name '*.py' 2>/dev/null)
GAZ_NEGCTX_SOURCES    := $(wildcard src/resecta_data/gazetteers/sources/negative_context/*.txt)
GAZ_INSTITUTIONS_PY   := $(shell find src/resecta_data/gazetteers/institutions -name '*.py' 2>/dev/null)
GAZ_INSTITUTIONS_SOURCES := $(wildcard src/resecta_data/gazetteers/institutions/sources/gsa_federal_agencies_*.csv) src/resecta_data/gazetteers/institutions/sources/federalregister_agencies.json $(wildcard src/resecta_data/gazetteers/institutions/sources/fdic_institutions_*.csv) $(wildcard src/resecta_data/gazetteers/institutions/sources/edgar_company_tickers_*.json)
GAZ_ADDRESS_PY        := $(shell find src/resecta_data/gazetteers/address_components -name '*.py' 2>/dev/null)
GAZ_ADDRESS_SOURCES   := $(wildcard src/resecta_data/gazetteers/institutions/sources/usgs_gnis_pop_places_*.zip) $(wildcard src/resecta_data/gazetteers/institutions/sources/census_counties_*.zip) $(wildcard src/resecta_data/gazetteers/address_components/sources/tiger_places/*.zip)
GAZ_NICKNAMES_PY      := $(shell find src/resecta_data/gazetteers/nicknames -name '*.py' 2>/dev/null)
GAZ_NICKNAMES_SOURCES := $(wildcard src/resecta_data/gazetteers/nicknames/sources/nicknames_cc0_*.csv)
# The nicknames sidecar joins the default build only once its CC0 raw
# source has been fetched on a Linux host (scripts/fetch_nicknames.sh — the
# fetch chain needs flock). Before that the builder fails loud on the missing
# source, so the stamp depends on source presence (same shape as the zip-scf census-source gate below).
ifneq ($(GAZ_NICKNAMES_SOURCES),)
GAZ_NICKNAMES_STAMP := $(STAMP_DIR)/gaz-nicknames
else
GAZ_NICKNAMES_STAMP :=
endif
PASSPORT_PATTERNS_PY  := $(shell find src/resecta_data/gazetteers/passport_patterns -name '*.py' 2>/dev/null)
PASSPORT_PATTERNS_SOURCES := $(wildcard src/resecta_data/gazetteers/passport_patterns/sources/*.json)
DL_PATTERNS_PY        := $(shell find src/resecta_data/gazetteers/dl_patterns -name '*.py' 2>/dev/null)
DL_PATTERNS_SOURCES   := $(wildcard src/resecta_data/gazetteers/dl_patterns/sources/*.json)
CONTEXT_PY            := $(shell find src/resecta_data/gazetteers/context_keywords -name '*.py' 2>/dev/null)
CONTEXT_SOURCES       := $(wildcard src/resecta_data/gazetteers/context_keywords/sources/*.json) $(wildcard src/resecta_data/context/sources/*.json)
RULES_PY              := $(shell find src/resecta_data/rules -name '*.py' 2>/dev/null)
RULES_SOURCES         := $(wildcard src/resecta_data/rules/sources/*.json)
DEMOGRAPHICS_PY       := $(shell find src/resecta_data/demographics -name '*.py' 2>/dev/null)
CLASSIFIER_PY         := $(shell find src/resecta_data/classifier -name '*.py' 2>/dev/null)
CORPUS_PY             := $(shell find src/resecta_data/corpus -name '*.py' 2>/dev/null)
INSTRUMENTATION_PY    := $(shell find src/resecta_data/instrumentation -name '*.py' 2>/dev/null)

# Phase 3b calibration inputs. Produced out-of-band by a Swift test target
# and dropped into $(CALIBRATION_DIR). The `calibrate-*`
# targets check for these files and fail with a clear pointer if absent.
CALIBRATION_DIR := $(BUILD_DIR)/calibration
SOFTMAX_DUMP    := $(CALIBRATION_DIR)/softmax_dump.json
SCORE_DUMP      := $(CALIBRATION_DIR)/detector_score_dump.json

SCHEMAS_DIR     := schemas

# Swift engine paths (relative to this Makefile). The sibling checkout is
# lowercase `../resecta`; the old `../Resecta` spelling resolved only via
# case-insensitive APFS (speed-plan N3/#20). Override RESECTA_IOS_ROOT for
# checkouts living elsewhere.
RESECTA_IOS_ROOT ?= ../resecta
SWIFT_RESOURCES := $(RESECTA_IOS_ROOT)/Packages/RedactionEngine/Sources/RedactionEngine/Resources
SWIFT_FIXTURES  := $(RESECTA_IOS_ROOT)/Packages/RedactionEngine/Tests/RedactionEngineTests/Fixtures

# Canonical seed for all deterministic generators.
RESECTA_SEED   := 20260416

# PYTHONHASHSEED must be 0 for deterministic dict iteration in builders.
# Exported to every recipe below that invokes Python.
export PYTHONHASHSEED := 0

# Default ZIP → SCF source. The full Census 2020 ZCTA-to-Tract Relationship
# File (the substitute for the HUD crosswalk) supersedes the
# 32-row bootstrap when present; the build auto-detects the format by header
# sniff. HUD path is retained as a fallback for environments that still use
# the bootstrap fixture.
ZIP_SCF_CENSUS_SOURCE := src/resecta_data/gazetteers/zip_scf/sources/census_zcta_tract_2020_20260419.txt
ZIP_SCF_BOOTSTRAP_SOURCE := src/resecta_data/gazetteers/zip_scf/sources/hud_zip_crosswalk_bootstrap_20260416.csv
ifeq ($(wildcard $(ZIP_SCF_CENSUS_SOURCE)),$(ZIP_SCF_CENSUS_SOURCE))
ZIP_SCF_SOURCE := $(ZIP_SCF_CENSUS_SOURCE)
ZIP_SCF_RETRIEVED := 2026-04-19
else
ZIP_SCF_SOURCE := $(ZIP_SCF_BOOTSTRAP_SOURCE)
ZIP_SCF_RETRIEVED := 2026-04-16
endif

# Required Python version.
REQUIRED_PY_MAJOR := 3
REQUIRED_PY_MINOR := 12

# -----------------------------------------------------------------------------
# Phase 0 reality check
# -----------------------------------------------------------------------------
# These variables list the artifacts each phase is expected to produce.
# Phase 0 produces none; later phases populate these lists.

PHASE1_ARTIFACTS := \
	$(BUILD_DIR)/vectors/npi_test_vectors.json \
	$(BUILD_DIR)/vectors/dea_test_vectors.json \
	$(BUILD_DIR)/vectors/ssn_structural_vectors.json \
	$(BUILD_DIR)/vectors/credit_card_vectors.json \
	$(BUILD_DIR)/vectors/ein_vectors.json \
	$(BUILD_DIR)/vectors/itin_vectors.json \
	$(BUILD_DIR)/vectors/dob_vectors.json \
	$(BUILD_DIR)/vectors/phone_test_vectors.json \
	$(BUILD_DIR)/vectors/email_test_vectors.json \
	$(BUILD_DIR)/vectors/passport_test_vectors.json \
	$(BUILD_DIR)/vectors/drivers_license_test_vectors.json \
	$(BUILD_DIR)/vectors/mrn_test_vectors.json \
	$(BUILD_DIR)/vectors/bates_test_vectors.json \
	$(BUILD_DIR)/vectors/license_plate_test_vectors.json \
	$(BUILD_DIR)/gazetteers/zip_scf_states.json \
	$(BUILD_DIR)/fuzz/redos_payloads.json \
	$(BUILD_DIR)/adversarial/adversarial_patterns.json

PHASE2_ARTIFACTS := \
	$(BUILD_DIR)/gazetteers/surnames.bloom \
	$(BUILD_DIR)/gazetteers/given-names.bloom \
	$(BUILD_DIR)/gazetteers/gazetteer_manifest.json \
	$(BUILD_DIR)/gazetteers/negative_context_candidates.json \
	$(BUILD_DIR)/gazetteers/institutions.json \
	$(BUILD_DIR)/gazetteers/address_components.json \
	$(BUILD_DIR)/gazetteers/passport_patterns.json \
	$(BUILD_DIR)/gazetteers/dl_patterns.json \
	$(BUILD_DIR)/context/context_keywords.json \
	$(BUILD_DIR)/rules/rule_catalog.json \
	$(BUILD_DIR)/demographics/coverage_report.json

PHASE3_ARTIFACTS := \
	$(BUILD_DIR)/classifier/doctype_keywords.json \
	$(BUILD_DIR)/classifier/preset_thresholds_candidates.json \
	$(BUILD_DIR)/corpus/g8_corpus.json \
	$(BUILD_DIR)/demographics/g8_bucket_recall_v1.json

# Phase 3b artifacts are out-of-band: they require Swift-side dumps and are
# produced by `make calibrate`, not by `make build`. They are NOT added to
# ALL_ARTIFACTS so default verification (schema / determinism / hash) does
# not require them.
PHASE3B_ARTIFACTS := \
	$(BUILD_DIR)/classifier/doctype_temperature.json \
	$(BUILD_DIR)/classifier/preset_thresholds.json

# nicknames.json exists only after the CC0 source has been fetched (see the
# GAZ_NICKNAMES_SOURCES gate above) — added to the verification surface only
# when buildable so schema/hash/determinism checks do not require it earlier.
ifneq ($(GAZ_NICKNAMES_SOURCES),)
PHASE2_ARTIFACTS += $(BUILD_DIR)/gazetteers/nicknames.json
endif

ALL_ARTIFACTS := $(PHASE1_ARTIFACTS) $(PHASE2_ARTIFACTS) $(PHASE3_ARTIFACTS)

# -----------------------------------------------------------------------------
# Help
# -----------------------------------------------------------------------------

.PHONY: help
help: ## Print this help
	@echo "Resecta DataPipeline — build targets"
	@echo ""
	@echo "Primary targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Current phase: 1+2+3 (Phase 3 adds doctype keywords, preset-threshold candidates, G8 corpus)."
	@echo "Phase 3b: `make calibrate` is out-of-band. It requires Swift-side softmax + detector-score dumps at $(CALIBRATION_DIR) (see schemas/doctype_softmax_dump.schema.json and schemas/detector_score_dump.schema.json)."

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------

.PHONY: check-python
check-python: ## Verify Python version matches .python-version
	@actual=$$($(PYTHON) -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'); \
	expected="$(REQUIRED_PY_MAJOR).$(REQUIRED_PY_MINOR)"; \
	if [ "$$actual" != "$$expected" ]; then \
		echo "ERROR: host Python $$expected required, found $$actual" >&2; \
		echo "       pyenv install $$expected && pyenv local $$expected" >&2; \
		exit 1; \
	fi
	@if [ -x "$(PYTHON_VENV)" ]; then \
		venv_actual=$$($(PYTHON_VENV) -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'); \
		expected="$(REQUIRED_PY_MAJOR).$(REQUIRED_PY_MINOR)"; \
		if [ "$$venv_actual" != "$$expected" ]; then \
			echo "ERROR: $(VENV_DIR)/ has Python $$venv_actual; required $$expected." >&2; \
			echo "       rm -rf $(VENV_DIR) && make bootstrap" >&2; \
			exit 1; \
		fi; \
	fi

$(VENV_DIR)/pyvenv.cfg: pyproject.toml requirements.lock requirements-dev.lock
	@$(MAKE) check-python
	@scripts/bootstrap.sh

.PHONY: bootstrap
bootstrap: $(VENV_DIR)/pyvenv.cfg ## Create venv and install pinned deps

# check-make — directly-invocable diagnostic twin of the parse-time guard at
# the top of this file (that guard is what actually fails fast; an order-only
# prereq cannot — make runs normal prereqs like verify's `build` first,
# measured on 3.81). Kept as a prereq too so exotic invocations that dodge
# MAKECMDGOALS matching (e.g. `make -f Makefile $(target)` wrappers) still
# hit a versioned error before a guarded recipe runs.
.PHONY: check-make
ifeq ($(filter 4.% 5.%,$(MAKE_VERSION)),)
check-make:
	@echo "ERROR: GNU Make $(MAKE_VERSION) is too old for this Makefile (>= 4.0 required)." >&2
	@echo "       3.81 cannot run 'verify' (--output-sync is 4.0+) and silently drops" >&2
	@echo "       .SHELLFLAGS, so recipes run without 'set -euo pipefail'." >&2
	@echo "       Install GNU make 4.x:   brew install make" >&2
	@echo "       Then invoke targets as: gmake <target>" >&2
	@exit 1
else
check-make:
	@:
endif

# Order-only prereq: every top-level entry point runs check-python first so a
# Python version drift fails loud instead of producing wrong artifacts. Order-only
# (`|`) means the check participates without triggering rebuilds via mtime.
build build-fast verify lint typecheck test bootstrap doctor: | check-python
build verify install-assets all: | check-make

# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------

# The build/ directory is created lazily by the Python builders via
# common.io.atomic_write_bytes; no explicit rule is needed.

NPROC := $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# Default parallelism for `build` (and `build-fast`, now an alias). Capped
# at 6: outer -j × inner worker pools must fit a 16 GB host — the old
# build-fast shape (-j$(hw.ncpu) with full-size inner pools) is the
# documented OOM hazard this cap replaces. RESECTA_BUILD_WORKERS below
# bounds the inner pools so the outer × inner product stays sane.
BUILD_JOBS ?= $(shell n=$(NPROC); [ $$n -gt 6 ] && n=6; [ $$n -lt 2 ] && n=2; echo $$n)

# Inner ProcessPoolExecutor cap, honored by common/process.py::
# effective_workers (full-size pools: bloom ingest 4 / bloom filter 8 /
# corpus 16). Exported so it reaches the builders under any entry path,
# including the determinism rebuild's `$(MAKE) build` subprocess.
RESECTA_BUILD_WORKERS ?= 4
export RESECTA_BUILD_WORKERS

# Cap xdist outer parallelism so outer * inner pool workers fits RAM.
# Rationale: inner ProcessPoolExecutor caps are 4 (bloom/corpus_ingest.py),
# 8 (bloom/filter.py), and 16 (corpus/generate.py); at -n auto on a
# 12-core/32GB host, the product OOMs. Override: make test PYTEST_WORKERS=8
PYTEST_WORKERS ?= $(shell n=$$(( $(NPROC) / 2 )); [ $$n -lt 2 ] && n=2; [ $$n -gt 8 ] && n=8; echo $$n)

# Determinism inner-rebuild parallelism: that rebuild runs INSIDE verify's
# -j5 ensemble alongside pytest -n$(PYTEST_WORKERS), so cap it at
# cores − PYTEST_WORKERS, clamped to [2, 6], to avoid oversubscription.
# Passed to the rebuild as a BUILD_JOBS variable override (not a raw -j)
# so the goal-filtered MAKEFLAGS block below stays the single place that
# turns parallelism on.
REBUILD_JOBS ?= $(shell n=$$(( $(NPROC) - $(PYTEST_WORKERS) )); [ $$n -lt 2 ] && n=2; [ $$n -gt 6 ] && n=6; echo $$n)

# Same binary as $(MAKE), under a name make's recursive-line scanner does
# not special-case. The rebuild command is a STRING handed to the Python
# CLI, not a recipe-level sub-make — with the literal `$(MAKE)` token in
# the recipe, `gmake -n verify` on a stale witness force-executes the line
# and the rebuild inherits -n, yielding an empty second build dir and a
# spurious determinism FAIL. (Pinned to the running make: the cli
# default `make build` resolves to stock 3.81 via PATH.)
REBUILD_MAKE := $(MAKE)

# --output-sync first shipped in GNU make 4.0. Expanding it conditionally
# keeps recipes runnable under an older make, so what a 3.81 user sees is
# check-make's actionable error — not the inner make crashing on an
# unrecognized option after the build prereq already ran.
OUTPUT_SYNC := $(if $(filter 4.% 5.%,$(MAKE_VERSION)),--output-sync=target)

# `build` / `build-fast` are parallel by default. Parse-time
# goal filter (same idiom as the version guard at the top of this file)
# instead of a recursive wrapper, so a no-op `gmake build` keeps its ~0.16 s
# wall, BUILD_DIR= overrides pass through untouched, and the determinism
# rebuild stays single-recursion. Only ever effective on GNU make ≥ 4 —
# older makes are blocked from the build goal by that same guard.
ifneq ($(filter build build-fast,$(MAKECMDGOALS)),)
MAKEFLAGS += -j$(BUILD_JOBS) $(OUTPUT_SYNC)
endif

# -----------------------------------------------------------------------------
# ParaNames pre-sharding (Phase 2 prereq; default-flipped so `bloom` parallelizes)
# -----------------------------------------------------------------------------
# 8 shards × parse_sources_parallel cap=4 (bloom/corpus_ingest.py) ≈ 4-way
# intra-pipeline ingest. Composes with the OOM-fix worker cap; this PR does
# not change _MAX_BUILD_WORKERS.
PARANAMES_SHARDS         ?= 8
PARANAMES_FULL           := src/resecta_data/gazetteers/sources/paranames/paranames_full.tsv.gz
PARANAMES_SHARD_DIR      := src/resecta_data/gazetteers/sources/paranames/shards
PARANAMES_SHARD_SENTINEL := $(PARANAMES_SHARD_DIR)/paranames_full_shard_00.tsv.gz
PARANAMES_SHARD_META     := $(BUILD_DIR)/gazetteers/paranames_shards.meta.json

# LFS-pointer files are ~130 B text; the real file is ~954 MB. `$(wildcard)`
# returns the path either way, so a presence check cannot distinguish a
# hydrated checkout from an unhydrated one — `gzip.open` on a pointer file
# raises BadGzipFile. Size threshold (>1 MB) cleanly separates the two.
# BSD/macOS stat takes -f%z; GNU stat takes -c%s — probe BSD first, fall
# back to GNU, then 0 (absent file). The previous GNU-only `stat -c %s`
# returned 0 on every macOS host, so PARANAMES_FULL_HYDRATED read "no"
# there despite a hydrated checkout: the shard rules fell into their
# not-hydrated fallbacks, paranames_shards.meta.json was written EMPTY
# (asset_hashes.lock pinned the empty-string sha as a result), and a fresh
# macOS checkout re-ran bloom+demographics+g8+bundle-size on every warm
# build (the sentinel never materialized).
PARANAMES_FULL_SIZE      := $(shell stat -f%z $(PARANAMES_FULL) 2>/dev/null || stat -c%s $(PARANAMES_FULL) 2>/dev/null || echo 0)
PARANAMES_FULL_HYDRATED  := $(shell test $(PARANAMES_FULL_SIZE) -gt 1000000 && echo yes || echo no)

.PHONY: paranames-shards
paranames-shards: $(PARANAMES_SHARD_SENTINEL) $(PARANAMES_SHARD_META) ## Pre-shard paranames_full.tsv.gz so `bloom` can parallelize ingest

# Depend on the parent file only when it exists ($(wildcard)) so re-fetched
# sources still retrigger sharding, but a checkout without the fetch-on-demand
# corpus (it is not committed) degrades to the bootstrap path instead of
# aborting the dependency graph ("No rule to make target"). When the file is
# a stale pointer or absent, fall back gracefully — `bloom` then uses the
# monolithic path via _paranames_full_specs (cli.py:799-807).
$(PARANAMES_SHARD_SENTINEL): $(wildcard $(PARANAMES_FULL)) scripts/shard_paranames.py
ifneq ($(PARANAMES_FULL_HYDRATED),yes)
ifeq ($(RESECTA_REQUIRE_LFS),1)
	@echo "ERROR: $(PARANAMES_FULL) appears to be an LFS pointer (size <1MB)." >&2
	@echo "       Hydrate with: git lfs install && git lfs pull" >&2
	@exit 1
else
	@echo "WARNING: $(PARANAMES_FULL) appears to be an LFS pointer (size <1MB); falling back to monolithic ingest." >&2
	@mkdir -p $(PARANAMES_SHARD_DIR)
endif
else
	$(PYTHON_VENV) scripts/shard_paranames.py \
	    --input $(PARANAMES_FULL) \
	    --output-dir $(PARANAMES_SHARD_DIR) \
	    --shards $(PARANAMES_SHARDS)
endif

$(PARANAMES_SHARD_META): $(PARANAMES_SHARD_SENTINEL) scripts/write_shard_meta.py
	@mkdir -p $(dir $@)
ifneq ($(PARANAMES_FULL_HYDRATED),yes)
ifeq ($(RESECTA_REQUIRE_LFS),1)
	@echo "ERROR: $(PARANAMES_FULL) appears to be an LFS pointer; meta sidecar requires hydrated source." >&2
	@echo "       Hydrate with: git lfs install && git lfs pull" >&2
	@exit 1
else
	@echo "WARNING: writing empty $(PARANAMES_SHARD_META) (paranames not hydrated)." >&2
	@touch $@
endif
else
	$(PYTHON_VENV) scripts/write_shard_meta.py \
	    --shard-dir $(PARANAMES_SHARD_DIR) \
	    --parent $(PARANAMES_FULL) \
	    --output $@
endif

.PHONY: build
build: bootstrap $(STAMP_DIR)/vectors $(STAMP_DIR)/fuzz $(STAMP_DIR)/zip-scf $(STAMP_DIR)/adversarial \
       $(STAMP_DIR)/bloom $(STAMP_DIR)/gaz-negctx $(STAMP_DIR)/gaz-institutions $(STAMP_DIR)/gaz-address \
       $(STAMP_DIR)/passport-patterns $(STAMP_DIR)/dl-patterns $(STAMP_DIR)/context $(STAMP_DIR)/rules \
       $(STAMP_DIR)/demographics $(STAMP_DIR)/classifier $(STAMP_DIR)/corpus \
       $(STAMP_DIR)/g8-bucket-recall $(STAMP_DIR)/bundle-size $(GAZ_NICKNAMES_STAMP) ## Generate all artifacts into build/
	@echo "Build complete. Artifacts under $(BUILD_DIR)/."

.PHONY: build-fast
build-fast: build ## Alias — `build` is parallel by default now (BUILD_JOBS=N to override; RAM-capped, see BUILD_JOBS)

# -----------------------------------------------------------------------------
# Timing (opt-in baseline — not part of `build` or `verify`)
# -----------------------------------------------------------------------------
# Logs per-target wall time to build/_timings.jsonl. Use to compare cold vs.
# warm runs when changing the ingest / bloom paths; the log is informational
# only, not a shipped artifact, and is ignored by schema-check and hash-lock.

TIMINGS_LOG := $(BUILD_DIR)/_timings.jsonl

# Usage: $(call time_step,<label>,<recipe>). Appends one JSONL record per
# invocation: {"label":"...","wall_seconds":N.NN}. Uses GNU `date +%s.%N` and
# `awk` (already required elsewhere via `stat -c %s` at line ~187); CI is
# ubuntu-24.04 so this is fine. macOS users would need `gdate` from coreutils.
define time_step
	@mkdir -p $(BUILD_DIR); \
	start=$$(date +%s.%N); \
	$(2); \
	end=$$(date +%s.%N); \
	elapsed=$$(awk "BEGIN { printf \"%.3f\", $$end - $$start }"); \
	printf '{"label":"%s","wall_seconds":%s}\n' "$(1)" "$$elapsed" >> $(TIMINGS_LOG); \
	printf '[time] %-18s %ss\n' "$(1)" "$$elapsed"
endef

.PHONY: time-build
time-build: bootstrap ## Run each build phase with per-phase wall-time logging
	@rm -f $(TIMINGS_LOG)
	$(call time_step,vectors,$(MAKE) vectors)
	$(call time_step,fuzz,$(MAKE) fuzz)
	$(call time_step,zip-scf,$(MAKE) zip-scf)
	$(call time_step,adversarial,$(MAKE) adversarial)
	$(call time_step,bloom,$(MAKE) bloom)
	$(call time_step,gazetteers,$(MAKE) gazetteers)
	$(call time_step,rules,$(MAKE) rules)
	$(call time_step,demographics,$(MAKE) demographics)
	$(call time_step,classifier,$(MAKE) classifier)
	$(call time_step,corpus,$(MAKE) corpus)
	$(call time_step,g8-bucket-recall,$(MAKE) g8-bucket-recall)
	$(call time_step,bundle-size,$(MAKE) bundle-size)
	@echo ""
	@echo "Per-phase timings written to $(TIMINGS_LOG)."

# Phase 1 build targets. Sentinel + alias pattern: the warm-build no-op case
# is what makes this worth the verbosity. .stamps/ lives under $(BUILD_DIR) so
# `make clean` removes it along with artifacts. STAMP_DIR / COMMON_DEPS /
# *_PY are declared near the top of the Paths section so the `build` target
# can reference $(STAMP_DIR)/vectors etc. in its prereq list at parse time.

$(STAMP_DIR)/vectors: $(VECTORS_PY) $(COMMON_DEPS) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,vectors,$(RESECTA_DATA) build vectors all --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: vectors
vectors: $(STAMP_DIR)/vectors  ## [Phase 1] Build NPI/DEA/SSN test vectors

$(STAMP_DIR)/fuzz: $(FUZZ_PY) $(COMMON_DEPS) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,fuzz,$(RESECTA_DATA) build fuzz redos --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: fuzz
fuzz: $(STAMP_DIR)/fuzz  ## [Phase 1] Build ReDoS fuzz payloads

$(STAMP_DIR)/zip-scf: $(ZIP_SCF_PY) $(COMMON_DEPS) $(ZIP_SCF_SOURCE) | $(VENV_DIR)/pyvenv.cfg
	@mkdir -p $(dir $@)
	@if [ ! -f "$(ZIP_SCF_SOURCE)" ]; then \
		echo "ERROR: ZIP crosswalk source not found: $(ZIP_SCF_SOURCE)" >&2; \
		echo "       Expected either $(ZIP_SCF_CENSUS_SOURCE) (the full Census relationship file)" >&2; \
		echo "       or $(ZIP_SCF_BOOTSTRAP_SOURCE) (fixture fallback)." >&2; \
		exit 1; \
	fi
	$(call keyed_stamp,zip-scf,$(RESECTA_DATA) build zip-scf --source $(ZIP_SCF_SOURCE) --build-dir $(BUILD_DIR) --retrieval-date $(ZIP_SCF_RETRIEVED) --seed $(RESECTA_SEED))

.PHONY: zip-scf
zip-scf: $(STAMP_DIR)/zip-scf  ## [Phase 1] Build ZIP → SCF → state table (Census ZCTA if present, else HUD bootstrap)

$(STAMP_DIR)/adversarial: $(ADVERSARIAL_PY) $(COMMON_DEPS) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,adversarial,$(RESECTA_DATA) build adversarial patterns --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: adversarial
adversarial: $(STAMP_DIR)/adversarial  ## [Phase 1] Build adversarial pattern fixtures

# Phase 2 + Phase 3 build targets. Same sentinel + alias shape as Phase 1
# above. Cross-module deps in two places: $(STAMP_DIR)/gaz-address pulls
# zips from institutions/sources/, and $(STAMP_DIR)/context pulls from a
# sibling top-level context/sources/ package. Demographics depends on the
# bloom *stamp* (transitive: shares the bloom ingest cache) so it rebuilds
# whenever bloom does. classifier and corpus are pure code — no source
# globs because their inputs are pre-baked into _keyword_data.py / templates.

# Hydrated: hard-depend on the shard sentinel so a fresh corpus fetch
# auto-triggers sharding before bloom ingests. Not hydrated: the sentinel
# never materializes on the fallback path, and keyed_stamp sha256-hashes
# every prereq in $^, so a hard sentinel dep would abort the bootstrap-sample
# build the CLI explicitly degrades to — $(wildcard) keeps it out unless it
# exists. The meta sidecar stays a hard prereq in both states: its rule
# always materializes the file (empty when not hydrated), and dropping it
# from the closure desyncs the determinism rebuild's build dir from the
# first build's (meta present on one side only).
ifeq ($(PARANAMES_FULL_HYDRATED),yes)
BLOOM_PARANAMES_DEPS := $(PARANAMES_SHARD_SENTINEL) $(PARANAMES_SHARD_META)
else
BLOOM_PARANAMES_DEPS := $(wildcard $(PARANAMES_SHARD_SENTINEL)) $(PARANAMES_SHARD_META)
endif

$(STAMP_DIR)/bloom: $(BLOOM_PY) $(COMMON_DEPS) $(BLOOM_PARANAMES_DEPS) $(BLOOM_NAME_CORPORA) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,bloom,$(RESECTA_DATA) build bloom --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: bloom
bloom: $(STAMP_DIR)/bloom  ## [Phase 2] Build name Bloom filters + manifest

# Patch G: self-recursive parallel umbrella. Each gaz stamp writes a
# distinct file under build/gazetteers/ (no shared output paths), so -j4 is
# safe. Internal -j makes `make gazetteers` standalone parallel without
# requiring callers to pass -j.
.PHONY: gazetteers
gazetteers:  ## [Phase 2] Build the non-Bloom gazetteers in parallel
	@$(MAKE) -j6 $(STAMP_DIR)/gaz-negctx $(STAMP_DIR)/gaz-institutions \
	             $(STAMP_DIR)/gaz-address $(STAMP_DIR)/passport-patterns \
	             $(STAMP_DIR)/dl-patterns $(GAZ_NICKNAMES_STAMP)

$(STAMP_DIR)/gaz-negctx: $(GAZ_NEGCTX_PY) $(COMMON_DEPS) $(GAZ_NEGCTX_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,gaz-negctx,$(RESECTA_DATA) build gazetteers negative-context --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: gazetteers-negative-context
gazetteers-negative-context: $(STAMP_DIR)/gaz-negctx  ## [Phase 2] Build negative-context candidates

# The reviewed negative_context.json is committed under
# src/.../negative_context/reviewed/ and staged into build/ on demand. Curated
# context assets change only under a written change plan approved by the
# maintainer before the edit — the asset, the rows or fields, the reason, and
# the regeneration and verification steps. No row-by-row review afterwards.
# The sidecar drift check stays as a mechanical tripwire the same change
# re-stamps: the CLI verifies the meta sidecar's reviewed_version (sha256 of
# the CANDIDATES file the change was based on) against the live candidates
# hash and refuses on drift.
.PHONY: stage-reviewed-negctx
stage-reviewed-negctx: bootstrap gazetteers-negative-context ## Stage the reviewed negative_context.json into build/ (verifies the candidates-hash sidecar; safe to re-run)
	$(RESECTA_DATA) stage-reviewed-negctx --build-dir $(BUILD_DIR)

$(STAMP_DIR)/gaz-institutions: $(GAZ_INSTITUTIONS_PY) $(COMMON_DEPS) $(GAZ_INSTITUTIONS_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,gaz-institutions,$(RESECTA_DATA) build gazetteers institutions --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: gazetteers-institutions
gazetteers-institutions: $(STAMP_DIR)/gaz-institutions  ## [Phase 2] Build institutions gazetteer

$(STAMP_DIR)/gaz-address: $(GAZ_ADDRESS_PY) $(COMMON_DEPS) $(GAZ_ADDRESS_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,gaz-address,$(RESECTA_DATA) build gazetteers address-components --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: gazetteers-address-components
gazetteers-address-components: $(STAMP_DIR)/gaz-address  ## [Phase 2] Build address-components gazetteer

$(STAMP_DIR)/gaz-nicknames: $(GAZ_NICKNAMES_PY) $(COMMON_DEPS) $(GAZ_NICKNAMES_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,gaz-nicknames,$(RESECTA_DATA) build gazetteers nicknames --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: gazetteers-nicknames
gazetteers-nicknames: $(STAMP_DIR)/gaz-nicknames  ## [Phase 2] Build nickname/diminutive sidecar (needs fetched CC0 source)

$(STAMP_DIR)/passport-patterns: $(PASSPORT_PATTERNS_PY) $(COMMON_DEPS) $(PASSPORT_PATTERNS_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,passport-patterns,$(RESECTA_DATA) build gazetteers passport-patterns --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: passport-patterns
passport-patterns: $(STAMP_DIR)/passport-patterns  ## [Phase 2] Build per-country passport-pattern gazetteer

$(STAMP_DIR)/dl-patterns: $(DL_PATTERNS_PY) $(COMMON_DEPS) $(DL_PATTERNS_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,dl-patterns,$(RESECTA_DATA) build gazetteers dl-patterns --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: dl-patterns
dl-patterns: $(STAMP_DIR)/dl-patterns  ## [Phase 2] Build per-state driver-license-pattern gazetteer

$(STAMP_DIR)/context: $(CONTEXT_PY) $(COMMON_DEPS) $(CONTEXT_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,context,$(RESECTA_DATA) build context --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: context
context: $(STAMP_DIR)/context  ## [Phase 2] Build per-category positive context-keyword gazetteer

$(STAMP_DIR)/rules: $(RULES_PY) $(COMMON_DEPS) $(RULES_SOURCES) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,rules,$(RESECTA_DATA) build rules --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: rules
rules: $(STAMP_DIR)/rules  ## [Phase 2] Build PII detector rule-ID catalog

$(STAMP_DIR)/demographics: $(DEMOGRAPHICS_PY) $(COMMON_DEPS) $(STAMP_DIR)/bloom | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,demographics,$(RESECTA_DATA) build demographics --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: demographics
demographics: $(STAMP_DIR)/demographics  ## [Phase 2] Build demographic coverage report

# The context-scorer candidates fit (run by `build classifier all`) reads
# the committed G8 corpus (provenance) plus the committed File-5 fire dump
# (pre-computed features), so both are content prereqs of the classifier stamp.
# The corpus stamp produces g8_corpus.json. The fire dump is a committed input
# emitted out-of-band by the Swift harness (no producing recipe here): it is
# pinned at its fixed repo-root location (NOT $(BUILD_DIR)/…, which the
# side-by-side determinism rebuild overrides to a fresh out-dir that holds no
# dump), so a refreshed dump rebuilds the scorer and the rebuild reads the same
# committed bytes. The final context_scorer.json stays the identity placeholder;
# only context_scorer_candidates.json carries the fit.
FIRE_FEATURES_DUMP := build/corpus/g8_fire_features.json
$(STAMP_DIR)/classifier: $(CLASSIFIER_PY) $(COMMON_DEPS) $(STAMP_DIR)/corpus $(FIRE_FEATURES_DUMP) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,classifier,$(RESECTA_DATA) build classifier all --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: classifier
classifier: $(STAMP_DIR)/classifier  ## [Phase 3] Build doctype keywords, preset-threshold candidates, and the context scorer

$(STAMP_DIR)/corpus: $(CORPUS_PY) $(COMMON_DEPS) | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,corpus,$(RESECTA_DATA) build corpus g8 --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: corpus
corpus: $(STAMP_DIR)/corpus  ## [Phase 3] Build the G8 synthetic corpus

# G8 bucket-stratified recall (a one-off measurement for the transparency copy).
# Depends on the surnames Bloom filter and the G8 corpus, both of which
# precede this step in the dependency graph.
$(STAMP_DIR)/g8-bucket-recall: $(DEMOGRAPHICS_PY) $(COMMON_DEPS) $(STAMP_DIR)/bloom $(STAMP_DIR)/corpus | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,g8-bucket-recall,$(RESECTA_DATA) build g8-bucket-recall --build-dir $(BUILD_DIR) --seed $(RESECTA_SEED))

.PHONY: g8-bucket-recall
g8-bucket-recall: $(STAMP_DIR)/g8-bucket-recall  ## [Phase 3] Build the G8 bucket-stratified recall artifact

# Bundle-size build probe (engineer-facing only).
# Walks gazetteers / context / rules / calibration / vectors and emits per-
# artifact byte counts. Depends on the artifact-producing stamps so a clean
# build orders this step last; calibration/ being absent is handled by the
# probe itself (empty payload). The Swift cold-start hook is the
# deferred Mac-side half.
$(STAMP_DIR)/bundle-size: $(INSTRUMENTATION_PY) $(COMMON_DEPS) \
    $(STAMP_DIR)/vectors $(STAMP_DIR)/zip-scf $(STAMP_DIR)/bloom \
    $(STAMP_DIR)/gaz-negctx $(STAMP_DIR)/gaz-institutions $(STAMP_DIR)/gaz-address \
    $(STAMP_DIR)/passport-patterns $(STAMP_DIR)/dl-patterns $(STAMP_DIR)/context \
    $(STAMP_DIR)/rules | $(VENV_DIR)/pyvenv.cfg
	$(call keyed_stamp,bundle-size,$(RESECTA_DATA) build bundle-size --build-dir $(BUILD_DIR))

.PHONY: bundle-size
bundle-size: $(STAMP_DIR)/bundle-size  ## [Phase 3] Build the bundle-size instrumentation probe

# -----------------------------------------------------------------------------
# Phase 3b calibration (out-of-band; requires Swift-side dumps)
# -----------------------------------------------------------------------------

.PHONY: calibrate-temperature
calibrate-temperature: bootstrap corpus ## [Phase 3b] Fit doctype-softmax temperature against a Swift dump
	@if [ ! -f "$(SOFTMAX_DUMP)" ]; then \
		echo "ERROR: Swift softmax dump not found at $(SOFTMAX_DUMP)." >&2; \
		echo "       Produced by the Swift DocumentTypeClassifier test target;" >&2; \
		echo "       see schemas/doctype_softmax_dump.schema.json." >&2; \
		exit 1; \
	fi
	$(RESECTA_DATA) build calibrate temperature \
		--softmax-dump $(SOFTMAX_DUMP) \
		--corpus $(BUILD_DIR)/corpus/g8_corpus.json \
		--schemas-dir $(SCHEMAS_DIR) \
		--build-dir $(BUILD_DIR) \
		--seed $(RESECTA_SEED)

.PHONY: calibrate-sweep
calibrate-sweep: bootstrap corpus calibrate-temperature ## [Phase 3b] Sweep per-category thresholds against a Swift dump (writes the sweep_raw inspection file only)
	@if [ ! -f "$(SCORE_DUMP)" ]; then \
		echo "ERROR: Swift detector score dump not found at $(SCORE_DUMP)." >&2; \
		echo "       Produced by the Swift PII detector test target;" >&2; \
		echo "       see schemas/detector_score_dump.schema.json." >&2; \
		exit 1; \
	fi
	$(RESECTA_DATA) build calibrate sweep \
		--score-dump $(SCORE_DUMP) \
		--temperature $(BUILD_DIR)/classifier/doctype_temperature.json \
		--corpus $(BUILD_DIR)/corpus/g8_corpus.json \
		--schemas-dir $(SCHEMAS_DIR) \
		--build-dir $(BUILD_DIR) \
		--seed $(RESECTA_SEED) \
		--prior-mode fresh
	@echo "Wrote $(BUILD_DIR)/classifier/preset_thresholds_sweep_raw.json (status=sweep_raw)."
	@echo "The shipping preset_thresholds.json is untouched; promote via make calibrate-finalize (under an approved change plan)."

.PHONY: calibrate-finalize
calibrate-finalize: bootstrap ## [Phase 3b] Promote sweep_raw to the shipping preset_thresholds.json (under an approved change plan: review the diff first)
	@if [ ! -f "$(BUILD_DIR)/classifier/preset_thresholds_sweep_raw.json" ]; then \
		echo "ERROR: $(BUILD_DIR)/classifier/preset_thresholds_sweep_raw.json not found." >&2; \
		echo "       Run: make calibrate-sweep" >&2; \
		exit 1; \
	fi
	@echo "=== Diff (shipping -> sweep_raw) ===" >&2
	@diff -u "$(BUILD_DIR)/classifier/preset_thresholds.json" \
	         "$(BUILD_DIR)/classifier/preset_thresholds_sweep_raw.json" || true
	@echo "" >&2
	@echo "Review the diff above. If correct, press Enter to promote." >&2
	@[ -t 0 ] || { echo 'calibrate-finalize requires an interactive terminal'; exit 1; }
	@read -r _confirm
	$(RESECTA_DATA) build calibrate finalize \
		--sweep-raw "$(BUILD_DIR)/classifier/preset_thresholds_sweep_raw.json" \
		--build-dir "$(BUILD_DIR)" \
		--schemas-dir "$(SCHEMAS_DIR)"
	@echo "Wrote $(BUILD_DIR)/classifier/preset_thresholds.json (status=calibrated)"
	@echo "Next: make install-assets (then regenerate the lockfile)"

.PHONY: calibrate
calibrate: calibrate-temperature calibrate-sweep ## [Phase 3b] Run both calibration steps (requires Swift-side dumps; finalize is a separate step under an approved change plan)

# -----------------------------------------------------------------------------
# Sources (network-permitted)
# -----------------------------------------------------------------------------

.PHONY: sources
sources: bootstrap ## Fetch raw inputs (the ONLY network target)
	@echo "Phase 1+2 ship bootstrap sources in git (no fetch needed)."
	@echo "For the full HUD crosswalk, run:"
	@echo "  scripts/fetch_hud_zip_crosswalk.sh <YYYY> <Qn>"
	@echo "For full name corpora, run any of:"
	@echo "  scripts/fetch_ssa_babynames.sh"
	@echo "  scripts/fetch_census_2010_surnames.sh"
	@echo "  scripts/fetch_census_spanish.sh"
	@echo "  scripts/fetch_paranames.sh"
	@echo "  scripts/fetch_popnames_snapshot.sh"
	@echo "  scripts/fetch_us_courts_glossary.sh"
	@echo "For institutions sources, run:"
	@echo "  scripts/fetch_gsa_agencies.sh"
	@echo "  scripts/fetch_tiger_counties.sh"
	@echo "  scripts/fetch_gnis.sh"
	@echo "For address_components sources, run:"
	@echo "  scripts/fetch_tiger_places.sh"
	@echo "then update SOURCES.md with the new SHA-256 rows."

# -----------------------------------------------------------------------------
# Verification
# -----------------------------------------------------------------------------

.PHONY: lint
lint: bootstrap ## Run ruff check and format check
	$(RUFF) check src tests scripts
	$(RUFF) format --check src tests scripts

.PHONY: format
format: bootstrap ## Apply ruff formatting
	$(RUFF) format src tests scripts
	$(RUFF) check --fix src tests scripts

.PHONY: typecheck
typecheck: bootstrap ## Run mypy --strict
	$(MYPY)

.PHONY: test
test: bootstrap ## Run pytest
	$(PYTEST) -n $(PYTEST_WORKERS) --dist=loadscope

.PHONY: test-fast
test-fast: bootstrap ## Run pytest excluding slow tests
	$(PYTEST) -n $(PYTEST_WORKERS) --dist=loadscope -m "not slow"

# `*-only` variants operate on existing $(BUILD_DIR)/ contents — they are
# the primitives `verify` uses to avoid re-running `build` once per check.
# The user-facing `schema-check` / `hash-check` targets keep the `build`
# prereq for direct invocation (`make schema-check` should still work
# standalone).
.PHONY: schema-check-only
schema-check-only: bootstrap
	$(PYTHON_VENV) -m resecta_data.cli validate-schemas --build-dir $(BUILD_DIR) --schemas-dir schemas

.PHONY: schema-check
schema-check: bootstrap build schema-check-only ## Validate every build/ artifact against its schema

# determinism-check witness. The sentinel file at $(DETERMINISM_WITNESS) is
# touched after a successful verify-determinism rebuild. Its prereqs are
# every builder stamp (Phase 1+2+3) plus asset_hashes.lock — the audited
# v2.1 stamp globs are the same invalidation oracle the build itself uses,
# so a fresh witness means "rebuild produces the same bytes as snapshot for
# this source state". The .stamps/ directory is excluded from
# iter_build_artifacts (common/io.py: bea1904), so the witness file is
# invisible to schema-check / hash-check / determinism-check's own diff.
#
# RESECTA_FORCE_DETERMINISM=1 bypasses the witness and always runs the
# rebuild. CI sets this so a future cache layer cannot silently skip the
# determinism gate (Patch P, Subagent 4 wires the workflow env).
DETERMINISM_WITNESS := $(STAMP_DIR)/.determinism-witness

# Witness key inputs — the 17 stamp files plus the lock. Single definition
# shared by the witness rule's prereq list, determinism-check-force's key
# write, and doctor's staleness probe, so the three can never drift apart.
# Because each stamp's CONTENT is the manifest of its own closure (speed
# plan #4), a manifest over these files transitively covers every tracked
# input of every builder + the lock — the reviewer-R2 floor for #4.
WITNESS_KEY_INPUTS := \
    $(STAMP_DIR)/vectors $(STAMP_DIR)/fuzz $(STAMP_DIR)/zip-scf $(STAMP_DIR)/adversarial \
    $(STAMP_DIR)/bloom $(STAMP_DIR)/gaz-negctx $(STAMP_DIR)/gaz-institutions \
    $(STAMP_DIR)/gaz-address $(STAMP_DIR)/passport-patterns $(STAMP_DIR)/dl-patterns \
    $(STAMP_DIR)/context $(STAMP_DIR)/rules $(STAMP_DIR)/demographics $(STAMP_DIR)/classifier \
    $(STAMP_DIR)/corpus $(STAMP_DIR)/g8-bucket-recall $(STAMP_DIR)/bundle-size \
    asset_hashes.lock

$(DETERMINISM_WITNESS): $(WITNESS_KEY_INPUTS) | $(VENV_DIR)/pyvenv.cfg
	@mkdir -p $(dir $@)
	@if $(STAMP_KEY) check $@ $^; then \
		echo "[witness] stamp manifests + lock byte-identical to the last green determinism rebuild -- rebuild skipped"; \
	else \
		echo "[witness] tracked content changed -- running determinism rebuild (cold ParaNames re-parse)"; \
		tmpdir=$$(mktemp -d /tmp/resecta-rebuild.XXXXXX); \
		trap "rm -rf $$tmpdir" EXIT; \
		$(PYTHON_VENV) -m resecta_data.cli verify-determinism \
		    --build-dir $(BUILD_DIR) \
		    --rebuild-command "$(REBUILD_MAKE) build BUILD_JOBS=$(REBUILD_JOBS)" \
		    --rebuild-out-dir $$tmpdir \
		&& $(STAMP_KEY) write $@ $^; \
	fi
	@touch $@

.PHONY: determinism-check
determinism-check: bootstrap $(if $(RESECTA_FORCE_DETERMINISM),determinism-check-force,$(DETERMINISM_WITNESS)) ## Rebuild artifacts and diff (cached via .stamps/.determinism-witness; RESECTA_FORCE_DETERMINISM=1 to bypass)

.PHONY: determinism-check-force
determinism-check-force: bootstrap
	@rm -f $(DETERMINISM_WITNESS)
	@tmpdir=$$(mktemp -d /tmp/resecta-rebuild.XXXXXX); \
	    trap "rm -rf $$tmpdir" EXIT; \
	    $(PYTHON_VENV) -m resecta_data.cli verify-determinism \
	        --build-dir $(BUILD_DIR) \
	        --rebuild-command "$(REBUILD_MAKE) build BUILD_JOBS=$(REBUILD_JOBS)" \
	        --rebuild-out-dir $$tmpdir
	@mkdir -p $(dir $(DETERMINISM_WITNESS))
	@$(STAMP_KEY) write $(DETERMINISM_WITNESS) $(WITNESS_KEY_INPUTS)

.PHONY: hash-check-only
hash-check-only: bootstrap
	$(PYTHON_VENV) -m resecta_data.cli verify-hashes --build-dir $(BUILD_DIR) --lockfile asset_hashes.lock

# Hash-verify only what the current host actually built; entries for
# artifacts that need the large fetched sources are reported as skipped.
.PHONY: hash-check-built-only
hash-check-built-only: bootstrap
	$(PYTHON_VENV) -m resecta_data.cli verify-hashes --build-dir $(BUILD_DIR) --lockfile asset_hashes.lock --built-only

.PHONY: hash-check
hash-check: bootstrap build hash-check-only ## Verify asset_hashes.lock matches current build

.PHONY: verify
verify: bootstrap build ## Full verification suite (single build; parallel checks + parallel determinism-check)
	@$(MAKE) -j5 $(OUTPUT_SYNC) lint typecheck test schema-check-only hash-check-only determinism-check

# verify minus determinism-check. The determinism rebuild is the only gate
# with a real cost: ~3-4 min when the witness is stale (cold ParaNames
# re-parse; was 40-55 min before the in-worker aggregation);
# everything else is ~10 s warm. This target exists for the dev loop ONLY —
# it does not prove rebuild reproducibility, so it is NEVER the pre-install
# gate: `install-assets` keeps full `verify` as its prerequisite for anything
# shipping to iOS.
.PHONY: verify-fast
verify-fast: bootstrap build ## Dev-loop gate: verify WITHOUT determinism-check — not sufficient before install-assets (that keeps full verify)
	@$(MAKE) -j5 $(OUTPUT_SYNC) lint typecheck test schema-check-only hash-check-only
	@echo "verify-fast PASSED — determinism-check NOT run; run 'gmake verify' before any install/ship step."

# -----------------------------------------------------------------------------
# Sign gazetteer manifest (verified by the iOS engine)
# -----------------------------------------------------------------------------
# Ed25519-signs build/gazetteers/gazetteer_manifest.json and writes
# manifest_public_key.pem + gazetteer_manifest.sig next to it. Both files
# flow into Resources/Gazetteers/ via `install-assets` so the iOS engine
# can verify the manifest at detector init.
#
# Private key lives at ~/.resecta-data/manifest-private-key.pem (gitignored
# — outside both repos so it never enters git history). Rotation cadence
# is per major release.
#
# install-assets depends on sign-manifest so the .sig / .pem files are
# always in build/ for the asset install + hash-check.

# Narrowed prereq: the sign command reads
# exactly one input — build/gazetteers/gazetteer_manifest.json, produced by
# the bloom builder — so the bloom stamp is the narrowest sound prerequisite.
# This cannot weaken the ship gate: install-assets still requires full
# `verify` before any signed byte crosses into the Swift tree.
.PHONY: sign-manifest
sign-manifest: bootstrap $(STAMP_DIR)/bloom ## Sign gazetteer_manifest.json with Ed25519 (writes .sig + .pem peers)
	$(PYTHON_VENV) -m resecta_data.cli sign-manifest --build-dir $(BUILD_DIR)

# -----------------------------------------------------------------------------
# Install into Swift tree
# -----------------------------------------------------------------------------

# stage-reviewed-negctx is a prerequisite so the reviewed file is always
# present-and-current in build/ before any byte crosses into the Swift tree.
# A drifted candidates file (sidecar not re-stamped) makes install-assets
# fail by design — installing an unreviewed negative_context.json is the
# failure mode the sidecar tripwire exists to stop.
# INSTALL_ASSETS_FLAGS is threaded to the install-assets CLI (empty by default).
# Set INSTALL_ASSETS_FLAGS=--allow-shrink to permit a shrink-guarded gazetteer
# (e.g. institutions.json) to be overwritten by a smaller build/ file
# (the first install after a source refresh).
INSTALL_ASSETS_FLAGS ?=
.PHONY: install-assets
install-assets: verify sign-manifest stage-reviewed-negctx ## Copy artifacts from build/ into the Swift Resources path
	@if [ ! -d "$(SWIFT_RESOURCES)" ]; then \
		echo "ERROR: Swift Resources path not found: $(SWIFT_RESOURCES)" >&2; \
		echo "       This target must be run from inside the Resecta repo." >&2; \
		exit 1; \
	fi
	$(PYTHON_VENV) -m resecta_data.cli install-assets \
		--build-dir $(BUILD_DIR) \
		--resources-dir $(SWIFT_RESOURCES) \
		--fixtures-dir $(SWIFT_FIXTURES) \
		$(INSTALL_ASSETS_FLAGS)

# -----------------------------------------------------------------------------
# Diagnostics
# -----------------------------------------------------------------------------

.PHONY: doctor
doctor: ## Print environment health summary (read-only)
	@echo "=== Python ==="
	@printf "  host:    "; $(PYTHON) --version 2>&1 || echo "not found on PATH"
	@printf "  venv:    "
	@if [ -x "$(PYTHON_VENV)" ]; then $(PYTHON_VENV) --version 2>&1; else echo "not bootstrapped (run: make bootstrap)"; fi
	@echo ""
	@echo "=== ParaNames LFS ==="
	@printf "  file:     %s\n" "$(PARANAMES_FULL)"
	@printf "  size:     %s bytes\n" "$(PARANAMES_FULL_SIZE)"
	@printf "  hydrated: %s\n" "$(PARANAMES_FULL_HYDRATED)"
	@echo ""
	@echo "=== Lockfile ==="
	@if [ -f asset_hashes.lock ]; then printf "  asset_hashes.lock mtime: "; stat -f '%Sm' asset_hashes.lock 2>/dev/null || stat -c '%y' asset_hashes.lock; else echo "  asset_hashes.lock: missing"; fi
	@echo ""
	@echo "=== Build directory ==="
	@if [ -d $(BUILD_DIR) ]; then \
		printf "  size:     "; du -sh $(BUILD_DIR) 2>/dev/null | cut -f1; \
		printf "  files:    "; find $(BUILD_DIR) -type f 2>/dev/null | wc -l; \
		if [ -d $(BUILD_DIR)/.stamps ]; then \
			printf "  oldest stamp: "; \
			o=$$({ find $(BUILD_DIR)/.stamps -type f ! -name '.determinism-witness' -printf '%T@ %p\n' 2>/dev/null || find $(BUILD_DIR)/.stamps -type f ! -name '.determinism-witness' -exec stat -f '%m %N' {} + 2>/dev/null; } | sort -n | head -1 | cut -d' ' -f2-); \
			echo "$${o:-(none)}"; \
		else \
			echo "  stamps:   none yet (added by later commits in this Makefile pass)"; \
		fi; \
	else \
		echo "  $(BUILD_DIR)/ does not exist (run: make build)"; \
	fi
	@echo ""
	@echo "=== Build tooling ==="
	@printf "  running make:  %s" "$(MAKE_VERSION)"; \
	  case "$(MAKE_VERSION)" in 4.*|5.*) echo "  ✓";; *) echo "  ⚠️  < 4.0 — build/verify need gmake (brew install make)";; esac
	@printf "  gmake:         "; command -v gmake >/dev/null 2>&1 && gmake --version 2>/dev/null | sed -n 1p || echo "not installed (brew install make)"
	@echo ""
	@echo "=== venv freshness ==="
	@if [ ! -f $(VENV_DIR)/pyvenv.cfg ]; then echo "  not bootstrapped (run: gmake bootstrap)"; \
	elif [ requirements.lock -nt $(VENV_DIR)/pyvenv.cfg ] || [ pyproject.toml -nt $(VENV_DIR)/pyvenv.cfg ]; then \
	  echo "  ⚠️  dep inputs newer than venv — next make invocation re-runs bootstrap"; \
	else echo "  ✓ venv at/after requirements.lock + pyproject.toml"; fi
	@printf "  input stamp:   "; \
	  if [ -f $(VENV_DIR)/.bootstrap-input-sha256 ] && [ -x $(PYTHON_VENV) ]; then \
	    cur=$$($(PYTHON_VENV) -c 'import hashlib;h=hashlib.sha256();[h.update(open(n,"rb").read()) for n in ("requirements.lock","pyproject.toml")];print(h.hexdigest())' 2>/dev/null || echo unavailable); \
	    if [ "$$cur" = "$$(cat $(VENV_DIR)/.bootstrap-input-sha256)" ]; then echo "✓ matches current inputs (bootstrap early-exits)"; \
	    else echo "⚠️  stale — next bootstrap re-runs pip"; fi; \
	  else echo "absent (next successful bootstrap writes it)"; fi
	@echo ""
	@echo "=== Determinism witness ==="
	@w=$(DETERMINISM_WITNESS); \
	  if [ ! -e "$$w" ]; then echo "  absent — next verify pays a full determinism rebuild"; \
	  elif [ -n "$$(find $(STAMP_DIR) -type f ! -name '.determinism-witness' -newer "$$w" 2>/dev/null)" ] || [ asset_hashes.lock -nt "$$w" ]; then \
	    if [ -x $(PYTHON_VENV) ] && $(STAMP_KEY) check "$$w" $(WITNESS_KEY_INPUTS) 2>/dev/null; then \
	      echo "  ✓ mtime-stale but content-key FRESH — next verify re-keys in seconds (no rebuild)"; \
	    else \
	      echo "  ⚠️  STALE — tracked content changed (or key not yet written); next verify pays a full determinism rebuild (cold ParaNames re-parse)"; \
	    fi; \
	  else echo "  ✓ fresh (determinism-check is a no-op)"; fi
	@echo ""
	@echo "=== Lock / out-of-band consistency (catches lock/out-of-band drift) ==="
	@if [ -x $(PYTHON_VENV) ]; then \
	  $(PYTHON_VENV) -c 'from resecta_data.common.determinism import is_out_of_band; import pathlib; bad = [l.split()[0] for l in pathlib.Path("asset_hashes.lock").read_text().splitlines() if l and not l.startswith("#") and is_out_of_band(l.split()[0])]; print("  ⚠️  lock lists out-of-band entries (hash-check WILL fail): " + ", ".join(bad) if bad else "  ✓ no out-of-band entries in lock")' \
	  || echo "  ⚠️  consistency check failed to run"; \
	else echo "  (venv needed — run: gmake bootstrap)"; fi
	@echo ""
	@echo "=== Bundle-size probe / out-of-band parity (catches probe/out-of-band drift) ==="
	@if [ -x $(PYTHON_VENV) ] && [ -d $(BUILD_DIR) ]; then \
	  $(PYTHON_VENV) -c 'from pathlib import Path; from resecta_data.common.determinism import is_out_of_band; from resecta_data.instrumentation.bundle_size import DEFAULT_SUB_DIRS, _walk_subdir; build = Path("$(BUILD_DIR)"); listed = [p.relative_to(build).as_posix() for sub in DEFAULT_SUB_DIRS for p in _walk_subdir(build, sub)]; leaks = [p for p in listed if is_out_of_band(p)]; print("  ⚠️  probe lists out-of-band files (determinism will fail on a signed tree): " + ", ".join(leaks) if leaks else "  ✓ probe walk excludes all out-of-band files (" + str(len(listed)) + " in-band files listed)")' \
	  || echo "  ⚠️  parity check failed to run"; \
	else echo "  (venv + $(BUILD_DIR)/ needed)"; fi
	@echo ""
	@echo "=== Calibration dumps (Swift-produced) ==="
	@for d in $(SOFTMAX_DUMP) $(SCORE_DUMP); do \
	  if [ -f "$$d" ]; then printf "  ✓ %s (mtime: %s)\n" "$$d" "$$(stat -f '%Sm' "$$d" 2>/dev/null || stat -c '%y' "$$d" 2>/dev/null || echo '?')"; \
	  else echo "  – $$d absent (make calibrate fails with a pointer)"; fi; done
	@echo ""
	@echo "=== Signing key ==="
	@k="$$HOME/.resecta-data/manifest-private-key.pem"; \
	  if [ -f "$$k" ]; then echo "  ✓ $$k present"; else echo "  ⚠️  $$k missing — sign-manifest needs it (or --generate-key for a new pair)"; fi
	@echo ""
	@echo "=== Ingest cache ==="
	@if [ -d $(BUILD_DIR)/gazetteers/_ingest_cache ]; then printf "  size: "; du -sh $(BUILD_DIR)/gazetteers/_ingest_cache 2>/dev/null | cut -f1; \
	else echo "  absent (next bloom build pays the cold ParaNames parse)"; fi
	@echo ""
	@echo "=== Stale workers ==="
	@$(PYTHON) scripts/reap_orphan_workers.py 2>&1 | sed 's/^/  /'

# Read-only orphan listing with the same heuristic as `make doctor`'s
# "Stale workers" section, but as a top-level target so a developer can
# inspect orphans without scrolling through the full doctor output.
.PHONY: doctor-orphans
doctor-orphans: ## List stale resecta-data worker processes (read-only)
	@$(PYTHON) scripts/reap_orphan_workers.py

# Explicit reaper target. Prompts for confirmation before sending
# SIGTERM (escalating to SIGKILL after a 5s grace) to any process the
# heuristic identifies. The reap_orphan_workers.py script itself does
# NOT prompt — the prompt lives here so direct script invocations stay
# scriptable.
.PHONY: reap-orphans
reap-orphans: ## Send SIGTERM to detected orphan workers (with confirmation)
	@$(PYTHON) scripts/reap_orphan_workers.py
	@printf 'Reap detected orphans? [y/N]: '; \
	read c && [ "$$c" = "y" ] && \
		$(PYTHON) scripts/reap_orphan_workers.py --reap || \
		echo "Aborted (no processes signalled)."

# -----------------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build/ (preserves sources/)
	rm -rf $(BUILD_DIR)

.PHONY: distclean
distclean: clean ## Remove build/, .venv/, caches
	rm -rf $(VENV_DIR) .ruff_cache .mypy_cache .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# -----------------------------------------------------------------------------
# Convenience
# -----------------------------------------------------------------------------

.PHONY: all
all: build verify install-assets ## Build, verify, and install into Swift tree

.PHONY: freeze
freeze: bootstrap ## Regenerate requirements.lock from pyproject.toml
	scripts/freeze_deps.sh

.PHONY: shell
shell: bootstrap ## Launch an interactive Python shell with the package importable
	$(PYTHON_VENV)
