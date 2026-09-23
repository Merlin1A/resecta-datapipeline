"""CLI entry points for the data pipeline.

At Phase 0 the CLI provides verification commands only: schema validation,
determinism check, hash-lockfile check, and asset installation. Build
subcommands land in Phase 1+.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import Any

import click

from .adversarial import build as build_adversarial_patterns
from .bloom import (
    NAME_FILTERS_CUTOVER,
    BloomFilter,
    FilterBuildResult,
    build_manifest,
    build_shipped_manifest,
    collect_asset_entries,
    optimal_bits,
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
    SHIPPED_MANIFEST_FILE,
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
from .commands import verify
from .commands.verify import _OUT_OF_BAND_PREFIXES, _is_out_of_band, _run_rebuild_streaming
from .common.cutover import build_cutover_diff
from .common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
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
)
from .common.schema import validate_file
from .corpus import build_g8_corpus, build_negative_corpus
from .corpus._profiles import PROFILE_G8
from .corpus._profiles import PROFILES as CORPUS_PROFILES
from .demographics import build as build_demographics
from .demographics.g8_bucket_recall import build as build_g8_bucket_recall
from .eval import build_compare
from .eval import documents as eval_documents
from .eval import run as eval_run
from .eval.compare_documents import build_compare_documents
from .eval.sitegap import build_site_gap
from .fuzz import DEFAULT_MUTATION_COUNT, MUTATIONS_DIRNAME, build_pdf_mutations
from .fuzz import build as build_fuzz_redos
from .gazetteers.address_components import ADDRESS_COMPONENTS_CUTOVER
from .gazetteers.address_components import build as build_address_components
from .gazetteers.context_keywords import build as build_context_keywords
from .gazetteers.dl_patterns import build as build_dl_patterns
from .gazetteers.institutions import (
    INSTITUTIONS_CUTOVER,
    legacy_institution_rows,
    rebuild_institution_rows,
)
from .gazetteers.institutions import build as build_institutions
from .gazetteers.name_common_words import build as build_name_common_words
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
from .routes import INSTALL_ROUTES, SCHEMA_ROUTES, SHRINK_GUARDED_ROUTES
from .rules import build as build_rule_catalog
from .vectors import VECTOR_FAMILIES, VectorBuilder

# The names importers read on this module besides ``main``: the routing tables (their
# historical home) and the private helpers the tests pin, each defined in the module that
# uses it.
__all__ = [
    "INSTALL_ROUTES",
    "SCHEMA_ROUTES",
    "SHRINK_GUARDED_ROUTES",
    "_OUT_OF_BAND_PREFIXES",
    "_is_out_of_band",
    "_run_rebuild_streaming",
    "main",
]

logger = logging.getLogger(__name__)


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
    """Stage the committed reviewed negative_context.json into build/.

    Verifies the meta sidecar's ``reviewed_version`` against the live
    candidates hash before copying — a mismatch means the candidates
    changed without the sidecar being re-stamped under an approved change
    plan, and staging refuses.
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
        "overwritten by a smaller build/ artifact. Required only after the "
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
                    f"superset; complete the institutions fetches + "
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
# Shipped manifest: the bloom manifest + every installed asset's digest
# -----------------------------------------------------------------------------


@main.command("manifest-assets")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--resources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "The iOS Resources/ tree install-assets targets. A routed asset this "
        "host did not build is digested from its installed copy here (install "
        "leaves it alone, so that is what ships). Absent or missing: only built "
        "artifacts are listed."
    ),
)
@click.option(
    "--lockfile",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("asset_hashes.lock"),
    show_default=True,
    help=(
        "Cross-check source: each entry is reported as matching, differing from, "
        "or absent from the lock."
    ),
)
def manifest_assets_cmd(build_dir: Path, resources_dir: Path | None, lockfile: Path) -> None:
    """Derive the shipped manifest from the bloom builder's manifest.

    Reads ``build/gazetteers/gazetteer_manifest.json`` (a locked ``make build``
    product) and writes ``gazetteer_manifest.shipped.json`` beside it: the
    same ``filters[]``, ``version`` raised to the shipped version, and an
    ``assets[]`` section with ``{path, sha256, bytes}`` for every asset
    ``install-assets`` routes into the engine bundle except the manifest
    triple. The digests are of the bytes install ships — the built artifact
    when present, else the installed file — so the engine's first-load
    verification never refuses the bundle the pipeline itself installed.
    ``sign-manifest`` signs this file; it is out-of-band relative to the lock.
    """
    base_path = build_dir / "gazetteers" / MANIFEST_FILE
    if not base_path.is_file():
        raise click.ClickException(f"Manifest not found at {base_path}. Run `make bloom` first.")
    base = load_json(base_path)
    if not isinstance(base, dict):
        raise click.ClickException(f"{base_path}: manifest root must be an object")
    lock = read_hash_lockfile(lockfile) if lockfile.is_file() else {}
    resources = resources_dir if resources_dir is not None and resources_dir.is_dir() else None
    entries, skipped = collect_asset_entries(
        build_dir=build_dir, resources_dir=resources, routes=INSTALL_ROUTES, lock=lock
    )
    shipped = build_shipped_manifest(base, entries)
    dest = build_dir / "gazetteers" / SHIPPED_MANIFEST_FILE
    dump_canonical_json(shipped, dest)

    for entry in entries:
        lock_state = entry.lock if entry.lock is not None else "no lock row"
        click.echo(
            f"  {entry.path}  {entry.sha256[:12]}…  {entry.size} B  "
            f"[{entry.source}; lock: {lock_state}]"
        )
    click.echo(
        f"Shipped manifest: {dest.relative_to(build_dir)} "
        f"({len(entries)} assets; version {shipped['version']})"
    )
    for rel in skipped:
        click.echo(f"  - not listed (neither built nor installed): {rel}")


# -----------------------------------------------------------------------------
# Sign manifest (verified by the iOS engine)
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
    """Sign the shipped manifest (``gazetteer_manifest.shipped.json``) with Ed25519.

    Writes ``gazetteer_manifest.sig`` and ``manifest_public_key.pem`` next
    to the manifest. Both files are picked up by ``install-assets`` via the
    routing entries above and copied into the iOS Resources/Gazetteers/
    tree. The iOS engine verifies the signature at detector init
    (see GazetteerLoader.swift).

    Cross-boundary wire-format changes need a paired Swift PR
    (Ed25519; the signing key rotates per major release).
    """
    key_path = private_key if private_key is not None else DEFAULT_PRIVATE_KEY_PATH

    if generate_key:
        if key_path.exists():
            click.echo(f"Key already exists at {key_path}; refusing to overwrite.")
        else:
            generate_private_key(key_path)
            click.echo(f"Generated new Ed25519 private key at {key_path}")

    pk = load_private_key(key_path)

    manifest_path = build_dir / "gazetteers" / SHIPPED_MANIFEST_FILE
    if not manifest_path.is_file():
        raise click.ClickException(
            f"Shipped manifest not found at {manifest_path}. Run `make manifest-assets` first."
        )

    # The signature file keeps its name (`gazetteer_manifest.sig`): the iOS
    # verifier reads the manifest as `gazetteer-manifest.json` and the
    # signature as `gazetteer_manifest.sig` regardless of the build-side stem.
    signature_path = sign_manifest_file(
        manifest_path, pk, signature_path=build_dir / "gazetteers" / "gazetteer_manifest.sig"
    )
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


# Views over the one vector-family config (``vectors/__init__.py``): the CLI
# ``kind`` names, their builders and their output filenames come from one tuple.
_VECTOR_BUILDERS: dict[str, VectorBuilder] = {f.kind: f.builder for f in VECTOR_FAMILIES}
_VECTOR_OUTPUT_FILENAMES: dict[str, str] = {f.kind: f.output_filename for f in VECTOR_FAMILIES}
_VECTOR_KINDS: tuple[str, ...] = tuple(f.kind for f in VECTOR_FAMILIES)


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
@click.argument("kind", type=click.Choice(["redos", "pdf-mutations"]))
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
    "--packet",
    "packet_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Source PDF the pdf-mutations kind damages (a sample-doc checkout's "
        "packet.pdf). Required for that kind; ignored otherwise. Nothing binary "
        "is committed here -- the base is read at build time and its sha256 is "
        "recorded in the manifest."
    ),
)
@click.option(
    "--count",
    type=int,
    default=DEFAULT_MUTATION_COUNT,
    show_default=True,
    help="How many pdf-mutations fixtures to emit, split evenly across the four families.",
)
def build_fuzz_cmd(
    kind: str,
    build_dir: Path,
    seed: int,
    packet_path: Path | None,
    count: int,
) -> None:
    """Build fuzz payload catalogs."""
    assert_hash_seed_pinned()
    if kind == "redos":
        payload = build_fuzz_redos(seed)
        dest = build_dir / "fuzz" / "redos_payloads.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['payloads'])} payloads)")
        return

    if packet_path is None:
        raise click.UsageError("--packet is required for `build fuzz pdf-mutations`.")
    mutation_set = build_pdf_mutations(seed, source=packet_path.read_bytes(), count=count)
    fuzz_dir = build_dir / "fuzz"
    for rel, data in mutation_set.files:
        atomic_write_bytes(fuzz_dir / rel, data)
    dest = fuzz_dir / "pdf_mutations.json"
    dump_canonical_json(mutation_set.manifest, dest)
    click.echo(
        f"Wrote {dest} ({len(mutation_set.files)} fixtures under "
        f"{fuzz_dir / MUTATIONS_DIRNAME}/; base sha256 "
        f"{mutation_set.manifest['base_sha256'][:12]}...)"
    )


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
    # stays bounded — see ingest_sources_parallel.
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

    cutover_diff = build_cutover_diff((), (), spec=NAME_FILTERS_CUTOVER)
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
            "name-common-words",
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
        cutover_diff = build_cutover_diff(
            legacy_institution_rows(), rebuild_institution_rows(), spec=INSTITUTIONS_CUTOVER
        )
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
        cutover_diff = build_cutover_diff((), (), spec=ADDRESS_COMPONENTS_CUTOVER)
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
    elif kind == "name-common-words":
        payload = build_name_common_words(seed)
        dest = build_dir / "gazetteers" / "name_common_words.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['entries'])} common-word entries)")
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
    """Build the per-category positive context-keyword gazetteer."""
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
    """Build the PII detector rule-ID catalog."""
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
    """Build the G8 bucket-stratified recall artifact.

    Reads the G8 corpus and the surnames Bloom filter from ``build_dir`` and
    emits ``demographics/g8_bucket_recall_v1.json``. A one-off measurement
    (point estimate + sample size only).
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
    """Build the deterministic no-PII negative corpus (the baseline FP surface).

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
    help="Path to the Swift harness _cells.json (file 1 in src/resecta_data/eval/README.md).",
)
@click.option(
    "--raw-scores",
    "raw_scores_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the Swift harness _raw_scores.json (file 2 in src/resecta_data/eval/README.md).",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory for g8_detection_baseline.json + g8_headroom.json.",
)
@click.option(
    "--spans",
    "spans_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional per-span JSONL sidecar the same emitter wrote beside the trio "
    "(g8_detector_spans.jsonl / g8_siteb_spans.jsonl); derives g8_span_outcomes.json.",
)
@click.option(
    "--corpus",
    "corpus_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="The G8 corpus the sidecar rows are joined to (required with --spans).",
)
def build_eval_baseline_cmd(
    cells_path: Path,
    raw_scores_path: Path,
    out_dir: Path,
    spans_path: Path | None,
    corpus_path: Path | None,
) -> None:
    """Derive the detection baseline + learned-term headroom from the Swift JSONs.

    Loads the harness's offset-overlap join cells and raw match scores and
    writes ``g8_detection_baseline.json`` + ``g8_headroom.json`` into
    ``--out-dir`` via the canonical JSON writer. With ``--spans`` (and
    ``--corpus``) the emitter's per-span sidecar is validated, reconciled
    against the cells, and aggregated into ``g8_span_outcomes.json`` beside
    them. Dev/eval only -- the artifacts are not installed to the Swift
    Resources path (like g8_bucket_recall).
    """
    assert_hash_seed_pinned()
    if (spans_path is None) != (corpus_path is None):
        raise click.UsageError("--spans and --corpus go together")
    written = eval_run.main(
        cells_path, raw_scores_path, out_dir, spans_path=spans_path, corpus_path=corpus_path
    )
    baseline = load_json(written["baseline"])
    totals = baseline["totals"]
    click.echo(
        f"Wrote {written['baseline']} "
        f"(grand-total precision={totals['precision']:.4f} "
        f"recall={totals['recall']:.4f} f1={totals['f1']:.4f}; "
        f"{len(baseline['per_family'])} families)"
    )
    click.echo(f"Wrote {written['headroom']}")
    if "spans" in written:
        outcomes = load_json(written["spans"])
        counts = outcomes["row_counts"]
        click.echo(
            f"Wrote {written['spans']} "
            f"({counts['total']} rows; ground truth {counts['ground_truth']}; "
            f"tp={counts['tp']} fn={counts['fn']} fp={counts['fp']} "
            f"one_token_tp={counts['one_token_tp']}; "
            f"cells crosscheck {outcomes['cells_crosscheck']['status']})"
        )


@build_group.command("eval-documents")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to documents.manifest.json (the T1.4 document manifest).",
)
@click.option(
    "--gt-root",
    "gt_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Root that resolves the manifest's gt paths (a sample-doc checkout with variants/ built).",
)
@click.option(
    "--hits-dir",
    "hits_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="DocumentHarnessTests output directory (RESECTA_DOCS_OUT).",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory for documents_eval.json.",
)
@click.option(
    "--join-rule",
    "join_rule",
    type=click.Choice(eval_documents.JOIN_RULES),
    default=eval_documents.DEFAULT_JOIN_RULE,
    show_default=True,
    help="Coverage credit: the union of same-category detections over a box, or the "
    "best single detection (the two are reported as a pair on the same hits).",
)
@click.option(
    "--schemas-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("schemas"),
    show_default=True,
    help="Directory holding documents_eval.schema.json; the artifact is validated against it.",
)
def build_eval_documents_cmd(
    manifest_path: Path,
    gt_root: Path,
    hits_dir: Path,
    out_dir: Path,
    join_rule: str,
    schemas_dir: Path,
) -> None:
    """Derive the document-level Site-B eval from the H1.2 harness JSONs.

    Joins each document's hits (per leg, per run) against its draw-time ground
    truth under the Option-C match rule with the chosen coverage credit, and
    writes ``documents_eval.json`` (per-document metrics + pooled micro/macro
    + Wilson/BCa intervals + miss attribution) into ``--out-dir`` via the
    canonical JSON writer, validated against its schema. Dev/eval only -- the
    artifact is not installed to the Swift Resources path.
    """
    assert_hash_seed_pinned()
    written = eval_documents.main(manifest_path, gt_root, hits_dir, out_dir, join_rule)
    validate_file(written["eval"], schemas_dir, "documents_eval")
    payload = load_json(written["eval"])
    click.echo(
        f"Wrote {written['eval']} "
        f"({len(payload['per_document'])} documents; site={payload['site']}; "
        f"join rule {payload['match_rule']['join_rule']}; schema-valid)"
    )
    for pool_name, pool in payload["pools"].items():
        click.echo(
            f"  pool {pool_name}: micro-F2 {pool['micro_f2']:.4f} "
            f"over {len(pool['documents'])} documents"
        )


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
    help="Clause 1: minimum precision uplift, in precision POINTS (5 = 5 points = 0.05).",
)
@click.option(
    "--delta-f-rel",
    type=float,
    default=0.30,
    show_default=True,
    help="Clause 2: minimum relative family-FPR cut, as a fraction (0.30 = 30%).",
)
@click.option(
    "--eps",
    type=float,
    default=0.01,
    show_default=True,
    help="Clause 3: recall floor slack, as a fraction (0.01 = 1 point).",
)
@click.option(
    "--delta-slice",
    type=float,
    default=3.0,
    show_default=True,
    help="Clause 4: max per-doctype/per-demographic precision drop, in POINTS (3 = 3 points).",
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
    """Decide the four-clause before/after predicate from two derived baselines.

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


@build_group.command("eval-compare-documents")
@click.option(
    "--before",
    "before_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the BEFORE documents_eval.json (the H1.3 document-level eval).",
)
@click.option(
    "--after",
    "after_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the AFTER documents_eval.json.",
)
@click.option(
    "--delta-p", type=float, default=5.0, show_default=True, help="C1 uplift, precision POINTS."
)
@click.option(
    "--delta-f-rel",
    type=float,
    default=0.30,
    show_default=True,
    help="C2 relative FPR cut, fraction.",
)
@click.option(
    "--eps", type=float, default=0.01, show_default=True, help="C3 recall floor slack, fraction."
)
@click.option(
    "--delta-slice",
    type=float,
    default=3.0,
    show_default=True,
    help="C4 max per-category (row) / per-leg (aggregate) strict-precision drop, precision POINTS.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Path for the g8_compare_documents_verdict.json output.",
)
def build_eval_compare_documents_cmd(
    before_path: Path,
    after_path: Path,
    delta_p: float,
    delta_f_rel: float,
    eps: float,
    delta_slice: float,
    out_path: Path,
) -> None:
    """Decide the four-clause before/after predicate over document rows.

    Reads two ``documents_eval.json`` dicts and applies the G8 comparator's
    C1 / C2 / C3 to every ``(document, leg)`` row present on both sides (C4
    over the row's per-category strict precision) and to the pooled aggregate
    (C4 over the leg kinds). Writes the verdict to ``--out`` via the canonical
    JSON writer. Same units as ``eval-compare``: ``--delta-p`` /
    ``--delta-slice`` in precision POINTS, ``--eps`` / ``--delta-f-rel`` as
    fractions. Pure arithmetic; dev/eval only.
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
    verdict = build_compare_documents(before, after, thresholds)
    dump_canonical_json(verdict, out_path)
    overall = "REGRESSION" if verdict["regression"] else "no-regression"
    click.echo(
        f"Wrote {out_path} (verdict={overall}; rows={len(verdict['rows'])}, "
        f"skipped={len(verdict['rows_skipped'])}, "
        f"aggregate regression={verdict['aggregate']['regression']})"
    )


@build_group.command("eval-sitegap")
@click.option(
    "--detector",
    "detector_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Derived g8_detection_baseline.json of the detector-site trio (NOT _cells.json).",
)
@click.option(
    "--siteb",
    "siteb_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Derived g8_detection_baseline.json of the Site-B trio (NOT _cells.json).",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Path for the g8_site_gap.json output.",
)
def build_eval_sitegap_cmd(detector_path: Path, siteb_path: Path, out_path: Path) -> None:
    """Join the detector-site and Site-B derived baselines into the site gap.

    Reads the two derived ``g8_detection_baseline.json`` dicts and writes
    ``g8_site_gap.json`` (per family + grand total: both sides' headline
    numbers, intervals and packet-tier block, and Site B minus detector on
    every differenced field) to ``--out`` via the canonical JSON writer. Pure
    arithmetic over the frozen baselines; dev/eval only.
    """
    assert_hash_seed_pinned()
    detector = load_json(detector_path)
    siteb = load_json(siteb_path)
    gap = build_site_gap(detector, siteb)
    dump_canonical_json(gap, out_path)
    total = gap["totals"]["delta_siteb_minus_detector"]
    click.echo(
        f"Wrote {out_path} (grand-total Site B minus detector: "
        f"precision {total['precision']:+.4f} recall {total['recall']:+.4f} "
        f"f2 {total['f2']:+.4f}; families with a gap: "
        f"{len(gap['families_with_gap'])} of {len(gap['families'])})"
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
        "defaults to the shipping-asset scope."
    ),
)
def build_bundle_size_cmd(build_dir: Path, sub_dirs: tuple[str, ...]) -> None:
    """Build the bundle-size instrumentation probe.

    Walks the configured ``build/`` subdirectories and writes
    ``instrumentation/bundle_size.json``. Engineer-facing only; the Swift
    cold-start hook ships from the Mac side.
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
            # In-band context scorer, PROMOTED. The final-named
            # artifact now carries the trained calibrated weights: the families
            # that cleared the orchestrator-path four-clause before/after
            # predicate ship w_family 1 (account, phone), the rest ship identity.
            # The promotion happens under an approved change plan; the
            # calibrated final's weights are
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


def g8_corpus_artifact_path(build_dir: Path, profile: str) -> Path:
    """The build/-relative home of a G8 corpus profile.

    The ``g8`` profile IS ``corpus/g8_corpus.json`` (the fixture the lock row
    and the engine test bundle carry); every other profile lands beside it as
    ``corpus/g8_corpus_<profile>.json``.
    """
    name = "g8_corpus.json" if profile == PROFILE_G8 else f"g8_corpus_{profile}.json"
    return build_dir / "corpus" / name


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
@click.option(
    "--profile",
    "profiles",
    type=click.Choice(list(CORPUS_PROFILES)),
    multiple=True,
    default=(PROFILE_G8,),
    show_default=True,
    help=(
        "Generator profile(s) to build (repeatable). g8 = the corpus as furnished "
        "(corpus/g8_corpus.json); g8-specC / g8-specD / g8-specCD = the same "
        "documents with name-context variety, furniture density, or both; "
        "g8-specA / g8-specG / g8-specH / g8-specAGH = name forms, the locale "
        "axis, the sparse placeholder, or all three "
        "(corpus/g8_corpus_<profile>.json; never installed)."
    ),
)
def build_corpus_cmd(kind: str, build_dir: Path, seed: int, profiles: tuple[str, ...]) -> None:
    """Build the G8 synthetic document corpus (one file per profile)."""
    assert_hash_seed_pinned()
    if kind == "g8":
        for profile in profiles:
            payload = build_g8_corpus(seed, profile=profile)
            dest = g8_corpus_artifact_path(build_dir, profile)
            dump_canonical_json(payload, dest)
            click.echo(
                f"Wrote {dest} ({len(payload['documents'])} documents across "
                f"{len(payload['counts_by_doctype'])} doctypes; profile {profile})"
            )


# -----------------------------------------------------------------------------
# Build subcommands (Phase 3b — calibration)
# -----------------------------------------------------------------------------
#
# These subcommands consume Swift-side dumps (softmax logits from
# DocumentTypeClassifier, raw per-candidate scores from the PII detectors)
# produced out-of-band via a Swift test target. They are NOT
# part of the default `make build`: the Makefile
# `calibrate` targets gate on dump presence and fail cleanly when dumps are
# absent, so normal builds stay fully offline and synthetic.


_DEFAULT_SCHEMAS_DIR = Path("schemas")


@build_group.group("calibrate")
def build_calibrate_group() -> None:
    """Phase 3b calibration artifacts produced from Swift-side dumps."""


verify.register(main, build_group, build_calibrate_group)


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
    calibrate flow. Promoted under an approved change plan via the interactive
    Makefile target ``calibrate-finalize``.
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
