"""Parser for the SEC EDGAR company_tickers.json feed (D3 source).

Consumes the JSON file at
``src/resecta_data/gazetteers/institutions/sources/edgar_company_tickers_YYYYMMDD.json``
(U.S. SEC federal government data, non-copyrightable under 17 U.S.C. § 105).

Source shape::

    {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
        ...
    }

Records are keyed by their 0-based file index. This parser takes the top-N
entries by file-index order (N=500 default, parameterized) and classifies each
into one of two categories:

- ``"financial_institution"`` — company whose title contains any of the
  name-keyword indicators below.
- ``"employer"`` — all other top-N companies.

**Proxy caveat:**

(a) This classification is a proxy because ``company_tickers.json`` carries no
    SIC field. The design specifies SIC 6000-6799 as the correct split for
    financial-institution vs. employer, but the ticker feed omits SIC codes.

(b) Both ``financial_institution`` and ``employer`` categories anchor identically
    to ``.financial`` in Swift's ``InstitutionGazetteer.anchoredDoctype``, so the
    split is currently behavior-neutral at the suppression layer.

(c) The curation criterion (keyword list + top-N cutoff) is documented here and
    changes only under an approved source plan. The fetcher is authored but not
    executed (see scripts/fetch_edgar_companies.sh).

Alias derivation uses ``normalize.derive_aliases`` identical to
the FDIC parser.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError

from .normalize import derive_aliases, nfkc_lower
from .parse_gsa import InstitutionEntry

logger = logging.getLogger(__name__)

_SOURCE_ENCODING: Final[str] = "utf-8"
_CATEGORY_FINANCIAL: Final[str] = "financial_institution"
_CATEGORY_EMPLOYER: Final[str] = "employer"
_DEFAULT_TOP_N: Final[int] = 500

# Name-keyword classifier for financial_institution category.
# A company title matching any of these terms (case-insensitive substring) is
# classified as financial_institution. Everything else is employer.
# This is a proxy for SIC 6000-6799 since company_tickers.json lacks SIC fields.
_FINANCIAL_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "bank",
        "bancorp",
        "bancshares",
        "financial",
        "finance",
        "capital",
        "insurance",
        "securities",
        "invest",
        "asset",
        "holdings",
        "trust",
        "mortgage",
        "credit",
    }
)


def _is_financial(title: str) -> bool:
    """Return True if the company title is classified as a financial institution.

    Uses a name-keyword classifier (proxy for SIC 6000-6799; see module
    docstring for documented limitations).

    Args:
        title: Raw company title from the EDGAR feed.

    Returns:
        True if any financial keyword appears in the lowercased title.
    """
    lower = nfkc_lower(title)
    return any(kw in lower for kw in _FINANCIAL_KEYWORDS)


def parse_edgar_companies(
    path: Path,
    top_n: int = _DEFAULT_TOP_N,
) -> list[InstitutionEntry]:
    """Parse the SEC EDGAR company_tickers.json into institution entries.

    Takes the top-N records by file-index order. Each record is classified as
    ``financial_institution`` or ``employer`` via a name-keyword classifier
    (see module docstring). Entries are sorted by name for determinism.

    Args:
        path: JSON path. Must exist; source is immutable.
        top_n: Maximum number of records to consume (default 500). Records
            beyond this index are discarded.

    Returns:
        Entries in deterministic order, sorted by name. Each entry has
        ``category`` of either ``"financial_institution"`` or ``"employer"``
        and ``jurisdictions=["US"]``.

    Raises:
        MissingSourceError: If ``path`` does not exist.
        PipelineError: If the JSON is malformed or records have unexpected shape.
    """
    if not path.is_file():
        raise MissingSourceError(
            f"EDGAR company_tickers.json not found at {path}. "
            "Run the fetch_edgar_companies.sh script on Linux to fetch, then copy to sources/."
        )

    with path.open("r", encoding=_SOURCE_ENCODING) as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"{path}: invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise PipelineError(
            f"{path}: expected a dict of file-index → record, got {type(raw).__name__}"
        )

    # Extract records sorted by integer file-index key, then take top-N.
    try:
        sorted_records = [raw[k] for k in sorted(raw.keys(), key=int)]
    except (ValueError, TypeError) as exc:
        raise PipelineError(f"{path}: non-integer key in company_tickers.json: {exc}") from exc

    selected = sorted_records[:top_n]
    logger.info(
        "parse_edgar: processing top-%d of %d records from %s",
        len(selected),
        len(sorted_records),
        path.name,
    )

    entries: dict[str, InstitutionEntry] = {}

    for record in selected:
        if not isinstance(record, dict):
            continue
        title = (record.get("title") or "").strip()
        if not title:
            continue

        canonical = title
        canonical_lower = nfkc_lower(canonical)
        category = _CATEGORY_FINANCIAL if _is_financial(title) else _CATEGORY_EMPLOYER

        aliases_lower = derive_aliases(title)
        aliases_filtered = [a for a in aliases_lower if a != canonical_lower and a.strip()]

        # Deduplicate: first occurrence of a canonical name wins.
        if canonical in entries:
            continue

        entries[canonical] = InstitutionEntry(
            name=canonical,
            aliases=tuple(sorted(set(aliases_filtered))),
            category=category,
            jurisdictions=("US",),
        )

    result = sorted(entries.values(), key=lambda e: e.name)
    n_financial = sum(1 for e in result if e.category == _CATEGORY_FINANCIAL)
    n_employer = len(result) - n_financial
    logger.info(
        "parse_edgar: %d entries (%d financial_institution, %d employer) from %s",
        len(result),
        n_financial,
        n_employer,
        path.name,
    )
    return result
