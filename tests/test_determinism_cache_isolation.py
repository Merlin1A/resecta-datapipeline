"""F5 guard: the verify-determinism rebuild must re-parse from an EMPTY ingest cache.

Once ``_ingest_cache/`` left the hash-lock surface (``iter_build_artifacts``
exclusion, speed S01 #8), the lockfile no longer transitively pins the parse
output — the side-by-side rebuild's fresh re-parse became the SOLE verifier
of parse-level determinism. These tests pin the two mechanisms that keep it
sole and sound:

1. ``verify-determinism --rebuild-out-dir`` appends ``BUILD_DIR=<fresh dir>``
   to the rebuild command, and the bloom builder derives its cache path from
   that build dir (``_INGEST_CACHE_SUBDIR`` is build-relative) — so the
   rebuild's cache starts empty and the canonical warm cache is never read.
2. Nothing in the snapshot/restore machinery copies or links the canonical
   ``_ingest_cache/`` into the rebuild dir.

Sharing the warm cache into the strict-gate rebuild was evaluated and
REJECTED in the 2026-06-10 speed review: both sides would then derive from a
single parse, making parse-level non-determinism invisible to the gate. If a
future change re-introduces any cache-share, these tests fail.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from resecta_data.cli import _INGEST_CACHE_SUBDIR, main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_ingest_cache_subdir_is_build_relative() -> None:
    """The cache path must be derived from the builder's own --build-dir.

    If this constant ever became absolute (or escaped the build dir), the
    determinism rebuild — which runs with ``BUILD_DIR=<fresh tmpdir>`` —
    would resolve its cache to the canonical warm one and stop re-parsing.
    """
    assert not _INGEST_CACHE_SUBDIR.is_absolute()
    assert _INGEST_CACHE_SUBDIR.parts == ("gazetteers", "_ingest_cache")


def test_rebuild_runs_against_empty_ingest_cache(runner: CliRunner, tmp_path: Path) -> None:
    """The side-by-side rebuild must observe NO pre-populated ingest cache.

    Runs ``verify-determinism --rebuild-out-dir`` against a canonical build
    dir whose ``_ingest_cache/`` is warm, with a probe as the rebuild
    command. The probe records the build dir it was handed and the cache
    state it observed there, then reproduces the artifact set so the
    determinism diff passes.
    """
    canonical = tmp_path / "build"
    warm_cache = canonical / _INGEST_CACHE_SUBDIR
    warm_cache.mkdir(parents=True)
    (warm_cache / "surnames.json").write_text('{"fingerprint": "warm"}', encoding="utf-8")
    (canonical / "a.json").write_text('{"k": 1}', encoding="utf-8")

    rebuild_dir = tmp_path / "rebuild"
    report_path = tmp_path / "probe-report.json"
    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """
            import json, sys
            from pathlib import Path

            report_path = Path(sys.argv[1])
            build_dir = None
            for arg in sys.argv[2:]:
                if arg.startswith("BUILD_DIR="):
                    build_dir = Path(arg.split("=", 1)[1])
            assert build_dir is not None, f"no BUILD_DIR= appended: {sys.argv}"

            cache = build_dir / "gazetteers" / "_ingest_cache"
            report_path.write_text(
                json.dumps(
                    {
                        "build_dir": str(build_dir.resolve()),
                        "cache_exists": cache.is_dir(),
                        "cache_entries": (
                            sorted(p.name for p in cache.rglob("*")) if cache.is_dir() else []
                        ),
                    }
                )
            )
            # Reproduce the canonical artifact set so the diff passes.
            (build_dir / "a.json").write_text('{"k": 1}', encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        main,
        [
            "verify-determinism",
            "--build-dir",
            str(canonical),
            "--rebuild-out-dir",
            str(rebuild_dir),
            "--rebuild-command",
            f'"{sys.executable}" "{probe}" "{report_path}"',
        ],
    )
    assert result.exit_code == 0, result.output

    report = json.loads(report_path.read_text(encoding="utf-8"))
    # The rebuild ran against its own fresh dir, not the canonical one…
    assert report["build_dir"] == str(rebuild_dir.resolve())
    assert report["build_dir"] != str(canonical.resolve())
    # …and observed an EMPTY cache there: the warm canonical cache was not
    # copied, linked, or otherwise shared into the strict-gate rebuild.
    assert report["cache_entries"] == [], (
        "verify-determinism rebuild saw a pre-populated _ingest_cache "
        f"({report['cache_entries']}) — cache-share into the strict gate "
        "defeats parse-level determinism verification."
    )
    # The canonical warm cache itself is untouched by the side-by-side run.
    assert (warm_cache / "surnames.json").read_text(encoding="utf-8") == '{"fingerprint": "warm"}'
