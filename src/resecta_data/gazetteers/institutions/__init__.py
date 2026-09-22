"""Government-institution name gazetteer builder (Phase 2).

Consumes the Federal Register agencies API feed (NARA Office of the
Federal Register + GPO; 17 U.S.C. §105, public domain) and emits
``build/gazetteers/institutions.json``. Phase 2 scope is federal agencies
only; state-level (NAAG, CDC, SSA rosters) is deferred pending a separate
license-review cycle (findings L4).

The legacy GSA Federal Hierarchy Crosswalk parser is retained for the
advisory cutover-diff sidecar emitted alongside the rebuild artifact
(``common.cutover.build_cutover_diff`` over this package's legacy and
rebuild rows).
"""

from __future__ import annotations

from .build import (
    INSTITUTIONS_CUTOVER,
    build,
    legacy_institution_rows,
    rebuild_institution_rows,
)

__all__ = ["INSTITUTIONS_CUTOVER", "build", "legacy_institution_rows", "rebuild_institution_rows"]
