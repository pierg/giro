# giro — guide

How giro works, in more depth than the [README](../README.md). The contract the code is held to is [design.md](design.md); the history is in [roadmap.md](roadmap.md).

## From chat

**Set up once.** Paste this into your coding agent, in the project's git repository:

```text
Install giro here: run `uv tool install giro`, then `giro install`, then read .claude/skills/giro-setup/SKILL.md and follow it.
```

`giro install` copies the five host skills into `.claude/skills/` and links `.agents/skills` to them. The agent then follows `giro-setup`, which writes `giro.toml`.

**Then drive giro from chat.** Its whole surface is a handful of skills your agent invokes for you. You never type the engine's verbs by hand:

```text
you  ▸ "set up giro"
giro ▸ the giro-setup skill inspects the repo and installed drivers, interviews you, writes giro.toml

you  ▸ "capture what we just discussed as a spec"
giro ▸ the spec skill writes docs/specs/my-feature/SPEC.md: plain markdown, no frontmatter

you  ▸ "implement my-feature with giro"
giro ▸ dispatched my-feature-20260811-143002-8814, detached, on branch giro/my-feature
you  ▸ …keep working on your own branch, uncommitted edits and all

you  ▸ "how's it going?"
giro ▸ reads giro status --json and the Run's Ledger: wave 2, issue 03, attempt 1

giro ▸ needs-human: "should deletes cascade?"
you  ▸ answer in chat; the skill edits the Issue body and re-dispatches. Re-invoking is the answer

giro ▸ proof: the work is on branch giro/my-feature
you  ▸ "walk me through what it changed"
giro ▸ shows the diff of giro/my-feature against your base branch
you  ▸ merge it yourself (always your hand)
```

Fuzzy idea, not a feature yet? Open with the **grill** skill. Want to slice the Spec into Issues yourself? The **plan** skill.

Those three, **grill**, **spec** and **plan**, are the **documentation phase**: they write plain Specs, ADRs and Issues and need *no giro install at all* (see [skills/README](../skills/README.md)). Handing that off to the engine is the **implementation phase**, where the giro CLI takes over: `giro implement` (dispatched from chat, or run headless in CI, where **exit codes are the contract**: `0` proof, `2` needs-human, `1` error). No chat host? `giro init` writes a starter `giro.toml` non-interactively: the CI/headless fallback, not the human path.

## Commands

The skills call these for you; a script or CI calls them directly. `giro <verb> --help` lists every flag.

| Command | Does |
|---|---|
| `giro install [--force]` | place the host skills in `.claude/skills/` and link `.agents/skills` |
| `giro init` | write a starter `giro.toml` (the headless fallback to `giro-setup`) |
| `giro doctor` | check config, agent CLIs on PATH (not login), git, specs |
| `giro new spec\|issue\|adr …` | scaffold a conformant Spec, Issue or ADR |
| `giro implement <target> [--detach]` | run the loop for a Spec or Issue; exit `0` proof, `2` needs-human, `1` error |
| `giro status [--json]` | every Spec and Issue state, and any live Run |
| `giro runs [--json]` | live and recent Runs: liveness, phase, outcome |
| `giro logs [<run-id>] [-f] [--json]` | a Run's story from its Ledger |
| `giro verify` | run the `[verify]` gates once on the working tree |
| `giro prompts [<name>]` | list the engine prompts, or print one as it will be injected |
| `giro project` | reconcile GitHub with the markdown store (experimental) |


## The thesis

Most agent frameworks describe what an agent *should* do and hope it complies. giro inverts that:

> **Control never crosses an LLM. Content always does.**

The `giro` CLI is a deterministic loop engine. It owns every arrow — state transitions, budgets, gate execution, wave scheduling, escalation. LLM contexts are spawned *inside* the loop to do the creative work (implement, plan, judge), and each one ends by emitting a JSON envelope. A budget enforced by a `while` loop cannot be talked out of; an LLM "following instructions" can.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/shape-dark.svg">
  <img alt="Your chat session (planning skills and a thin /giro doorway) and your terminal both drive the giro CLI — the loop engine — which spawns worker and judge contexts through drivers, each returning a JSON envelope. Escalations flow back to the conversation; state lives in local markdown beside the code." src="figures/shape-light.svg">
</picture>

Planning is a conversation; building is the machine. Chat is optional — the same command runs headless in CI — and doubles as the **escalation console** where `needs-human` gets answered.

## One primitive, two levels

An **actor** makes an attempt. An independent **verifier** — a set of gates — judges it. All green: the loop exits with proof. Otherwise the findings feed the next attempt, until the **budget** runs out and the loop escalates to `needs-human`. It cannot just stop.

| Loop | Actor | Verifier | On fail |
|---|---|---|---|
| **Issue loop** | a fresh worker context | `[verify]` gates | retry, findings carried forward |
| **Spec loop** | a wave of Issue loops | `[validate]` gates | findings become gap Issues → next wave |

There is no third loop. Validate *is* the Spec loop's verifier; gap Issues *are* its feedback. The "lifecycle" is one feedback edge, not a controller.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/loops-dark.svg">
  <img alt="The Spec loop: a wave of Issue loops runs in isolated workspaces, integrates onto one branch with a re-verify, then Validate judges the whole Spec — pass ends the loop for the human merge; failure files gap Issues into the next wave." src="figures/loops-light.svg">
</picture>

At `concurrency > 1` the wave runs workers **in parallel, each in an isolated git worktree**, merged back one at a time with an integrated re-verify — the shared-checkout corruption that makes parallel agents dangerous elsewhere simply cannot happen.

## One verb

```bash
giro implement <target>
```

The router reads the target and picks the level. An Issue runs the Issue loop. A Spec runs the Spec loop — planned into Issues first if it has none. No second entrypoint, no mode flags. You don't type this verb — the **`giro`** skill dispatches it and relays the outcome (CI calls it directly). When a Run ends `needs-human`, you answer in chat and the skill re-invokes: re-invoking *is* the answer, and the budget resets.

Merging `giro/<spec>` into your main line stays your hand, always.

## Your checkout is never touched

A **Run** — one execution of the engine for one target — happens in the engine's own workshop, never in your working copy:

```bash
giro implement my-feature --detach   # returns at once with a Run id
giro status --json                   # every Spec, every Issue, and any live Run
giro runs                            # liveness, phase, outcome
giro logs <run-id>                   # that Run's story, as it happens
```

- **A worktree per Spec.** The loop runs in `.giro/worktrees/<slug>/`, holding `giro/<slug>`, forked at first activation from a **base branch** recorded in the Spec's frontmatter — the branch its pull request will target. Your checkout is never switched, never required clean, never written; keep editing, on any branch, with dirty files.
- **Dispatch and walk away.** `--detach` hands the terminal back immediately and the Run outlives the shell — or the chat session — that started it. Foreground stays the default, with the same exit codes for CI.
- **The Ledger.** Every Run records its liveness, an append-only event stream (activation, plan, wave, claim, attempt with gate verdicts and findings, merge, gap cycle, escalation, exit), and its outcome. It is disposable observability — markdown is still the memory, and deleting a finished Run's Ledger loses nothing durable.
- **One Run per Spec, one cap per checkout.** A second dispatch of the same Spec is refused loudly rather than queued (a dead Run's lock is reclaimed automatically); independent Specs run concurrently under one ceiling on worker contexts (`max_workers`), shared by every Run dispatched from this checkout.
- **Gates are hermetic.** Because every loop runs on committed content in a fresh worktree, no git-ignored local file — a `.env`, a build artifact — ever reaches a worker or a judge. Whatever a gate needs is committed, or the gate builds it itself.

## Gates

A gate is anything that ends in `{ "verdict": "pass" | "fail", "findings": [...] }`.

- **command** — a shell command; exit 0 is pass. Deterministic and cheap.
- **judge** — a fresh, blind judge context applies a criterion — either an inline `rubric` in `giro.toml` or a `criterion = "<name>"` resolving to `prompts/<name>.md` — and returns the verdict envelope.

Judges start fresh on every attempt and are never handed the worker's reply; findings feed the next *actor*. A judge does see the Issue body, so the attempt log appended by earlier Runs reaches it (within one Run the log is written after the cycle, so a later attempt's judge does not see earlier attempts). Judges run in the Spec's worktree and can read the repository; the conformance criterion reads `CONTEXT.md` and the ADRs. A judge that can't produce the envelope gets one retry, then **fails closed**. All gates run to completion — the next attempt sees every finding, not just the first.

## State

Specs and Issues are markdown with tiny frontmatter, committed beside the code:

```
docs/specs/<slug>/SPEC.md            state: draft | active | done | needs-human
docs/specs/<slug>/issues/NN-slug.md  state: ready | in-progress | done | needs-human | wontfix
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/states-dark.svg">
  <img alt="Issue states: blocked, then ready, then in-progress; Verify pass goes to done; a fail with budget left returns to ready with its findings; a spent budget or a worker escalation goes to needs-human, and re-running giro implement returns it to ready. Spec states: draft, then active, then done when Validate is green; active goes to needs-human when an Issue is stuck or the validate budget is spent, and re-running giro implement returns it to active." src="figures/states-light.svg">
</picture>

The store is the durable checkpoint: kill the engine at any point and `giro implement` resumes from exactly where the markdown says. Every attempt appends its story to the Issue file — the history is readable, not buried in a log.

Conversation and engine meet on the *same file* without colliding, because they never write the same bytes — **one file, two zones**. The authoring skills (or `giro new`) write only the body — no LLM ever types a `state:` line; the engine owns the frontmatter zone, stamping state when a Spec activates and appending the log:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/zones-dark.svg">
  <img alt="One file, two zones: the conversation scaffolds via giro new and writes only the body; the engine writes frontmatter state and appends attempt logs. giro implement passes the baton to the engine; needs-human passes it back." src="figures/zones-light.svg">
</picture>

## Prompts and skills

giro bundles its LLM touchpoints as readable, editable Markdown, split into **two directories by who consumes them** ([ADR-0015](adr/0015-prompts-and-skills-live-apart-cleanly.md)) — so nothing engine-internal ever masquerades as a chat command.

**Skills** (`skills/`) — host-discoverable `SKILL.md` operator skills a person invokes in chat. `giro install` places these into `.claude/skills/`. They come in two kinds: *engine skills* that drive the giro CLI (the implementation phase) and *standalone authoring skills* that need no giro at all (the documentation phase) — see [skills/README](../skills/README.md).

Engine skills (`giro`-prefixed — they require the CLI):

- **`giro`** — the dispatch console: start detached Runs, answer "what is it doing?" from status and the Ledger, work escalations — all without leaving the branch you are on.
- **`giro-setup`** — configure a project for giro: inspect the repo and installed drivers, interview for preferences, write `giro.toml`. The human path in; `giro init` is the non-interactive fallback.

Authoring skills (unprefixed — usable with or without giro; they write plain markdown the engine later ingests):

- **`spec`** — turn a conversation into a Spec: `docs/specs/<slug>/SPEC.md`, body only, no frontmatter.
- **`plan`** — decompose a Spec into tracer-bullet Issues with blocking edges; the same slicing judgment is *also* mirrored in the engine's own `prompts/planner.md`, so hand-planned and machine-planned decomposition share one discipline.
- **`grill`** — the relentless upstream interview: sharpen fuzzy intent before it becomes a Spec, capturing vocabulary into `CONTEXT.md` and load-bearing decisions into `docs/adr/` as they crystallise. Docs change in conversation; whatever builds from them obeys.

**Prompts** (`prompts/`) — plain markdown the engine injects into a spawned context. No YAML frontmatter, nothing invokable from a host. The engine ships four:

- **`worker.md`** — worker discipline, injected into every worker packet.
- **`planner.md`** — the planner's slicing judgment (a distillation of the standalone `plan` skill).
- **`review.md`** — default judged verify gate: issue-fit without scope creep, code quality in context, honest tests.
- **`conformance.md`** — holds every diff to `CONTEXT.md` vocabulary and ADR decisions; a suspected stale ADR fails with a *human-decision* finding that routes to the escalation console — a worker can never pass a gate by rewriting what it checks against.

A judged gate in `giro.toml` references one of these by name (`type = "judge", criterion = "review"` → `prompts/review.md`) or carries an inline `rubric` instead. The engine works with zero setup (bundled defaults); every file is overridable by dropping your own copy in the matching project directory (`prompts/<name>.md` or `skills/<name>/SKILL.md`).

## Drivers

A driver is how giro starts one agent CLI: prompt in, one JSON envelope out. The roster in `giro.toml` picks a driver per role (`worker`, `judge`, `planner`). Built in (`src/giro/drivers.py`):

- **`claude`** — Claude Code (`claude -p`).
- **`agy`** — Antigravity, Google's agent CLI, driven through its `stream-json` mode.
- **`codex`** — OpenAI Codex CLI (`codex exec`).
- **`gemini`** — Gemini CLI (`gemini -p`).

`RecipeDriver` runs any CLI that follows the same contract from a declarative recipe (command, how the prompt is delivered, a model-flag template, an optional envelope unwrap). The class and its tests exist; selecting a recipe from `giro.toml` is not wired yet ([open-drivers](specs/open-drivers/SPEC.md)).

Cursor is not a driver. It is a chat host: `giro install` links `.agents/skills` to the installed skills, so Cursor and Codex read the same copies Claude Code does.

## Layout

- [`docs/design.md`](design.md) — the founding design: vocabulary, topology, state machines, envelopes, loop contracts, the events schema, decisions, milestones.
- [`CONTEXT.md`](../CONTEXT.md) — the project's canonical vocabulary.
- [`docs/adr/`](adr/) — the load-bearing decisions, one paragraph each.
- [`skills/`](../skills/) — host skills (the engine skills `giro` and `giro-setup`, and the standalone authoring skills `grill`, `spec`, `plan`); [`prompts/`](../prompts/) — engine-injected plain markdown (worker discipline, planner judgment, judged-gate criteria).
- [`src/giro/`](../src/giro/) — the engine. `loops.py` is the heart.

## Running safely

giro runs agent CLIs that **execute arbitrary code on your machine** with whatever permission you grant them in `giro.toml` — the worker roster ships `--dangerously-skip-permissions` so a worker can actually edit files. The engine bounds *iteration* (attempts, gap cycles, timeouts); it does not sandbox *blast radius*. Before pointing giro at anything you care about:

- **Run it in a container, VM, or a throwaway clone** — not your only clone of a repo with secrets. Your checkout's branch and uncommitted edits are safe (the loop runs in the engine's own worktree), but *the machine* is not sandboxed: a worker executes code with the permissions you granted it.
- **Treat Spec and Issue bodies, and gate output, as untrusted input.** They are fed into worker and judge contexts, so a hostile body is a prompt-injection channel. Write your own Specs; review before you run.
- **Keep bypass flags off the judge and planner.** They only read; the default roster grants write to the worker alone, and the role fallback never inherits those flags (see [`giro.toml`](../giro.toml) and `giro init`'s template).
- **Command gates run shell straight from `giro.toml`.** A change that edits `giro.toml` edits what runs on your machine — review config changes like code.
- **Merging `giro/<slug>` into your main line is always yours.** The machine never merges for you.
- **The `gh` CLI is required only when `[github_projection]` is enabled** — the projection is experimental in 0.1.0. Without it, giro has no runtime system dependencies beyond Python 3.11+ and git. On shared machines, rotate any tokens `.env` may hold before walking away.

## Credits

The `spec`, `plan`, `grill`, and `review` touchpoints adapt material from Matt Pocock's [Skills for Real Engineers](https://github.com/mattpocock/skills) (`to-spec`, `to-tickets`, `grilling`, `domain-modeling`, `code-review`), released under MIT — the slicing, spec-writing, interviewing, and review judgment is his; the mechanics are giro's. Notices in [LICENSE](../LICENSE).
