"""Negative-context scope rules: the loader for ``sources/scope_rules_v1.json``.

The tables that map a source keyword to (category, doctype, weight) live in the
data file beside this module: the per-source defaults and removal lists, the
per-keyword overrides and the four hand-curated buckets (MEDICAL, GENERIC,
AUDIT_PART3, S3_FINANCIAL; 26 + 16 + 52 + 23 entries). Its ``provenance.note``
carries the review that settled every rationale. Buckets are ordered arrays:
the build stage's dedup is first-writer-wins, so their order is part of the
asset. The data file and the reviewed ``negative_context.json`` change only
under an approved change plan (CONTRIBUTING.md); this module reads the file by
name, checks its shape, pins its counts and serves the builder's three calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import load_json

_DEFAULT_PATH: Final[Path] = Path(__file__).resolve().parent / "sources" / "scope_rules_v1.json"

# Pinned counts: drift means the data file changed, which needs an approved change plan.
EXPECTED_ENTRIES: Final[int] = 117
EXPECTED_MANUAL_ROWS: Final[int] = 136
EXPECTED_OVERRIDES: Final[int] = 15
EXPECTED_REMOVED: Final[int] = 93

Override = tuple[str, tuple[str, ...], float, str]  # doctype, categories, weight, rationale


@dataclass(frozen=True, slots=True)
class SourceScope:
    doctype_scope: str
    category_scopes: tuple[str, ...]
    default_weight: float


@dataclass(frozen=True, slots=True)
class Entry:
    """A hand-curated keyword; aliases fan out to rows, ``source_id`` None = the bucket id."""

    keyword: str
    aliases: tuple[str, ...]
    category_scopes: tuple[str, ...]
    doctype_scope: str
    weight: float
    rationale: str
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeRules:
    """The loaded tables. ``manual_buckets`` keeps the file's bucket order."""

    source_defaults: dict[str, SourceScope]
    audit_remove_per_source: dict[str, frozenset[str]]
    keyword_overrides: dict[str, Override]
    manual_buckets: tuple[tuple[str, tuple[Entry, ...]], ...]

    def bucket(self, index: int) -> tuple[Entry, ...]:
        return self.manual_buckets[index][1]

    manual_medical = property(lambda self: self.bucket(0))
    manual_generic = property(lambda self: self.bucket(1))
    manual_audit_part3 = property(lambda self: self.bucket(2))
    manual_s3_financial = property(lambda self: self.bucket(3))


def _field(obj: Any, key: str, kind: type | tuple[type, ...], path: Path) -> Any:
    if not isinstance(obj, dict) or not isinstance(obj.get(key), kind):
        raise PipelineError(f"negative_context: {path.name} is malformed at {key!r}.")
    return obj[key]


def _strings(obj: Any, key: str, path: Path) -> tuple[str, ...]:
    items = _field(obj, key, list, path)
    if not all(isinstance(item, str) for item in items):
        raise PipelineError(f"negative_context: {path.name} {key!r} must hold strings only.")
    return tuple(items)


def _entry(obj: Any, path: Path) -> Entry:
    return Entry(
        keyword=_field(obj, "keyword", str, path),
        aliases=_strings(obj, "aliases", path),
        category_scopes=_strings(obj, "category_scopes", path),
        doctype_scope=_field(obj, "doctype_scope", str, path),
        weight=_field(obj, "weight", float, path),
        rationale=_field(obj, "rationale", str, path),
        source_id=_field(obj, "source_id", (str, type(None)), path),
    )


def load_scope_rules(path: Path = _DEFAULT_PATH) -> ScopeRules:
    """Load the data file; absent raises MissingSourceError, bad shape or count PipelineError."""
    if not path.is_file():
        raise MissingSourceError(f"negative_context: scope rules source missing: {path}")
    data = load_json(path)
    _field(_field(data, "provenance", dict, path), "note", str, path)
    defaults = {
        _field(o, "source_id", str, path): SourceScope(
            doctype_scope=_field(o, "doctype_scope", str, path),
            category_scopes=_strings(o, "category_scopes", path),
            default_weight=_field(o, "default_weight", float, path),
        )
        for o in _field(data, "source_defaults", list, path)
    }
    removed = {
        _field(o, "source_id", str, path): frozenset(_strings(o, "keywords", path))
        for o in _field(data, "audit_remove_per_source", list, path)
    }
    overrides: dict[str, Override] = {
        _field(o, "keyword", str, path): (
            _field(o, "doctype", str, path),
            _strings(o, "categories", path),
            _field(o, "weight", float, path),
            _field(o, "rationale", str, path),
        )
        for o in _field(data, "keyword_overrides", list, path)
    }
    buckets = tuple(
        (
            _field(b, "bucket_source_id", str, path),
            tuple(_entry(o, path) for o in _field(b, "entries", list, path)),
        )
        for b in _field(data, "manual_buckets", list, path)
    )
    rules = ScopeRules(defaults, removed, overrides, buckets)
    for expected, got, what in (
        (EXPECTED_ENTRIES, sum(len(entries) for _, entries in buckets), "entries"),
        (EXPECTED_MANUAL_ROWS, len(_manual_rows(rules)), "manual rows"),
        (EXPECTED_OVERRIDES, len(overrides), "overrides"),
        (EXPECTED_REMOVED, sum(len(k) for k in removed.values()), "removed keywords"),
    ):
        if got != expected:
            raise PipelineError(
                f"negative_context: expected {expected} {what} in {path.name}, got {got}. "
                "A change to the scope rules needs an approved change plan."
            )
    return rules


@cache
def _rules() -> ScopeRules:
    return load_scope_rules()


def scope_keyword(keyword: str, source_id: str) -> list[tuple[str, str, float, str]]:
    """One (category, doctype, weight, rationale) row per category; [] for a removed keyword."""
    rules = _rules()
    if source_id not in rules.source_defaults:
        known = sorted(rules.source_defaults)
        raise PipelineError(f"Unknown negative-context source_id {source_id!r}; known: {known}")
    if keyword in rules.audit_remove_per_source.get(source_id, frozenset()):
        return []
    override = rules.keyword_overrides.get(keyword)
    if override is not None:
        doctype, categories, weight, rationale = override
        return [(category, doctype, weight, rationale) for category in categories]
    defaults = rules.source_defaults[source_id]
    rationale = (
        f"Source-default scoping for {source_id}: "
        f"doctype={defaults.doctype_scope}, weight={defaults.default_weight}."
    )
    return [
        (category, defaults.doctype_scope, defaults.default_weight, rationale)
        for category in defaults.category_scopes
    ]


def known_source_ids() -> frozenset[str]:
    """Return the set of accepted source_id values."""
    return frozenset(_rules().source_defaults)


def _manual_rows(rules: ScopeRules) -> list[tuple[str, str, str, float, str, str]]:
    return [
        (
            surface.lower(),
            category,
            entry.doctype_scope,
            entry.weight,
            entry.source_id if entry.source_id is not None else bucket_source_id,
            entry.rationale,
        )
        for bucket_source_id, entries in rules.manual_buckets
        for entry in entries
        for surface in (entry.keyword, *entry.aliases)
        for category in entry.category_scopes
    ]


def manual_entries() -> list[tuple[str, str, str, float, str, str]]:
    """Hand-curated (keyword, category, doctype, weight, source_id, rationale) rows, file order."""
    return _manual_rows(_rules())
