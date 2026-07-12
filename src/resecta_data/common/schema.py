"""JSON Schema validation wrapper.

Every output file type has a schema under ``schemas/*.schema.json``. Builders
validate their output against the schema before writing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from .exceptions import SchemaValidationError
from .io import load_json

logger = logging.getLogger(__name__)


def load_schema(schemas_dir: Path, name: str) -> dict[str, Any]:
    """Load a named JSON Schema.

    Args:
        schemas_dir: Directory containing ``*.schema.json`` files.
        name: Schema name without the ``.schema.json`` suffix.

    Returns:
        Parsed schema dict.

    Raises:
        SchemaValidationError: If the schema is missing or invalid.
    """
    path = schemas_dir / f"{name}.schema.json"
    if not path.is_file():
        raise SchemaValidationError(f"Schema not found: {path}")
    schema: Any = load_json(path)
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"Schema at {path} is not a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise SchemaValidationError(f"Malformed schema at {path}: {exc.message}") from exc
    return schema


def validate(payload: Any, schema: dict[str, Any], *, context: str = "<payload>") -> None:
    """Validate ``payload`` against ``schema``.

    Args:
        payload: The value to validate.
        schema: A parsed JSON Schema (Draft 2020-12).
        context: Human label for error messages (typically a filename).

    Raises:
        SchemaValidationError: If validation fails. The message lists every
            violation, not just the first.
    """
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if not errors:
        return

    lines = [f"Schema validation failed for {context}:"]
    for err in errors:
        path = "/".join(str(p) for p in err.path) or "<root>"
        lines.append(f"  {path}: {err.message}")
    raise SchemaValidationError("\n".join(lines))


def validate_file(
    file_path: Path,
    schemas_dir: Path,
    schema_name: str,
) -> None:
    """Load and validate a JSON file against a named schema.

    Convenience wrapper around :func:`load_schema` + :func:`validate`.
    """
    payload = load_json(file_path)
    schema = load_schema(schemas_dir, schema_name)
    validate(payload, schema, context=str(file_path))
    logger.debug("Schema %s: %s passed", schema_name, file_path)
