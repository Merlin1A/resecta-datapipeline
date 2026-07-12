"""Span-tracking string builder for corpus templates.

Each template constructs a document by appending plain text and
PII-tagged text to a :class:`SpanBuilder`. The builder records character
offsets as pieces are appended, so the final list of spans has accurate
``[start, end)`` offsets into the assembled text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

ExpectedOutcome = Literal["redact", "suppress", "flag"]

_DEFAULT_OUTCOME: Final[ExpectedOutcome] = "redact"

# Placeholder templates substitute for person names in name-sparse
# documents (design 03 §3.2). Plain text, never recorded as a span.
REDACTED_NAME_PLACEHOLDER: Final[str] = "[REDACTED]"


@dataclass
class SpanBuilder:
    """Accumulate text while tracking PII span offsets.

    Usage:
        sb = SpanBuilder()
        sb.append("Dear ")
        sb.append_pii("Jane Doe", "name")
        sb.append(", your SSN ")
        sb.append_pii("123-45-6789", "ssn")
        sb.append(".")
        text, spans = sb.finalize()
    """

    _parts: list[str] = field(default_factory=list)
    _length: int = 0
    spans: list[dict[str, Any]] = field(default_factory=list)

    def append(self, text: str) -> None:
        """Append plain (non-PII) text."""
        if not text:
            return
        self._parts.append(text)
        self._length += len(text)

    def append_pii(
        self,
        text: str,
        category: str,
        *,
        adversarial: bool = False,
        expected_outcome: ExpectedOutcome = _DEFAULT_OUTCOME,
    ) -> None:
        """Append PII-tagged text and record its span."""
        if not text:
            return
        start = self._length
        self._parts.append(text)
        self._length += len(text)
        self.spans.append(
            {
                "category": category,
                "start": start,
                "end": self._length,
                "value": text,
                "adversarial": adversarial,
                "expected_outcome": expected_outcome,
            }
        )

    def finalize(self) -> tuple[str, list[dict[str, Any]]]:
        """Return the assembled text and its spans (sorted by start offset)."""
        text = "".join(self._parts)
        spans = sorted(self.spans, key=lambda s: (s["start"], s["end"]))
        return text, spans


def append_name_or_placeholder(sb: SpanBuilder, full_name: str, *, name_sparse: bool) -> None:
    """Append a person-name span, or the plain placeholder when sparse."""
    if name_sparse:
        sb.append(REDACTED_NAME_PLACEHOLDER)
    else:
        sb.append_pii(full_name, "name")
