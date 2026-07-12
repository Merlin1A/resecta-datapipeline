"""Document-type classifier assets — keywords, temperature, presets.

Phase 3 ships the keyword dictionary and placeholder preset thresholds
(``build_doctype_keywords``, ``build_preset_thresholds``).

Phase 3b lands the calibrated temperature and final preset vectors, both of
which consume Swift-side softmax / detector-score dumps.
The dump contract is defined by ``schemas/doctype_softmax_dump.schema.json``
and ``schemas/detector_score_dump.schema.json``.
"""

from __future__ import annotations

from .context_scorer import build as build_context_scorer
from .doctype_keywords import build as build_doctype_keywords
from .fit_temperature import build as build_fit_temperature
from .preset_thresholds import build as build_preset_thresholds
from .sweep_thresholds import build as build_sweep_thresholds
from .sweep_thresholds import finalize as finalize_sweep_thresholds

__all__ = [
    "build_context_scorer",
    "build_doctype_keywords",
    "build_fit_temperature",
    "build_preset_thresholds",
    "build_sweep_thresholds",
    "finalize_sweep_thresholds",
]
