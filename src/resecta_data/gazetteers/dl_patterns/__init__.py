"""Per-state driver-license-number pattern gazetteer.

See :mod:`resecta_data.gazetteers.dl_patterns.build` for the
:func:`build` entry point.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
