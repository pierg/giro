---
state: done
base: 8745d04d0d8deeccd21f18b3a0b93c0b3b913e78
---
# Detached Runs

## Problem Statement

Starting a loop takes the human's workspace hostage. `giro implement` switches the invoking checkout onto the Spec branch, refuses to start unless that checkout is clean, and blocks the terminal until proof or escalation. While the machine runs its laps, the human cannot edit on their own branch, cannot dispatch a second Spec, and cannot even see fresh progress — state commits land on the Spec branch, so `giro status` from anywhere else reads yesterday's frontmatter. Planning and building were meant to overlap; today they cannot.

## Solution

The engine gets its own workshop. Every loop runs in a persistent per-Spec worktree on the `giro/<slug>` branch, forked from an explicit base branch — the invoking checkout is never switched, never required clean, never touched at all. A Run can be dispatched detached: the terminal returns immediately, the loop survives the session that started it, and every Run — foreground or detached — writes a Ledger of liveness, events, and outcome. `giro status` tells the truth from any branch by reading Spec-branch tips, several Specs run concurrently under one global worker cap, and the `giro` skill turns from a blocking call into a dispatch console: start Runs, keep working, ask for progress, answer escalations — all without leaving the branch the human is on.

## User Stories

1. As the human, I want to dispatch a Spec and keep editing on my own branch with uncommitted work, so that the machine's laps never interrupt mine.
2. As the human, I want `giro status` to report every Spec's live state no matter which branch I have checked out, so that progress is visible without switching.
3. As the human, I want to dispatch a second Spec while the first is still running, so that independent features proceed in parallel.
4. As the human, I want a detached Run to survive closing the terminal or chat session that started it, so that long loops finish without babysitting.
5. As the human, I want to follow a live Run's story and read a finished Run's outcome, so that "what is the machine doing?" always has an answer.
6. As the human, I want to answer a needs-human and re-dispatch without leaving my branch, so that the escalation console works from anywhere.
7. As the human, I want double-dispatching a Spec to fail loudly, so that two engines never fight over one branch.
8. As the human, I want one global ceiling on concurrent worker contexts across all Runs, so that dispatching freely cannot melt my machine or my budget.
9. As a CI job, I want the exit-code contract and the resulting Spec branch unchanged, so that headless pipelines keep working untouched.

## Implementation Decisions

- **One persistent worktree per Spec**, under a git-ignored engine-owned directory at the project root, holding the `giro/<slug>` branch. Created at first activation, reused by every later Run, never the invoking checkout. The whole loop lives there: activation, planning, waves, merges, state commits. The clean-tree requirement applies to the worktree, not the checkout.
- **The base branch is explicit and recorded.** Resolved once at first activation — config value if set, else the invoking checkout's current branch at that moment — then written into Spec frontmatter by the engine, so every later Run and the eventual pull request agree. Never re-inferred.
- **A Spec is born in the checkout but lives on the branch.** At first activation the engine seeds the fresh worktree with the Spec's directory from the invoking checkout and commits it; from then on the branch copy is the only truth, and status and target resolution prefer the branch tip wherever the branch exists. Draft Specs without a branch are still read from the checkout.
- **Both concurrency shapes run in worktrees now**, closing the documented isolation asymmetry: no worker ever sees git-ignored local files. Gates must be hermetic — committed content only — and the docs say so.
- **Dispatch is the engine's own daemonization** (`--detach`): the Run outlives the invoking shell and session. No resident scheduler exists — each Run is one process that exits at proof or escalation. Foreground stays the default, with today's exit-code contract (0 proof, 2 needs-human, 1 error).
- **The Ledger** records each Run: liveness, an append-only event stream — one line per state-changing moment (activation, plan, wave start, claim, attempt end with verdicts and findings, merge, gap cycle, escalation, exit) — and the final outcome. It is disposable observability: markdown remains the memory, and deleting a finished Run's Ledger loses nothing durable. The event stream is the seam the GitHub projection will consume; its shape is settled here.
- **One lock per Spec** for the life of a Run, with stale-lock reclaim when the holding process is dead. Double-dispatch fails loudly; it never queues.
- **One global cap on concurrent worker contexts** across all Runs, enforced by the engine; a Run waits for a slot rather than exceeding it.
- **Status grows a machine face.** `giro status` reads branch tips, overlays Ledger liveness, and gains machine-readable output for skills and CI; new porcelain lists Runs and follows a live one's story.
- **The `giro` skill's contract changes** from run-and-block to dispatch-and-console.
- **Vocabulary and decisions are already recorded upstream** (conversation-owned, per ADR-0010): Run, Dispatch, Ledger, and Base branch in the glossary; ADR-0013 for the sacred checkout. No worker Issue re-writes them.

## Testing Decisions

External behaviour through the existing seam — the engine with a FakeDriver against real temporary git repositories, as the loop and parallel tests already do. What matters: the invoking checkout's branch, HEAD, index, and dirty files are byte-identical before, during, and after a Run; status reports branch-tip truth from an unrelated checkout; a second dispatch fails loudly while a lock is held and reclaims a stale lock from a dead process; a killed Run resumes from markdown exactly as today; the Ledger's events and outcome match the Run's report, and deleting it breaks nothing; the global cap holds under two concurrent Runs; foreground exit codes are unchanged. Daemonization itself is exercised at the process level: dispatch, outlive the parent shell, read the outcome from the Ledger.

## Out of Scope

- Anything GitHub — pushing, pull requests, issues, comments. That is the github-projection Spec, which consumes the event seam settled here.
- A resident scheduler or daemon that owns Runs.
- Cross-Spec conflict resolution: two Spec branches touching the same code still collide at human merge time; slicing Specs to disjoint areas remains planning judgment.
- Copying git-ignored local files into worktrees for gates that want them — gates are hermetic, full stop.
- Multi-repo orchestration.
