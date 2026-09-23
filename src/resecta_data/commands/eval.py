"""The eval stages: build eval-baseline, eval-documents, eval-compare, eval-compare-documents,
eval-sitegap."""

from __future__ import annotations

from pathlib import Path

import click

from resecta_data.common.determinism import assert_hash_seed_pinned
from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import validate_file
from resecta_data.eval import build_compare
from resecta_data.eval import documents as eval_documents
from resecta_data.eval import run as eval_run
from resecta_data.eval.compare_documents import build_compare_documents
from resecta_data.eval.sitegap import build_site_gap


@click.command("eval-baseline")
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
        name = outcomes["per_family"].get("name")
        if name is not None:
            click.echo(
                f"name recall: any-overlap={name['recall']:.4f} "
                f"all-tokens={name['recall_all_tokens']:.4f} "
                f"(tp={name['tp']}, partially covered={name['one_token_tp']})"
            )


@click.command("eval-documents")
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


@click.command("eval-compare")
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


@click.command("eval-compare-documents")
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


@click.command("eval-sitegap")
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


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_eval_baseline_cmd)
    build.add_command(build_eval_documents_cmd)
    build.add_command(build_eval_compare_cmd)
    build.add_command(build_eval_compare_documents_cmd)
    build.add_command(build_eval_sitegap_cmd)
