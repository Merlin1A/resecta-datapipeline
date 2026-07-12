"""Promote the hand-curated detector→rule_id catalog into the shipping artifact.

The PII detector rule-ID catalog (D-18 in the V1 data requirements catalog,
A22 surface) ships as a closed list of rows, one per Swift detector that
emits into ``MatchAuditRecord.ruleID``. Per spec §1.20 acceptance, every
Swift detector symbol that wires into ``MatchAuditRecord.ruleID`` must map
to ≥ 1 catalog entry (W-I1 ``Done =`` test).

Workflow:

1. A CC-DERIVE orchestration session walks the Swift detector tree
   read-only and authors
   ``src/resecta_data/rules/sources/rule_catalog.json`` row-by-row, with
   per-row provenance (``_source_swift_file``, ``_source_swift_lines``,
   ``_emitted_rule_id``, ``_naming_rationale``, ``_jesse_review_flags``)
   captured as underscore-prefixed staging fields. Jesse reviews
   row-by-row in PR (naming sign-off per spec §1.20). The file is
   immutable.
2. This builder reads the source catalog, strips staging fields,
   sorts by ``rule_id`` ascending, and emits the canonical wrapper
   payload at ``build/rules/rule_catalog.json``.

Wire-format wrapper (``{version, generated_by, seed, entries}``) matches
the established context_keywords / passport_patterns / institutions /
address_components precedent — see ``schemas/rule_catalog.schema.json``.

F-item posture baked in here:

* **F-8 (version-bump policy).** Per-row ``version`` field carries the
  V1 baseline ``"1.0"`` string. Behavior-changing edits to a Swift
  detector roll the per-row version (e.g., ``"1.0"`` → ``"1.1"``);
  rule_id-breaking changes roll the v-tag in ``rule_id`` itself
  (``pii.foo.v1`` → ``pii.foo.v2``). Ties to spec §D13 = M3
  engine-code-hash trigger.
* **F-42 (DCJ/DCK separation).** ``pii.dd.v1`` + ``pii.audit.v1`` +
  ``pii.icn.v1`` are NOT shipped from D-18 — per spec §1.20 D-36
  addendum, all three land in D-36's chain (the kickoff list ambiguously
  proposed shipping audit/icn here; the spec wins per
  cc-derive-FAILURE-MODES §2). Defer-note in
  cc-derive-d18-session-2.md surfaces this for Jesse.

Determinism rules apply: rows are sorted by ``rule_id``
before emission; no wall-clock content; ``seed`` is recorded but not
consumed (deterministic transcription).

See the V1 data-requirements catalog and the session-1 commit for full
lineage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json

_GENERATED_BY: Final[str] = "resecta-data/rules"
_SCHEMA_VERSION: Final[int] = 1

# Source catalog lives under ``sources/`` (raw inputs
# under ``src/resecta_data/<module>/sources/``). Same placement precedent
# as context_keywords (W-D14-2 R-2 Option A; D-11 session-1 commit
# f364095). The ``build/`` location named in spec §1.20 is the shipping
# artifact destination, not the hand-curated source — ``build/`` is
# git-ignored (see .gitignore), so the source-of-truth
# could not live there.
_SOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "sources" / "rule_catalog.json"

# Wire-format row keys (matches schemas/rule_catalog.schema.json
# entry shape, additionalProperties: false). Underscore-prefixed staging
# keys (``_source_swift_file``, ``_source_swift_lines``, ``_emitted_rule_id``,
# ``_naming_rationale``, ``_jesse_review_flags``) are dropped by ``_to_wire``.
_WIRE_ROW_KEYS: Final[tuple[str, ...]] = (
    "rule_id",
    "family",
    "detector",
    "version",
    "source_artifact",
    "is_checksum_gated",
)

# Closed shipping count. Originally 19: the 17 Swift detectors
# invoked by PIIDetector.detect (Resecta master @
# 051a41be0bb15e1785679c350f9b0443ecd27f1f, lines 119-151), with the MRN
# detector contributing 3 rows (pii.mrn.labeled.v1 / pii.mrn.patient_id.v1 /
# pii.mrn.institution.v1) per Jesse's session-2 split decision. The
# 2026-06-10 W-I1 reconciliation (Resecta @ cbbf0c8) then tracked two
# engine-side changes: +2 rect-based visual detectors from DRAW-2/DRAW-3
# (pii.barcode.vision.v1 / pii.signature.heuristic.v1), and -1 for
# pii.bates.v1 -- the general-purpose pivot (Resecta PR #80, e116401)
# dropped the Bates detection category from the engine, and the shipped
# iOS rule-catalog.json already carries 18 rows without it. Total was 20.
# Search-impl S2 task 11 (2026-06-11) adds pii.routing_number.v1 for the
# new ABA routing-number detector (design 01 section 4). Total = 21.
# D-36 adds pii.dd.v1 + pii.audit.v1 + pii.icn.v1 separately per F-42
# (spec section 7.5).
_EXPECTED_TOTAL: Final[int] = 21


def _to_wire(entry: dict[str, Any]) -> dict[str, Any]:
    """Project ``entry`` onto the wire-format row shape.

    Underscore-prefixed staging fields are dropped — the shipping
    schema is strict (``additionalProperties: false``).
    """
    return {key: entry[key] for key in _WIRE_ROW_KEYS}


def build(seed: int, *, source_path: Path | None = None) -> dict[str, Any]:
    """Return the rule-catalog shipping payload.

    Args:
        seed: PRNG seed. Recorded for reproducibility; the builder is a
            deterministic strip-and-sort over the source file and does
            not consume randomness.
        source_path: Override for the source JSON location. Defaults to
            the file shipped alongside this builder under ``sources/``;
            tests pass a tmp path.

    Returns:
        A payload dict conforming to ``schemas/rule_catalog.schema.json``.
        Rows are sorted by ``rule_id`` ascending.

    Raises:
        PipelineError: If the source file is missing, malformed, or its
            row count diverges from the V1 closed count (17). Fail-loud.
    """
    path = source_path if source_path is not None else _SOURCE_PATH
    data = load_json(path)
    if not isinstance(data, list):
        raise PipelineError(
            f"rule_catalog: source file {path} is malformed "
            "(expected a top-level JSON array of row dicts)."
        )

    if len(data) != _EXPECTED_TOTAL:
        raise PipelineError(
            f"rule_catalog: expected {_EXPECTED_TOTAL} rows, got {len(data)}. "
            "Catalog is closed at the currently-shipping Swift detectors "
            "(17 PIIDetector.detect detectors with the MRN 3-way split, plus "
            "the DRAW-2/DRAW-3 visual detectors, plus pii.routing_number.v1 "
            "from S2 task 11). D-36 adds pii.dd.v1 + "
            "pii.audit.v1 + pii.icn.v1 separately per F-42 (spec section 7.5)."
        )

    entries = [_to_wire(entry) for entry in data]
    entries.sort(key=lambda row: row["rule_id"])

    rule_ids = [row["rule_id"] for row in entries]
    if len(set(rule_ids)) != len(rule_ids):
        raise PipelineError(
            f"rule_catalog: duplicate rule_id detected. Rule IDs must be "
            f"unique within the catalog. Saw: {rule_ids}"
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "entries": entries,
    }
