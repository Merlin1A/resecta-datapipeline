"""Promote the pre-reviewed positive context keywords into the shipping artifact.

The per-category context-keyword gazetteer is produced by a multi-stage
workflow:

1. The Swift lift: positive context keywords lifted from the per-detector
   Swift ``*ContextKeywords.swift`` files into
   ``src/resecta_data/gazetteers/context_keywords/sources/d11_lift_candidates.json``
   with per-row provenance and routing decisions, plus four authored
   license-plate label words. 49 rows ship; 22 honorifics stay engine-side.
2. The authored set: English context-keyword candidates in
   ``src/resecta_data/context/sources/d12_candidates.json``. 143 rows
   ship across DOB / Name / NPI / DEA / ITIN (dea 29 · dob 26 · itin 28 ·
   name 31 · npi 29; the X12 ``DMG*D8`` literal ships engine-side as a
   regex, not as a context keyword, and the four court role nouns
   ``defendant`` / ``plaintiff`` / ``petitioner`` / ``respondent`` are not
   positive name anchors — they name a party role, not a person), plus the
   ssn and ein label rows.
3. The Bates anchors: 10 English ``.legal``-scoped anchors in
   ``src/resecta_data/context/sources/d16_bates_anchors.json``. The
   engine-side baseline regex ``^[A-Z]{1,4}[_-]?0*\\d{4,8}$`` is the Swift
   half and does NOT flow through this builder.
4. This builder reads all three candidate files, drops engine-side / staging
   rows, strips staging fields, applies vocabulary translation on
   ``proposed_doctypes`` (dotted → engine enum), backfills ``confidence`` on
   the lifted rows, and canonicalizes the merged payload.

Every candidates file is a curated context asset: it changes only under an
approved change plan (see CONTRIBUTING.md).

Facts the engine side imposes on this file: the ``bates`` rows are dropped by
the engine's loader (no category mapping), so they only reach the OCR custom
words; every doctype-scoped positive row reaches the OCR custom words only —
the detectors query global rows alone; ``tin`` must stay doctype-scoped, since
as a global row it would be a live substring of ``routing`` and boost every
SSN-shaped routing number.

Posture baked in here:

* **Honorifics stay engine-side.** ``_SHIPPING_STATUS`` (`a21-shipping`)
  filters them out belt-and-suspenders with ``_SHIPPING_CATEGORIES``.
* **Negatives stay in the negative-context gazetteer.** Every shipping row
  has ``polarity: positive``; the ITIN-specific negatives are drafted
  separately and do NOT flow through this builder.
* **Vocabulary translation.** The authored candidates use a dotted vocabulary
  (`.legal`, `.medical`, `.foia`, `.financial`, `.generic`) in
  ``proposed_doctypes``. The builder translates onto the engine 5-class
  enum: strip leading `.`, map `legal` → `court`. Other values pass through.
* **Bare DEA gate.** The bare ``DEA`` row carries
  ``detector_requires_secondary: true`` plus ``confidence: low``; both fields
  pass through to the wire as the engine-routing gate.
* **Name reinstatements.** ``decedent`` and ``name of requester`` ship
  at the candidates-file confidence (`high`); other V1.1+ Name rows are
  omitted upstream in the candidates file (not in this builder).
* **License posture.** The builder strips ``license_posture`` from the wire
  (the engine does not need it); the citation work lives on the candidates
  file, not the build output.
* **IRSN literal.** Confidence ``medium (flag)`` passes through.
* **DOB family windowed-matching.** ``detector_note`` passes through
  on the 13 DOB-family rows that carry it.
* **Non-literal flagging.** The lifted candidates carry ``non_literal_flag``;
  staging-only, stripped before emission.

Determinism rules apply: rows are sorted by
``(category, term)`` before emission; no wall-clock content; ``seed``
is recorded but not consumed (deterministic transcription).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/context_keywords"
_SCHEMA_VERSION: Final[int] = 1

# The lifted candidates live beside this builder under ``sources/``
# (raw inputs live under ``src/resecta_data/<module>/sources/``).
# The authored candidates sit at ``src/resecta_data/context/sources/`` — a
# separate location because they are authored content, not a Swift lift.
# Both change only under an approved change plan.
_D11_CANDIDATES_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "sources" / "d11_lift_candidates.json"
)
_REPO_SRC: Final[Path] = Path(__file__).resolve().parents[2]
_D12_CANDIDATES_PATH: Final[Path] = _REPO_SRC / "context" / "sources" / "d12_candidates.json"
# The Bates anchors: authored `.legal`-scoped anchors, placed under
# ``src/resecta_data/context/sources/`` parallel to the authored set
# (``build/`` is git-ignored; source candidates live under
# ``src/resecta_data/<module>/sources/``).
_D16_CANDIDATES_PATH: Final[Path] = _REPO_SRC / "context" / "sources" / "d16_bates_anchors.json"

# The gazetteer ships ten detector categories (4 from the lift, 6 from the
# authored set). Honorific rows in the lift are flagged ``engine-side, do
# not migrate`` and are filtered before emission. The set is closed for
# V1; later locales / detectors bump ``_SCHEMA_VERSION``.
_SHIPPING_STATUS: Final[str] = "a21-shipping"
_SHIPPING_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"bates", "dea", "dob", "ein", "itin", "licenseplate", "mrn", "name", "npi", "ssn"}
)

# Per-category row counts. Drift means a candidates file changed; surface
# loudly rather than ship silently. The lift split is 10/13/11/11; the
# authored split is 26/30/29/29/28 (dob 27 → 26 when the X12 ``DMG*D8``
# literal was dropped; the regex ships engine-side). The Bates anchors add
# 10 ``.legal`` rows on top of the lifted bates baseline (11 → 21 bates rows
# total). The search-and-redact additions: ssn 10->15 (+5 IRS TIN labels),
# name 30->35 (+5 IRS 1099/W-2 labels), ein 0->6 (EIN category
# infrastructure). The context-asset review then added four license-plate
# label words (licenseplate 11->15) and dropped the four court role nouns
# from name (35->31). Combined total: 213 shipping rows.
_EXPECTED_PER_CATEGORY: Final[dict[str, int]] = {
    "ssn": 15,
    "mrn": 13,
    "bates": 21,
    "licenseplate": 15,
    "dob": 26,
    "name": 31,
    "npi": 29,
    "dea": 29,
    "itin": 28,
    "ein": 6,
}
_EXPECTED_TOTAL: Final[int] = sum(_EXPECTED_PER_CATEGORY.values())

# Vocabulary translation: the authored candidates use a dotted vocabulary in
# ``proposed_doctypes``; the wire enum is unprefixed (engine-side). Strip
# the leading ``.`` and remap ``legal`` (candidates vocabulary) onto ``court``
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

# Lifted rows have no ``confidence`` field in the candidates file (they are
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
    for already-wire values (lifted rows already have empty or wire-vocabulary
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
    """Return the context-keywords shipping payload (lift + authored + Bates anchors).

    Args:
        seed: PRNG seed. Recorded for reproducibility; the builder is a
            deterministic filter-and-sort over the candidates files and
            does not consume randomness.
        d11_candidates_path: Override for the lifted candidates JSON.
            Defaults to the file shipped under
            ``gazetteers/context_keywords/sources/``; tests pass a tmp path.
        d12_candidates_path: Override for the authored candidates JSON.
            Defaults to ``context/sources/d12_candidates.json``; tests pass
            a tmp path.
        d16_candidates_path: Override for the Bates-anchor candidates JSON.
            Defaults to ``context/sources/d16_bates_anchors.json``; tests
            pass a tmp path. The Bates anchors use the authored shape
            (``proposed_doctypes`` dotted vocabulary, candidate-side
            ``confidence``) so the existing ``source="d12"`` projection
            applies without special-casing.

    Returns:
        A payload dict conforming to ``schemas/context_keywords.schema.json``.
        Rows are sorted by ``(category, term)`` ascending. Total row count
        is 213 (15 ssn + 13 mrn + 21 bates + 15 licenseplate + 26 dob +
        31 name + 29 npi + 29 dea + 28 itin + 6 ein; bates count reflects
        the 10 ``.legal``-scoped anchors on top of the lifted base of 11;
        the dob count reflects dropping the X12 ``DMG*D8`` literal; the
        ssn/name/ein rows were added with the search-and-redact release).

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
            f"got {len(shipping)}. See _EXPECTED_PER_CATEGORY."
        )

    actual_per_category = Counter(row["category"] for row in shipping)
    if dict(actual_per_category) != _EXPECTED_PER_CATEGORY:
        raise PipelineError(
            "context_keywords: per-category counts diverge from the "
            f"expectations. Expected {_EXPECTED_PER_CATEGORY}, "
            f"got {dict(actual_per_category)}. This indicates a candidates-file "
            "change that needs an approved change plan."
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "entries": shipping,
    }
