"""The G8 site gap: Site B (the product's search gate) minus the detector site.

The two G8 emitters score the same 1,100 documents through two surfacing
gates -- ``G8BaselineHarnessTests`` (``site: "detector"``: doctype-aware
detection surfaced on the raw balanced cutoff) and
``G8SearchParityHarnessTests.emitSiteBBaseline`` (``site: "siteB"``: the
doctype-blind product path, scored families through the composed posterior).
Each trio is derived into a ``g8_detection_baseline.json`` by
:mod:`resecta_data.eval.baseline`; this module joins the two derived
baselines per family and over the grand total and reports Site B minus
detector on every headline number (precision / recall / F1 / F2 / the raw
counts) and on the packet-tier block (must recall + Option-C precision + F2,
should recall, must_not fire rate). It is the site-gap arithmetic, in the
pipeline rather than beside it.

Pure arithmetic over the two frozen derived dicts: no re-join, no re-scoring.
A family present in only one baseline is reported with the side it appears
on and no delta (never a fabricated zero). Deterministic: sorted iteration,
input digests instead of a wall-clock. Dev/eval artifact; no install route.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final, Literal

from resecta_data.common.io import canonical_bytes, sha256_bytes

from .payloads import (
    BaselineCell,
    PerTier,
    SiteDelta,
    SiteGapPayload,
    SiteGapRow,
    SiteGapTotals,
    SiteSummary,
    SiteTierSummary,
    as_baseline_payload,
)

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.eval.sitegap"
_SCHEMA_VERSION: Final[int] = 1
_METRIC: Final[str] = "g8_site_gap"

# Headline scalars carried per side and differenced (Site B minus detector).
_ScalarField = Literal[
    "precision",
    "recall",
    "f1",
    "f2",
    "precision_with_decoys",
    "true_positives",
    "false_negatives",
    "false_positives",
    "adversarial_suppress_fired",
]
_SCALAR_FIELDS: Final[tuple[_ScalarField, ...]] = (
    "precision",
    "recall",
    "f1",
    "f2",
    "precision_with_decoys",
    "true_positives",
    "false_negatives",
    "false_positives",
    "adversarial_suppress_fired",
)

# Movements below this are float dust, not a gap (ratios of small integers).
_FLOAT_TOL: Final[float] = 1e-12

_SIDE_DETECTOR: Final[str] = "detector"
_SIDE_SITEB: Final[str] = "siteb"


def _tier_summary(per_tier: PerTier) -> SiteTierSummary:
    """The per-tier numbers a gap row carries for one side."""
    must, should = per_tier["must"], per_tier["should"]
    watch, must_not = per_tier["watch"], per_tier["must_not"]
    return {
        "must": {
            "total": must["total"],
            "covered": must["covered"],
            "recall": must["recall"],
            "precision_option_c": must["precision_option_c"],
            "f2_option_c": must["f2_option_c"],
        },
        "should": {
            "total": should["total"],
            "covered": should["covered"],
            "recall": should["recall"],
        },
        "watch": {
            "total": watch["total"],
            "covered": watch["covered"],
            "covered_rate": watch["covered_rate"],
        },
        "must_not": {
            "total": must_not["total"],
            "fired": must_not["fired"],
            "fire_rate": must_not["fire_rate"],
        },
    }


def _summary(cell: BaselineCell) -> SiteSummary:
    """One side of a gap row: the headline scalars (``_SCALAR_FIELDS``, in
    order), intervals and tier block."""
    return {
        "precision": cell["precision"],
        "recall": cell["recall"],
        "f1": cell["f1"],
        "f2": cell["f2"],
        "precision_with_decoys": cell["precision_with_decoys"],
        "true_positives": cell["true_positives"],
        "false_negatives": cell["false_negatives"],
        "false_positives": cell["false_positives"],
        "adversarial_suppress_fired": cell["adversarial_suppress_fired"],
        "support_n": cell["support_n"],
        "detections_n": cell["detections_n"],
        "precision_wilson95": cell["precision_wilson95"],
        "recall_wilson95": cell["recall_wilson95"],
        "low_confidence": cell["low_confidence"],
        "per_tier": _tier_summary(cell["per_tier"]),
    }


def _delta(detector: BaselineCell, siteb: BaselineCell) -> SiteDelta:
    """Site B minus detector on every differenced scalar (``_SCALAR_FIELDS``, in
    order) and tier field (keyed ``<tier>_<field>``; the summary carries the
    counts beside them)."""
    d_tier, s_tier = detector["per_tier"], siteb["per_tier"]
    return {
        "precision": siteb["precision"] - detector["precision"],
        "recall": siteb["recall"] - detector["recall"],
        "f1": siteb["f1"] - detector["f1"],
        "f2": siteb["f2"] - detector["f2"],
        "precision_with_decoys": siteb["precision_with_decoys"] - detector["precision_with_decoys"],
        "true_positives": siteb["true_positives"] - detector["true_positives"],
        "false_negatives": siteb["false_negatives"] - detector["false_negatives"],
        "false_positives": siteb["false_positives"] - detector["false_positives"],
        "adversarial_suppress_fired": siteb["adversarial_suppress_fired"]
        - detector["adversarial_suppress_fired"],
        "per_tier": {
            "must_recall": s_tier["must"]["recall"] - d_tier["must"]["recall"],
            "must_precision_option_c": s_tier["must"]["precision_option_c"]
            - d_tier["must"]["precision_option_c"],
            "must_f2_option_c": s_tier["must"]["f2_option_c"] - d_tier["must"]["f2_option_c"],
            "should_recall": s_tier["should"]["recall"] - d_tier["should"]["recall"],
            "watch_covered_rate": s_tier["watch"]["covered_rate"] - d_tier["watch"]["covered_rate"],
            "must_not_fire_rate": s_tier["must_not"]["fire_rate"] - d_tier["must_not"]["fire_rate"],
        },
    }


def _has_gap(delta: SiteDelta) -> bool:
    """True when any differenced number moved beyond float dust."""
    scalars = [abs(float(delta[field])) for field in _SCALAR_FIELDS]
    tiers = [abs(float(value)) for value in delta["per_tier"].values()]
    return any(value > _FLOAT_TOL for value in scalars + tiers)


def _gap_row(detector: BaselineCell | None, siteb: BaselineCell | None) -> SiteGapRow:
    """One family's row: both sides (null when absent), the delta, presence."""
    present = [
        side
        for side, cell in ((_SIDE_DETECTOR, detector), (_SIDE_SITEB, siteb))
        if cell is not None
    ]
    return {
        "present_in": present,
        "detector": _summary(detector) if detector is not None else None,
        "siteb": _summary(siteb) if siteb is not None else None,
        "delta_siteb_minus_detector": (
            _delta(detector, siteb) if detector is not None and siteb is not None else None
        ),
    }


def _totals_row(detector: BaselineCell, siteb: BaselineCell) -> SiteGapTotals:
    """The grand-total row: a family row whose two sides are always present."""
    return {
        "present_in": [_SIDE_DETECTOR, _SIDE_SITEB],
        "detector": _summary(detector),
        "siteb": _summary(siteb),
        "delta_siteb_minus_detector": _delta(detector, siteb),
    }


def build_site_gap(detector: Mapping[str, object], siteb: Mapping[str, object]) -> SiteGapPayload:
    """Join two derived G8 baselines into the per-family + grand-total site gap.

    Args:
        detector: The parsed derived ``g8_detection_baseline.json`` of the
            detector-site trio (NOT ``_cells.json``).
        siteb: The parsed derived baseline of the Site-B trio.

    Returns:
        A JSON-serializable dict matching ``schemas/g8_site_gap.schema.json``:
        per-family rows (every family present on either side), the grand-total
        row, the sorted list of families whose delta moved, and both input
        digests for provenance.

    Raises:
        KeyError: If a baseline lacks a required block or field (fail loud --
            a malformed baseline is a contract violation).
    """
    detector_baseline = as_baseline_payload(detector)
    siteb_baseline = as_baseline_payload(siteb)
    detector_families = detector_baseline["per_family"]
    siteb_families = siteb_baseline["per_family"]
    families = sorted(set(detector_families) | set(siteb_families))

    per_family = {
        family: _gap_row(detector_families.get(family), siteb_families.get(family))
        for family in families
    }
    totals = _totals_row(detector_baseline["totals"], siteb_baseline["totals"])
    families_with_gap = sorted(
        family
        for family, row in per_family.items()
        if (delta := row["delta_siteb_minus_detector"]) is not None and _has_gap(delta)
    )

    payload: SiteGapPayload = {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "metric": _METRIC,
        "detector_sha256": sha256_bytes(canonical_bytes(detector)),
        "siteb_sha256": sha256_bytes(canonical_bytes(siteb)),
        "families": families,
        "families_with_gap": families_with_gap,
        "per_family": per_family,
        "totals": totals,
    }
    total_delta = totals["delta_siteb_minus_detector"]
    logger.info(
        "eval sitegap: %d families, %d with a gap; grand-total recall %+.4f precision %+.4f",
        len(families),
        len(families_with_gap),
        total_delta["recall"],
        total_delta["precision"],
    )
    return payload


__all__ = ["build_site_gap"]
