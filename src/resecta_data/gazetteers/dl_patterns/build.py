"""Promote the pre-reviewed dl-pattern candidates into the shipping artifact.

The per-state driver-license-number pattern gazetteer (D-13 in the V1 data
requirements catalog) is produced by a two-stage workflow:

1. The CC-DERIVE D-13 orchestration emits
   ``src/resecta_data/gazetteers/dl_patterns/sources/dl_patterns_candidates.json``
   from W-R-3 + W-R-5 + the 2026-04-25 V1 shipping dispositions (§2 + §8).
   The file is reviewed row-by-row by Jesse and becomes immutable
   once committed.
2. This builder reads that candidates file, strips any audit-only keys
   (none defined for D-13 today; the strip step is a structural mirror of
   the passport_patterns builder against future audit-field additions),
   sorts defensively, and canonicalizes the payload.

License posture is baked into the candidates file (this builder is a
pure transcription, not a reclassifier):

* **F-25.** NC, TN, UT ship tight per-state regex anchored to
  state-statute primary publication. AK, MT, SC, SD, WV, WY, DC ship the
  canonical AAMVA M1 envelope ``^[A-Z0-9]{8,13}$`` with the per-state
  format claim retracted.
* **F-39.** IL, WA, NV, NJ ship under the same AAMVA-envelope rule;
  aggregator lineage (Skyhigh / MyShn / NTSI) is retired from
  ``source_url`` and ``license_notes`` under ingestion-of-record
  discipline.
* **F-32 (Tier 2 advisory).** The candidates file's top-level
  ``_advisory_note`` carries the WA specimen-image audit summary. The
  advisory ships as a JSON-only field on the shipping artifact (no
  separate audit deliverable). This builder propagates the note verbatim.

Determinism rules apply: no wall-clock content;
``generated_date`` is lifted verbatim from the candidates file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/dl_patterns"
_SCHEMA_VERSION: Final[int] = 1

# The candidates file lives beside this builder under ``sources/``
# (raw inputs under ``src/resecta_data/<module>/sources/``).
# Keeping it out of ``build/`` makes it immutable-by-convention and
# safe from ``make clean`` / ``make verify``'s determinism rewipe.
_CANDIDATES_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "sources" / "dl_patterns_candidates.json"
)

# Total shipping rows: 50 states + DC.
_EXPECTED_ROW_COUNT: Final[int] = 51

# Audit-only row keys produced by the candidates-file workflow. Empty for
# D-13 today (no staging fields exist on the row schema; the per-row
# review surface lives in `license_notes` + `f_flags`). Retained as a
# structural mirror of passport_patterns/build.py so a future audit-only
# field addition (e.g. ``_internal_note``) can be filtered without
# re-engineering the strip step. The shipping schema is strict
# (``additionalProperties: false``) — any leak would fail validation.
_AUDIT_ONLY_ROW_KEYS: Final[frozenset[str]] = frozenset()


def _strip_audit(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop audit-only keys so the row conforms to the shipping schema."""
    if not _AUDIT_ONLY_ROW_KEYS:
        return dict(entry)
    return {k: v for k, v in entry.items() if k not in _AUDIT_ONLY_ROW_KEYS}


def build(seed: int, *, candidates_path: Path | None = None) -> dict[str, Any]:
    """Return the dl-patterns shipping payload.

    Args:
        seed: PRNG seed. Recorded for reproducibility; the builder is a
            deterministic filter-and-sort over the candidates file and
            does not consume randomness.
        candidates_path: Override for the candidates JSON location.
            Defaults to the file shipped alongside this builder under
            ``sources/``; tests pass a tmp path.

    Returns:
        A payload dict conforming to ``schemas/dl_patterns.schema.json``.
        Rows are sorted by ``state_code`` ascending; per-row
        ``patterns`` arrays are sorted lexicographically (no-op for the
        51-row V1 set, every row carries a single anchored regex).

    Raises:
        PipelineError: If the candidates file is missing or malformed,
            or if the shipping row count diverges from 51. Fail-loud.
    """
    path = candidates_path if candidates_path is not None else _CANDIDATES_PATH
    data = load_json(path)
    if not isinstance(data, dict) or "rows" not in data:
        raise PipelineError(
            f"dl_patterns: candidates file {path} is malformed (expected a dict with a 'rows' key)."
        )

    rows = data["rows"]
    if not isinstance(rows, list):
        raise PipelineError(f"dl_patterns: candidates file {path} 'rows' is not a list.")

    shipping: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            raise PipelineError(
                f"dl_patterns: candidates file {path} contains a non-object row entry."
            )
        cleaned = _strip_audit(entry)
        patterns = cleaned.get("patterns")
        if isinstance(patterns, list) and len(patterns) > 1:
            cleaned["patterns"] = sorted(patterns)
        shipping.append(cleaned)

    shipping.sort(key=lambda entry: entry["state_code"])

    if len(shipping) != _EXPECTED_ROW_COUNT:
        raise PipelineError(
            f"dl_patterns: expected {_EXPECTED_ROW_COUNT} shipping rows, "
            f"got {len(shipping)} (51 jurisdictions: "
            "50 states + DC)."
        )

    generated_date = data.get("generated_date")
    if not isinstance(generated_date, str):
        raise PipelineError(f"dl_patterns: candidates file {path} missing 'generated_date' string.")

    source_briefs = data.get("source_briefs")
    if not isinstance(source_briefs, list) or not all(isinstance(s, str) for s in source_briefs):
        raise PipelineError(
            f"dl_patterns: candidates file {path} 'source_briefs' must be a list of strings."
        )

    payload: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "generated_date": generated_date,
        "seed": seed,
        "source_briefs": list(source_briefs),
        "rows": shipping,
    }

    advisory_note = data.get("_advisory_note")
    if isinstance(advisory_note, str) and advisory_note:
        payload["_advisory_note"] = advisory_note

    return payload
