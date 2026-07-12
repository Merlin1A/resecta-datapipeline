"""G8 synthetic corpus generator (Phase 3)."""

from __future__ import annotations

from .generate import build as build_g8_corpus
from .negative import build as build_negative_corpus

__all__ = ["build_g8_corpus", "build_negative_corpus"]
