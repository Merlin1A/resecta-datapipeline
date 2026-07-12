"""Negative-context candidate gazetteer builder (Phase 2).

Emits build/gazetteers/negative_context_candidates.json. The installed
``negative_context.json`` is a hand-reviewed subset owned by Jesse
— this pipeline never writes it directly.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
