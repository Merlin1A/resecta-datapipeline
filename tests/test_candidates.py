"""The shared candidates-file shape: load, shape check, project, sort, count pin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.candidates import build_from_candidates, load_candidate_rows, ship_rows
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json


def _write(path: Path, payload: Any) -> Path:
    dump_canonical_json(payload, path)
    return path


def test_envelope_rows_come_back_with_the_envelope(tmp_path: Path) -> None:
    path = _write(tmp_path / "c.json", {"rows": [{"k": "b"}, {"k": "a"}], "note": "x"})
    rows, envelope = load_candidate_rows(path, label="t", rows_key="rows")
    assert rows == [{"k": "b"}, {"k": "a"}]
    assert envelope["note"] == "x"


def test_top_level_array_has_an_empty_envelope(tmp_path: Path) -> None:
    path = _write(tmp_path / "c.json", [{"k": 1}])
    rows, envelope = load_candidate_rows(path, label="t", rows_key=None)
    assert rows == [{"k": 1}] and envelope == {}


@pytest.mark.parametrize(
    ("payload", "rows_key", "fragment"),
    [
        ([], "rows", "expected a dict with a 'rows' key"),
        ({"entries": 3}, "entries", "'entries' is not a list"),
        ({"x": 1}, "entries", "expected a dict with an 'entries' key"),
        ({"rows": 1}, None, "expected a top-level JSON array"),
        ([1], None, "non-object row"),
    ],
)
def test_shape_errors_name_the_builder_and_the_file(
    tmp_path: Path, payload: Any, rows_key: str | None, fragment: str
) -> None:
    path = _write(tmp_path / "c.json", payload)
    with pytest.raises(PipelineError, match=fragment) as excinfo:
        load_candidate_rows(path, label="mybuilder", rows_key=rows_key, tag=" (src)")
    assert str(excinfo.value).startswith(f"mybuilder: candidates file {path} (src)")


def test_missing_file_is_load_json_s_error(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="not found"):
        load_candidate_rows(tmp_path / "absent.json", label="t", rows_key="rows")


def test_ship_rows_projects_drops_sorts_and_pins_the_count() -> None:
    rows: list[dict[str, Any]] = [{"k": "b", "drop": True}, {"k": "c"}, {"k": "a"}]
    shipped = ship_rows(
        rows,
        label="t",
        project=lambda row: None if row.get("drop") else {"k": row["k"]},
        sort_key=lambda row: row["k"],
        expected_count=2,
    )
    assert shipped == [{"k": "a"}, {"k": "c"}]


def test_ship_rows_count_pin_carries_the_note() -> None:
    with pytest.raises(PipelineError, match=r"t: expected 2 shipping rows, got 3\. Closed at 2\."):
        ship_rows(
            [{"k": 1}, {"k": 2}, {"k": 3}],
            label="t",
            sort_key=lambda row: row["k"],
            expected_count=2,
            count_note=". Closed at 2.",
        )


def test_ship_rows_runs_the_check_before_the_count_pin() -> None:
    def refuse(rows: list[dict[str, Any]]) -> None:
        raise PipelineError(f"set diverges: {[r['k'] for r in rows]}")

    with pytest.raises(PipelineError, match=r"set diverges: \[1, 2, 3\]"):
        ship_rows(
            [{"k": 3}, {"k": 1}, {"k": 2}],
            label="t",
            sort_key=lambda row: row["k"],
            expected_count=2,
            check=refuse,
        )


def test_build_from_candidates_composes_both_halves(tmp_path: Path) -> None:
    path = _write(tmp_path / "c.json", {"rows": [{"k": "b"}, {"k": "a"}], "date": "d"})
    shipped, envelope = build_from_candidates(
        path, label="t", rows_key="rows", sort_key=lambda row: row["k"], expected_count=2
    )
    assert shipped == [{"k": "a"}, {"k": "b"}]
    assert envelope["date"] == "d"
