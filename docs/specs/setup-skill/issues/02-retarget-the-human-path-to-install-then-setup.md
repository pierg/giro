---
state: done
blocked_by: [01-ship-the-setup-host-skill]
attempts: 1
---
# Retarget the human path to install-then-setup

Every human-facing bootstrap path teaches install-then-setup: get the CLI, place host skills, then configure in chat. `giro init` remains the non-interactive fallback and says so.

**Escalation answer (conversation):** Sources and most tests are already on the branch. The engine rejects `completed` with an empty diff — three empty attempts just burned the budget. You **must** produce a non-empty commit. Do not touch `CONTEXT.md`, `docs/adr/`, README, or `skills/giro/SKILL.md` unless a test fails.

**Mandatory change this attempt:** extend `tests/test_setup_docs.py` with one more test that statically locks `giro init`'s "Next:" strings in `src/giro/cli.py` (assert the success message text contains both `giro install` and `setup`). Keep the existing README + doorway tests in that file, and keep `tests/test_init.py`'s behavioural init test. Envelope summary: say you added the static init-copy lock test — nothing else.

## Acceptance criteria

- [ ] `tests/test_setup_docs.py` locks README quickstart order (install → setup chat → spec path; init only as fallback).
- [ ] `tests/test_setup_docs.py` locks the doorway Setup section (`setup` in the install roster; chat setup; init = fallback).
- [ ] `tests/test_setup_docs.py` gains a new test locking `cmd_init`'s "Next:" copy in `src/giro/cli.py` for `giro install` + `setup`; `tests/test_init.py` behavioural coverage remains green.
- [ ] The attempt commit is non-empty (engine requirement).

## Attempt 1 — gates failed

- review: ADR created hand-numbered instead of scaffolded via `giro new adr`
- conformance: Worker modifies CONTEXT.md, which ADR 0010 forbids
- conformance: Worker creates ADR file, which ADR 0010 forbids
- conformance: ADR created manually instead of via 'giro new adr'

## Attempt 2 — escalated by worker

- Criteria #1 and #4 require a worker to create docs/adr/ content and edit CONTEXT.md's Skill glossary. Both my task-packet hard rules ('Never edit CONTEXT.md or docs/adr/') and this repo's own ADR 0010 ('Docs change in conversation; loops obey them') forbid exactly this — those paths are written upstream via the grill skill, enforced per attempt by conformance. The previous attempt made these edits anyway and conformance correctly flagged it; a hand-numbered ADR file (docs/adr/0012) and a CONTEXT.md diff already sit committed on this branch from that attempt. ADR 0010 names the resolution itself: 'a suspected stale ADR fails with a human-decision finding and resolves through escalation, then re-run' — that's what's needed here, not another worker attempt at the same forbidden edit. A human needs to either (a) apply the CONTEXT.md glossary line and scaffold/replace the ADR via `giro new adr` themselves (content is already drafted at docs/adr/0012-setup-owns-config-init-is-the-ci-fallback.md and CONTEXT.md:47), or (b) revise the Issue so these two criteria aren't worker-executed. Everything else in the Issue — README quickstart reorder, the giro doorway skill's Setup section, and giro init's 'Next:' copy pointing at install+setup — was already completed by the previous attempt and is untouched by the findings; I verified it's still correct and `pytest tests/test_init.py tests/test_skills.py` passes (11/11). I made no file changes this attempt.

## Attempt 1 — empty

- worker reported completed but produced no changes

## Attempt 2 — empty

- worker reported completed but produced no changes

## Attempt 3 — gates failed

- review: Test coverage incomplete for acceptance criteria
- review: Worker claim contradicts diff material

## Budget exhausted after 3 attempts

- Test coverage incomplete for acceptance criteria
- Worker claim contradicts diff material

## Attempt 1 — empty

- worker reported completed but produced no changes

## Attempt 2 — empty

- worker reported completed but produced no changes

## Attempt 3 — empty

- worker reported completed but produced no changes

## Budget exhausted after 3 attempts

- worker reported completed but produced no changes

## Attempt 1 — done — all gates green

- worker: Added the mandatory static-lock test to tests/test_setup_docs.py: test_cmd_init_next_copy_points_at_install_and_setup asserts cmd_init's 'Next:' string in src/giro/cli.py contains both 'giro install' and '`setup`'. No other files touched. All 4 tests in test_setup_docs.py plus tests/test_init.py's behavioural init test pass (also re-ran test_skills.py, 10/10 green).
- notes: Sources for all other acceptance criteria (README quickstart order, giro doorway skill's Setup section, cmd_init's Next copy itself) were already correct on the branch from a prior attempt; this attempt only added the missing test coverage per the escalation's mandatory instruction. Diff is non-empty: tests/test_setup_docs.py, +11/-0.
