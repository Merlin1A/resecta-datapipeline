"""CLI entry points for the data pipeline.

At Phase 0 the CLI provides verification commands only: schema validation,
determinism check, hash-lockfile check, and asset installation. Build
subcommands land in Phase 1+.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import click

from .adversarial import build as build_adversarial_patterns
from .bloom import (
    BloomFilter,
    FilterBuildResult,
    build_manifest,
    optimal_bits,
)
from .bloom import (
    build_cutover_diff as build_name_filters_cutover_diff,
)
from .bloom.corpus_ingest import (
    IngestResult,
    ParseSpec,
    compute_sources_fingerprint,
    ingest_result_from_canonical_dict,
    ingest_result_to_canonical_dict,
    ingest_sources_parallel,
    parse_census_spanish,
    parse_census_spanish_full,
    parse_census_surnames,
    parse_paranames,
    parse_paranames_full,
    parse_popnames_by_type,
    parse_popnames_fullfile,
    parse_ssa_given_names,
)
from .bloom.spec import (
    FPR_TARGET,
    GIVEN_NAME_FILTER_FILE,
    K_HASHES,
    MANIFEST_FILE,
    SURNAME_FILTER_FILE,
)
from .classifier import (
    build_context_scorer,
    build_doctype_keywords,
    build_fit_temperature,
    build_preset_thresholds,
    build_sweep_thresholds,
    finalize_sweep_thresholds,
)
from .common.determinism import (
    CANONICAL_SEED,
    OUT_OF_BAND_PREFIXES,
    assert_hash_seed_pinned,
    diff_hash_maps,
    is_out_of_band,
    snapshot_build_dir,
)
from .common.exceptions import (
    DeterminismError,
    HashMismatchError,
    PipelineError,
    SchemaValidationError,
)
from .common.io import (
    atomic_write_bytes,
    dump_canonical_json,
    iter_build_artifacts,
    load_json,
    read_hash_lockfile,
    sha256_file,
    write_hash_lockfile,
)
from .common.schema import validate_file
from .corpus import build_g8_corpus, build_negative_corpus
from .demographics import build as build_demographics
from .demographics.g8_bucket_recall import build as build_g8_bucket_recall
from .eval import build_compare
from .eval import run as eval_run
from .fuzz import build as build_fuzz_redos
from .gazetteers.address_components import build as build_address_components
from .gazetteers.address_components import (
    build_cutover_diff as build_address_components_cutover_diff,
)
from .gazetteers.context_keywords import build as build_context_keywords
from .gazetteers.dl_patterns import build as build_dl_patterns
from .gazetteers.institutions import build as build_institutions
from .gazetteers.institutions import build_cutover_diff as build_institutions_cutover_diff
from .gazetteers.negative_context import build as build_negative_context
from .gazetteers.negative_context.stage_reviewed import (
    stage_reviewed as stage_reviewed_negative_context,
)
from .gazetteers.nicknames import build as build_nicknames
from .gazetteers.passport_patterns import build as build_passport_patterns
from .gazetteers.zip_scf import build as build_zip_scf
from .instrumentation.bundle_size import DEFAULT_SUB_DIRS as BUNDLE_SIZE_DEFAULT_SUB_DIRS
from .instrumentation.bundle_size import build as build_bundle_size
from .instrumentation.bundle_size import build_meta as build_bundle_size_meta
from .manifest_signing import (
    DEFAULT_PRIVATE_KEY_PATH,
    export_public_key,
    generate_private_key,
    load_private_key,
    sign_manifest_file,
)
from .rules import build as build_rule_catalog
from .vectors import (
    build_bates_vectors,
    build_credit_card_vectors,
    build_dea_vectors,
    build_dob_vectors,
    build_drivers_license_vectors,
    build_ein_vectors,
    build_email_vectors,
    build_itin_vectors,
    build_license_plate_vectors,
    build_mrn_vectors,
    build_npi_vectors,
    build_passport_vectors,
    build_phone_vectors,
    build_routing_number_vectors,
    build_ssn_vectors,
)

logger = logging.getLogger(__name__)


# Mapping from artifact relative-path pattern to schema name.
# Populated per phase as artifacts land.
SCHEMA_ROUTES: dict[str, str] = {
    "vectors/npi_test_vectors.json": "npi_test_vectors",
    "vectors/dea_test_vectors.json": "dea_test_vectors",
    "vectors/ssn_structural_vectors.json": "ssn_structural_vectors",
    "vectors/credit_card_vectors.json": "credit_card_vectors",
    "vectors/ein_vectors.json": "ein_vectors",
    "vectors/itin_vectors.json": "itin_vectors",
    "vectors/dob_vectors.json": "dob_vectors",
    # C4 follow-up vectors (Phone, Email, Passport, DL, MRN, Bates, LicensePlate).
    "vectors/phone_test_vectors.json": "phone_vectors",
    "vectors/email_test_vectors.json": "email_vectors",
    "vectors/passport_test_vectors.json": "passport_vectors",
    "vectors/drivers_license_test_vectors.json": "drivers_license_vectors",
    "vectors/mrn_test_vectors.json": "mrn_vectors",
    "vectors/bates_test_vectors.json": "bates_vectors",
    "vectors/license_plate_test_vectors.json": "license_plate_vectors",
    # S2 task 11 — ABA routing-number vectors (design 01 §4).
    "vectors/routing_number_vectors.json": "routing_number_vectors",
    "gazetteers/zip_scf_states.json": "zip_scf_states",
    "fuzz/redos_payloads.json": "redos_payloads",
    "adversarial/adversarial_patterns.json": "adversarial_patterns",
    # Phase 2
    "gazetteers/gazetteer_manifest.json": "gazetteer_manifest",
    "gazetteers/name_filters.cutover-diff.json": "cutover_diff",
    "gazetteers/negative_context_candidates.json": "negative_context",
    "gazetteers/negative_context.json": "negative_context",
    "gazetteers/institutions.json": "institutions",
    "gazetteers/institutions.cutover-diff.json": "cutover_diff",
    "gazetteers/address_components.json": "address_components",
    "gazetteers/address_components.cutover-diff.json": "cutover_diff",
    # S5 (design 02 §7) — nickname/diminutive sidecar; built only once the
    # CC0 raw source has been fetched (see the Makefile GAZ_NICKNAMES gate).
    "gazetteers/nicknames.json": "nicknames",
    "gazetteers/dl_patterns.json": "dl_patterns",
    "gazetteers/passport_patterns.json": "passport_patterns",
    "context/context_keywords.json": "context_keywords",
    "rules/rule_catalog.json": "rule_catalog",
    "demographics/coverage_report.json": "demographic_coverage",
    "demographics/g8_bucket_recall_v1.json": "g8_bucket_recall",
    "instrumentation/bundle_size.json": "bundle_size",
    # Phase 3
    "classifier/doctype_keywords.json": "doctype_keywords",
    "classifier/preset_thresholds_candidates.json": "preset_thresholds",
    # B03 — in-band context scorer (C1). Both the candidate and the final-named
    # build artifact validate against the same schema; only the final is routed
    # for install (below), and promotion stays Jesse-gated.
    "classifier/context_scorer.json": "context_scorer",
    "classifier/context_scorer_candidates.json": "context_scorer",
    "corpus/g8_corpus.json": "g8_corpus",
    # S3 baseline (eval) — deterministic no-PII negative corpus for the
    # document-level FP measurement. Dev/eval fixture; no INSTALL_ROUTES entry
    # (not shipped), like g8_bucket_recall.
    "corpus/negative_corpus.json": "negative_corpus",
    # S3 baseline (eval) — derived detection baseline + M9 headroom probe.
    # Produced by `resecta-data build eval-baseline` from the Swift harness's
    # _cells.json / _raw_scores.json. Dev/eval only; no INSTALL_ROUTES entry
    # (not shipped), like g8_bucket_recall / negative_corpus.
    "eval/g8_detection_baseline.json": "g8_detection_baseline",
    "eval/g8_headroom.json": "g8_headroom",
    # Phase 3b (produced only when Swift-side dumps are present under
    # build/calibration/).
    "classifier/doctype_temperature.json": "doctype_temperature",
    "classifier/preset_thresholds.json": "preset_thresholds",
}


# Mapping from build/-relative artifact path to (target, subpath) where
# target is "resources" (shipped to Swift Resources/) or "fixtures" (shipped
# to Swift Tests/.../Fixtures/). Artifacts without an entry stay in build/.
#
# Phase 2 ships the dual-Bloom filter bundle to Resources/Gazetteers/. The
# negative-context candidate file and the demographic coverage report stay
# in build/: the former is hand-review-gated, the latter is
# a dev/CI artifact not shipped to end users.
INSTALL_ROUTES: dict[str, tuple[str, str]] = {
    "vectors/npi_test_vectors.json": ("fixtures", "vectors/npi_test_vectors.json"),
    "vectors/dea_test_vectors.json": ("fixtures", "vectors/dea_test_vectors.json"),
    "vectors/ssn_structural_vectors.json": ("fixtures", "vectors/ssn_structural_vectors.json"),
    "vectors/credit_card_vectors.json": ("fixtures", "vectors/credit_card_vectors.json"),
    "vectors/ein_vectors.json": ("fixtures", "vectors/ein_vectors.json"),
    "vectors/itin_vectors.json": ("fixtures", "vectors/itin_vectors.json"),
    "vectors/dob_vectors.json": ("fixtures", "vectors/dob_vectors.json"),
    "vectors/phone_test_vectors.json": ("fixtures", "vectors/phone_test_vectors.json"),
    "vectors/email_test_vectors.json": ("fixtures", "vectors/email_test_vectors.json"),
    "vectors/passport_test_vectors.json": ("fixtures", "vectors/passport_test_vectors.json"),
    "vectors/drivers_license_test_vectors.json": (
        "fixtures",
        "vectors/drivers_license_test_vectors.json",
    ),
    "vectors/mrn_test_vectors.json": ("fixtures", "vectors/mrn_test_vectors.json"),
    "vectors/bates_test_vectors.json": ("fixtures", "vectors/bates_test_vectors.json"),
    "vectors/license_plate_test_vectors.json": (
        "fixtures",
        "vectors/license_plate_test_vectors.json",
    ),
    # S2 task 11 — ABA routing-number vectors (design 01 §4).
    "vectors/routing_number_vectors.json": ("fixtures", "vectors/routing_number_vectors.json"),
    "fuzz/redos_payloads.json": ("fixtures", "fuzz/redos_payloads.json"),
    "adversarial/adversarial_patterns.json": (
        "fixtures",
        "adversarial/adversarial_patterns.json",
    ),
    "gazetteers/surnames.bloom": ("resources", "Gazetteers/surnames.bloom"),
    "gazetteers/given-names.bloom": ("resources", "Gazetteers/given-names.bloom"),
    "gazetteers/gazetteer_manifest.json": ("resources", "Gazetteers/gazetteer-manifest.json"),
    # SEC-6 — Signed manifest peer files. The .sig is the detached Ed25519
    # signature over `gazetteer_manifest.json`; the .pem is the public key
    # the iOS engine verifies against. Both produced by `make sign-manifest`
    # (a `make install-assets` prerequisite).
    "gazetteers/gazetteer_manifest.sig": ("resources", "Gazetteers/gazetteer_manifest.sig"),
    "gazetteers/manifest_public_key.pem": ("resources", "Gazetteers/manifest_public_key.pem"),
    "gazetteers/dl_patterns.json": ("resources", "Gazetteers/dl_patterns.json"),
    "gazetteers/passport_patterns.json": ("resources", "Gazetteers/passport_patterns.json"),
    # Phase 3 AddressDetector landed (ZIPStateTableLoader.swift consumes this
    # at runtime); zip_scf_states ships as a runtime resource (S1 0.7). The
    # iOS copy was previously manually placed — install-assets now owns it.
    "gazetteers/zip_scf_states.json": ("resources", "Gazetteers/zip_scf_states.json"),
    "gazetteers/negative_context.json": ("resources", "Gazetteers/negative-context.json"),
    # S5 (design 02 §§6-8) — institutions and address_components were
    # previously MANUAL copies in the iOS tree; install-assets now owns them.
    # The first routed install reconciles drifted iOS copies (Jesse reviews
    # that diff at install time — landmine note in the S5 session prompt).
    # nicknames.json is the new given-name sidecar; the route is inert until
    # the artifact exists in build/ (post-fetch).
    "gazetteers/institutions.json": ("resources", "Gazetteers/institutions.json"),
    "gazetteers/address_components.json": ("resources", "Gazetteers/address_components.json"),
    "gazetteers/nicknames.json": ("resources", "Gazetteers/nicknames.json"),
    "context/context_keywords.json": ("resources", "Gazetteers/context-keywords.json"),
    "rules/rule_catalog.json": ("resources", "Audit/rule-catalog.json"),
    # Phase 3: doctype keywords ship as a runtime resource.
    # Preset-threshold candidates stay in build/ pending the Phase 3b G9
    # sweep. The G8 corpus is a test fixture.
    "classifier/doctype_keywords.json": ("resources", "Classifier/doctype-keywords.json"),
    # B03 — context scorer ships hyphenated to Classifier/. FINAL only; the
    # `_candidates` artifact stays in build/. shutil.copy2 does no auto-rename,
    # so the hyphenated dest is hard-coded here (D-6). The installed promotion
    # is Jesse-gated — not executed by B03 (which ships a placeholder).
    "classifier/context_scorer.json": ("resources", "Classifier/context-scorer.json"),
    "corpus/g8_corpus.json": ("fixtures", "corpus/g8_corpus.json"),
    # Phase 3b: calibrated artifacts ship to Classifier/. Only present when
    # `make calibrate` has been run against Swift-produced dumps. The
    # placeholder `*_candidates.json` stays in build/ and is not routed.
    "classifier/doctype_temperature.json": ("resources", "Classifier/doctype-temperature.json"),
    "classifier/preset_thresholds.json": ("resources", "Classifier/preset-thresholds.json"),
}


# Routes whose JSON `entries` list must not silently shrink on install. A
# committed shipped file may be an out-of-band superset (e.g. institutions.json
# built on Linux from the full FDIC / federal-agency sources, committed in #27);
# a bare build/ draft must not regress it. See D11-config-golive-F1 / REL-5.
SHRINK_GUARDED_ROUTES: frozenset[str] = frozenset(
    {
        "gazetteers/institutions.json",
    }
)


def _entries_count(path: Path) -> int | None:
    """Return ``len(payload["entries"])`` for an ``entries``-list gazetteer, else None.

    Returns None on any read / parse problem or a non-``entries`` shape, so the
    shrink guard fails open (skips) rather than blocking an unrelated artifact.
    """
    try:
        payload = load_json(path)
    except PipelineError:
        return None
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return len(entries) if isinstance(entries, list) else None


DEBUG_VERBOSITY = 2
"""Verbosity level at which we emit DEBUG-level logs."""


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v, -vv).")
@click.pass_context
def main(ctx: click.Context, verbose: int) -> None:
    """Resecta DataPipeline CLI."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= DEBUG_VERBOSITY:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)


# -----------------------------------------------------------------------------
# Schema validation
# -----------------------------------------------------------------------------


@main.command("validate-schemas")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing generated artifacts.",
)
@click.option(
    "--schemas-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing *.schema.json files.",
)
def validate_schemas_cmd(build_dir: Path, schemas_dir: Path) -> None:
    """Validate every build artifact against its routed schema.

    Artifacts without a routing entry in SCHEMA_ROUTES are skipped with a
    warning. Binary artifacts (e.g., .bloom) are always skipped.
    """
    artifacts = iter_build_artifacts(build_dir)
    if not artifacts:
        click.echo("No artifacts to validate. [Phase 0: expected]")
        return

    failures: list[str] = []
    validated = 0
    skipped: list[str] = []

    for path in artifacts:
        rel = path.relative_to(build_dir).as_posix()
        schema_name = _route_schema(rel)
        if schema_name is None:
            skipped.append(rel)
            continue
        try:
            validate_file(path, schemas_dir, schema_name)
            validated += 1
        except SchemaValidationError as exc:
            failures.append(f"{rel}:\n{exc}")

    click.echo(f"Validated: {validated}")
    if skipped:
        click.echo(f"Skipped (no schema route): {len(skipped)}")
        for s in skipped:
            click.echo(f"  - {s}")

    if failures:
        click.echo("", err=True)
        for f in failures:
            click.echo(f, err=True)
        sys.exit(1)


def _route_schema(relative_path: str) -> str | None:
    """Return the schema name for a given artifact path, or None if not routed.

    Routing is currently exact-match by relative path. Phase 0 has no routes.
    """
    return SCHEMA_ROUTES.get(relative_path)


# -----------------------------------------------------------------------------
# Determinism check
# -----------------------------------------------------------------------------


# The out-of-band prefix list and predicate live in common/determinism.py so
# the bundle-size probe shares them (N7 fix). Aliased here because the CLI is
# the historical home — doctor/regen tooling references cli._is_out_of_band.
_OUT_OF_BAND_PREFIXES = OUT_OF_BAND_PREFIXES
_is_out_of_band = is_out_of_band


# Number of stderr lines retained in memory for failure reporting. The
# rebuild emits tens of MB of output; we only want the tail when the
# rebuild fails so callers see the actionable error without holding the
# full stream.
_STDERR_TAIL_LINES = 200

# Grace period (seconds) between the first SIGTERM to the rebuild's
# process group and the SIGKILL escalation. Long enough for `make` to
# unwind its sub-recipes; short enough that an unresponsive child does
# not delay the parent's exit indefinitely.
_REBUILD_TERM_GRACE_SECONDS = 5.0


def _run_rebuild_streaming(full_command: str) -> tuple[int, deque[str]]:
    """Run the rebuild subprocess with process-group containment + streaming.

    Three properties differ from the previous ``subprocess.run`` invocation:

    1. ``start_new_session=True`` — the child shell becomes the leader of a
       new process group. SIGINT/SIGTERM handlers in the parent call
       ``os.killpg`` against that group so the entire subtree (bash → make
       → builder → pool workers) is reaped together. Without this, an
       interrupted determinism-check leaves orphaned ProcessPoolExecutor
       workers reparented to ``systemd --user``, holding multi-GB RSS
       until they finish — the host-OOM mechanism this guards against.

    2. **Streaming output** — instead of buffering the entire rebuild
       stdout/stderr into the parent's RAM via ``capture_output=True``,
       two daemon threads pump the child's pipes to the parent's
       ``sys.stdout`` / ``sys.stderr`` line by line. This makes long-
       running rebuilds visible in real time and bounds parent-side
       memory use to a small ring buffer.

    3. **Bounded failure context** — the stderr pump retains the final
       ``_STDERR_TAIL_LINES`` lines in a ``deque(maxlen=...)``. On a
       non-zero exit the caller prints just that tail, which is what an
       operator actually wants to see (a 12-minute build's stderr is
       mostly progress noise; the actionable error is at the end).

    Returns the rebuild's exit code and the stderr tail. Re-raises any
    exception from the subprocess pump unmodified after best-effort
    cleanup of the child group.
    """
    stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

    # S602 suppressed: full_command is operator-controlled via --rebuild-command,
    # not user-supplied from untrusted input. Shell execution is intentional so
    # operators can pass shell constructs (e.g. `make build BUILD_DIR=/tmp/foo`).
    proc = subprocess.Popen(  # noqa: S602
        full_command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    pgid = proc.pid

    def _pump(pipe: Any, sink: Any, tail: deque[str] | None) -> None:
        try:
            for line in iter(pipe.readline, ""):
                sink.write(line)
                sink.flush()
                if tail is not None:
                    tail.append(line)
        finally:
            with contextlib.suppress(Exception):
                pipe.close()

    stdout_thread = threading.Thread(
        target=_pump, args=(proc.stdout, sys.stdout, None), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_pump, args=(proc.stderr, sys.stderr, stderr_tail), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    def _terminate_group() -> None:
        # Best-effort: SIGTERM the whole group, give it a grace window,
        # then SIGKILL. Suppress ProcessLookupError because the group may
        # already be gone by the time we reach this branch on a clean exit.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=_REBUILD_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_REBUILD_TERM_GRACE_SECONDS)

    def _signal_handler(signum: int, frame: object) -> None:
        # Make the interruption visible to the operator before the parent
        # exits so they understand why the rebuild stopped.
        click.echo(
            f"\nverify-determinism: received signal {signum}; terminating rebuild "
            f"process group {pgid}.",
            err=True,
        )
        _terminate_group()
        # Re-raise the signal with the default handler so the parent exits
        # with the conventional 128+signum status. Restoring SIG_DFL and
        # re-sending the same signal is the canonical CPython idiom.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    prev_sigint = signal.signal(signal.SIGINT, _signal_handler)
    prev_sigterm = signal.signal(signal.SIGTERM, _signal_handler)
    try:
        returncode = proc.wait()
        # Drain pumps — the pipes are closed by the child exiting; both
        # threads should observe EOF and finish quickly.
        stdout_thread.join(timeout=_REBUILD_TERM_GRACE_SECONDS)
        stderr_thread.join(timeout=_REBUILD_TERM_GRACE_SECONDS)
    except BaseException:
        # Anything from KeyboardInterrupt through unexpected exceptions
        # must still reap the child group before propagating.
        _terminate_group()
        raise
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        # Belt-and-suspenders: if the child somehow survived its own wait()
        # (e.g. the wait raised before completion), reap the group now.
        if proc.poll() is None:
            _terminate_group()

    return returncode, stderr_tail


@main.command("verify-determinism")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing the first build's artifacts.",
)
@click.option(
    "--rebuild-command",
    default="make build",
    show_default=True,
    help="Command to invoke for the second build.",
)
@click.option(
    "--rebuild-out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "When set, rebuild into this directory instead of wiping --build-dir. "
        "BUILD_DIR=<path> is appended to the rebuild command so make builds "
        "into the alternate location, and the original --build-dir is "
        "read-only across the run. The Makefile's verify recipe uses this "
        "to run determinism-check in parallel with verifiers that read "
        "build/."
    ),
)
def verify_determinism_cmd(
    build_dir: Path,
    rebuild_command: str,
    rebuild_out_dir: Path | None,
) -> None:
    """Rebuild and confirm bit-identical output.

    Two modes:

    1. **In-place** (``--rebuild-out-dir`` unset): snapshot ``--build-dir``,
       wipe it, run the rebuild in place, diff. Out-of-band artifacts
       (Swift-produced calibration dumps, hand-reviewed gazetteers) are
       restored across the wipe.

    2. **Side-by-side** (``--rebuild-out-dir`` set): snapshot ``--build-dir``,
       run the rebuild into ``--rebuild-out-dir`` (with
       ``BUILD_DIR=<path>`` appended to the rebuild command so ``make``
       writes there), diff snapshot vs the alternate directory. The
       original ``--build-dir`` is never modified, so out-of-band
       artifacts survive naturally and the determinism step can run in
       parallel with verifiers that read ``--build-dir``.

    Out-of-band artifacts are excluded from the comparison because the
    rebuild command does not regenerate them.

    """
    try:
        assert_hash_seed_pinned()
    except DeterminismError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)

    if rebuild_out_dir is not None and rebuild_out_dir.resolve() == build_dir.resolve():
        click.echo(
            "ERROR: --rebuild-out-dir resolves to the same path as --build-dir; "
            "they must differ for the side-by-side rebuild to make sense.",
            err=True,
        )
        sys.exit(2)

    artifacts = iter_build_artifacts(build_dir)
    if not artifacts:
        click.echo("No artifacts to check. [Phase 0: expected]")
        return

    with tempfile.TemporaryDirectory(prefix="resecta-determinism-") as tmp:
        snapshot_root = Path(tmp) / "snapshot"
        click.echo(f"Snapshotting {build_dir} -> {snapshot_root}")
        first_hashes = snapshot_build_dir(build_dir, snapshot_root)

        if rebuild_out_dir is None:
            # Mode 1: in-place wipe-and-rebuild.
            click.echo(f"Clearing {build_dir}")
            shutil.rmtree(build_dir)
            build_dir.mkdir(parents=True)

            # Restore out-of-band artifacts that the rebuild command does not
            # regenerate (Swift-produced calibration dumps, calibrated outputs,
            # hand-reviewed gazetteers). Preserving them here keeps the user's
            # calibration and review work intact across the verify pipeline.
            preserved = 0
            for rel in first_hashes:
                if not _is_out_of_band(rel):
                    continue
                src = snapshot_root / rel
                dst = build_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                preserved += 1
            if preserved:
                click.echo(f"Preserved {preserved} out-of-band artifact(s) across rebuild")

            full_command = rebuild_command
            second_dir = build_dir
        else:
            # Mode 2: side-by-side rebuild. Original build_dir is read-only;
            # out-of-band artifacts in build_dir are not touched and so do
            # not need to be restored. Cmdline arg overrides Makefile := so
            # the rebuild writes to rebuild_out_dir.
            rebuild_out_dir.mkdir(parents=True, exist_ok=True)
            full_command = f"{rebuild_command} BUILD_DIR={shlex.quote(str(rebuild_out_dir))}"
            second_dir = rebuild_out_dir

        click.echo(f"Rebuilding: {full_command}")
        returncode, tail_stderr = _run_rebuild_streaming(full_command)
        if returncode != 0:
            click.echo(
                "Rebuild failed (last 200 stderr lines):\n" + "".join(tail_stderr),
                err=True,
            )
            sys.exit(returncode)

        second_hashes = {
            p.relative_to(second_dir).as_posix(): sha256_file(p)
            for p in iter_build_artifacts(second_dir)
        }

    # Exclude out-of-band artifacts (Swift-produced dumps, calibration
    # outputs, hand-reviewed gazetteers) from the determinism comparison.
    first_hashes = {k: v for k, v in first_hashes.items() if not _is_out_of_band(k)}
    second_hashes = {k: v for k, v in second_hashes.items() if not _is_out_of_band(k)}

    differences = diff_hash_maps(first_hashes, second_hashes)
    if differences:
        click.echo("Determinism check FAILED:", err=True)
        for d in differences:
            click.echo(f"  {d}", err=True)
        sys.exit(1)

    click.echo(f"Determinism check PASSED ({len(first_hashes)} artifacts)")


# -----------------------------------------------------------------------------
# Hash lockfile
# -----------------------------------------------------------------------------


@main.command("verify-hashes")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--lockfile",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--built-only",
    is_flag=True,
    default=False,
    help=(
        "Verify only artifacts present in the build tree; lockfile entries "
        "with no built file are reported as skipped instead of failing. "
        "Hash mismatches and unlisted artifacts still fail."
    ),
)
def verify_hashes_cmd(build_dir: Path, lockfile: Path, built_only: bool) -> None:
    """Verify every build artifact matches its lockfile entry.

    Fails if:
      - A lockfile entry has no corresponding file (unless --built-only).
      - A file has no lockfile entry.
      - A file's hash does not match the lockfile.

    With --built-only, lockfile entries absent from the build tree are
    listed as skipped and do not fail the check — for hosts that build a
    subset of the artifacts (a fresh checkout without the large fetched
    sources) while still proving every built byte matches the lock.
    """
    expected = read_hash_lockfile(lockfile)
    actual = {
        p.relative_to(build_dir).as_posix(): sha256_file(p) for p in iter_build_artifacts(build_dir)
    }
    # SEC-6 — sign-manifest products are out-of-band relative to `make build`
    # (the rebuild scope hash-check runs against). They're not in the lockfile
    # by design; the iOS verifier is the source of truth for their integrity.
    actual = {k: v for k, v in actual.items() if not _is_out_of_band(k)}

    if not expected and not actual:
        click.echo("No artifacts and empty lockfile. [Phase 0: expected]")
        return

    problems: list[str] = []
    skipped: list[str] = []
    for rel, digest in sorted(expected.items()):
        if rel not in actual:
            if built_only:
                skipped.append(rel)
            else:
                problems.append(f"In lockfile but missing from build: {rel}")
        elif actual[rel] != digest:
            problems.append(
                f"Hash mismatch for {rel}: expected {digest[:12]}..., got {actual[rel][:12]}..."
            )
    for rel in sorted(set(actual) - set(expected)):
        problems.append(f"In build but missing from lockfile: {rel}")

    if problems:
        click.echo("Hash check FAILED:", err=True)
        for p in problems:
            click.echo(f"  {p}", err=True)
        click.echo("", err=True)
        click.echo("See CLAUDE.md §4 — investigate before updating the lockfile.", err=True)
        sys.exit(1)

    if skipped:
        click.echo(f"Skipped {len(skipped)} lockfile entries absent from the build tree:")
        for rel in skipped:
            click.echo(f"  {rel}")
    click.echo(f"Hash check PASSED ({len(actual)} artifacts)")


@main.command("regenerate-lockfile")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--lockfile",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt. Use only when you understand the diff.",
)
def regenerate_lockfile_cmd(build_dir: Path, lockfile: Path, yes: bool) -> None:
    """Write a fresh asset_hashes.lock matching the current build.

    Use this only when you've verified that the artifact changes are intentional.
    Blind regeneration defeats the purpose of the lockfile.
    """
    if not yes:
        click.confirm(
            "This overwrites asset_hashes.lock with current build hashes. "
            "Have you reviewed the intended changes?",
            abort=True,
        )
    entries = {
        p.relative_to(build_dir).as_posix(): sha256_file(p) for p in iter_build_artifacts(build_dir)
    }
    # SEC-6 — exclude sign-manifest output (out-of-band relative to `make build`).
    entries = {k: v for k, v in entries.items() if not _is_out_of_band(k)}
    write_hash_lockfile(lockfile, entries)
    click.echo(f"Wrote {len(entries)} entries to {lockfile}")


# -----------------------------------------------------------------------------
# Stage reviewed negative-context (D6)
# -----------------------------------------------------------------------------


@main.command("stage-reviewed-negctx")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--reviewed-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory holding the committed reviewed files. Defaults to "
        "src/resecta_data/gazetteers/negative_context/reviewed/."
    ),
)
def stage_reviewed_negctx_cmd(build_dir: Path, reviewed_dir: Path | None) -> None:
    """Stage the committed reviewed negative_context.json into build/ (D6).

    Verifies the meta sidecar's ``reviewed_version`` against the live
    candidates hash before copying — a mismatch means the candidates
    changed since Jesse's review and staging refuses.
    """
    if reviewed_dir is None:
        staged = stage_reviewed_negative_context(build_dir)
    else:
        staged = stage_reviewed_negative_context(build_dir, reviewed_dir)
    for dest in staged:
        click.echo(f"Staged {dest}")
    click.echo("Reviewed negative_context.json staged into build/ (survives make clean).")


# -----------------------------------------------------------------------------
# Install assets
# -----------------------------------------------------------------------------


@main.command("install-assets")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--resources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Target: Packages/RedactionEngine/Sources/RedactionEngine/Resources/",
)
@click.option(
    "--fixtures-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Target: Packages/RedactionEngine/Tests/RedactionEngineTests/Fixtures/",
)
@click.option(
    "--allow-shrink",
    is_flag=True,
    default=False,
    help=(
        "Permit a shrink-guarded gazetteer (e.g. institutions.json) to be "
        "overwritten by a smaller build/ artifact. Required only after the S5 "
        "institutions fetches make build/ a verified superset. Off by default "
        "so a bare refresh cannot regress a committed corpus."
    ),
)
def install_assets_cmd(
    build_dir: Path,
    resources_dir: Path,
    fixtures_dir: Path,
    allow_shrink: bool,
) -> None:
    """Copy artifacts from build/ into the Swift tree.

    Routing is defined in ``INSTALL_ROUTES``. Each route names a target
    ("resources" or "fixtures") and a path within that target directory.
    Artifacts without an install route remain in build/ and are not copied.
    """
    artifacts = iter_build_artifacts(build_dir)
    if not artifacts:
        click.echo("Nothing to install.")
        return

    targets = {"resources": resources_dir, "fixtures": fixtures_dir}
    copied = 0
    skipped_no_route: list[str] = []

    for path in artifacts:
        rel = path.relative_to(build_dir).as_posix()
        route = INSTALL_ROUTES.get(rel)
        if route is None:
            skipped_no_route.append(rel)
            continue
        target_name, sub_path = route
        if target_name not in targets:
            raise PipelineError(
                f"{rel}: unknown install target {target_name!r}; expected one of {sorted(targets)}"
            )
        dest = targets[target_name] / sub_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        # D11-config-golive-F1 shrink-guard — fail loud before overwriting a
        # larger committed file. Acts only when the route is shrink-guarded, the
        # dest already exists, both parse as `entries`-list gazetteers, and the
        # source has strictly fewer entries. Growth / equal / non-gazetteer /
        # unparseable paths fall through to the copy unchanged.
        if rel in SHRINK_GUARDED_ROUTES and dest.exists() and not allow_shrink:
            src_n = _entries_count(path)
            dst_n = _entries_count(dest)
            if src_n is not None and dst_n is not None and src_n < dst_n:
                raise PipelineError(
                    f"{rel}: refusing to shrink shipped gazetteer "
                    f"{dst_n} -> {src_n} entries. The committed file is a "
                    f"superset; complete the S5 institutions fetches + "
                    f"`gmake gazetteers` so build/ is >= shipped, or pass "
                    f"--allow-shrink to override. See cutover-diff "
                    f"build/gazetteers/institutions.cutover-diff.json "
                    f"(legacy_only entries are the names that would be dropped)."
                )
        shutil.copy2(path, dest)
        if sha256_file(dest) != sha256_file(path):
            raise PipelineError(f"Install verify failed: {dest} differs from {path}")
        copied += 1

    click.echo(f"Installed: {copied}")
    if skipped_no_route:
        click.echo(f"Held in build/ (no install route): {len(skipped_no_route)}")
        for r in skipped_no_route:
            click.echo(f"  - {r}")


# -----------------------------------------------------------------------------
# Sign manifest (SEC-6 — paired iOS PR)
# -----------------------------------------------------------------------------


@main.command("sign-manifest")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing build/gazetteers/gazetteer_manifest.json.",
)
@click.option(
    "--private-key",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to the Ed25519 private key PEM. Defaults to "
        "~/.resecta-data/manifest-private-key.pem (gitignored — outside "
        "both repos so the key never enters git history)."
    ),
)
@click.option(
    "--generate-key",
    is_flag=True,
    default=False,
    help=(
        "Generate a new Ed25519 private key at --private-key if it does "
        "not already exist. Rotation cadence is per major release; this "
        "flag exists for the initial keypair only."
    ),
)
def sign_manifest_cmd(
    build_dir: Path,
    private_key: Path | None,
    generate_key: bool,
) -> None:
    """Sign ``build/gazetteers/gazetteer_manifest.json`` with Ed25519.

    Writes ``gazetteer_manifest.sig`` and ``manifest_public_key.pem`` next
    to the manifest. Both files are picked up by ``install-assets`` via the
    routing entries above and copied into the iOS Resources/Gazetteers/
    tree. The iOS engine verifies the signature at detector init
    (see GazetteerLoader.swift / SEC-6).

    Cross-boundary wire-format changes need a paired Swift PR
    (plan.md §3 SEC-6: Ed25519, rotation per major release).
    """
    key_path = private_key if private_key is not None else DEFAULT_PRIVATE_KEY_PATH

    if generate_key:
        if key_path.exists():
            click.echo(f"Key already exists at {key_path}; refusing to overwrite.")
        else:
            generate_private_key(key_path)
            click.echo(f"Generated new Ed25519 private key at {key_path}")

    pk = load_private_key(key_path)

    manifest_path = build_dir / "gazetteers" / "gazetteer_manifest.json"
    if not manifest_path.is_file():
        raise click.ClickException(
            f"Manifest not found at {manifest_path}. Run `make bloom` first."
        )

    signature_path = sign_manifest_file(manifest_path, pk)
    public_key_path = build_dir / "gazetteers" / "manifest_public_key.pem"
    export_public_key(pk, public_key_path)

    click.echo(f"Signed: {signature_path.relative_to(build_dir)}")
    click.echo(f"Public key: {public_key_path.relative_to(build_dir)}")


# -----------------------------------------------------------------------------
# Build subcommands (Phase 1)
# -----------------------------------------------------------------------------


@main.group("build")
def build_group() -> None:
    """Generate Phase 1+ artifacts into build/."""


_VECTOR_BUILDERS: dict[str, Callable[[int], dict[str, Any]]] = {
    "npi": build_npi_vectors,
    "dea": build_dea_vectors,
    "ssn": build_ssn_vectors,
    "credit-card": build_credit_card_vectors,
    "ein": build_ein_vectors,
    "itin": build_itin_vectors,
    "dob": build_dob_vectors,
    "phone": build_phone_vectors,
    "email": build_email_vectors,
    "passport": build_passport_vectors,
    "drivers-license": build_drivers_license_vectors,
    "mrn": build_mrn_vectors,
    "bates": build_bates_vectors,
    "license-plate": build_license_plate_vectors,
    "routing-number": build_routing_number_vectors,
}

_VECTOR_OUTPUT_FILENAMES: dict[str, str] = {
    "npi": "npi_test_vectors.json",
    "dea": "dea_test_vectors.json",
    "ssn": "ssn_structural_vectors.json",
    "credit-card": "credit_card_vectors.json",
    "ein": "ein_vectors.json",
    "itin": "itin_vectors.json",
    "dob": "dob_vectors.json",
    "phone": "phone_test_vectors.json",
    "email": "email_test_vectors.json",
    "passport": "passport_test_vectors.json",
    "drivers-license": "drivers_license_test_vectors.json",
    "mrn": "mrn_test_vectors.json",
    "bates": "bates_test_vectors.json",
    "license-plate": "license_plate_test_vectors.json",
    "routing-number": "routing_number_vectors.json",
}


_VECTOR_KINDS: tuple[str, ...] = (
    "npi",
    "dea",
    "ssn",
    "credit-card",
    "ein",
    "itin",
    "dob",
    "phone",
    "email",
    "passport",
    "drivers-license",
    "mrn",
    "bates",
    "license-plate",
    "routing-number",
)


@build_group.command("vectors")
@click.argument(
    "kind",
    type=click.Choice([*_VECTOR_KINDS, "all"]),
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
    help="PRNG seed. Default is the canonical seed; vary only for ablation.",
)
def build_vectors_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build Phase 1 test vectors (checksummed + structural PII detectors)."""
    assert_hash_seed_pinned()
    selected = list(_VECTOR_KINDS) if kind == "all" else [kind]
    for name in selected:
        payload = _VECTOR_BUILDERS[name](seed)
        dest = build_dir / "vectors" / _VECTOR_OUTPUT_FILENAMES[name]
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['vectors'])} vectors)")


@build_group.command("zip-scf")
@click.option(
    "--source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the HUD ZIP-to-state crosswalk CSV.",
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--retrieval-date",
    required=True,
    help="ISO date the source was fetched (YYYY-MM-DD). Must match SOURCES.md.",
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_zip_scf_cmd(
    source: Path,
    build_dir: Path,
    retrieval_date: str,
    seed: int,
) -> None:
    """Build the ZIP → SCF → state table from a HUD crosswalk CSV."""
    assert_hash_seed_pinned()
    payload = build_zip_scf(seed, source=source, retrieval_date=retrieval_date)
    dest = build_dir / "gazetteers" / "zip_scf_states.json"
    dump_canonical_json(payload, dest)
    click.echo(
        f"Wrote {dest} ({len(payload['scf_table'])} SCF rows, "
        f"{len(payload['overrides'])} overrides)"
    )


@build_group.command("fuzz")
@click.argument("kind", type=click.Choice(["redos"]))
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_fuzz_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build fuzz payload catalogs."""
    assert_hash_seed_pinned()
    if kind == "redos":
        payload = build_fuzz_redos(seed)
        dest = build_dir / "fuzz" / "redos_payloads.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['payloads'])} payloads)")


@build_group.command("adversarial")
@click.argument("kind", type=click.Choice(["patterns"]))
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_adversarial_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build adversarial pattern fixtures."""
    assert_hash_seed_pinned()
    if kind == "patterns":
        payload = build_adversarial_patterns(seed)
        dest = build_dir / "adversarial" / "adversarial_patterns.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['patterns'])} patterns)")


# -----------------------------------------------------------------------------
# Build subcommands (Phase 2)
# -----------------------------------------------------------------------------
#
# Deterministic build timestamp. Wall-clock content is banned from
# artifact payloads; the manifest builtAt field is included in the hash lock,
# so we pin it to the canonical build date rather than call datetime.now().
_BUILD_YEAR = CANONICAL_SEED // 10000
_BUILD_MONTH = (CANONICAL_SEED // 100) % 100
_BUILD_DAY = CANONICAL_SEED % 100
_DEFAULT_BUILD_DATE = f"{_BUILD_YEAR:04d}-{_BUILD_MONTH:02d}-{_BUILD_DAY:02d}T00:00:00Z"

_DEFAULT_SOURCES_DIR = Path("src/resecta_data/gazetteers/sources")

# The committed File-5 fire-features dump (Swift-harness output, force-tracked
# under build/; no producing recipe). Repo-root-relative so the in-band scorer
# fit reads identical bytes in the side-by-side determinism rebuild, where the
# rebuild out-dir holds no dump (the candidates artifact must stay byte-stable).
_COMMITTED_FIRE_FEATURES_DUMP = Path("build/corpus/g8_fire_features.json")

# Subdirectory under paranames/ where ``scripts/shard_paranames.py`` lands
# pre-split PER-block shards. When present, we fan the ingest across shards
# to break the single-worker gzip-decompression ceiling inside
# ``parse_paranames_full``. All shards carry the canonical source_id
# ``"paranames_full"`` so ``per_source_counts`` is identical to the
# single-file build.
_PARANAMES_SHARD_SUBDIR = "shards"
_PARANAMES_SHARD_GLOB = "paranames_full_shard_*.tsv.gz"


# Mirrors the Makefile's hydration probe: an LFS pointer file is ~130 B
# text; the real ParaNames corpus is ~954 MB. Size threshold (>1 MB)
# cleanly separates the two.
_PARANAMES_MIN_HYDRATED_BYTES = 1_000_000


def _paranames_full_specs(sources_dir: Path) -> list[ParseSpec]:
    """Return shard specs if a shards dir exists, else the single-file spec.

    Shard files are discovered under
    ``<sources_dir>/paranames/shards/paranames_full_shard_*.tsv.gz``. Order is
    deterministic (lexicographic by filename) so ``parse_sources_parallel``
    produces rows in a stable order across runs and machines.

    Shards are produced offline by ``scripts/shard_paranames.py``; when the
    shards/ directory is absent, the monolithic ``paranames_full.tsv.gz`` is
    used exactly as before. Returns an empty list if neither is present —
    LOUDLY (0.6 guard): the bloom build then ingests only the ~3k bootstrap
    sample, which must never happen silently. Set ``RESECTA_REQUIRE_LFS=1``
    to turn the warning into a hard failure (matches the Makefile guard).

    Raises:
        PipelineError: If the monolithic file is present but pointer-sized
            (unhydrated LFS checkout), or if the full corpus is absent and
            ``RESECTA_REQUIRE_LFS=1``.
    """
    shards_dir = sources_dir / "paranames" / _PARANAMES_SHARD_SUBDIR
    if shards_dir.is_dir():
        shard_paths = sorted(shards_dir.glob(_PARANAMES_SHARD_GLOB))
        if shard_paths:
            return [
                partial(
                    parse_paranames_full,
                    shard_path,
                    "paranames_full",
                    min_languages=5,
                )
                for shard_path in shard_paths
            ]
    full_paranames = sources_dir / "paranames/paranames_full.tsv.gz"
    if full_paranames.is_file():
        if full_paranames.stat().st_size < _PARANAMES_MIN_HYDRATED_BYTES:
            raise PipelineError(
                f"{full_paranames} is pointer-sized "
                f"({full_paranames.stat().st_size} bytes) — an unhydrated "
                "git-lfs checkout. gzip would fail on it mid-ingest. "
                "Hydrate with: git lfs install && git lfs pull"
            )
        return [
            partial(
                parse_paranames_full,
                full_paranames,
                "paranames_full",
                min_languages=5,
            )
        ]
    if os.environ.get("RESECTA_REQUIRE_LFS") == "1":
        raise PipelineError(
            f"ParaNames full corpus not found under {sources_dir / 'paranames'} "
            "and RESECTA_REQUIRE_LFS=1. Hydrate with: git lfs install && "
            "git lfs pull (or scripts/fetch_paranames.sh)."
        )
    logger.warning(
        "ParaNames full corpus not found under %s — the bloom filters will "
        "be built from the ~3k bootstrap sample only. Hydrate with "
        "`git lfs pull` or scripts/fetch_paranames.sh; set "
        "RESECTA_REQUIRE_LFS=1 to make this a hard failure.",
        sources_dir / "paranames",
    )
    return []


def _spec_inputs(specs: list[ParseSpec]) -> list[tuple[Path, str]]:
    """Extract (path, source_id) from each spec for cache fingerprinting.

    All specs in this module are built via ``functools.partial(parser, path,
    source_id, ...)``; we peek at ``.args`` to recover them. A spec that does
    not follow this shape is skipped (the fingerprint is a best-effort key,
    not a proof).
    """
    inputs: list[tuple[Path, str]] = []
    min_spec_positional_args = 2
    for spec in specs:
        args: tuple[Any, ...] = getattr(spec, "args", ())
        if (
            len(args) >= min_spec_positional_args
            and isinstance(args[0], Path)
            and isinstance(args[1], str)
        ):
            inputs.append((args[0], args[1]))
    return inputs


def _surname_ingest_specs(sources_dir: Path) -> list[ParseSpec]:
    """Canonical-order parse specs for the surname filter.

    Order matters: ``merge()`` attributes duplicate keys to the first-seen
    source, so reordering this list changes per-source counts even though
    the final row set is identical. Do not reorder without a compelling
    reason — and update ``tests/test_bloom_determinism.py`` if you do.
    """
    specs: list[ParseSpec] = []
    full_census = sources_dir / "census_surnames/Names_2010Census.csv"
    if full_census.is_file():
        specs.append(partial(parse_census_surnames, full_census, "census_surnames"))
    specs.append(
        partial(
            parse_census_surnames,
            sources_dir / "census_surnames/census_surnames_bootstrap_20260416.csv",
            "census_surnames",
        )
    )
    full_census_spanish = sources_dir / "census_spanish/census_spanish_full_20260419.zip"
    if full_census_spanish.is_file():
        specs.append(partial(parse_census_spanish_full, full_census_spanish, "census_spanish_full"))
    specs.append(
        partial(
            parse_census_spanish,
            sources_dir / "census_spanish/census_spanish_bootstrap_20260416.txt",
            "census_spanish",
        )
    )
    specs.append(
        partial(
            parse_paranames,
            sources_dir / "paranames/paranames_bootstrap_20260416.tsv",
            "paranames",
        )
    )
    specs.extend(_paranames_full_specs(sources_dir))
    full_popnames_surnames = sources_dir / "popnames/common-surnames-by-country.csv"
    if full_popnames_surnames.is_file():
        specs.append(
            partial(
                parse_popnames_fullfile,
                full_popnames_surnames,
                "popnames_common_surnames",
                "surname",
            )
        )
    specs.append(
        partial(
            parse_popnames_by_type,
            sources_dir / "popnames/popnames_bootstrap_20260416.csv",
            "popnames",
            "surname",
        )
    )
    return specs


def _given_name_ingest_specs(sources_dir: Path) -> list[ParseSpec]:
    """Canonical-order parse specs for the given-name filter. See _surname_ingest_specs."""
    specs: list[ParseSpec] = []
    full_ssa = sources_dir / "ssa_given_names/yob2024.txt"
    if full_ssa.is_file():
        specs.append(partial(parse_ssa_given_names, full_ssa, "ssa_given_names"))
    specs.append(
        partial(
            parse_ssa_given_names,
            sources_dir / "ssa_given_names/ssa_bootstrap_20260416.txt",
            "ssa_given_names",
        )
    )
    specs.append(
        partial(
            parse_paranames,
            sources_dir / "paranames/paranames_bootstrap_20260416.tsv",
            "paranames",
        )
    )
    specs.extend(_paranames_full_specs(sources_dir))
    full_popnames_forenames = sources_dir / "popnames/common-forenames-by-country.csv"
    if full_popnames_forenames.is_file():
        specs.append(
            partial(
                parse_popnames_fullfile,
                full_popnames_forenames,
                "popnames_common_forenames",
                "given",
            )
        )
    specs.append(
        partial(
            parse_popnames_by_type,
            sources_dir / "popnames/popnames_bootstrap_20260416.csv",
            "popnames",
            "given",
        )
    )
    return specs


_INGEST_CACHE_SUBDIR = Path("gazetteers") / "_ingest_cache"


def _load_cached_ingest(
    cache_path: Path,
    fingerprint: str,
    sources: list[str],
) -> tuple[IngestResult, list[str]] | None:
    """Return the cached ingest iff the fingerprint matches, else ``None``.

    Any malformed cache payload is treated as a miss — the write path will
    overwrite with a fresh build. We deliberately do not raise on malformed
    caches because the file is ephemeral (git-ignored under ``build/``).
    """
    if not cache_path.is_file():
        return None
    try:
        data = load_json(cache_path)
    except PipelineError:
        return None
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint:
        return None
    try:
        ingest = ingest_result_from_canonical_dict(data["ingest"])
    except (PipelineError, KeyError):
        return None
    return ingest, sources


def _write_cached_ingest(
    cache_path: Path,
    fingerprint: str,
    ingest: IngestResult,
) -> None:
    payload = {
        "fingerprint": fingerprint,
        "ingest": ingest_result_to_canonical_dict(ingest),
    }
    dump_canonical_json(payload, cache_path)


def _ingest_surnames(
    sources_dir: Path,
    *,
    workers: int | None = None,
    cache_dir: Path | None = None,
) -> tuple[IngestResult, list[str]]:
    return _ingest_with_cache(
        specs=_surname_ingest_specs(sources_dir),
        workers=workers,
        cache_dir=cache_dir,
        cache_name="surnames.json",
    )


def _ingest_given_names(
    sources_dir: Path,
    *,
    workers: int | None = None,
    cache_dir: Path | None = None,
) -> tuple[IngestResult, list[str]]:
    return _ingest_with_cache(
        specs=_given_name_ingest_specs(sources_dir),
        workers=workers,
        cache_dir=cache_dir,
        cache_name="given-names.json",
    )


def _ingest_with_cache(
    *,
    specs: list[ParseSpec],
    workers: int | None,
    cache_dir: Path | None,
    cache_name: str,
) -> tuple[IngestResult, list[str]]:
    """Parse, merge, and optionally cache an ingest.

    When ``cache_dir`` is provided, the fingerprint of the spec inputs is
    checked against ``cache_dir / cache_name``; on match, the cached result is
    returned and no parsing runs. On miss, the spec is parsed, merged, and
    the resulting payload is written atomically to the cache path before
    returning. The cache file is not a shipped artifact — it lives under an
    ``_ingest_cache/`` directory that ``iter_build_artifacts`` skips, so it
    is outside the hash-lock, schema-check, determinism-diff, and
    install-assets surfaces. The determinism rebuild derives its cache path
    from its own fresh build dir (empty cache ⇒ full re-parse), which is the
    parse-level determinism gate; see tests/test_determinism_cache_isolation.py.
    """
    inputs = _spec_inputs(specs)
    # Sources list is derived purely from the source_ids of wired specs, so
    # we can compute it without parsing — important for cache-hit paths.
    sources = sorted({source_id for _path, source_id in inputs})

    cache_path: Path | None = None
    fingerprint: str | None = None
    if cache_dir is not None and inputs:
        cache_path = cache_dir / cache_name
        fingerprint = compute_sources_fingerprint(inputs)
        hit = _load_cached_ingest(cache_path, fingerprint, sources)
        if hit is not None:
            logger.info("ingest cache hit: %s", cache_path)
            return hit

    # Workers aggregate (dedup) rows before they cross the process boundary;
    # byte-equivalent to merge(parse_sources_parallel(...)) but the live set
    # stays bounded — see ingest_sources_parallel and the S04 design note.
    ingest, seen_source_ids = ingest_sources_parallel(specs, workers=workers)
    # Derive sources from actual rows (covers specs whose .args we couldn't
    # introspect); seen_source_ids is already sorted.
    sources = list(seen_source_ids)

    if cache_path is not None and fingerprint is not None:
        _write_cached_ingest(cache_path, fingerprint, ingest)

    return ingest, sources


def _build_one_filter(ingest: IngestResult, seed: int) -> tuple[BloomFilter, int]:
    m = max(optimal_bits(len(ingest.rows), FPR_TARGET), 8)
    bf = BloomFilter.build_parallel(m=m, k=K_HASHES, seed=seed, keys=ingest.rows)
    return bf, m


@build_group.command("bloom")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--sources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=_DEFAULT_SOURCES_DIR,
    show_default=True,
    help="Directory containing the name-corpus source subdirectories.",
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
@click.option(
    "--build-date",
    default=_DEFAULT_BUILD_DATE,
    show_default=True,
    help="ISO-8601 UTC timestamp recorded in the manifest. Pinned for determinism.",
)
def build_bloom_cmd(build_dir: Path, sources_dir: Path, seed: int, build_date: str) -> None:
    """Build surnames.bloom + given-names.bloom + gazetteer_manifest.json."""
    assert_hash_seed_pinned()

    cache_dir = build_dir / _INGEST_CACHE_SUBDIR
    surname_ingest, surname_sources = _ingest_surnames(sources_dir, cache_dir=cache_dir)
    given_ingest, given_sources = _ingest_given_names(sources_dir, cache_dir=cache_dir)

    surname_bf, surname_m = _build_one_filter(surname_ingest, seed)
    given_bf, given_m = _build_one_filter(given_ingest, seed)

    surname_bytes = surname_bf.serialize(surname_ingest.source_hash)
    given_bytes = given_bf.serialize(given_ingest.source_hash)

    atomic_write_bytes(build_dir / "gazetteers" / SURNAME_FILTER_FILE, surname_bytes)
    atomic_write_bytes(build_dir / "gazetteers" / GIVEN_NAME_FILTER_FILE, given_bytes)

    filters = [
        FilterBuildResult(
            name="surnames",
            type_="surname",
            n=len(surname_ingest.rows),
            m=surname_m,
            k=K_HASHES,
            fpr_target=FPR_TARGET,
            sources=tuple(surname_sources),
        ),
        FilterBuildResult(
            name="given-names",
            type_="givenName",
            n=len(given_ingest.rows),
            m=given_m,
            k=K_HASHES,
            fpr_target=FPR_TARGET,
            sources=tuple(given_sources),
        ),
    ]
    manifest = build_manifest(filters, seed=seed, built_at=build_date)
    dump_canonical_json(manifest, build_dir / "gazetteers" / MANIFEST_FILE)

    cutover_diff = build_name_filters_cutover_diff(filters)
    cutover_dest = build_dir / "gazetteers" / "name_filters.cutover-diff.json"
    dump_canonical_json(cutover_diff, cutover_dest)
    summary = cutover_diff["summary"]

    click.echo(
        f"Wrote {SURNAME_FILTER_FILE} ({len(surname_ingest.rows)} keys, m={surname_m}), "
        f"{GIVEN_NAME_FILTER_FILE} ({len(given_ingest.rows)} keys, m={given_m}), {MANIFEST_FILE}; "
        f"{cutover_dest.name} (legacy_only={summary['legacy_only_count']}, "
        f"rebuild_only={summary['rebuild_only_count']}, "
        f"keyed_diff={summary['keyed_diff_count']})"
    )


@build_group.command("gazetteers")
@click.argument(
    "kind",
    type=click.Choice(
        [
            "negative-context",
            "institutions",
            "address-components",
            "dl-patterns",
            "passport-patterns",
            "nicknames",
        ]
    ),
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--sources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=_DEFAULT_SOURCES_DIR / "negative_context",
    show_default=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_gazetteers_cmd(kind: str, build_dir: Path, sources_dir: Path, seed: int) -> None:
    """Build non-Bloom gazetteer artifacts."""
    assert_hash_seed_pinned()
    if kind == "negative-context":
        payload = build_negative_context(seed, source_dir=sources_dir)
        dest = build_dir / "gazetteers" / "negative_context_candidates.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['entries'])} candidate entries)")
    elif kind == "institutions":
        payload = build_institutions(seed)
        dest = build_dir / "gazetteers" / "institutions.json"
        dump_canonical_json(payload, dest)
        cutover_diff = build_institutions_cutover_diff()
        cutover_dest = build_dir / "gazetteers" / "institutions.cutover-diff.json"
        dump_canonical_json(cutover_diff, cutover_dest)
        summary = cutover_diff["summary"]
        click.echo(
            f"Wrote {dest} ({len(payload['entries'])} institution entries); "
            f"{cutover_dest} (legacy_only={summary['legacy_only_count']}, "
            f"rebuild_only={summary['rebuild_only_count']}, "
            f"keyed_diff={summary['keyed_diff_count']})"
        )
    elif kind == "address-components":
        payload = build_address_components(seed)
        dest = build_dir / "gazetteers" / "address_components.json"
        dump_canonical_json(payload, dest)
        cutover_diff = build_address_components_cutover_diff()
        cutover_dest = build_dir / "gazetteers" / "address_components.cutover-diff.json"
        dump_canonical_json(cutover_diff, cutover_dest)
        summary = cutover_diff["summary"]
        click.echo(
            f"Wrote {dest} ({len(payload['cities'])} cities, "
            f"{len(payload['counties'])} counties, "
            f"{len(payload['street_types'])} street types); "
            f"{cutover_dest} (legacy_only={summary['legacy_only_count']}, "
            f"rebuild_only={summary['rebuild_only_count']}, "
            f"keyed_diff={summary['keyed_diff_count']})"
        )
    elif kind == "nicknames":
        payload = build_nicknames(seed)
        dest = build_dir / "gazetteers" / "nicknames.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['entries'])} nickname entries)")
    elif kind == "dl-patterns":
        payload = build_dl_patterns(seed)
        dest = build_dir / "gazetteers" / "dl_patterns.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['rows'])} dl-pattern rows)")
    elif kind == "passport-patterns":
        payload = build_passport_patterns(seed)
        dest = build_dir / "gazetteers" / "passport_patterns.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['rows'])} passport-pattern rows)")


@build_group.command("context")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_context_cmd(build_dir: Path, seed: int) -> None:
    """Build per-category positive context-keyword gazetteer (D-11 / A21)."""
    assert_hash_seed_pinned()
    payload = build_context_keywords(seed)
    dest = build_dir / "context" / "context_keywords.json"
    dump_canonical_json(payload, dest)
    click.echo(f"Wrote {dest} ({len(payload['entries'])} context-keyword entries)")


@build_group.command("rules")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_rules_cmd(build_dir: Path, seed: int) -> None:
    """Build PII detector rule-ID catalog (D-18 / A22)."""
    assert_hash_seed_pinned()
    payload = build_rule_catalog(seed)
    dest = build_dir / "rules" / "rule_catalog.json"
    dump_canonical_json(payload, dest)
    click.echo(f"Wrote {dest} ({len(payload['entries'])} rule-catalog entries)")


@build_group.command("demographics")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--sources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=_DEFAULT_SOURCES_DIR,
    show_default=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_demographics_cmd(build_dir: Path, sources_dir: Path, seed: int) -> None:
    """Build the demographic coverage report from the current name corpora."""
    assert_hash_seed_pinned()

    cache_dir = build_dir / _INGEST_CACHE_SUBDIR
    surname_ingest, surname_sources = _ingest_surnames(sources_dir, cache_dir=cache_dir)
    given_ingest, given_sources = _ingest_given_names(sources_dir, cache_dir=cache_dir)

    payload = build_demographics(
        seed,
        ingests={"surnames": surname_ingest, "given-names": given_ingest},
        source_ids=[*surname_sources, *given_sources],
    )
    dest = build_dir / "demographics" / "coverage_report.json"
    dump_canonical_json(payload, dest)
    click.echo(
        f"Wrote {dest} (parity gap {payload['parity_gap_points']:.2f} pt across "
        f"{len(payload['filters'])} filters)"
    )


@build_group.command("g8-bucket-recall")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_g8_bucket_recall_cmd(build_dir: Path, seed: int) -> None:
    """Build the G8 bucket-stratified recall artifact (D-24).

    Reads the G8 corpus and the surnames Bloom filter from ``build_dir`` and
    emits ``demographics/g8_bucket_recall_v1.json``. V1 one-off measurement
    (spec §1.24 + F-10 ack option a).
    """
    assert_hash_seed_pinned()

    corpus_path = build_dir / "corpus" / "g8_corpus.json"
    bloom_path = build_dir / "gazetteers" / "surnames.bloom"

    if not corpus_path.is_file():
        raise click.UsageError(f"G8 corpus not found at {corpus_path}; run `make corpus` first.")
    if not bloom_path.is_file():
        raise click.UsageError(f"surnames.bloom not found at {bloom_path}; run `make bloom` first.")

    corpus = load_json(corpus_path)
    surnames_bloom_bytes = bloom_path.read_bytes()
    bloom_version_sha256 = sha256_file(bloom_path)

    payload = build_g8_bucket_recall(
        seed,
        corpus=corpus,
        surnames_bloom_bytes=surnames_bloom_bytes,
        bloom_version_sha256=bloom_version_sha256,
    )
    dest = build_dir / "demographics" / "g8_bucket_recall_v1.json"
    dump_canonical_json(payload, dest)
    summary = ", ".join(
        f"{label}={payload['buckets'][label]['recall_pts']:.2f}"
        for label in ("white", "black", "hispanic", "asian", "ai_an")
    )
    click.echo(f"Wrote {dest} (recall_pts: {summary})")


@build_group.command("negative-corpus")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_negative_corpus_cmd(build_dir: Path, seed: int) -> None:
    """Build the deterministic no-PII negative corpus (S3 baseline FP surface).

    Emits ``corpus/negative_corpus.json``: benign documents that contain no
    valid PII, so any detection the engine surfaces against them is a false
    positive. Dev/eval fixture; not installed to the Swift Resources path.
    """
    assert_hash_seed_pinned()
    payload = build_negative_corpus(seed)
    dest = build_dir / "corpus" / "negative_corpus.json"
    dump_canonical_json(payload, dest)
    click.echo(f"Wrote {dest} ({len(payload['documents'])} documents)")


@build_group.command("eval-baseline")
@click.option(
    "--cells",
    "cells_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the Swift harness _cells.json (CONTRACT.md File 1).",
)
@click.option(
    "--raw-scores",
    "raw_scores_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the Swift harness _raw_scores.json (CONTRACT.md File 2).",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory for g8_detection_baseline.json + g8_headroom.json.",
)
def build_eval_baseline_cmd(cells_path: Path, raw_scores_path: Path, out_dir: Path) -> None:
    """Derive the S3 detection baseline + M9 headroom from the Swift JSONs.

    Loads the harness's offset-overlap join cells and raw match scores and
    writes ``g8_detection_baseline.json`` + ``g8_headroom.json`` into
    ``--out-dir`` via the canonical JSON writer. Dev/eval only -- the artifacts
    are not installed to the Swift Resources path (like g8_bucket_recall).
    """
    assert_hash_seed_pinned()
    written = eval_run.main(cells_path, raw_scores_path, out_dir)
    baseline = load_json(written["baseline"])
    totals = baseline["totals"]
    click.echo(
        f"Wrote {written['baseline']} "
        f"(grand-total precision={totals['precision']:.4f} "
        f"recall={totals['recall']:.4f} f1={totals['f1']:.4f}; "
        f"{len(baseline['per_family'])} families)"
    )
    click.echo(f"Wrote {written['headroom']}")


# Precision deltas at the CLI are expressed in POINTS (a 5 means 5 precision
# points); build_compare consumes FRACTIONS. One conversion, here, keeps the
# unit contract consistent across CLI + comparator + tests.
_POINTS_TO_FRACTION: float = 0.01


@build_group.command("eval-compare")
@click.option(
    "--before",
    "before_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the BEFORE derived g8_detection_baseline.json (NOT _cells.json).",
)
@click.option(
    "--after",
    "after_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the AFTER derived g8_detection_baseline.json (NOT _cells.json).",
)
@click.option(
    "--delta-p",
    type=float,
    default=5.0,
    show_default=True,
    help="C1 minimum precision uplift, in precision POINTS (5 = 5 points = 0.05).",
)
@click.option(
    "--delta-f-rel",
    type=float,
    default=0.30,
    show_default=True,
    help="C2 minimum relative family-FPR cut, as a fraction (0.30 = 30%).",
)
@click.option(
    "--eps",
    type=float,
    default=0.01,
    show_default=True,
    help="C3 recall floor slack, as a fraction (0.01 = 1 point).",
)
@click.option(
    "--delta-slice",
    type=float,
    default=3.0,
    show_default=True,
    help="C4 max per-doctype/per-demographic precision drop, in precision POINTS (3 = 3 points).",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Path for the g8_compare_verdict.json output.",
)
def build_eval_compare_cmd(
    before_path: Path,
    after_path: Path,
    delta_p: float,
    delta_f_rel: float,
    eps: float,
    delta_slice: float,
    out_path: Path,
) -> None:
    """Decide the §3 before/after predicate from two derived baselines.

    Reads the two derived ``g8_detection_baseline.json`` dicts (a BEFORE and an
    AFTER), applies the four-clause predicate per scorer family and over the
    grand-total aggregate (pure arithmetic; no sim, no re-derivation), and
    writes ``g8_compare_verdict.json`` to ``--out`` via the canonical JSON
    writer. Dev/eval only -- no install route, no lock entry, not produced by
    ``make build``. ``--delta-p`` / ``--delta-slice`` are precision POINTS;
    ``--eps`` / ``--delta-f-rel`` are fractions.
    """
    assert_hash_seed_pinned()
    before = load_json(before_path)
    after = load_json(after_path)
    thresholds = {
        "delta_p": delta_p * _POINTS_TO_FRACTION,
        "delta_f_rel": delta_f_rel,
        "eps": eps,
        "delta_slice": delta_slice * _POINTS_TO_FRACTION,
    }
    verdict = build_compare(before, after, thresholds)
    dump_canonical_json(verdict, out_path)
    overall = "REGRESSION" if verdict["regression"] else "no-regression"
    click.echo(
        f"Wrote {out_path} (verdict={overall}; "
        f"families={len(verdict['families'])}, "
        f"aggregate regression={verdict['aggregate']['regression']})"
    )


def _git_head_short(repo_root: Path) -> str:
    """Return the short SHA of HEAD, or the literal 'unknown' if unavailable.

    The build-input commit -- not a wall-clock -- so it is determinism-safe.
    Failure paths (no git binary, detached state with no
    commits, non-repo) collapse to ``"unknown"`` rather than raising so the
    probe still produces a schema-valid artifact off-tree.
    """
    git_bin = shutil.which("git")
    if git_bin is None:
        return "unknown"
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [git_bin, "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            cwd=repo_root,
            text=True,
            # Bound the probe so a stuck git invocation (e.g. unresponsive
            # network-mounted repo, credential prompt on misconfigured
            # remote) does not block the CLI indefinitely.
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    sha = result.stdout.strip()
    return sha if sha else "unknown"


@build_group.command("bundle-size")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--sub-dir",
    "sub_dirs",
    multiple=True,
    default=BUNDLE_SIZE_DEFAULT_SUB_DIRS,
    show_default=True,
    help=(
        "Subdirectory under build/ to include. Repeat to add more; "
        "defaults to the spec §1.35 scope."
    ),
)
def build_bundle_size_cmd(build_dir: Path, sub_dirs: tuple[str, ...]) -> None:
    """Build the bundle-size instrumentation probe (D-35).

    Walks the configured ``build/`` subdirectories and writes
    ``instrumentation/bundle_size.json``. Engineer-facing only per spec
    §1.35 + §4 #13; the Swift cold-start hook (F-12) ships from the Mac
    side.
    """
    assert_hash_seed_pinned()

    repo_root = Path(__file__).resolve().parent.parent.parent
    git_head = _git_head_short(repo_root)
    payload = build_bundle_size(build_dir, sub_dirs=tuple(sub_dirs))
    meta_payload = build_bundle_size_meta(git_head=git_head)

    instrumentation_dir = build_dir / "instrumentation"
    dest = instrumentation_dir / "bundle_size.json"
    meta_dest = instrumentation_dir / "bundle_size.meta.json"
    dump_canonical_json(payload, dest)
    dump_canonical_json(meta_payload, meta_dest)
    click.echo(
        f"Wrote {dest} (total_build_size={payload['total_build_size']} bytes "
        f"across {len(payload['subdirectories'])} subdirectories; "
        f"git_head={git_head} → {meta_dest.name})"
    )


# -----------------------------------------------------------------------------
# Build subcommands (Phase 3)
# -----------------------------------------------------------------------------


@build_group.command("classifier")
@click.argument("kind", type=click.Choice(["keywords", "presets", "scorer", "all"]))
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_classifier_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build doctype keywords and preset threshold candidates."""
    assert_hash_seed_pinned()
    selected = ["keywords", "presets", "scorer"] if kind == "all" else [kind]
    for name in selected:
        if name == "keywords":
            payload = build_doctype_keywords(seed)
            dest = build_dir / "classifier" / "doctype_keywords.json"
            dump_canonical_json(payload, dest)
            click.echo(f"Wrote {dest} ({len(payload['classes'])} classes)")
        elif name == "presets":
            payload = build_preset_thresholds(seed)
            dest = build_dir / "classifier" / "preset_thresholds_candidates.json"
            dump_canonical_json(payload, dest)
            click.echo(
                f"Wrote {dest} (status={payload['status']}, {len(payload['presets'])} presets)"
            )
        elif name == "scorer":
            # B05 — in-band context scorer (C1), PROMOTED. The final-named
            # artifact now carries the trained calibrated weights: the families
            # that cleared the orchestrator-path §3 before/after predicate ship
            # w_family 1 (account, phone), the rest ship identity. The promotion
            # is Jesse-gated (D-10); the calibrated final's weights are
            # byte-identical to the `_candidates` fit — only the status (+ notes)
            # differ. Both shapes read the SAME committed File-5 fire dump.
            corpus_path = build_dir / "corpus" / "g8_corpus.json"
            # The File-5 fire dump is a committed input emitted out-of-band by
            # the Swift harness (no producing recipe), not a built artifact:
            # prefer the active build-dir copy, else the committed repo-root
            # location. In the side-by-side determinism rebuild the out-dir has
            # no dump, so the fallback reads the SAME committed bytes the real
            # build read — keeping the calibrated final + candidates byte-reproducible.
            fire_features_path = build_dir / "corpus" / "g8_fire_features.json"
            if not fire_features_path.is_file():
                fire_features_path = _COMMITTED_FIRE_FEATURES_DUMP
            final_payload = build_context_scorer(
                seed,
                status="calibrated",
                corpus_path=corpus_path,
                fire_features_path=fire_features_path,
                schemas_dir=_DEFAULT_SCHEMAS_DIR,
            )
            dest = build_dir / "classifier" / "context_scorer.json"
            dump_canonical_json(final_payload, dest)
            candidates_payload = build_context_scorer(
                seed,
                status="candidates",
                corpus_path=corpus_path,
                fire_features_path=fire_features_path,
                schemas_dir=_DEFAULT_SCHEMAS_DIR,
            )
            cand_dest = build_dir / "classifier" / "context_scorer_candidates.json"
            dump_canonical_json(candidates_payload, cand_dest)
            active = sorted(
                fam for fam, block in final_payload["families"].items() if block["w_family"] != 0.0
            )
            click.echo(
                f"Wrote {dest} (status={final_payload['status']}, active={active}) + "
                f"{cand_dest.name} (status={candidates_payload['status']}, "
                f"{len(candidates_payload['families'])} families)"
            )


@build_group.command("corpus")
@click.argument("kind", type=click.Choice(["g8"]))
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_corpus_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build the G8 synthetic document corpus."""
    assert_hash_seed_pinned()
    if kind == "g8":
        payload = build_g8_corpus(seed)
        dest = build_dir / "corpus" / "g8_corpus.json"
        dump_canonical_json(payload, dest)
        click.echo(
            f"Wrote {dest} ({len(payload['documents'])} documents across "
            f"{len(payload['counts_by_doctype'])} doctypes)"
        )


# -----------------------------------------------------------------------------
# Build subcommands (Phase 3b — calibration)
# -----------------------------------------------------------------------------
#
# These subcommands consume Swift-side dumps (softmax logits from
# DocumentTypeClassifier, raw per-candidate scores from the PII detectors)
# that Jesse produces out-of-band via a Swift test target. They are NOT
# part of the default `make build`: the Makefile
# `calibrate` targets gate on dump presence and fail cleanly when dumps are
# absent, so normal builds stay fully offline and synthetic.


_DEFAULT_SCHEMAS_DIR = Path("schemas")


@build_group.group("calibrate")
def build_calibrate_group() -> None:
    """Phase 3b calibration artifacts produced from Swift-side dumps."""


@build_calibrate_group.command("temperature")
@click.option(
    "--softmax-dump",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the Swift DocumentTypeClassifier softmax (logits) dump.",
)
@click.option(
    "--corpus",
    "corpus_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the G8 corpus the dump was produced against.",
)
@click.option(
    "--schemas-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=_DEFAULT_SCHEMAS_DIR,
    show_default=True,
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_calibrate_temperature_cmd(
    softmax_dump: Path,
    corpus_path: Path,
    schemas_dir: Path,
    build_dir: Path,
    seed: int,
) -> None:
    """Fit the scalar doctype-softmax temperature T against a Swift dump."""
    assert_hash_seed_pinned()
    payload = build_fit_temperature(
        seed,
        softmax_dump_path=softmax_dump,
        corpus_path=corpus_path,
        schemas_dir=schemas_dir,
    )
    dest = build_dir / "classifier" / "doctype_temperature.json"
    dump_canonical_json(payload, dest)
    click.echo(
        f"Wrote {dest} (T={payload['temperature']:.6f}, "
        f"NLL {payload['fit_metadata']['nll_before']:.4f} -> "
        f"{payload['fit_metadata']['nll_after']:.4f}, "
        f"{payload['fit_metadata']['iterations']} iters)"
    )


@build_calibrate_group.command("sweep")
@click.option(
    "--score-dump",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the Swift per-candidate detector score dump.",
)
@click.option(
    "--temperature",
    "temperature_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the fitted doctype_temperature.json.",
)
@click.option(
    "--corpus",
    "corpus_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the G8 corpus.",
)
@click.option(
    "--schemas-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=_DEFAULT_SCHEMAS_DIR,
    show_default=True,
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
@click.option(
    "--prior-mode",
    type=click.Choice(["corpus", "fresh", "mixed"]),
    default="fresh",
    show_default=True,
    help=(
        "Prior composition mode for threshold sweep. "
        "'fresh' uses a neutral 0.5 prior (matches first-scan behavior); "
        "'corpus' uses train-split category density (may inflate posteriors "
        "when templates are not name-sparse); 'mixed' averages the two."
    ),
)
def build_calibrate_sweep_cmd(
    score_dump: Path,
    temperature_path: Path,
    corpus_path: Path,
    schemas_dir: Path,
    build_dir: Path,
    seed: int,
    prior_mode: str,
) -> None:
    """Sweep per-category thresholds against a Swift score dump.

    Writes ``preset_thresholds_sweep_raw.json`` (status=sweep_raw) for
    inspection — never the shipping ``preset_thresholds.json``, which is
    written only by ``build calibrate finalize`` (0.4 two-stage flow).
    """
    assert_hash_seed_pinned()
    payload = build_sweep_thresholds(
        seed,
        score_dump_path=score_dump,
        temperature_path=temperature_path,
        corpus_path=corpus_path,
        schemas_dir=schemas_dir,
        prior_mode=prior_mode,
    )
    dest = build_dir / "classifier" / "preset_thresholds_sweep_raw.json"
    dump_canonical_json(payload, dest)
    click.echo(
        f"Wrote {dest} (status={payload['status']}, "
        f"{len(payload['presets'])} presets, {len(payload['categories'])} categories)"
    )


@build_calibrate_group.command("finalize")
@click.option(
    "--sweep-raw",
    "sweep_raw_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the sweep_raw artifact written by `build calibrate sweep`.",
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--schemas-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=_DEFAULT_SCHEMAS_DIR,
    show_default=True,
)
def build_calibrate_finalize_cmd(
    sweep_raw_path: Path,
    build_dir: Path,
    schemas_dir: Path,
) -> None:
    """Promote a reviewed sweep_raw artifact to the shipping preset file.

    The only writer of ``build/classifier/preset_thresholds.json`` in the
    calibrate flow (0.4). Jesse-gated via the interactive Makefile target
    ``calibrate-finalize``.
    """
    assert_hash_seed_pinned()
    dest = build_dir / "classifier" / "preset_thresholds.json"
    payload = finalize_sweep_thresholds(sweep_raw_path, schemas_dir, shipping_path=dest)
    dump_canonical_json(payload, dest)
    click.echo(f"Wrote {dest} (status={payload['status']})")


# -----------------------------------------------------------------------------
# Top-level error handler
# -----------------------------------------------------------------------------


def _install_exception_handler() -> None:
    """Convert PipelineError subclasses into clean CLI exits."""
    original_excepthook = sys.excepthook

    def handler(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        if isinstance(exc, HashMismatchError | DeterminismError | SchemaValidationError):
            click.echo(f"{exc_type.__name__}: {exc}", err=True)
            sys.exit(1)
        if isinstance(exc, PipelineError):
            click.echo(f"{exc_type.__name__}: {exc}", err=True)
            sys.exit(2)
        original_excepthook(exc_type, exc, tb)  # type: ignore[arg-type]

    sys.excepthook = handler


_install_exception_handler()


if __name__ == "__main__":
    main()
