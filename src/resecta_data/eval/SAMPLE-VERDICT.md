# Sample verdict — one `make eval` run, transcribed

**Corpus-relative: G8 17/17 at corpus digest
`c7fc84c39761cf65044b68bf06c075cd8296aa413b26d3d0c5dd01915da5ca8c` (the
`asset_hashes.lock` row `corpus/g8_corpus.json`), engine at iOS commit
`39d9990e7b0007399f9a5ca7486c3db2e1166dc5`; no standards bar exists.** The six
trio files and the two sidecars were byte-identical across the two emitter
runs. The numbers are the derived `g8_detection_baseline.json` and
`g8_span_outcomes.json` of each site, rounded to four decimals. Support is the
number of ground-truth spans of the family; both sites read the same corpus,
so it is the same on both.

## Grand totals

| site | precision | recall | F1 | F2 | support | must-not-fire decoys fired |
|---|---|---|---|---|---|---|
| detector | 0.8868 | 0.8792 | 0.8830 | 0.8807 | 11,287 | 0 / 528 |
| Site B | 0.8933 | 0.9439 | 0.9179 | 0.9333 | 11,287 | 0 / 528 |

## Per family

| family | support | detector P | detector R | detector F1 | Site B P | Site B R | Site B F1 |
|---|---|---|---|---|---|---|---|
| account | 300 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.6667 | 0.8000 |
| address | 1,300 | 1.0000 | 0.8723 | 0.9318 | 1.0000 | 0.8723 | 0.9318 |
| creditcard | 550 | 0.9982 | 1.0000 | 0.9991 | 0.9982 | 1.0000 | 0.9991 |
| dateofbirth | 700 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| dea | 250 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| driver'slicense | 435 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| ein | 100 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| email | 1,000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| itin | 300 | 1.0000 | 0.8333 | 0.9091 | 1.0000 | 0.8333 | 0.9091 |
| licenseplate | 450 | 1.0000 | 0.6667 | 0.8000 | 1.0000 | 0.6667 | 0.8000 |
| medicalrecord | 250 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| name | 2,837 | 0.7557 | 0.9767 | 0.8521 | 0.7557 | 0.9767 | 0.8521 |
| npi | 250 | 1.0000 | 1.0000 | 1.0000 | 0.9804 | 1.0000 | 0.9901 |
| passport | 265 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| phone | 1,100 | 0.6060 | 0.5173 | 0.5581 | 0.7297 | 0.9082 | 0.8092 |
| routingnumber | 200 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| ssn | 1,000 | 1.0000 | 0.9000 | 0.9474 | 1.0000 | 1.0000 | 1.0000 |

## Span outcomes (the sidecar aggregate)

The sidecar's ground-truth rows include the 528 must-not-fire decoys, which
the cells count separately (11,287 + 528 = 11,815).

| site | ground-truth rows | tp | fn | fp (detection-only) | tn (quiet decoys) | must-not decoys fired |
|---|---|---|---|---|---|---|
| detector | 11,815 | 9,924 | 1,363 | 1,267 | 528 | 0 |
| Site B | 11,815 | 10,654 | 633 | 1,272 | 528 | 0 |

Reading this run: `account` surfaces nothing at the detector site (0 of 300)
and two thirds at Site B at precision 1.0000; `phone` is the widest site gap;
`name` carries most of the false positives on both sites (precision 0.7557 at
recall 0.9767). None of these is a grade — see the contract's §5.
