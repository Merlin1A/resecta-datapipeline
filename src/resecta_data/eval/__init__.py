"""Detection-baseline derivation/aggregation scorers (S3 eval substrate).

The Swift G8 harness is the only place the live detector runs; it emits raw
join cells (``_cells.json``) and raw per-match scores (``_raw_scores.json``)
per the S3 contract. These modules DERIVE the committed-ready detection
baseline (P/R/F1/FPR + slices) and the M9 learned-term headroom probe from
those counts -- they do not re-run detection, re-join offsets, or read any
device score-dump. :mod:`resecta_data.eval.compare` then decides the
before/after §3 predicate purely from two derived baselines.

All three are dev/eval only: their artifacts are not installed to the Swift
Resources path (no ``INSTALL_ROUTES`` entry), like ``g8_bucket_recall``.

See the S3 eval baseline contract; this module follows the pipeline's
determinism (``common/determinism.py``) and mechanism-language
(``common/mechanism_language.py``) rules.
"""

from __future__ import annotations

from .baseline import build_baseline
from .compare import build_compare
from .headroom import build_headroom

__all__ = ["build_baseline", "build_compare", "build_headroom"]
