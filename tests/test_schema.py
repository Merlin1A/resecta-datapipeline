"""Tests for resecta_data.common.schema.

Covers:
  - load_schema roundtrip
  - validate on conforming/non-conforming payloads
  - validate_file end-to-end
  - Malformed schema rejection
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resecta_data.common.exceptions import SchemaValidationError
from resecta_data.common.schema import load_schema, validate, validate_file

# A minimal schema used across several tests.
_MINIMAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["version", "entries"],
    "properties": {
        "version": {"type": "integer", "minimum": 1},
        "entries": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": False,
}


def _write_schema(schemas_dir: Path, name: str, body: object) -> Path:
    path = schemas_dir / f"{name}.schema.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# -----------------------------------------------------------------------------
# load_schema
# -----------------------------------------------------------------------------


def test_load_schema_roundtrip(tmp_schemas_dir: Path) -> None:
    _write_schema(tmp_schemas_dir, "example", _MINIMAL_SCHEMA)
    loaded = load_schema(tmp_schemas_dir, "example")
    assert loaded == _MINIMAL_SCHEMA


def test_load_schema_missing(tmp_schemas_dir: Path) -> None:
    with pytest.raises(SchemaValidationError, match="not found"):
        load_schema(tmp_schemas_dir, "nonexistent")


def test_load_schema_rejects_non_object(tmp_schemas_dir: Path) -> None:
    # A schema must be a JSON object at the top level.
    path = tmp_schemas_dir / "bad.schema.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="not a JSON object"):
        load_schema(tmp_schemas_dir, "bad")


def test_load_schema_rejects_malformed_schema(tmp_schemas_dir: Path) -> None:
    # The JSON Schema spec says `type` must be a string or array of strings;
    # `type: 42` is therefore an invalid schema that Draft202012Validator rejects.
    _write_schema(tmp_schemas_dir, "malformed", {"type": 42})
    with pytest.raises(SchemaValidationError, match="Malformed schema"):
        load_schema(tmp_schemas_dir, "malformed")


# -----------------------------------------------------------------------------
# validate
# -----------------------------------------------------------------------------


def test_validate_passes_on_conforming_payload() -> None:
    payload = {"version": 1, "entries": ["a", "b", "c"]}
    validate(payload, _MINIMAL_SCHEMA, context="test")  # no raise


def test_validate_rejects_missing_required_field() -> None:
    payload = {"version": 1}  # missing "entries"
    with pytest.raises(SchemaValidationError, match="entries"):
        validate(payload, _MINIMAL_SCHEMA, context="test")


def test_validate_rejects_wrong_type() -> None:
    payload = {"version": "not an integer", "entries": []}
    with pytest.raises(SchemaValidationError, match="version"):
        validate(payload, _MINIMAL_SCHEMA, context="test")


def test_validate_rejects_additional_properties() -> None:
    payload = {"version": 1, "entries": [], "extra": "nope"}
    with pytest.raises(SchemaValidationError, match="extra"):
        validate(payload, _MINIMAL_SCHEMA, context="test")


def test_validate_rejects_out_of_range() -> None:
    payload = {"version": 0, "entries": []}  # minimum is 1
    with pytest.raises(SchemaValidationError):
        validate(payload, _MINIMAL_SCHEMA, context="test")


def test_validate_reports_context_in_error() -> None:
    payload = {"version": 1}
    with pytest.raises(SchemaValidationError, match=r"my_artifact\.json"):
        validate(payload, _MINIMAL_SCHEMA, context="my_artifact.json")


def test_validate_lists_all_errors() -> None:
    """When multiple violations exist, the error message should list all of them."""
    payload = {"version": "wrong", "entries": [1, 2, 3]}  # two violations
    with pytest.raises(SchemaValidationError) as exc_info:
        validate(payload, _MINIMAL_SCHEMA, context="test")
    message = str(exc_info.value)
    # Both field names should appear in the combined error message.
    assert "version" in message
    assert "entries" in message or "0" in message  # array index of first item


def test_validate_error_is_deterministic() -> None:
    """Same bad payload → same error message every time.

    Error ordering is guaranteed by sorting; this locks the contract.
    """
    payload = {"version": "x", "entries": [1, 2]}
    with pytest.raises(SchemaValidationError) as first:
        validate(payload, _MINIMAL_SCHEMA, context="test")
    with pytest.raises(SchemaValidationError) as second:
        validate(payload, _MINIMAL_SCHEMA, context="test")
    assert str(first.value) == str(second.value)


# -----------------------------------------------------------------------------
# validate_file
# -----------------------------------------------------------------------------


def test_validate_file_end_to_end(tmp_path: Path, tmp_schemas_dir: Path) -> None:
    _write_schema(tmp_schemas_dir, "example", _MINIMAL_SCHEMA)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"version": 1, "entries": ["a"]}', encoding="utf-8")
    validate_file(payload_path, tmp_schemas_dir, "example")  # no raise


def test_validate_file_fails_on_bad_payload(tmp_path: Path, tmp_schemas_dir: Path) -> None:
    _write_schema(tmp_schemas_dir, "example", _MINIMAL_SCHEMA)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"version": 999}', encoding="utf-8")  # missing entries
    with pytest.raises(SchemaValidationError, match="entries"):
        validate_file(payload_path, tmp_schemas_dir, "example")


def test_validate_file_missing_schema_raises(tmp_path: Path, tmp_schemas_dir: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"version": 1, "entries": []}', encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="not found"):
        validate_file(payload_path, tmp_schemas_dir, "missing_schema")
