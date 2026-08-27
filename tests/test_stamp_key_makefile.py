"""Make-level guarantees of the content-keyed stamps (speed plan #4).

`test_stamp_key.py` proves the keyer; this file proves the *wiring* — the
part a quiet Makefile edit could weaken without any Python test noticing:

1. **Closure membership** (real Makefile, via `make -np` database): every
   stamp rule's prerequisites still cover the input classes the key must
   cover — cli.py + all of common/ everywhere; per-builder sources on their
   stamp; the bloom data corpus on bloom; the 17 stamps + asset_hashes.lock
   on the determinism witness. The key hashes ``$^``, so prereq coverage IS
   key coverage (reviewer R2's floor for #4).
2. **Recipes consult the keyer over ``$@ $^``** — nobody can drop the
   mechanism or hand it a hand-rolled file list without failing here.
3. **End-to-end pattern harness** (fixture Makefile in a tmpdir): mtime-only
   churn fires recipes but runs no builder and keeps a witness-style rule
   green; content changes in each fixture input class re-run exactly the
   affected work; a deleted stamp rebuilds without staling the witness when
   its closure is unchanged.

Requires GNU make >= 4 (gmake on macOS, make on CI's ubuntu); skipped when
absent — the unit tests above still cover the keyer itself there.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_gnu_make_4() -> str | None:
    for cand in ("gmake", "make"):
        path = shutil.which(cand)
        if path is None:
            continue
        out = subprocess.run(  # noqa: S603 — fixed argv, no user input
            [path, "--version"], capture_output=True, text=True, check=False
        ).stdout
        first = out.splitlines()[0] if out else ""
        if "GNU Make" in first and first.split()[-1].split(".")[0] in ("4", "5"):
            return path
    return None


_MAKE = _find_gnu_make_4()

pytestmark = pytest.mark.skipif(
    _MAKE is None, reason="GNU make >= 4 not available (brew install make)"
)


def _clean_env() -> dict[str, str]:
    """Subprocess env without inherited make state (jobserver flags etc.)."""
    env = dict(os.environ)
    for var in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
        env.pop(var, None)
    return env


@pytest.fixture(scope="module")
def make_db() -> str:
    """The real Makefile's parsed database (rules with expanded prereqs)."""
    assert _MAKE is not None
    result = subprocess.run(  # noqa: S603 — fixed argv against the repo Makefile
        [_MAKE, "-np", "-f", "Makefile"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_clean_env(),
        check=False,
    )
    assert result.stdout, f"make -np produced no database: {result.stderr[-2000:]}"
    return result.stdout


STAMPS = [
    "vectors",
    "fuzz",
    "zip-scf",
    "adversarial",
    "bloom",
    "gaz-negctx",
    "gaz-institutions",
    "gaz-address",
    "passport-patterns",
    "dl-patterns",
    "context",
    "rules",
    "demographics",
    "classifier",
    "corpus",
    "g8-bucket-recall",
    "bundle-size",
]


def _rule(db: str, target: str) -> tuple[list[str], str]:
    """Return (normal prereqs, recipe text) for ``target`` from the database.

    ``make -p`` interleaves ``#``-comment status lines between the target
    line and the tab-prefixed recipe; both are tolerated, only tab lines
    are returned as recipe text.
    """
    pattern = re.compile(rf"^{re.escape(target)}:([^\n]*)\n((?:(?:\t|#)[^\n]*\n)*)", re.M)
    match = pattern.search(db)
    assert match is not None, f"no rule for {target} in make database"
    prereq_part = match.group(1).split("|", 1)[0]  # drop order-only prereqs
    recipe = "".join(
        line for line in match.group(2).splitlines(keepends=True) if line.startswith("\t")
    )
    return prereq_part.split(), recipe


def test_every_stamp_covers_common_deps(make_db: str) -> None:
    """cli.py and every common/*.py module must stay in every stamp's
    closure — they are the irreducible shared writer set."""
    common_files = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "src/resecta_data/common").glob("*.py")
    }
    assert common_files, "no common/*.py found — wrong repo root?"
    for stamp in STAMPS:
        prereqs, _ = _rule(make_db, f"build/.stamps/{stamp}")
        missing = ({"src/resecta_data/cli.py"} | common_files) - set(prereqs)
        assert not missing, f"stamp {stamp} lost closure coverage of: {sorted(missing)}"


def test_builder_sources_stay_in_their_stamp_closure(make_db: str) -> None:
    """Spot-pin one stamp per input class: builder code, data sources, and
    the bloom corpus. The bloom corpus pins are hydration-conditional,
    mirroring the Makefile: full file + shards + meta are hard inputs on a
    hydrated tree; without the fetch-on-demand corpus the closure must hold
    only paths that exist (plus the committed bootstrap sample)."""
    vectors_prereqs, _ = _rule(make_db, "build/.stamps/vectors")
    vectors_py = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "src/resecta_data/vectors").rglob("*.py")
    }
    assert vectors_py and vectors_py <= set(vectors_prereqs)

    bloom_prereqs, _ = _rule(make_db, "build/.stamps/bloom")
    bloom_set = set(bloom_prereqs)
    paranames = "src/resecta_data/gazetteers/sources/paranames"
    full_corpus = REPO_ROOT / paranames / "paranames_full.tsv.gz"
    if full_corpus.is_file() and full_corpus.stat().st_size > 1_000_000:
        # Hydrated: full corpus, shard sentinel, and meta sidecar are hard
        # inputs, so a re-fetched corpus retriggers sharding + rebuild.
        assert f"{paranames}/paranames_full.tsv.gz" in bloom_set
        assert f"{paranames}/shards/paranames_full_shard_00.tsv.gz" in bloom_set
        assert "build/gazetteers/paranames_shards.meta.json" in bloom_set
    else:
        # Not hydrated: the shard sentinel never materializes on the
        # fallback path, and keyed_stamp sha256-hashes every prereq in $^ —
        # one absent path in the closure aborts the bootstrap-sample build
        # the CLI is documented to degrade to. ($(wildcard) prereqs keep
        # absent paths out; build/-side inputs are materialized by their
        # own rules.)
        absent = [
            p for p in bloom_set if not p.startswith("build/") and not (REPO_ROOT / p).is_file()
        ]
        assert not absent, f"bloom stamp closure lists absent inputs: {sorted(absent)}"
        bootstrap = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in (REPO_ROOT / paranames).glob("paranames_bootstrap_*.tsv")
        }
        assert bootstrap and bootstrap <= bloom_set
    corpora_data = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "src/resecta_data/gazetteers/sources/ssa_given_names").rglob("*")
        if p.is_file()
    }
    assert corpora_data and corpora_data <= bloom_set


def test_witness_covers_all_stamps_and_lock(make_db: str) -> None:
    prereqs, _ = _rule(make_db, "build/.stamps/.determinism-witness")
    expected = {f"build/.stamps/{s}" for s in STAMPS} | {"asset_hashes.lock"}
    missing = expected - set(prereqs)
    assert not missing, f"witness lost closure coverage of: {sorted(missing)}"


def test_stamp_recipes_consult_the_keyer_over_make_prereqs(make_db: str) -> None:
    """Every stamp recipe and the witness recipe must route through the
    keyed_stamp/check-write mechanism with $@ $^ — the construction that
    makes key coverage equal prereq coverage."""
    for stamp in STAMPS:
        _, recipe = _rule(make_db, f"build/.stamps/{stamp}")
        assert "$(call keyed_stamp," in recipe, f"stamp {stamp} dropped the keyed recipe"
    define = re.search(r"define keyed_stamp\n(.*?)\nendef", make_db, re.S)
    assert define is not None, "keyed_stamp canned recipe not defined"
    assert "$(STAMP_KEY) check $@ $^" in define.group(1)
    assert "$(STAMP_KEY) write $@ $^" in define.group(1)

    _, witness_recipe = _rule(make_db, "build/.stamps/.determinism-witness")
    assert "$(STAMP_KEY) check $@ $^" in witness_recipe
    assert "$(STAMP_KEY) write $@ $^" in witness_recipe
    assert "verify-determinism" in witness_recipe, "witness no longer runs the rebuild"


def test_force_path_never_consults_the_key(make_db: str) -> None:
    """CI's RESECTA_FORCE_DETERMINISM path must stay unconditional: it may
    write the key after a green rebuild but must never *check* one."""
    recipe = _rule(make_db, "determinism-check-force")[1]
    assert "verify-determinism" in recipe
    assert "$(STAMP_KEY) check" not in recipe


# ---------------------------------------------------------------------------
# End-to-end pattern harness
# ---------------------------------------------------------------------------

_HARNESS_MAKEFILE = textwrap.dedent(
    """\
    SHELL := /bin/bash
    .DELETE_ON_ERROR:
    STAMP_KEY := {python} -m resecta_data.common.stamp_key

    stamps/alpha: src_a.txt common.txt
    \t@mkdir -p stamps
    \t@if $(STAMP_KEY) check $@ $^; then \\
    \t\techo "SKIP alpha"; \\
    \telse \\
    \t\techo built-alpha >> build.log && $(STAMP_KEY) write $@ $^; \\
    \tfi
    \t@touch $@

    witness: stamps/alpha lock.txt
    \t@if $(STAMP_KEY) check $@ $^; then \\
    \t\techo "SKIP witness"; \\
    \telse \\
    \t\techo rebuilt-witness >> build.log && $(STAMP_KEY) write $@ $^; \\
    \tfi
    \t@touch $@
    """
)


class _Harness:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "Makefile").write_text(
            _HARNESS_MAKEFILE.format(python=sys.executable), encoding="utf-8"
        )
        (root / "src_a.txt").write_text("a-v1\n", encoding="utf-8")
        (root / "common.txt").write_text("common-v1\n", encoding="utf-8")
        (root / "lock.txt").write_text("lock-v1\n", encoding="utf-8")

    def run(self) -> str:
        assert _MAKE is not None
        result = subprocess.run(  # noqa: S603 — fixed argv against the fixture
            [_MAKE, "witness"],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=_clean_env(),
            check=False,
        )
        assert result.returncode == 0, f"harness make failed:\n{result.stdout}\n{result.stderr}"
        return result.stdout

    @property
    def log(self) -> list[str]:
        path = self.root / "build.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def churn_mtimes(self) -> None:
        """Re-stamp source mtimes to *now* (bytes untouched) — the checkout/
        editor-save class. Real churn is never future-dated, and the recipe's
        catch-up ``touch $@`` must land after these mtimes."""
        time.sleep(0.05)  # APFS ns resolution: guarantee now > stamp mtimes
        for name in ("src_a.txt", "common.txt", "lock.txt"):
            os.utime(self.root / name, None)
        time.sleep(0.05)


@pytest.fixture
def harness(tmp_path: Path) -> _Harness:
    h = _Harness(tmp_path)
    h.run()  # initial build: both rules fire for real
    assert h.log == ["built-alpha", "rebuilt-witness"]
    return h


def test_mtime_churn_fires_recipes_but_runs_nothing(harness: _Harness) -> None:
    """The no-op-churn obligation: recipes fire (mtime graph), keyer skips
    the work, stamps are re-touched so the next run is a true no-op."""
    harness.churn_mtimes()
    out = harness.run()
    assert "SKIP alpha" in out and "SKIP witness" in out
    assert harness.log == ["built-alpha", "rebuilt-witness"]  # nothing re-ran
    out = harness.run()  # mtimes now current: recipes must not even fire
    assert "SKIP" not in out


def test_builder_source_content_change_rebuilds_and_cascades(harness: _Harness) -> None:
    (harness.root / "src_a.txt").write_text("a-v2\n", encoding="utf-8")
    harness.run()
    assert harness.log == ["built-alpha", "rebuilt-witness"] * 2


def test_shared_dep_content_change_rebuilds_and_cascades(harness: _Harness) -> None:
    (harness.root / "common.txt").write_text("common-v2\n", encoding="utf-8")
    harness.run()
    assert harness.log == ["built-alpha", "rebuilt-witness"] * 2


def test_lock_content_change_rebuilds_witness_only(harness: _Harness) -> None:
    (harness.root / "lock.txt").write_text("lock-v2\n", encoding="utf-8")
    harness.run()
    assert harness.log == ["built-alpha", "rebuilt-witness", "rebuilt-witness"]


def test_deleted_stamp_rebuilds_without_staling_witness(harness: _Harness) -> None:
    """A rebuilt stamp over an unchanged closure reproduces the same
    manifest (determinism), so the witness key stays valid — strictly
    cheaper than mtime semantics and sound for the same reason."""
    (harness.root / "stamps/alpha").unlink()
    out = harness.run()
    assert harness.log == ["built-alpha", "rebuilt-witness", "built-alpha"]
    assert "SKIP witness" in out
