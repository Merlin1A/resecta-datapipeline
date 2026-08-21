"""Read multi-source name corpora and emit sorted, normalized insertion lists.

The ingest step is where source formats meet the filter. Each corpus has a
small parser (row count, column index, optional demographic tags); the merge
step deduplicates across sources, normalizes via ``nfkc_lower``, sorts, and
emits a list suitable for both insertion into the Bloom filter and hashing for
the RSBF header.

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
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
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
# hosts. Since S04 the aggregated path returns per-spec first-occurrence
# aggregates (~50 MB pickled for a paranames_full shard, measured) instead
# of multi-GB row lists, but the cap is deliberately retained: four
# concurrent gzip+normalize passes are the RAM/CPU budget the
# oom-freeze-fix plan sized, and the legacy list-returning path
# (parse_sources_parallel) still exists. Deliberately lower than
# bloom/filter.py's cap (8) and corpus/generate.py's cap (16).
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


def parse_ssa_given_names(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse an SSA baby-names file.

    The SSA ships yob<year>.txt in the format ``Name,Sex,Count``; the bootstrap
    sample uses the same layout. Demographic is always ``unlabeled`` — SSA
    records do not carry ethnicity.
    """
    if not path.is_file():
        raise MissingSourceError(f"SSA given-names file not found at {path}")
    rows: list[IngestedRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        for lineno, raw in enumerate(reader, start=1):
            if not raw or raw[0].startswith("#"):
                continue
            if len(raw) < 1:
                raise PipelineError(f"{path}:{lineno}: empty row")
            name = raw[0].strip()
            if not name:
                continue
            rows.append(IngestedRow(nfkc_lower(name), source_id, "unlabeled"))
    return rows


_PLURALITY_THRESHOLD: Final[float] = 40.0


def _parse_census_share(value: str) -> float:
    # Census publishes "(S)" in the real Surname File where a cell has been
    # suppressed to protect respondent privacy on tiny-count rows. Treat
    # suppressed cells as zero — if the row has no unsuppressed plurality it
    # falls to "unlabeled" naturally.
    v = (value or "").strip()
    if not v or v == "(S)":
        return 0.0
    return float(v)


def parse_census_surnames(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the Census 2010 Surname File.

    Expected columns: ``name,pctwhite,pctblack,pctapi,pctaian,pcthispanic``
    where the pct columns are race shares in percent (0..100). The row's
    demographic bucket is the column with the highest share; if the top share
    is below 40 (no plurality), label as ``unlabeled``.
    """
    if not path.is_file():
        raise MissingSourceError(f"Census surname file not found at {path}")
    rows: list[IngestedRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        required = {"name", "pctwhite", "pctblack", "pctapi", "pctaian", "pcthispanic"}
        missing = required - set(header)
        if missing:
            raise PipelineError(f"{path}: missing columns {sorted(missing)}; header was {header!r}")
        for lineno, raw in enumerate(reader, start=2):
            name = (raw["name"] or "").strip()
            if not name or name.startswith("#"):
                continue
            try:
                shares = {
                    "white": _parse_census_share(raw["pctwhite"]),
                    "black": _parse_census_share(raw["pctblack"]),
                    "asian": _parse_census_share(raw["pctapi"]),
                    "ai_an": _parse_census_share(raw["pctaian"]),
                    "hispanic": _parse_census_share(raw["pcthispanic"]),
                }
            except ValueError as exc:
                raise PipelineError(f"{path}:{lineno}: non-numeric share: {exc}") from exc
            top_label = max(shares, key=lambda k: shares[k])
            # Census releases rows where no bucket reaches the plurality
            # threshold. Those are tagged unlabeled so we don't overstate
            # coverage for any one demographic.
            demographic = top_label if shares[top_label] >= _PLURALITY_THRESHOLD else "unlabeled"
            rows.append(IngestedRow(nfkc_lower(name), source_id, demographic))
    return rows


def parse_census_spanish(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the Census Spanish Surname List (one surname per line)."""
    if not path.is_file():
        raise MissingSourceError(f"Census Spanish file not found at {path}")
    rows: list[IngestedRow] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        rows.append(IngestedRow(nfkc_lower(name), source_id, "hispanic"))
    return rows


_CENSUS_SPANISH_FULL_INNER_CSV: Final[str] = "app_c.csv"


def parse_census_spanish_full(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the full Census 2000 Spanish Surname List (File B, ~151,671 rows).

    The upstream Census publication ships as a zip wrapper containing
    ``app_c.csv`` (comma-delimited, header ``name,rank,count,prop100k,
    cum_prop100k,pctwhite,pctblack,pctapi,pctaian,pct2prace,pcthispanic``) and
    a companion ``app_c.xlsx``. This adapter dispatches on ``path.suffix``:

    - ``.zip``: open the inner ``app_c.csv`` via ``zipfile`` (stdlib) and
      delegate to the CSV branch.
    - ``.csv`` / ``.txt``: read directly with ``csv.DictReader``.
    - ``.xls`` / ``.xlsx``: raise ``PipelineError``. No XLSX reader is in
      ``pyproject.toml``, and unsupervised dependency additions are not
      allowed; point the adapter at the companion CSV (or the zip wrapper
      that ships with it) instead.

    Every emitted row carries ``demographic="hispanic"`` per Census's own
    labeling of the File B publication.
    """
    if not path.is_file():
        raise MissingSourceError(f"Census Spanish full file not found at {path}")

    suffix = path.suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        raise PipelineError(
            f"{path}: .xls/.xlsx inputs require an XLSX reader that is not in "
            "pyproject.toml (CLAUDE.md §2.5); point the adapter at the "
            "companion app_c.csv or the zip wrapper that contains it."
        )
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            members = zf.namelist()
            if _CENSUS_SPANISH_FULL_INNER_CSV not in members:
                raise PipelineError(
                    f"{path}: expected inner member "
                    f"{_CENSUS_SPANISH_FULL_INNER_CSV!r}; archive contains {members!r}"
                )
            with zf.open(_CENSUS_SPANISH_FULL_INNER_CSV) as raw_fh:
                text_fh = io.TextIOWrapper(raw_fh, encoding="utf-8-sig", newline="")
                return _read_census_spanish_full_rows(text_fh, path, source_id)
    if suffix in {".csv", ".txt"}:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return _read_census_spanish_full_rows(fh, path, source_id)
    raise PipelineError(
        f"{path}: unsupported suffix {suffix!r} for Census Spanish full file; "
        "expected .zip, .csv, or .txt"
    )


def _read_census_spanish_full_rows(fh: TextIO, path: Path, source_id: str) -> list[IngestedRow]:
    reader = csv.DictReader(fh)
    header = reader.fieldnames or []
    if "name" not in header:
        raise PipelineError(
            f"{path}: expected column 'name' in Census Spanish full file; header was {header!r}"
        )
    rows: list[IngestedRow] = []
    for raw in reader:
        name = (raw.get("name") or "").strip()
        if not name or name.startswith("#"):
            continue
        rows.append(IngestedRow(nfkc_lower(name), source_id, "hispanic"))
    return rows


def parse_paranames(path: Path, source_id: str) -> list[IngestedRow]:
    """Parse the ParaNames TSV dump (PER-filtered, ≥5 sitelinks).

    Expected columns: ``qid\\tlabel\\tsitelinks``; ``label`` is the name.
    """
    if not path.is_file():
        raise MissingSourceError(f"ParaNames file not found at {path}")
    rows: list[IngestedRow] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("qid\t"):
            continue
        parts = line.split("\t")
        min_paranames_cols = 2
        if len(parts) < min_paranames_cols:
            raise PipelineError(f"{path}:{lineno}: expected ≥2 TSV columns, got {line!r}")
        name = parts[1].strip()
        if not name:
            continue
        rows.append(IngestedRow(nfkc_lower(name), source_id, "unlabeled"))
    return rows


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


def parse_popnames_by_type(path: Path, source_id: str, name_type: str) -> list[IngestedRow]:
    """Parse PopNames and keep only rows with the given ``name_type``."""
    if name_type not in {"given", "surname"}:
        raise PipelineError(f"Unknown name_type {name_type!r}")
    if not path.is_file():
        raise MissingSourceError(f"PopNames file not found at {path}")
    rows: list[IngestedRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        required = {"country", "type", "name"}
        missing = required - set(header)
        if missing:
            raise PipelineError(f"{path}: missing columns {sorted(missing)}; header was {header!r}")
        for raw in reader:
            rtype = (raw["type"] or "").strip()
            if rtype != name_type:
                continue
            name = (raw["name"] or "").strip()
            if not name:
                continue
            rows.append(IngestedRow(nfkc_lower(name), source_id, "unlabeled"))
    return rows


def parse_popnames_fullfile(path: Path, source_id: str, name_type: str) -> list[IngestedRow]:
    """Parse a sigpwned popular-names upstream full-file CSV.

    Matches the schema shipped at
    github.com/sigpwned/popular-names-by-country-dataset
    (``common-forenames-by-country.csv`` / ``common-surnames-by-country.csv``),
    which differs from the bootstrap layout (``country,type,name``) by carrying
    multiple name columns (``Localized Name``, ``Romanized Name``) and no
    ``type`` column — upstream partitions by filename instead of by row, so the
    caller supplies ``name_type``.

    Only ``Romanized Name`` is ingested (Bloom keys are Latin-normalized via
    ``nfkc_lower``; mixing scripts would add little discriminative value). All
    rows are tagged ``demographic='unlabeled'``. The surnames file is
    BOM-prefixed so the reader uses ``utf-8-sig``.
    """
    if name_type not in {"given", "surname"}:
        raise PipelineError(f"Unknown name_type {name_type!r}")
    if not path.is_file():
        raise MissingSourceError(f"PopNames full file not found at {path}")
    rows: list[IngestedRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        if "Romanized Name" not in header:
            raise PipelineError(f"{path}: expected column 'Romanized Name'; header was {header!r}")
        for raw in reader:
            name = (raw.get("Romanized Name") or "").strip()
            if not name or name.startswith("#"):
                continue
            rows.append(IngestedRow(nfkc_lower(name), source_id, "unlabeled"))
    return rows


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
    shard this is the difference between ~0.84 GB (full row list, measured
    S03) and ~0.35 GB (this dict, measured S04 T1) of worker RSS. Attribution
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
    alone would collide on same-size edits and serve a stale cache (reviewer
    F6). Size stays in the payload as a cheap discriminator alongside the
    digest. Cost: one streaming sha256 pass over each source file per
    fingerprint computation (~0.4 s for the 1 GB ParaNames file on this
    hardware, measured) — noise next to the parse it guards.

    The cache file this keys is never shipped (lives under ``build/…/_ingest_cache/``)
    so it is not subject to §1.3 determinism; it exists purely to short-circuit
    recomputation across ``make build`` invocations.
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

    Production ingest goes through :func:`ingest_sources_parallel` since S04;
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
    the S04 fix for the memory-pressure-bound cold gate (speed plan
    #14-redirected). Measured on a real paranames_full shard: 4.51M rows
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
