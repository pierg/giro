---
state: done
blocked_by: []
attempts: 2
---
# Worktree-resident spec loops

Every `giro implement` runs its loop inside a persistent per-Spec worktree holding the `giro/<slug>` branch, forked from an explicit, recorded base branch. The invoking checkout — any branch, clean or dirty — is never switched, never gated, never written. First activation seeds the worktree with the Spec's directory from the checkout and commits it; from then on the branch copy is the truth.

## Acceptance criteria

- [ ] A Run never changes the invoking checkout's branch, HEAD, index, or files — verified with uncommitted changes present and a non-base branch checked out.
- [ ] All loop commits (activation, plan, claims, attempts, merges, state moves) land on `giro/<slug>` inside a worktree created at first activation and reused on re-invoke and resume.
- [ ] The base branch resolves once — config value if set, else the invoking checkout's branch at first activation — is recorded in Spec frontmatter by the engine, and later Runs use the recorded value.
- [ ] At first activation the Spec's directory is seeded from the invoking checkout into the worktree and committed; on resume it is not re-seeded — the branch copy wins.
- [ ] Sequential and parallel concurrency behave identically with respect to the invoking checkout, and parallel waves still work from within the Spec worktree.
- [ ] The engine's runtime directory is never committed to any branch, by engine or human.
- [ ] A missing base branch or a repository without commits fails with a clear error before any context is spawned.
- [ ] Foreground exit codes are unchanged: 0 proof, 2 needs-human, 1 error.

## Attempt 1 — gates failed

- review: Axis 1 (Issue fit): later Runs never see the recorded `base_branch` — it is read from the invoking checkout's Spec copy, which the engine never writes, so a resume can be refused on a base branch it does not need.
- review: Axis 2 (code quality): `_preflight` still resolves skill gates against the invoking checkout while the gates themselves now run in the worktree, so its fail-closed promise no longer matches what the Run will execute.

## Attempt 2 — done — all gates green

- worker: Fixed both review findings on the worktree-resident Spec loop. (1) Base-branch resolution is now a fork's question only: `_open_run` checks whether `giro/<slug>` already exists and, when it does, skips `_base_branch_for` entirely — so a resume never re-infers from config-or-current-branch and never validates a branch it does not need. `_base_branch_for` also drops its dead `spec.base_branch` clause (that copy is structurally always empty), and the recorded `base` / `base_branch` are written exactly once, at first activation. A resume from a detached HEAD after the original base branch has been deleted now proceeds and still reports `base_branch == "side"`. (2) Preflight moved onto the bound engine — `run._preflight()` runs after `_open_run`, so skill-gate criteria resolve against the Spec worktree the gates will actually execute in, still before any context is spawned. A criterion that exists only as an uncommitted file in the checkout now fails closed at zero cost; a criterion present only on the branch resolves and the Run proceeds. Added four tests to tests/test_worktree_runs.py (resume-honours-recorded-base, spec-branch-cannot-be-its-own-base, and the two preflight-root tests); each was verified to fail against the previous code. Full suite: 112 passed, ruff clean.
- notes: Two adjustments worth flagging. (a) test_checkout_sitting_on_the_spec_branch_fails_loudly was updated, not weakened: with the branch-exists short-circuit, a checkout sitting on giro/<slug> now always hits the worktree-conflict error ('is checked out') rather than 'cannot fork from itself', which is the accurate cause; the fork-from-itself guard is still live and now covered by its own test (config pins base_branch = "giro/demo" while that branch does not exist). (b) docs/design.md still carried the old 'isolation asymmetry' paragraph (sequential workers see git-ignored files); the Spec's implementation decisions say that asymmetry closes here and 'the docs say so', so that paragraph now states that both shapes run on committed content only and gates must be hermetic. Unrelated observation, not fixed: Config is loaded from the invoking checkout's giro.toml while gate criteria and commands resolve inside the worktree, so the gate *list* and the gate *content* come from different trees. That is defensible (the config belongs to the invocation) but is worth an explicit decision when the dispatch Issue lands, since a detached Run's config is captured at dispatch time.
