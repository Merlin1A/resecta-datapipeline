"""Tests for the multi-source corpus ingest / merge step."""

from __future__ import annotations

import gzip
import hashlib
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

from resecta_data.bloom.corpus_ingest import (
    _MAX_BUILD_WORKERS,
    DEMOGRAPHIC_LABELS,
    IngestedRow,
    compute_sources_fingerprint,
    ingest_result_from_canonical_dict,
    ingest_result_to_canonical_dict,
    merge,
    parse_census_spanish_full,
    parse_census_surnames,
    parse_paranames_full,
    parse_popnames_fullfile,
    parse_sources_parallel,
    parse_ssa_given_names,
)
from resecta_data.common.exceptions import MissingSourceError, PipelineError

_SOURCES = Path(__file__).parent.parent / "src" / "resecta_data" / "gazetteers" / "sources"

_FORENAMES_HEADER = (
    "Country,Country Group,Region,Population,Note,Year,Romanization,"
    "Index,Name Group,Gender,Localized Name,Romanized Name"
)
_SURNAMES_HEADER = "Country,Rank,Index,Name Group,Localized Name,Romanized Name,Count,Percent"


def test_merge_deduplicates_by_normalized_key() -> None:
    rows = [
        IngestedRow("smith", "source_a", "white"),
        IngestedRow("smith", "source_b", "white"),
        IngestedRow("garcia", "source_b", "hispanic"),
    ]
    result = merge(rows)
    assert result.rows == ("garcia", "smith")
    assert result.per_source_counts == {"source_a": 1, "source_b": 1}
    assert result.per_demographic_counts["white"] == 1
    assert result.per_demographic_counts["hispanic"] == 1


def test_merge_output_is_sorted() -> None:
    rows = [IngestedRow(x, "s", "unlabeled") for x in ("z", "a", "m", "b")]
    assert merge(rows).rows == ("a", "b", "m", "z")


def test_merge_hash_is_stable_across_insertion_order() -> None:
    rows_a = [IngestedRow("a", "s", "unlabeled"), IngestedRow("b", "s", "unlabeled")]
    rows_b = [IngestedRow("b", "s", "unlabeled"), IngestedRow("a", "s", "unlabeled")]
    assert merge(rows_a).source_hash == merge(rows_b).source_hash


def test_census_surnames_bucket_distribution() -> None:
    """The bootstrap sample should populate every labeled demographic bucket."""
    rows = parse_census_surnames(
        _SOURCES / "census_surnames/census_surnames_bootstrap_20260416.csv",
        "census_surnames",
    )
    demos = {r.demographic for r in rows}
    for label in ("white", "black", "hispanic", "asian", "ai_an"):
        assert label in demos, f"census bootstrap missing plurality for {label}"


def test_ssa_rows_are_unlabeled() -> None:
    rows = parse_ssa_given_names(
        _SOURCES / "ssa_given_names/ssa_bootstrap_20260416.txt",
        "ssa_given_names",
    )
    assert rows
    assert all(r.demographic == "unlabeled" for r in rows)


def test_demographic_labels_are_canonical() -> None:
    """Every demographic label the ingest emits must be in the canonical tuple."""
    assert set(DEMOGRAPHIC_LABELS) == {"white", "black", "hispanic", "asian", "ai_an", "unlabeled"}


def test_parse_popnames_fullfile_forenames_schema(tmp_path: Path) -> None:
    path = tmp_path / "common-forenames-by-country.csv"
    path.write_text(
        "\n".join(
            [
                _FORENAMES_HEADER,
                "AD,1,,,,2018,N,1,N-AD-1-F-1,F,Martina,Martina",
                "AD,1,,,,2018,N,2,N-AD-1-M-1,M,Jordi,Jordi",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = parse_popnames_fullfile(path, "popnames_common_forenames", "given")
    assert rows == [
        IngestedRow("martina", "popnames_common_forenames", "unlabeled"),
        IngestedRow("jordi", "popnames_common_forenames", "unlabeled"),
    ]


def test_parse_popnames_fullfile_surnames_schema_with_bom(tmp_path: Path) -> None:
    path = tmp_path / "common-surnames-by-country.csv"
    # Upstream surnames file ships with a UTF-8 BOM on the first line; the
    # parser uses utf-8-sig to strip it so DictReader sees "Country" rather
    # than "\ufeffCountry".
    path.write_text(
        "\ufeff"
        + "\n".join(
            [
                _SURNAMES_HEADER,
                "AM,1,1,AM-1,Գրիգորյան,Grigoryan,,",
                "US,1,2,US-1,Smith,Smith,100,0.5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = parse_popnames_fullfile(path, "popnames_common_surnames", "surname")
    assert rows == [
        IngestedRow("grigoryan", "popnames_common_surnames", "unlabeled"),
        IngestedRow("smith", "popnames_common_surnames", "unlabeled"),
    ]


def test_parse_popnames_fullfile_missing_romanized_column(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text("Country,Localized Name\nAM,Գրիգորյան\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="Romanized Name"):
        parse_popnames_fullfile(path, "popnames_common_surnames", "surname")


def test_parse_popnames_fullfile_empty_romanized_skipped(tmp_path: Path) -> None:
    path = tmp_path / "with-empties.csv"
    path.write_text(
        "\n".join(
            [
                _SURNAMES_HEADER,
                "CN,1,1,CN-1,李,,,",  # empty Romanized Name
                "CN,2,2,CN-2,王,   ,,",  # whitespace-only Romanized Name
                "US,3,3,US-3,Smith,Smith,100,0.5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = parse_popnames_fullfile(path, "popnames_common_surnames", "surname")
    assert rows == [IngestedRow("smith", "popnames_common_surnames", "unlabeled")]


def test_parse_popnames_fullfile_unknown_name_type(tmp_path: Path) -> None:
    path = tmp_path / "any.csv"
    path.write_text(_SURNAMES_HEADER + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="name_type"):
        parse_popnames_fullfile(path, "popnames_common_surnames", "patronymic")


def test_parse_popnames_fullfile_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MissingSourceError):
        parse_popnames_fullfile(
            tmp_path / "does-not-exist.csv",
            "popnames_common_surnames",
            "surname",
        )


def test_parse_popnames_fullfile_determinism(tmp_path: Path) -> None:
    path = tmp_path / "common-surnames-by-country.csv"
    path.write_text(
        "\ufeff"
        + "\n".join(
            [
                _SURNAMES_HEADER,
                "AM,1,1,AM-1,Գրիգորյան,Grigoryan,,",
                "US,1,2,US-1,Smith,Smith,100,0.5",
                "ES,1,3,ES-1,García,Garcia,200,0.7",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    first = parse_popnames_fullfile(path, "popnames_common_surnames", "surname")
    second = parse_popnames_fullfile(path, "popnames_common_surnames", "surname")
    assert first == second


# ---- ParaNames full-file adapter -------------------------------------------

_PARANAMES_FULL_FIXTURE = Path(__file__).parent / "fixtures" / "paranames_full_mini.tsv"


def _gzip_fixture(tmp_path: Path, body: str) -> Path:
    """Write ``body`` as a gzipped UTF-8 TSV under tmp_path and return the path."""
    path = tmp_path / "paranames_full.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(body)
    return path


def _gzip_mini(tmp_path: Path) -> Path:
    return _gzip_fixture(tmp_path, _PARANAMES_FULL_FIXTURE.read_text(encoding="utf-8"))


def test_parse_paranames_full_determinism(tmp_path: Path) -> None:
    path = _gzip_mini(tmp_path)
    first = list(parse_paranames_full(path, "paranames_full"))
    second = list(parse_paranames_full(path, "paranames_full"))
    assert first == second


def test_parse_paranames_full_filters(tmp_path: Path) -> None:
    path = _gzip_mini(tmp_path)
    rows = list(parse_paranames_full(path, "paranames_full"))
    normalized = {r.normalized for r in rows}

    # Q0001 (PER, 5 distinct languages): "Albert Einstein" composite label.
    assert "albert einstein" in normalized
    # Q0004 (PER, 5 distinct languages): both ASCII and accented label variants.
    assert "maria garcia" in normalized
    assert "maría garcía" in normalized

    # Q0002 (LOC) is filtered out by type, even with 5 distinct languages.
    assert not any("berlin" in r.normalized for r in rows)
    # Q0003 (PER, only 3 distinct languages) is below the 5-language threshold.
    assert "john doe" not in normalized
    assert "john" not in normalized
    # Q0005 (PER, only 2 distinct languages) is below threshold.
    assert "sample person" not in normalized

    # All emissions carry the source ID and the unlabeled demographic.
    assert all(r.source_id == "paranames_full" for r in rows)
    assert all(r.demographic == "unlabeled" for r in rows)


def test_parse_paranames_full_languages_filter_rejects_underflow(
    tmp_path: Path,
) -> None:
    # With languages restricted to {"es"}, every group has at most 1 Spanish
    # row in this fixture, which fails the 5-distinct-language threshold.
    path = _gzip_mini(tmp_path)
    rows = list(
        parse_paranames_full(
            path,
            "paranames_full",
            languages=frozenset({"es"}),
        )
    )
    assert rows == []


def test_parse_paranames_full_emits_composite_label_only(tmp_path: Path) -> None:
    path = _gzip_mini(tmp_path)
    rows = list(parse_paranames_full(path, "paranames_full"))
    normalized = {r.normalized for r in rows}
    # Curated contract: "Albert Einstein" yields the composite label only; the
    # bare whitespace-split single tokens are not emitted (membership-FPR
    # reduction — the single tokens are the dominant collision source).
    assert "albert einstein" in normalized
    assert "albert" not in normalized
    assert "einstein" not in normalized


def test_parse_paranames_full_raises_on_noncontiguous_wikidata_id(
    tmp_path: Path,
) -> None:
    body = (
        "\n".join(
            [
                "wikidata_id\teng\tlabel\tlanguage\ttype",
                "Q0001\tFoo\tFoo\ten\tPER",
                "Q0001\tFoo\tFoo\tes\tPER",
                "Q0002\tBar\tBar\ten\tPER",
                "Q0001\tFoo\tFoo\tfr\tPER",
            ]
        )
        + "\n"
    )
    path = _gzip_fixture(tmp_path, body)
    with pytest.raises(PipelineError, match="non-contiguously"):
        list(parse_paranames_full(path, "paranames_full"))


def test_parse_paranames_full_missing_columns(tmp_path: Path) -> None:
    body = "wikidata_id\tlabel\n" + "Q0001\tFoo\n"
    path = _gzip_fixture(tmp_path, body)
    with pytest.raises(PipelineError, match="missing columns"):
        list(parse_paranames_full(path, "paranames_full"))


def test_parse_paranames_full_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MissingSourceError):
        list(parse_paranames_full(tmp_path / "nope.tsv.gz", "paranames_full"))


# ---- Census Spanish full-file adapter ----------------------------------------

_CENSUS_SPANISH_FULL_HEADER = (
    "name,rank,count,prop100k,cum_prop100k,pctwhite,pctblack,pctapi,pctaian,pct2prace,pcthispanic"
)
_CENSUS_SPANISH_FULL_ROWS = [
    "GARCIA,1,1166120,432.41,432.41,5.42,0.52,0.49,0.59,1.39,91.60",
    "RODRIGUEZ,2,1094924,405.91,838.32,5.30,0.52,0.40,0.51,1.19,92.08",
    "MARTINEZ,3,1060159,393.02,1231.35,7.00,0.55,0.50,0.58,1.39,90.00",
    "HERNANDEZ,4,1043281,386.76,1618.11,5.39,0.50,0.39,0.53,1.29,91.90",
    "LOPEZ,5,874523,324.21,1942.32,6.22,0.61,0.58,0.57,1.30,90.72",
]


def _write_census_spanish_full_csv(tmp_path: Path, name: str = "app_c.csv") -> Path:
    path = tmp_path / name
    body = _CENSUS_SPANISH_FULL_HEADER + "\n" + "\n".join(_CENSUS_SPANISH_FULL_ROWS) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def _write_census_spanish_full_zip(tmp_path: Path) -> Path:
    csv_path = _write_census_spanish_full_csv(tmp_path, name="app_c_source.csv")
    body = csv_path.read_text(encoding="utf-8")
    zip_path = tmp_path / "census_spanish_full.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("app_c.csv", body)
    return zip_path


def test_parse_census_spanish_full_determinism(tmp_path: Path) -> None:
    path = _write_census_spanish_full_csv(tmp_path)
    first = parse_census_spanish_full(path, "census_spanish_full")
    second = parse_census_spanish_full(path, "census_spanish_full")
    assert first == second


def test_parse_census_spanish_full_hispanic_attribution(tmp_path: Path) -> None:
    path = _write_census_spanish_full_csv(tmp_path)
    rows = parse_census_spanish_full(path, "census_spanish_full")
    assert rows == [
        IngestedRow("garcia", "census_spanish_full", "hispanic"),
        IngestedRow("rodriguez", "census_spanish_full", "hispanic"),
        IngestedRow("martinez", "census_spanish_full", "hispanic"),
        IngestedRow("hernandez", "census_spanish_full", "hispanic"),
        IngestedRow("lopez", "census_spanish_full", "hispanic"),
    ]


def test_parse_census_spanish_full_zip_dispatch(tmp_path: Path) -> None:
    zip_path = _write_census_spanish_full_zip(tmp_path)
    rows = parse_census_spanish_full(zip_path, "census_spanish_full")
    assert [r.normalized for r in rows] == [
        "garcia",
        "rodriguez",
        "martinez",
        "hernandez",
        "lopez",
    ]
    assert all(r.demographic == "hispanic" for r in rows)
    assert all(r.source_id == "census_spanish_full" for r in rows)


def test_parse_census_spanish_full_zip_missing_inner_member(tmp_path: Path) -> None:
    zip_path = tmp_path / "wrong_member.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("other.csv", _CENSUS_SPANISH_FULL_HEADER + "\n")
    with pytest.raises(PipelineError, match=r"app_c\.csv"):
        parse_census_spanish_full(zip_path, "census_spanish_full")


def test_parse_census_spanish_full_xlsx_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.xlsx"
    path.write_bytes(b"PK\x03\x04")  # ZIP magic stand-in; adapter dispatches on suffix, not content
    with pytest.raises(PipelineError, match="XLSX reader"):
        parse_census_spanish_full(path, "census_spanish_full")


def test_parse_census_spanish_full_xls_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.xls"
    path.write_bytes(b"")
    with pytest.raises(PipelineError, match="XLSX reader"):
        parse_census_spanish_full(path, "census_spanish_full")


def test_parse_census_spanish_full_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MissingSourceError):
        parse_census_spanish_full(tmp_path / "nope.csv", "census_spanish_full")


def test_parse_census_spanish_full_missing_name_column(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text("surname,rank\nGARCIA,1\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="'name'"):
        parse_census_spanish_full(path, "census_spanish_full")


def test_parse_census_spanish_full_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(PipelineError, match="unsupported suffix"):
        parse_census_spanish_full(path, "census_spanish_full")


def test_merge_precedence_full_file() -> None:
    """First-wins merge preserves Census 2010 plurality labels for surnames
    present in both sources; census_spanish_full carries the long-tail
    hispanic attribution; paranames_full stays unlabeled.
    """
    rows = [
        # census_surnames leader keeps plurality label "white" for smith.
        IngestedRow("smith", "census_surnames", "white"),
        # census_spanish_full also sees smith; must not override the white label.
        IngestedRow("smith", "census_spanish_full", "hispanic"),
        # "quezada" is absent from census_surnames, present in the full file.
        IngestedRow("quezada", "census_spanish_full", "hispanic"),
        # "einstein" only appears in paranames_full — stays unlabeled.
        IngestedRow("einstein", "paranames_full", "unlabeled"),
    ]
    result = merge(rows)
    assert result.rows == ("einstein", "quezada", "smith")
    assert result.per_source_counts == {
        "census_surnames": 1,
        "census_spanish_full": 1,
        "paranames_full": 1,
    }
    assert result.per_demographic_counts["white"] == 1
    assert result.per_demographic_counts["hispanic"] == 1
    assert result.per_demographic_counts["unlabeled"] == 1


def test_parse_paranames_full_skips_non_per_wikidata_id_collisions(
    tmp_path: Path,
) -> None:
    """The real upstream is sorted by ``(type, wikidata_id)``: an entity can
    appear in the LOC block and again in the PER block under the same wid.
    The adapter must filter to PER **before** the wid-transition logic so
    cross-type reappearances are not flagged as a sort-invariant break.
    """
    body = (
        "\n".join(
            [
                "wikidata_id\teng\tlabel\tlanguage\ttype",
                # LOC block — Q0002 appears here first.
                "Q0002\tBerlin\tBerlin\ten\tLOC",
                "Q0002\tBerlin\tBerlin\tes\tLOC",
                "Q0002\tBerlin\tBerlin\tfr\tLOC",
                "Q0002\tBerlin\tBerlin\tde\tLOC",
                "Q0002\tBerlin\tBerlin\tit\tLOC",
                # PER block — Q0001, then Q0002 reappears as a person entity.
                "Q0001\tFoo\tFoo\ten\tPER",
                "Q0001\tFoo\tFoo\tes\tPER",
                "Q0001\tFoo\tFoo\tfr\tPER",
                "Q0001\tFoo\tFoo\tde\tPER",
                "Q0001\tFoo\tFoo\tit\tPER",
                "Q0002\tBerlinPerson\tBerlinPerson\ten\tPER",
                "Q0002\tBerlinPerson\tBerlinPerson\tes\tPER",
                "Q0002\tBerlinPerson\tBerlinPerson\tfr\tPER",
                "Q0002\tBerlinPerson\tBerlinPerson\tde\tPER",
                "Q0002\tBerlinPerson\tBerlinPerson\tit\tPER",
            ]
        )
        + "\n"
    )
    path = _gzip_fixture(tmp_path, body)
    rows = list(parse_paranames_full(path, "paranames_full"))
    normalized = {r.normalized for r in rows}
    # Both PER entities emit; the prior LOC appearance of Q0002 is invisible.
    assert "foo" in normalized
    assert "berlinperson" in normalized
    # The LOC-block label "berlin" must NOT show up — the adapter dropped
    # those rows before they could touch the grouping state.
    assert "berlin" not in normalized


# ---- Sharded ParaNames (wid-boundary split equivalence) --------------------
#
# ``scripts/shard_paranames.py`` pre-splits the monolithic gzip into
# wid-boundary shards so ``parse_sources_parallel`` can fan across them.
# Because shards emit under the canonical source_id ``"paranames_full"``,
# the merged ``IngestResult`` must be byte-identical to the single-file
# build. This is the hard determinism gate for that optimization.


def _gzip_body(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(body)
    return path


_PARANAMES_HEADER_LINE = "wikidata_id\teng\tlabel\tlanguage\ttype"


def test_sharded_paranames_matches_monolithic(tmp_path: Path) -> None:
    """Parsing N shards + merge ≡ parsing the single file + merge.

    Asserts equality of every field the downstream manifest reads:
    ``rows``, ``per_source_counts``, ``per_demographic_counts``, and
    ``source_hash``. A failure on *any* of these would silently break the
    hash-lock of ``gazetteer_manifest.json``.
    """
    monolithic_body = _PARANAMES_FULL_FIXTURE.read_text(encoding="utf-8")
    monolithic_path = _gzip_body(tmp_path, "paranames_full.tsv.gz", monolithic_body)

    # Split the fixture's PER rows at wid boundaries: shard_00 keeps Q0001
    # (Albert Einstein), shard_01 keeps Q0003..Q0005. The LOC Q0002 is
    # dropped from both shards, exactly as the offline shard script does.
    shard_00_body = (
        _PARANAMES_HEADER_LINE
        + "\n"
        + "\n".join(
            [
                "Q0001\tAlbert Einstein\tAlbert Einstein\ten\tPER",
                "Q0001\tAlbert Einstein\tAlbert Einstein\tes\tPER",
                "Q0001\tAlbert Einstein\tAlbert Einstein\tfr\tPER",
                "Q0001\tAlbert Einstein\tAlbert Einstein\tde\tPER",
                "Q0001\tAlbert Einstein\tAlbert Einstein\tit\tPER",
            ]
        )
        + "\n"
    )
    shard_01_body = (
        _PARANAMES_HEADER_LINE
        + "\n"
        + "\n".join(
            [
                "Q0003\tJohn Doe\tJohn Doe\ten\tPER",
                "Q0003\tJohn Doe\tJuan Doe\tes\tPER",
                "Q0003\tJohn Doe\tJean Doe\tfr\tPER",
                "Q0004\tMaria Garcia\tMaria Garcia\ten\tPER",
                "Q0004\tMaria Garcia\tMaría García\tes\tPER",
                "Q0004\tMaria Garcia\tMaria Garcia\tfr\tPER",
                "Q0004\tMaria Garcia\tMaria Garcia\tde\tPER",
                "Q0004\tMaria Garcia\tMaria Garcia\tit\tPER",
                "Q0005\tSample Person\tSample Person\ten\tPER",
                "Q0005\tSample Person\tSample Person\tes\tPER",
            ]
        )
        + "\n"
    )
    shard_00_path = _gzip_body(tmp_path, "paranames_full_shard_00.tsv.gz", shard_00_body)
    shard_01_path = _gzip_body(tmp_path, "paranames_full_shard_01.tsv.gz", shard_01_body)

    mono_rows = list(parse_paranames_full(monolithic_path, "paranames_full"))
    shard_rows = list(parse_paranames_full(shard_00_path, "paranames_full")) + list(
        parse_paranames_full(shard_01_path, "paranames_full")
    )

    mono_result = merge(mono_rows)
    shard_result = merge(shard_rows)

    assert shard_result.rows == mono_result.rows
    assert shard_result.per_source_counts == mono_result.per_source_counts
    assert shard_result.per_demographic_counts == mono_result.per_demographic_counts
    assert shard_result.source_hash == mono_result.source_hash


# ---- Ingest cache roundtrip ------------------------------------------------
#
# The cache file under ``build/gazetteers/_ingest_cache/`` lets the
# ``build_bloom`` and ``build_demographics`` targets share one parse per
# ``make build`` invocation. Fingerprint invalidation + canonical-dict
# roundtrip are the correctness guarantees.


def test_compute_sources_fingerprint_stable_across_mtime_churn(tmp_path: Path) -> None:
    """mtime churn alone must NOT flip the fingerprint (speed plan #5+F6).

    Fresh checkouts/clones/touches rewrite mtimes without changing bytes;
    the old mtime_ns-keyed fingerprint threw away the ~817 MB warm cache on
    every such churn and re-paid the cold ParaNames parse.
    """
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")
    before = compute_sources_fingerprint([(path, "src")])

    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    after = compute_sources_fingerprint([(path, "src")])

    assert before == after


def test_compute_sources_fingerprint_changes_on_same_size_edit(tmp_path: Path) -> None:
    """A same-size content edit must flip the fingerprint (reviewer F6).

    This is the collision that makes drop-mtime-alone unsound: with a
    path+size+source_id key, editing bytes without changing the length would
    serve a stale cache and build a wrong bloom filter. The content digest
    in the key is what closes it. The mtime is pinned identical on both
    reads so only the bytes differ.
    """
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")
    st = path.stat()
    pinned = (st.st_atime_ns, st.st_mtime_ns)
    before = compute_sources_fingerprint([(path, "src")])

    path.write_text("hellp", encoding="utf-8")
    os.utime(path, ns=pinned)
    after = compute_sources_fingerprint([(path, "src")])

    assert path.stat().st_size == st.st_size
    assert path.stat().st_mtime_ns == st.st_mtime_ns
    assert before != after


def test_compute_sources_fingerprint_changes_on_source_id(tmp_path: Path) -> None:
    """Rewiring a new source_id against the same file flips the fingerprint.

    This guards against the failure mode where a developer adds a new source
    to ``_surname_ingest_specs`` but the cache silently returns stale counts
    because the underlying file didn't change.
    """
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")
    assert compute_sources_fingerprint([(path, "src_v1")]) != compute_sources_fingerprint(
        [(path, "src_v2")]
    )


def test_ingest_result_canonical_dict_roundtrip() -> None:
    rows = [
        IngestedRow("alpha", "src_a", "white"),
        IngestedRow("beta", "src_b", "hispanic"),
        IngestedRow("gamma", "src_a", "unlabeled"),
    ]
    original = merge(rows)
    payload = ingest_result_to_canonical_dict(original)
    recovered = ingest_result_from_canonical_dict(payload)

    assert recovered.rows == original.rows
    assert recovered.per_source_counts == original.per_source_counts
    assert recovered.per_demographic_counts == original.per_demographic_counts
    assert recovered.source_hash == original.source_hash


def test_ingest_result_from_canonical_dict_rejects_malformed() -> None:
    with pytest.raises(PipelineError):
        ingest_result_from_canonical_dict({"rows": "not a list"})
    with pytest.raises(PipelineError):
        ingest_result_from_canonical_dict({})


# ---- parse_sources_parallel: cap, env guard, order preservation -----------
#
# These specs are module-level so ``ProcessPoolExecutor`` can pickle them
# when the parallel path runs. Each returns a small distinguishable list of
# ``IngestedRow`` so order- and equality-based assertions are exact.


def _spec_alpha() -> list[IngestedRow]:
    return [
        IngestedRow("alpha_one", "spec_alpha", "white"),
        IngestedRow("alpha_two", "spec_alpha", "white"),
    ]


def _spec_bravo() -> list[IngestedRow]:
    return [
        IngestedRow("bravo_one", "spec_bravo", "black"),
        IngestedRow("bravo_two", "spec_bravo", "black"),
        IngestedRow("bravo_three", "spec_bravo", "black"),
    ]


def _spec_charlie() -> list[IngestedRow]:
    return [
        IngestedRow("charlie_one", "spec_charlie", "hispanic"),
    ]


def _spec_delta() -> list[IngestedRow]:
    return [
        IngestedRow("delta_one", "spec_delta", "asian"),
    ]


def _spec_echo() -> list[IngestedRow]:
    return [
        IngestedRow("echo_one", "spec_echo", "ai_an"),
    ]


def test_parse_sources_parallel_preserves_spec_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submission order drives output order regardless of completion order."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    specs = [_spec_alpha, _spec_bravo, _spec_charlie]
    rows = parse_sources_parallel(specs, workers=2)
    assert rows == _spec_alpha() + _spec_bravo() + _spec_charlie()


def test_parse_sources_parallel_caps_workers_at_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pool size is clamped to ``_MAX_BUILD_WORKERS`` regardless of caller intent."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    captured: dict[str, int] = {}

    class _RecordingPool:
        def __init__(self, max_workers: int) -> None:
            captured["max_workers"] = max_workers

        def __enter__(self) -> _RecordingPool:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def map(self, fn: Any, items: Any, chunksize: int = 1) -> Any:
            return (fn(s) for s in items)

        def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
            return None

    monkeypatch.setattr(
        "resecta_data.bloom.corpus_ingest.ProcessPoolExecutor",
        _RecordingPool,
    )
    specs = [_spec_alpha, _spec_bravo, _spec_charlie, _spec_delta, _spec_echo]
    parse_sources_parallel(specs, workers=100)
    assert "max_workers" in captured
    assert captured["max_workers"] <= _MAX_BUILD_WORKERS


def test_parse_sources_parallel_respects_pytest_xdist_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under xdist with ``workers=None``, the parent must take the serial path."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "ProcessPoolExecutor must not be instantiated under PYTEST_XDIST_WORKER"
        )

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setattr(
        "resecta_data.bloom.corpus_ingest.ProcessPoolExecutor",
        _boom,
    )
    specs = [_spec_alpha, _spec_bravo, _spec_charlie]
    rows = parse_sources_parallel(specs, workers=None)
    assert rows == _spec_alpha() + _spec_bravo() + _spec_charlie()


def test_parse_sources_parallel_honors_build_workers_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RESECTA_BUILD_WORKERS`` caps the pool size when ``workers`` is left default."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "2")
    captured: dict[str, int] = {}

    class _RecordingPool:
        def __init__(self, max_workers: int) -> None:
            captured["max_workers"] = max_workers

        def __enter__(self) -> _RecordingPool:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def map(self, fn: Any, items: Any, chunksize: int = 1) -> Any:
            return (fn(s) for s in items)

        def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
            return None

    monkeypatch.setattr(
        "resecta_data.bloom.corpus_ingest.ProcessPoolExecutor",
        _RecordingPool,
    )
    specs = [_spec_alpha, _spec_bravo, _spec_charlie, _spec_delta, _spec_echo]
    parse_sources_parallel(specs, workers=None)
    assert captured["max_workers"] == 2


def test_parse_sources_parallel_build_workers_env_clamped_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized ``RESECTA_BUILD_WORKERS`` value still clamps to ``_MAX_BUILD_WORKERS``."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", "100")
    captured: dict[str, int] = {}

    class _RecordingPool:
        def __init__(self, max_workers: int) -> None:
            captured["max_workers"] = max_workers

        def __enter__(self) -> _RecordingPool:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def map(self, fn: Any, items: Any, chunksize: int = 1) -> Any:
            return (fn(s) for s in items)

        def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
            return None

    monkeypatch.setattr(
        "resecta_data.bloom.corpus_ingest.ProcessPoolExecutor",
        _RecordingPool,
    )
    specs = [_spec_alpha, _spec_bravo, _spec_charlie, _spec_delta, _spec_echo]
    parse_sources_parallel(specs, workers=None)
    assert captured["max_workers"] <= _MAX_BUILD_WORKERS


@pytest.mark.parametrize("bad_value", ["abc", "0"])
def test_parse_sources_parallel_build_workers_env_invalid_ignored(
    monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    """Unparseable or non-positive env values fall back to the default cap path."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("RESECTA_BUILD_WORKERS", bad_value)
    captured: dict[str, int] = {}

    class _RecordingPool:
        def __init__(self, max_workers: int) -> None:
            captured["max_workers"] = max_workers

        def __enter__(self) -> _RecordingPool:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def map(self, fn: Any, items: Any, chunksize: int = 1) -> Any:
            return (fn(s) for s in items)

        def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
            return None

    monkeypatch.setattr(
        "resecta_data.bloom.corpus_ingest.ProcessPoolExecutor",
        _RecordingPool,
    )
    specs = [_spec_alpha, _spec_bravo, _spec_charlie, _spec_delta, _spec_echo]
    parse_sources_parallel(specs, workers=None)
    # Invalid env => env override is a no-op; default path clamps at _MAX_BUILD_WORKERS.
    assert captured["max_workers"] == _MAX_BUILD_WORKERS


def test_parse_sources_parallel_byte_identical_serial_vs_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial and pool-driven runs over the same specs produce identical hashes."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    specs = [_spec_alpha, _spec_bravo, _spec_charlie, _spec_delta]
    serial = parse_sources_parallel(specs, workers=1)
    parallel = parse_sources_parallel(specs, workers=4)

    def _digest(rows: list[IngestedRow]) -> str:
        sorted_rows = sorted((r.normalized, r.source_id, r.demographic) for r in rows)
        return hashlib.sha256(repr(sorted_rows).encode("utf-8")).hexdigest()

    assert _digest(serial) == _digest(parallel)
