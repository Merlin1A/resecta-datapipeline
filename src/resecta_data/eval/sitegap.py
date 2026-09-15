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
should recall, must_not fire rate). It is the M12-02 arithmetic, in the
pipeline rather than beside it.

Pure arithmetic over the two frozen derived dicts: no re-join, no re-scoring.
A family present in only one baseline is reported with the side it appears
on and no delta (never a fabricated zero). Deterministic: sorted iteration,
input digests instead of a wall-clock. Dev/eval artifact; no install route.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from resecta_data.common.io import sha256_bytes

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.eval.sitegap"
_SCHEMA_VERSION: Final[int] = 1
_METRIC: Final[str] = "g8_site_gap"

# Headline scalars carried per side and differenced (Site B minus detector).
_SCALAR_FIELDS: Final[tuple[str, ...]] = (
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

# Per-tier scalars differenced, as (tier, field) pairs; the summary carries
# the counts beside them.
_TIER_DELTA_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("must", "recall"),
    ("must", "precision_option_c"),
    ("must", "f2_option_c"),
    ("should", "recall"),
    ("watch", "covered_rate"),
    ("must_not", "fire_rate"),
)

# Movements below this are float dust, not a gap (ratios of small integers).
_FLOAT_TOL: Final[float] = 1e-12

_SIDE_DETECTOR: Final[str] = "detector"
_SIDE_SITEB: Final[str] = "siteb"


def _tier_summary(per_tier: dict[str, Any]) -> dict[str, Any]:
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


def _summary(cell: dict[str, Any]) -> dict[str, Any]:
    """One side of a gap row: the headline scalars, intervals and tier block."""
    summary: dict[str, Any] = {field: cell[field] for field in _SCALAR_FIELDS}
    summary["support_n"] = cell["support_n"]
    summary["detections_n"] = cell["detections_n"]
    summary["precision_wilson95"] = cell["precision_wilson95"]
    summary["recall_wilson95"] = cell["recall_wilson95"]
    summary["low_confidence"] = cell["low_confidence"]
    summary["per_tier"] = _tier_summary(cell["per_tier"])
    return summary


def _delta(detector: dict[str, Any], siteb: dict[str, Any]) -> dict[str, Any]:
    """Site B minus detector on every differenced scalar and tier field."""
    delta: dict[str, Any] = {field: siteb[field] - detector[field] for field in _SCALAR_FIELDS}
    delta["per_tier"] = {
        f"{tier}_{field}": siteb["per_tier"][tier][field] - detector["per_tier"][tier][field]
        for tier, field in _TIER_DELTA_FIELDS
    }
    return delta


def _has_gap(delta: dict[str, Any]) -> bool:
    """True when any differenced number moved beyond float dust."""
    scalars = [abs(float(delta[field])) for field in _SCALAR_FIELDS]
    tiers = [abs(float(value)) for value in delta["per_tier"].values()]
    return any(value > _FLOAT_TOL for value in scalars + tiers)


def _gap_row(detector: dict[str, Any] | None, siteb: dict[str, Any] | None) -> dict[str, Any]:
    """One family's row: both sides (null when absent), the delta, presence."""
    present = [
        side
        for side, cell in ((_SIDE_DETECTOR, detector), (_SIDE_SITEB, siteb))
        if cell is not None
    ]
    both = detector is not None and siteb is not None
    return {
        "present_in": present,
        _SIDE_DETECTOR: _summary(detector) if detector is not None else None,
        _SIDE_SITEB: _summary(siteb) if siteb is not None else None,
        "delta_siteb_minus_detector": (
            _delta(detector, siteb) if both else None  # type: ignore[arg-type]
        ),
    }


def build_site_gap(detector: dict[str, Any], siteb: dict[str, Any]) -> dict[str, Any]:
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
    detector_families: dict[str, Any] = detector["per_family"]
    siteb_families: dict[str, Any] = siteb["per_family"]
    families = sorted(set(detector_families) | set(siteb_families))

    per_family = {
        family: _gap_row(detector_families.get(family), siteb_families.get(family))
        for family in families
    }
    totals = _gap_row(detector["totals"], siteb["totals"])
    families_with_gap = sorted(
        family
        for family, row in per_family.items()
        if row["delta_siteb_minus_detector"] is not None
        and _has_gap(row["delta_siteb_minus_detector"])
    )

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "metric": _METRIC,
        "detector_sha256": sha256_bytes(_canonical_bytes(detector)),
        "siteb_sha256": sha256_bytes(_canonical_bytes(siteb)),
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


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical-JSON bytes of ``payload`` for the provenance digest.

    Mirrors :func:`resecta_data.common.io.dump_canonical_json` (sorted keys,
    indent 2, the canonical separators, ``ensure_ascii=False``, trailing
    newline) so an unchanged baseline yields an unchanged digest.
    """
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=False,
    )
    return encoded.encode("utf-8") + b"\n"


__all__ = ["build_site_gap"]
