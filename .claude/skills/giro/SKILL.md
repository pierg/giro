---
name: giro
description: Drive giro from a chat session — dispatch detached Runs, report their progress, and help the human answer needs-human escalations. Use when the user asks to implement a Spec or Issue with giro, check giro status or a Run's progress, or resolve a giro escalation.
---

# giro — the dispatch console

giro is a deterministic loop engine: it turns Specs and Issues under `docs/specs/` into verified work on a `giro/<slug>` branch, exiting only through proof or `needs-human`. You do not implement anything yourself — the engine spawns fresh worker contexts for that. Your job is the console around the machine: dispatch Runs, report on them, and walk escalations.

Every loop runs in the engine's own worktree at `.giro/worktrees/<slug>/`, on the `giro/<slug>` branch, forked from the base branch recorded in the Spec's frontmatter. **The human's checkout is never touched** — not switched, not required clean, not written. They keep editing on their own branch, uncommitted work and all, while Runs proceed.

## Setup (first time in a project)

```bash
uv tool install giro
giro install           # places the host skills (giro, giro-setup, spec, plan, grill) into .claude/skills
```

Then, in chat: "set up giro" invokes the **`giro-setup`** skill, which inspects the repo and installed drivers, interviews for preferences, and writes `giro.toml`. `giro init` is the non-interactive fallback for headless/CI bootstraps — don't tell a chat user to run it.

The engine's own prompts (`prompts/worker.md`, `prompts/planner.md`, and the judged-gate criteria `prompts/review.md` and `prompts/conformance.md`) ship inside the wheel as plain markdown and are never installed into chat discovery — they are injected by the engine, not invoked by anyone.

## Commands

```bash
giro implement <target> --detach   # dispatch: returns at once with a Run id; the Run outlives this session
giro status --json                 # every Spec and Issue, plus any live Run — read this, don't guess
giro runs --json                   # live and recent Runs: liveness, phase, outcome
giro logs <run-id>                 # that Run's event stream from its Ledger (add -f to tail)
giro new spec|issue|adr …          # scaffold conformant artifacts — the only way they are born
giro verify                        # run the [verify] gate set once, on demand
```

Exit codes: `0` proof, `2` needs-human, `1` error. `giro status` uses them too — `2` while anything waits on a human.

`giro logs` prints what has happened and returns; add `-f`/`--follow` only when a human at a terminal wants to tail the Run — never from chat, where following would block your turn.

`giro logs --json` is the machine tail: one JSON object per line from `events.jsonl`, in `seq` order, with no human preamble — the same `-n` and `--follow` semantics apply. Use it when a script or a projection needs to consume the Run's raw event stream; the primary contract for a person remains `giro status` and the final report.

## How to behave

1. **Dispatching.** If there is no `giro.toml`, the project is not set up: follow the `giro-setup` skill first. Resolve what the user wants into one target (`giro status --json` helps), then run `giro implement <target> --detach`. It returns immediately. Relay the **Run id** and the branch the work lands on (`giro/<slug>`), say the Run survives this session, and **hand the conversation back** — never block on the engine, never poll in a loop, never wait for the Run to end before answering the human. They can keep working; so can you.

   Dispatch a second Spec while the first runs — independent Specs proceed in parallel under one worker cap for this checkout. Two Runs on *one* Spec is refused loudly: one Run per Spec branch.

   Foreground (`giro implement <target>`, no `--detach`) is for CI and for a human at a terminal who wants to watch. From chat, dispatch.

2. **Progress questions.** "What is it doing?" is answered by *reading*, **never by re-running a loop**: `giro status --json` for Spec and Issue states plus the live Run overlay, `giro runs --json` for liveness and outcome, `giro logs <run-id>` for the Ledger's event stream — activation, plan, wave, claim, attempt (with gate verdicts and findings), merge, gap cycle, escalation, exit. Summarize the story; don't paste the JSON.

   The Ledger is disposable observability — markdown is the memory. A missing Ledger costs you liveness and nothing else; the states in `giro status` are still true.

3. **On `needs-human`.** This is the console's main job, and it works from whatever branch the human is on. `giro status` marks the escalation with ⚠ and prints where the Spec lives: `.giro/worktrees/<slug>/`. Read the Issue there — `.giro/worktrees/<slug>/docs/specs/<slug>/issues/NN-slug.md` — including its trailing `## Attempt N` / `## Budget exhausted` sections, and present the blocker plainly. Discuss it with the user. Their answer lands as an edit to the **Issue body in that worktree** (sharper acceptance criteria, the decision made explicit) — that copy is the truth, not the one in their checkout. Then dispatch the same target again: **re-invoking is the answer**; the engine commits the answer, resets the budget, and carries on.

4. **On proof.** The Run's Ledger ends `done`/`all-done`. Tell the user what is on `giro/<slug>`, and that merging it into their main line is theirs to make. When they ask what changed, walk them through `git log` and `git diff <base>...giro/<slug>` (the base branch is in the Spec's frontmatter) and the Issues' attempt logs. Never merge it yourself, even when asked: say how they can (a local merge or a pull request) and leave it to them.

5. **Planning.** If there is no Spec yet, the standalone authoring skills are the path — they need no giro and write plain artifacts the engine ingests: the `grill` skill when intent is still fuzzy (it also writes `CONTEXT.md` and ADRs), then the `spec` skill to turn the settled conversation into a Spec, then optionally the `plan` skill to slice it by hand. A Spec with no Issues is fine — the engine plans it. A Spec authored this way carries no `state:` line; the engine stamps it on first activation.

## Useful facts when the user asks what is happening

- Workers never see the human's uncommitted or git-ignored files: every loop runs on committed content in the engine's worktree. **Gates are hermetic** — whatever a gate needs is committed, or the gate builds it itself. A gate that depends on a local `.env` fails there, and that is the contract, not a bug.
- At `concurrency > 1` a wave's workers run in their own worktrees on ephemeral `giro-wt/<slug>/<issue>` branches, merged back one at a time with a re-verify; those are gone by the time the Run exits.
- A Run that dies leaves a stale lock; the next dispatch reclaims it and says so. Nothing needs unlocking by hand.
- Everything durable is in the Spec and Issue markdown on `giro/<slug>`.
- `giro status` also warns when installed `.claude/skills` copies drift from this giro's bundled skills. Stale or missing copies: `giro install` / `--force`. A deliberately edited copy is an override by design — never restore one without asking.

## Hard rules

- Never edit Issue/Spec `state:` frontmatter or attempt logs — state belongs to the engine; you edit *bodies* when the user answers an escalation.
- Never bypass the engine by implementing Issues in this chat context.
- Never merge `giro/<slug>` into the main branch.
- Never block the conversation on a Run: dispatch, report, and let the human keep going.
