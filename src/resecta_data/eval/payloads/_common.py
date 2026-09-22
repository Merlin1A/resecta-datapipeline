"""The pieces every payload module shares: the interval shape, the leg kinds, the key check."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

# A Wilson / BCa interval: [low, high], or null when the denominator is zero.
Interval = list[float] | None

# The leg kinds a document eval reports (``documents._leg_kind``), in order.
LegKind = Literal["text", "ocr", "ocr-forced", "mixed"]
LEG_KINDS: Final[tuple[LegKind, ...]] = ("text", "ocr", "ocr-forced", "mixed")


def _require(raw: Mapping[str, object], keys: tuple[str, ...]) -> None:
    """Fail like the dict access a stage makes: ``KeyError`` on the first absent key."""
    for key in keys:
        if key not in raw:
            raise KeyError(key)


__all__ = ["LEG_KINDS", "Interval", "LegKind"]
