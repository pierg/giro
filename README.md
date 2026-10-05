# giro

**Control never crosses an LLM. Content always does.**

Most agent tooling tells a coding agent what to do and hopes it complies. giro inverts that. A deterministic loop owns every decision: it hands your coding agent one slice of a spec, runs your tests and blind reviewers on the result, feeds their findings back, and ends one of two ways: proof on a branch for you to merge, or a question for you. Never a silent "done". A budget enforced by a `while` loop cannot be talked out of; an instruction can.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/hero-dark.svg">
  <img alt="How giro works. You ask your coding agent in plain words: grill me on this idea, write a spec, plan it into issues, implement it with giro, how is it going. The giro skill dispatches a Run. Inside the engine, a deterministic loop: a fresh worker agent edits files, then the gates judge it, tests where the exit code decides and blind reviewers who are fresh judges. All green exits with proof on branch giro/spec, which you merge. Otherwise the findings go back to the worker while budget is left; when it is spent, the Run exits needs-human with a question back to you in chat, and your answer resumes it." src="docs/figures/hero-light.svg">
</picture>

## Install

You do not type giro's commands. Paste this into your coding agent, in the git repository giro should work on:

```text
Install giro here: run `uv tool install giro`, then `giro install`, then read .claude/skills/giro-setup/SKILL.md and follow it.
```

`giro install` places five skills in `.claude/skills/` (`.agents/skills/` links there for Codex and Cursor). `giro-setup` finds the commands your tests really run and the agent CLIs you have, asks only what it cannot look up, and writes `giro.toml` once you confirm. You need Python 3.11+, uv, git, and one [supported coding agent](#supported-coding-agents) logged in. Read [running safely](https://github.com/pierg/giro/blob/main/docs/guide.md#running-safely) first: the worker gets its CLI's permission-bypass flag.

## What you ask, in plain words

| You say | Skill | What happens |
|---|---|---|
| "Grill me on rate limiting for the public API." | `grill` | Interviews you; writes terms to `CONTEXT.md`, lasting decisions to `docs/adr/`. |
| "Write that up as a spec." | `spec` | Writes `docs/specs/rate-limit/SPEC.md`, plain markdown. |
| "Plan it into issues." | `plan` | Optional: slices the Spec into Issues. Otherwise the engine plans. |
| "Implement rate-limit with giro." | `giro` | Dispatches a detached Run on branch `giro/rate-limit`. You keep working on yours. |
| "How is it going?" | `giro` | Reads the Run's log: wave 2, issue 03, attempt 1, which gate failed. |
| "What does it need from me?" | `giro` | Shows the question the Run stopped on, writes your answer into the Issue, dispatches again. |
| "Walk me through what it changed." | `giro` | Shows the proof's diff against your base branch. Merging it is always your hand. |

## Grill, spec and plan

These three skills are the documentation phase, and they need no giro at all: copy them into any coding agent's skills folder. They start from Matt Pocock's `grill-with-docs`, `to-spec` and `to-tickets` and keep his judgment: the frontier-round interview, the spec template, tracer-bullet slicing. What changes is where the result lives. Everything is a file in your repository, beside the code it describes, and the loop treats those files as rules a worker cannot rewrite.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/grill-spec-plan-dark.svg">
  <img alt="What grill, spec and plan write, and how the files relate. You say: grill me on rate limiting; the grill skill writes CONTEXT.md, the glossary, for example Limit: the requests a key may make in one window, avoid quota and throttle, and an ADR, docs/adr/0007-token-bucket.md: token bucket, not a fixed window. Both outlive every spec. You say: write that up as a spec; the spec skill writes the folder docs/specs/rate-limit/ with SPEC.md, whose sections are problem statement, solution, user stories, shape, implementation decisions, testing decisions, docs impact and out of scope. The shape is a diagram drawn with any tool, here shape.svg in the same folder; each contract gets an inline example such as limit 100, window 60s. SPEC.md uses the glossary's words and respects the ADRs. You say: plan it into issues; the plan skill writes issues/ in the same folder, slices of SPEC.md: 01-key-lookup, then 02-one-route and 04-per-key, both blocked by 01, then 03-headers blocked by 02. Every issue ends with a Not in this Issue fence. Then giro builds from these files: each worker is given CONTEXT.md, the spec and one issue, and any edit it makes to them is reverted. Without giro they are plain markdown any agent reads." src="docs/figures/grill-spec-plan-light.svg">
</picture>

| Skill | Writes | Adapted from | What is different |
|---|---|---|---|
| `grill` | `CONTEXT.md`, `docs/adr/NNNN-*.md` | `grill-with-docs` | One skill instead of two. The glossary and the decisions it writes become binding: every worker is given `CONTEXT.md`, and any worker edit to either is reverted. |
| `spec` | `docs/specs/<slug>/SPEC.md` | `to-spec` | A folder in the repo, not a tracker issue. Adds a diagram of the moving parts (in any format, kept in the folder), an example for every contract, and a Docs impact section naming the docs and figures the change makes stale. |
| `plan` | `docs/specs/<slug>/issues/NN-*.md` | `to-tickets` | Issues live in the Spec's folder, with `blocked_by` in frontmatter that the engine schedules from. Each one ends with "Not in this Issue", the scope fence the reviewers check. |

They are plain markdown, so adjust them to how you work: `giro install` keeps an edited copy as yours and replaces it only with `--force`. The side-by-side, section by section, is in [skills/README](https://github.com/pierg/giro/blob/main/skills/README.md#how-they-differ-from-the-originals).

## The idea

**The agent does the work. Structure it can't argue with decides what counts. A human holds the exits.**

A Spec is markdown describing one feature. The engine splits it into Issues (small slices) and for each starts a fresh agent, the worker, to edit files. giro commits the change and runs your gates: command gates (the exit code decides) and judge gates, where a blind LLM, given the Spec, the Issue (with its log from earlier Runs), the diff and a criterion, gives a verdict. A `while` loop counts budgets; no prompt talks it round.

A Run that completes ends one of two ways. **Proof**: all gates green, the work waits on branch `giro/<spec>` for your merge. Or **`needs-human`**: budget spent or a worker question. The reason is written into the Issue in `.giro/worktrees/<spec>/`, where the `giro` skill reads it to you. After an error, ask again; the Run resumes from the markdown. Runs see only committed code (the Spec is copied as-is): commit what gates need.

## What is enforced by code, and what only by instruction

| Rule | Enforced by | Where |
|---|---|---|
| Attempts per Issue and validate gap cycles per Spec are capped | code | `src/giro/loops.py` `_attempt_cycle`, `run_spec_loop` |
| Worker edits to `docs/specs/`, `docs/adr/`, `prompts/`, `gates/`, `CONTEXT.md`, `giro.toml` are reverted on every outcome, including ones the worker committed or hid with an ignore rule (not via git index flags; see the `.git` row) | code | `src/giro/loops.py` `_guardrail_hits`, `_attempt_cycle` |
| Only the engine commits: worker commits are unwound and re-judged (not its branches, tags or pushes) | code | `src/giro/loops.py` `_attempt_cycle` |
| Judges are fresh and never see the current attempt's reply; the Issue's log from earlier Runs reaches them. No valid verdict after one retry: fail | code | `src/giro/gates.py` `run_judged_gate`, `src/giro/loops.py` `_material` |
| Your branch, index and uncommitted edits are never touched | code | `src/giro/workspace.py` `ensure_spec_worktree` |
| One Run per Spec; a per-checkout worker cap | code | `src/giro/runs.py` `spec_lock`, `worker_slot` |
| giro never merges into your base branch | code: its only merge targets `giro/<spec>` | `src/giro/loops.py` `_integrate_branch` |
| The worker stays in the Issue's scope and never weakens a test | instruction, LLM-judged | `src/giro/loops.py` `WORKER_PREAMBLE`, `prompts/review.md` |
| Changes follow `CONTEXT.md` and ADRs (conformance gate, off in the starter config) | instruction, LLM-judged | `prompts/conformance.md` |
| Judges and planners don't edit files | config only: no bypass flag in the starter roster or the role fallback | `src/giro/config.py` `Config.roster_for`, `INIT_TEMPLATE` |
| The worker doesn't touch `.git` (index flags included) or files outside the repo | instruction only; no sandbox | `src/giro/loops.py` `WORKER_PREAMBLE` |

## Supported coding agents

giro starts a coding agent through a driver: prompt in, edits in the working tree, one JSON reply out. It ships drivers for Claude Code (`claude`, the default), Codex (`codex`), Gemini CLI (`gemini`) and Antigravity (`agy`). `giro.toml` picks one per role, so the worker, the reviewers and the planner can each use a different agent.

Any other agent that runs headless from the command line can be added. A driver is a short class in [`src/giro/drivers.py`](https://github.com/pierg/giro/blob/main/src/giro/drivers.py): it builds the command line and, if needed, unwraps the reply; register it in `DRIVER_REGISTRY` and name it in `giro.toml`. Drivers defined in `giro.toml` alone, with no code, are specified in [open-drivers](https://github.com/pierg/giro/blob/main/docs/specs/open-drivers/SPEC.md) and not built yet. Cursor and other chat hosts need no driver: they read the same skills.

## Reference

In CI, call the command the skills call: `giro implement <spec>` exits `0` on proof, `2` on needs-human, `1` on error; `giro init` writes a starter `giro.toml` with no interview. Every command, the loops, gates, state and drivers: the [guide](https://github.com/pierg/giro/blob/main/docs/guide.md). The contract: [design](https://github.com/pierg/giro/blob/main/docs/design.md).

## Where it is used

giro is built with giro. Four of the Specs under [`docs/specs/`](https://github.com/pierg/giro/tree/main/docs/specs) (`detached-runs`, `github-projection`, `setup-skill`, `strip-skill-frontmatter`) were run to `done` by the engine, and each Issue file keeps its attempts and verdicts; the others were not. Two live runs elsewhere ([first light](https://github.com/pierg/giro/blob/main/docs/notes/m1-first-light.md), [parallel waves](https://github.com/pierg/giro/blob/main/docs/notes/m2-parallel-waves.md)) are on toy repos. This repository's public history begins at 0.1.0.

## Status and limits

- Pre-alpha 0.1.0, POSIX, MIT. The GitHub projection is experimental.
- The engine bounds iteration, not blast radius: use a container, VM or throwaway clone.
- Judge gates are opinions; put real tests in command gates.

## Credits

The `grill`, `spec` and `plan` skills, and the review gate's prompt, adapt Matt Pocock's [Skills for Real Engineers](https://github.com/mattpocock/skills) (MIT): the interviewing, spec-writing, slicing and review judgment is his; the loop around them is giro's. The notice is in [LICENSE](LICENSE).

---

Part of a set: [folio](https://github.com/pierg/folio) · [lab-kit](https://github.com/pierg/lab-kit) · [giro](https://github.com/pierg/giro)
