---
state: done
blocked_by: []
attempts: 1
---
# Strip leading YAML frontmatter in resolve_skill

When `resolve_skill` (src/giro/skills.py) reads a skill body for prompt injection, a leading YAML frontmatter block — delimited by a `---` line, the `name:`/`description:`/host-flag lines, and a closing `---` line — is stripped before the body is returned. Consumers that inject skill text into a prompt (worker task packets, planner prompts, skill-gate criteria) automatically receive clean bodies with no code changes of their own, since they all go through this one function.

Rules:
- Only a block that begins at the very start of the file is treated as frontmatter. A `---` line appearing later in the body (e.g. as a Markdown section divider) is ordinary content and must survive untouched.
- A skill body with no leading frontmatter block passes through byte-for-byte unchanged.
- `giro install` and any code path that reads `SKILL.md` from disk for host discovery (rather than for prompt injection) is untouched — hosts still see the raw file including frontmatter.

Acceptance criteria:
- [ ] `resolve_skill` strips a leading `---`-delimited frontmatter block from the returned body.
- [ ] A `---` occurring mid-body (not at the start of the file) is preserved in the returned body.
- [ ] A skill body with no frontmatter is returned unchanged.
- [ ] Unit tests cover all three cases above.
- [ ] The existing test suite (including `tests/test_skills.py`) stays green with no test weakened to accommodate this change.
- [ ] No change to `giro install` behavior or to any on-disk `SKILL.md` file.

## Attempt 1 — done — all gates green

- —
