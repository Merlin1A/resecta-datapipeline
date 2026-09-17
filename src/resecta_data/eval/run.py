"""Driver: load the Swift G8 JSONs, derive the baseline + headroom.

Reads ``_cells.json`` and ``_raw_scores.json``,
runs :func:`resecta_data.eval.baseline.build_baseline` and
:func:`resecta_data.eval.headroom.build_headroom`, and writes
``g8_detection_baseline.json`` + ``g8_headroom.json`` into ``out_dir`` via the
canonical JSON writer.

Both artifacts are dev/eval only (no install route, like ``g8_bucket_recall``).
The ``g8-bucket-recall`` CLI command is the shape mirror; a paired
``build eval-baseline`` Click command wires this into the CLI. This module's
:func:`main` is also directly callable for ad-hoc / test use.

This module follows the pipeline's determinism rules
(``common/determinism.py``) and the no-print-in-library-code rule -- it
uses the module logger; the CLI command does the user-facing echo.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json

from .baseline import build_baseline
from .headroom import build_headroom
from .spans import main as spans_main

logger = logging.getLogger(__name__)

BASELINE_FILENAME = "g8_detection_baseline.json"
HEADROOM_FILENAME = "g8_headroom.json"


def main(
    cells_path: Path,
    raw_scores_path: Path,
    out_dir: Path,
    *,
    spans_path: Path | None = None,
    corpus_path: Path | None = None,
) -> dict[str, Path]:
    """Build both eval artifacts from the two Swift JSONs into ``out_dir``.

    Args:
        cells_path: Path to the Swift ``_cells.json``.
        raw_scores_path: Path to the Swift ``_raw_scores.json``.
        out_dir: Directory the two derived artifacts are written into; created
            if absent.
        spans_path: Optional path to the emitter's per-span JSONL sidecar
            (``g8_detector_spans.jsonl`` / ``g8_siteb_spans.jsonl``). When
            given, ``g8_span_outcomes.json`` is derived beside the other two
            and the sidecar is reconciled against the cells payload.
        corpus_path: The G8 corpus the sidecar rows are joined to; required
            with ``spans_path``.

    Returns:
        A dict mapping ``"baseline"`` / ``"headroom"`` (and ``"spans"`` when a
        sidecar was read) to the written paths.

    Raises:
        PipelineError: If either input JSON is missing or unparsable
            (propagated from :func:`common.io.load_json`).
    """
    cells_payload: dict[str, Any] = load_json(cells_path)
    raw_scores_payload: dict[str, Any] = load_json(raw_scores_path)

    baseline = build_baseline(cells_payload)
    headroom = build_headroom(raw_scores_payload)

    baseline_path = out_dir / BASELINE_FILENAME
    headroom_path = out_dir / HEADROOM_FILENAME
    dump_canonical_json(baseline, baseline_path)
    dump_canonical_json(headroom, headroom_path)

    logger.info(
        "eval baseline: grand-total precision=%.4f recall=%.4f f1=%.4f -> %s",
        baseline["totals"]["precision"],
        baseline["totals"]["recall"],
        baseline["totals"]["f1"],
        baseline_path,
    )
    written = {"baseline": baseline_path, "headroom": headroom_path}
    if spans_path is not None:
        if corpus_path is None:
            raise PipelineError("a per-span sidecar needs the corpus it was emitted against")
        written["spans"] = spans_main(spans_path, corpus_path, out_dir, cells_payload=cells_payload)
    return written
