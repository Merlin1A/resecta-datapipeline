#!/usr/bin/env python3
"""Emit the ETL stage map into README.md from the make database (Mermaid; stdlib).

Nodes are the phase-tagged build targets (the ``## [Phase N]`` help text in the
Makefile) plus the verify / eval / install chain; edges are the prerequisite
relations ``make -pn`` reports between those targets and their stamps, so the
map cannot drift from the Makefile. ``--write`` replaces the block between the
``<!-- etl-graph:begin -->`` / ``<!-- etl-graph:end -->`` markers in README.md;
``--check`` (the default; ``make lint`` runs it) exits 1 when the block is stale.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
BEGIN, END = "<!-- etl-graph:begin -->", "<!-- etl-graph:end -->"
CHAIN = (
    "build",
    "verify",
    "eval",
    "stage-reviewed-negctx",
    "manifest-assets",
    "sign-manifest",
    "install-assets",
)
STAMP = "build/.stamps/"
PHASE_TAG = re.compile(r"^([a-zA-Z0-9_-]+):.*?## \[Phase ([^\]]+)\]", re.M)
RULE = re.compile(r"^([^#\s:][^:=\n]*):([^=\n]*)$", re.M)


def _make_db() -> str:
    make = next(
        (c for c in (os.environ.get("MAKE"), "gmake", "make") if c and shutil.which(c)), None
    )
    if make is None:
        sys.exit("etl_graph: GNU make not found")
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    return subprocess.run(  # noqa: S603 -- fixed argv against the repo Makefile
        [make, "-pn", "-f", "Makefile"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    ).stdout


def _ident(name: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", name)


def render() -> str:
    rules = {m.group(1): m.group(2).split("|", 1)[0].split() for m in RULE.finditer(_make_db())}
    phases: dict[str, list[str]] = {}
    for target, phase in PHASE_TAG.findall((ROOT / "Makefile").read_text(encoding="utf-8")):
        phases.setdefault(phase, []).append(target)
    nodes = [t for ts in phases.values() for t in ts] + list(CHAIN)
    owner: dict[str, str] = {}  # a stamp belongs to the first (phase) target that lists it
    for node in nodes:
        for prereq in rules.get(node, ()):
            if prereq.startswith(STAMP):
                owner.setdefault(prereq, node)
    edges: list[tuple[str, str]] = []
    for node in nodes:
        own = [p for p in rules.get(node, ()) if owner.get(p) == node]
        deps = list(rules.get(node, ())) + [d for stamp in own for d in rules.get(stamp, ())]
        for dep in deps:
            src = owner.get(dep, dep if dep in nodes else None)
            if src and src != node and (src, node) not in edges:
                edges.append((src, node))
    lines = ["```mermaid", "graph LR"]
    for phase, targets in phases.items():
        lines += (
            [f'  subgraph P{re.sub(r"[^A-Za-z0-9]", "", phase)}["Phase {phase}"]']
            + [f'    {_ident(t)}["{t}"]' for t in targets]
            + ["  end"]
        )
    lines += (
        ['  subgraph CHAIN["verify · eval · install"]']
        + [f'    {_ident(t)}["{t}"]' for t in CHAIN]
        + ["  end"]
    )
    # A phase whose every stamped target feeds `build` collapses to one edge.
    for phase, targets in phases.items():
        stamped = [t for t in targets if t in owner.values()]
        if stamped and all((t, "build") in edges for t in stamped):
            edges = [e for e in edges if not (e[1] == "build" and e[0] in targets)]
            lines.append(f"  P{re.sub(r'[^A-Za-z0-9]', '', phase)} --> n_build")
    lines += [f"  {_ident(a)} --> {_ident(b)}" for a, b in edges] + ["```"]
    return "\n".join(lines)


def main() -> int:
    write = "--write" in sys.argv[1:]
    text = README.read_text(encoding="utf-8")
    head, _, rest = text.partition(BEGIN)
    current, marker, tail = rest.partition(END)
    if not marker:
        sys.exit(f"etl_graph: {README.name} lacks the {BEGIN} / {END} markers")
    fresh = "\n" + render() + "\n"
    if write:
        README.write_text(head + BEGIN + fresh + END + tail, encoding="utf-8")
        print("etl_graph: README.md stage map written")
        return 0
    if current != fresh:
        print("etl_graph: README.md stage map is stale -- run `make graph`")
        return 1
    print("etl_graph: README.md stage map is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
