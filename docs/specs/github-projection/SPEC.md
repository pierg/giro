---
state: done
base: 709e433700b5852ec3c6f3f69be60ce4d403926f
base_branch: main
---
# GitHub Projection

## Problem Statement

A Run tells its story only to the Ledger and the markdown — legible at a terminal, invisible everywhere the human actually lives: the repository page, its issues and pull requests, the notifications on their phone. Progress questions require a CLI; escalations wait silently until someone thinks to ask; a teammate watching the repository sees nothing until an unexplained branch appears. The machine works in the dark.

## Solution

giro renders its state onto GitHub, continuously and strictly one-way. A Spec becomes a parent tracking issue plus a draft pull request; every giro Issue becomes a sub-issue whose labels and open/closed state follow the loop; attempts, verdicts, waves, and escalations arrive as comments as they happen; integrated gate results appear as commit statuses; Validate passing flips the pull request from draft to ready for review — and the merge stays the human's, now one click on GitHub's own review page. needs-human reaches the human as a notification, not a discovery. The Projection is display, never memory and never control: the engine never reads GitHub back, a dead network never fails a loop, and one Reconcile command reconverges GitHub from markdown at any time.

## User Stories

1. As the human, I want each Spec to appear as a tracking issue with its Issues as sub-issues, so that GitHub's native progress bar shows the wave landing.
2. As the human, I want labels and open/closed states to follow the loop automatically, so that the board never needs hand-tending.
3. As the human, I want each attempt's outcome and findings posted as comments when they happen, so that I can follow a Run from the repository page instead of a terminal.
4. As the human, I want a draft pull request opened when a Spec activates, so that the work has a review surface from its first commit.
5. As the human, I want integrated Verify and Validate verdicts as commit statuses on the pull request, so that "all gates green" is literally green.
6. As the human, I want needs-human to land as a comment, a label, and an assignment that notifies me, so that escalations reach my phone when I am away.
7. As the human, I want Validate-green to flip the pull request to ready for review with the tracking issue linked to close on merge, so that my merge is the only remaining act.
8. As the human, I want one command that reconverges GitHub after an outage, drift, or hand-meddling, so that the Projection is never load-bearing.
9. As the human, I want Projection failures to warn and never block, so that a dead network costs visibility, not work.
10. As the operator, I want the token read from local env config, scoped to one repository, and used only by the engine, so that a leak's blast radius is one repository's tracker.

## Implementation Decisions

- **Projection is derived state** — a pure function of the markdown store, applied incrementally as Ledger events happen and re-appliable in full by Reconcile. GitHub is never read back into control flow; human edits to projected surfaces are drift that Reconcile overwrites. (ADR-0014.)
- **All GitHub calls go through the `gh` CLI from the engine's main thread only** — the parallel-wave invariant ("only the main thread touches the store or the integration branch") extends to the Projection. Serialization doubles as rate discipline; content-creating calls retry once on secondary-limit errors, then drop fail-soft.
- **Auth:** the engine reads GITHUB_TOKEN from the project's git-ignored env file and passes it to `gh` through the subprocess environment; `gh`'s own login is the fallback. Because loops are worktree-resident, no worker's filesystem view ever contains that file. Documented recommendation: a fine-grained token scoped to this one repository (issues, pull requests, contents, commit statuses — read/write), ideally owned by a machine account so mentions, assignments, and review requests actually notify — GitHub suppresses notifications for one's own actions.
- **The mapping.** Spec → parent tracking issue + draft pull request against the recorded base branch, with a closing keyword so the human's merge closes the parent. Issue → sub-issue of the parent. States → `giro:` labels plus native open/closed: done closes as completed, wontfix closes as not planned. blocked_by → native issue dependencies where the API offers them, else a stated line in the sub-issue body. Attempt stories → sub-issue comments mirroring the Issue's log sections verbatim — one writer, one story, two displays. Orchestration (plan, wave, Validate verdict, gap cycle) → parent-issue comments. Integrated Verify and Validate verdicts → commit statuses on the pull-request head. Validate green → draft flipped to ready for review.
- **Identifiers live in frontmatter.** GitHub issue and pull-request numbers are recorded by the engine in Spec and Issue frontmatter — the engine's zone — so the mapping survives clones, resumes, and Reconcile.
- **Idempotency by construction:** find-or-create through recorded identifiers; a hidden marker in every generated comment; a marker-fenced engine zone in the pull-request body, with human prose outside it never touched. Projecting twice changes nothing.
- **Gates fail closed; Projection fails soft — deliberately inverted.** Every call has a timeout; a failure records a Ledger event and the loop continues. Preflight (gh present, token usable, repository reachable, label set ensured) downgrades the Run to projection-off with a loud warning; a strict opt-in makes preflight failures fatal for operators who want fail-closed.
- **Push policy: post-decision checkpoints only** — after activation and plan, after an Issue's final state commit, after Validate or gap commits. Every engine reset happens before its checkpoint, so published history only grows and the engine never force-pushes.
- **Configuration:** Projection is off unless enabled; the repository is autodetected from the origin remote, overridable in config. Nothing worker-facing changes — workers never see the token and never make GitHub calls.
- **Vocabulary and decisions are already recorded upstream** (conversation-owned, per ADR-0010): Projection and Reconcile in the glossary; ADR-0014 for the one-way, fail-soft rule. No worker Issue re-writes them.

## Testing Decisions

The projector's judgment tests without GitHub. Desired-surface computation — which issues, labels, comments, statuses, and pull-request state *should* exist for a given store — is pure: assert it directly against stores in every lifecycle state, including that projecting twice is a no-op. The `gh` boundary follows the FakeDriver pattern: a scripted runner records invocations, letting engine tests assert main-thread-only ordering, checkpoint-only pushes, fail-soft behaviour under injected failures (Run outcome identical with Projection healthy, broken, or off), and preflight downgrade. One live smoke against a scratch repository mirrors the live-driver milestone pattern and stays out of the default test run. Prior art: the FakeDriver loop tests and the parallel-wave suite.

## Out of Scope

- Reading anything back from GitHub into engine behaviour: no webhooks, no comment-driven commands, no answer-sync from pull-request threads. (A future human-triggered pull of answer text would be content, not control — deferred until wanted, with commenter-trust rules of its own.)
- GitHub Apps and the Checks API (app-only); commit statuses carry the verdicts with a plain token.
- Projects boards, milestones, and org-level issue types — labels, sub-issues, and the native progress bar do the tracking.
- Two-way sync of any kind, and trackers other than GitHub.
- Auto-merge — merging is the human's, always (ADR-0005).
