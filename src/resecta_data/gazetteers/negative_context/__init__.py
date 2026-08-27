"""Negative-context candidate gazetteer builder (Phase 2).

Emits build/gazetteers/negative_context_candidates.json. The installed
``negative_context.json`` is the reviewed subset, changed under an approved
change plan — this pipeline never writes it directly.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
