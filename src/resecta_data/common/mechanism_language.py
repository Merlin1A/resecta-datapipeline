"""Enforcement of mechanism-description language on generated text.

Any human-readable copy produced by this pipeline — JSON comment strings,
error messages, README entries, NOTICE.txt content — must comply with the
banned-phrase rule below.

This module provides ``scan_text(...)`` which returns a list of offending
phrases found in a string. Builders that emit text should route it through
``assert_safe(...)`` before writing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from .exceptions import PipelineError

# Banned phrases.
# Matching is case-insensitive and tolerant of whitespace variants.
_BANNED_PHRASES: Final[tuple[str, ...]] = (
    # Outcome promises — hard bans.
    "guaranteed",
    "guarantees",
    "ensures",
    "securely removes",
    "100%",
    "impossible to recover",
    "impossible to recover",
    "military-grade",
    "bank-level",
    "certified",
    "structurally impossible",
    "the only provably secure approach",
    "destroy-level sanitization per nist",
    "mathematically irreversible",
    "security invariant",
    "provably reliable",
    "your data is safe",
    "no one can recover",
    # Additional phrases surfaced in the marketing strategy review.
    "fully secure",
    "completely secure",
    "absolutely safe",
    "tamper-proof",
    "unhackable",
)


@dataclass(frozen=True, slots=True)
class BannedPhraseMatch:
    """One occurrence of a banned phrase in scanned text.

    Attributes:
        phrase: The phrase that matched (lower-cased canonical form).
        start: Character offset of the match in the original text.
        end: Exclusive end offset.
        excerpt: ±30 characters of surrounding context for debugging.
    """

    phrase: str
    start: int
    end: int
    excerpt: str


def _compile_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Compile banned phrases into case-insensitive, whitespace-tolerant patterns.

    Word boundaries (``\\b``) are applied only on sides where the phrase
    begins or ends with a word character. Phrases that already start or end
    with a non-word character (``100%``, ``-grade``) would fail a ``\\b``
    check against adjacent non-word characters, so we omit the boundary on
    those sides.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for phrase in _BANNED_PHRASES:
        tokens = phrase.split()
        inner = r"\s+".join(re.escape(t) for t in tokens)

        # Add \b only where an adjacent word character would create ambiguity.
        # The first/last character of the phrase after escaping is what matters.
        left_boundary = r"\b" if phrase[0].isalnum() or phrase[0] == "_" else r""
        right_boundary = r"\b" if phrase[-1].isalnum() or phrase[-1] == "_" else r""

        pattern = left_boundary + inner + right_boundary
        compiled.append((phrase, re.compile(pattern, re.IGNORECASE)))
    return compiled


_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = _compile_patterns()


def scan_text(text: str) -> list[BannedPhraseMatch]:
    """Return every banned-phrase occurrence in ``text``.

    Returns:
        A list of matches. Empty list means the text is safe.
    """
    matches: list[BannedPhraseMatch] = []
    for phrase, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.span()
            excerpt_start = max(0, start - 30)
            excerpt_end = min(len(text), end + 30)
            matches.append(
                BannedPhraseMatch(
                    phrase=phrase,
                    start=start,
                    end=end,
                    excerpt=text[excerpt_start:excerpt_end],
                )
            )
    return matches


def assert_safe(text: str, *, context: str = "<unspecified>") -> None:
    """Raise if ``text`` contains any banned phrase.

    Args:
        text: The string to check.
        context: Description of where this text is headed (e.g., a filename).
            Included in the error message to speed debugging.

    Raises:
        PipelineError: If any banned phrase is found.
    """
    hits = scan_text(text)
    if not hits:
        return

    lines = [f"Banned phrase(s) in text destined for {context}:"]
    for h in hits:
        lines.append(f"  - {h.phrase!r} at offset {h.start}: ...{h.excerpt!r}...")
    lines.append("See CLAUDE.md §5 for the safe-patterns rule.")
    raise PipelineError("\n".join(lines))
