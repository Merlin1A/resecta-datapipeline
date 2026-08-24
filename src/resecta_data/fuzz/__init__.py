"""Fuzz fixture builders (Phase 1).

Two families live here: the ReDoS payload catalog (``redos``) and the
malformed-PDF fixture set (``pdf_mutations``). Neither module may import
``re``; ``tests/test_fuzz_no_re.py`` enforces that across the package.
"""

from __future__ import annotations

from .pdf_mutations import DEFAULT_COUNT as DEFAULT_MUTATION_COUNT
from .pdf_mutations import MUTATIONS_DIRNAME
from .pdf_mutations import build as build_pdf_mutations
from .redos import build

__all__ = [
    "DEFAULT_MUTATION_COUNT",
    "MUTATIONS_DIRNAME",
    "build",
    "build_pdf_mutations",
]
