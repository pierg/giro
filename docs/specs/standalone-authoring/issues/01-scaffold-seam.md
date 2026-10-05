# Extract the shared authoring seam

## What to build

A dependency-free module that is the single home for how a fresh Spec, Issue, or ADR is numbered and what its starter body says. Both the engine's `giro new` and the standalone authoring skills rely on the same numbering scheme (`NN-slug` Issues, `NNNN-slug` ADRs) so the two paths cannot drift.

## Acceptance criteria

- A `slugify`, starter body templates, and `issue_id`/`adr_filename`/`write_adr` helpers exist in one module with no imports from other engine modules.
- `write_adr` produces plain markdown with no frontmatter, numbered as the next `NNNN` in `docs/adr/`.
- `giro new` produces the same artifacts it did before (the engine still stamps state).

## Not in this Issue

Changing how the engine reads artifacts — that is the tolerant-reading slice.
