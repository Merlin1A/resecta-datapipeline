"""Nickname / diminutive given-name sidecar builder.

Consumes the carltonnorthern/nicknames CC0 dataset and emits
``build/gazetteers/nicknames.json`` — an alias→canonicals lookup sidecar
consumed by ``NicknameGazetteer`` in the Swift engine to widen given-name
bloom queries via nickname resolution.  Not part of the signed bloom manifest;
no bloom rebuild required.

See ``schemas/nicknames.schema.json`` for the wire contract and
``scripts/fetch_nicknames.sh`` for the fetch step.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
