"""The one cutover-diff builder every sidecar-emitting builder shares."""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.common.cutover import CUTOVER_DIFF_VERSION, CutoverSpec, build_cutover_diff
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.schema import load_schema, validate

_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
_SPEC = CutoverSpec(artifact="gazetteers/example.json", generated_by="resecta-data/example")


def _rows(*names: str, **fields: object) -> list[dict[str, object]]:
    return [{"name": name, "aliases": [], **fields} for name in names]


def test_empty_inputs_give_the_empty_envelope() -> None:
    diff = build_cutover_diff((), (), spec=_SPEC)
    assert diff == {
        "version": CUTOVER_DIFF_VERSION,
        "generated_by": "resecta-data/example",
        "artifact": "gazetteers/example.json",
        "summary": {"legacy_only_count": 0, "rebuild_only_count": 0, "keyed_diff_count": 0},
        "legacy_only": [],
        "rebuild_only": [],
        "keyed_diff": [],
    }


def test_unmatched_keys_sorted_and_differing_rows_listed_with_both_sides() -> None:
    legacy = _rows("Charlie", "Alpha", "Bravo")
    rebuild: list[dict[str, object]] = [
        *_rows("Delta", "Charlie"),
        {"name": "Bravo", "aliases": ["B"]},
    ]
    diff = build_cutover_diff(legacy, rebuild, spec=_SPEC)
    assert diff["legacy_only"] == ["Alpha"]
    assert diff["rebuild_only"] == ["Delta"]
    assert diff["keyed_diff"] == [
        {
            "key": "Bravo",
            "legacy": {"name": "Bravo", "aliases": []},
            "rebuild": {"name": "Bravo", "aliases": ["B"]},
        }
    ]
    assert diff["summary"] == {
        "legacy_only_count": 1,
        "rebuild_only_count": 1,
        "keyed_diff_count": 1,
    }


def test_later_row_with_the_same_key_replaces_the_earlier_one() -> None:
    legacy = [{"name": "Alpha", "aliases": ["old"]}, {"name": "Alpha", "aliases": ["new"]}]
    diff = build_cutover_diff(legacy, _rows("Alpha", aliases=["new"]), spec=_SPEC)
    assert diff["keyed_diff"] == []


def test_matching_on_another_key_field() -> None:
    spec = CutoverSpec(artifact="x.json", generated_by="g", key="id")
    diff = build_cutover_diff([{"id": "a", "v": 1}], [{"id": "a", "v": 2}], spec=spec)
    assert diff["keyed_diff"] == [
        {"key": "a", "legacy": {"id": "a", "v": 1}, "rebuild": {"id": "a", "v": 2}}
    ]


def test_row_without_a_string_key_fails_loud() -> None:
    with pytest.raises(PipelineError, match="legacy row has no string 'name' field"):
        build_cutover_diff([{"aliases": []}], (), spec=_SPEC)
    with pytest.raises(PipelineError, match="rebuild row has no string 'name' field"):
        build_cutover_diff((), [{"name": 7}], spec=_SPEC)


def test_payload_validates_against_the_shared_schema() -> None:
    schema = load_schema(_SCHEMAS_DIR, "cutover_diff")
    validate(build_cutover_diff(_rows("A", "B"), _rows("B", "C"), spec=_SPEC), schema, context="x")
    validate(build_cutover_diff((), (), spec=_SPEC), schema, context="empty")


def test_two_calls_are_equal() -> None:
    legacy, rebuild = _rows("A", "B"), _rows("B", "C")
    assert build_cutover_diff(legacy, rebuild, spec=_SPEC) == build_cutover_diff(
        legacy, rebuild, spec=_SPEC
    )
