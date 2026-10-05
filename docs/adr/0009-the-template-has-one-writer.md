# The template has one writer

Extended by [ADR-0019](0019-state-frontmatter-is-engine-only.md): the one writer moves down a layer into `src/giro/scaffold.py` (numbering and starter bodies), and the standalone authoring skills now write plain bodies with *no* frontmatter — `state:`/`attempts:` stay engine-only, stamped at activation rather than at creation.

Specs, Issues, and ADR numbering are scaffolded by `giro new` through the same store code the planner and gap-filing use; chat skills write bodies only (one file, two zones). `state:` and `attempts:` are engine-only; `blocked_by` is planning data, set via `--blocked-by` and validated loudly.
