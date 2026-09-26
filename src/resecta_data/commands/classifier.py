"""Classifier builders: build classifier and the build calibrate group's temperature, sweep,
finalize."""

from __future__ import annotations

from pathlib import Path

import click

from resecta_data.classifier import (
    build_context_scorer,
    build_doctype_keywords,
    build_fit_temperature,
    build_preset_thresholds,
    build_sweep_thresholds,
    finalize_sweep_thresholds,
)
from resecta_data.common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
from resecta_data.common.io import dump_canonical_json

# The committed File-5 fire-features dump (Swift-harness output, force-tracked
# under build/; no producing recipe). Repo-root-relative so the in-band scorer
# fit reads identical bytes in the side-by-side determinism rebuild, where the
# rebuild out-dir holds no dump (the candidates artifact must stay byte-stable).
_COMMITTED_FIRE_FEATURES_DUMP = Path("build/corpus/g8_fire_features.json")


# -----------------------------------------------------------------------------
# Build subcommands (Phase 3)
# -----------------------------------------------------------------------------


@click.command("classifier")
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


@click.command("temperature")
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


@click.command("sweep")
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


@click.command("finalize")
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


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_classifier_cmd)
    calibrate.add_command(build_calibrate_temperature_cmd)
    calibrate.add_command(build_calibrate_sweep_cmd)
    calibrate.add_command(build_calibrate_finalize_cmd)
