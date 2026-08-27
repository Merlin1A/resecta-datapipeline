"""Tests for the rule catalog.

Covers the cross-repo acceptance check (every Swift detector maps to a row) plus the
structural / determinism guarantees:

* **W-I1 acceptance** — every Swift detector symbol that emits into
  ``MatchAuditRecord.ruleID`` under
  ``resecta/Packages/RedactionEngine/Sources/RedactionEngine/Detection/``
  maps to ≥ 1 catalog row. The test scans the Detection/ tree for
  ``ruleID:`` emit-sites and cross-checks against the
  ``_emitted_rule_id`` staging fields on the source catalog. If a new
  Swift detector lands with a new emit-string and no catalog row, the
  test fails until either (a) a row is added or (b) the new emit-site
  reuses an existing rule_id.
* **Closed count** — ships exactly 21 rows: the original 19 (17
  PIIDetector.detect detectors, with the MRN detector contributing 3
  rows per the session-2 split decision), plus the DRAW-2/DRAW-3 visual
  detectors pii.barcode.vision.v1 / pii.signature.heuristic.v1, minus
  pii.bates.v1 (Bates detection dropped engine-side by the
  general-purpose pivot, Resecta PR #80) -- both tracked by the
  2026-06-10 W-I1 reconciliation, plus pii.routing_number.v1 added by
  the routing-number detector (2026-06-11).
* **rule_id pattern** — every ``rule_id`` matches the schema regex.
* **uniqueness** — no two rows share a ``rule_id``.
* **determinism** — two ``build()`` calls with the same seed return
  byte-identical canonical-JSON output.
* **strict schema** — ``additionalProperties: false`` enforced; staging
  fields stripped from the wire payload.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import validate_file
from resecta_data.rules import build as build_rule_catalog

_RULE_ID_PATTERN = re.compile(r"^pii\.[a-z_]+(\.[a-z_]+)?\.v[0-9]+$")
_EXPECTED_ROW_COUNT = 21

_SOURCE_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "resecta_data"
    / "rules"
    / "sources"
    / "rule_catalog.json"
)
_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

# Resecta sibling repo root (peer of DataPipeline). The W-I1 test reads
# the Swift Detection/ tree from here. When running outside the maintainer's local
# layout (CI, contributor checkouts) the sibling may be absent — the test
# skips rather than fails so non-Resecta environments aren't blocked.
_RESECTA_DETECTION_DIR = (
    Path(__file__).parent.parent.parent
    / "Resecta"
    / "Packages"
    / "RedactionEngine"
    / "Sources"
    / "RedactionEngine"
    / "Detection"
)

# Match Swift emit-sites of three shapes:
#
#   1. ``ruleID: "<value>"`` (call-site argument) and
#      ``ruleID = "<value>"`` (local-var assignment). Captures the
#      quoted literal directly.
#   2. ``case .<kind>: "<value>"`` arms inside the
#      ``defaultRuleID(for:)`` switch body — the kind→ruleID mapping
#      lives there for detectors invoked through
#      ``PIIDetector.detect``, so the ruleID literals never appear
#      next to a literal "ruleID" token. The arm regex is loose by
#      design — we run it only inside the ``defaultRuleID`` function
#      span so it can't false-positive on unrelated switch statements.
#   3. Tuple-list patterns of the form ``(Self.<regex>, "<value>")``
#      — used by ``detectMedicalRecords`` to fan multiple regexes out
#      against a list of (pattern, ruleID) pairs. Captures the second
#      element of each such tuple. Narrow enough not to false-positive
#      on unrelated tuples because of the ``Self.<name>`` first element.
_DIRECT_EMIT_RE = re.compile(r'\bruleID(?:\s*[:=])\s*"([^"]+)"')
_DEFAULT_RULE_ID_FN_RE = re.compile(
    r"func\s+defaultRuleID\b[^{]*\{(.*?)^\s*\}",
    re.DOTALL | re.MULTILINE,
)
_SWITCH_ARM_STRING_RE = re.compile(r'case\s+\.\w+\s*:\s*"([^"]+)"')
_TUPLE_PATTERN_EMIT_RE = re.compile(r'\(\s*Self\.\w+\s*,\s*"([^"]+)"\s*\)')


def _source_emitted_rule_ids() -> set[str]:
    """Return the set of every Swift emit-string declared by the source catalog.

    The ``_emitted_rule_id`` staging field on each row is either a single
    string (one Swift ruleID per row) or a comma-separated list (rolled-up
    rows that cover multiple Swift emit-sites). This collapses both shapes
    into one set.
    """
    source = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    emitted: set[str] = set()
    for row in source:
        raw = row["_emitted_rule_id"]
        for piece in raw.split(","):
            stripped = piece.strip()
            if stripped:
                emitted.add(stripped)
    return emitted


def _swift_emitted_rule_ids() -> set[str]:
    """Return the set of every literal ruleID string emitted under Detection/.

    Walks the Swift Detection/ tree and collects every distinct value
    appearing as either:

    * a direct ``ruleID: "<x>"`` / ``ruleID = "<x>"`` source position; or
    * a ``case .<kind>: "<x>"`` arm inside the ``defaultRuleID(for:)``
      switch (which is how PIIDetector.detect-invoked detectors map
      PIIKind to a ruleID string).
    """
    found: set[str] = set()
    for path in sorted(_RESECTA_DETECTION_DIR.rglob("*.swift")):
        text = path.read_text(encoding="utf-8")
        for match in _DIRECT_EMIT_RE.finditer(text):
            found.add(match.group(1))
        for fn_match in _DEFAULT_RULE_ID_FN_RE.finditer(text):
            body = fn_match.group(1)
            for arm in _SWITCH_ARM_STRING_RE.finditer(body):
                found.add(arm.group(1))
        for tuple_match in _TUPLE_PATTERN_EMIT_RE.finditer(text):
            found.add(tuple_match.group(1))
    return found


# -----------------------------------------------------------------------------
# Structural guarantees
# -----------------------------------------------------------------------------


def test_builder_emits_expected_count() -> None:
    """Catalog ships exactly 21 rows (19 original + 2 DRAW-2/DRAW-3 visual
    detectors - 1 Bates + 1 routing-number)."""
    payload = build_rule_catalog(CANONICAL_SEED)
    assert len(payload["entries"]) == _EXPECTED_ROW_COUNT


def test_every_rule_id_matches_pattern() -> None:
    """Every rule_id matches `^pii\\.[a-z_]+(\\.[a-z_]+)?\\.v[0-9]+$`."""
    payload = build_rule_catalog(CANONICAL_SEED)
    for row in payload["entries"]:
        assert _RULE_ID_PATTERN.match(row["rule_id"]), row["rule_id"]


def test_rule_ids_are_unique() -> None:
    payload = build_rule_catalog(CANONICAL_SEED)
    rule_ids = [row["rule_id"] for row in payload["entries"]]
    assert len(set(rule_ids)) == len(rule_ids), rule_ids


def test_entries_sorted_by_rule_id() -> None:
    """Builder sorts deterministically; row order must match sorted(rule_id)."""
    payload = build_rule_catalog(CANONICAL_SEED)
    rule_ids = [row["rule_id"] for row in payload["entries"]]
    assert rule_ids == sorted(rule_ids)


def test_wire_payload_strips_staging_fields() -> None:
    """Underscore-prefixed staging fields must not leak into the shipping payload."""
    payload = build_rule_catalog(CANONICAL_SEED)
    allowed = {
        "rule_id",
        "family",
        "detector",
        "version",
        "source_artifact",
        "is_checksum_gated",
    }
    for row in payload["entries"]:
        assert set(row.keys()) == allowed, row


def test_family_is_pii() -> None:
    """V1 ships PII detection only; family enum is closed at 'pii'."""
    payload = build_rule_catalog(CANONICAL_SEED)
    assert {row["family"] for row in payload["entries"]} == {"pii"}


def test_version_baseline_is_one_zero() -> None:
    """V1 baseline is `1.0` for every row; per-row bumps come later."""
    payload = build_rule_catalog(CANONICAL_SEED)
    for row in payload["entries"]:
        assert row["version"] == "1.0", row


# -----------------------------------------------------------------------------
# Determinism
# -----------------------------------------------------------------------------


@pytest.mark.determinism
def test_builder_is_deterministic(tmp_build_dir: Path) -> None:
    """Two builds with the same seed produce byte-identical canonical JSON."""
    payload_a = build_rule_catalog(CANONICAL_SEED)
    payload_b = build_rule_catalog(CANONICAL_SEED)
    assert payload_a == payload_b

    path_a = tmp_build_dir / "a_rule_catalog.json"
    path_b = tmp_build_dir / "b_rule_catalog.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


# -----------------------------------------------------------------------------
# Schema (additionalProperties: false enforced by the wire shape)
# -----------------------------------------------------------------------------


def test_built_payload_validates_against_schema(tmp_build_dir: Path) -> None:
    payload = build_rule_catalog(CANONICAL_SEED)
    out = tmp_build_dir / "rule_catalog.json"
    dump_canonical_json(payload, out)
    validate_file(out, _SCHEMAS_DIR, "rule_catalog")
    # Sanity: round-tripped payload still has 20 entries with the canonical seed.
    loaded = load_json(out)
    assert loaded["seed"] == CANONICAL_SEED
    assert len(loaded["entries"]) == _EXPECTED_ROW_COUNT


# -----------------------------------------------------------------------------
# Cross-repo acceptance: every Swift detector emit string maps to a catalog row
# -----------------------------------------------------------------------------


@pytest.mark.skipif(
    not _RESECTA_DETECTION_DIR.exists(),
    reason="Resecta sibling not present; W-I1 cross-repo check requires Detection/",
)
def test_w_i1_every_swift_emit_string_has_a_catalog_row() -> None:
    """Every Swift detector symbol that emits a ruleID maps to a catalog row.

    Surfaces drift in either direction:

    * If a new Swift detector lands with a new ``ruleID: "..."`` emit-site
      and no catalog row is added, this test fails until the catalog
      catches up.
    * If the catalog drops an ``_emitted_rule_id`` that the Swift tree
      still emits, this test fails likewise.

    The audit / ICN / DD surface (``audit.*`` / ``icn.*`` / ``dd.*``) is
    permitted to appear in Swift without a V1 catalog row — those rows ship
    with their own chain.
    """
    swift_emitted = _swift_emitted_rule_ids()
    catalog_emitted = _source_emitted_rule_ids()

    # Deferred surface — exempt until that chain ships catalog rows.
    d36_prefixes = ("audit.", "icn.", "dd.")

    # Defensive fallback strings inside ``defaultRuleID(for:)`` that a
    # real detector always overrides before emitting. Concretely:
    #   * ``ssn.regex`` — SSN matches actually emit ``ssn.state-machine``
    #     (PIIDetector.swift line 374) via SSNStateMachine; the
    #     defaultRuleID arm for ``.ssn`` is unreachable.
    #   * ``mrn.regex`` — MRN matches always come through
    #     ``detectMedicalRecords`` and emit one of mrn.labeled /
    #     mrn.patientID / mrn.institution.
    #   * ``pii.other`` — the ``.other`` PIIKind is a sentinel never
    #     produced by a shipping detector.
    # These are surfaced here so a future audit can re-evaluate; if any
    # of the three becomes reachable, drop it from this set and add a
    # catalog row.
    unreachable_fallbacks = {"ssn.regex", "mrn.regex", "pii.other"}

    swift_in_v1_scope = {
        emit
        for emit in swift_emitted
        if not emit.startswith(d36_prefixes) and emit not in unreachable_fallbacks
    }

    missing = swift_in_v1_scope - catalog_emitted
    assert not missing, (
        "Swift Detection/ emits these ruleID strings with no catalog row: "
        f"{sorted(missing)}. Add a row to "
        "src/resecta_data/rules/sources/rule_catalog.json (and bump "
        "_EXPECTED_TOTAL in build.py) or, if the new emit-site should "
        "share an existing rule_id, fold the value into an existing "
        "row's _emitted_rule_id field."
    )

    stale = catalog_emitted - swift_emitted - unreachable_fallbacks
    assert not stale, (
        "Catalog declares _emitted_rule_id values that no Swift detector "
        f"under Detection/ currently emits: {sorted(stale)}. Either the "
        "Swift emit-site was renamed/removed (update or drop the catalog "
        "row) or the catalog typo'd the value."
    )


@pytest.mark.skipif(
    not _RESECTA_DETECTION_DIR.exists(),
    reason="Resecta sibling not present; detector-symbol cross-check requires Detection/",
)
def test_every_catalog_detector_resolves_to_a_swift_file() -> None:
    """Every distinct ``detector`` field names a Swift file in Detection/.

    PIIDetector hosts several detectors as instance methods, so the
    detector field there resolves to ``PIIDetector.swift`` itself.
    """
    payload = build_rule_catalog(CANONICAL_SEED)
    detectors = {row["detector"] for row in payload["entries"]}
    swift_files = {path.stem for path in _RESECTA_DETECTION_DIR.rglob("*.swift")}
    unresolved = detectors - swift_files
    assert not unresolved, (
        f"Catalog detector(s) with no matching <name>.swift under Detection/: {sorted(unresolved)}"
    )
