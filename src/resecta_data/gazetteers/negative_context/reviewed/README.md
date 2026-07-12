# Reviewed negative-context gazetteer (D6)

This directory holds the **hand-reviewed** `negative_context.json` and its
`negative_context.meta.json` sidecar. It is empty (README only) until the
first reviewed file lands (search-impl S3).

## Review gate (decision D6)

- `make gazetteers` emits `build/gazetteers/negative_context_candidates.json`.
- Jesse hand-reviews the candidates — the first version and **every
  subsequent diff**. Agents never auto-promote candidates to this directory.
- Jesse commits the reviewed `negative_context.json` here, together with the
  sidecar, **in the same commit**.

## Sidecar format

```json
{
  "reviewed_version": "<SHA-256 of build/gazetteers/negative_context_candidates.json at review time>",
  "reviewed_by": "jesse",
  "reviewed_at": "YYYY-MM-DD",
  "note": "free-form provenance"
}
```

`reviewed_version` is the hash of the **candidates** file the review was
based on — not the hash of the reviewed file itself (that would self-verify
trivially and never detect a stale review).

## Staging

`make stage-reviewed-negctx` copies both files into `build/gazetteers/`
after recomputing the live candidates hash and comparing it against
`reviewed_version`. A mismatch fails the staging step: the candidates have
drifted since the review and a re-review is required. The staged copies are
out-of-band build artifacts (`make clean` destroys them; this committed
directory is the durable source). `make install-assets` runs the staging
step as a prerequisite.
