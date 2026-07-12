"""Adversarial pattern template generator.

Produces test-fixture fragments that probe pressures on the downstream
detector stack:

  - detector_shape_fp: strings shaped like SSN / NPI / DEA / address that
    should NOT redact, exercising the false-positive guard.
  - keyword_stuffing_g5: stanzas that try to swing the document-type
    classifier via keyword repetition (plan item G5).
  - homoglyph: labels or digit fields laced with Unicode confusables.
  - invisible_style: HTML fragments whose PII text is visually suppressed
    via CSS (white-on-white, zero opacity, sub-point font size).
  - whitespace_injection: digit runs broken by zero-width characters.
  - multiline_date_collision: adjacent date lines that could confuse a
    DOB detector into latching onto a filing date.
  - column_header_label: table-layout fragments carrying a bbox_context
    array so the detector can resolve labels from header-row bboxes.
"""

from __future__ import annotations

from .patterns import build

__all__ = ["build"]
