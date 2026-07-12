"""Custom exception types for the pipeline.

Never raise bare Exception.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for all pipeline errors."""


class DeterminismError(PipelineError):
    """Raised when a build is not bit-reproducible.

    The typical cause is an unseeded random source, a wall-clock leak into the
    artifact payload, or an insertion-order dependency in a nominally sorted
    output.
    """


class LicenseError(PipelineError):
    """Raised when a source file has no SOURCES.md row or an unexpected hash."""


class SchemaValidationError(PipelineError):
    """Raised when a generated artifact fails its JSON Schema."""


class HashMismatchError(PipelineError):
    """Raised when an artifact's SHA-256 does not match asset_hashes.lock.

    Either the artifact changed unexpectedly (non-determinism) or the lockfile
    is stale. Never update the lockfile to silence this — investigate first.
    """


class MissingSourceError(PipelineError):
    """Raised when a required raw source file is absent.

    Triggered by build targets when ``src/resecta_data/<module>/sources/`` is
    missing a file that the builder requires. Fix by running ``make sources``
    (which may require Jesse to supply restricted datasets manually).
    """


class UnsupervisedActionError(PipelineError):
    """Raised when an automated action attempts to do something that requires Jesse.

    See CONTRIBUTING.md's Hard stops section for the full list. This
    exception exists to turn silent policy violations into loud, halting
    failures.
    """
