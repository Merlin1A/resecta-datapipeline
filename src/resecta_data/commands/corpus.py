"""Corpus-side builders: build corpus, build negative-corpus, build demographics,
build g8-bucket-recall, build rules."""

from __future__ import annotations

from pathlib import Path

import click

from resecta_data.bloom.ingest_cache import _INGEST_CACHE_SUBDIR
from resecta_data.commands.bloom import _DEFAULT_SOURCES_DIR, _ingest_given_names, _ingest_surnames
from resecta_data.common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
from resecta_data.common.io import dump_canonical_json, load_json, sha256_file
from resecta_data.corpus import build_g8_corpus, build_negative_corpus
from resecta_data.corpus._profiles import PROFILE_G8
from resecta_data.corpus._profiles import PROFILES as CORPUS_PROFILES
from resecta_data.demographics import build as build_demographics
from resecta_data.demographics.g8_bucket_recall import build as build_g8_bucket_recall
from resecta_data.rules import build as build_rule_catalog


@click.command("rules")
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


@click.command("demographics")
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


@click.command("g8-bucket-recall")
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


@click.command("negative-corpus")
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


def g8_corpus_artifact_path(build_dir: Path, profile: str) -> Path:
    """The build/-relative home of a G8 corpus profile.

    The ``g8`` profile IS ``corpus/g8_corpus.json`` (the fixture the lock row
    and the engine test bundle carry); every other profile lands beside it as
    ``corpus/g8_corpus_<profile>.json``.
    """
    name = "g8_corpus.json" if profile == PROFILE_G8 else f"g8_corpus_{profile}.json"
    return build_dir / "corpus" / name


@click.command("corpus")
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


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_rules_cmd)
    build.add_command(build_demographics_cmd)
    build.add_command(build_g8_bucket_recall_cmd)
    build.add_command(build_negative_corpus_cmd)
    build.add_command(build_corpus_cmd)
