"""Shared loaders for Phase 3b calibration inputs.

``fit_temperature`` and ``sweep_thresholds`` both consume Swift-side dumps
and the G8 corpus. This module validates each file against its schema and
asserts the cross-file invariants (matching seed, doc_id coverage) so the
builders can rely on well-formed inputs.

These inputs come from Swift rather than from a Python reimplementation by
design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import load_json, sha256_file
from resecta_data.common.schema import validate_file

_SOFTMAX_DUMP_SCHEMA: Final[str] = "doctype_softmax_dump"
_SCORE_DUMP_SCHEMA: Final[str] = "detector_score_dump"
_CORPUS_SCHEMA: Final[str] = "g8_corpus"
_TEMPERATURE_SCHEMA: Final[str] = "doctype_temperature"


def load_softmax_dump(path: Path, schemas_dir: Path) -> dict[str, Any]:
    """Validate and load a Swift doctype-softmax dump."""
    validate_file(path, schemas_dir, _SOFTMAX_DUMP_SCHEMA)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise PipelineError(f"{path}: softmax dump root must be an object")
    return payload


def load_score_dump(path: Path, schemas_dir: Path) -> dict[str, Any]:
    """Validate and load a Swift detector-score dump."""
    validate_file(path, schemas_dir, _SCORE_DUMP_SCHEMA)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise PipelineError(f"{path}: score dump root must be an object")
    return payload


def load_corpus(path: Path, schemas_dir: Path) -> dict[str, Any]:
    """Validate and load the G8 corpus at ``path``."""
    validate_file(path, schemas_dir, _CORPUS_SCHEMA)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise PipelineError(f"{path}: corpus root must be an object")
    return payload


def load_temperature(path: Path, schemas_dir: Path) -> dict[str, Any]:
    """Validate and load a fitted-temperature artifact."""
    validate_file(path, schemas_dir, _TEMPERATURE_SCHEMA)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise PipelineError(f"{path}: temperature root must be an object")
    return payload


def assert_corpus_seed_matches(
    dump: dict[str, Any],
    corpus: dict[str, Any],
    *,
    context: str,
) -> None:
    """Hard-fail if a dump's ``g8_corpus_seed`` does not match the corpus seed."""
    dump_seed = dump["g8_corpus_seed"]
    corpus_seed = corpus["seed"]
    if dump_seed != corpus_seed:
        raise PipelineError(
            f"{context}: dump g8_corpus_seed={dump_seed} does not match "
            f"corpus seed={corpus_seed}. The dump was produced against a different "
            "G8 build. Rebuild the corpus or regenerate the dump."
        )


def assert_doc_ids_cover_corpus(
    dump: dict[str, Any],
    corpus: dict[str, Any],
    *,
    context: str,
) -> None:
    """Hard-fail if a dump is missing any corpus doc_id (or contains extras)."""
    corpus_ids = {doc["id"] for doc in corpus["documents"]}
    dump_ids = {doc["doc_id"] for doc in dump["documents"]}
    missing = corpus_ids - dump_ids
    extra = dump_ids - corpus_ids
    if missing or extra:
        problems: list[str] = []
        if missing:
            problems.append(f"missing {len(missing)} (e.g., {sorted(missing)[:3]})")
        if extra:
            problems.append(f"extra {len(extra)} (e.g., {sorted(extra)[:3]})")
        raise PipelineError(f"{context}: dump doc_ids do not match corpus: {'; '.join(problems)}.")


def dump_sha256(path: Path) -> str:
    """Return the hex SHA-256 of a dump file (recorded in fit_metadata)."""
    return sha256_file(path)
