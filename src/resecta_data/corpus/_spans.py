"""Span-tracking string builder for corpus templates.

Each template constructs a document by appending plain text and
PII-tagged text to a :class:`SpanBuilder`. The builder records character
offsets as pieces are appended, so the final list of spans has accurate
``[start, end)`` offsets into the assembled text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from ._names import NAME_FORMS

ExpectedOutcome = Literal["redact", "suppress", "flag"]

# Packet-tier vocabulary (the sample-doc ground truth's must_fire / should_fire
# / watch / must_not_fire, spelled without the ``_fire`` suffix). Every G8 span
# carries one so the corpus runner can score per tier alongside the document
# harness; see ``bridge_tier`` for the default mapping from the older
# ``expected_outcome`` field.
Tier = Literal["must", "should", "watch", "must_not"]

# The context class of a span: the left-context slot it sits in.
# Every span of every family carries one so a per-span join over the corpus is
# total -- ``none`` where no label applies (every non-name span today). The
# eight classes the shipped templates draw are the fixed literal preceding the
# name (a caption side, a role/field label with its colon, a ``Dr.`` title, a
# subject line, a closing line, a salutation, or the document's first line);
# ``body_prose`` / ``table_cell`` / ``header`` are the slots this corpus lacks
# and exist for the generator profiles that add them. The class is an
# annotation beside the offsets: it changes no text and no offset.
ContextClass = Literal[
    "caption_left",
    "caption_right",
    "role_label",
    "title_label",
    "closing_line",
    "salutation",
    "subject_line",
    "document_initial",
    "body_prose",
    "table_cell",
    "header",
    "none",
]

CONTEXT_CLASSES: Final[tuple[str, ...]] = (
    "caption_left",
    "caption_right",
    "role_label",
    "title_label",
    "closing_line",
    "salutation",
    "subject_line",
    "document_initial",
    "body_prose",
    "table_cell",
    "header",
    "none",
)

NO_CONTEXT: Final[ContextClass] = "none"

_DEFAULT_OUTCOME: Final[ExpectedOutcome] = "redact"

# The G8 -> packet tier bridge. ``redact`` spans are designed to fire (the
# mechanism and its score clear the balanced cutoff by construction);
# ``flag`` spans are routed to user review (record-only); ``suppress`` spans
# are decoys the engine is designed to reject. ``should`` is never derived --
# a template assigns it explicitly to a surface it DESIGNED to be marginal
# (a context-scored family drawn outside its keyword window, so the engine's
# own profile scores it below the cutoff).
_TIER_BY_OUTCOME: Final[dict[str, Tier]] = {
    "redact": "must",
    "flag": "watch",
    "suppress": "must_not",
}


def bridge_tier(expected_outcome: ExpectedOutcome) -> Tier:
    """Return the packet tier an ``expected_outcome`` maps to by default."""
    return _TIER_BY_OUTCOME[expected_outcome]


# Placeholder templates substitute for person names in name-sparse
# documents. Plain text, never recorded as a span.
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
    # Non-PII page furniture a generator profile plants (the Spec-D axis):
    # [start, end) regions with a kind, recorded beside the spans and never
    # as a span. Empty under the ``g8`` profile.
    furniture: list[dict[str, Any]] = field(default_factory=list)

    def append(self, text: str) -> None:
        """Append plain (non-PII) text."""
        if not text:
            return
        self._parts.append(text)
        self._length += len(text)

    @property
    def at_line_start(self) -> bool:
        """True when nothing has been appended yet or the last piece ended a line."""
        return not self._parts or self._parts[-1].endswith("\n")

    def append_furniture(self, text: str, kind: str) -> None:
        """Append non-PII furniture text and record its ``[start, end)`` region.

        ``kind`` is the free-string furniture kind the corpus schema carries
        (``role_noun`` · ``label`` · ``plate_label`` · ``salutation`` ·
        ``closing`` from the Spec-D generator). The region is an annotation
        for the eval join (a detection overlapping it attributes to the kind);
        it is not ground truth and carries no tier.
        """
        if not text:
            return
        if not kind:
            raise ValueError("furniture needs a non-empty kind")
        start = self._length
        self._parts.append(text)
        self._length += len(text)
        self.furniture.append({"start": start, "end": self._length, "kind": kind})

    def append_pii(
        self,
        text: str,
        category: str,
        *,
        adversarial: bool = False,
        expected_outcome: ExpectedOutcome = _DEFAULT_OUTCOME,
        tier: Tier | None = None,
        context_class: ContextClass = NO_CONTEXT,
        form: str | None = None,
    ) -> None:
        """Append PII-tagged text and record its span.

        ``tier`` defaults to :func:`bridge_tier` of ``expected_outcome``; a
        template passes ``tier="should"`` only for a designed-marginal
        surface. A ``suppress`` span is always ``must_not`` and a ``flag``
        span always ``watch`` -- an explicit tier that contradicts the
        outcome is a template bug and raises.

        ``context_class`` names the left-context slot the span sits in
        (:data:`CONTEXT_CLASSES`); it defaults to ``none`` and every name
        call site passes its slot explicitly.

        ``form`` (name spans only; the Spec-A axis) names the surface form
        the value was rendered in (:data:`~resecta_data.corpus._names.NAME_FORMS`).
        The key is written only when given, so a corpus that does not draw
        forms carries no ``form`` key at all.
        """
        if not text:
            return
        if context_class not in CONTEXT_CLASSES:
            raise ValueError(f"unknown context_class {context_class!r} for a {category} span")
        if form is not None and (category != "name" or form not in NAME_FORMS):
            raise ValueError(f"form {form!r} is not a name form for a {category} span")
        resolved_tier = bridge_tier(expected_outcome) if tier is None else tier
        if expected_outcome != "redact" and resolved_tier != bridge_tier(expected_outcome):
            raise ValueError(
                f"tier {resolved_tier!r} contradicts expected_outcome {expected_outcome!r} "
                f"for a {category} span"
            )
        start = self._length
        self._parts.append(text)
        self._length += len(text)
        span: dict[str, Any] = {
            "category": category,
            "start": start,
            "end": self._length,
            "value": text,
            "adversarial": adversarial,
            "expected_outcome": expected_outcome,
            "tier": resolved_tier,
            "context_class": context_class,
        }
        if form is not None:
            span["form"] = form
        self.spans.append(span)

    def finalize(self) -> tuple[str, list[dict[str, Any]]]:
        """Return the assembled text and its spans (sorted by start offset)."""
        text = "".join(self._parts)
        spans = sorted(self.spans, key=lambda s: (s["start"], s["end"]))
        return text, spans

    def furniture_sorted(self) -> list[dict[str, Any]]:
        """The planted furniture regions, sorted by start offset."""
        return sorted(self.furniture, key=lambda f: (f["start"], f["end"], f["kind"]))


def append_name_or_placeholder(
    sb: SpanBuilder,
    full_name: str,
    *,
    name_sparse: bool,
    context_class: ContextClass,
    placeholder: str = REDACTED_NAME_PLACEHOLDER,
    form: str | None = None,
) -> None:
    """Append a person-name span, or the plain placeholder when sparse.

    ``context_class`` is required: every name slot a template emits names the
    left-context class it sits in, so the corpus join is total by construction.
    ``placeholder`` is the plain text a sparse slot renders (the literal
    ``[REDACTED]`` as furnished; a generator profile may hand in a role
    phrase); ``form`` is recorded on the span when given.
    """
    if name_sparse:
        sb.append(placeholder)
    else:
        sb.append_pii(full_name, "name", context_class=context_class, form=form)
