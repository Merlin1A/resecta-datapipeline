"""Read multi-source name corpora and emit sorted, normalized insertion lists.

The ingest step is where source formats meet the filter. Each record-per-name
corpus is one row of a format table read by a single parser (the streaming
ParaNames dump has its own reader); the merge step deduplicates across sources,
normalizes via ``nfkc_lower``, sorts, and emits a list suitable for both
insertion into the Bloom filter and hashing for the RSBF header.

All parsers here are pure: file bytes in, dict out. No network, no caching.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import itertools
import os
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TextIO

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import sha256_file
from resecta_data.common.process import effective_workers, request_parent_death_signal

from .normalize import nfkc_lower

# Demographic labels used by the census surnames corpus and the coverage
# report. Kept stable because they're part of the demographic_coverage schema.
DEMOGRAPHIC_LABELS: Final[tuple[str, ...]] = (
    "white",
    "black",
    "hispanic",
    "asian",
    "ai_an",
    "unlabeled",
)

# Cap parse workers so concurrent parse passes stay bounded on 16-32 GB
# hosts. The aggregated path returns per-spec first-occurrence
# aggregates (~50 MB pickled for a paranames_full shard, measured) instead
# of multi-GB row lists, but the cap is deliberately retained: four
# concurrent gzip+normalize passes are the sized RAM/CPU budget, and the
# legacy list-returning path (parse_sources_parallel) still exists.
# Deliberately lower than bloom/filter.py's cap (8) and corpus/generate.py's
# cap (16).
_MAX_BUILD_WORKERS: Final[int] = 4

# The (source_id, demographic) attribution a merged key carries. Interned
# per-spec during aggregation so millions of keys share one tuple instance.
type SourcePair = tuple[str, str]

# One aggregate group: a shared attribution pair plus the sorted keys
# first-attributed to it within the spec.
type AggregatedGroup = tuple[SourcePair, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class IngestedRow:
    """One normalized name with demographic attribution.

    ``demographic`` is the bucket the row contributes to. A row from a source
    that carries no ethnicity information is tagged ``"unlabeled"``.
    """

    normalized: str
    source_id: str
    demographic: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    """The result of merging multiple corpora.

    Attributes:
        rows: Sorted, deduplicated normalized names. One entry per unique key.
        per_source_counts: {source_id: rows contributed} (post-dedup attribution
            to the earliest source encountered for each key, so counts always
            sum to ``len(rows)``).
        per_demographic_counts: {demographic_label: row count}, same accounting.
        source_hash: SHA-256 of ``"\\n".join(rows)`` UTF-8 bytes.
    """

    rows: tuple[str, ...]
    per_source_counts: dict[str, int]
    per_demographic_counts: dict[str, int]
    source_hash: bytes


# ---- Per-corpus parsers ----------------------------------------------------
# Every record-per-name corpus reads through ``_parse_corpus`` under one
# ``_CorpusFormat`` row; the public ``parse_*`` entries keep their signatures
# (``bloom/paranames_specs.py`` pickles them into the process pool). The
# streaming ParaNames dump keeps its own reader, ``parse_paranames_full``.

type _Record = Sequence[str] | Mapping[str, str | None]
type _Bucket = Callable[[Mapping[str, str | None], int], str]


@dataclass(frozen=True, slots=True)
class _CorpusFormat:
    """How one record-per-name corpus opens and where the name sits in a record:
    ``shape`` is ``"fields"`` (``csv.reader`` rows), ``"header"`` (``csv.DictReader``
    rows, ``required`` columns checked), ``"lines"`` (a name per line) or ``"tsv"``
    (tab-split lines; a line starting with ``header_prefix`` is the header);
    ``name`` is the name field (an index, or a column for ``"header"``)."""

    label: str  # the noun of the not-found error
    shape: str
    name: int | str = 0
    required: tuple[str, ...] = ()
    encoding: str = "utf-8"
    demographic: str = "unlabeled"
    min_fields: int = 1  # fewer on a non-empty record is a PipelineError
    header_prefix: str | None = None
    skip_comment_names: bool = True  # a record whose first field starts with "#"


_SSA_GIVEN_NAMES: Final = _CorpusFormat("SSA given-names file", "fields")
_CENSUS_SURNAMES: Final = _CorpusFormat(
    "Census surname file",
    "header",
    "name",
    ("name", "pctwhite", "pctblack", "pctapi", "pctaian", "pcthispanic"),
)
_CENSUS_SPANISH: Final = _CorpusFormat("Census Spanish file", "lines", demographic="hispanic")
_CENSUS_SPANISH_FULL: Final = _CorpusFormat(
    "Census Spanish full file", "header", "name", ("name",), "utf-8-sig", "hispanic"
)
_CENSUS_SPANISH_FULL_INNER_CSV: Final[str] = "app_c.csv"
_PARANAMES: Final = _CorpusFormat("ParaNames file", "tsv", 1, min_fields=2, header_prefix="qid\t")
_POPNAMES: Final = _CorpusFormat(
    "PopNames file", "header", "name", ("country", "type", "name"), skip_comment_names=False
)
_POPNAMES_FULLFILE: Final = _CorpusFormat(
    "PopNames full file", "header", "Romanized Name", ("Romanized Name",), "utf-8-sig"
)

_PLURALITY_THRESHOLD: Final[float] = 40.0


def _parse_census_share(value: str | None) -> float:
    # Census publishes "(S)" in the real Surname File where a cell has been
    # suppressed to protect respondent privacy on tiny-count rows. Treat
    # suppressed cells as zero — if the row has no unsuppressed plurality it
    # falls to "unlabeled" naturally.
    v = (value or "").strip()
    if not v or v == "(S)":
        return 0.0
    return float(v)


def _census_plurality_bucket(path: Path) -> _Bucket:
    """The Census surname row's bucket: the race-share column with the highest value,
    or ``unlabeled`` when the top share is under the 40-percent plurality threshold."""

    def bucket(record: Mapping[str, str | None], lineno: int) -> str:
        try:
            shares = {
                "white": _parse_census_share(record["pctwhite"]),
                "black": _parse_census_share(record["pctblack"]),
                "asian": _parse_census_share(record["pctapi"]),
                "ai_an": _parse_census_share(record["pctaian"]),
                "hispanic": _parse_census_share(record["pcthispanic"]),
            }
        except ValueError as exc:
            raise PipelineError(f"{path}:{lineno}: non-numeric share: {exc}") from exc
        top_label = max(shares, key=lambda k: shares[k])
        # Rows where no bucket reaches the threshold are tagged unlabeled so we
        # don't overstate coverage for any one demographic.
        return top_label if shares[top_label] >= _PLURALITY_THRESHOLD else "unlabeled"

    return bucket


@contextmanager
def _open_zip_member(path: Path, member: str, encoding: str) -> Iterator[TextIO]:
    """The text of ``member`` inside the zip at ``path``, read without extracting to disk."""
    with zipfile.ZipFile(path) as zf:
        members = zf.namelist()
        if member not in members:
            raise PipelineError(
                f"{path}: expected inner member {member!r}; archive contains {members!r}"
            )
        with zf.open(member) as raw_fh:
            yield io.TextIOWrapper(raw_fh, encoding=encoding, newline="")


def _records(fh: TextIO, fmt: _CorpusFormat, path: Path) -> Iterator[tuple[int, _Record]]:
    """``(lineno, record)`` pairs of ``fh`` under ``fmt.shape``; an empty record is a blank line."""
    if fmt.shape == "header":
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [column for column in fmt.required if column not in header]
        if missing:
            raise PipelineError(f"{path}: missing columns {sorted(missing)}; header was {header!r}")
        yield from enumerate(reader, start=2)
        return
    if fmt.shape == "fields":
        yield from enumerate(csv.reader(fh), start=1)
        return
    for lineno, raw in enumerate(fh.read().splitlines(), start=1):
        line = raw.strip()
        if fmt.shape == "tsv":
            if fmt.header_prefix is not None and line.startswith(fmt.header_prefix):
                continue
            yield lineno, line.split("\t") if line else []
        else:
            yield lineno, [line] if line else []


def _parse_corpus(
    path: Path,
    source_id: str,
    fmt: _CorpusFormat,
    *,
    bucket: _Bucket | None = None,
    keep: Callable[[Mapping[str, str | None]], bool] | None = None,
    zip_member: str | None = None,
) -> list[IngestedRow]:
    """Read one record-per-name corpus into rows — the body behind the ``parse_*`` entries."""
    if not path.is_file():
        raise MissingSourceError(f"{fmt.label} not found at {path}")
    opened: AbstractContextManager[TextIO] = (
        _open_zip_member(path, zip_member, fmt.encoding)
        if zip_member is not None
        else path.open("r", encoding=fmt.encoding, newline="")
    )
    rows: list[IngestedRow] = []
    with opened as fh:
        for lineno, record in _records(fh, fmt, path):
            if not record:
                continue
            if isinstance(record, Mapping):
                if keep is not None and not keep(record):
                    continue
                raw_name = record.get(str(fmt.name))
            else:
                if fmt.skip_comment_names and record[0].startswith("#"):
                    continue
                if len(record) < fmt.min_fields:
                    raise PipelineError(
                        f"{path}:{lineno}: expected {fmt.min_fields}+ fields, got {record!r}"
                    )
                raw_name = record[int(fmt.name)]
            name = (raw_name or "").strip()
            if not name or (fmt.skip_comment_names and name.startswith("#")):
                continue
            demographic = fmt.demographic
            if bucket is not None and isinstance(record, Mapping):
                demographic = bucket(record, lineno)
            rows.append(IngestedRow(nfkc_lower(name), source_id, demographic))
    return rows


def _check_name_type(name_type: str) -> None:
    if name_type not in {"given", "surname"}:
        raise PipelineError(f"Unknown name_type {name_type!r}")


def parse_ssa_given_names(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse an SSA baby-names file (yob<year>.txt, ``Name,Sex,Count``; the bootstrap
    sample uses the same layout). SSA records carry no ethnicity: always ``unlabeled``."""
    return _parse_corpus(path, source_id, _SSA_GIVEN_NAMES)


def parse_census_surnames(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the Census 2010 Surname File: the pct columns are race shares in percent
    (0..100); the bucket is the column with the highest share, ``unlabeled`` under 40."""
    return _parse_corpus(path, source_id, _CENSUS_SURNAMES, bucket=_census_plurality_bucket(path))


def parse_census_spanish(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the Census Spanish Surname List (one surname per line)."""
    return _parse_corpus(path, source_id, _CENSUS_SPANISH)


def parse_census_spanish_full(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the full Census 2000 Spanish Surname List (File B, ~151,671 rows): a zip
    wrapper holding ``app_c.csv`` beside a companion ``app_c.xlsx``. ``.zip`` reads the
    inner CSV via ``zipfile``; ``.csv``/``.txt`` read directly; ``.xls``/``.xlsx`` raise
    (no XLSX reader is in ``pyproject.toml``, and unsupervised dependency additions
    are not allowed). Every row is ``hispanic`` per Census's labeling of File B."""
    if not path.is_file():
        raise MissingSourceError(f"{_CENSUS_SPANISH_FULL.label} not found at {path}")
    suffix = path.suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        raise PipelineError(
            f"{path}: .xls/.xlsx inputs require an XLSX reader that is not in "
            "pyproject.toml, and unsupervised dependency additions are not "
            "allowed; point the adapter at the companion app_c.csv or the "
            "zip wrapper that contains it."
        )
    if suffix == ".zip":
        return _parse_corpus(
            path, source_id, _CENSUS_SPANISH_FULL, zip_member=_CENSUS_SPANISH_FULL_INNER_CSV
        )
    if suffix in {".csv", ".txt"}:
        return _parse_corpus(path, source_id, _CENSUS_SPANISH_FULL)
    raise PipelineError(
        f"{path}: unsupported suffix {suffix!r} for Census Spanish full file; "
        "expected .zip, .csv, or .txt"
    )


def parse_paranames(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the ParaNames TSV dump (PER-filtered, ≥5 sitelinks): ``qid\\tlabel\\tsitelinks``."""
    return _parse_corpus(path, source_id, _PARANAMES)


def parse_popnames_by_type(path: Path, source_id: str, name_type: str) -> list[IngestedRow]:
    """Parse PopNames and keep only rows with the given ``name_type``."""
    _check_name_type(name_type)
    return _parse_corpus(
        path, source_id, _POPNAMES, keep=lambda record: (record["type"] or "").strip() == name_type
    )


def parse_popnames_fullfile(path: Path, source_id: str, name_type: str) -> list[IngestedRow]:
    """Parse a sigpwned popular-names upstream full-file CSV
    (github.com/sigpwned/popular-names-by-country-dataset): ``Localized Name`` and
    ``Romanized Name`` columns and no ``type`` (upstream partitions by filename, so the
    caller supplies ``name_type``). Only ``Romanized Name`` is ingested (Bloom keys are
    Latin-normalized); the surnames file is BOM-prefixed, hence ``utf-8-sig``."""
    _check_name_type(name_type)
    return _parse_corpus(path, source_id, _POPNAMES_FULLFILE)


def parse_paranames_full(
    path: Path,
    source_id: str,
    *,
    min_languages: int = 5,
    languages: frozenset[str] | None = None,
) -> Iterator[IngestedRow]:
    """Stream-parse the full ParaNames gzip dump.

    Expected columns: ``wikidata_id``, ``eng``, ``label``, ``language``,
    ``type``. The reader streams the gzip, restricts to ``type == "PER"``
    rows, groups consecutive PER rows by ``wikidata_id``, and retains
    entities whose distinct-language count is at least ``min_languages``
    (default 5, matching the ParaNames-as-sitelinks substitute per plan M5).

    If ``languages`` is set, rows are restricted to that locale set for both
    the language-count check and emission.

    Each retained entity yields one IngestedRow per unique normalized
    composite label (e.g. ``"albert einstein"``) under ``nfkc_lower``. The
    bare whitespace-split single tokens (``"albert"``, ``"einstein"``) are not
    emitted: contributing only composite labels is designed to lower the
    surname-membership false-positive rate, since high-frequency single tokens
    are the dominant collision source. Demographic is always ``"unlabeled"`` —
    ParaNames carries no ethnicity tag.

    The upstream file is sorted by ``(type, wikidata_id)``: rows partition
    into LOC, ORG, and PER blocks, and within each block ``wikidata_id``
    runs are contiguous (verified empirically over all 93.7M PER rows; the
    PER block carries zero non-contiguous ``wikidata_id`` reappearances).
    Filtering to PER **before** the wid-transition logic keeps the streaming
    group-by valid; the type filter has to lead so cross-type wid
    reappearances (e.g. an entity tagged both LOC and PER) are not mistaken
    for a sort-invariant violation. A genuine reappearance within the PER
    subset still raises ``PipelineError`` rather than silently mis-group,
    since upstream publishes no ordering contract for future regenerations.
    """
    if not path.is_file():
        raise MissingSourceError(f"ParaNames full file not found at {path}")

    required_columns = {"wikidata_id", "label", "language", "type"}
    finalized_ids: set[str] = set()
    current_id: str | None = None
    current_rows: list[tuple[str, str]] = []

    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        header = reader.fieldnames or []
        missing = required_columns - set(header)
        if missing:
            raise PipelineError(f"{path}: missing columns {sorted(missing)}; header was {header!r}")

        for raw in reader:
            row_type = (raw.get("type") or "").strip()
            if row_type != "PER":
                continue
            wid = (raw.get("wikidata_id") or "").strip()
            if not wid:
                continue
            label = (raw.get("label") or "").strip()
            if not label:
                continue
            language = (raw.get("language") or "").strip()
            if wid != current_id:
                if current_id is not None:
                    yield from _emit_paranames_group(
                        current_rows,
                        source_id,
                        min_languages=min_languages,
                        languages=languages,
                    )
                    finalized_ids.add(current_id)
                if wid in finalized_ids:
                    raise PipelineError(
                        f"{path}: wikidata_id {wid!r} reappears non-contiguously "
                        "within the PER block; upstream sort invariant violated"
                    )
                current_id = wid
                current_rows = []
            current_rows.append((label, language))

        if current_id is not None:
            yield from _emit_paranames_group(
                current_rows,
                source_id,
                min_languages=min_languages,
                languages=languages,
            )


def _emit_paranames_group(
    rows: list[tuple[str, str]],
    source_id: str,
    *,
    min_languages: int,
    languages: frozenset[str] | None,
) -> Iterator[IngestedRow]:
    if not rows:
        return
    if languages is not None:
        rows = [(label, lang) for label, lang in rows if lang in languages]
    distinct_languages = {lang for _, lang in rows if lang}
    if len(distinct_languages) < min_languages:
        return
    emitted: set[str] = set()
    normalized_labels = sorted({nfkc_lower(label) for label, _ in rows if label})
    for normalized in normalized_labels:
        if not normalized or normalized in emitted:
            continue
        emitted.add(normalized)
        # Composite labels only: the bare whitespace-split single tokens are
        # deliberately not emitted. Splitting a multi-word label into its
        # single tokens is the dominant surname-membership collision source,
        # so dropping that emission is designed to lower the membership FPR.
        yield IngestedRow(normalized, source_id, "unlabeled")


# ---- Merge step ------------------------------------------------------------


def _finalize(attributions: dict[str, SourcePair]) -> IngestResult:
    """Sort, count, and hash a key→attribution map into an IngestResult.

    This is the single counting/sort/hash implementation shared by ``merge``
    (row-stream path) and ``merge_aggregates`` (aggregated path): both reduce
    their input to the same first-occurrence attributions map, so routing both
    through one finalizer is what makes the two paths byte-equivalent by
    construction rather than by parallel maintenance.
    """
    sorted_keys = sorted(attributions)
    per_source: dict[str, int] = {}
    per_demo: dict[str, int] = dict.fromkeys(DEMOGRAPHIC_LABELS, 0)
    for key in sorted_keys:
        source_id, demographic = attributions[key]
        per_source[source_id] = per_source.get(source_id, 0) + 1
        # An unknown demographic label is a builder bug — fail loudly.
        if demographic not in per_demo:
            raise PipelineError(f"Unknown demographic {demographic!r} for {key!r}")
        per_demo[demographic] += 1

    joined = "\n".join(sorted_keys).encode("utf-8")
    source_hash = hashlib.sha256(joined).digest()

    return IngestResult(
        rows=tuple(sorted_keys),
        per_source_counts=per_source,
        per_demographic_counts=per_demo,
        source_hash=source_hash,
    )


def merge(rows: Iterable[IngestedRow]) -> IngestResult:
    """Merge rows from multiple sources, dedup, sort, and hash.

    For keys that appear in multiple sources, the first-encountered source is
    credited with the row. This keeps per-source counts stable and additive.
    """
    attributions: dict[str, SourcePair] = {}
    for row in rows:
        attributions.setdefault(row.normalized, (row.source_id, row.demographic))
    return _finalize(attributions)


@dataclass(frozen=True, slots=True)
class AggregatedRows:
    """Per-spec first-occurrence aggregate — the compact IPC form of a parse.

    Produced by :func:`aggregate_rows` inside ingest workers so that what
    crosses the process boundary (and what the parent holds live) is the
    deduplicated key set, not the raw row stream. Everything ``merge`` can
    observe about a spec's rows survives:

    - ``groups``: each unique normalized key appears exactly once, under the
      ``(source_id, demographic)`` pair of its first occurrence within the
      spec. Groups are sorted by pair and keys are sorted within each group
      (explicit sorts; no insertion-ordered structure crosses
      the boundary). Group order carries no semantics — keys are disjoint
      across groups — so the canonical sort is safe.
    - ``seen_source_ids``: sorted distinct ``source_id`` over ALL rows,
      including rows shadowed by dedup. ``cli._ingest_with_cache`` derives the
      manifest ``sources`` list from raw rows, so a source whose every row
      duplicated an earlier key must still be reported.

    What it discards — row multiplicity, the attribution pairs of non-first
    duplicates, and encounter order — is exactly what ``merge`` cannot
    observe: counts are derived post-dedup, ``setdefault`` never reads a
    later duplicate, and every output is explicitly sorted. The equivalence
    is pinned by tests/test_bloom_ingest_aggregation.py.
    """

    groups: tuple[AggregatedGroup, ...]
    seen_source_ids: tuple[str, ...]


def aggregate_rows(rows: Iterable[IngestedRow]) -> AggregatedRows:
    """Reduce a row stream to its first-occurrence aggregate, streaming.

    Consumes generators without materializing them — for a paranames_full
    shard this is the difference between ~0.84 GB (full row list, measured)
    and ~0.35 GB (this dict, measured) of worker RSS. Attribution
    pairs are interned so the millions of dict values are references to a
    handful of shared tuples.
    """
    firsts: dict[str, SourcePair] = {}
    pair_cache: dict[SourcePair, SourcePair] = {}
    seen_sources: set[str] = set()
    for row in rows:
        seen_sources.add(row.source_id)
        pair = (row.source_id, row.demographic)
        pair = pair_cache.setdefault(pair, pair)
        firsts.setdefault(row.normalized, pair)

    grouped: dict[SourcePair, list[str]] = {}
    for key, pair in firsts.items():
        grouped.setdefault(pair, []).append(key)
    return AggregatedRows(
        groups=tuple((pair, tuple(sorted(keys))) for pair, keys in sorted(grouped.items())),
        seen_source_ids=tuple(sorted(seen_sources)),
    )


def merge_aggregates(
    aggregates: Iterable[AggregatedRows],
) -> tuple[IngestResult, tuple[str, ...]]:
    """Fold per-spec aggregates, in spec submission order, into one result.

    Byte-equivalent to ``merge(itertools.chain(*rows_per_spec))``: folding
    spec-by-spec preserves first-wins attribution because within one
    aggregate every key appears exactly once (already carrying its
    first-occurrence pair), and across aggregates ``setdefault`` resolves
    duplicates to the earliest spec — the same key the chained row stream
    would have attributed. The iterable is consumed lazily, one aggregate at
    a time, so a parent folding pool results never holds them all.

    Returns:
        ``(result, seen_source_ids)`` where ``seen_source_ids`` is the sorted
        union over all rows of all specs — the aggregate-path equivalent of
        ``{row.source_id for row in rows}`` on the raw stream.
    """
    attributions: dict[str, SourcePair] = {}
    seen_sources: set[str] = set()
    for aggregate in aggregates:
        seen_sources.update(aggregate.seen_source_ids)
        for pair, keys in aggregate.groups:
            for key in keys:
                attributions.setdefault(key, pair)
    return _finalize(attributions), tuple(sorted(seen_sources))


# ---- Parallel dispatch ----------------------------------------------------


# A spec is a zero-arg callable (typically ``functools.partial(parser, path,
# source_id, ...)``) that returns a list of IngestedRow, or an iterator that
# ``_run_parse_spec`` will materialize. Must be picklable for
# ProcessPoolExecutor; using module-level parsers + ``functools.partial``
# satisfies that.
ParseSpec = Callable[[], Iterable[IngestedRow]]


def _run_parse_spec(spec: ParseSpec) -> list[IngestedRow]:
    """Worker entry point. Materializes generator results to lists for IPC."""
    # First action: register parent-death signal so this worker self-
    # terminates if the builder parent dies (Ctrl+C, OOM-kill of just the
    # parent, hook timeout). Without this, pool workers get reparented to
    # systemd --user and hold their full RSS — the OOM-freeze mechanism that
    # PR_SET_PDEATHSIG guards against.
    request_parent_death_signal()
    result = spec()
    if isinstance(result, list):
        return result
    return list(result)


# ---- Ingest cache (shared by build_bloom and build_demographics) ----------


def compute_sources_fingerprint(inputs: Iterable[tuple[Path, str]]) -> str:
    """Content-addressed fingerprint for the ``_ingest_cache`` key.

    Hashes ``(path, size, sha256(content), source_id)`` for each spec input,
    sorted by path then source_id. Any change to a source file's bytes or to
    the set of wired-in ``source_id``s flips the fingerprint and forces
    re-parse; mtime churn alone (checkout, clone, ``touch``, rebase) does
    not — that churn is what used to throw away the ~817 MB warm cache and
    re-pay the ~40-minute ParaNames parse on trees whose bytes never changed.

    ``st_mtime_ns`` is deliberately NOT in the key, and dropping it is sound
    only because the content digest replaced it: a key of path+size+source_id
    alone would collide on same-size edits and serve a stale cache. Size
    stays in the payload as a cheap discriminator alongside the
    digest. Cost: one streaming sha256 pass over each source file per
    fingerprint computation (~0.4 s for the 1 GB ParaNames file on this
    hardware, measured) — noise next to the parse it guards.

    The cache file this keys is never shipped (lives under ``build/…/_ingest_cache/``)
    so it is not subject to determinism verification; it exists purely to
    short-circuit recomputation across ``make build`` invocations.
    """
    h = hashlib.sha256()
    for path, source_id in sorted(inputs, key=lambda t: (str(t[0]), t[1])):
        payload = f"{path}\x00{path.stat().st_size}\x00{sha256_file(path)}\x00{source_id}\x00"
        h.update(payload.encode("utf-8"))
    return h.hexdigest()


def ingest_result_to_canonical_dict(result: IngestResult) -> dict[str, Any]:
    """Serialize an :class:`IngestResult` for the cache JSON file.

    JSON-safe: the ``source_hash`` bytes are hex-encoded. Round-trips via
    :func:`ingest_result_from_canonical_dict`.
    """
    return {
        "rows": list(result.rows),
        "per_source_counts": dict(result.per_source_counts),
        "per_demographic_counts": dict(result.per_demographic_counts),
        "source_hash": result.source_hash.hex(),
    }


def ingest_result_from_canonical_dict(data: dict[str, Any]) -> IngestResult:
    """Inverse of :func:`ingest_result_to_canonical_dict`.

    Raises :class:`PipelineError` on malformed payloads; the cache file is
    trusted only after its fingerprint matches, so a malformed payload
    indicates a build-system bug rather than a corrupt input.
    """
    try:
        rows = tuple(data["rows"])
        per_source = dict(data["per_source_counts"])
        per_demo = dict(data["per_demographic_counts"])
        source_hash = bytes.fromhex(data["source_hash"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError(f"Malformed ingest cache payload: {exc}") from exc
    return IngestResult(
        rows=rows,
        per_source_counts=per_source,
        per_demographic_counts=per_demo,
        source_hash=source_hash,
    )


def _pool_size_for(specs: Sequence[ParseSpec], workers: int | None) -> int:
    """Resolve the process-pool size for a spec batch; 1 means run serial.

    Single resolver shared by :func:`parse_sources_parallel` and
    :func:`ingest_sources_parallel` so the two paths can never drift on caps
    or guards:

    - ``workers=None``: per-host override for memory-constrained /
      back-to-back-build scenarios (e.g. determinism-check rebuild on a host
      where swap is already saturated by the first build). The
      ``effective_workers`` helper composes the ``RESECTA_BUILD_WORKERS`` env
      override with the per-site hard cap; lifting the env-handling into
      ``common.process`` is what makes one knob control all three pools.
    - Under pytest-xdist, each outer worker may invoke a builder
      concurrently. Forcing serial prevents N xdist workers * M inner workers
      from stacking parse buffers and OOM'ing. Tests that want to exercise
      the parallel path pass ``workers=`` explicitly; serial-vs-parallel
      byte-equality is gated by test_parse_sources_parallel_byte_identical_*
      and tests/test_bloom_ingest_aggregation.py.
    """
    if workers is None:
        effective = effective_workers(default=os.cpu_count() or 1, hard_cap=_MAX_BUILD_WORKERS)
        if os.environ.get("PYTEST_XDIST_WORKER"):
            effective = 1
    else:
        effective = workers
    if effective <= 1 or len(specs) <= 1:
        return 1
    return max(1, min(effective, len(specs), _MAX_BUILD_WORKERS))


def parse_sources_parallel(
    specs: Sequence[ParseSpec],
    *,
    workers: int | None = None,
) -> list[IngestedRow]:
    """Run parse specs across a process pool; concatenate results in input order.

    The ``merge`` step is insertion-order sensitive for source attribution
    (``setdefault`` on first-seen), so concatenating in the submission order
    is the invariant that preserves byte-identical output regardless of worker
    count or completion order. ``ProcessPoolExecutor.map`` yields results in
    input order, so we iterate its generator and chain.

    Production ingest goes through :func:`ingest_sources_parallel`;
    this materializing variant remains for callers that need the raw rows.

    Args:
        specs: Per-source parse callables in canonical order.
        workers: Pool size. ``None`` (default) uses ``os.cpu_count()``.
            ``1`` or a single-spec list forces serial execution (no pool
            overhead).
    """
    pool_size = _pool_size_for(specs, workers)
    if pool_size <= 1:
        return list(itertools.chain.from_iterable(_run_parse_spec(s) for s in specs))
    # Explicit shutdown(cancel_futures=True) on every exit path — including
    # exception unwind. Bare ``with pool:`` would still call shutdown(wait=True),
    # but cancel_futures (Python 3.9+) is what releases queued-but-not-started
    # work on a KeyboardInterrupt instead of running every spec to completion.
    pool = ProcessPoolExecutor(max_workers=pool_size)
    try:
        # Eliminate the intermediate ``results = list(pool.map(...))`` step
        # so the parent does not hold a list-of-lists AND a flattened copy
        # at once. Combined with the cap above, this keeps parent-side RSS
        # bounded by roughly one full output instead of two.
        return list(itertools.chain.from_iterable(pool.map(_run_parse_spec, specs, chunksize=1)))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _run_parse_spec_aggregated(spec: ParseSpec) -> AggregatedRows:
    """Worker entry point for the aggregated ingest path.

    Streams the parser's output directly into :func:`aggregate_rows` — the
    full row list is never materialized in the worker, and only the compact
    aggregate is pickled back to the parent.
    """
    # Same rationale as _run_parse_spec: register parent-death first so an
    # orphaned worker cannot outlive the builder holding shard-sized RSS.
    request_parent_death_signal()
    return aggregate_rows(spec())


def ingest_sources_parallel(
    specs: Sequence[ParseSpec],
    *,
    workers: int | None = None,
) -> tuple[IngestResult, tuple[str, ...]]:
    """Parse specs across a process pool, aggregating rows inside each worker.

    Byte-equivalent to ``merge(parse_sources_parallel(specs))`` plus the raw
    stream's distinct source ids (pinned by
    tests/test_bloom_ingest_aggregation.py), while collapsing the live set —
    the fix for the memory-pressure-bound cold gate. Measured on a real
    paranames_full shard: 4.51M rows
    collapse to 2.37M unique keys before crossing the process boundary;
    worker RSS 0.84 → 0.35 GB; IPC payload 134 → 48 MB. The parent folds
    aggregates one at a time instead of concatenating ~36M row objects.

    Worker selection (caps, xdist guard, serial fallback) is shared with
    :func:`parse_sources_parallel` via ``_pool_size_for``.

    Returns:
        ``(result, seen_source_ids)`` — see :func:`merge_aggregates`.
    """
    pool_size = _pool_size_for(specs, workers)
    if pool_size <= 1:
        return merge_aggregates(_run_parse_spec_aggregated(s) for s in specs)
    pool = ProcessPoolExecutor(max_workers=pool_size)
    try:
        # pool.map yields in submission order; merge_aggregates consumes the
        # generator lazily, so parent-side RSS is the fold dict plus the few
        # completed-but-not-yet-yielded aggregates the pool buffers.
        return merge_aggregates(pool.map(_run_parse_spec_aggregated, specs, chunksize=1))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
