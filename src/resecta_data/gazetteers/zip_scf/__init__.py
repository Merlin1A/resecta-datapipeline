"""ZIP-to-SCF-to-state table builder (Phase 1).

Derives a compact Sectional Center Facility (SCF) map from the HUD USPS
ZIP-to-state crosswalk. The primary table is keyed on the 3-digit SCF prefix;
5-digit overrides carry ZIPs whose state differs from the dominant state for
their SCF.

The raw HUD crosswalk is a federal-government work (17 U.S.C. §105, public
domain) and is fetched by ``make sources``. Build targets never fetch.
"""

from __future__ import annotations

from .build import build

__all__ = ["build"]
