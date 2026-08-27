"""Per-category positive context-keyword gazetteer.

See :mod:`resecta_data.gazetteers.context_keywords.build` for the
:func:`build` entry point.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
