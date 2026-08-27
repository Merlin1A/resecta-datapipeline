"""Address-component gazetteer builder (Phase 2).

Consumes the USGS GNIS populated-places extract and the Census 2024
counties gazetteer (both 17 U.S.C. §105, public domain), plus a hardcoded
street-types list, and emits ``build/gazetteers/address_components.json``.

The ``build_cutover_diff`` helper emits a cutover-diff sidecar for the
rebuilt source chain. Verification-posture: this builder already consumes
the mandated sources, so the diff is empty by construction.
"""

from __future__ import annotations

from .build import build, build_cutover_diff

__all__ = ["build", "build_cutover_diff"]
