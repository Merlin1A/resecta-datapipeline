"""Guards on the SOURCES.md ledger check (``common/sources_ledger.py``).

Each exception the ledger section grants is exact: a third absent row, an
unlisted gated row, an orphan file and a prose license cell each fail; the
listed ones pass; an ``AND`` expression is judged operand by operand.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from resecta_data.common import sources_ledger as ledger
from resecta_data.common.licensing import SourceEntry, parse_sources_md

LEDGER_SECTION = """
## Ledger check (`make lint`)

### Rows whose file is fetched on demand (licence checked; file check skipped)

- `data/sources/big.gz` — fetched on demand

### Gated licences allowed after the maintainer's review (path-exact)

- `CC-BY-4.0` for `data/sources/big.gz` — reviewed

### Tracked files under `sources/` with no row

- `data/sources/notes.md` — a note, not an input
"""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _row(path: str, license_cell: str, sha: str) -> str:
    url = f"https://example.invalid/{path}"
    return f"| {path} | {license_cell} | {url} | 2026-01-01 | {sha} | fixture |"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "data" / "sources").mkdir(parents=True)
    (tmp_path / "data" / "sources" / "a.txt").write_bytes(b"alpha")
    return tmp_path


def _parse(repo: Path, rows: list[str]) -> tuple[list[SourceEntry], ledger.LedgerAllow]:
    text = "\n".join(rows) + "\n" + LEDGER_SECTION
    path = repo / "SOURCES.md"
    path.write_text(text, encoding="utf-8")
    return parse_sources_md(path), ledger.parse_allow(text)


GOOD_ROW = _row("data/sources/a.txt", "Public Domain", _sha(b"alpha"))
ON_DEMAND_ROW = _row("data/sources/big.gz", "CC-BY-4.0", "0" * 64)
TRACKED = [
    "data/sources/a.txt",
    "data/sources/a.txt.sha256",
    "data/sources/notes.md",
    "data/sources/notes.md.sha256",
]


def test_parse_allow_reads_the_three_lists() -> None:
    allow = ledger.parse_allow(LEDGER_SECTION)
    assert allow.on_demand == frozenset({"data/sources/big.gz"})
    assert allow.gated == frozenset({("data/sources/big.gz", "CC-BY-4.0")})
    assert allow.rowless == frozenset({"data/sources/notes.md"})


def test_listed_rows_and_files_pass(repo: Path) -> None:
    entries, allow = _parse(repo, [GOOD_ROW, ON_DEMAND_ROW])
    assert ledger.check(entries, TRACKED, allow, repo) == []


def test_and_expression_is_judged_operand_by_operand(repo: Path) -> None:
    mixed = _row("data/sources/a.txt", "Public Domain AND CC0-1.0 AND Apache-2.0", _sha(b"alpha"))
    entries, allow = _parse(repo, [mixed])
    assert ledger.check(entries, TRACKED[:2], allow, repo) == []
    forbidden = _row("data/sources/a.txt", "Public Domain AND CPT", _sha(b"alpha"))
    entries, allow = _parse(repo, [forbidden])
    offences = ledger.check(entries, TRACKED[:2], allow, repo)
    assert len(offences) == 1 and "'CPT' is forbidden" in offences[0]


def test_a_third_absent_row_fails(repo: Path) -> None:
    absent = _row("data/sources/missing.txt", "Public Domain", "1" * 64)
    entries, allow = _parse(repo, [GOOD_ROW, ON_DEMAND_ROW, absent])
    offences = ledger.check(entries, TRACKED, allow, repo)
    assert len(offences) == 1 and "data/sources/missing.txt" in offences[0]
    assert "file is missing" in offences[0]


def test_an_unlisted_gated_row_fails(repo: Path) -> None:
    gated = _row("data/sources/a.txt", "CC-BY-4.0", _sha(b"alpha"))
    entries, allow = _parse(repo, [gated])
    offences = ledger.check(entries, TRACKED[:2], allow, repo)
    assert len(offences) == 1 and "not listed as reviewed" in offences[0]


def test_a_prose_license_cell_fails(repo: Path) -> None:
    prose = _row("data/sources/a.txt", "Public Domain (factual)", _sha(b"alpha"))
    entries, allow = _parse(repo, [prose])
    offences = ledger.check(entries, TRACKED[:2], allow, repo)
    assert len(offences) == 1 and "not an allowlisted identifier" in offences[0]


def test_an_orphan_and_its_sidecar_fail(repo: Path) -> None:
    entries, allow = _parse(repo, [GOOD_ROW, ON_DEMAND_ROW])
    tracked = [*TRACKED, "data/sources/orphan.json", "data/sources/orphan.json.sha256"]
    offences = ledger.check(entries, tracked, allow, repo)
    assert [line.split(":")[0] for line in offences] == [
        "data/sources/orphan.json",
        "data/sources/orphan.json.sha256",
    ]


def test_a_sha_mismatch_fails(repo: Path) -> None:
    stale = _row("data/sources/a.txt", "Public Domain", _sha(b"beta"))
    entries, allow = _parse(repo, [stale])
    offences = ledger.check(entries, TRACKED[:2], allow, repo)
    assert len(offences) == 1 and "SHA-256 mismatch" in offences[0]


def test_the_repository_ledger_is_clean() -> None:
    """The real SOURCES.md and the tracked sources pass the check (what ``make lint`` runs)."""
    root = Path(__file__).resolve().parent.parent
    assert ledger.main([str(root)]) == 0
