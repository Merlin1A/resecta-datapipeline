# Reviewed negative-context gazetteer

This directory holds the reviewed `negative_context.json` that ships to the
engine as `Resources/Gazetteers/negative-context.json`, together with its
`negative_context.meta.json` sidecar. `make gazetteers` builds the candidates
file (`build/gazetteers/negative_context_candidates.json`); the reviewed file is
committed here and staged from here — the build never writes it.

## Change policy

Curated context assets change only under a written change plan approved by
the maintainer before the edit — the asset, the rows or fields, the reason,
and the regeneration and verification steps. No row-by-row review afterwards.
The sidecar drift check stays as a mechanical tripwire the same change
re-stamps.

A change to this gazetteer therefore lands as one commit that carries the
edited builder inputs (`_scope_rules.py`), the rebuilt candidates, the updated
reviewed file, the re-stamped sidecar and the moved `asset_hashes.lock` row.

## Sidecar format

```json
{
  "reviewed_version": "<SHA-256 of build/gazetteers/negative_context_candidates.json the change was based on>",
  "reviewed_by": "maintainer",
  "reviewed_at": "YYYY-MM-DD",
  "note": "free-form provenance: the approved plan the change implements"
}
```

`reviewed_version` is the hash of the **candidates** file the change was based
on — not the hash of the reviewed file itself (that would self-verify
trivially and never detect drift).

## Staging

`make stage-reviewed-negctx` copies both files into `build/gazetteers/` after
recomputing the live candidates hash and comparing it against
`reviewed_version`. A mismatch fails the staging step: the candidates changed
without the sidecar being re-stamped, so the change that produced them is
incomplete. The staged copies are out-of-band build artifacts (`make clean`
destroys them; this committed directory is the durable source).
`make install-assets` runs the staging step as a prerequisite.
