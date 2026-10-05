---
state: done
base: d4b6b3ecdc065834b6570da3db83cecbb7f164eb
---
# Strip skill frontmatter

## Problem Statement

Skill bodies under `skills/` carry YAML frontmatter (`name`, `description`, host flags) meant for host discovery. When the engine injects a skill body into a context prompt — worker discipline, planner judgment, skill-gate criteria — the frontmatter rides along as noise the model must ignore.

## Solution

When the engine resolves a skill body for prompt injection, a leading YAML frontmatter block is stripped. Hosts reading `SKILL.md` from disk are unaffected.

## User Stories

1. As a worker context, I receive the implement discipline without a YAML preamble, so my packet starts with instructions.
2. As a judge context, I receive a skill gate's criterion without `name:`/`description:` lines, so the criterion is only judgment.
3. As a planner context, I receive the plan skill's slicing rules without host metadata.

## Implementation Decisions

- Stripping happens once, in `resolve_skill` (`src/giro/skills.py`), so every consumer — worker packets, planner prompts, skill gates — benefits without changes elsewhere.
- Only a *leading* block delimited by `---` lines is stripped; `---` appearing later in a body is content and must be preserved.
- A body with no frontmatter passes through unchanged.

## Testing Decisions

- Unit tests on `resolve_skill`: body with frontmatter (stripped), body without (unchanged), `---` mid-body (preserved).
- The existing suite must stay green; no existing test may be weakened.

## Out of Scope

- Any change to `giro install` or the on-disk `SKILL.md` files.
- Parsing or using frontmatter values.
