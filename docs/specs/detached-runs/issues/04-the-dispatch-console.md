---
state: done
blocked_by: [03-detached-runs-and-the-run-ledger]
attempts: 1
---
# The dispatch console

The `giro` host skill becomes the console for a fleet of Runs: dispatch detached, relay the Run identifier and branch, keep the conversation free, answer progress questions from status and the Ledger, and walk escalations without the human ever leaving their branch. The published docs teach the new shape.

## Acceptance criteria

- [ ] The skill dispatches with detach, relays the Run identifier and branch, and returns the conversation to the human instead of blocking on the engine.
- [ ] Progress questions are answered from machine-readable status and the Ledger — never by re-running loops.
- [ ] The needs-human flow works from any branch: read the escalation from status and the Issue's log, edit only the Issue body in the Spec's worktree, re-dispatch — re-invoking is the answer.
- [ ] The skill's hard rules survive the rewrite: never edit frontmatter or logs, never implement in-chat, never merge the Spec branch.
- [ ] README and the design doc describe worktree-resident loops, dispatch, the Ledger, hermetic gates, and the console flow; stale prose about checkout-resident runs is gone.

## Attempt 1 — done — all gates green

- worker: The `giro` skill is now a dispatch console, and the published docs teach the shape the engine actually has. skills/giro/SKILL.md rewritten: it dispatches with `giro implement <target> --detach`, relays the Run id and the `giro/<slug>` branch, and hands the conversation back (explicit never-block rule, including `--no-follow` on `giro logs` so the command cannot hang a turn); progress questions are answered by reading `giro status --json`, `giro runs --json`, and the Ledger's event stream, never by re-running a loop; the needs-human walk reads the escalation from status and the Issue's trailing log in `.giro/worktrees/<slug>/`, answers by editing only the Issue body there, and re-dispatches; the hard rules survive and gain a fourth (never block the conversation on a Run). README gains a "Your checkout is never touched" section (per-Spec worktree, recorded base branch, dispatch, Ledger, one-Run-per-Spec lock + global worker cap, hermetic gates), a dispatch-shaped quickstart transcript, and loses the stale blocking prose; docs/design.md gains Run/Dispatch/Ledger/Base branch in the vocabulary and a `## Runs` section covering dispatch, the Ledger layout and event set, locks and the global cap, branch-tip status, and the escalation-answer contract. Tests in tests/test_console.py: one behavioural test on the engine plus guards on the skill (including that every `giro <verb>` and `--flag` it teaches exists in the CLI parser) and on both docs. Full suite green (162 tests) and ruff clean.
- notes: One engine change was required, not just prose: the documented escalation flow was broken. A human's answer edited in the Spec worktree left it dirty, and `_open_run`'s `ws.ensure_clean()` refused the next Run with 'working tree is not clean'. Added `Workspace.commit_within(relpath, message)` (pathspec-scoped through status/add/commit, no-op when nothing changed there — `git add` on an unmatched pathspec is fatal, so status gates it) and called it in `_open_run` for `docs/specs/<slug>` before the clean check, so the engine commits the answer and the clean check still speaks only about debris outside the Spec's own directory. I also flipped the M5 roadmap entry from 'planned' to 'done' in both README and docs/design.md, since this Issue is the Spec's last slice and leaving it as 'planned' would itself be stale prose; say the word if you would rather that wait until the branch is merged.
