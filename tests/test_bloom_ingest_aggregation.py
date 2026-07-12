"""Equivalence guards for the S04 in-worker ingest aggregation.

The aggregated path (``aggregate_rows`` per spec → ``merge_aggregates`` fold,
production entry ``ingest_sources_parallel``) must be byte-equivalent to the
row-stream oracle (``merge(chain(*rows_per_spec))`` plus the raw stream's
distinct source ids). Each test pins one divergence class from the S04 T1
design note (s04-t1-design-ingest-aggregation.md §5):

- D1 cross-spec duplicate keys (earliest spec wins)
- D2 within-spec duplicate keys with conflicting demographics (first wins)
- D3 multi-source spec with a fully-shadowed source (sources list keeps it)
- D4 spec-order permutations (both paths shift attribution identically)
- D7 unknown demographic raises identically
- D8 empty specs / no specs
- D9 serial ≡ parallel ≡ oracle (real pool: pickles AggregatedRows)
- D10 the aggregate structure is order-canonical (explicit sorts)
- D11 Hypothesis property sweep over random spec batches

D5 (tie-breaking) collapses into D1/D2 — "equal keys" are the same dict key
and first-wins is the only rule; D6 (count overflow) is void under Python's
arbitrary-precision ints.
"""

from __future__ import annotations

import itertools
import pickle

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from resecta_data.bloom.corpus_ingest import (
    DEMOGRAPHIC_LABELS,
    AggregatedRows,
    IngestedRow,
    IngestResult,
    aggregate_rows,
    ingest_sources_parallel,
    merge,
    merge_aggregates,
    parse_sources_parallel,
)
from resecta_data.common.exceptions import PipelineError


def _oracle(row_lists: list[list[IngestedRow]]) -> tuple[IngestResult, tuple[str, ...]]:
    """The pre-S04 semantics: merge the concatenated raw stream."""
    rows = list(itertools.chain.from_iterable(row_lists))
    return merge(rows), tuple(sorted({r.source_id for r in rows}))


def _aggregated(row_lists: list[list[IngestedRow]]) -> tuple[IngestResult, tuple[str, ...]]:
    """The S04 path, minus the process pool (pure fold)."""
    return merge_aggregates(aggregate_rows(rows) for rows in row_lists)


def _assert_paths_equal(row_lists: list[list[IngestedRow]]) -> None:
    oracle_result, oracle_sources = _oracle(row_lists)
    agg_result, agg_sources = _aggregated(row_lists)
    assert agg_result == oracle_result
    assert agg_result.source_hash == oracle_result.source_hash
    assert agg_sources == oracle_sources


# ---- D1 / D2 / D3: duplicate-key attribution --------------------------------


def test_cross_spec_duplicate_attributed_to_earliest_spec() -> None:
    spec_a = [IngestedRow("smith", "src_a", "white")]
    spec_b = [
        IngestedRow("smith", "src_b", "hispanic"),
        IngestedRow("garcia", "src_b", "hispanic"),
    ]
    _assert_paths_equal([spec_a, spec_b])
    result, _ = _aggregated([spec_a, spec_b])
    assert result.per_source_counts == {"src_a": 1, "src_b": 1}
    assert result.per_demographic_counts["white"] == 1
    assert result.per_demographic_counts["hispanic"] == 1


def test_within_spec_duplicate_keeps_first_demographic() -> None:
    # Two raw spellings can normalize to the same key with different
    # demographic tags (census rows); the first occurrence must win inside
    # the worker exactly as it would in the concatenated stream.
    spec = [
        IngestedRow("muller", "census", "white"),
        IngestedRow("muller", "census", "hispanic"),
    ]
    _assert_paths_equal([spec])
    result, _ = _aggregated([spec])
    assert result.per_demographic_counts["white"] == 1
    assert result.per_demographic_counts["hispanic"] == 0


def test_fully_shadowed_source_survives_in_sources_list() -> None:
    # src_b contributes no attributed keys (every row duplicates an earlier
    # key within the same spec) but cli derives the manifest sources list
    # from RAW rows — seen_source_ids must keep it.
    spec = [
        IngestedRow("smith", "src_a", "white"),
        IngestedRow("smith", "src_b", "hispanic"),
    ]
    _assert_paths_equal([spec])
    result, sources = _aggregated([spec])
    assert "src_b" not in result.per_source_counts
    assert sources == ("src_a", "src_b")


# ---- D4: spec-order permutations --------------------------------------------


def test_spec_order_permutations_track_the_oracle() -> None:
    spec_a = [IngestedRow("kim", "src_a", "asian"), IngestedRow("lee", "src_a", "asian")]
    spec_b = [IngestedRow("lee", "src_b", "white")]
    spec_c = [IngestedRow("kim", "src_c", "unlabeled"), IngestedRow("park", "src_c", "asian")]
    for order in itertools.permutations([spec_a, spec_b, spec_c]):
        _assert_paths_equal(list(order))


# ---- D7 / D8: failure parity and empties -------------------------------------


def test_unknown_demographic_raises_identically() -> None:
    spec = [IngestedRow("smith", "src", "martian")]
    with pytest.raises(PipelineError, match="Unknown demographic 'martian'") as oracle_exc:
        merge(spec)
    with pytest.raises(PipelineError, match="Unknown demographic 'martian'") as agg_exc:
        merge_aggregates([aggregate_rows(spec)])
    assert str(agg_exc.value) == str(oracle_exc.value)


def test_shadowed_unknown_demographic_does_not_raise() -> None:
    # A bogus label on a non-first duplicate is invisible to merge() —
    # the aggregated path must not start rejecting it.
    spec = [
        IngestedRow("smith", "src", "white"),
        IngestedRow("smith", "src", "martian"),
    ]
    _assert_paths_equal([spec])


def test_empty_specs_and_no_specs() -> None:
    _assert_paths_equal([])
    _assert_paths_equal([[], []])
    result, sources = merge_aggregates([])
    assert result.rows == ()
    assert sources == ()
    assert result == merge([])


# ---- D10: the aggregate is order-canonical -----------------------------------


def test_aggregate_structure_is_sorted() -> None:
    rows = [
        IngestedRow("zeta", "src_b", "white"),
        IngestedRow("alpha", "src_a", "black"),
        IngestedRow("mike", "src_b", "white"),
        IngestedRow("echo", "src_a", "black"),
    ]
    agg = aggregate_rows(rows)
    assert list(agg.groups) == sorted(agg.groups)
    for _pair, keys in agg.groups:
        assert list(keys) == sorted(keys)
    assert list(agg.seen_source_ids) == sorted(agg.seen_source_ids)


def test_duplicate_only_reorders_yield_identical_aggregates() -> None:
    # Moving a NON-first duplicate around must not change the aggregate;
    # equality is structural, so this also pins canonical form.
    base = [
        IngestedRow("ann", "src_a", "white"),
        IngestedRow("bob", "src_b", "black"),
        IngestedRow("ann", "src_b", "black"),
    ]
    moved = [base[0], base[2], base[1]]
    assert aggregate_rows(base) == aggregate_rows(moved)


def test_aggregate_rows_pickle_round_trip() -> None:
    agg = aggregate_rows(
        [
            IngestedRow("ann", "src_a", "white"),
            IngestedRow("bob", "src_b", "unlabeled"),
        ]
    )
    # S301: deserializing bytes this test just produced — the same trusted
    # parent<->worker hop ProcessPoolExecutor performs on AggregatedRows.
    clone = pickle.loads(pickle.dumps(agg, protocol=pickle.HIGHEST_PROTOCOL))  # noqa: S301
    assert isinstance(clone, AggregatedRows)
    assert clone == agg


# ---- D9: serial ≡ parallel ≡ oracle (real pool) -------------------------------
#
# Module-level specs so ProcessPoolExecutor can pickle them (same pattern as
# test_bloom_corpus_ingest.py). Keys overlap across specs on purpose.


def _spec_overlap_one() -> list[IngestedRow]:
    return [
        IngestedRow("ann", "src_one", "white"),
        IngestedRow("bob", "src_one", "white"),
        IngestedRow("cho", "src_one", "asian"),
    ]


def _spec_overlap_two() -> list[IngestedRow]:
    return [
        IngestedRow("bob", "src_two", "black"),
        IngestedRow("dee", "src_two", "black"),
    ]


def _spec_overlap_three() -> list[IngestedRow]:
    return [
        IngestedRow("cho", "src_three", "hispanic"),
        IngestedRow("dee", "src_three", "hispanic"),
        IngestedRow("eli", "src_three", "unlabeled"),
    ]


def test_ingest_sources_parallel_serial_pool_and_oracle_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    specs = [_spec_overlap_one, _spec_overlap_two, _spec_overlap_three]

    oracle_rows = parse_sources_parallel(specs, workers=1)
    oracle_result = merge(oracle_rows)
    oracle_sources = tuple(sorted({r.source_id for r in oracle_rows}))

    serial_result, serial_sources = ingest_sources_parallel(specs, workers=1)
    pooled_result, pooled_sources = ingest_sources_parallel(specs, workers=4)

    assert serial_result == oracle_result
    assert pooled_result == oracle_result
    assert pooled_result.source_hash == oracle_result.source_hash
    assert serial_sources == oracle_sources
    assert pooled_sources == oracle_sources


def test_ingest_sources_parallel_respects_pytest_xdist_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under xdist the default-worker path must stay serial (no nested pools).

    The pool-size resolver is shared with parse_sources_parallel, so this
    mirrors test_parse_sources_parallel_respects_pytest_xdist_env for the
    aggregated entry point: with PYTEST_XDIST_WORKER set, workers=None must
    not construct a ProcessPoolExecutor.
    """
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ProcessPoolExecutor must not be constructed under xdist")

    monkeypatch.setattr("resecta_data.bloom.corpus_ingest.ProcessPoolExecutor", _boom)
    specs = [_spec_overlap_one, _spec_overlap_two]
    result, sources = ingest_sources_parallel(specs, workers=None)
    assert result == _oracle([_spec_overlap_one(), _spec_overlap_two()])[0]
    assert sources == ("src_one", "src_two")


# ---- D11: property sweep ------------------------------------------------------

_KEYS = st.sampled_from(["ann", "bob", "cho", "dee", "eli", "fay"])
_SOURCES = st.sampled_from(["s1", "s2", "s3"])
_DEMOS = st.sampled_from(DEMOGRAPHIC_LABELS)
_ROW = st.builds(IngestedRow, normalized=_KEYS, source_id=_SOURCES, demographic=_DEMOS)
_SPEC_BATCH = st.lists(st.lists(_ROW, max_size=15), max_size=5)


@settings(deadline=None)  # CPU-oversubscribed verify runs; see #21 precedent
@given(_SPEC_BATCH)
def test_aggregated_path_equals_oracle_property(row_lists: list[list[IngestedRow]]) -> None:
    _assert_paths_equal(row_lists)
