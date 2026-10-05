---
state: done
blocked_by: []
attempts: 1
---
# Ship the setup host skill

giro gains a new host skill, `setup`, that a chat agent can run in a project to produce a fitting `giro.toml`. The skill inspects the current working tree and the agent CLIs on PATH, interviews only for preferences, and writes config only after the user confirms. `giro install` places it beside the existing host skills.

## Acceptance criteria

- [ ] A bundled host skill `setup` exists under `skills/` with a `SKILL.md` whose frontmatter name/description make it discoverable for "set up giro" / configure this project.
- [ ] The skill procedure covers: treat cwd as project root; probe the four bundled drivers on PATH; gather Verify-gate evidence from the repo; grill-style preference rounds (driver/roster, concurrency, budgets, which gates including `review` / `conformance`); propose a full `giro.toml`; write only after confirm; create `docs/specs/` if missing; revise an existing `giro.toml` when present rather than requiring deletion.
- [ ] The skill writes the full config from settled preferences — it does not call `giro init` and patch the template.
- [ ] `giro install` places `setup` into the host skills destination alongside `giro` / `spec` / `plan` / `grill`.
- [ ] Existing install / bundled-category tests are extended so a missing `setup` skill fails the suite.

## Attempt 1 — done — all gates green

- worker: Added skills/setup/SKILL.md (a new host skill, peer of giro/spec/plan/grill) covering the full Acceptance Criteria: treat cwd as project root, probe the four bundled drivers on PATH, gather Verify-gate evidence from repo artifacts (manifests, Makefile, CI, docs, CONTEXT.md/docs/adr presence), run grill-style preference rounds for driver/roster/concurrency/budgets/gates, propose and write the full giro.toml only after confirmation (never via giro init + patch), create docs/specs/ if missing, and revise an existing giro.toml rather than requiring deletion. Extended tests/test_skills.py's test_bundled_categories_hold_the_right_files and test_install_places_only_host_skills to assert the setup skill is bundled and installed, so a missing setup skill fails the suite. giro install already copies any skills/<name>/SKILL.md directory generically, so no cli.py change was needed for placement. Ran the full test suite (91 passed) and the targeted skills tests (10 passed).
- notes: Stayed strictly inside this Issue's scope: did not touch skills/giro/SKILL.md's Setup section, README, CONTEXT.md, or ADRs, even though the parent Spec mentions those doc updates — they read as separate Issues in this Spec and CONTEXT.md/docs/adr are explicitly off-limits to workers.
