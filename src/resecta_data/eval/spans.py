"""Per-span outcome reader for the G8 emitters' JSONL sidecars.

Beside each trio, the two Swift emitters (``G8BaselineHarnessTests.emitBaseline``
at the detector site, ``G8SearchParityHarnessTests.emitSiteBBaseline`` at
Site B) write one JSON-Lines sidecar (``g8_detector_spans.jsonl`` /
``g8_siteb_spans.jsonl``): one row per ground-truth span of all 17 families,
plus one row per surfaced detection that overlaps no ground-truth span. Rows
carry OFFSETS ONLY -- ``{doc_id, family, start, end, tier, outcome}``, with
``det_start`` / ``det_end`` (the hull of every surfaced same-family detection
overlapping the span) and ``det_spans`` (those detections' own ``[start,
end]`` pairs, in start order) on the rows where a detection covered the span.
No document text ever leaves the harness; the corpus text is read HERE, on
the datapipeline side, and only to count whitespace-separated tokens. The
emitter's offsets are UTF-16 units and the corpus offsets are code points;
they coincide because every corpus character sits inside the Basic
Multilingual Plane (checked, never assumed).

Outcome vocabulary, under the same binary offset-overlap join the trio uses:

- ``tp`` / ``fn`` -- a positive ground-truth span (tier must / should / watch)
  covered / not covered by at least one surfaced detection of its family;
  ``tp`` rows carry the hull and the pairs of every overlapping detection
  (the name path emits one detection per token, so a two-token name is
  usually covered by two detections -- the pairs keep that split countable).
- ``fp`` with ``tier: null`` -- a surfaced detection overlapping no
  ground-truth span of its family (``start`` / ``end`` are the detection's).
- ``fp`` with ``tier: must_not`` -- a planted decoy span that fired (carries
  the detection's offsets like a ``tp`` row); ``tn`` -- a decoy that stayed
  quiet. The trio counts these under ``tier_must_not_fired`` / ``_total``,
  never under ``false_positives``, and so does this reader.

This module validates every row, cross-checks each ground-truth row against
the corpus (family and tier by the same bridge the emitter applies),
reconciles the per-cell tallies against the trio's ``_cells.json`` when that
payload is supplied (the sidecar must reproduce the cells exactly), and
aggregates per family x doctype x bucket x outcome with a Wilson interval on
recall. When the corpus carries the context annotation (every span's
``context_class``, the generator's left-context slot), the ground-truth rows
are ALSO aggregated per family x context class (``by_context_class``) and per
family x doctype x bucket x context class (``cells_by_context_class``); the
emitter never classifies, the class is read from the corpus at the join.
Detection-only ``fp`` rows name no ground-truth span and so carry no class;
when the corpus carries planted FURNITURE (a generator profile's
``furniture[] {start, end, kind}`` regions -- 1.2 C12-95 Spec-D), every
detection-only ``fp`` row is instead joined to the furniture regions it
overlaps and counted per family x furniture kind (``by_furniture_kind``),
the rows overlapping no region as ``unattributed``. An unannotated corpus
yields ``context_class: null`` and empty class tables; a corpus with no
furniture yields ``furniture: null`` and empty kind tables.

Determinism: sorted iteration everywhere, hashes of the input bytes instead
of any clock. Library code logs, never prints.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json, sha256_bytes
from resecta_data.corpus._spans import CONTEXT_CLASSES

from .documents import wilson_ci

logger = logging.getLogger(__name__)

SPAN_OUTCOMES_FILENAME: Final[str] = "g8_span_outcomes.json"

_MODULE_NAME: Final[str] = "resecta_data.eval.spans"
# 2 since the context-class tables (by_context_class / cells_by_context_class
# and a descriptor in place of the reserved null); 3 since the furniture join
# (by_furniture_kind + a furniture descriptor) over the detection-only rows.
_SCHEMA_VERSION: Final[int] = 3
_CONTEXT_CLASS_SOURCE: Final[str] = (
    "corpus pii_spans[].context_class (the generator's left-context slot, read at the "
    "join; the emitter never classifies)"
)
_FURNITURE_SOURCE: Final[str] = (
    "corpus documents[].furniture[] {start, end, kind} (the generator profile's planted "
    "regions, read at the join; the emitter never classifies)"
)
_FURNITURE_JOIN_RULE: Final[str] = (
    "a detection-only fp row (tier null) attributes to every furniture kind whose "
    "[start, end) region overlaps the detection's offsets; a row overlapping no region "
    "is unattributed; ground-truth rows never join furniture"
)
_UNATTRIBUTED: Final[str] = "unattributed"
_METRIC: Final[str] = "g8_span_outcomes"

# The 17 G8 corpus categories, in the corpus vocabulary the sidecar uses,
# each mapped to the trio's cell-category key (PIICategory.rawValue,
# lowercased, spaces stripped -- the apostrophe survives).
FAMILY_TO_CELL_KEY: Final[dict[str, str]] = {
    "ssn": "ssn",
    "npi": "npi",
    "dea": "dea",
    "dob": "dateofbirth",
    "address": "address",
    "account": "account",
    "mrn": "medicalrecord",
    "name": "name",
    "phone": "phone",
    "email": "email",
    "routingNumber": "routingnumber",
    "ein": "ein",
    "itin": "itin",
    "creditCard": "creditcard",
    "driversLicense": "driver'slicense",
    "passport": "passport",
    "licensePlate": "licenseplate",
}
FAMILIES: Final[tuple[str, ...]] = tuple(FAMILY_TO_CELL_KEY)

_OUTCOMES: Final[frozenset[str]] = frozenset({"tp", "fn", "fp", "tn"})
_POSITIVE_TIERS: Final[frozenset[str]] = frozenset({"must", "should", "watch"})
_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"doc_id", "family", "start", "end", "tier", "outcome"}
)
_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"det_start", "det_end", "det_spans"})
_ALLOWED_KEYS: Final[frozenset[str]] = _REQUIRED_KEYS | _OPTIONAL_KEYS

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\S+")
# The last code point of the Basic Multilingual Plane: every character at or
# below it is one UTF-16 unit, so the emitter's offsets equal the corpus's.
_MAX_BMP_CODE_POINT: Final[int] = 0xFFFF


def bridged_tier(span: dict[str, Any]) -> str:
    """The packet tier of a corpus span: its explicit ``tier``, else the bridge.

    The same bridge the Swift emitters apply (``bridgedTier``): suppress ->
    must_not, flag -> watch, anything else -> must.
    """
    tier = span.get("tier")
    if isinstance(tier, str) and tier:
        return tier
    outcome = span.get("expected_outcome")
    if outcome == "suppress":
        return "must_not"
    if outcome == "flag":
        return "watch"
    return "must"


# ---------------------------------------------------------------------------
# Reading + validation
# ---------------------------------------------------------------------------


def _is_offset(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_shape(row: Any, where: str) -> dict[str, Any]:
    """Keys, families, outcomes and offsets of one sidecar row."""
    if not isinstance(row, dict):
        raise PipelineError(f"{where}: row is not a JSON object")
    keys = set(row)
    extra = keys - _ALLOWED_KEYS
    if extra:
        raise PipelineError(f"{where}: unexpected keys {sorted(extra)} (offsets only)")
    missing = _REQUIRED_KEYS - keys
    if missing:
        raise PipelineError(f"{where}: missing keys {sorted(missing)}")
    if row["family"] not in FAMILY_TO_CELL_KEY:
        raise PipelineError(f"{where}: unknown family {row['family']!r}")
    if row["outcome"] not in _OUTCOMES:
        raise PipelineError(f"{where}: unknown outcome {row['outcome']!r}")
    if not isinstance(row["doc_id"], str) or not row["doc_id"]:
        raise PipelineError(f"{where}: doc_id must be a non-empty string")
    if not (_is_offset(row["start"]) and _is_offset(row["end"]) and row["start"] < row["end"]):
        raise PipelineError(f"{where}: offsets must satisfy 0 <= start < end")
    has_det = "det_start" in row or "det_end" in row or "det_spans" in row
    if has_det and not (
        "det_start" in row
        and "det_end" in row
        and _is_offset(row["det_start"])
        and _is_offset(row["det_end"])
        and row["det_start"] < row["det_end"]
    ):
        raise PipelineError(f"{where}: det_start / det_end must be a pair of offsets")
    if has_det and not (row["det_start"] < row["end"] and row["start"] < row["det_end"]):
        raise PipelineError(f"{where}: the detection hull does not overlap the span")
    if has_det:
        _validate_det_spans(row, where)
    return row


def _validate_det_spans(row: dict[str, Any], where: str) -> None:
    """``det_spans``: non-empty, start-ordered [start, end] pairs inside the hull,
    each overlapping the span, whose hull IS det_start / det_end."""
    spans = row.get("det_spans")
    if not isinstance(spans, list) or not spans:
        raise PipelineError(f"{where}: det_spans must list every overlapping detection")
    prev = (-1, -1)
    for pair in spans:
        if not (
            isinstance(pair, list)
            and len(pair) == 2  # noqa: PLR2004 -- a [start, end] pair
            and _is_offset(pair[0])
            and _is_offset(pair[1])
            and pair[0] < pair[1]
        ):
            raise PipelineError(f"{where}: det_spans entries are [start, end] offset pairs")
        if (pair[0], pair[1]) < prev:
            raise PipelineError(f"{where}: det_spans must be in start order")
        if not (pair[0] < row["end"] and row["start"] < pair[1]):
            raise PipelineError(f"{where}: a det_spans entry does not overlap the span")
        prev = (pair[0], pair[1])
    hull = (min(p[0] for p in spans), max(p[1] for p in spans))
    if hull != (row["det_start"], row["det_end"]):
        raise PipelineError(f"{where}: det_start / det_end must be the hull of det_spans")


def _validate_outcome(row: dict[str, Any], where: str) -> None:
    """The tier / det-offset rules of each outcome."""
    tier, outcome = row["tier"], row["outcome"]
    has_det = "det_start" in row
    if outcome in {"tp", "fn"}:
        if tier not in _POSITIVE_TIERS:
            raise PipelineError(f"{where}: {outcome} row needs a positive tier, got {tier!r}")
        if (outcome == "tp") != has_det:
            raise PipelineError(f"{where}: det offsets are required on tp rows and only there")
    elif outcome == "tn":
        if tier != "must_not" or has_det:
            raise PipelineError(f"{where}: tn rows are quiet must_not spans without det offsets")
    elif tier is None:
        if has_det:
            raise PipelineError(f"{where}: a detection-only fp row carries no det offsets")
    elif tier == "must_not":
        if not has_det:
            raise PipelineError(f"{where}: a fired must_not row carries the det offsets")
    else:
        raise PipelineError(f"{where}: fp rows carry tier null or must_not, got {tier!r}")


def _validate_row(row: Any, line_no: int) -> dict[str, Any]:
    """Validate one sidecar row; return it unchanged."""
    where = f"sidecar line {line_no}"
    checked = _validate_shape(row, where)
    _validate_outcome(checked, where)
    return checked


def read_sidecar(path: Path) -> list[dict[str, Any]]:
    """Read and validate a JSONL sidecar (one object per line, LF-terminated)."""
    if not path.is_file():
        raise PipelineError(f"span sidecar not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.rstrip("\n")
            if not text:
                raise PipelineError(f"sidecar line {line_no}: blank line")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PipelineError(f"sidecar line {line_no}: {exc}") from exc
            rows.append(_validate_row(parsed, line_no))
    return rows


def _is_gt_row(row: dict[str, Any]) -> bool:
    return row["tier"] is not None


# ---------------------------------------------------------------------------
# Corpus join
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CorpusSpan:
    family: str
    tier: str
    tokens: tuple[tuple[int, int], ...]
    context_class: str | None


@dataclass(frozen=True)
class _CorpusDoc:
    doctype: str
    bucket: str
    spans: dict[tuple[int, int], _CorpusSpan]
    furniture: tuple[tuple[int, int, str], ...] = ()


def _token_offsets(text: str, start: int, end: int) -> tuple[tuple[int, int], ...]:
    """Absolute [start, end) offsets of the whitespace-separated tokens in text[start:end]."""
    return tuple((start + m.start(), start + m.end()) for m in _TOKEN_RE.finditer(text[start:end]))


def _index_corpus(corpus: dict[str, Any]) -> dict[str, _CorpusDoc]:
    docs: dict[str, _CorpusDoc] = {}
    for doc in corpus.get("documents", []):
        text = doc["text"]
        if any(ord(ch) > _MAX_BMP_CODE_POINT for ch in text):
            # The emitter's offsets are UTF-16 units and the corpus offsets
            # are code points; the two coincide for every character inside
            # the Basic Multilingual Plane (the corpus uses accented Latin
            # letters and the em dash, nothing beyond), so a surrogate pair
            # would silently shift every later offset.
            raise PipelineError(
                f"corpus document {doc['id']} carries a character beyond U+FFFF; "
                "UTF-16 and code-point offsets would not join"
            )
        spans: dict[tuple[int, int], _CorpusSpan] = {}
        for span in doc["pii_spans"]:
            key = (span["start"], span["end"])
            if key in spans:
                raise PipelineError(f"corpus document {doc['id']} repeats span offsets {key}")
            spans[key] = _CorpusSpan(
                family=span["category"],
                tier=bridged_tier(span),
                tokens=_token_offsets(text, span["start"], span["end"]),
                context_class=_context_class_of(span, doc["id"]),
            )
        docs[doc["id"]] = _CorpusDoc(
            doctype=doc["doctype"],
            bucket=doc["demographic_bucket"],
            spans=spans,
            furniture=_furniture_of(doc, len(text)),
        )
    return docs


def _furniture_of(doc: dict[str, Any], text_length: int) -> tuple[tuple[int, int, str], ...]:
    """The document's planted furniture regions, validated against its text."""
    regions: list[tuple[int, int, str]] = []
    for item in doc.get("furniture") or []:
        start, end, kind = item.get("start"), item.get("end"), item.get("kind")
        if not (_is_offset(start) and _is_offset(end) and start < end <= text_length):
            raise PipelineError(f"corpus document {doc['id']}: malformed furniture region {item!r}")
        if not isinstance(kind, str) or not kind:
            raise PipelineError(f"corpus document {doc['id']}: furniture region without a kind")
        regions.append((start, end, kind))
    return tuple(sorted(regions))


def _context_class_of(span: dict[str, Any], doc_id: str) -> str | None:
    """The span's annotated context class, or None on an unannotated corpus."""
    value = span.get("context_class")
    if value is None:
        return None
    if value not in CONTEXT_CLASSES:
        raise PipelineError(
            f"corpus document {doc_id}@{(span['start'], span['end'])}: "
            f"unknown context_class {value!r}"
        )
    return str(value)


def _covered_tokens(tokens: Iterable[tuple[int, int]], det_spans: Iterable[list[int]]) -> int:
    """Tokens of the span that at least one detection pair overlaps."""
    pairs = list(det_spans)
    return sum(1 for s, e in tokens if any(ds < e and s < de for ds, de in pairs))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class _Tally:
    tp: int = 0
    fn: int = 0
    fp: int = 0
    must_not_total: int = 0
    must_not_fired: int = 0
    one_token_tp: int = 0
    detections_per_tp: dict[str, int] = field(default_factory=dict)
    tier_total: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_POSITIVE_TIERS, 0))
    tier_covered: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_POSITIVE_TIERS, 0))
    coverage: dict[str, int] = field(default_factory=dict)

    def fold(self, row: dict[str, Any], span: _CorpusSpan | None) -> None:
        outcome, tier = row["outcome"], row["tier"]
        if outcome == "tp":
            self.tp += 1
            self.tier_total[tier] += 1
            self.tier_covered[tier] += 1
            n_det = str(len(row["det_spans"]))
            self.detections_per_tp[n_det] = self.detections_per_tp.get(n_det, 0) + 1
            if span is not None:
                covered = _covered_tokens(span.tokens, row["det_spans"])
                total = len(span.tokens)
                bucket = f"{covered}/{total}"
                self.coverage[bucket] = self.coverage.get(bucket, 0) + 1
                if covered < total:
                    self.one_token_tp += 1
        elif outcome == "fn":
            self.fn += 1
            self.tier_total[tier] += 1
        elif outcome == "tn":
            self.must_not_total += 1
        elif tier is None:
            self.fp += 1
        else:
            self.must_not_total += 1
            self.must_not_fired += 1

    def view(self) -> dict[str, Any]:
        positives = self.tp + self.fn
        return {
            "tp": self.tp,
            "fn": self.fn,
            "fp": self.fp,
            "must_not_total": self.must_not_total,
            "must_not_fired": self.must_not_fired,
            "one_token_tp": self.one_token_tp,
            "recall": self.tp / positives if positives else 0.0,
            "recall_wilson95": wilson_ci(self.tp, positives),
            "by_tier": {
                tier: {"total": self.tier_total[tier], "covered": self.tier_covered[tier]}
                for tier in sorted(_POSITIVE_TIERS)
            },
            "token_coverage": {k: self.coverage[k] for k in sorted(self.coverage)},
            "detections_per_tp": {
                k: self.detections_per_tp[k] for k in sorted(self.detections_per_tp, key=int)
            },
        }


def _cell_key(family: str, doctype: str, bucket: str) -> str:
    return f"{family}_{doctype}_{bucket}"


def _crosscheck_cells(cells_payload: dict[str, Any], tallies: dict[str, _Tally]) -> dict[str, Any]:
    """The sidecar must reproduce the trio's per-cell counters exactly."""
    trio_cells: dict[str, Any] = cells_payload["cells"]
    seen: set[str] = set()
    mismatches: list[str] = []
    for key, tally in sorted(tallies.items()):
        family, doctype, bucket = key.split("_", 2)
        trio_key = _cell_key(FAMILY_TO_CELL_KEY[family], doctype, bucket)
        cell = trio_cells.get(trio_key)
        if cell is None:
            mismatches.append(f"{trio_key}: present in the sidecar, absent from the cells")
            continue
        seen.add(trio_key)
        expected = {
            "true_positives": tally.tp,
            "false_negatives": tally.fn,
            "false_positives": tally.fp,
            "tier_must_not_total": tally.must_not_total,
            "tier_must_not_fired": tally.must_not_fired,
            "tier_must_total": tally.tier_total["must"],
            "tier_must_covered": tally.tier_covered["must"],
            "tier_should_total": tally.tier_total["should"],
            "tier_should_covered": tally.tier_covered["should"],
            "tier_watch_total": tally.tier_total["watch"],
            "tier_watch_covered": tally.tier_covered["watch"],
        }
        for counter, value in expected.items():
            if cell.get(counter, 0) != value:
                mismatches.append(
                    f"{trio_key}.{counter}: sidecar {value} vs cells {cell.get(counter)}"
                )
    for trio_key, cell in sorted(trio_cells.items()):
        if trio_key in seen:
            continue
        nonzero = {k: v for k, v in cell.items() if isinstance(v, int) and v}
        if nonzero:
            mismatches.append(f"{trio_key}: in the cells with {nonzero}, absent from the sidecar")
    if mismatches:
        raise PipelineError(
            "span sidecar does not reproduce the trio cells: " + "; ".join(mismatches[:20])
        )
    return {"status": "identical", "cells_compared": len(seen)}


@dataclass
class _JoinResult:
    tallies: dict[str, _Tally]
    class_tallies: dict[tuple[str, str, str, str], _Tally]
    n_gt: int
    counts: dict[str, int]
    classed: int
    unclassed: int
    # (family, doctype, kind) -> detection-only fp rows overlapping a region of
    # that kind; kind == "unattributed" for rows overlapping no region.
    furniture_fp: dict[tuple[str, str, str], int] = field(default_factory=dict)


def _join_rows(rows: list[dict[str, Any]], docs: dict[str, _CorpusDoc]) -> _JoinResult:
    """Fold every row into its cell tally, cross-checking ground truth against the corpus.

    Ground-truth rows whose corpus span carries a context class are also folded
    into a (family, doctype, bucket, class) tally; detection-only fp rows never
    are (they name no span). A corpus that classes some spans and not others is
    an error: the annotation is total or absent.
    """
    tallies: dict[str, _Tally] = {}
    class_tallies: dict[tuple[str, str, str, str], _Tally] = {}
    furniture_fp: dict[tuple[str, str, str], int] = {}
    gt_seen: set[tuple[str, int, int]] = set()
    counts = dict.fromkeys(("tp", "fn", "fp", "tn", "must_not_fired"), 0)
    classed = unclassed = 0
    for row in rows:
        doc = docs.get(row["doc_id"])
        if doc is None:
            raise PipelineError(f"sidecar row names unknown document {row['doc_id']!r}")
        span: _CorpusSpan | None = None
        if _is_gt_row(row):
            span = _corpus_span_for(row, doc)
            gt_key = (row["doc_id"], row["start"], row["end"])
            if gt_key in gt_seen:
                raise PipelineError(f"{row['doc_id']}@{gt_key[1:]}: duplicate ground-truth row")
            gt_seen.add(gt_key)
        if row["outcome"] == "fp" and row["tier"] == "must_not":
            counts["must_not_fired"] += 1
        else:
            counts[row["outcome"]] += 1
        tallies.setdefault(_cell_key(row["family"], doc.doctype, doc.bucket), _Tally()).fold(
            row, span
        )
        if row["outcome"] == "fp" and row["tier"] is None:
            for kind in _furniture_kinds_at(doc, row["start"], row["end"]):
                fk = (row["family"], doc.doctype, kind)
                furniture_fp[fk] = furniture_fp.get(fk, 0) + 1
        if span is not None:
            if span.context_class is None:
                unclassed += 1
            else:
                classed += 1
                class_tallies.setdefault(
                    (row["family"], doc.doctype, doc.bucket, span.context_class), _Tally()
                ).fold(row, span)
    if classed and unclassed:
        raise PipelineError(
            f"corpus annotation is partial: {classed} classed and {unclassed} unclassed "
            "ground-truth spans (context_class must be on every span or on none)"
        )
    return _JoinResult(
        tallies, class_tallies, len(gt_seen), counts, classed, unclassed, furniture_fp
    )


def _furniture_kinds_at(doc: _CorpusDoc, start: int, end: int) -> tuple[str, ...]:
    """The kinds of the furniture regions overlapping [start, end), or the
    unattributed marker when none does (each kind once)."""
    kinds = sorted({kind for s, e, kind in doc.furniture if s < end and start < e})
    return tuple(kinds) if kinds else (_UNATTRIBUTED,)


def _corpus_span_for(row: dict[str, Any], doc: _CorpusDoc) -> _CorpusSpan:
    """The corpus span a ground-truth row names; family and tier must agree."""
    key = (row["start"], row["end"])
    span = doc.spans.get(key)
    if span is None:
        raise PipelineError(f"{row['doc_id']}: no corpus span at {key}")
    if span.family != row["family"]:
        raise PipelineError(
            f"{row['doc_id']}@{key}: family {row['family']!r} vs corpus {span.family!r}"
        )
    if span.tier != row["tier"]:
        raise PipelineError(
            f"{row['doc_id']}@{key}: tier {row['tier']!r} vs corpus bridge {span.tier!r}"
        )
    return span


def _add_into(target: _Tally, tally: _Tally) -> None:
    target.tp += tally.tp
    target.fn += tally.fn
    target.fp += tally.fp
    target.must_not_total += tally.must_not_total
    target.must_not_fired += tally.must_not_fired
    target.one_token_tp += tally.one_token_tp
    for tier in _POSITIVE_TIERS:
        target.tier_total[tier] += tally.tier_total[tier]
        target.tier_covered[tier] += tally.tier_covered[tier]
    for k, v in tally.coverage.items():
        target.coverage[k] = target.coverage.get(k, 0) + v
    for k, v in tally.detections_per_tp.items():
        target.detections_per_tp[k] = target.detections_per_tp.get(k, 0) + v


def _roll_up(tallies: dict[str, _Tally]) -> tuple[dict[str, _Tally], _Tally]:
    """Per-family and grand-total tallies from the cell tallies."""
    per_family: dict[str, _Tally] = {}
    totals = _Tally()
    for key, tally in tallies.items():
        family = key.split("_", 1)[0]
        _add_into(per_family.setdefault(family, _Tally()), tally)
        _add_into(totals, tally)
    return per_family, totals


def _roll_up_classes(
    class_tallies: dict[tuple[str, str, str, str], _Tally],
) -> dict[str, dict[str, _Tally]]:
    """Per family x context class tallies from the class cell tallies."""
    out: dict[str, dict[str, _Tally]] = {}
    for (family, _doctype, _bucket, cls), tally in class_tallies.items():
        _add_into(out.setdefault(family, {}).setdefault(cls, _Tally()), tally)
    return out


def build_span_outcomes(
    rows: list[dict[str, Any]],
    corpus: dict[str, Any],
    *,
    site: str,
    spans_sha256: str,
    corpus_sha256: str,
    cells_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate validated sidecar rows into the ``g8_span_outcomes`` payload."""
    docs = _index_corpus(corpus)
    n_corpus_spans = sum(len(d.spans) for d in docs.values())
    joined = _join_rows(rows, docs)
    tallies, n_gt, counts = joined.tallies, joined.n_gt, joined.counts
    if n_gt != n_corpus_spans:
        raise PipelineError(
            f"sidecar carries {n_gt} ground-truth rows; the corpus has {n_corpus_spans} spans"
        )
    crosscheck = (
        _crosscheck_cells(cells_payload, tallies)
        if cells_payload is not None
        else {"status": "not_run", "cells_compared": 0}
    )
    per_family, totals = _roll_up(tallies)

    cells_out: dict[str, Any] = {}
    for key in sorted(tallies):
        family, doctype, bucket = key.split("_", 2)
        cells_out[key] = {
            "family": family,
            "doctype": doctype,
            "bucket": bucket,
            "context_class": None,
            **tallies[key].view(),
        }

    annotated = joined.classed > 0
    class_descriptor: dict[str, Any] | None = None
    by_class_out: dict[str, Any] = {}
    class_cells_out: dict[str, Any] = {}
    if annotated:
        present = sorted({key[3] for key in joined.class_tallies})
        class_descriptor = {
            "source": _CONTEXT_CLASS_SOURCE,
            "vocabulary": list(CONTEXT_CLASSES),
            "present": present,
            "classed_ground_truth_rows": joined.classed,
        }
        per_class = _roll_up_classes(joined.class_tallies)
        by_class_out = {
            family: {cls: per_class[family][cls].view() for cls in sorted(per_class[family])}
            for family in sorted(per_class)
        }
        for class_key in sorted(joined.class_tallies):
            c_family, c_doctype, c_bucket, cls = class_key
            class_cells_out[f"{c_family}_{c_doctype}_{c_bucket}_{cls}"] = {
                "family": c_family,
                "doctype": c_doctype,
                "bucket": c_bucket,
                "context_class": cls,
                **joined.class_tallies[class_key].view(),
            }

    furniture_descriptor, by_furniture_out = _furniture_tables(docs, joined.furniture_fp)

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "metric": _METRIC,
        "site": site,
        "source_spans_sha256": spans_sha256,
        "source_corpus_sha256": corpus_sha256,
        "join_rule": "binary offset overlap, half-open [start, end), same family",
        "context_class": class_descriptor,
        "furniture": furniture_descriptor,
        "row_counts": {
            "total": len(rows),
            "ground_truth": n_gt,
            "corpus_spans": n_corpus_spans,
            **{k: counts[k] for k in ("tp", "fn", "fp", "tn", "must_not_fired")},
            "one_token_tp": totals.one_token_tp,
        },
        "one_token_rule": "a tp row whose detections together overlap fewer "
        "whitespace-separated tokens than the ground-truth span holds (counted on the "
        "corpus text here; the emitter carries offsets only)",
        "cells_crosscheck": crosscheck,
        "per_family": {family: per_family[family].view() for family in sorted(per_family)},
        "cells": cells_out,
        "by_context_class": by_class_out,
        "cells_by_context_class": class_cells_out,
        "by_furniture_kind": by_furniture_out,
        "totals": totals.view(),
    }


def _furniture_tables(
    docs: dict[str, _CorpusDoc], furniture_fp: dict[tuple[str, str, str], int]
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The furniture descriptor and the per-family x kind fp table.

    Null / empty when no document carries furniture (the corpus as furnished);
    otherwise every family with at least one detection-only fp row appears,
    each kind with its fp count and its split by doctype, plus the rows that
    overlapped no region under ``unattributed``.
    """
    regions = sum(len(d.furniture) for d in docs.values())
    if regions == 0:
        return None, {}
    kinds_present = sorted({kind for d in docs.values() for _s, _e, kind in d.furniture})
    descriptor = {
        "source": _FURNITURE_SOURCE,
        "join": _FURNITURE_JOIN_RULE,
        "kinds_present": kinds_present,
        "regions": regions,
        "documents_with_furniture": sum(1 for d in docs.values() if d.furniture),
    }
    table: dict[str, Any] = {}
    for (family, doctype, kind), n in sorted(furniture_fp.items()):
        entry = table.setdefault(family, {}).setdefault(kind, {"fp": 0, "by_doctype": {}})
        entry["fp"] += n
        entry["by_doctype"][doctype] = entry["by_doctype"].get(doctype, 0) + n
    return descriptor, table


def main(
    spans_path: Path,
    corpus_path: Path,
    out_dir: Path,
    *,
    cells_payload: dict[str, Any] | None = None,
) -> Path:
    """Read a sidecar + the corpus, write ``g8_span_outcomes.json`` into ``out_dir``."""
    spans_bytes = spans_path.read_bytes() if spans_path.is_file() else b""
    rows = read_sidecar(spans_path)
    corpus_bytes = corpus_path.read_bytes() if corpus_path.is_file() else b""
    corpus = load_json(corpus_path)
    site = str(cells_payload.get("site", "unknown")) if cells_payload else "unknown"
    payload = build_span_outcomes(
        rows,
        corpus,
        site=site,
        spans_sha256=sha256_bytes(spans_bytes),
        corpus_sha256=sha256_bytes(corpus_bytes),
        cells_payload=cells_payload,
    )
    out_path = out_dir / SPAN_OUTCOMES_FILENAME
    dump_canonical_json(payload, out_path)
    logger.info(
        "span outcomes (%s): %d rows, %d ground truth, one-token tp %d -> %s",
        site,
        payload["row_counts"]["total"],
        payload["row_counts"]["ground_truth"],
        payload["row_counts"]["one_token_tp"],
        out_path,
    )
    return out_path
