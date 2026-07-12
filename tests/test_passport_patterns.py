"""Tests for the per-country passport-pattern gazetteer (D-14).

Covers schema conformance, determinism, the closed 11-issuer F-36 ship
set, the F-37 CU exclusion, the F-38 GB needs-legal-review posture, and
the SV MEDIUM-confidence ceiling with post-V1 follow-up tasks. See
``src/resecta_data/gazetteers/passport_patterns/build.py`` for the
builder and ``schemas/passport_patterns.schema.json`` for the shape.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.exceptions import PipelineError, SchemaValidationError
from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import load_schema, validate, validate_file
from resecta_data.gazetteers.passport_patterns import build as build_passport_patterns

REPO_ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
CANDIDATES_PATH = (
    REPO_ROOT
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "passport_patterns"
    / "sources"
    / "passport_patterns_candidates.json"
)

_CANONICAL_SEED = 20260416

_EXPECTED_SHIPPING_ISSUERS = frozenset(
    {"CA", "CN", "DO", "GB", "IN", "KR", "MX", "PH", "SV", "US", "VN"}
)

_EXPECTED_SHIPPING_COUNT = 11

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture
def payload() -> dict[str, Any]:
    """Build the shipping payload once per test that needs it."""
    return build_passport_patterns(_CANONICAL_SEED)


def test_artifact_exists_and_parses(payload: dict[str, Any], tmp_build_dir: Path) -> None:
    """Dumping via the canonical writer yields a valid JSON file."""
    dest = tmp_build_dir / "gazetteers" / "passport_patterns.json"
    dump_canonical_json(payload, dest)
    assert dest.is_file()
    loaded = load_json(dest)
    assert loaded == payload


def test_schema_conformance(payload: dict[str, Any], tmp_build_dir: Path) -> None:
    """Payload validates against schemas/passport_patterns.schema.json."""
    dest = tmp_build_dir / "gazetteers" / "passport_patterns.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "passport_patterns")


def test_determinism(tmp_build_dir: Path) -> None:
    """Two consecutive builds produce byte-identical output."""
    first = build_passport_patterns(_CANONICAL_SEED)
    second = build_passport_patterns(_CANONICAL_SEED)
    assert first == second

    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(first, path_a)
    dump_canonical_json(second, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_row_count_is_eleven(payload: dict[str, Any]) -> None:
    """F-36 closed with exactly 11 shipping rows."""
    assert len(payload["rows"]) == _EXPECTED_SHIPPING_COUNT


def test_shipping_issuer_set(payload: dict[str, Any]) -> None:
    """Shipping set == {CA, CN, DO, GB, IN, KR, MX, PH, SV, US, VN}."""
    actual = {row["issuer_code"] for row in payload["rows"]}
    assert actual == _EXPECTED_SHIPPING_ISSUERS


def test_cu_never_shipped(payload: dict[str, Any]) -> None:
    """F-37 hard gate: Cuba is omitted entirely on OFAC sanctions grounds."""
    issuer_codes = {row["issuer_code"] for row in payload["rows"]}
    assert "CU" not in issuer_codes, (
        "CU appeared in the shipping artifact. F-37 CLOSED 2026-04-23 "
        "excludes Cuba entirely; check candidates-file exclude_from_ship gate."
    )


def test_gt_never_shipped(payload: dict[str, Any]) -> None:
    """GT is candidate_only per F-36; shipping GT in V1 is a scope error."""
    issuer_codes = {row["issuer_code"] for row in payload["rows"]}
    assert "GT" not in issuer_codes


def test_gb_marked_needs_legal_review(payload: dict[str, Any]) -> None:
    """F-38 OGL attribution posture: GB ships with pending_decision_memo."""
    gb = next(row for row in payload["rows"] if row["issuer_code"] == "GB")
    assert gb["license_posture"] == "needs-legal-review"
    memo = gb["pending_decision_memo"]
    assert memo["f_item"] == "F-38"
    # Options A / B / C must all be present — the memo is the hand-off
    # to Jesse + counsel.
    options_blob = " ".join(memo["options"])
    for marker in ("A -- ", "B -- ", "C -- "):
        assert marker in options_blob, f"GB F-38 memo is missing option marker {marker!r}"


def test_sv_medium_confidence_with_tasks(payload: dict[str, Any]) -> None:
    """SV is capped at MEDIUM with V-ES-1/2/3 post-V1 follow-ups."""
    sv = next(row for row in payload["rows"] if row["issuer_code"] == "SV")
    assert sv["confidence"] == "medium"
    tasks_blob = " ".join(sv["post_v1_tasks"])
    for task_id in ("V-ES-1", "V-ES-2", "V-ES-3"):
        assert task_id in tasks_blob, f"SV row is missing {task_id} in post_v1_tasks"


def test_issuer_code_sort_order(payload: dict[str, Any]) -> None:
    """Rows are emitted in ascending issuer_code order."""
    codes = [row["issuer_code"] for row in payload["rows"]]
    assert codes == sorted(codes)
    assert codes == ["CA", "CN", "DO", "GB", "IN", "KR", "MX", "PH", "SV", "US", "VN"]


def test_all_patterns_anchored(payload: dict[str, Any]) -> None:
    """Every regex in every row is anchored with ^ and $."""
    for row in payload["rows"]:
        for pattern in row["patterns"]:
            assert pattern.startswith("^"), (
                f"{row['issuer_code']} pattern {pattern!r} is not ^-anchored"
            )
            assert pattern.endswith("$"), (
                f"{row['issuer_code']} pattern {pattern!r} is not $-anchored"
            )


def test_source_verified_date_iso_format(payload: dict[str, Any]) -> None:
    """Every source_verified_date is YYYY-MM-DD."""
    for row in payload["rows"]:
        date = row["source_verified_date"]
        assert _ISO_DATE.match(date), (
            f"{row['issuer_code']} source_verified_date {date!r} is not ISO YYYY-MM-DD"
        )


def test_no_wall_clock_leak(payload: dict[str, Any]) -> None:
    """generated_date is lifted from the candidates file, not from wall-clock."""
    candidates = load_json(CANDIDATES_PATH)
    assert payload["generated_date"] == candidates["generated_date"]
    # Candidates file is authored 2026-04-23; pin the expectation so a
    # wall-clock regression is caught even if the candidates file drifts.
    assert payload["generated_date"] == "2026-04-23"


def test_seed_recorded(payload: dict[str, Any]) -> None:
    """The CLI-supplied seed is recorded verbatim for reproducibility."""
    assert payload["seed"] == _CANONICAL_SEED


def test_source_briefs_verbatim(payload: dict[str, Any]) -> None:
    """source_briefs is copied verbatim from the candidates file."""
    candidates = load_json(CANDIDATES_PATH)
    assert payload["source_briefs"] == candidates["source_briefs"]


def test_no_audit_only_keys_leak(payload: dict[str, Any]) -> None:
    """Audit keys (candidate_only, exclude_from_ship, ...) never land in rows."""
    banned = {
        "candidate_only",
        "exclude_from_ship",
        "exclusion_reason",
        "format_attestation",
        "ship_loose_vs_omit_analysis",
    }
    for row in payload["rows"]:
        leaked = banned & set(row)
        assert not leaked, f"{row['issuer_code']} row leaks audit key(s): {leaked}"


def test_check_digit_policy_uniform(payload: dict[str, Any]) -> None:
    """Every row carries 'none known' per the ICAO 9303 cross-issuer finding."""
    for row in payload["rows"]:
        assert row["check_digit_policy"] == "none known"


def test_license_posture_distribution(payload: dict[str, Any]) -> None:
    """1 §105 PD (US) + 9 gov-work Feist + 1 needs-legal-review (GB), post-F-36."""
    postures = [row["license_posture"] for row in payload["rows"]]
    us_count = 1
    feist_count = 9
    review_count = 1
    assert postures.count("§105 PD") == us_count
    assert postures.count("gov-work (Feist)") == feist_count
    assert postures.count("needs-legal-review") == review_count


def test_do_promoted_despite_candidate_only_flag() -> None:
    """Dominican Republic (DO) ships via F-36 post-closure override.

    The candidates file predates F-36 closure and still carries DO as
    ``candidate_only: true``. The builder's post-F-36 promotion set
    promotes DO without mutating the candidates file. This test guards
    the override so a future candidates-file regen that flips DO's flag
    to ``false`` does not silently remove the override.
    """
    candidates = load_json(CANDIDATES_PATH)
    do_entry = next(e for e in candidates["entries"] if e["issuer_code"] == "DO")
    # Snapshot the candidates-file state so a regen that flips this flag
    # forces a reviewer to look at the override logic.
    assert do_entry.get("candidate_only") is True, (
        "DO's candidate_only flag is no longer True in the candidates file. "
        "Revisit the _POST_F36_PROMOTED override in build.py — if the candidates "
        "regen promoted DO, the override is redundant and should be removed."
    )

    payload = build_passport_patterns(_CANONICAL_SEED)
    assert "DO" in {row["issuer_code"] for row in payload["rows"]}


def test_schema_rejects_gb_without_pending_decision_memo(payload: dict[str, Any]) -> None:
    """Schema ``if/then`` forces GB to carry ``pending_decision_memo`` (F-38)."""
    mutated = {**payload, "rows": [dict(row) for row in payload["rows"]]}
    gb = next(row for row in mutated["rows"] if row["issuer_code"] == "GB")
    del gb["pending_decision_memo"]
    schema = load_schema(SCHEMAS_DIR, "passport_patterns")
    with pytest.raises(SchemaValidationError, match="pending_decision_memo"):
        validate(mutated, schema)


def test_schema_rejects_sv_without_post_v1_tasks(payload: dict[str, Any]) -> None:
    """Schema ``if/then`` forces SV to carry ``post_v1_tasks`` (MEDIUM ceiling)."""
    mutated = {**payload, "rows": [dict(row) for row in payload["rows"]]}
    sv = next(row for row in mutated["rows"] if row["issuer_code"] == "SV")
    del sv["post_v1_tasks"]
    schema = load_schema(SCHEMAS_DIR, "passport_patterns")
    with pytest.raises(SchemaValidationError, match="post_v1_tasks"):
        validate(mutated, schema)


def test_builder_raises_when_candidates_file_missing(tmp_path: Path) -> None:
    """Fail-loud: missing candidates file is a build error."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(PipelineError, match="not found"):
        build_passport_patterns(_CANONICAL_SEED, candidates_path=missing)


def test_builder_raises_when_issuer_set_diverges(tmp_path: Path) -> None:
    """Promoting GT (candidate-only per F-36) trips the shipping-set guard."""
    candidates = load_json(CANDIDATES_PATH)
    mutated = {**candidates, "entries": [dict(e) for e in candidates["entries"]]}
    gt = next(e for e in mutated["entries"] if e["issuer_code"] == "GT")
    gt["candidate_only"] = False
    mutated_path = tmp_path / "candidates.json"
    dump_canonical_json(mutated, mutated_path)
    with pytest.raises(PipelineError, match="F-36"):
        build_passport_patterns(_CANONICAL_SEED, candidates_path=mutated_path)
