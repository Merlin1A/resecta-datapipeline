"""Document-level Site-B eval: join H1.2 harness hits against draw-time ground truth.

Consumes the ``DocumentHarnessTests`` emissions (one hits JSON per document x
leg x run, schema_version 1) plus ``documents.manifest.json`` (T1.4) and the
per-document ground-truth sidecars, and derives per-document / per-category
precision, recall, F1 and the F2 headline, per leg, with:

- the D22 Option-C match rule the Site-A packet harness established
  (``PacketPRHarnessTests``): region match by coverage >= 0.5 (label-agnostic
  privacy outcome) and by IoU >= 0.5 / >= 0.7 (match quality), DetEval-style
  merge credit for multi-span occurrences, then the CoNLL-strict category
  ratchet; tier mapping must_fire -> recall denominator, must_not_fire ->
  precision denominator, should_fire -> off-headline, watch -> record-only;
- two join rules for the coverage credit, selected per run and named in the
  output: ``union`` (the default) credits the union of the same-category
  detections over one ground-truth box, so a value the product surfaced as
  two adjacent hits (a token-split name) reads as covered; ``single`` credits
  only the best single detection (the earlier rule, kept so the two can be
  reported as a pair on the same hits). IoU stays single-best under both.
  Every verdict row surfaces the covering category, the covering hit's text
  (the ground truth already carries the value, so no new text leaves the run
  directory), the credited fraction and the number of hits credited;
- value-string span rules, strict and relaxed: STRICT is normalized string
  equality between the ground-truth value and the best-covering hit's text;
  RELAXED tolerates up to 2 characters of slack at each end (the i2b2
  +-2-offset rule transposed to string space -- character offsets are not
  comparable across extraction/OCR text spaces, so the tolerance is applied
  at the value-string boundary instead);
- Wilson 95% intervals on every headline proportion, and a document-level
  BCa bootstrap on pooled (micro) F1/F2 where a pool has >= 2 documents;
- macro (mean over categories) beside micro (pooled counts), with categories
  under 30 must-fire occurrences flagged ``low_support``;
- OCR-induced vs detector-induced miss attribution by CLEAN-TWIN re-detection:
  every OCR-leg document here is a derived view of a born-digital master
  with identical occurrence ids, so a missed must-fire is OCR-induced when a
  clean text leg strictly matched that occurrence (the detector fires on the
  clean text) and detector-induced when it missed there too. The twin is the
  document's OWN text leg when it ran one, else the first other document
  whose text leg carries the occurrence id (the packet for packet-derived
  variants, the capture masters for the capture variants); an id no text leg
  carries stays unattributed. Among the OCR-induced misses, those whose value
  survived recognition intact in the raw Vision lines but not in the
  product-normalized lines (read from the harness's ``ocr-lines-run-<n>.json``
  dump for the median run) are split out as ``normalizer_destroyed``; when no
  dump exists for the run that class is reported unavailable, never inferred;
- two DERIVED strata on every verdict, with recall per stratum on every leg:
  the digit-ambiguity stratum (every digit group of the ground-truth value
  made only of the digits 0, 1, 5 and 8 -- the ones the OCR letter-context
  path confuses with O, I, S and B -- versus some, none, or a value with no
  digits) and the ground truth's ``context_class`` (the name-context shape
  the value is drawn in); both are read off the ground truth, no new values;
- the caption-merge flag: for a ground-truth row whose sidecar carries the
  caption drawn above it and the clearance to it in points, whether the raw
  Vision line that carries the value also carries that caption (read from
  the same OCR line dump, median run), bucketed by clearance so "a caption
  merged into the value line" is a measured per-row flag, not an inference.

Determinism: the bootstrap RNG is seeded (CANONICAL_SEED); no wall-clock, no
hash-order dependence (rows are processed in manifest order, runs in filename
order). Library code logs, never prints; the CLI command does the echo.
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import NormalDist
from typing import Any, Final

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json

logger = logging.getLogger(__name__)

EVAL_FILENAME: Final[str] = "documents_eval.json"

JOIN_RULES: Final[tuple[str, ...]] = ("single", "union")
DEFAULT_JOIN_RULE: Final[str] = "union"

_COVER_THRESHOLD: Final[float] = 0.5
_IOU_HEADLINE: Final[float] = 0.5
_IOU_TIGHT: Final[float] = 0.7
_RELAXED_END_SLACK: Final[int] = 2
_LOW_SUPPORT: Final[int] = 30
_BOOTSTRAP_B: Final[int] = 2000
_MIN_BOOTSTRAP_DOCS: Final[int] = 2
_Z95: Final[float] = 1.959963984540054

# The Site-A snapshot vocabulary used "dob"; the Site-B harness emits the
# PIICategory case names, which match the ground-truth vocabulary exactly.
_CATEGORY_ALIASES: Final[dict[str, str]] = {"dob": "dateOfBirth"}


def _canon(category: str) -> str:
    return _CATEGORY_ALIASES.get(category, category)


# ---------------------------------------------------------------------------
# Geometry (Option-C primitives; boxes are normalized, bottom-left origin)
# ---------------------------------------------------------------------------


def _corner_to_xywh(b: Sequence[float]) -> tuple[float, float, float, float]:
    """Ground-truth corner form [x0,y0,x1,y1] -> (x, y, w, h)."""
    return (b[0], b[1], b[2] - b[0], b[3] - b[1])


def _intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    inter = _intersect(a, b)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _cover_frac(
    gt: tuple[float, float, float, float], det: tuple[float, float, float, float]
) -> float:
    area = gt[2] * gt[3]
    return _intersect(gt, det) / area if area > 0 else 0.0


def _union_cover_frac(
    gt: tuple[float, float, float, float],
    dets: Sequence[tuple[float, float, float, float]],
) -> float:
    """Fraction of ``gt`` covered by the UNION of ``dets`` (exact, by coordinate compression)."""
    area = gt[2] * gt[3]
    if area <= 0 or not dets:
        return 0.0
    gx0, gy0, gx1, gy1 = gt[0], gt[1], gt[0] + gt[2], gt[1] + gt[3]
    clipped: list[tuple[float, float, float, float]] = []
    for r in dets:
        x0, y0 = max(gx0, r[0]), max(gy0, r[1])
        x1, y1 = min(gx1, r[0] + r[2]), min(gy1, r[1] + r[3])
        if x1 > x0 and y1 > y0:
            clipped.append((x0, y0, x1, y1))
    if not clipped:
        return 0.0
    xs = sorted({c[0] for c in clipped} | {c[2] for c in clipped})
    ys = sorted({c[1] for c in clipped} | {c[3] for c in clipped})
    covered = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
            if any(c[0] <= cx <= c[2] and c[1] <= cy <= c[3] for c in clipped):
                covered += (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j])
    return min(covered / area, 1.0)


def _norm_text(s: str) -> str:
    return " ".join(s.split()).casefold()


def _end_slack_match(a: str, b: str, k: int = _RELAXED_END_SLACK) -> bool:
    """True when one normalized string equals the other within k chars of slack
    at EACH end (the i2b2 relaxed rule transposed to string space)."""
    na, nb = _norm_text(a), _norm_text(b)
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if not short:
        return False
    idx = long_.find(short)
    while idx != -1:
        if idx <= k and (len(long_) - idx - len(short)) <= k:
            return True
        idx = long_.find(short, idx + 1)
    return False


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def _check_rule(rule: str) -> None:
    if rule not in JOIN_RULES:
        raise PipelineError(f"unknown join rule {rule!r}; expected one of {JOIN_RULES}")


def _best_cover(
    gt: tuple[float, float, float, float], dets: list[dict[str, Any]], rule: str
) -> tuple[float, str | None, str | None, int]:
    """The coverage credit for one box: (fraction, category, text, hits credited).

    ``single`` credits the best single detection (ties keep the earlier
    detection). ``union`` starts from that single credit and lets a category's
    union of detections over the box replace it only when the union is
    STRICTLY larger, so the union rule can add credit but never re-label a
    tie (a full-box detection of another category never displaces the
    single winner it merely equals); categories are visited in hit order.
    The text is the best single hit's of the credited category (the
    value-text rules stay single-hit).
    """
    best_cover, best_cat, best_text = 0.0, None, None
    by_cat: dict[str, list[tuple[tuple[float, float, float, float], float, str | None]]] = {}
    for d in dets:
        r = (d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3])
        c = _cover_frac(gt, r)
        if c > best_cover:
            best_cover, best_cat, best_text = c, _canon(d["category"]), d.get("text")
        if c > 0:
            by_cat.setdefault(_canon(d["category"]), []).append((r, c, d.get("text")))
    single_n = 1 if best_cat is not None else 0
    if rule == "single" or not by_cat:
        return best_cover, best_cat, best_text, single_n
    union_cover, union_cat, union_text, union_n = best_cover, best_cat, best_text, single_n
    for cat, rows in by_cat.items():
        if len(rows) < 2:  # noqa: PLR2004 -- one detection has no union to add
            continue
        u = _union_cover_frac(gt, [r for r, _, _ in rows])
        if u > union_cover:
            top = max(rows, key=lambda t: t[1])
            union_cover, union_cat, union_text, union_n = u, cat, top[2], len(rows)
    return union_cover, union_cat, union_text, union_n


def join_occurrence(
    occ: dict[str, Any], dets: list[dict[str, Any]], rule: str = DEFAULT_JOIN_RULE
) -> dict[str, Any]:
    """Option-C verdict for one occurrence against one page's detections.

    ``dets`` rows carry ``rect`` as [x, y, w, h] and ``category``/``text``.
    Returns covered / iou_matched / iou_tight / cover_category / cover_text /
    cover_fraction / cover_detections under the given join ``rule``.
    """
    _check_rule(rule)
    bbox = occ.get("bbox")
    if not bbox:
        return {
            "covered": False,
            "iou_matched": False,
            "iou_tight": False,
            "cover_category": None,
            "cover_text": None,
            "cover_fraction": 0.0,
            "cover_detections": 0,
        }
    whole = _corner_to_xywh(bbox)
    best_cover, best_cat, best_text, best_n = _best_cover(whole, dets, rule)
    best_iou = 0.0
    for d in dets:
        r = (d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3])
        best_iou = max(best_iou, _iou(whole, r))

    # DetEval merge credit: a multi-span occurrence whose every span is
    # individually covered counts as covered/matched even when the whole-bbox
    # IoU is diluted by inter-span gaps.
    spans = occ.get("spans") or []
    if len(spans) > 1:
        merge = _merge_credit(spans, dets, rule)
        if merge["all_covered"]:
            best_cover = max(best_cover, 1.0)
            best_cat = best_cat or merge["category"]
            best_text = best_text or merge["text"]
        if merge["all_iou"]:
            best_iou = max(best_iou, _IOU_HEADLINE)

    return {
        "covered": best_cover >= _COVER_THRESHOLD,
        "iou_matched": best_iou >= _IOU_HEADLINE,
        "iou_tight": best_iou >= _IOU_TIGHT,
        "cover_category": best_cat,
        "cover_text": best_text,
        "cover_fraction": round(min(best_cover, 1.0), 6),
        "cover_detections": best_n,
    }


def _merge_credit(
    spans: list[dict[str, Any]], dets: list[dict[str, Any]], rule: str = DEFAULT_JOIN_RULE
) -> dict[str, Any]:
    """Per-span coverage/IoU over a multi-span occurrence (DetEval credit)."""
    all_cov, all_iou = True, True
    merge_cat: str | None = None
    merge_text: str | None = None
    for s in spans:
        sb = s.get("bbox")
        if not sb:
            return {"all_covered": False, "all_iou": False, "category": None, "text": None}
        sr = _corner_to_xywh(sb)
        sc, scat, stext, _ = _best_cover(sr, dets, rule)
        sv = 0.0
        for d in dets:
            r = (d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3])
            sv = max(sv, _iou(sr, r))
        if sc >= _COVER_THRESHOLD:
            merge_cat = merge_cat or scat
            merge_text = merge_text or stext
        else:
            all_cov = False
        if sv < _IOU_HEADLINE:
            all_iou = False
    return {"all_covered": all_cov, "all_iou": all_iou, "category": merge_cat, "text": merge_text}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def wilson_ci(k: int, n: int, z: float = _Z95) -> list[float] | None:
    """Wilson score interval for k successes of n; None when n == 0."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, center - half), min(1.0, center + half)]


def _fbeta(precision: float, recall: float, beta: float) -> float:
    b2 = beta * beta
    denom = b2 * precision + recall
    return (1 + b2) * precision * recall / denom if denom > 0 else 0.0


def _micro_f(counts: Sequence[dict[str, int]], beta: float) -> float:
    mf = sum(c["mf_total"] for c in counts)
    mf_strict = sum(c["mf_strict"] for c in counts)
    mnf_fire = sum(c["mnf_fire_strict"] for c in counts)
    recall = mf_strict / mf if mf else 0.0
    precision = mf_strict / (mf_strict + mnf_fire) if (mf_strict + mnf_fire) else 1.0
    return _fbeta(precision, recall, beta)


def bca_bootstrap(
    doc_counts: Sequence[dict[str, int]],
    stat: Callable[[Sequence[dict[str, int]]], float],
    b: int = _BOOTSTRAP_B,
    seed: int = CANONICAL_SEED,
) -> list[float] | None:
    """Document-level BCa bootstrap CI for a pooled statistic; None if < 2 docs."""
    n = len(doc_counts)
    if n < _MIN_BOOTSTRAP_DOCS:
        return None
    obs = stat(doc_counts)
    rng = random.Random(seed)  # noqa: S311 -- deterministic resampling, not security
    boot = sorted(stat([doc_counts[rng.randrange(n)] for _ in range(n)]) for _ in range(b))
    if boot[0] == boot[-1]:
        return [obs, obs]
    # Bias correction: fraction of bootstrap statistics below the observed.
    below = sum(1 for v in boot if v < obs)
    frac = min(max(below / b, 1.0 / (b + 1)), b / (b + 1))
    nd = NormalDist()
    z0 = nd.inv_cdf(frac)
    # Acceleration from the jackknife.
    jack = [stat([c for j, c in enumerate(doc_counts) if j != i]) for i in range(n)]
    jmean = sum(jack) / n
    num = sum((jmean - v) ** 3 for v in jack)
    den = 6.0 * (sum((jmean - v) ** 2 for v in jack) ** 1.5)
    a = num / den if den != 0 else 0.0

    def adjusted(alpha: float) -> float:
        za = nd.inv_cdf(alpha)
        adj = z0 + (z0 + za) / (1 - a * (z0 + za))
        p = nd.cdf(adj)
        idx = min(max(int(p * b), 0), b - 1)
        return boot[idx]

    return [adjusted(0.025), adjusted(0.975)]


# ---------------------------------------------------------------------------
# Derived strata (read off the ground truth; no new values)
# ---------------------------------------------------------------------------

_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]+")
_AMBIGUOUS_DIGITS: Final[frozenset[str]] = frozenset("0158")
DIGIT_STRATA: Final[tuple[str, ...]] = (
    "all_ambiguous",
    "some_ambiguous",
    "none_ambiguous",
    "no_digits",
)
_STRATA_KINDS: Final[tuple[str, ...]] = ("digit_ambiguity", "context_class")


def digit_stratum(value: str) -> tuple[str, int, int]:
    """(stratum, ambiguous digit groups, digit groups) of a ground-truth value.

    A digit group is ambiguous when every digit is one of 0, 1, 5, 8 -- the
    digits an OCR letter-context pass reads as O, I, S, B. ``all_ambiguous``
    = every group ambiguous (an SSN like 555-01-1580), ``some_ambiguous`` =
    at least one, ``none_ambiguous`` = digit groups but none ambiguous,
    ``no_digits`` = no digit group at all (a name, an email).
    """
    groups = _DIGITS_RE.findall(value)
    if not groups:
        return "no_digits", 0, 0
    k = sum(1 for g in groups if set(g) <= _AMBIGUOUS_DIGITS)
    if k == len(groups):
        return "all_ambiguous", k, len(groups)
    if k:
        return "some_ambiguous", k, len(groups)
    return "none_ambiguous", 0, len(groups)


def _stratum_view(t: dict[str, int]) -> dict[str, Any]:
    return {
        "support": t["mf_total"],
        "region_recall": round(t["mf_region"] / t["mf_total"], 6) if t["mf_total"] else 0.0,
        "strict_recall": round(t["mf_strict"] / t["mf_total"], 6) if t["mf_total"] else 0.0,
        "strict_recall_ci95": wilson_ci(t["mf_strict"], t["mf_total"]),
    }


def _strata_views(counts: dict[str, dict[str, dict[str, int]]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind in _STRATA_KINDS:
        rows = counts.get(kind, {})
        keys: tuple[str, ...] | list[str] = (
            DIGIT_STRATA if kind == "digit_ambiguity" else sorted(rows)
        )
        out[kind] = {k: _stratum_view(rows.get(k, _zero_counts())) for k in keys}
    return out


def _zero_counts() -> dict[str, int]:
    return {"mf_total": 0, "mf_region": 0, "mf_strict": 0}


def _add_counts(
    into: dict[str, dict[str, dict[str, int]]], add: dict[str, dict[str, dict[str, int]]]
) -> None:
    for kind, rows in add.items():
        for key, t in rows.items():
            dst = into.setdefault(kind, {}).setdefault(key, _zero_counts())
            for field in dst:
                dst[field] += t[field]


# ---------------------------------------------------------------------------
# Per-run evaluation
# ---------------------------------------------------------------------------


def _gt_leg_for(occ: dict[str, Any], run: dict[str, Any]) -> str | None:
    """Which measurement leg this occurrence belongs to under this run.

    ``ocr-forced`` runs measure the OCR leg on every page. ``natural`` runs
    measure the text leg on ``rich`` pages and the OCR leg elsewhere (the
    product routing). Returns None when the occurrence's ground-truth
    ``leg_applicability`` excludes the leg the run measured on its page.
    """
    page = occ.get("page")
    if page is None:
        return None
    if run["leg"] == "ocr-forced":
        leg = "ocr"
    else:
        statuses = run.get("text_layer_status") or []
        status = statuses[page] if 0 <= page < len(statuses) else "rich"
        leg = "text" if status == "rich" else "ocr"
    return leg if leg in occ.get("leg_applicability", []) else None


def evaluate_run(
    occs: list[dict[str, Any]],
    run: dict[str, Any],
    carried: list[dict[str, Any]] | None = None,
    rule: str = DEFAULT_JOIN_RULE,
) -> dict[str, Any]:
    """Join one harness run against the occurrence list -> the metric block.

    ``carried`` rows (the packet's carried_stmt block) join no denominator --
    they are count-declared, not per-instance-drawn -- but their boxes DO
    count as ground truth for the surplus-fire census, so legitimate hits on
    the carried statement pages are not misread as surplus. ``rule`` selects
    the coverage credit (``union`` / ``single``).
    """
    _check_rule(rule)
    hits_by_page: dict[int, list[dict[str, Any]]] = {}
    for h in run["hits"]:
        hits_by_page.setdefault(h["page"], []).append(h)

    per_cat: dict[str, dict[str, int]] = {}
    totals = {
        "mf_total": 0,
        "mf_region": 0,
        "mf_strict": 0,
        "mnf_total": 0,
        "mnf_fire_region": 0,
        "mnf_fire_strict": 0,
        "sf_total": 0,
        "sf_region": 0,
        "iou_denom": 0,
        "iou_head": 0,
        "iou_tight": 0,
        "text_strict": 0,
        "text_relaxed": 0,
        "text_denom": 0,
    }
    confusion: dict[str, int] = {}
    verdicts: dict[str, dict[str, Any]] = {}
    strata_counts: dict[str, dict[str, dict[str, int]]] = {}

    for occ in occs:
        leg = _gt_leg_for(occ, run)
        if leg is None:
            continue
        page = occ["page"]
        v = join_occurrence(occ, hits_by_page.get(page, []), rule)
        want = _canon(occ["category"])
        strict = bool(v["covered"] and v["cover_category"] == want)
        if v["covered"] and not strict and v["cover_category"]:
            confusion[f"{want}->{v['cover_category']}"] = (
                confusion.get(f"{want}->{v['cover_category']}", 0) + 1
            )
        cat = per_cat.setdefault(want, dict.fromkeys(totals, 0))
        _tally_occurrence(occ, v, strict, (totals, cat))
        stratum, ambiguous, digit_groups = digit_stratum(str(occ.get("value", "")))
        context_class = str(occ.get("context_class") or "none")
        if occ["expectation"] == "must_fire":
            for kind, key in (("digit_ambiguity", stratum), ("context_class", context_class)):
                t = strata_counts.setdefault(kind, {}).setdefault(key, _zero_counts())
                t["mf_total"] += 1
                t["mf_region"] += 1 if v["covered"] else 0
                t["mf_strict"] += 1 if strict else 0
        verdicts[occ["id"]] = {
            "leg": leg,
            "expectation": occ["expectation"],
            "covered": v["covered"],
            "strict": strict,
            "iou_matched": v["iou_matched"],
            "cover_category": v["cover_category"],
            "cover_text": v["cover_text"],
            "cover_fraction": v["cover_fraction"],
            "cover_detections": v["cover_detections"],
            "digit_stratum": stratum,
            "ambiguous_token_count": ambiguous,
            "digit_token_count": digit_groups,
            "context_class": context_class,
        }

    surplus_by_page = _surplus_fires(occs + (carried or []), hits_by_page)
    surplus = sum(surplus_by_page.values())
    pages = max(run.get("page_count", 0), 1)

    return {
        "run_index": run["run_index"],
        "leg": run["leg"],
        "counts": totals,
        "per_category": {c: _metric_view(t) for c, t in sorted(per_cat.items())},
        "headline": _metric_view(totals),
        "confusion": confusion,
        "surplus_fires": surplus,
        "surplus_fires_per_page": round(surplus / pages, 6),
        "surplus_fires_by_page": {str(k): v for k, v in sorted(surplus_by_page.items())},
        "strata": _strata_views(strata_counts),
        "strata_counts": strata_counts,
        "verdicts": verdicts,
        "diagnostics": run.get("diagnostics", {}),
    }


def _tally_occurrence(
    occ: dict[str, Any],
    v: dict[str, Any],
    strict: bool,
    tallies: tuple[dict[str, int], ...],
) -> None:
    """Tier -> denominator mapping (Option C) for one joined occurrence."""
    expectation = occ["expectation"]
    if expectation == "must_fire":
        for t in tallies:
            t["mf_total"] += 1
            t["mf_region"] += 1 if v["covered"] else 0
            t["mf_strict"] += 1 if strict else 0
            t["iou_denom"] += 1
            t["iou_head"] += 1 if v["iou_matched"] else 0
            t["iou_tight"] += 1 if v["iou_tight"] else 0
        if strict and v["cover_text"] is not None:
            exact = _norm_text(v["cover_text"]) == _norm_text(occ.get("value", ""))
            relaxed = _end_slack_match(v["cover_text"], occ.get("value", ""))
            for t in tallies:
                t["text_denom"] += 1
                t["text_strict"] += 1 if exact else 0
                t["text_relaxed"] += 1 if relaxed else 0
    elif expectation == "must_not_fire":
        for t in tallies:
            t["mnf_total"] += 1
            t["mnf_fire_region"] += 1 if v["covered"] else 0
            t["mnf_fire_strict"] += 1 if strict else 0
    elif expectation == "should_fire":
        for t in tallies:
            t["sf_total"] += 1
            t["sf_region"] += 1 if v["covered"] else 0


def _surplus_fires(
    occs: list[dict[str, Any]], hits_by_page: dict[int, list[dict[str, Any]]]
) -> dict[int, int]:
    """Detections covering no ground-truth box (any tier) on their page.

    Reported per page, off-headline -- the Option-C precision denominator
    stays must_not_fire-constructed.
    """
    gt_boxes_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for occ in occs:
        if occ.get("bbox") and occ.get("page") is not None:
            gt_boxes_by_page.setdefault(occ["page"], []).append(_corner_to_xywh(occ["bbox"]))
            for s in occ.get("spans") or []:
                if s.get("bbox"):
                    gt_boxes_by_page.setdefault(occ["page"], []).append(_corner_to_xywh(s["bbox"]))
    surplus: dict[int, int] = {}
    for page, dets in hits_by_page.items():
        for d in dets:
            r = (d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3])
            if not any(
                _cover_frac(g, r) >= _COVER_THRESHOLD or _cover_frac(r, g) >= _COVER_THRESHOLD
                for g in gt_boxes_by_page.get(page, [])
            ):
                surplus[page] = surplus.get(page, 0) + 1
    return surplus


def _metric_view(t: dict[str, int]) -> dict[str, Any]:
    region_recall = t["mf_region"] / t["mf_total"] if t["mf_total"] else 0.0
    strict_recall = t["mf_strict"] / t["mf_total"] if t["mf_total"] else 0.0
    rp_den = t["mf_region"] + t["mnf_fire_region"]
    sp_den = t["mf_strict"] + t["mnf_fire_strict"]
    region_precision = t["mf_region"] / rp_den if rp_den else 1.0
    strict_precision = t["mf_strict"] / sp_den if sp_den else 1.0
    view: dict[str, Any] = {
        "support": t["mf_total"],
        "low_support": t["mf_total"] < _LOW_SUPPORT,
        "region": {
            "recall": round(region_recall, 6),
            "precision": round(region_precision, 6),
            "f1": round(_fbeta(region_precision, region_recall, 1.0), 6),
            "f2": round(_fbeta(region_precision, region_recall, 2.0), 6),
            "recall_ci95": wilson_ci(t["mf_region"], t["mf_total"]),
        },
        "strict": {
            "recall": round(strict_recall, 6),
            "precision": round(strict_precision, 6),
            "f1": round(_fbeta(strict_precision, strict_recall, 1.0), 6),
            "f2": round(_fbeta(strict_precision, strict_recall, 2.0), 6),
            "recall_ci95": wilson_ci(t["mf_strict"], t["mf_total"]),
        },
        "must_not_fire": {
            "total": t["mnf_total"],
            "fired_as_category": t["mnf_fire_strict"],
            "region_covered": t["mnf_fire_region"],
        },
        "should_fire": {"total": t["sf_total"], "region_covered": t["sf_region"]},
        "iou": {
            "headline_rate": round(t["iou_head"] / t["iou_denom"], 6) if t["iou_denom"] else 0.0,
            "tight_rate": round(t["iou_tight"] / t["iou_denom"], 6) if t["iou_denom"] else 0.0,
        },
    }
    if t["text_denom"]:
        view["value_text"] = {
            "denom": t["text_denom"],
            "strict_rate": round(t["text_strict"] / t["text_denom"], 6),
            "relaxed_rate": round(t["text_relaxed"] / t["text_denom"], 6),
        }
    return view


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _median_run(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """The median run by strict headline recall (ties by run_index)."""
    ordered = sorted(blocks, key=lambda r: (r["headline"]["strict"]["recall"], r["run_index"]))
    return ordered[len(ordered) // 2]


def _leg_kind(run: dict[str, Any]) -> str:
    if run["leg"] == "ocr-forced":
        return "ocr-forced"
    statuses = set(run.get("text_layer_status") or [])
    if statuses == {"rich"}:
        return "text"
    if "rich" not in statuses:
        return "ocr"
    return "mixed"


def evaluate(
    manifest_path: Path, gt_root: Path, hits_dir: Path, rule: str = DEFAULT_JOIN_RULE
) -> dict[str, Any]:
    """Full document eval over every manifest row with hits present."""
    _check_rule(rule)
    manifest = load_json(manifest_path)
    per_document: dict[str, Any] = {}
    doc_leg_counts: dict[str, list[tuple[str, dict[str, int]]]] = {}
    doc_leg_strata: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    values_by_doc: dict[str, dict[str, tuple[str, int | None]]] = {}
    occ_index: dict[str, dict[str, dict[str, Any]]] = {}
    site: str | None = None

    for row in manifest:
        gt_rel = row.get("gt")
        if not gt_rel or not str(row.get("path", "")).endswith(".pdf"):
            continue
        doc_dir = hits_dir / row["id"]
        run_files = sorted(doc_dir.glob("*.json")) if doc_dir.is_dir() else []
        if not run_files:
            logger.info("no hits for %s; skipped", row["id"])
            continue
        gt = load_json(gt_root / gt_rel)
        occs = gt["occurrences"]
        carried = gt.get("carried_stmt") or []
        values_by_doc[row["id"]] = {o["id"]: (str(o.get("value", "")), o.get("page")) for o in occs}
        occ_index[row["id"]] = {
            o["id"]: {
                "value": str(o.get("value", "")),
                "page": o.get("page"),
                "caption_clearance_pt": o.get("caption_clearance_pt"),
                "caption_text": o.get("caption_text"),
                "multiline": bool((o.get("render") or {}).get("multiline")),
            }
            for o in occs
        }
        legs: dict[str, list[dict[str, Any]]] = {}
        for rf in run_files:
            if rf.name.startswith("ocr-lines-run-"):
                continue  # the OCR line dump sits beside the hits; not a run
            run = load_json(rf)
            if site is None:
                site = run.get("site")
            elif run.get("site") != site:
                raise PipelineError(f"site drift in {rf}")
            block = evaluate_run(occs, run, carried, rule)
            legs.setdefault(_leg_kind(run), []).append(block)

        doc_block: dict[str, Any] = {"variant": (gt.get("variant") or {}).get("kind")}
        for kind, blocks in sorted(legs.items()):
            med = _median_run(blocks)
            recalls = sorted(b["headline"]["strict"]["recall"] for b in blocks)
            region_recalls = sorted(b["headline"]["region"]["recall"] for b in blocks)
            doc_block[kind] = {
                "n_runs": len(blocks),
                "median_run_index": med["run_index"],
                "median": {
                    k: med[k]
                    for k in (
                        "headline",
                        "per_category",
                        "confusion",
                        "surplus_fires",
                        "surplus_fires_per_page",
                        "surplus_fires_by_page",
                        "strata",
                        "verdicts",
                    )
                },
                "strict_recall_min_median_max": [
                    recalls[0],
                    recalls[len(recalls) // 2],
                    recalls[-1],
                ],
                "region_recall_min_median_max": [
                    region_recalls[0],
                    region_recalls[len(region_recalls) // 2],
                    region_recalls[-1],
                ],
            }
            doc_leg_counts.setdefault(kind, []).append((row["id"], med["counts"]))
            _add_counts(doc_leg_strata.setdefault(kind, {}), med["strata_counts"])
        per_document[row["id"]] = doc_block

    # Pools: micro + macro + document-level BCa where n_docs >= 2.
    pools: dict[str, Any] = {}
    for kind, entries in sorted(doc_leg_counts.items()):
        counts = [c for _, c in entries]
        micro_f1 = _micro_f(counts, 1.0)
        micro_f2 = _micro_f(counts, 2.0)
        pools[kind] = {
            "documents": [d for d, _ in entries],
            "micro_f1": round(micro_f1, 6),
            "micro_f2": round(micro_f2, 6),
            "micro_f1_bca_ci95": bca_bootstrap(counts, lambda c: _micro_f(c, 1.0)),
            "micro_f2_bca_ci95": bca_bootstrap(counts, lambda c: _micro_f(c, 2.0)),
            "strata": _strata_views(doc_leg_strata.get(kind, {})),
        }

    attribution = _attribute_misses(per_document, values_by_doc, hits_dir)
    caption_merge = _caption_merge(per_document, occ_index, hits_dir)

    credit = (
        "union of same-category detections over the box"
        if rule == "union"
        else "best single detection"
    )
    return {
        "schema_version": 1,
        "site": site or "unknown",
        "generated_by": "resecta_data.eval.documents",
        "match_rule": {
            "join_rule": rule,
            "region": f"coverage >= {_COVER_THRESHOLD} by the {credit} "
            "(DetEval merge credit on multi-span)",
            "iou": f"headline >= {_IOU_HEADLINE}, tight >= {_IOU_TIGHT} (best single detection)",
            "strict": "region AND category (CoNLL ratchet)",
            "value_text": f"strict = normalized equality; relaxed = +-{_RELAXED_END_SLACK} "
            "chars end slack (i2b2 rule in string space)",
            "precision_denominator": "must_not_fire construction (Option C); surplus fires "
            "reported off-headline",
        },
        "per_document": per_document,
        "pools": pools,
        "miss_attribution": attribution,
        "caption_merge": caption_merge,
    }


def _load_ocr_lines(path: Path) -> dict[int, list[tuple[str, str]]] | None:
    """The harness's OCR line dump as {page: [(raw, normalized), ...]}; None when absent."""
    if not path.is_file():
        return None
    dump = load_json(path)
    return {
        int(p["page"]): [(str(line["text"]), str(line["normalized"])) for line in p["lines"]]
        for p in dump.get("pages", [])
    }


def _digits_pattern(value: str) -> re.Pattern[str] | None:
    """A pattern matching the value's digit groups in order, tolerant of separators."""
    groups = _DIGITS_RE.findall(value)
    if not groups:
        return None
    return re.compile(r"[^0-9]{0,3}".join(re.escape(g) for g in groups))


def _normalizer_destroyed(
    value: str, page: int | None, lines: dict[int, list[tuple[str, str]]]
) -> bool:
    """True when the value's digits survived recognition on the page (present intact
    in a raw Vision line) but not the product normalizer (absent from that line's
    normalized form)."""
    pattern = _digits_pattern(value)
    if pattern is None or page is None:
        return False
    return any(
        pattern.search(raw) is not None and pattern.search(normalized) is None
        for raw, normalized in lines.get(page, [])
    )


def _text_twins(per_document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every document's text-leg median verdicts, keyed by document id."""
    twins: dict[str, dict[str, Any]] = {}
    for doc_id, doc_block in sorted(per_document.items()):
        verdicts = (doc_block.get("text") or {}).get("median", {}).get("verdicts", {})
        if verdicts:
            twins[doc_id] = verdicts
    return twins


def _twin_for(
    occ_id: str, doc_id: str, twins: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    """The clean twin verdict for one occurrence: the document's own text leg
    first, else the first other text leg (by document id) carrying the id."""
    own = twins.get(doc_id)
    if own is not None and occ_id in own:
        return f"{doc_id}/text/median", own[occ_id]
    for twin_doc in sorted(twins):
        if twin_doc != doc_id and occ_id in twins[twin_doc]:
            return f"{twin_doc}/text/median", twins[twin_doc][occ_id]
    return None


def _attribute_leg(
    doc_id: str,
    verdicts: dict[str, Any],
    twins: dict[str, dict[str, Any]],
    values: dict[str, tuple[str, int | None]],
    lines: dict[int, list[tuple[str, str]]] | None,
) -> dict[str, Any]:
    """Attribute one OCR leg's strict must-fire misses."""
    classes: dict[str, list[str]] = {
        "ocr_induced": [],
        "normalizer_destroyed": [],
        "detector_induced": [],
        "unattributed": [],
    }
    twins_used: dict[str, int] = {}
    for occ_id, v in verdicts.items():
        if v.get("expectation") != "must_fire" or v["strict"]:
            continue
        twin = _twin_for(occ_id, doc_id, twins)
        if twin is None:
            classes["unattributed"].append(occ_id)
            continue
        twin_name, t = twin
        twins_used[twin_name] = twins_used.get(twin_name, 0) + 1
        if not t["strict"]:
            classes["detector_induced"].append(occ_id)
        elif lines is not None and _normalizer_destroyed(*values.get(occ_id, ("", None)), lines):
            classes["normalizer_destroyed"].append(occ_id)
        else:
            classes["ocr_induced"].append(occ_id)
    block: dict[str, Any] = {
        "clean_twins": {k: twins_used[k] for k in sorted(twins_used)},
        "ocr_induced": sorted(classes["ocr_induced"]),
        "normalizer_destroyed": (
            sorted(classes["normalizer_destroyed"]) if lines is not None else None
        ),
        "detector_induced": sorted(classes["detector_induced"]),
        "unattributed": sorted(classes["unattributed"]),
        "ocr_induced_count": len(classes["ocr_induced"]),
        "normalizer_destroyed_count": (
            len(classes["normalizer_destroyed"]) if lines is not None else None
        ),
        "detector_induced_count": len(classes["detector_induced"]),
        "unattributed_count": len(classes["unattributed"]),
        "strict_miss_total": sum(len(ids) for ids in classes.values()),
        "normalizer_check": "ocr-lines dump" if lines is not None else "unavailable",
    }
    return block


def _attribute_misses(
    per_document: dict[str, Any],
    values_by_doc: dict[str, dict[str, tuple[str, int | None]]],
    hits_dir: Path,
) -> dict[str, Any]:
    """Clean-twin attribution of every OCR leg's strict must-fire misses.

    The twin of a miss is the document's own text-leg median run when it ran
    one, else the first other document whose text leg carries the occurrence
    id; ``normalizer_destroyed`` is split out of the OCR-induced class from
    the median run's ``ocr-lines-run-<n>.json`` dump when it exists (else
    reported as unavailable). The four classes partition the strict misses.
    """
    twins = _text_twins(per_document)
    out: dict[str, Any] = {
        "clean_twin_rule": "the document's own text-leg median run when present, else the "
        "first other document (by id) whose text leg carries the occurrence id",
        "text_legs_present": sorted(twins),
        "classes": ["ocr_induced", "normalizer_destroyed", "detector_induced", "unattributed"],
        "documents": {},
    }
    if not twins:
        out["note"] = "no text-leg run present; every miss stays unattributed"
    for doc_id, doc_block in sorted(per_document.items()):
        for kind in ("ocr", "ocr-forced"):
            leg = doc_block.get(kind)
            if not leg:
                continue
            dump = hits_dir / doc_id / f"ocr-lines-run-{leg['median_run_index']}.json"
            block = _attribute_leg(
                doc_id,
                leg["median"]["verdicts"],
                twins,
                values_by_doc.get(doc_id, {}),
                _load_ocr_lines(dump),
            )
            if block["strict_miss_total"]:
                out["documents"].setdefault(doc_id, {})[kind] = block
    return out


# ---------------------------------------------------------------------------
# The caption-merge flag (the clearance column of the ground truth x the OCR line dump)
# ---------------------------------------------------------------------------

CLEARANCE_BUCKETS: Final[tuple[tuple[str, float | None, float | None], ...]] = (
    ("overprint", None, 0.0),
    ("under_3pt", 0.0, 3.0),
    ("3_to_12pt", 3.0, 12.0),
    ("over_12pt", 12.0, None),
)


def clearance_bucket(clearance_pt: float) -> str:
    """The clearance bucket of one row: overprint (< 0), [0, 3), [3, 12), >= 12 points."""
    for name, lo, hi in CLEARANCE_BUCKETS:
        if (lo is None or clearance_pt >= lo) and (hi is None or clearance_pt < hi):
            return name
    raise PipelineError(f"clearance {clearance_pt} falls in no bucket")  # pragma: no cover


def _value_lines(
    value: str,
    page: int | None,
    lines: dict[int, list[tuple[str, str]]],
    *,
    multiline: bool = False,
) -> list[str]:
    """The raw Vision lines on ``page`` that carry ``value``: its digit groups in order for a
    digit value, else its normalized text as a substring. A value drawn over several lines
    (``multiline``) never sits in one Vision line, so its FIRST line is what can carry the
    caption: the first two words (a house number and street, a name) stand in for it when the
    whole value is not found."""
    if page is None:
        return []
    pattern = _digits_pattern(value)
    needle = _norm_text(value)
    out = []
    for raw, _normalized in lines.get(page, []):
        if pattern is not None:
            if pattern.search(raw) is not None:
                out.append(raw)
        elif needle and needle in _norm_text(raw):
            out.append(raw)
    if out or not multiline:
        return out
    head = " ".join(needle.split()[:2])  # an address's house number + street, a name's two words
    return [raw for raw, _normalized in lines.get(page, []) if head and head in _norm_text(raw)]


def caption_merged(
    value: str,
    caption: str,
    page: int | None,
    lines: dict[int, list[tuple[str, str]]],
    *,
    multiline: bool = False,
) -> bool | None:
    """True when a raw line carrying the value also carries the caption text (its normalized
    form as a substring, or -- for a long caption -- its first three words), False when the
    value's line exists without it, None when no line on the page carries the value (nothing
    to decide: the value was not recognized as a line at all)."""
    carriers = _value_lines(value, page, lines, multiline=multiline)
    if not carriers:
        return None
    cap = _norm_text(caption)
    head = " ".join(cap.split()[:3])
    return any(cap in _norm_text(raw) or (head and head in _norm_text(raw)) for raw in carriers)


def _merge_leg(
    verdicts: dict[str, Any],
    index: dict[str, dict[str, Any]],
    lines: dict[int, list[tuple[str, str]]] | None,
) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    buckets: dict[str, dict[str, int]] = {
        name: {"rows": 0, "decided": 0, "merged": 0} for name, _, _ in CLEARANCE_BUCKETS
    }
    for occ_id, v in verdicts.items():
        meta = index.get(occ_id)
        if not meta or meta.get("caption_clearance_pt") is None or not meta.get("caption_text"):
            continue
        clearance = float(meta["caption_clearance_pt"])
        bucket = clearance_bucket(clearance)
        merged = (
            caption_merged(
                meta["value"],
                str(meta["caption_text"]),
                meta.get("page"),
                lines,
                multiline=bool(meta.get("multiline")),
            )
            if lines is not None
            else None
        )
        rows[occ_id] = {
            "clearance_pt": clearance,
            "bucket": bucket,
            "merged": merged,
            "strict": bool(v["strict"]),
        }
        buckets[bucket]["rows"] += 1
        if merged is not None:
            buckets[bucket]["decided"] += 1
            buckets[bucket]["merged"] += 1 if merged else 0
    return {
        "rows": rows,
        "buckets": {
            name: {
                **b,
                "merged_rate": round(b["merged"] / b["decided"], 6) if b["decided"] else None,
            }
            for name, b in buckets.items()
        },
        "line_check": "ocr-lines dump" if lines is not None else "unavailable",
    }


def _caption_merge(
    per_document: dict[str, Any],
    occ_index: dict[str, dict[str, dict[str, Any]]],
    hits_dir: Path,
) -> dict[str, Any]:
    """Per document, per OCR leg kind (median run): the caption-merge flag on every ground-truth
    row that carries a caption above it, bucketed by clearance; pooled per leg kind."""
    out: dict[str, Any] = {
        "rule": "a row is MERGED when the raw Vision line carrying its value also carries the "
        "caption the ground truth records above it (clearance measured from the draw geometry; "
        "negative = overprint); rows the dump never carries as a line are undecided",
        "buckets": [name for name, _, _ in CLEARANCE_BUCKETS],
        "documents": {},
        "pools": {},
    }
    pooled: dict[str, dict[str, dict[str, int]]] = {}
    for doc_id, doc_block in sorted(per_document.items()):
        for kind in ("ocr", "ocr-forced"):
            leg = doc_block.get(kind)
            if not leg:
                continue
            dump = hits_dir / doc_id / f"ocr-lines-run-{leg['median_run_index']}.json"
            block = _merge_leg(
                leg["median"]["verdicts"], occ_index.get(doc_id, {}), _load_ocr_lines(dump)
            )
            if not block["rows"]:
                continue
            out["documents"].setdefault(doc_id, {})[kind] = block
            pool = pooled.setdefault(
                kind, {n: {"rows": 0, "decided": 0, "merged": 0} for n, _, _ in CLEARANCE_BUCKETS}
            )
            for name, b in block["buckets"].items():
                for field in ("rows", "decided", "merged"):
                    pool[name][field] += b[field]
    for kind, buckets in sorted(pooled.items()):
        out["pools"][kind] = {
            name: {
                **b,
                "merged_rate": round(b["merged"] / b["decided"], 6) if b["decided"] else None,
            }
            for name, b in buckets.items()
        }
    return out


def main(
    manifest_path: Path,
    gt_root: Path,
    hits_dir: Path,
    out_dir: Path,
    rule: str = DEFAULT_JOIN_RULE,
) -> dict[str, Path]:
    """Build ``documents_eval.json`` from the harness emissions into ``out_dir``."""
    payload = evaluate(manifest_path, gt_root, hits_dir, rule)
    out_path = out_dir / EVAL_FILENAME
    dump_canonical_json(payload, out_path)
    logger.info("wrote %s (%d documents)", out_path, len(payload["per_document"]))
    return {"eval": out_path}
