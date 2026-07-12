"""Promote the pre-reviewed passport-pattern candidates into the shipping artifact.

The per-country passport-number pattern gazetteer (D-14 in the V1 data
requirements catalog) is produced by a two-stage workflow:

1. An orchestration session emits
   ``src/resecta_data/gazetteers/passport_patterns/sources/passport_patterns_candidates.json``
   with per-row provenance (W-R-4 + W-R-4.1 research briefs) and audit
   metadata (``candidate_only``, ``exclude_from_ship``, ...). Jesse
   reviews row-by-row. The file is immutable.
2. This builder reads that candidates file, strips audit-only keys,
   drops non-shipping rows, and canonicalizes the payload.

F-item posture baked in here:

* **F-36 (ship set).** 11 issuers ship: CA, CN, DO, GB, IN, KR, MX, PH,
  SV, US, VN. Dominican Republic uses ``DO`` (ISO 3166-1 alpha-2);
  the candidates file predates F-36's closure and still carries DO as
  ``candidate_only: true`` — the builder carries an explicit post-F-36
  promotion override so DO ships without mutating the candidates file.
* **F-37 (Cuba).** CU is omitted entirely for V1 on OFAC sanctions
  grounds. The candidates-file gate (``exclude_from_ship: true``) is
  the primary drop; the builder additionally carries an ``_OFAC_EXCLUDED``
  belt-and-suspenders set against candidates-file mutation.
* **F-38 (GB OGL attribution).** Preserve the candidates-file posture
  verbatim: ``license_posture: "needs-legal-review"`` plus a
  ``pending_decision_memo`` body naming options A / B / C. Jesse +
  counsel pick; this builder does not.

See the data-requirements spec §1.16 for
the full cross-issuer findings (ICAO 9303 9-character MRZ cap, no
book-level check digit, US "Passport Book Number" terminology
collision). Determinism rules apply: no wall-clock
content; ``generated_date`` is lifted verbatim from the candidates
file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/passport_patterns"
_SCHEMA_VERSION: Final[int] = 1

# The candidates file lives beside this builder under ``sources/``
# (raw inputs under ``src/resecta_data/<module>/sources/``).
# Keeping it out of ``build/`` makes it immutable-by-convention and
# safe from ``make clean`` / ``make verify``'s determinism rewipe.
_CANDIDATES_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "sources" / "passport_patterns_candidates.json"
)

_EXPECTED_SHIPPING_ISSUERS: Final[frozenset[str]] = frozenset(
    {"CA", "CN", "DO", "GB", "IN", "KR", "MX", "PH", "SV", "US", "VN"}
)

# Issuers whose ``candidate_only: true`` flag in the candidates file is
# overridden by a subsequent F-item resolution. DO: F-36 option (b) —
# "ship 11 rows with DR filling CU-freed slot." The candidates file
# predates F-36 closure; this override promotes DO without mutating it.
_POST_F36_PROMOTED: Final[frozenset[str]] = frozenset({"DO"})

# Belt-and-suspenders guard against candidates-file mutation flipping
# CU's ``exclude_from_ship`` flag. The primary gate is ``_is_shipping``'s
# ``exclude_from_ship`` check; this set is a defence-in-depth backstop.
_OFAC_EXCLUDED: Final[frozenset[str]] = frozenset({"CU"})

# Audit-only row keys produced by the candidates-file workflow. Stripped
# from every shipping row before emission — shipping artifact schema is
# strict (``additionalProperties: false``).
_AUDIT_ONLY_ROW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "candidate_only",
        "exclude_from_ship",
        "exclusion_reason",
        "format_attestation",
        "ship_loose_vs_omit_analysis",
    }
)


def _is_shipping(entry: dict[str, Any]) -> bool:
    """Return True iff ``entry`` ships in the V1 artifact."""
    if entry.get("exclude_from_ship") is True:
        return False
    if entry.get("issuer_code") in _OFAC_EXCLUDED:
        return False
    if entry.get("candidate_only") is True:
        return entry.get("issuer_code") in _POST_F36_PROMOTED
    return True


def _strip_audit(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop audit-only keys so the row conforms to the shipping schema."""
    return {k: v for k, v in entry.items() if k not in _AUDIT_ONLY_ROW_KEYS}


def build(seed: int, *, candidates_path: Path | None = None) -> dict[str, Any]:
    """Return the passport-patterns shipping payload.

    Args:
        seed: PRNG seed. Recorded for reproducibility; the builder is a
            deterministic filter-and-sort over the candidates file and
            does not consume randomness.
        candidates_path: Override for the candidates JSON location.
            Defaults to the file shipped alongside this builder under
            ``sources/``; tests pass a tmp path.

    Returns:
        A payload dict conforming to
        ``schemas/passport_patterns.schema.json``. Rows are sorted by
        ``issuer_code`` ascending.

    Raises:
        PipelineError: If the candidates file is missing or malformed,
            or if the filtered shipping set diverges from the 11-issuer
            F-36 ship list. Fail-loud.
    """
    path = candidates_path if candidates_path is not None else _CANDIDATES_PATH
    data = load_json(path)
    if not isinstance(data, dict) or "entries" not in data:
        raise PipelineError(
            f"passport_patterns: candidates file {path} is malformed "
            "(expected a dict with an 'entries' key)."
        )

    entries = data["entries"]
    if not isinstance(entries, list):
        raise PipelineError(f"passport_patterns: candidates file {path} 'entries' is not a list.")

    shipping = [_strip_audit(entry) for entry in entries if _is_shipping(entry)]
    shipping.sort(key=lambda entry: entry["issuer_code"])

    actual_issuers = frozenset(entry["issuer_code"] for entry in shipping)
    if actual_issuers != _EXPECTED_SHIPPING_ISSUERS:
        missing = sorted(_EXPECTED_SHIPPING_ISSUERS - actual_issuers)
        extra = sorted(actual_issuers - _EXPECTED_SHIPPING_ISSUERS)
        raise PipelineError(
            "passport_patterns: shipping issuer set diverges from F-36 close. "
            f"Expected {sorted(_EXPECTED_SHIPPING_ISSUERS)}, "
            f"got {sorted(actual_issuers)}. "
            f"Missing: {missing}. Extra: {extra}. "
            "This indicates a candidates-file change that warrants human review; "
            "see the data-requirements spec §1.16 + §7.4."
        )

    expected_count = 11
    if len(shipping) != expected_count:
        raise PipelineError(
            f"passport_patterns: expected {expected_count} shipping rows, "
            f"got {len(shipping)}. See F-36 ship list."
        )

    generated_date = data.get("generated_date")
    if not isinstance(generated_date, str):
        raise PipelineError(
            f"passport_patterns: candidates file {path} missing 'generated_date' string."
        )

    source_briefs = data.get("source_briefs")
    if not isinstance(source_briefs, list) or not all(isinstance(s, str) for s in source_briefs):
        raise PipelineError(
            f"passport_patterns: candidates file {path} 'source_briefs' must be a list of strings."
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "generated_date": generated_date,
        "seed": seed,
        "source_briefs": list(source_briefs),
        "rows": shipping,
    }
