"""Promote the pre-reviewed positive context keywords into the shipping artifact.

The per-category context-keyword gazetteer (D-11 + D-12 + D-16 in the V1 data
requirements catalog, A21 surface) is produced by a multi-stage workflow:

1. CC-DERIVE D-11 lifts positive context keywords from the per-detector Swift
   ``*ContextKeywords.swift`` files into
   ``src/resecta_data/gazetteers/context_keywords/sources/d11_lift_candidates.json``
   with per-row provenance and routing decisions. 45 rows ship; 22 honorifics
   stay engine-side per spec §4 #9 (A19).
2. CC-DERIVE D-12 authors English context-keyword candidates (W-R-8 §3-§7)
   into ``src/resecta_data/context/sources/d12_candidates.json``. 142 rows
   ship across DOB / Name / NPI / DEA / ITIN (was 143; the F-50 Row 3 X12
   ``DMG*D8`` literal was dropped — pattern-class regex ships engine-side,
   not as a context-keyword).
3. CC-DERIVE D-16 authors 10 English Bates ``.legal``-scoped anchors into
   ``src/resecta_data/context/sources/d16_bates_anchors.json`` per spec
   §1.18 + §4 ambiguity #14 (F-14 acked default). The engine-side baseline
   regex ``^[A-Z]{1,4}[_-]?0*\\d{4,8}$`` is the Swift-deferred half and
   does NOT flow through this builder.
4. This builder reads all three candidate files, drops engine-side / staging
   rows, strips staging fields, applies vocabulary translation on
   ``proposed_doctypes`` (dotted → engine enum), backfills ``confidence`` on
   D-11 rows, and canonicalizes the merged 197-row payload.

F-item posture baked in here:

* **A19 honorifics stay engine-side.** ``_SHIPPING_STATUS`` (`a21-shipping`)
  filters them out belt-and-suspenders with ``_SHIPPING_CATEGORIES``.
* **Negatives stay in A5.** Every shipping row has ``polarity: positive``;
  D-12's F-48 ITIN-specific A5 negatives are drafted separately per spec
  §1.14 + §7.6 and do NOT flow through this builder.
* **F-46 vocabulary expansion.** D-12 candidates use a dotted vocabulary
  (`.legal`, `.medical`, `.foia`, `.financial`, `.generic`) in
  ``proposed_doctypes``. The builder translates onto the engine 5-class
  enum: strip leading `.`, map `legal` → `court`. Other values pass through.
* **F-47 bare DEA gate.** The bare ``DEA`` row carries
  ``detector_requires_secondary: true`` plus ``confidence: low``; both fields
  pass through to the wire as the engine-routing gate.
* **F-49 Name reinstatements.** ``decedent`` and ``name of requester`` ship
  at the candidates-file confidence (`high`); other V1.1+ Name rows are
  omitted upstream in the candidates file (not in this builder).
* **F-50 license posture.** Three NUCC/payer/X12 DOB rows carry
  ``license_posture: needs-review`` in the candidates file; the builder
  strips ``license_posture`` from the wire (engine doesn't need it). Jesse's
  re-citation work is on the candidates file, not the build output.
* **F-51 IRSN literal.** Confidence ``medium (flag)`` passes through.
* **F-52 DOB family windowed-matching.** ``detector_note`` passes through
  on the 13 DOB-family rows that carry it.
* **F-6 (non-literal flagging).** D-11 candidates carry ``non_literal_flag``;
  staging-only, stripped before emission.

Determinism rules apply: rows are sorted by
``(category, term)`` before emission; no wall-clock content; ``seed``
is recorded but not consumed (deterministic transcription).

See the V1 data-requirements catalog and the chain DONE markers for full
lineage.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/context_keywords"
_SCHEMA_VERSION: Final[int] = 1

# D-11 candidates: lives beside this builder under ``sources/``
# (raw inputs live under ``src/resecta_data/<module>/sources/``).
# D-12 candidates: at ``src/resecta_data/context/sources/`` — separate
# location because D-12 is authored content, not a Swift lift, so the
# CC-DERIVE chain placed it under a sibling top-level package. Both are
# immutable.
_D11_CANDIDATES_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "sources" / "d11_lift_candidates.json"
)
_REPO_SRC: Final[Path] = Path(__file__).resolve().parents[2]
_D12_CANDIDATES_PATH: Final[Path] = _REPO_SRC / "context" / "sources" / "d12_candidates.json"
# D-16 candidates: authored Bates `.legal` anchors (spec §1.18 + §4 #14).
# Placed under ``src/resecta_data/context/sources/`` parallel to D-12's
# authored set; the kickoff's ``build/context/`` path was overridden because
# ``build/`` is git-ignored and source candidates live
# under ``src/resecta_data/<module>/sources/``.
_D16_CANDIDATES_PATH: Final[Path] = _REPO_SRC / "context" / "sources" / "d16_bates_anchors.json"

# A21 ships nine detector categories (4 from D-11, 5 from D-12). Honorific
# rows in D-11 are flagged ``engine-side, do not migrate`` per spec §4 #9
# (A19) and are filtered before emission. The set is closed at 9 for V1;
# later locales / detectors bump ``_SCHEMA_VERSION``.
_SHIPPING_STATUS: Final[str] = "a21-shipping"
_SHIPPING_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"bates", "dea", "dob", "ein", "itin", "licenseplate", "mrn", "name", "npi", "ssn"}
)

# Per-category row counts. Drift means a candidates file changed; surface
# loudly rather than ship silently. D-11 split is verified at 10/13/11/11
# (cc-derive-d11-DONE); D-12 split is verified at 26/30/29/29/28
# (cc-derive-d12-session-1 + audit at session 2 start; dob 27 → 26 at
# session 4 follow-up — F-50 Row 3 dropped the X12 ``DMG*D8`` literal;
# the regex ships engine-side).
# D-16 adds 10 Bates ``.legal`` anchors on top of the D-11 bates baseline
# (11 → 21 bates rows total) per spec §1.18 + §4 #14.
# search-impl S3 (design 02 sections 2.1, 2.6): ssn 10->15 (+5 IRS TIN labels),
# name 30->35 (+5 IRS 1099/W-2 labels), ein 0->6 (EIN category infrastructure).
# Combined total after S3: 197 + 5 + 5 + 6 = 213 shipping rows.
_EXPECTED_PER_CATEGORY: Final[dict[str, int]] = {
    "ssn": 15,
    "mrn": 13,
    "bates": 21,
    "licenseplate": 11,
    "dob": 26,
    "name": 35,
    "npi": 29,
    "dea": 29,
    "itin": 28,
    "ein": 6,
}
_EXPECTED_TOTAL: Final[int] = sum(_EXPECTED_PER_CATEGORY.values())

# F-46 vocabulary translation: D-12 candidates use a dotted vocabulary in
# ``proposed_doctypes``; the wire enum is unprefixed (engine-side). Strip
# the leading ``.`` and remap ``legal`` (D-12 vocabulary) onto ``court``
# (engine enum). Any value not in the wire enum after translation is
# rejected loudly so a future vocabulary expansion in candidates surfaces
# here rather than at Swift decode.
_DOCTYPE_RENAME: Final[dict[str, str]] = {"legal": "court"}
_WIRE_DOCTYPE_ENUM: Final[frozenset[str]] = frozenset(
    {"court", "financial", "foia", "generic", "medical"}
)

# Optional engine-routing flags that may appear on a subset of rows. Core
# wire keys are emitted unconditionally by ``_to_wire``; these are copied
# only when present on the source row.
_WIRE_OPTIONAL_KEYS: Final[tuple[str, ...]] = (
    "detector_note",
    "detector_requires_secondary",
)

# D-11 rows have no ``confidence`` field in the candidates file (they are
# verbatim from production Swift, presumed reliable). Backfilled to
# ``high`` so the wire format is uniform.
_D11_BACKFILL_CONFIDENCE: Final[str] = "high"


def _is_shipping(entry: dict[str, Any]) -> bool:
    """Return True iff ``entry`` ships in the A21 artifact."""
    if entry.get("proposed_status") != _SHIPPING_STATUS:
        return False
    return entry.get("category") in _SHIPPING_CATEGORIES


def _translate_doctypes(raw: list[str], *, term: str) -> list[str]:
    """Translate candidates-file dotted doctype vocabulary onto wire enum.

    Strips the leading ``.`` and applies ``_DOCTYPE_RENAME``. Pass-through
    for already-wire values (D-11 rows already have empty or wire-vocabulary
    arrays). Raises ``PipelineError`` on any value that does not land in
    ``_WIRE_DOCTYPE_ENUM`` after translation — fail-loud.
    """
    translated: list[str] = []
    for value in raw:
        normalized = value[1:] if value.startswith(".") else value
        normalized = _DOCTYPE_RENAME.get(normalized, normalized)
        if normalized not in _WIRE_DOCTYPE_ENUM:
            raise PipelineError(
                f"context_keywords: row term={term!r} has untranslatable "
                f"doctype value {value!r} (normalized {normalized!r}); "
                f"expected one of {sorted(_WIRE_DOCTYPE_ENUM)}."
            )
        translated.append(normalized)
    return sorted(set(translated))


def _to_wire(entry: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Project ``entry`` onto the wire-format row shape.

    Args:
        entry: A row from one of the candidates files.
        source: Either ``"d11"`` or ``"d12"`` — distinguishes
            confidence-backfill behavior.

    Returns:
        A dict containing the six wire core keys (term, category, polarity,
        locale, doctypes, confidence) plus any of the optional engine-routing
        flags (detector_note, detector_requires_secondary) present on the
        input. Staging fields (``proposed_status``, ``non_literal_flag``,
        ``source_swift_file``, ``source_swift_line``, ``fp_neighbors``,
        ``source_url``, ``synthetic_sample``, ``aliases``, ``notes``,
        ``primary_source_type``, ``license_posture``, ``cross_category``,
        ``proposed_doctypes``) are dropped — the shipping schema is strict
        (``additionalProperties: false``).
    """
    raw_doctypes = entry.get("proposed_doctypes")
    if raw_doctypes is None:
        raw_doctypes = entry.get("doctypes", [])
    doctypes = _translate_doctypes(list(raw_doctypes), term=entry["term"])

    if source == "d11":
        confidence = entry.get("confidence", _D11_BACKFILL_CONFIDENCE)
    else:
        confidence = entry["confidence"]

    row: dict[str, Any] = {
        "term": entry["term"],
        "category": entry["category"],
        "polarity": entry["polarity"],
        "locale": entry["locale"],
        "doctypes": doctypes,
        "confidence": confidence,
    }
    for key in _WIRE_OPTIONAL_KEYS:
        if key in entry:
            row[key] = entry[key]
    return row


def _load_rows(path: Path, *, source: str) -> list[dict[str, Any]]:
    """Load + filter shipping rows from a candidates file."""
    if not path.exists():
        raise PipelineError(f"context_keywords: candidates file {path} ({source}) is missing.")
    data = load_json(path)
    if not isinstance(data, list):
        raise PipelineError(
            f"context_keywords: candidates file {path} ({source}) is malformed "
            "(expected a top-level JSON array of row dicts)."
        )
    return [_to_wire(entry, source=source) for entry in data if _is_shipping(entry)]


def build(
    seed: int,
    *,
    d11_candidates_path: Path | None = None,
    d12_candidates_path: Path | None = None,
    d16_candidates_path: Path | None = None,
) -> dict[str, Any]:
    """Return the context-keywords shipping payload (D-11 + D-12 + D-16 merged).

    Args:
        seed: PRNG seed. Recorded for reproducibility; the builder is a
            deterministic filter-and-sort over the candidates files and
            does not consume randomness.
        d11_candidates_path: Override for the D-11 candidates JSON.
            Defaults to the file shipped under
            ``gazetteers/context_keywords/sources/``; tests pass a tmp path.
        d12_candidates_path: Override for the D-12 candidates JSON.
            Defaults to ``context/sources/d12_candidates.json``; tests pass
            a tmp path.
        d16_candidates_path: Override for the D-16 candidates JSON.
            Defaults to ``context/sources/d16_bates_anchors.json``; tests
            pass a tmp path. D-16 candidates use D-12's authored shape
            (``proposed_doctypes`` dotted vocabulary, candidate-side
            ``confidence``) so the existing ``source="d12"`` projection
            applies without special-casing.

    Returns:
        A payload dict conforming to ``schemas/context_keywords.schema.json``.
        Rows are sorted by ``(category, term)`` ascending. Total row count
        is 213 (15 ssn + 13 mrn + 21 bates + 11 licenseplate + 26 dob +
        35 name + 29 npi + 29 dea + 28 itin + 6 ein; bates count reflects
        the D-16 addition of 10 ``.legal``-scoped anchors on top of the D-11
        base of 11; the dob count reflects dropping the X12 ``DMG*D8``
        literal; ssn/name/ein additions per search-impl S3 design 02
        §§2.1, 2.6).

    Raises:
        PipelineError: If a candidates file is missing or malformed, if the
            doctype vocabulary translation hits an unknown value, or if the
            filtered shipping set diverges from the per-category split. Fail
            loud.
    """
    d11_path = d11_candidates_path if d11_candidates_path is not None else _D11_CANDIDATES_PATH
    d12_path = d12_candidates_path if d12_candidates_path is not None else _D12_CANDIDATES_PATH
    d16_path = d16_candidates_path if d16_candidates_path is not None else _D16_CANDIDATES_PATH

    shipping = (
        _load_rows(d11_path, source="d11")
        + _load_rows(d12_path, source="d12")
        + _load_rows(d16_path, source="d12")
    )
    shipping.sort(key=lambda row: (row["category"], row["term"]))

    if len(shipping) != _EXPECTED_TOTAL:
        raise PipelineError(
            f"context_keywords: expected {_EXPECTED_TOTAL} shipping rows, "
            f"got {len(shipping)}. See D-11 + D-12 session-1 lift counts."
        )

    actual_per_category = Counter(row["category"] for row in shipping)
    if dict(actual_per_category) != _EXPECTED_PER_CATEGORY:
        raise PipelineError(
            "context_keywords: per-category counts diverge from the D-11 + D-12 "
            f"lift expectations. Expected {_EXPECTED_PER_CATEGORY}, "
            f"got {dict(actual_per_category)}. This indicates a candidates-file "
            "change that warrants human review."
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "entries": shipping,
    }
