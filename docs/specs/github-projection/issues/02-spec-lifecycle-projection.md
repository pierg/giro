---
state: done
blocked_by: [01-projection-plumbing]
attempts: 2
---
# Spec lifecycle projection

An activating Spec appears on GitHub: parent tracking issue created, branch pushed at its first checkpoint, draft pull request opened against the recorded base branch with a closing keyword linking the parent. Identifiers land in Spec frontmatter, and from then on the pull-request body's engine zone re-renders as states change.

## Acceptance criteria

- [ ] First activation creates the parent issue and the draft pull request exactly once; every later Run finds them through the identifiers recorded in Spec frontmatter.
- [ ] Pushes happen only at post-decision checkpoints; a Run that resets after a failed integrated Verify never force-pushes — asserted, not assumed.
- [ ] The pull-request body carries a marker-fenced engine zone (Issue checklist with states, current phase) that updates in place; human prose outside the fence is never modified.
- [ ] The closing keyword ties the pull request to the parent issue so the human's merge closes it; nothing merges automatically.
- [ ] Spec needs-human and done are visible on the parent issue via labels; done leaves the parent open for the merge to close.
- [ ] With Projection off or preflight-downgraded, Runs behave exactly as before — asserted against the scripted runner.

## Attempt 1 — gates failed

- review: Axis 1 (Issue fit / Spec fidelity): the checkpoint push is an untimed, potentially interactive network call in a path the Spec requires to be fail-soft, so an unreachable or credential-prompting remote blocks the Run instead of costing only visibility.
- conformance: New identifier `payload` uses a term CONTEXT.md lists under _Avoid_.

## Attempt 2 — done — all gates green

- worker: Fixed both findings from the previous attempt. (1) The checkpoint push is now bounded and non-interactive, like every other Projection call: Workspace._git takes an optional timeout and env, closes stdin when bounded, and converts a TimeoutExpired into an ordinary non-zero result (exit 124) rather than a raised exception; Workspace.push passes GIT_TERMINAL_PROMPT=0 and appends -o BatchMode=yes to GIT_SSH_COMMAND (preserving any the operator set), and Engine._checkpoint threads ProjectionSettings.timeout into it — so a dropping remote or a credential/host-key prompt is a fast False that _checkpoint already records via note("push", ok=False), and the loop carries on. (2) Renamed the two `payload` locals in Projection._apply_labels and _update_pull_request to `viewed`; the word CONTEXT.md lists under Avoid now appears nowhere in src/ or tests/. Two new tests in tests/test_projection_spec.py: one asserts every checkpoint push carries the Projection's timeout, DEVNULL stdin, and both non-interactivity variables; one injects a remote that never answers and asserts the Run's outcome, Issue states, Spec state, and per-checkpoint push attempts are identical to the healthy Run, that the parent issue is still created while the pull request waits for a published head, and that a failed-push Ledger event is recorded. Both were verified to fail against the pre-fix code — the hang escaped and killed the Run, exactly the reported scenario. Full suite: 196 passed; ruff clean.
- notes: Also updated the two config comments describing [projection] timeout, which now bounds the checkpoint push as well as each `gh` call. Unrelated observation, not acted on: Workspace._git leaves stdin inherited for unbounded calls (commit, merge), which is deliberate here — forcing DEVNULL everywhere could break a foreground Run that signs commits with a pinentry prompt.
