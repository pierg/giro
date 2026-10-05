# Rename and de-giro the authoring skills

## What to build

The three authoring skills lose the `giro-` prefix and every hard CLI dependency, becoming generic skills a person can install and run with no giro at all. Their content describes the method and the plain artifacts it produces, references only sibling authoring skills, and credits its Matt Pocock lineage. The engine skills (`giro`, `giro-setup`) and every doc that names the old skills are updated to match.

## Acceptance criteria

- `skills/spec`, `skills/plan`, `skills/grill` exist; the `giro-`prefixed folders are gone.
- No authoring skill names `giro new`, `giro implement`, `giro install`, or `giro.toml`.
- Each authoring skill credits Matt Pocock and describes the plain artifact it writes.
- `README`, `DESIGN`, `CONTEXT`, `CHANGELOG`, the config template, and the engine skills reference the new names; a standalone-install README exists.

## Not in this Issue

The scaffold seam and the reading/boundary changes — those are their own slices.
