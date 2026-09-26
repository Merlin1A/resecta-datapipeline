"""Build the G8 synthetic corpus.

Generates a stratified corpus of synthetic documents (default 1000) with
per-doctype counts and per-demographic-bucket counts that match the
plan's 20%-per-group stratification. Every document carries ground-truth
PII span annotations.

Pure-Faker fallback: this module does not depend on
Synthea or any external JVM tool. Synthetic text comes from the
deterministic templates under :mod:`corpus.templates`.

The builder is deterministic: the same seed yields the same corpus.
Per-document sub-RNGs are derived from the seed, doctype, and index,
so adding a new doctype does not shift existing document content.

Generator PROFILES (Spec-A/C/D/G/H, :mod:`corpus._profiles`):
``build(seed, profile=)`` keeps every document's base stream byte-identical
to the ``g8`` corpus and hands the emitter a second stream seeded on
``(seed, doctype, index, profile)`` for the profile's own choices (name-slot
contexts under Spec-C, planted furniture under Spec-D, name forms under
Spec-A, the document locale and a re-drawn address under Spec-G, the sparse
placeholder under Spec-H). The ``g8`` profile never consults that stream, so
``build(seed)`` and ``build(seed, profile="g8")`` are the same bytes.
"""

from __future__ import annotations

import hashlib
import os
import random
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.process import effective_workers, request_parent_death_signal

from ._names import BUCKETS, NameSampler
from ._profiles import PROFILE_G8, PROFILES, Profile, draw_locale, is_spec_g
from .templates import EMITTERS, SUB_TEMPLATE_EMITTERS

_MODULE_NAME: Final[str] = "resecta_data.corpus.generate"
# Wire-format version of the corpus payload. 2 since the context annotation:
# every span carries ``context_class`` and every document a (for now empty)
# ``furniture`` array. Text, offsets, families and tiers are those of
# version 1 byte for byte -- the annotation adds keys only.
_SCHEMA_VERSION: Final[int] = 2

# Canonical per-doctype counts. The financial count was raised from 200 to
# 300: the last third of the financial index range emits the W-2 tax
# sub-template, keeping the 200 invoice docs and adding 100 tax-form docs.
_DEFAULT_COUNTS: Final[dict[str, int]] = {
    "court": 300,
    "medical": 250,
    "financial": 300,
    "foia": 150,
    "generic": 100,
}

# Fraction of docs emitted with zero name spans so
# the (doctype, name) train-prior is not pinned at ≈1.0 by template
# construction. Drawn per-document from the per-doc rng.
_NAME_SPARSE_FRACTION: Final[float] = 0.30

_FINANCIAL_DOCTYPE: Final[str] = "financial"
_FINANCIAL_TAX_SUB_TEMPLATE: Final[str] = "financial_tax"

_DOCTYPE_ORDER: Final[tuple[str, ...]] = (
    "court",
    "medical",
    "financial",
    "foia",
    "generic",
)

# Ground-truth limits on PII spans per document. The ceiling rose 15 -> 18
# with the 17/17 category extension (1.2 T1.1): an adversarial court document
# carries 13 pre-existing spans plus a driver's license, a plate and the
# plate-label decoy.
_MIN_SPANS_PER_DOC: Final[int] = 5
_MAX_SPANS_PER_DOC: Final[int] = 18

_ADVERSARIAL_TAG_UNIVERSE: Final[frozenset[str]] = frozenset(
    {
        "ssn_shaped_case_number",
        "dob_shaped_filing_date",
        "npi_shaped_phone_number",
        "all_caps_header_name",
        "keyword_stuffing",
        "invisible_text",
        "column_header_label",
        "none",
        # 1.2 T1.1 negative twins of the five added categories.
        "luhn_failed_card_number",
        "itin_yy_out_of_range",
        "dl_shape_no_jurisdiction",
        "passport_shape_no_issuer",
        "business_registration_plate_label",
    }
)


_SUB_SEED_MODULUS: Final[int] = 2**63

_MAX_BUILD_WORKERS: Final[int] = 16

_DEFAULT_LOCALE: Final[str] = "en_US"
_HISPANIC_BUCKET_LABEL: Final[str] = "hispanic"

# Deterministic locale partition for Hispanic-labelled docs:
# bucket = seed % 10 → es_MX for 0-2, es_ES for 3-5, en_US for 6-9
# (30% / 30% / 40%).
_LOCALE_BUCKET_MODULUS: Final[int] = 10
_LOCALE_BUCKET_ES_MX_MAX: Final[int] = 3
_LOCALE_BUCKET_ES_ES_MAX: Final[int] = 6


def _sub_seed(master: int, doctype: str, index: int) -> int:
    """Deterministic per-doc seed.

    SHA-256 hashes ``(master, doctype, index)`` and reduces to a 63-bit int.
    Hash-based derivation means changing one doctype's count does not shift
    the bit-stream of a later doctype's documents.
    """
    key = f"{master}:{doctype}:{index}".encode()
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big") % _SUB_SEED_MODULUS


def _profile_sub_seed(master: int, doctype: str, index: int, profile: str) -> int:
    """Deterministic per-doc seed of a profile's OWN stream.

    Keyed on ``(master, doctype, index, profile)``: two profiles draw different
    contexts and furniture for the same document, and the same profile draws
    the same ones on every rebuild. The base stream (:func:`_sub_seed`) is
    untouched, which is what keeps every ground-truth value of a profile
    document identical to its ``g8`` twin.
    """
    key = f"{master}:{doctype}:{index}:{profile}".encode()
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big") % _SUB_SEED_MODULUS


def _sub_seed_and_locale(
    master: int, doctype: str, index: int, demographic: str
) -> tuple[int, str]:
    """Return per-document seed and Faker locale hint.

    Derives the seed via :func:`_sub_seed`, then picks a locale based on
    the demographic bucket. Hispanic-labelled docs are partitioned by
    ``seed % 10`` into 30% ``es_MX`` / 30% ``es_ES`` / 40% ``en_US``;
    every other bucket always returns ``en_US``. Locale selection is a
    deterministic function of the seed, so rebuilds produce identical
    ``(seed, locale)`` pairs.
    """
    seed = _sub_seed(master, doctype, index)
    if demographic == _HISPANIC_BUCKET_LABEL:
        bucket = seed % _LOCALE_BUCKET_MODULUS
        if bucket < _LOCALE_BUCKET_ES_MX_MAX:
            return seed, "es_MX"
        if bucket < _LOCALE_BUCKET_ES_ES_MAX:
            return seed, "es_ES"
    return seed, _DEFAULT_LOCALE


def _bucket_for_index(index: int) -> str:
    """Round-robin bucket assignment for 20%-per-group stratification."""
    return BUCKETS[index % len(BUCKETS)]


def _sub_template_for(doctype: str, index: int, count: int) -> str | None:
    """Return the sub-template key for ``(doctype, index)``, if any.

    The financial doctype splits its index range: the first two thirds
    emit the invoice shape, the last third the W-2 tax shape (both
    labeled ``doctype="financial"``). The split is a pure
    function of the per-doctype count, so invoice docs keep the indexes
    (and therefore sub-seeds) they had before the split existed.
    """
    if doctype != _FINANCIAL_DOCTYPE:
        return None
    tax_count = count // 3
    if index >= count - tax_count:
        return _FINANCIAL_TAX_SUB_TEMPLATE
    return None


def _generate_one_document(
    master_seed: int,
    doctype: str,
    index: int,
    sub_template: str | None = None,
    profile: str = PROFILE_G8,
) -> tuple[str, list[dict[str, Any]], list[str], str, list[dict[str, Any]]]:
    """Render a single G8 document from ``(master_seed, doctype, index)``.

    Module-level so it is picklable for ``ProcessPoolExecutor``. Pure
    function of its arguments: derives the per-doc seed, bucket, locale,
    and RNG entirely inside the worker, so workers share no state and can
    execute in any order without affecting the byte-identical output of
    the caller assembling results in canonical order.

    ``sub_template`` selects an alternate emitter shape that shares the
    doctype label (currently only ``"financial_tax"``); the sub-seed is
    still keyed on ``(master_seed, doctype, index)`` alone. ``profile`` is
    the generator profile: ``g8`` hands the emitter no profile stream; any
    other profile hands it a :class:`Profile` whose stream is keyed on
    ``(master_seed, doctype, index, profile)``.

    Returns:
        ``(text, pii_spans, adversarial_tags, demographic_bucket, furniture)``.
        The caller turns this into the final document dict.
    """
    # First action: register parent-death signal so this worker self-
    # terminates if the builder parent dies (see common/process.py).
    request_parent_death_signal()
    bucket = _bucket_for_index(index)
    sub_seed, locale = _sub_seed_and_locale(master_seed, doctype, index, bucket)
    rng = random.Random(sub_seed)  # noqa: S311
    sampler = NameSampler(rng)
    # Drawn before the emitter so every template sees the same stream
    # position regardless of the sparse outcome.
    name_sparse = rng.random() < _NAME_SPARSE_FRACTION
    active_profile: Profile | None = None
    if profile != PROFILE_G8:
        profile_rng = random.Random(  # noqa: S311
            _profile_sub_seed(master_seed, doctype, index, profile)
        )
        # Spec-G: the document's locale is the profile stream's FIRST draw,
        # crossed with the bucket at g8's corpus-wide marginal; the emitter
        # still receives the BASE locale so the base stream's address draw
        # is the same as g8's (see _profiles.render_address).
        drawn_locale = draw_locale(profile_rng) if is_spec_g(profile) else None
        active_profile = Profile(profile, profile_rng, locale=drawn_locale)
    emitter = SUB_TEMPLATE_EMITTERS[sub_template] if sub_template is not None else EMITTERS[doctype]
    text, spans, tags, furniture = emitter(
        rng, sampler, bucket, locale=locale, name_sparse=name_sparse, profile=active_profile
    )
    if active_profile is not None and active_profile.locale is not None:
        # Every span of a Spec-G document records the drawn locale (the key
        # exists only on the locale-axis profiles, so no other corpus moves).
        for span in spans:
            span["locale"] = active_profile.locale
    return text, spans, tags, bucket, furniture


def _assemble_document(
    doctype: str,
    index: int,
    text: str,
    spans: list[dict[str, Any]],
    tags: list[str],
    bucket: str,
    sub_template: str | None = None,
    furniture: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate worker output and build the final document dict.

    Validation lives in the parent so PipelineError carries the ordinary
    stack trace (not a pickled RemoteTraceback) and so workers stay
    minimal. The checks are O(#spans + #furniture) per doc and cheap.
    """
    if not (_MIN_SPANS_PER_DOC <= len(spans) <= _MAX_SPANS_PER_DOC):
        raise PipelineError(
            f"{doctype} #{index}: emitter produced {len(spans)} spans, "
            f"expected [{_MIN_SPANS_PER_DOC}, {_MAX_SPANS_PER_DOC}]."
        )

    unknown_tags = set(tags) - _ADVERSARIAL_TAG_UNIVERSE
    if unknown_tags:
        raise PipelineError(f"{doctype} #{index}: unknown adversarial tags {sorted(unknown_tags)}.")

    planted = list(furniture or [])
    for region in planted:
        if not (0 <= region["start"] < region["end"] <= len(text)) or not region["kind"]:
            raise PipelineError(f"{doctype} #{index}: malformed furniture region {region!r}.")
        if any(region["start"] < s["end"] and s["start"] < region["end"] for s in spans):
            raise PipelineError(
                f"{doctype} #{index}: furniture region {region!r} overlaps a ground-truth span."
            )

    doc: dict[str, Any] = {
        "id": f"{doctype}_{index:06d}",
        "doctype": doctype,
        "demographic_bucket": bucket,
        "text": text,
        "pii_spans": spans,
        # Non-PII page furniture (role nouns, labels, salutations, closings)
        # as [start, end) regions with a kind. Empty under the ``g8`` profile;
        # the Spec-D profiles plant it. The key is present on every document
        # so a reader never has to special-case its absence.
        "furniture": planted,
    }
    if sub_template is not None:
        doc["sub_template"] = sub_template
    if tags:
        doc["adversarial_tags"] = sorted(set(tags))
    return doc


def build(
    seed: int,
    *,
    counts: dict[str, int] | None = None,
    parallel: bool = True,
    workers: int | None = None,
    profile: str = PROFILE_G8,
) -> dict[str, Any]:
    """Return the G8 corpus payload.

    Args:
        seed: Master PRNG seed. Per-document sub-seeds are derived from it.
        counts: Optional override for per-doctype document counts. Defaults
            to the canonical 300/250/300/150/100.
        profile: The generator profile (:data:`corpus._profiles.PROFILES`).
            ``g8`` (default) is the corpus as furnished; ``g8-specC`` /
            ``g8-specD`` / ``g8-specCD`` re-render the name slots and / or
            plant furniture on the SAME documents (every value identical);
            ``g8-specA`` / ``g8-specG`` / ``g8-specH`` / ``g8-specAGH`` draw
            name forms, the locale axis and the sparse placeholder on them
            (Spec-A moves the name value, Spec-G the address value of a
            document whose locale moved; every other value identical).
            A non-default profile is named in the payload's ``profile`` key;
            ``g8`` carries no such key, so its bytes never move.
        parallel: When True (default), each document is rendered in a
            ``ProcessPoolExecutor`` worker. Results are collected in
            canonical order so the output is byte-identical to the serial
            path — the regression test in ``tests/test_g8_corpus.py``
            asserts this.
        workers: Pool size. ``None`` (default) uses ``os.cpu_count()``
            clamped to :data:`_MAX_BUILD_WORKERS`. Tests pass a small
            value to exercise the worker path without 12-process overhead.

    Returns:
        A payload dict conforming to ``schemas/g8_corpus.schema.json``.

    Raises:
        PipelineError: If a template emits out-of-range span counts or
            unknown adversarial tags.
    """
    if profile not in PROFILES:
        raise PipelineError(f"unknown corpus profile {profile!r}; choose from {list(PROFILES)}.")

    effective_counts = dict(_DEFAULT_COUNTS) if counts is None else dict(counts)

    if set(effective_counts) != set(_DOCTYPE_ORDER):
        missing = sorted(set(_DOCTYPE_ORDER) - set(effective_counts))
        extra = sorted(set(effective_counts) - set(_DOCTYPE_ORDER))
        raise PipelineError(
            f"counts must cover exactly {list(_DOCTYPE_ORDER)}; missing={missing}, extra={extra}."
        )

    for doctype, n in effective_counts.items():
        if n < 0:
            raise PipelineError(f"counts[{doctype!r}] negative: {n}")

    # Canonical plan — _DOCTYPE_ORDER x range(count). This order is the
    # one the serial loop produces and the one the final payload preserves
    # (the sort at the end is a no-op on this order but kept as a belt).
    plan: list[tuple[str, int, str | None]] = [
        (doctype, index, _sub_template_for(doctype, index, effective_counts[doctype]))
        for doctype in _DOCTYPE_ORDER
        for index in range(effective_counts[doctype])
    ]

    # Mirror parse_sources_parallel: under pytest-xdist with workers=None,
    # fall to the serial path so N outer xdist workers * M inner pool
    # workers do not stack. Tests that want to exercise the parallel
    # branch pass workers= or parallel=True+workers= explicitly.
    if parallel and workers is None and os.environ.get("PYTEST_XDIST_WORKER"):
        parallel = False

    if parallel and plan:
        if workers is None:
            # Honour the same RESECTA_BUILD_WORKERS lever as
            # parse_sources_parallel + BloomFilter.build_parallel, so a
            # single env var caps all three pools. See
            # common.process.effective_workers.
            pool_workers = effective_workers(
                default=os.cpu_count() or 1, hard_cap=_MAX_BUILD_WORKERS
            )
        else:
            pool_workers = workers
        pool_workers = max(1, min(pool_workers, _MAX_BUILD_WORKERS, len(plan)))
        # Explicit shutdown(cancel_futures=True) on every exit path; see
        # corpus_ingest.parse_sources_parallel for rationale.
        pool = ProcessPoolExecutor(max_workers=pool_workers)
        try:
            # pool.map preserves input order, so results line up with plan
            # without us needing an explicit (doctype, index) key map.
            raw_results = list(
                pool.map(
                    _generate_one_document,
                    [seed] * len(plan),
                    [p[0] for p in plan],
                    [p[1] for p in plan],
                    [p[2] for p in plan],
                    [profile] * len(plan),
                    chunksize=16,
                )
            )
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    else:
        raw_results = [_generate_one_document(seed, dt, idx, sub, profile) for dt, idx, sub in plan]

    documents: list[dict[str, Any]] = []
    counts_by_demographic: dict[str, int] = dict.fromkeys(BUCKETS, 0)
    for (doctype, index, sub_template), (text, spans, tags, bucket, furniture) in zip(
        plan, raw_results, strict=True
    ):
        doc = _assemble_document(doctype, index, text, spans, tags, bucket, sub_template, furniture)
        documents.append(doc)
        counts_by_demographic[bucket] += 1

    documents.sort(key=lambda d: (d["doctype"], d["id"]))

    payload: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "counts_by_doctype": {dt: effective_counts[dt] for dt in _DOCTYPE_ORDER},
        "counts_by_demographic": {k: counts_by_demographic[k] for k in BUCKETS},
        "demographic_labels": list(BUCKETS),
        "documents": documents,
    }
    if profile != PROFILE_G8:
        # Named only off the default so the g8 payload's bytes never move.
        payload["profile"] = profile
    return payload
