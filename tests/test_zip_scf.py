"""Unit tests for the zip_scf parser and builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.gazetteers.zip_scf.build import build
from resecta_data.gazetteers.zip_scf.parser import parse

FIXTURE_HEADER = "ZIP,STATE,RES_RATIO,BUS_RATIO,OTH_RATIO,TOT_RATIO\n"


def _write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(FIXTURE_HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_parser_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(MissingSourceError):
        parse(tmp_path / "nope.csv")


def test_parser_rejects_bad_zip(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, ["0210,MA,1.0,1.0,1.0,1.0"])  # 4-digit ZIP
    with pytest.raises(PipelineError, match="5-digit ZIP"):
        parse(csv)


def test_parser_rejects_bad_state(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, ["02108,MASS,1.0,1.0,1.0,1.0"])
    with pytest.raises(PipelineError, match="2-letter state"):
        parse(csv)


def test_parser_rejects_ratio_out_of_range(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, ["02108,MA,1.5,1.0,1.0,1.0"])
    with pytest.raises(PipelineError, match=r"outside \[0, 1\]"):
        parse(csv)


def test_parser_rejects_missing_columns(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    csv.write_text("ZIP,STATE\n02108,MA\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="missing required columns"):
        parse(csv)


def test_builder_groups_by_scf_prefix(tmp_path: Path) -> None:
    csv = tmp_path / "sample.csv"
    _write_csv(
        csv,
        [
            "02108,MA,1.0,1.0,1.0,1.0",
            "02109,MA,1.0,1.0,1.0,1.0",
            "10001,NY,1.0,1.0,1.0,1.0",
            "10002,NY,1.0,1.0,1.0,1.0",
        ],
    )
    payload = build(seed=1, source=csv, retrieval_date="2026-04-16")
    assert payload["zip_key_type"] == "string"
    assert payload["scf_table"] == {"021": "MA", "100": "NY"}
    assert payload["overrides"] == {}


def test_builder_handles_split_scf(tmp_path: Path) -> None:
    csv = tmp_path / "split.csv"
    # Two ZIPs under prefix 063: three in CT, one in MA. CT is dominant; MA zip
    # becomes an override.
    _write_csv(
        csv,
        [
            "06301,CT,1.0,1.0,1.0,1.0",
            "06302,CT,1.0,1.0,1.0,1.0",
            "06303,CT,1.0,1.0,1.0,1.0",
            "06390,MA,1.0,1.0,1.0,1.0",
        ],
    )
    payload = build(seed=1, source=csv, retrieval_date="2026-04-16")
    assert payload["scf_table"] == {"063": "CT"}
    assert payload["overrides"] == {"06390": "MA"}


def test_builder_picks_highest_res_ratio_for_duplicate_zip(tmp_path: Path) -> None:
    csv = tmp_path / "dup.csv"
    _write_csv(
        csv,
        [
            "06390,CT,0.20,1.0,1.0,1.0",
            "06390,MA,0.80,1.0,1.0,1.0",  # wins
        ],
    )
    payload = build(seed=1, source=csv, retrieval_date="2026-04-16")
    # MA wins 06390; becomes dominant state for 063 prefix.
    assert payload["scf_table"] == {"063": "MA"}
    assert payload["overrides"] == {}


def test_builder_output_has_sorted_keys(tmp_path: Path) -> None:
    csv = tmp_path / "order.csv"
    _write_csv(
        csv,
        [
            "90001,CA,1.0,1.0,1.0,1.0",
            "10001,NY,1.0,1.0,1.0,1.0",
            "50001,IA,1.0,1.0,1.0,1.0",
        ],
    )
    payload = build(seed=1, source=csv, retrieval_date="2026-04-16")
    assert list(payload["scf_table"].keys()) == sorted(payload["scf_table"].keys())
