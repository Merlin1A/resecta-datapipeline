"""Tests for the D6 reviewed negative-context staging mechanics (S1 0.5).

Content arrives in search-impl S3; these tests cover the mechanics only —
the sidecar gate, the failure modes, and the staged-copy fidelity — against
synthetic reviewed/candidates files in tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from resecta_data.cli import main as cli_main
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import sha256_file
from resecta_data.gazetteers.negative_context.stage_reviewed import stage_reviewed

_REVIEWED_BODY = b'{"entries": [], "version": 1}\n'


def _make_candidates(build_dir: Path) -> Path:
    candidates = build_dir / "gazetteers" / "negative_context_candidates.json"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_bytes(b'{"candidates": [], "version": 1}\n')
    return candidates


def _make_reviewed(reviewed_dir: Path, reviewed_version: str) -> tuple[Path, Path]:
    reviewed_dir.mkdir(parents=True, exist_ok=True)
    reviewed = reviewed_dir / "negative_context.json"
    reviewed.write_bytes(_REVIEWED_BODY)
    meta = reviewed_dir / "negative_context.meta.json"
    meta.write_text(
        json.dumps(
            {
                "reviewed_version": reviewed_version,
                "reviewed_by": "jesse",
                "reviewed_at": "2026-06-11",
                "note": "synthetic test sidecar",
            }
        )
    )
    return reviewed, meta


def test_stage_reviewed_happy_path(tmp_build_dir: Path, tmp_path: Path) -> None:
    candidates = _make_candidates(tmp_build_dir)
    reviewed_dir = tmp_path / "reviewed"
    _make_reviewed(reviewed_dir, sha256_file(candidates))

    staged = stage_reviewed(tmp_build_dir, reviewed_dir)

    dest = tmp_build_dir / "gazetteers" / "negative_context.json"
    meta_dest = tmp_build_dir / "gazetteers" / "negative_context.meta.json"
    assert staged == [dest, meta_dest]
    assert dest.read_bytes() == _REVIEWED_BODY
    assert json.loads(meta_dest.read_text())["reviewed_by"] == "jesse"


def test_stage_reviewed_is_rerunnable(tmp_build_dir: Path, tmp_path: Path) -> None:
    candidates = _make_candidates(tmp_build_dir)
    reviewed_dir = tmp_path / "reviewed"
    _make_reviewed(reviewed_dir, sha256_file(candidates))

    first = stage_reviewed(tmp_build_dir, reviewed_dir)
    second = stage_reviewed(tmp_build_dir, reviewed_dir)
    assert first == second
    assert (tmp_build_dir / "gazetteers" / "negative_context.json").read_bytes() == _REVIEWED_BODY


def test_stage_reviewed_missing_reviewed_files_raises(tmp_build_dir: Path, tmp_path: Path) -> None:
    _make_candidates(tmp_build_dir)
    with pytest.raises(PipelineError, match="D6 review gate"):
        stage_reviewed(tmp_build_dir, tmp_path / "reviewed")


def test_stage_reviewed_missing_candidates_raises(tmp_build_dir: Path, tmp_path: Path) -> None:
    reviewed_dir = tmp_path / "reviewed"
    _make_reviewed(reviewed_dir, "0" * 64)
    with pytest.raises(PipelineError, match="make gazetteers"):
        stage_reviewed(tmp_build_dir, reviewed_dir)


def test_stage_reviewed_hash_mismatch_raises(tmp_build_dir: Path, tmp_path: Path) -> None:
    """Adversarial: candidates drifted since the review — staging must refuse."""
    candidates = _make_candidates(tmp_build_dir)
    reviewed_dir = tmp_path / "reviewed"
    _make_reviewed(reviewed_dir, sha256_file(candidates))
    candidates.write_bytes(b'{"candidates": ["drifted"], "version": 2}\n')

    with pytest.raises(PipelineError, match="re-review"):
        stage_reviewed(tmp_build_dir, reviewed_dir)
    assert not (tmp_build_dir / "gazetteers" / "negative_context.json").exists()


def test_stage_reviewed_malformed_sidecar_raises(tmp_build_dir: Path, tmp_path: Path) -> None:
    candidates = _make_candidates(tmp_build_dir)
    reviewed_dir = tmp_path / "reviewed"
    _make_reviewed(reviewed_dir, sha256_file(candidates))
    (reviewed_dir / "negative_context.meta.json").write_text('{"reviewed_by": "jesse"}')

    with pytest.raises(PipelineError, match="reviewed_version"):
        stage_reviewed(tmp_build_dir, reviewed_dir)


def test_stage_reviewed_cli_happy_path(tmp_build_dir: Path, tmp_path: Path) -> None:
    candidates = _make_candidates(tmp_build_dir)
    reviewed_dir = tmp_path / "reviewed"
    _make_reviewed(reviewed_dir, sha256_file(candidates))

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "stage-reviewed-negctx",
            "--build-dir",
            str(tmp_build_dir),
            "--reviewed-dir",
            str(reviewed_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Staged" in result.output
    assert (tmp_build_dir / "gazetteers" / "negative_context.json").exists()
