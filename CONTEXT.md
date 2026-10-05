# giro

A guarded-loop engine for AI coding agents: Specs become integrated, verified work through bounded loops that end in proof or escalation, never a silent stop.

## Language

**Spec**:
The forward-looking intent for one shippable feature, at `docs/specs/<slug>/SPEC.md`.
_Avoid_: PRD, epic, roadmap

**Issue**:
One bounded, demoable slice of a Spec — the unit a worker context implements.
_Avoid_: ticket, card

**Gate**:
A verification unit ending in a verdict envelope; kinds are `command` (exit code decides) and `judge` (a blind LLM judges against a criterion or inline rubric).

**Verify**:
The Issue-level gate set — "built right, per the rules?" — run after every worker attempt.

**Validate**:
The Spec-level gate set — "built the right thing?" — the Spec loop's verifier; its findings become gap Issues.
_Avoid_: QA, sign-off

**Envelope**:
The single JSON object every spawned context ends with; malformed envelopes fail closed.
_Avoid_: payload

**Finding**:
One structured problem from a gate or a failed attempt; findings feed the next actor, never the next judge.

**Worker**:
A fresh context that implements one Issue. It edits files; it never commits and never touches engine-owned markdown. The roster role is `worker` (the legacy name `implementer` is accepted with a deprecation notice).
_Avoid_: subagent, coder, implementer

**Judge**:
A fresh, blind context that applies one judged gate against its criterion or inline rubric and emits a verdict envelope.

**Planner**:
A fresh context that decomposes a Spec into Issues, returning a plan the engine materializes as files.

**Driver**:
An open, declarative recipe for spawning one agent CLI — its command, how the prompt is delivered (`stdin` | `positional-dash` | `file`), the model-flag template, and an optional envelope unwrap. Not a fixed set: `claude`, `agy`, `codex`, `gemini` ship as built-in presets, and any CLI that honours the worker contract (prompt in, JSON envelope out) can be defined as another.
_Avoid_: backend, adapter, plugin

**Profile**:
A named bundle of roster, runner, and budget overrides in `giro.toml` (`[profiles.<name>]`), selected per-Spec or at dispatch — the "who and how" for one Run, layered last-wins over the project defaults.
_Avoid_: mode

**Driver registry**:
The resolved set of drivers a roster name binds against for a Run — built-in presets, then the user/machine-level registry, then project `[drivers.*]`, last-wins. Preflight fails loud on a name it cannot resolve in the current environment.
_Avoid_: catalog, plugin list

**Skill**:
A host-discoverable `SKILL.md` a person invokes in a chat session, in `skills/`. Two kinds: **engine skills** (`giro`-prefixed, require the CLI) — `giro` (dispatch console) and `giro-setup` (configuration); and **authoring skills** (unprefixed, standalone, no giro required) — `spec`, `plan`, `grill`, which write plain Specs, Issues, and ADRs the engine later ingests. Distinct from a **Prompt** (the engine's own, in `prompts/`) and a **Criterion** (a Prompt named by a judged gate). `giro install` places both kinds; the authoring skills also install and run on their own.
_Avoid_: calling an engine prompt or a gate criterion a "skill"

**Authoring skill**:
One of `spec`, `plan`, `grill` — the documentation-phase skills, cut loose from the giro CLI. They produce plain artifacts (no lifecycle frontmatter) and never depend on the engine; the engine is one possible downstream consumer.
_Avoid_: planning skill, upstream skill

**Prompt**:
Plain markdown the engine injects into a spawned context — `prompts/worker.md`, `prompts/planner.md`, and the judged-gate criteria `prompts/review.md` / `prompts/conformance.md`. Not host-discoverable; nobody invokes it.
_Avoid_: template, preamble

**Criterion**:
A Prompt referenced by a judged gate (`type = "judge", criterion = "review"` resolves to `prompts/review.md`). A judge gate may carry an inline `rubric` instead; specifying both is refused. A criterion is a Prompt, never a Skill.
_Avoid_: rubric skill, skill gate

**Budget**:
The bound that turns "loop until green" into "loop until green or ask" — attempt and gap-cycle ceilings in `giro.toml`.

**Wave**:
One frontier of unblocked Issues run together; at `concurrency > 1`, in parallel worktrees, integrated one at a time.
_Avoid_: sprint

**Zone**:
The ownership split inside one Spec or Issue file: engine-owned frontmatter and appended logs, conversation-owned body.

**Escalation**:
The only non-proof exit — `needs-human`, with a durable reason; re-invoking `giro implement` is the answer.
_Avoid_: failure, timeout

**Run**:
One execution of the engine for one target, foreground or dispatched; it ends at proof or escalation.
_Avoid_: job, session

**Dispatch**:
Starting a detached Run — the terminal returns at once, and the Run outlives the session that started it.
_Avoid_: spawn, launch

**Ledger**:
The disposable per-Run record: liveness, event stream, outcome. Observability only — the markdown store remains the memory.
_Avoid_: registry, journal

**Base branch**:
The branch a Spec's work forks from and its pull request targets, recorded in frontmatter at first activation.
_Avoid_: default branch

**Projection**:
The one-way rendering of store state onto GitHub — tracking issue, sub-issues, labels, comments, pull request, statuses. Display, never memory, never control.
_Avoid_: sync, mirror, two-way integration

**Reconcile**:
Recomputing the desired GitHub surface from the markdown store and converging GitHub to it — idempotent, safe at any point.
_Avoid_: backfill, replay

**Skill drift**:
The gap between an installed `.claude/skills` copy and the bundled skill it came from — `stale`, `missing`, and `unknown` warn in `giro status`; a `customized` copy is an override by design.
_Avoid_: outdated skills
