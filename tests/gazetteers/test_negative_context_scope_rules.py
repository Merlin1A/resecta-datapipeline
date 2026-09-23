"""The negative-context scope rules as data: the source file's shape and the loader's pins.

Coverage:
- The data file is pretty-printed UTF-8 (the non-ASCII rationale characters
  verbatim, never escaped) with a trailing newline, so a hand edit that
  reformats it shows as a diff.
- The four bucket ids appear in the loaded order and agree with the
  provenance block; no two manual entries share a surface-form triple.
- The pinned counts hold on the shipped file and a truncated copy raises.
- A malformed shape raises ``PipelineError``; a missing file raises
  ``MissingSourceError``.
- The three builder-facing functions read the loaded structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.gazetteers.negative_context._scope_rules import (
    EXPECTED_ENTRIES,
    EXPECTED_MANUAL_ROWS,
    EXPECTED_OVERRIDES,
    EXPECTED_REMOVED,
    Entry,
    ScopeRules,
    known_source_ids,
    load_scope_rules,
    manual_entries,
    scope_keyword,
)

SOURCE_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "negative_context"
    / "sources"
    / "scope_rules_v1.json"
)
BUCKET_ORDER = ("manual_medical", "manual_generic", "manual_audit_part3", "self_authored_20260610")


def _raw() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    return data


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# -----------------------------------------------------------------------------
# The data file
# -----------------------------------------------------------------------------


def test_source_file_is_pretty_printed_utf8_with_verbatim_code_points() -> None:
    text = SOURCE_PATH.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n"
    assert "\\u" not in text, "non-ASCII rationale characters must be stored verbatim"
    assert "§" in text


def test_source_file_provenance_and_bucket_order() -> None:
    data = _raw()
    provenance = data["provenance"]
    assert provenance["id"] == "scope_rules_v1"
    assert len(provenance["captured_from_commit"]) == 40
    assert provenance["note"].startswith("Provenance:")
    bucket_ids = tuple(b["bucket_source_id"] for b in data["manual_buckets"])
    assert bucket_ids == BUCKET_ORDER
    assert tuple(provenance["source_ids"].values()) == BUCKET_ORDER


def test_source_file_counts_match_the_pins() -> None:
    data = _raw()
    entries = [e for b in data["manual_buckets"] for e in b["entries"]]
    rows = sum((1 + len(e["aliases"])) * len(e["category_scopes"]) for e in entries)
    removed = sum(len(p["keywords"]) for p in data["audit_remove_per_source"])
    assert len(entries) == EXPECTED_ENTRIES == 117
    assert rows == EXPECTED_MANUAL_ROWS == 136
    assert len(data["keyword_overrides"]) == EXPECTED_OVERRIDES == 15
    assert removed == EXPECTED_REMOVED == 93
    assert len(data["source_defaults"]) == 3


def test_source_file_has_no_duplicate_surface_triple() -> None:
    triples = [
        (surface.lower(), category, e["doctype_scope"])
        for b in _raw()["manual_buckets"]
        for e in b["entries"]
        for surface in (e["keyword"], *e["aliases"])
        for category in e["category_scopes"]
    ]
    assert len(triples) == len(set(triples))


# -----------------------------------------------------------------------------
# The loader
# -----------------------------------------------------------------------------


def test_loader_reads_the_shipped_file() -> None:
    rules = load_scope_rules()
    assert isinstance(rules, ScopeRules)
    assert tuple(source_id for source_id, _ in rules.manual_buckets) == BUCKET_ORDER
    assert rules.manual_medical is rules.manual_buckets[0][1]
    assert rules.manual_generic is rules.manual_buckets[1][1]
    assert rules.manual_audit_part3 is rules.manual_buckets[2][1]
    assert rules.manual_s3_financial is rules.manual_buckets[3][1]
    assert all(isinstance(e, Entry) for _, bucket in rules.manual_buckets for e in bucket)
    assert set(rules.source_defaults) == set(rules.audit_remove_per_source)
    assert len(rules.keyword_overrides) == EXPECTED_OVERRIDES


def test_loader_rejects_a_truncated_file(tmp_path: Path) -> None:
    data = _raw()
    data["manual_buckets"][2]["entries"].pop()
    with pytest.raises(PipelineError, match="approved change plan"):
        load_scope_rules(_write(tmp_path / "truncated.json", data))


def test_loader_rejects_a_dropped_override(tmp_path: Path) -> None:
    data = _raw()
    data["keyword_overrides"].pop(0)
    with pytest.raises(PipelineError, match="overrides"):
        load_scope_rules(_write(tmp_path / "overrides.json", data))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("manual_buckets"),
        lambda d: d["manual_buckets"][0].__setitem__("entries", "not-a-list"),
        lambda d: d["manual_buckets"][0]["entries"][0].__setitem__("weight", "0.40"),
        lambda d: d["keyword_overrides"][0].__setitem__("categories", ["ssn", 7]),
        lambda d: d["source_defaults"][0].pop("doctype_scope"),
        lambda d: d.__setitem__("provenance", []),
    ],
    ids=[
        "no-buckets",
        "entries-not-list",
        "weight-string",
        "category-int",
        "no-doctype",
        "provenance",
    ],
)
def test_loader_rejects_a_malformed_shape(tmp_path: Path, mutate: Any) -> None:
    data = _raw()
    mutate(data)
    with pytest.raises(PipelineError):
        load_scope_rules(_write(tmp_path / "malformed.json", data))


def test_loader_missing_file_raises() -> None:
    with pytest.raises(MissingSourceError):
        load_scope_rules(Path("/nonexistent/scope_rules_v1.json"))


# -----------------------------------------------------------------------------
# The builder-facing functions read the loaded structure
# -----------------------------------------------------------------------------


def test_known_source_ids_are_the_three_defaults() -> None:
    assert known_source_ids() == frozenset(load_scope_rules().source_defaults)


def test_manual_entries_follow_bucket_alias_and_category_order() -> None:
    rules = load_scope_rules()
    expected = [
        (
            surface.lower(),
            category,
            e.doctype_scope,
            e.weight,
            e.source_id or bucket_id,
            e.rationale,
        )
        for bucket_id, bucket in rules.manual_buckets
        for e in bucket
        for surface in (e.keyword, *e.aliases)
        for category in e.category_scopes
    ]
    assert manual_entries() == expected
    assert len(expected) == EXPECTED_MANUAL_ROWS


def test_scope_keyword_reads_overrides_removals_and_defaults() -> None:
    rules = load_scope_rules()
    doctype, categories, weight, rationale = rules.keyword_overrides["case number"]
    assert scope_keyword("case number", "us_courts_glossary") == [
        (c, doctype, weight, rationale) for c in categories
    ]
    removed = next(iter(rules.audit_remove_per_source["us_courts_glossary"]))
    assert scope_keyword(removed, "us_courts_glossary") == []
    rows = scope_keyword("no-such-keyword", "doj_legal_glossary")
    defaults = rules.source_defaults["doj_legal_glossary"]
    assert [r[0] for r in rows] == list(defaults.category_scopes)
    assert {(r[1], r[2]) for r in rows} == {(defaults.doctype_scope, defaults.default_weight)}
    with pytest.raises(PipelineError):
        scope_keyword("anything", "unknown_source")
