"""Gazetteer builders: build gazetteers, build context, build zip-scf."""

from __future__ import annotations

from pathlib import Path

import click

from resecta_data.commands.bloom import _DEFAULT_SOURCES_DIR
from resecta_data.common.cutover import build_cutover_diff
from resecta_data.common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
from resecta_data.common.io import dump_canonical_json
from resecta_data.gazetteers.address_components import ADDRESS_COMPONENTS_CUTOVER
from resecta_data.gazetteers.address_components import build as build_address_components
from resecta_data.gazetteers.context_keywords import build as build_context_keywords
from resecta_data.gazetteers.dl_patterns import build as build_dl_patterns
from resecta_data.gazetteers.institutions import (
    INSTITUTIONS_CUTOVER,
    legacy_institution_rows,
    rebuild_institution_rows,
)
from resecta_data.gazetteers.institutions import build as build_institutions
from resecta_data.gazetteers.name_common_words import build as build_name_common_words
from resecta_data.gazetteers.negative_context import build as build_negative_context
from resecta_data.gazetteers.nicknames import build as build_nicknames
from resecta_data.gazetteers.passport_patterns import build as build_passport_patterns
from resecta_data.gazetteers.zip_scf import build as build_zip_scf


@click.command("zip-scf")
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


@click.command("gazetteers")
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


@click.command("context")
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


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_zip_scf_cmd)
    build.add_command(build_gazetteers_cmd)
    build.add_command(build_context_cmd)
