"""Schema, floor, and scope-coverage checks for negative_context_candidates.json."""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.cli import INSTALL_ROUTES, SCHEMA_ROUTES
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.negative_context import build as build_neg
from resecta_data.gazetteers.negative_context._scope_rules import (
    _AUDIT_REMOVE_PER_SOURCE,
    _MANUAL_AUDIT_PART3,
    _MANUAL_GENERIC,
    _MANUAL_MEDICAL,
    manual_entries,
    scope_keyword,
)

_SOURCES = (
    Path(__file__).parent.parent
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "sources"
    / "negative_context"
)
_SCHEMAS = Path(__file__).parent.parent / "schemas"

# Swift-supported negative-context categories per
# NegativeContextGazetteer.swift:82-95 (see findings L5 "Swift-side category
# support" and decisions log M7). ``medicalRecord`` is omitted per M7's
# scope-to-ssn-only resolution.
_SWIFT_SUPPORTED_SCOPES: frozenset[str] = frozenset(
    {"ssn", "npi", "dea", "name", "address", "dob", "account"}
)


@pytest.fixture
def built_payload() -> dict[str, object]:
    return build_neg(CANONICAL_SEED, source_dir=_SOURCES)


def test_matches_schema(tmp_build_dir: Path, built_payload: dict[str, object]) -> None:
    dest = tmp_build_dir / "negative_context_candidates.json"
    dump_canonical_json(built_payload, dest)
    validate_file(dest, _SCHEMAS, "negative_context")


def test_precedence_weight_respects_a1_floor(built_payload: dict[str, object]) -> None:
    for entry in built_payload["entries"]:  # type: ignore[attr-defined]
        assert entry["precedence_weight"] >= 0.25, entry


def test_every_doctype_scope_is_represented(built_payload: dict[str, object]) -> None:
    """Our three sources cover court / foia / financial — all three should appear."""
    doctypes = {e["doctype_scope"] for e in built_payload["entries"]}  # type: ignore[attr-defined]
    assert {"court", "foia", "financial"} <= doctypes


def test_builder_is_deterministic(built_payload: dict[str, object]) -> None:
    again = build_neg(CANONICAL_SEED, source_dir=_SOURCES)
    assert again == built_payload


def test_entries_are_sorted() -> None:
    payload = build_neg(CANONICAL_SEED, source_dir=_SOURCES)
    entries = payload["entries"]
    keys = [(e["keyword"], e["category_scope"], e["doctype_scope"]) for e in entries]
    assert keys == sorted(keys)


# -----------------------------------------------------------------------------
# Hand-curated MEDICAL + GENERIC + AUDIT_PART3 entries (C5 + A5 audit)
# -----------------------------------------------------------------------------


# Source-derived bootstrap pools; everything else in the payload is from the
# manual path (`_MANUAL_MEDICAL`, `_MANUAL_GENERIC`, `_MANUAL_AUDIT_PART3`).
# After A5 audit (1.md), most manual rows carry a per-Entry primary-source
# `source_id` (e.g. `nucc_1500_v13`) instead of the synthetic bucket id.
_BOOTSTRAP_SOURCE_IDS: frozenset[str] = frozenset(
    {"us_courts_glossary", "doj_legal_glossary", "ubl_invoice_labels"}
)


def test_manual_medical_determinism(built_payload: dict[str, object]) -> None:
    """Rebuilding twice produces byte-identical manual entries.

    The full determinism check in ``test_builder_is_deterministic`` covers
    the whole payload; this test narrows to the manual rows so a regression
    in the manual path surfaces with a focused assertion.
    """
    again = build_neg(CANONICAL_SEED, source_dir=_SOURCES)
    first_manual = [
        e
        for e in built_payload["entries"]  # type: ignore[attr-defined]
        if e["source_id"] not in _BOOTSTRAP_SOURCE_IDS
    ]
    second_manual = [e for e in again["entries"] if e["source_id"] not in _BOOTSTRAP_SOURCE_IDS]
    assert first_manual == second_manual
    # Sanity: the manual rows actually landed in the payload.
    assert len(first_manual) > 0


def test_manual_medical_schema(tmp_build_dir: Path) -> None:
    """A build containing manual entries validates against the candidates schema."""
    payload = build_neg(CANONICAL_SEED, source_dir=_SOURCES)
    manual_rows = [e for e in payload["entries"] if e["source_id"] not in _BOOTSTRAP_SOURCE_IDS]
    medical_rows = [e for e in manual_rows if e["doctype_scope"] == "medical"]
    generic_rows = [e for e in manual_rows if e["doctype_scope"] == "generic"]
    assert len(medical_rows) > 0, "manual medical rows missing"
    assert len(generic_rows) > 0, "manual generic rows missing"

    dest = tmp_build_dir / "negative_context_candidates.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "negative_context")


def test_manual_entries_scopes_valid() -> None:
    """Every manual ``category_scopes`` element is a Swift-supported category.

    Per findings L5 §"Swift-side category support" and decisions log M7:
    the Swift ``wireName`` switch recognises ssn, npi, dea, name, address,
    dob, account. ``medicalRecord`` is deliberately excluded (M7 =
    scope-to-ssn-only, 2026-04-18).
    """
    for entry in (*_MANUAL_MEDICAL, *_MANUAL_GENERIC):
        for category in entry.category_scopes:
            assert category in _SWIFT_SUPPORTED_SCOPES, (
                f"{entry.keyword!r}: category_scope {category!r} is not "
                f"one of the Swift-supported scopes {_SWIFT_SUPPORTED_SCOPES}"
            )
        assert category != "medicalRecord", (
            "M7 = scope-to-ssn-only forbids medicalRecord scope in v1"
        )


def test_manual_medical_doctype_and_weight_ranges() -> None:
    """Medical entries use doctype_scope='medical', weight in [0.30, 0.65].

    Range expanded post-A5-audit (1.md): MRN moves up to 0.65 and
    room/bed/barcode/billing-code drop to 0.30 after re-scoping.
    """
    for entry in _MANUAL_MEDICAL:
        assert entry.doctype_scope == "medical", entry
        assert 0.30 <= entry.weight <= 0.65, entry


def test_manual_generic_doctype_and_weight() -> None:
    """Generic entries use doctype_scope='generic', weight in [0.30, 0.50].

    Range expanded post-A5-audit (1.md): tracking/confirmation move to 0.50,
    model number drops to 0.30, support/request lower to 0.35.
    """
    for entry in _MANUAL_GENERIC:
        assert entry.doctype_scope == "generic", entry
        assert 0.30 <= entry.weight <= 0.50, entry


def test_manual_entries_counts() -> None:
    """A5-audit-shaped manual lists with bounded counts.

    Bounds (not exact equality) so future audit tweaks don't keep flipping
    the test. Pre-audit counts were 25/15; post-audit bands account for
    +1 medical (account number) and +1 generic (reservation number).
    """
    assert 20 <= len(_MANUAL_MEDICAL) <= 30, len(_MANUAL_MEDICAL)
    assert 12 <= len(_MANUAL_GENERIC) <= 20, len(_MANUAL_GENERIC)


def test_audit_part3_present(built_payload: dict[str, object]) -> None:
    """A5 audit Part 3 ships at least 40 entries spanning 15+ cells (1.md)."""
    assert len(_MANUAL_AUDIT_PART3) >= 40, len(_MANUAL_AUDIT_PART3)
    cells = {(entry.category_scopes, entry.doctype_scope) for entry in _MANUAL_AUDIT_PART3}
    assert len(cells) >= 15, sorted(cells)
    part3_rows = [
        e
        for e in built_payload["entries"]  # type: ignore[attr-defined]
        if e["source_id"] == "manual_audit_part3"
        or e["source_id"]
        in {
            # Per-Entry source_ids that win over the bucket default per
            # _scope_rules.manual_entries(); ensure the ingest path emitted
            # at least some of them so the bucket plumbing is wired.
            "cms_iom_pub100_04_ch1",
            "cms_mbi_format",
            "five_usc_552",
            "frcp_rule_5_2",
        }
    ]
    assert part3_rows, "manual_audit_part3 ingest produced no rows"


def test_audit_remove_short_circuits() -> None:
    """Every keyword in _AUDIT_REMOVE_PER_SOURCE returns [] from scope_keyword."""
    for source_id, removed in _AUDIT_REMOVE_PER_SOURCE.items():
        for keyword in removed:
            assert scope_keyword(keyword, source_id) == [], (source_id, keyword)


def test_no_account_scope_in_v1(built_payload: dict[str, object]) -> None:
    """Schema enum lacks 'account' until Session 2's paired Swift PR.

    Defensive guard locking the M7 scope-to-ssn-only resolution: any
    candidate that the research routes to an account-number semantic must
    stay scoped to ``ssn`` in Session 1. Failure here means a Part 3 entry
    leaked an out-of-enum category.
    """
    accounts = [
        e
        for e in built_payload["entries"]  # type: ignore[attr-defined]
        if e["category_scope"] == "account"
    ]
    assert accounts == [], accounts


def test_manual_entries_unique_surface_forms() -> None:
    """No two Entries share a (surface_form, category, doctype) triple."""
    rows = manual_entries()
    triples = [(keyword, category, doctype) for keyword, category, doctype, *_ in rows]
    assert len(triples) == len(set(triples)), "duplicate manual surface-form triple"


def test_manual_medical_and_generic_doctypes_represented(
    built_payload: dict[str, object],
) -> None:
    """medical and generic doctypes should both appear in the payload now."""
    doctypes = {e["doctype_scope"] for e in built_payload["entries"]}  # type: ignore[attr-defined]
    assert "medical" in doctypes
    assert "generic" in doctypes


def test_candidates_file_not_installed() -> None:
    """The candidates file is build-only.

    ``SCHEMA_ROUTES`` contains the candidates file (schema validation runs
    on it) but ``INSTALL_ROUTES`` must not — Jesse hand-reviews candidates
    before the installed ``negative_context.json`` is updated.
    """
    candidates_key = "gazetteers/negative_context_candidates.json"
    assert candidates_key in SCHEMA_ROUTES, f"{candidates_key} should be schema-validated"
    assert candidates_key not in INSTALL_ROUTES, (
        f"{candidates_key} must NOT be installed — §2.2 requires Jesse's "
        "hand-review before anything under Packages/.../Resources/ is "
        "updated. Found it in INSTALL_ROUTES."
    )


# -----------------------------------------------------------------------------
# S3 D6 reviewed/ path existence checks (design 03 §0.5)
#
# These tests assert that the committed reviewed files exist in
# src/resecta_data/gazetteers/negative_context/reviewed/. The reviewed file
# lands after the Jesse gate later this session; the tests are intentionally
# RED right now and will turn GREEN once Jesse approves and the reviewed
# file is committed.
# -----------------------------------------------------------------------------

_REVIEWED_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "negative_context"
    / "reviewed"
)


def test_reviewed_file_exists_in_sources() -> None:
    """The reviewed negative_context.json and its meta sidecar exist in reviewed/.

    Per design 03 §0.5: the committed reviewed file is the source-of-truth;
    the stage-reviewed-negctx make target copies it to build/. This test
    will be RED until Jesse approves the candidates diff and commits the
    reviewed file. That is expected; do not skip or xfail it.
    """
    reviewed_json = _REVIEWED_DIR / "negative_context.json"
    reviewed_meta = _REVIEWED_DIR / "negative_context.meta.json"
    assert reviewed_json.exists(), (
        f"Reviewed file not yet committed: {reviewed_json}. Jesse gate required before staging."
    )
    assert reviewed_meta.exists(), (
        f"Reviewed meta sidecar not yet committed: {reviewed_meta}. "
        "Jesse gate required before staging."
    )
