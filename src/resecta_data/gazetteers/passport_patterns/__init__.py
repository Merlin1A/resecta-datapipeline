"""Per-country passport-number pattern gazetteer.

See :mod:`resecta_data.gazetteers.passport_patterns.build` for the
:func:`build` entry point.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
