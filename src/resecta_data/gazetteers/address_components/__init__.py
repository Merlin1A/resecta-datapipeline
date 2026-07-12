"""Address-component gazetteer builder (Phase 2).

Consumes the USGS GNIS populated-places extract and the Census 2024
counties gazetteer (both 17 U.S.C. §105, public domain), plus a hardcoded
street-types list, and emits ``build/gazetteers/address_components.json``.

The ``build_cutover_diff`` helper emits the BINDING cutover-diff sidecar
required by STRAT §10.4 row §D7 (G1 PARTIAL [BINDING]) for the
cc-derive-rebuild S2 segment. Verification-posture: A7 already consumes
the §D7 mandated sources, so the diff is empty by construction.
"""

from __future__ import annotations

from .build import build, build_cutover_diff

__all__ = ["build", "build_cutover_diff"]
