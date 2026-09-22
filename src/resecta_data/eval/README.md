# The G8 eval contract

`make eval` is the pipeline's regression gate for the redaction engine's
detection behaviour: it runs the engine's two G8 emitters over the synthetic
G8 corpus and derives one verdict per surfacing site. This document is the
contract the code in this package and the `build eval-*` CLI commands cite:
what the target runs and writes (§1), the two harness files the derivations
read (§2), the four clauses a before/after comparison is decided on (§3), a
checked-in sample verdict (§4), how to read one (§5) and where everything
lands (§6).

Every number this contract produces is **corpus-relative**: it measures the
engine against the G8 corpus this repository generates (17 PII families,
1,100 documents, five doctypes × five demographic buckets) at one engine
commit and one corpus digest. There is no external standards bar; a verdict
is a comparison between two such measurements, never an absolute grade.

## 1. What `make eval` runs and writes

Prerequisites: `bootstrap corpus` (the stamped corpus build; the profile is
`EVAL_CORPUS_PROFILE`, default `g8`) and the sibling iOS checkout at
`RESECTA_IOS_ROOT` with a Swift toolchain (`ENGINE_PACKAGE` is
`$(RESECTA_IOS_ROOT)/Packages/RedactionEngine`). The target then:

1. checks that the engine's bundled test fixture **is** the built corpus
   (`shasum -a 256` of `build/corpus/g8_corpus.json` equals the engine's
   `Fixtures/corpus/g8_corpus.json`; `EVAL_INSTALL_CORPUS=1` installs it
   first). A non-`g8` profile is handed to the harness through
   `RESECTA_G8_CORPUS_PATH` (with its sha) and is never installed;
2. runs the two G8 emitters of the engine's **test** target on the host, each
   twice (`swift test --package-path <engine> --no-parallel --filter …`; no
   simulator, no production code):
   - `G8BaselineHarnessTests` — the **detector site**: doctype-aware
     detection surfaced on the raw balanced cutoff;
   - `G8SearchParityHarnessTests/emitSiteBBaseline` — **Site B**: the
     product's search gate, doctype-blind, with the scored families read
     through the composed posterior;

   the second pair writes into `rerun/`;
3. `cmp`s the six trio files and the two span sidecars against their `rerun/`
   twins — the determinism belt; any byte difference fails the target;
4. derives each site with `resecta-data build eval-baseline --cells …
   --raw-scores … --out-dir eval-<site> --spans … --corpus …`
   (`run.py` drives `baseline.py`, `headroom.py` and `spans.py`), then the
   site gap with `build eval-sitegap --detector … --siteb … --out
   g8_site_gap.json` (`sitegap.py`).

Everything lands under `EVAL_OUT` (default `build/eval/g8/`):

| path | written by | content |
|---|---|---|
| `g8_cells.json`, `g8_siteb_cells.json` | the emitters | file 1 — the per-cell counts (§2) |
| `g8_raw_scores.json`, `g8_siteb_raw_scores.json` | the emitters | file 2 — every pre-cutoff match with its ground-truth class (§2) |
| `g8_fire_features.json`, `g8_siteb_fire_features.json` | the emitters | per-fire feature rows; the context-scorer build consumes them, the derivations here do not |
| `g8_detector_spans.jsonl`, `g8_siteb_spans.jsonl` | the emitters | the per-span outcome sidecars (§2) |
| `rerun/` | the second emitter pair | the eight files above, byte-compared with the first |
| `eval-detector/`, `eval-siteb/` | `build eval-baseline` | `g8_detection_baseline.json`, `g8_headroom.json`, `g8_span_outcomes.json` |
| `g8_site_gap.json` | `build eval-sitegap` | Site B minus the detector site on every headline number |

The derived files are dev/eval artifacts: no install route, no
`asset_hashes.lock` row, not produced by `make build`. They follow the
pipeline's determinism rules (canonical JSON, sorted iteration, no
wall-clock) and its mechanism-language rule (category, aggregate and
mechanism only — no document text, no PII values, no coordinates).

## 2. The two harness files

**File 1 — `_cells.json`.** The Swift harness performs the offset-overlap
join between detections and ground-truth spans and emits one cell per
`"<category>_<doctype>_<bucket>"` (17 families × 5 doctypes × 5 buckets).
Each cell carries the six raw counts `true_positives`, `false_positives`,
`false_negatives`, `suppressed_by_negative_context`,
`adversarial_suppress_fired` and `adversarial_suppress_total`, plus the eight
additive packet-tier counters (`tier_must_total` / `tier_must_covered` and
their `should`, `watch` and `must_not` siblings). `baseline.py::build_baseline`
turns those counts into precision / recall / F1 / F2 / the
adversarial-suppression FP rate per cell and aggregated four ways
(`per_family`, `per_doctype`, `per_demographic`, `totals`), with Wilson
intervals on the totals and a `low_confidence` flag on any slice under 30
supports. No re-join, no IoU, no device score dump: pure arithmetic over the
supplied counts, with the canonical bytes of the input hashed into
`source_cells_sha256` for provenance.

**File 2 — `_raw_scores.json`.** Every match the detector returned *before*
the cutoff, as `rows` of `{category, doctype, bucket, raw, gt_class}`
(`gt_class` ∈ positive / suppress / none by offset overlap), plus
`balanced_cutoffs` (family → cutoff) and `absorbing_state_floor`.
`headroom.py::build_headroom` derives the per-family learned-term headroom
probe: how much false-positive mass sits above the cutoff against how much
true-positive mass sits below it, on the engine-seam posterior
`sigmoid(logit(raw) + logit(floor))`.

**The sidecars — `*_spans.jsonl`.** One row per ground-truth span of every
family plus one per surfaced detection overlapping no span, offsets only:
`{doc_id, family, start, end, tier, outcome}` with `det_start` / `det_end`
(the hull of the overlapping same-family detections) and `det_spans` on
covered rows; `outcome` ∈ tp / fn / fp / tn. `spans.py::build_span_outcomes`
aggregates them per cell, per context class and per furniture kind, and
**cross-checks the per-cell tallies against file 1** — a sidecar that does not
reproduce the trio's counters fails the derivation.

## 3. The four compare clauses

A before/after decision (`resecta-data build eval-compare`;
`compare.py::build_compare`) reads two derived `g8_detection_baseline.json`
files and applies four clauses per scorer family (the five the context scorer
covers: account, phone, mrn, ein, itin) and over the grand-total aggregate.
Each clause carries a **win** sense (the improvement bar is met) and a
**regressed** sense (the metric got worse beyond float noise, `1e-12`).
Failing to improve is not a regression: an identical before/after is a clean
non-regression.

| clause | function | win | regressed |
|---|---|---|---|
| C1 precision | `_clause_c1` | `after.precision ≥ before.precision + delta_p` | precision dropped |
| C2 family FPR | `_clause_c2` | `after_FPR ≤ before_FPR × (1 − delta_f_rel)`, where `family_FPR = 1 − precision_with_decoys` | `after_FPR > before_FPR` |
| C3 recall floor | `_clause_c3` | `after.recall ≥ before.recall − eps` (the over-suppression guard; one test decides both senses) | the same bound fails |
| C4 slice non-regression | `_clause_c4` | no uplift is demanded | any `per_doctype` (5) or `per_demographic` (5) precision drops by more than `delta_slice` |

A **regression is any gating clause regressing**, and an aggregate clean
verdict never excuses a per-family regression. A family with zero false
positives on the corpus is held to non-regression only; a family absent from
the panel is marked off-panel, never a `KeyError`. `delta_p` and `delta_slice`
are precision fractions in the module (the CLI takes points and converts);
`eps` and `delta_f_rel` are fractions throughout.
`compare_documents.py::build_compare_documents` applies the same four clauses
to two `documents_eval.json` files (the document-level Site-B eval,
`documents.py`), one row per document × leg kind, plus a pooled aggregate.

## 4. The sample verdict

[`SAMPLE-VERDICT.md`](./SAMPLE-VERDICT.md) is a checked-in transcription of
one `make eval` run's derived files — the per-family precision / recall / F1
table for both sites, the grand totals and the adversarial must-not-fire
count — labelled with the engine commit and the corpus digest it measured.
It is a worked example of the shape §1 produces, not a bar: the numbers are
corpus-relative and move whenever the engine, the corpus or the harness join
moves.

## 5. How to read a verdict

- **What moves is the finding.** Compare like with like: the same corpus
  digest, the same harness join, two engine commits. A per-family cell that
  moved names the family and the site; the four clauses (§3) say whether the
  move is a win, noise or a regression.
- **The identity belt comes first.** The trio + sidecar `cmp` across the two
  emitter runs and the `source_cells_sha256` in each derived file are the
  determinism evidence; without them a difference cannot be attributed.
- **Corpus-relative, no standards bar.** Precision and recall here are
  properties of the engine *on this synthetic corpus*. A family at recall
  1.0000 has exhausted the corpus's cases for it, not the world's; a family
  at precision 0.75 says the corpus's decoys fire it, and the `per_doctype` /
  `per_demographic` slices plus the context-class and furniture tables in
  `g8_span_outcomes.json` say where.
- **`low_confidence`** marks a slice under 30 supports; its interval is wide
  and its movement is not evidence on its own.
- **Site B against the detector site.** `g8_site_gap.json` is the product
  path minus the detector path on every headline number: a positive recall
  gap is the search gate surfacing what raw detection did not, a negative
  precision gap is its price.

## 6. Where the outputs go

`EVAL_OUT` (default `build/eval/g8/`) is git-ignored. The evidence copy of a
run — the derived files plus a manifest naming the engine commit, the corpus
digest and the host — is a hand step kept outside this repository; the
repository holds only this contract, the sample verdict and the code.
