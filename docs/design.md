# giro — founding design

This is the contract the engine is built against. The prose vision lives in the [README](../README.md); this document is the part you can hold the code to.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/primitive-dark.svg">
  <img alt="The guarded loop: an actor makes an attempt, a verifier judges it; all green exits with proof, findings feed the retry, and a spent budget escalates to needs-human." src="figures/primitive-light.svg">
</picture>

## Vocabulary

- **Spec** — forward-looking intent for one shippable feature. `docs/specs/<slug>/SPEC.md`.
- **Issue** — one bounded, demoable slice of a Spec. `docs/specs/<slug>/issues/NN-slug.md`.
- **Gate** — a verification unit ending in a verdict envelope. Kinds: `command` (exit code decides) and `judge` (a fresh blind LLM context decides against a criterion).
- **Prompt** — engine-injected plain markdown in `prompts/`: `worker.md` (worker discipline), `planner.md` (slicing judgment), and the judged-gate criteria `review.md` and `conformance.md`. No frontmatter, nothing invokable from a host.
- **Skill** — a host-discoverable `SKILL.md` a person invokes in a chat session, in `skills/`. Engine skills are giro-prefixed and need the CLI: `giro` (dispatch console), `giro-setup` (configure). Authoring skills are unprefixed and standalone: `grill`, `spec`, `plan`. `giro install` copies these into `.claude/skills/`.
- **Criterion** — a prompt named by a judged gate: `criterion = "review"` resolves to `prompts/review.md`. A judge gate may carry an inline `rubric` instead of a named criterion; specifying both is refused.
- **Verify** — the Issue-level gate set (`[verify]` in giro.toml).
- **Validate** — the Spec-level gate set (`[validate]`). It is the Spec loop's verifier, not a separate stage.
- **Worker** — a fresh LLM context that implements one Issue. Edits files; never commits. The roster role is `worker` (the legacy name `implementer` is accepted with a deprecation notice).
- **Judge** — a fresh, blind LLM context that applies one judged gate against its criterion or rubric.
- **Planner** — a fresh LLM context that decomposes a Spec into Issues, injected with `prompts/planner.md`'s slicing judgment. Returns a plan; the engine writes the files.
- **Driver** — how a context is spawned: `claude` (Claude Code), `agy` (Google's Antigravity CLI), `codex`, `gemini` are the built-in drivers; `RecipeDriver` runs any CLI that follows the contract from a declarative recipe, though `giro.toml` cannot select one yet (plus `FakeDriver` in tests). One contract: prompt in, JSON envelope out; CLI failures surface the CLI's own message and fail the attempt, never a bare exit code.
- **Finding** — one structured problem `{summary, detail?, location?, gate}`. Findings feed the next actor.
- **Budget** — the bound that turns "loop until green" into "loop until green or ask."
- **needs-human** — the only non-proof exit. Both state machines land here when a budget is spent.
- **Run** — one execution of the engine for one target, foreground or dispatched. It holds the Spec's lock for its whole life and ends at proof or escalation.
- **Dispatch** — starting a detached Run (`--detach`): the terminal returns at once and the Run outlives the session that started it. No resident scheduler; one Run is one process.
- **Ledger** — the disposable per-Run record under `.giro/runs/<run-id>/`: liveness, an append-only event stream (`{seq, at, type, …}`), outcome. Observability only — deleting it loses nothing durable.
- **Base branch** — the branch a Spec's work forks from and its pull request targets. Resolved once at first activation (config, else the invoking checkout's branch) and recorded in Spec frontmatter; never re-inferred.

## The topology rule

**Control never crosses an LLM. Content always does.**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/shape-dark.svg">
  <img alt="Your chat session (the standalone grill / spec / plan authoring skills and a thin /giro doorway) and your terminal both drive the giro CLI. The CLI is the loop engine; it spawns Worker and Judge contexts through drivers, each returning a JSON envelope. Escalations flow back to the chat. State lives in local markdown beside the code." src="figures/shape-light.svg">
</picture>

The engine (deterministic Python) owns: state transitions, attempt counting, gate execution, wave scheduling, commits, escalation. LLM contexts own: implementing, planning, judging. The engine's view of any context is identical — *spawn via driver, wait, parse envelope*. A context that cannot produce its envelope is a failed attempt or a failed gate (fail closed), never a shrug.

Corollaries:

- Workers never run `git commit`; the engine commits. Engine bookkeeping (activation, claim, plan, state moves, merges, gap cycles) is committed **path-scoped** to `docs/specs/<slug>/` — separate from worker output, so "worker produced no changes" stays detectable and gate artifacts never sneak into a state commit. Engine commits also skip project hooks (`--no-verify`) and GPG signing, so a pre-commit hook or a pinentry cannot dirty or hang the engine.
- Judges start blind on every attempt — no memory of prior rounds. Findings feed the next *actor*, never the next judge.
- Workers never edit `docs/specs/`, `CONTEXT.md`, `docs/adr/`, `prompts/`, or `giro.toml`. The engine enforces this per attempt: any diff under a protected path is reverted (`git checkout HEAD -- <path>` and a clean of untracked files under it), the attempt is refused with a `worker edited protected path(s)` finding, and the retry runs against the reverted tree. A worker cannot pass a gate by rewriting what it checks against.

## State machines

Stored frontmatter is exactly what the loops branch on. `blocked` is computed from `blocked_by` at scheduling time, never stored.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/states-dark.svg">
  <img alt="Issue states: blocked, then ready, then in-progress; Verify pass goes to done; a fail with budget left returns to ready with its findings; a spent budget or a worker escalation goes to needs-human, and re-running giro implement returns it to ready. Spec states: draft, then active, then done when Validate is green; active goes to needs-human when an Issue is stuck or the validate budget is spent, and re-running giro implement returns it to active." src="figures/states-light.svg">
</picture>

### Issue — `state:` in frontmatter, plus `blocked_by: []`, `attempts: N`

| From | To | Actor | When |
|---|---|---|---|
| ready | in-progress | engine | attempt starts (claim committed first) |
| in-progress | done | engine | worker completed + all Verify gates green |
| in-progress | ready | engine | attempt failed, budget remains (findings carried) |
| in-progress | needs-human | engine | budget spent, or worker escalated |
| needs-human | ready | human | re-invoking `giro implement` on the issue *is* the answer; attempts reset |
| any | wontfix | human | by editing the file; the engine never sets it |

Terminal for scheduling: `done`, `wontfix`.

### Spec — `state:` in frontmatter, plus `base: <sha>`, `base_branch: <branch>`, `gap_cycles: N`

| From | To | Actor | When |
|---|---|---|---|
| draft | active | engine | first `giro implement <slug>` |
| active | done | engine | all Issues terminal + all Validate gates green |
| active | needs-human | engine | any Issue stuck needs-human, or validate budget spent |
| needs-human | active | human | re-invoking `giro implement` *is* the answer; `gap_cycles` resets |

`done` means: **Validate passed; the human merges `giro/<slug>`.** There is no `shipped` state, no Feature record, no auto-merge. The machine's job ends where your branch review begins. `done` is terminal: re-invoking `giro implement` on a done Spec or Issue is a no-op that reports its state, never a re-validation.

## Envelopes

Every context ends with exactly one JSON object. Schemas in [`envelope.py`](../src/giro/envelope.py):

```json
// worker            // judge (gate verdict)        // planner
{ "outcome": "completed | needs-human | failed",
  "summary": "…",    { "verdict": "pass | fail",    { "issues": [ { "title", "body",
  "notes": "…" }       "findings": [ {"summary",        "blocked_by": [1] } ] }
                         "detail", "location"} ] }
```

Parsing is strict and fails closed: a malformed judge envelope gets one retry (with the validation error appended), then the gate fails with a `failed closed` finding. A malformed worker envelope consumes the attempt.

## The loops

Both loops instantiate one primitive — actor → verifier → proof | escalation — with these bindings:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/loops-dark.svg">
  <img alt="The Spec loop: a wave of Issue loops runs in isolated worktrees; branches integrate onto giro/&lt;slug&gt; one at a time with a re-verify; Validate then judges the whole Spec. On pass, the Spec is done and a human merges. On fail, gap Issues loop back into the next wave. Budget exhaustion escalates to needs-human." src="figures/loops-light.svg">
</picture>

| Clause | Issue loop | Spec loop |
|---|---|---|
| objective | Issue `done` | Spec `done` |
| actor | worker context | wave of Issue loops |
| verifier | `[verify]` gates | `[validate]` gates |
| findings feed | next worker attempt | gap Issues → next wave |
| budget | `issue_attempts` per Issue | `validate_cycles` gap cycles |
| escalation | Issue `needs-human` | Spec `needs-human` |
| memory | Issue frontmatter + attempt log | Spec frontmatter + gap Issues |

Gates run in config order to completion — every gate runs and every finding is collected, so a failed attempt sees the whole picture, not just the first problem. Green is decided by the verdict, not by the presence of findings: a gate that returns `pass` contributes nothing, so a judge that answers `pass` with advisory notes neither blocks the loop nor feeds noise to the next worker. A judged verify gate sees only the Issue's own diff; validate sees the whole Spec branch.

Waves: the frontier is every `ready` Issue whose `blocked_by` are all terminal (stale `in-progress` claims from a crashed run count as ready — the engine is the single writer; a `blocked_by` cycle can never reach the frontier, so it is rejected loudly before any wave). At `concurrency = 1` the frontier runs sequentially on the integration branch — still one fresh context per attempt, never the invoking chat session.

Every Run happens in the Spec's own persistent worktree — `.giro/worktrees/<slug>`, holding `giro/<slug>`, forked at first activation from the base branch recorded in Spec frontmatter ([ADR-0013](adr/0013-the-invoking-checkout-is-sacred.md)). The invoking checkout is never switched, never required clean, never written; the engine's runtime directory ignores itself, so neither the engine nor a human can commit it. Both concurrency shapes therefore run on committed content only — a sequential worker sees exactly what a parallel one does, and no git-ignored local file (a `.env`, a build artifact) reaches either. **Gates must be hermetic**: whatever a gate needs is committed, or the gate builds it itself.

At `concurrency > 1` the wave runs in parallel under one invariant: **only the engine's main thread touches the store or the integration branch.** Each Issue gets an isolated worktree at `.giro/wave-worktrees/<slug>/<issue-id>/` on a `giro-wt/<slug>/<issue>` branch forked from the wave head; its store-free attempt cycle (worker → commit → branch-local `[verify]` → retry) runs in a thread. Wave worktrees live under `.giro/` on purpose — the runtime directory self-ignores, so a hard-killed wave never leaves a stray worktree in the system tmp that a later Run cannot prune. Green branches are then integrated one at a time: `--no-ff` merge, integrated `[verify]` on the merged result, state + logs committed. A merge conflict or integrated failure resets to pre-merge and returns the Issue to `ready` with findings — the retry from the updated HEAD *is* the serialization of colliding scopes, bounded by the same attempt budget. One thread's exception is isolated to that Issue's future — a sibling's green branch is still integrated. Worktrees and worker branches are always discarded.

## Runs

One `giro implement` is one **Run**: one process, one target, ending at proof or escalation. There is no resident scheduler.

- **Dispatch.** `--detach` starts the Run in its own session — it survives the shell and the chat session that opened it — and returns immediately with the Run id. Foreground is the default and keeps the exit-code contract (`0` proof, `2` needs-human, `1` error); a dispatched Run's answer lives in its Ledger instead.
- **The Ledger** (`.giro/runs/<run-id>/`): `run.json` (liveness, phase, outcome), `events.jsonl` (one JSON object per state-changing moment — `activation`, `plan`, `wave`, `claim`, `attempt`, `merge`, `gap-cycle`, `escalation`, `exit`), `console.log`. Append-only, never rewritten; it is the seam the M6 projection consumes. Nothing reads it for control flow ([ADR-0008](adr/0008-markdown-is-the-memory.md)), so a deleted Ledger costs observability alone.
- **One Run per Spec.** A Run holds `.giro/locks/<slug>.json` for its life; a second dispatch is refused loudly, never queued. A lock whose process is gone is reclaimed under a per-runtime `flock` — judgment and reclaim happen inside one critical section, so two concurrent Runs can never both take a dead holder's lock. Independent Specs run concurrently, bounded by one **per-checkout** cap on worker contexts (`.giro/slots/`) — a Run waits for a slot rather than exceeding it.
- **Reading a Run.** `giro status` resolves each Spec through its branch tip (checkout only for Specs with no branch yet), so it tells the truth from any branch, and overlays Ledger liveness; `--json` is the same structure for skills and CI. `giro runs` lists Runs; `giro logs <run-id>` tails one Run's story (default is a human rendering — pass `-f` to follow, `--json` for JSONL). A foreground `giro implement` also emits short human phase lines on stderr — claim, worker end, each gate verdict, state transition, wave merge — silenced with `--quiet`. `giro doctor` checks readiness before a Run: config parses, every roster driver is on `PATH`, git is a repo with a branch, and (if `[github_projection]` is on) `gh` and the token are present. Status also holds the installed `.claude/skills` copies against this giro's bundled skills: `giro install` records a content hash of what it placed, so a copy an older giro left behind warns as stale (`giro install --force` refreshes), while a copy edited after placement is an override by design and stays quiet.
- **Answering an escalation.** The Issue's truth is the copy on `giro/<slug>`, so the human edits the body in the Spec's worktree from wherever they are standing. That answer arrives uncommitted; the engine commits it (pathspec-scoped to `docs/specs/<slug>`) at the start of the next Run, before the clean-tree check, which therefore still speaks only about debris. Re-invoking is the answer; the budget resets. With the Projection on, the escalation also lands on GitHub — the `giro:needs-human` label, a comment carrying the durable reason, and an assignment to `[github_projection] assignee` — and the re-invocation clears the label and comments that the Run resumed.
- **Who the token is.** GitHub never notifies anyone about their own actions, so a token owned by the human giro escalates to assigns and mentions them *silently*: the surface renders perfectly and the phone stays quiet. Give the engine a **machine account's** fine-grained token instead, scoped to this one repository (issues, pull requests, contents, commit statuses — read/write). `giro project --check` names the identity in play and warns when it is the assignee themself; a Run says the same warning out loud at preflight.

The `giro` skill is the console over this surface: dispatch, report from status and the Ledger, walk escalations — never blocking the conversation on the engine, never implementing in-chat, never merging `giro/<slug>`.

### Events

The event stream in `events.jsonl` is the Run's public contract: one JSON object per line, one line per state-changing moment, `{schema_version, seq, at, type, …}`. `giro logs --json` is the machine tail; consumers (the GitHub projection, a CI job, a chat skill) parse it as a versioned stream.

Invariants: append-only, never rewritten; one event per state-changing moment; `seq` starts at 1 and increases monotonically across the whole Run; every event carries `schema_version = 1` from now on (a consumer that meets an event without one treats it as pre-1). Within one process `Ledger.event()` is thread-safe (append under a lock); the engine's main thread is the only one that writes state-changing commits, so a wave's threads emit only `attempt` and `merge` events while every other event comes from the main thread.

Every event carries `schema_version`, `seq`, `at`, `type`. The full set (each line names the type and every field it may carry):

- type activation: fields schema_version, seq, at, type, spec, state, branch, base_branch
- type plan: fields schema_version, seq, at, type, spec, issues
- type wave: fields schema_version, seq, at, type, spec, wave, issues, concurrency
- type claim: fields schema_version, seq, at, type, issue, attempts
- type attempt: fields schema_version, seq, at, type, issue, n, outcome, verdicts, findings, summary
- type merge: fields schema_version, seq, at, type, issue, branch, result, verdicts, findings
- type gap-cycle: fields schema_version, seq, at, type, spec, cycle, issues, findings
- type escalation: fields schema_version, seq, at, type, target, reason, findings
- type projection: fields schema_version, seq, at, type, action, ok, detail, command
- type exit: fields schema_version, seq, at, type, outcome, detail

Adding an event type or a field without naming it here fails the doc-drift test.

## The seam — one file, two zones

Planning happens in conversation; building happens in the machine. They meet on the same markdown file without collisions because they never write the same bytes.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="figures/zones-dark.svg">
  <img alt="One file, two zones: the conversation scaffolds via giro new and writes only the body; the engine writes frontmatter state and appends attempt logs. giro implement passes the baton to the engine; needs-human passes it back." src="figures/zones-light.svg">
</picture>

`giro new spec | issue | adr` scaffolds the artifact so no LLM ever types a `state:` line. The conversation writes only the **body**. The engine owns the **frontmatter** and appends the **attempt log** under a fenced separator (`<!-- giro:log -->`), never rewriting body bytes. `giro implement` passes the baton to the engine; `needs-human` passes it back. Re-invoking is the answer, at both levels: on an Issue, its attempts reset; on a Spec, `gap_cycles` resets and every needs-human child Issue is set back to `ready` in the activation commit.

## Configuration

`giro.toml` at the project root, read once per Run, never invented by a loop.

```toml
[verify]                # Issue-level · runs after every worker attempt
gates = [
  { name = "check",       type = "command", run = "make check" },
  { name = "review",      type = "judge",   criterion = "review" },       # prompts/review.md
  { name = "conformance", type = "judge",   criterion = "conformance" },  # prompts/conformance.md
]

[validate]              # Spec-level · the outer verifier
gates = [
  { name = "spec-fit", type = "judge", rubric = "Every acceptance criterion is met, with tests." },
]

[runner]
concurrency = 1         # 1 = sequential fresh workers · >1 = isolated worktrees, merged serially
max_workers = 4         # cap on worker contexts across this checkout's Runs
# base_branch = "main"  # unset = the branch you dispatch from, recorded once at first activation

[runner.roster]         # drivers: claude | agy | codex | gemini
worker  = { driver = "claude", args = ["--dangerously-skip-permissions"] }
judge   = { driver = "claude" }   # blind, read-only — no bypass flag
planner = { driver = "claude" }   # reads the Spec — no bypass flag

[budget]                # the "never silent" bounds
issue_attempts  = 3     # Verify retries per Issue before needs-human
validate_cycles = 3     # gap → re-wave loops before needs-human
gate_timeout    = 600   # seconds per command gate
context_timeout = 3600  # seconds per spawned LLM context

[github_projection]     # optional, experimental in 0.1.0 — one-way, fail-soft (ADR-0014)
enabled = false
```

The retired gate types `prompt` and `skill` (three-kind era) are refused with a migration error; the retired roster role `implementer` is accepted with a deprecation notice for one cycle and mapped to `worker`.

## Decisions

The load-bearing decisions are ADRs under [docs/adr/](adr/) — recorded the way giro itself prescribes (one paragraph each, `giro new adr` allocates numbers):

1. [Control never crosses an LLM](adr/0001-control-never-crosses-an-llm.md)
2. [The CLI is the product; skills are the touchpoints](adr/0002-cli-owns-the-loop-skills-are-touchpoints.md)
3. [One build verb, routed by the target](adr/0003-one-verb-routing.md)
4. [Validate is the Spec loop's verifier, not a stage](adr/0004-validate-is-the-spec-loops-verifier.md)
5. [Spec `done` = Validate passed; a human merges](adr/0005-done-means-validated-human-merges.md)
6. [Fail closed everywhere](adr/0006-fail-closed-everywhere.md)
7. [Local and parallel are one code path](adr/0007-one-code-path-for-local-and-parallel.md)
8. [Markdown is the memory](adr/0008-markdown-is-the-memory.md)
9. [The template has one writer](adr/0009-the-template-has-one-writer.md)
10. [Docs change in conversation; loops obey them](adr/0010-docs-change-in-conversation-loops-obey.md)
11. [Host skills and engine prompts live apart](adr/0011-host-skills-and-engine-prompts-live-apart.md)
12. [Setup owns config; init is the CI fallback](adr/0012-setup-owns-config-init-is-the-ci-fallback.md)
13. [The invoking checkout is sacred](adr/0013-the-invoking-checkout-is-sacred.md)
14. [GitHub is a one-way, fail-soft projection](adr/0014-github-is-a-one-way-fail-soft-projection.md)
15. [Prompts and skills live apart cleanly](adr/0015-prompts-and-skills-live-apart-cleanly.md) — supersedes ADR-0011

## Non-goals

Bug intake/triage queues, two-way tracker sync (M6's GitHub surface is a one-way projection the engine renders and never reads back — [ADR-0014](adr/0014-github-is-a-one-way-fail-soft-projection.md)), Feature documentation, merge-to-main automation, multi-repo orchestration. Some may return later; none are core.

## Milestones

- **M0 — engine core** (done): everything above, proven by `tests/` against `FakeDriver`.
- **M1 — live driver** (done 2026-08-06, [first light](notes/m1-first-light.md)): four CLI drivers with fail-loud errors; empty Spec planned → implemented → verified → validated end-to-end by live contexts, first attempt.
- **M2 — parallel waves** (done 2026-08-06, [evidence](notes/m2-parallel-waves.md)): isolated worktrees, serialized `--no-ff` merges + integrated re-Verify, conflict-retry from updated HEAD; two live workers ran a wave concurrently.
- **M3 — the doorway** (done 2026-08-06): the wheel bundles `skills/` as `giro/_skills`; `giro install` places editable copies into `.claude/skills` (skip-existing, `--force` to restore); skill text resolves project `skills/` → `.claude/skills/` → bundled, for both worker injection and skill gates; `giro status` surfaces `needs-human` with the answer-and-re-run flow. One install unit: the CLI carries the skills.
- **M4 — the upstream** (done 2026-08-07): the `giro new` seam and the one-file-two-zones contract; the `spec`, `plan`, and `grill` skills adapted from Matt Pocock's judgment onto giro mechanics (renamed from `giro-`prefixed and cut loose from the CLI at M9); default judged gates in `prompts/review.md` (active) and `prompts/conformance.md` (docs-obedience, commented until docs exist); `CONTEXT.md` injected into worker packets; dangling `blocked_by` fails loud everywhere.
- **M5 — detached runs** (done 2026-08-11, [spec](specs/detached-runs/SPEC.md)): loops move into per-Spec worktrees forked from a recorded base branch — the invoking checkout is never touched ([ADR-0013](adr/0013-the-invoking-checkout-is-sacred.md)); `--detach` daemonizes a Run; the Ledger (liveness, events, outcome) makes every Run observable; per-Spec locks, a per-checkout worker cap, branch-tip `giro status` with machine output; the `giro` skill became the dispatch console.
- **M6 — the projection** (done 2026-08-11, [spec](specs/github-projection/SPEC.md)): Run state rendered onto GitHub one-way and fail-soft via `gh` ([ADR-0014](adr/0014-github-is-a-one-way-fail-soft-projection.md)) — parent tracking issue and sub-issues with labels and native progress, verbatim attempt comments, commit statuses per gate, a draft pull request flipped ready when Validate passes, escalations that notify; `giro project` reconverges the surface from markdown alone.
- **M7 — 0.1.0** (done 2026-09-09, [ADR-0015](adr/0015-prompts-and-skills-live-apart-cleanly.md)): the shipping cut. Four moves land together:
  - **Reorg.** The three-directory split of ADR-0011 collapses to two — `prompts/` holds plain markdown the engine injects (`worker.md`, `planner.md`, `review.md`, `conformance.md`), `skills/` holds host-invocable SKILL.md operator skills: the giro-prefixed engine skills (`giro`, `giro-setup`) and the unprefixed, standalone authoring skills (`spec`, `plan`, `grill`; see the documentation-phase move below). Gate types collapse to `command | judge`; a judge names a criterion or carries an inline rubric. The roster role renames `implementer` → `worker` (legacy accepted with a deprecation notice).
  - **Engine correctness.** Workers cannot rewrite what judges them (protected-path guard reverts and refuses the attempt). Spec re-invoke resets its `needs-human` Issues so re-invoking is the answer at both levels, not just at Issue targets. Stale-lock reclaim happens under a `flock`, so two Runs cannot both take a dead holder's lock. Wave worktrees live under `.giro/` — a hard-killed wave cannot wedge the Spec. Prompts flow on stdin (past the 128 KiB argv limit); every subprocess spawns with `start_new_session=True` so a timeout kills descendants; `[validate]` is required so no Spec silently all-greens; engine commits are path-scoped and skip hooks/signing; one thread's exception no longer discards its siblings' green branches.
  - **Operator surface.** `giro doctor` for readiness, driver preflight before git is touched, branch-aware `giro new issue`, numeric Issue-id prefixes (`giro implement my-feature/01`), `giro logs` defaults to non-follow, a top-level traceback guard on the CLI, and stderr warnings for unknown config keys or roles.
  - **Release prep.** Version is dynamic from `src/giro/__init__.py`; classifiers declare POSIX and Python 3.11–3.13; CI runs the matrix and `scripts/dist-check.sh` on every PR; sdist excludes `.claude`, `.github`, `tests`, `uv.lock`, `docs/specs/`, `.env`; README is PyPI-renderable; `CHANGELOG.md` records 0.1.0.
- **M8 — events stream** (done 2026-09-09 by hand, outside the engine; [spec](specs/events-stream/SPEC.md)): the Ledger's `events.jsonl` is a public versioned contract — every event carries `schema_version = 1`, every type and field is named in the Runs → Events subsection above, and a doc-drift test walks a fixture Run and asserts nothing appears that isn't documented. `giro logs --json` prints the raw JSONL for any Run, live or finished, with `--follow` for a machine tail. `giro implement` gains stderr phase lines while it runs, silenced by `--quiet`; the final stdout report, the Ledger, and exit codes are unchanged.
- **M9 — the documentation phase stands alone** (done 2026-09-15, [ADR-0018](adr/0018-authoring-skills-are-giro-independent.md), [ADR-0019](adr/0019-state-frontmatter-is-engine-only.md)): the two phases split at the **activation line**. Upstream of it — the documentation phase — the `grill`/`spec`/`plan` skills drop the `giro-` prefix and every CLI dependency; they carry their own templates and numbering-by-inspection and write *plain* artifacts (bodies, ADRs, `blocked_by` edges — never a `state:` line), so they install and run with no giro at all ([skills/README](../skills/README.md)). The shared authoring seam moves into `src/giro/scaffold.py` (stdlib-only, no engine imports), the one home for numbering and starter bodies that `giro new` also builds on. Downstream — the implementation phase — the engine ingests those artifacts: a missing lifecycle field defaults on read (`SPEC_INITIAL`/`ISSUE_INITIAL`) and is stamped when the Spec activates, so state stays the engine's alone (ADR-0009 preserved, moved one layer down). `giro doctor` gains a **specs** section that reads every artifact the engine's way — parse, resolvable `blocked_by`, no cycle — the fail-closed boundary that keeps a malformed doc from ever reaching a Run.
