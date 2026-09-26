"""The document-level eval's inputs: manifest rows, ground truth, harness runs, OCR line dumps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict, cast

from ._common import _require

# ---------------------------------------------------------------------------
# The document-level eval's inputs (manifest, ground truth, harness runs, OCR line dumps)
# ---------------------------------------------------------------------------


class ManifestRow(TypedDict):
    id: str
    path: NotRequired[str]
    gt: NotRequired[str | None]


def as_manifest(raw: Sequence[Mapping[str, object]]) -> Sequence[ManifestRow]:
    """The parsed ``documents.manifest.json`` rows under their named shape."""
    return cast("Sequence[ManifestRow]", raw)


class OccurrenceSpan(TypedDict):
    page: NotRequired[int]
    bbox: NotRequired[list[float] | None]


class OccurrenceRender(TypedDict):
    multiline: NotRequired[bool]


class Occurrence(TypedDict):
    """One ground-truth occurrence; boxes are corner form [x0, y0, x1, y1]."""

    id: str
    category: str
    expectation: str
    page: NotRequired[int | None]
    bbox: NotRequired[list[float] | None]
    spans: NotRequired[list[OccurrenceSpan] | None]
    value: NotRequired[str]
    leg_applicability: NotRequired[list[str]]
    context_class: NotRequired[str | None]
    caption_clearance_pt: NotRequired[float | None]
    caption_text: NotRequired[str | None]
    render: NotRequired[OccurrenceRender | None]


class GroundTruthVariant(TypedDict):
    kind: NotRequired[str | None]


class GroundTruth(TypedDict):
    occurrences: list[Occurrence]
    carried_stmt: NotRequired[list[Occurrence] | None]
    variant: NotRequired[GroundTruthVariant | None]


def as_ground_truth(raw: Mapping[str, object]) -> GroundTruth:
    """A parsed ground-truth sidecar under its named shape (``occurrences`` required)."""
    _require(raw, ("occurrences",))
    return cast("GroundTruth", raw)


class Hit(TypedDict):
    """One harness hit; ``rect`` is [x, y, w, h], normalized, bottom-left origin."""

    page: int
    category: str
    rect: list[float]
    text: NotRequired[str | None]
    # Carried by the harness, never read by the join.
    confidence: NotRequired[float]
    source: NotRequired[str]
    ocr_confidence: NotRequired[float | None]


class HarnessRun(TypedDict):
    """One ``DocumentHarnessTests`` hits JSON (one document x leg x run)."""

    leg: str
    run_index: int
    hits: list[Hit]
    site: NotRequired[str]
    page_count: NotRequired[int]
    text_layer_status: NotRequired[list[str] | None]
    # Opaque harness diagnostics, carried through to the run block untouched.
    diagnostics: NotRequired[dict[str, object]]


def as_harness_run(raw: Mapping[str, object]) -> HarnessRun:
    """A parsed harness hits JSON under its named shape."""
    _require(raw, ("leg", "run_index", "hits"))
    return cast("HarnessRun", raw)


class OcrLine(TypedDict):
    text: str
    normalized: str


class OcrPage(TypedDict):
    page: int
    lines: list[OcrLine]


class OcrLineDump(TypedDict):
    pages: NotRequired[list[OcrPage]]


def as_ocr_line_dump(raw: Mapping[str, object]) -> OcrLineDump:
    """A parsed ``ocr-lines-run-<n>.json`` dump under its named shape."""
    return cast("OcrLineDump", raw)


__all__ = [
    "GroundTruth",
    "GroundTruthVariant",
    "HarnessRun",
    "Hit",
    "ManifestRow",
    "Occurrence",
    "OccurrenceRender",
    "OccurrenceSpan",
    "OcrLine",
    "OcrLineDump",
    "OcrPage",
    "as_ground_truth",
    "as_harness_run",
    "as_manifest",
    "as_ocr_line_dump",
]
