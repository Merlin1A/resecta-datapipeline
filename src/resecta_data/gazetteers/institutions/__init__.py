"""Government-institution name gazetteer builder (Phase 2).

Consumes the Federal Register agencies API feed (D-01; NARA Office of the
Federal Register + GPO; 17 U.S.C. §105, public domain) and emits
``build/gazetteers/institutions.json``. Phase 2 scope is federal agencies
only; state-level (NAAG, CDC, SSA rosters) is deferred pending a separate
license-review cycle (findings L4).

The legacy GSA Federal Hierarchy Crosswalk parser is retained for the
advisory cutover-diff sidecar emitted alongside the rebuild artifact (STRAT
§10.4 row §D1 = A1 1:1).
"""

from __future__ import annotations

from .build import build, build_cutover_diff

__all__ = ["build", "build_cutover_diff"]
