---
state: done
blocked_by: [03-issue-lifecycle-projection]
attempts: 1
---
# Escalation surface and the ready flip

The two moments a human must feel, delivered. needs-human: the blocker posted as a comment, the escalation label applied, and the configured human assigned so a notification actually fires. done: Validate green flips the draft pull request to ready for review with a closing summary — leaving the human's merge as the only remaining act.

## Acceptance criteria

- [ ] Issue-level needs-human produces, on the sub-issue: the escalation label, a comment carrying the durable reason from the log, and assignment of the configured human. Spec-level needs-human does the same on the parent.
- [ ] Re-dispatch after an answer clears the escalation label and comments that the Run resumed.
- [ ] When the token identity is the human themself, the self-notification limitation is surfaced in preflight output and the docs, with the machine-account recommendation.
- [ ] Validate green flips the pull request from draft to ready for review and posts a final summary comment on the parent; nothing merges automatically.
- [ ] All of it is fail-soft: with GitHub unreachable, escalation and completion still land fully in markdown and the Ledger, and exit codes are unchanged.

## Attempt 1 — done — all gates green

- worker: Implemented the escalation surface and the ready flip. needs-human now lands on GitHub as a marked comment carrying the durable reason (plus the last gates' findings), an @mention, and an assignment of the new `[projection] assignee` — on the sub-issue for an Issue, on the parent for a Spec; the `giro:needs-human` label continues to follow state through ordinary convergence, and re-invoking giro clears it and posts a 'Run resumed' comment (both Issue and Spec paths). Preflight gained an `identity` check that names the token's login and warns — in `giro project --check` output, in the JSON, and out loud at every Run's start — when that identity is the assignee themself, with the machine-account recommendation; the same is documented in docs/design.md and the giro.toml starter template. Validate green now reads the pull request's draft state, flips it to ready for review only if it is still a draft, and posts a final summary comment on the parent; nothing merges. All of it is fail-soft: escalation and completion land in markdown and the Ledger with GitHub unreachable, and Run outcomes/exit codes are unchanged. 18 new tests in tests/test_projection_escalation.py; full suite 255 passed, ruff clean.
- notes: Escalation projection had to move to just after the state checkpoint (it previously would have fired before the sub-issue existed on a first Run), so `_escalate_budget`/`_record_integration` now return the durable reason and the callers tell it once the decision is committed — the Ledger escalation event moved with it, same target/reason payload. Two existing narration tests were adapted, not weakened, for the deliberately added comments: comment-volume now asserts the story is still exactly one comment per log section plus the single escalation comment, and the parent-narration test asserts the Validate verdict comment is followed by the completion comment. Out of scope, worth noting for the Reconciler Issue: if the ready flip fails soft, a later Run of an already-done Spec returns early and never retries it — reconverging that is Reconcile's job.
