---
blocked_by: [01-scaffold-seam]
---
# Read plain artifacts, default the missing state

## What to build

The store accepts an artifact authored with no lifecycle frontmatter — the whole file is body — and reads it back at its initial state (`draft` for a Spec, `ready` for an Issue), while the engine remains the only writer of state. Genuinely malformed frontmatter still fails loud.

## Acceptance criteria

- A Spec or Issue with no frontmatter parses at its initial state instead of raising.
- An Issue may carry a `blocked_by` list with no `state` line and read as `ready`.
- A present-but-invalid `state` is still a loud error.
- The engine stamps the real frontmatter when the Spec activates (existing save path).

## Not in this Issue

The `giro doctor` pre-flight — that is the boundary slice.
