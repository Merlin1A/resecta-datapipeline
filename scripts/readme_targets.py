#!/usr/bin/env python3
"""Keep the Makefile-targets block in README.md equal to ``make help`` (stdlib).

The block between ``<!-- make-help:begin -->`` and ``<!-- make-help:end -->``
holds ``make help``'s output verbatim inside a text fence, so the documented
target list is generated from the Makefile's own ``## `` help comments and
cannot drift from it. ``--write`` regenerates the block (``make readme-targets``);
``--check`` (the default; ``make lint`` runs it) exits 1 when it is stale.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
BEGIN, END = "<!-- make-help:begin -->", "<!-- make-help:end -->"


def render() -> str:
    make = next(
        (c for c in (os.environ.get("MAKE"), "gmake", "make") if c and shutil.which(c)), None
    )
    if make is None:
        sys.exit("readme_targets: GNU make not found")
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    out = subprocess.run(  # noqa: S603 -- fixed argv against the repo Makefile
        [make, "help"], cwd=ROOT, capture_output=True, text=True, env=env, check=False
    ).stdout.rstrip()
    return "```text\n" + out + "\n```"


def main() -> int:
    write = "--write" in sys.argv[1:]
    text = README.read_text(encoding="utf-8")
    head, _, rest = text.partition(BEGIN)
    current, marker, tail = rest.partition(END)
    if not marker:
        sys.exit(f"readme_targets: {README.name} lacks the {BEGIN} / {END} markers")
    fresh = "\n" + render() + "\n"
    if write:
        README.write_text(head + BEGIN + fresh + END + tail, encoding="utf-8")
        print("readme_targets: README.md targets block written")
        return 0
    if current != fresh:
        print("readme_targets: README.md targets block is stale -- run `make readme-targets`")
        return 1
    print("readme_targets: README.md targets block is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
