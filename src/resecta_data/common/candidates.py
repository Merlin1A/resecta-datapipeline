"""One load → shape check → project → sort → count pin for the builders that ship a candidates file.

Four builders (the DL and passport patterns, the context keywords, the rule
catalog) read a hand-curated JSON of candidate rows, drop the rows and fields
that do not ship, sort the survivors on a stable key and refuse to emit unless
the count matches a closed number. This module holds that shape once; each
builder contributes its label, its row projection, its sort key, its closed
count and, where it has one, an invariant to check before the count pin.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .exceptions import PipelineError
from .io import load_json

Row = dict[str, Any]
"""One candidate row: a JSON object."""


def load_candidate_rows(
    path: Path,
    *,
    label: str,
    rows_key: str | None,
    file_noun: str = "candidates file",
    tag: str = "",
) -> tuple[list[Row], Mapping[str, Any]]:
    """Read ``path`` and return its candidate rows, plus the file's envelope (empty for an array).

    ``rows_key`` names the key that holds the row list inside an envelope
    object; ``None`` means the file is a top-level JSON array (an empty envelope).
    Every row must be an object. ``file_noun`` and ``tag`` keep the builder's
    error wording (``"source file"``; ``" (d11)"``). A missing file raises the
    reader's own error.
    """
    data = load_json(path)
    where = f"{label}: {file_noun} {path}{tag}"
    envelope: Mapping[str, Any]
    if rows_key is None:
        if not isinstance(data, list):
            raise PipelineError(
                f"{where} is malformed (expected a top-level JSON array of row dicts)."
            )
        rows, envelope = data, {}
    else:
        if not isinstance(data, dict) or rows_key not in data:
            article = "an" if rows_key[:1] in {"a", "e", "i", "o", "u"} else "a"
            raise PipelineError(
                f"{where} is malformed (expected a dict with {article} '{rows_key}' key)."
            )
        rows, envelope = data[rows_key], data
        if not isinstance(rows, list):
            raise PipelineError(f"{where} '{rows_key}' is not a list.")
    for entry in rows:
        if not isinstance(entry, dict):
            raise PipelineError(f"{where} contains a non-object row entry.")
    return rows, envelope


def ship_rows(
    rows: Iterable[Row],
    *,
    label: str,
    sort_key: Callable[[Row], Any],
    expected_count: int,
    project: Callable[[Row], Row | None] | None = None,
    count_note: str = ".",
    check: Callable[[list[Row]], None] | None = None,
) -> list[Row]:
    """Project every row (``None`` drops it), sort on ``sort_key``, run ``check``, pin the count.

    The sort is stable, so rows equal under ``sort_key`` keep their file
    order. ``count_note`` is appended to the count error verbatim (it starts
    with the punctuation the builder wants after ``got N``).
    """
    if project is None:
        shipping = list(rows)
    else:
        shipping = [shipped for entry in rows if (shipped := project(entry)) is not None]
    shipping.sort(key=sort_key)
    if check is not None:
        check(shipping)
    if len(shipping) != expected_count:
        raise PipelineError(
            f"{label}: expected {expected_count} shipping rows, got {len(shipping)}{count_note}"
        )
    return shipping


def build_from_candidates(
    path: Path,
    *,
    label: str,
    rows_key: str | None,
    sort_key: Callable[[Row], Any],
    expected_count: int,
    project: Callable[[Row], Row | None] | None = None,
    count_note: str = ".",
    check: Callable[[list[Row]], None] | None = None,
    file_noun: str = "candidates file",
    tag: str = "",
) -> tuple[list[Row], Mapping[str, Any]]:
    """The whole shape over one file: the shipped rows and the envelope (empty for an array)."""
    rows, envelope = load_candidate_rows(
        path, label=label, rows_key=rows_key, file_noun=file_noun, tag=tag
    )
    shipped = ship_rows(
        rows,
        label=label,
        sort_key=sort_key,
        expected_count=expected_count,
        project=project,
        count_note=count_note,
        check=check,
    )
    return shipped, envelope
