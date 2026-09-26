"""The CLI: the three click groups and the registration of every command.

``main`` (the root), ``build`` and ``build calibrate`` are defined here; the
commands live in ``commands/``, one module per builder family, and each module
attaches its commands through the ``register`` call below. A new subcommand
lands in its family's module and reaches the tree only through that call
(CONTRIBUTING.md, "Structure").
"""

from __future__ import annotations

import logging
import sys

import click

from .commands import (
    bloom,
    classifier,
    corpus,
    gazetteers,
    install,
    instrumentation,
    vectors,
    verify,
)
from .commands import eval as eval_commands
from .commands.verify import _OUT_OF_BAND_PREFIXES, _is_out_of_band
from .common.exceptions import (
    DeterminismError,
    HashMismatchError,
    PipelineError,
    SchemaValidationError,
)
from .routes import INSTALL_ROUTES, SCHEMA_ROUTES, SHRINK_GUARDED_ROUTES

# The names importers read on this module besides ``main``: the routing tables (their
# historical home) and the out-of-band aliases the doctor tooling and one test pin, each
# defined in the module that uses it.
__all__ = [
    "INSTALL_ROUTES",
    "SCHEMA_ROUTES",
    "SHRINK_GUARDED_ROUTES",
    "_OUT_OF_BAND_PREFIXES",
    "_is_out_of_band",
    "main",
]


DEBUG_VERBOSITY = 2
"""Verbosity level at which we emit DEBUG-level logs."""


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v, -vv).")
@click.pass_context
def main(ctx: click.Context, verbose: int) -> None:
    """Resecta DataPipeline CLI."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= DEBUG_VERBOSITY:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)


# -----------------------------------------------------------------------------
# Build subcommands (Phase 1)
# -----------------------------------------------------------------------------


@main.group("build")
def build_group() -> None:
    """Generate Phase 1+ artifacts into build/."""


@build_group.group("calibrate")
def build_calibrate_group() -> None:
    """Phase 3b calibration artifacts produced from Swift-side dumps."""


verify.register(main, build_group, build_calibrate_group)
install.register(main, build_group, build_calibrate_group)
vectors.register(main, build_group, build_calibrate_group)
bloom.register(main, build_group, build_calibrate_group)
gazetteers.register(main, build_group, build_calibrate_group)
corpus.register(main, build_group, build_calibrate_group)
eval_commands.register(main, build_group, build_calibrate_group)
classifier.register(main, build_group, build_calibrate_group)
instrumentation.register(main, build_group, build_calibrate_group)


# -----------------------------------------------------------------------------
# Top-level error handler
# -----------------------------------------------------------------------------


def _install_exception_handler() -> None:
    """Convert PipelineError subclasses into clean CLI exits."""
    original_excepthook = sys.excepthook

    def handler(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        if isinstance(exc, HashMismatchError | DeterminismError | SchemaValidationError):
            click.echo(f"{exc_type.__name__}: {exc}", err=True)
            sys.exit(1)
        if isinstance(exc, PipelineError):
            click.echo(f"{exc_type.__name__}: {exc}", err=True)
            sys.exit(2)
        original_excepthook(exc_type, exc, tb)  # type: ignore[arg-type]

    sys.excepthook = handler


_install_exception_handler()


if __name__ == "__main__":
    main()
