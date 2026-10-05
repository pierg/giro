# Skills

Two kinds of host-invocable skill live here, side by side.

## Authoring skills — standalone, no giro required

`grill`, `spec`, and `plan` are the **documentation phase**: sharpen intent, capture a spec, slice it into Issues. They depend on nothing but a repo and a chat host — no `giro` install, no CLI, no config. Each writes plain markdown:

| skill | writes |
|-------|--------|
| `grill` | `CONTEXT.md` (glossary) and `docs/adr/NNNN-*.md` (decisions) |
| `spec` | `docs/specs/<slug>/SPEC.md` |
| `plan` | `docs/specs/<slug>/issues/NN-*.md` (with `blocked_by` edges) |

They carry no lifecycle state — a spec written here is just a document. That is what lets them stand alone *and* feed a loop engine downstream: giro reads these same files and stamps its own state when it starts to build.

**Install them anywhere** by copying the three folders into your host's skill directory — for Claude Code:

```bash
cp -r skills/grill skills/spec skills/plan ~/.claude/skills/   # or a project's .claude/skills/
```

Then invoke `grill` / `spec` / `plan` in chat. No other setup.

## How they differ from the originals

The three skills adapt Matt Pocock's [Skills for Real Engineers](https://github.com/mattpocock/skills). The judgment is his and is kept close to his wording. The changes all serve one goal: the documents are files in the repository, written once by a person and an agent together, then read, and never edited, by whatever builds from them.

### `grill`, from `grill-with-docs`

`grill-with-docs` calls two skills, `grilling` (the interview) and `domain-modeling` (the glossary and ADRs). `grill` is the two in one file, so it installs as a single folder.

- **Kept:** the design tree worked in frontier rounds, each question numbered with a recommended answer; facts looked up by the agent, decisions put to you; challenging terms against the glossary and checking claims against the code; ADRs only when a decision is hard to reverse, surprising and a real trade-off.
- **Changed:** the glossary is `CONTEXT.md`, one per repository (no multi-context map). ADRs take the next free number in `docs/adr/`.
- **Added:** what happens after. The glossary and ADRs are the source of truth for everything built later. An implementation may not edit them; a change of vocabulary or a stale decision comes back through `grill`. Under giro this is enforced: every worker is given `CONTEXT.md`, worker edits to it or to `docs/adr/` are reverted, and the optional conformance gate judges the diff against both. When the frontier is empty, `grill` hands off to `spec`.

### `spec`, from `to-spec`

`to-spec` synthesises the conversation into a spec and publishes it to your issue tracker with a `ready-for-agent` label, after a setup skill has configured the tracker.

- **Kept:** no interview, only synthesis; explore the repo first; sketch the test seams (as few as possible, as high as possible) and check them with you; the template's Problem statement, Solution, User stories, Implementation decisions, Testing decisions and Out of scope.
- **Changed:** the output is `docs/specs/<slug>/SPEC.md`, a folder per feature in the repository. It is versioned with the code, reviewed in a pull request, and later holds the feature's Issues and, under giro, their attempt logs. No tracker and no setup step.
- **Added:**
  - **Shape**, a small diagram of the moving parts named by role, never by file path, drawn with whatever tool the project has. A diagram that is its own file sits in the spec's folder.
  - **A contract gets an example**: any schema, event or API shape the spec defines is shown as one trimmed instance, because prose alone cannot carry a contract.
  - **Docs impact**, the docs, README claims and figures the change makes stale and what must change in each; "None" is allowed, silence is not.
  - **Lean beats thorough-sounding**: every fresh implementer and every reviewer re-reads the spec, so a decision is a bullet with a one-line reason.
- **Dropped:** Further notes, and the label: a spec carries no status. Under giro the engine stamps state when it starts to build.

### `plan`, from `to-tickets`

`to-tickets` slices a spec into tracer-bullet tickets with blocking edges and publishes them to the tracker, or to `.scratch/<feature>/issues/` with the edges written as text and a `ready-for-agent` status.

- **Kept:** vertical slices through every layer, each verifiable alone and sized for one fresh context; prefactoring first; blocking edges and the frontier; expand, migrate, contract for a wide refactor; the quiz on granularity and edges before anything is written.
- **Changed:** Issues are `docs/specs/<slug>/issues/NN-slug.md`, beside their spec, and the edges are a `blocked_by` frontmatter list a program can read. giro schedules waves from it, and `giro doctor` refuses a dangling edge or a cycle before any Run.
- **Added:** each Issue body ends with **Not in this Issue**, the adjacent work it must refuse and where that work lives. The review gate checks scope against it.
- **Optional under giro:** skip `plan` and the engine's planner slices the spec with the same judgment.

### What replaces `implement-spec`

The originals continue with `implement-spec`, which orchestrates sub-agents from the chat session. giro replaces that step with the engine: a deterministic loop runs each Issue in a fresh context, judges it with your tests and blind reviewers, and stops at proof on a branch or a question for you.

## Engine skills — the implementation phase

`giro` and `giro-setup` drive the giro CLI (the guarded-loop engine). They require `giro` installed and are placed by `giro install` alongside the authoring trio. See the repo README.
