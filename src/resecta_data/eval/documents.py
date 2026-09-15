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
  every OCR-leg document here is a derived view of the born-digital packet
  with identical occurrence ids, so a missed must-fire is OCR-induced when the
  packet's own text leg strictly matched that occurrence (the detector fires
  on the clean text) and detector-induced when it missed there too.

Determinism: the bootstrap RNG is seeded (CANONICAL_SEED); no wall-clock, no
hash-order dependence (rows are processed in manifest order, runs in filename
order). Library code logs, never prints; the CLI command does the echo.
"""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import NormalDist
from typing import Any, Final

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, load_json

logger = logging.getLogger(__name__)

EVAL_FILENAME: Final[str] = "documents_eval.json"

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


def join_occurrence(occ: dict[str, Any], dets: list[dict[str, Any]]) -> dict[str, Any]:
    """Option-C verdict for one occurrence against one page's detections.

    ``dets`` rows carry ``rect`` as [x, y, w, h] and ``category``/``text``.
    Returns covered / iou_matched / iou_tight / cover_category / cover_text.
    """
    bbox = occ.get("bbox")
    if not bbox:
        return {
            "covered": False,
            "iou_matched": False,
            "iou_tight": False,
            "cover_category": None,
            "cover_text": None,
        }
    whole = _corner_to_xywh(bbox)
    best_cover, best_cat, best_text, best_iou = 0.0, None, None, 0.0
    for d in dets:
        r = (d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3])
        c = _cover_frac(whole, r)
        if c > best_cover:
            best_cover, best_cat, best_text = c, _canon(d["category"]), d.get("text")
        v = _iou(whole, r)
        best_iou = max(best_iou, v)

    # DetEval merge credit: a multi-span occurrence whose every span is
    # individually covered counts as covered/matched even when the whole-bbox
    # IoU is diluted by inter-span gaps.
    spans = occ.get("spans") or []
    if len(spans) > 1:
        merge = _merge_credit(spans, dets)
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
    }


def _merge_credit(spans: list[dict[str, Any]], dets: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-span coverage/IoU over a multi-span occurrence (DetEval credit)."""
    all_cov, all_iou = True, True
    merge_cat: str | None = None
    merge_text: str | None = None
    for s in spans:
        sb = s.get("bbox")
        if not sb:
            return {"all_covered": False, "all_iou": False, "category": None, "text": None}
        sr = _corner_to_xywh(sb)
        sc, scat, stext, sv = 0.0, None, None, 0.0
        for d in dets:
            r = (d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3])
            c = _cover_frac(sr, r)
            if c > sc:
                sc, scat, stext = c, _canon(d["category"]), d.get("text")
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
) -> dict[str, Any]:
    """Join one harness run against the occurrence list -> the metric block.

    ``carried`` rows (the packet's carried_stmt block) join no denominator --
    they are count-declared, not per-instance-drawn -- but their boxes DO
    count as ground truth for the surplus-fire census, so legitimate hits on
    the carried statement pages are not misread as surplus.
    """
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

    for occ in occs:
        leg = _gt_leg_for(occ, run)
        if leg is None:
            continue
        page = occ["page"]
        v = join_occurrence(occ, hits_by_page.get(page, []))
        want = _canon(occ["category"])
        strict = bool(v["covered"] and v["cover_category"] == want)
        if v["covered"] and not strict and v["cover_category"]:
            confusion[f"{want}->{v['cover_category']}"] = (
                confusion.get(f"{want}->{v['cover_category']}", 0) + 1
            )
        cat = per_cat.setdefault(want, dict.fromkeys(totals, 0))
        _tally_occurrence(occ, v, strict, (totals, cat))
        verdicts[occ["id"]] = {
            "leg": leg,
            "expectation": occ["expectation"],
            "covered": v["covered"],
            "strict": strict,
            "iou_matched": v["iou_matched"],
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


def evaluate(manifest_path: Path, gt_root: Path, hits_dir: Path) -> dict[str, Any]:
    """Full document eval over every manifest row with hits present."""
    manifest = load_json(manifest_path)
    per_document: dict[str, Any] = {}
    doc_leg_counts: dict[str, list[tuple[str, dict[str, int]]]] = {}
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
        legs: dict[str, list[dict[str, Any]]] = {}
        for rf in run_files:
            run = load_json(rf)
            if site is None:
                site = run.get("site")
            elif run.get("site") != site:
                raise PipelineError(f"site drift in {rf}")
            block = evaluate_run(occs, run, carried)
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
        }

    attribution = _attribute_misses(per_document)

    return {
        "schema_version": 1,
        "site": site or "unknown",
        "generated_by": "resecta_data.eval.documents",
        "match_rule": {
            "region": f"coverage >= {_COVER_THRESHOLD} (DetEval merge credit on multi-span)",
            "iou": f"headline >= {_IOU_HEADLINE}, tight >= {_IOU_TIGHT}",
            "strict": "region AND category (CoNLL ratchet)",
            "value_text": f"strict = normalized equality; relaxed = +-{_RELAXED_END_SLACK} "
            "chars end slack (i2b2 rule in string space)",
            "precision_denominator": "must_not_fire construction (Option C); surplus fires "
            "reported off-headline",
        },
        "per_document": per_document,
        "pools": pools,
        "miss_attribution": attribution,
    }


def _attribute_misses(per_document: dict[str, Any]) -> dict[str, Any]:
    """Clean-twin attribution of OCR-leg misses (packet text leg = clean text)."""
    packet = per_document.get("packet", {})
    twin = (packet.get("text") or {}).get("median", {}).get("verdicts", {})
    if not twin:
        return {"note": "no packet text-leg run present; attribution skipped"}
    out: dict[str, Any] = {"clean_twin": "packet/text/median"}
    for doc_id, doc_block in sorted(per_document.items()):
        for kind in ("ocr", "ocr-forced"):
            leg = doc_block.get(kind)
            if not leg:
                continue
            verdicts = leg["median"]["verdicts"]
            ocr_induced: list[str] = []
            detector_induced: list[str] = []
            unattributed: list[str] = []
            for occ_id, v in verdicts.items():
                if v.get("expectation") != "must_fire" or v["strict"]:
                    continue
                t = twin.get(occ_id)
                if t is None:
                    unattributed.append(occ_id)
                elif t["strict"]:
                    ocr_induced.append(occ_id)
                else:
                    detector_induced.append(occ_id)
            if ocr_induced or detector_induced or unattributed:
                out.setdefault(doc_id, {})[kind] = {
                    "ocr_induced": sorted(ocr_induced),
                    "detector_induced": sorted(detector_induced),
                    "unattributed": sorted(unattributed),
                    "ocr_induced_count": len(ocr_induced),
                    "detector_induced_count": len(detector_induced),
                }
    return out


def main(manifest_path: Path, gt_root: Path, hits_dir: Path, out_dir: Path) -> dict[str, Path]:
    """Build ``documents_eval.json`` from the harness emissions into ``out_dir``."""
    payload = evaluate(manifest_path, gt_root, hits_dir)
    out_path = out_dir / EVAL_FILENAME
    dump_canonical_json(payload, out_path)
    logger.info("wrote %s (%d documents)", out_path, len(payload["per_document"]))
    return {"eval": out_path}
