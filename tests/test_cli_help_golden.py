"""Characterisation fixture for the CLI's ``--help`` text.

Every command path in the tree (the root group, ``build``, ``build calibrate``
and each command) is invoked with ``--help`` through ``CliRunner`` at a fixed
terminal width and compared byte-for-byte with the checked-in golden under
``tests/fixtures/cli_help/``. The goldens were captured before the command
modules moved out of ``cli.py``; a difference means a command, option or
docstring changed, which a structural refactor must not do.

Regenerate on purpose only: ``python tests/test_cli_help_golden.py --write``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from resecta_data.cli import main

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "cli_help"
ROOT_NAME = "resecta-data"
TERMINAL_WIDTH = 100


def _golden_name(path: list[str]) -> str:
    return "__".join(path) if path else ROOT_NAME


def _help_pages() -> dict[str, str]:
    """Walk the command tree; ``{golden name: help text}`` in tree order."""
    runner = CliRunner()
    pages: dict[str, str] = {}

    def walk(cmd: click.Command, path: list[str]) -> None:
        result = runner.invoke(
            main,
            [*path, "--help"],
            prog_name=ROOT_NAME,
            terminal_width=TERMINAL_WIDTH,
            color=False,
            catch_exceptions=False,
        )
        assert result.exit_code == 0, (path, result.output)
        pages[_golden_name(path)] = result.output
        if isinstance(cmd, click.Group):
            ctx = click.Context(cmd, info_name=cmd.name)
            for name in cmd.list_commands(ctx):
                sub = cmd.get_command(ctx, name)
                assert sub is not None, name
                walk(sub, [*path, name])

    walk(main, [])
    return pages


def _golden_names() -> list[str]:
    return sorted(p.stem for p in GOLDEN_DIR.glob("*.txt"))


def test_golden_set_matches_the_command_tree() -> None:
    assert _golden_names() == sorted(_help_pages())


@pytest.mark.parametrize("name", _golden_names())
def test_help_text_is_byte_identical_to_its_golden(name: str) -> None:
    pages = _help_pages()
    assert name in pages, f"no command path for golden {name}"
    expected = (GOLDEN_DIR / f"{name}.txt").read_text(encoding="utf-8")
    assert pages[name] == expected


def _write_goldens() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in GOLDEN_DIR.glob("*.txt"):
        stale.unlink()
    for name, text in _help_pages().items():
        (GOLDEN_DIR / f"{name}.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: test_cli_help_golden.py --write")
    _write_goldens()
