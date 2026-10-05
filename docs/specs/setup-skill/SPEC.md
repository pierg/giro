---
state: done
base: e64f08aed0dee7223add15c42ba25cc5c7080860
gap_cycles: 1
---
# Setup Skill

## Problem Statement

Onboarding a project to giro today starts with `giro init`, which dumps a one-size-fits-all `giro.toml` (e.g. `make test`, driver `claude`) that almost never matches the repo or the agent CLIs the user actually has installed. The host skills that could have inspected the project and asked preferences arrive only *after* that bad config exists, so the human path is "generate wrong file, then hand-edit." Setup is conversational work treated as a static template.

## Solution

Make project configuration a chat skill. The human path becomes: install the CLI, run `giro install` so host skills are discoverable, then invoke a new **`giro-setup`** skill in the project where giro should run. That skill inspects the repo and the agent CLIs on PATH, interviews the user for preferences, and writes a fitting `giro.toml` (plus `docs/specs/` if needed). `giro init` remains as a non-interactive fallback for headless/CI use, not the default human path.

## User Stories

1. As a developer new to giro, I want `giro install` to be the first project step after installing the CLI, so that chat skills are available before any config is written.
2. As a developer, I want to say "set up giro in this project" and have a skill configure it from the current working tree, so that I never hand-copy a template that doesn't match the repo.
3. As a developer, I want the setup skill to detect which agent CLIs I have installed (`claude`, `agy`, `codex`, `gemini`), so that the roster defaults to something that will actually spawn.
4. As a developer, I want the setup skill to propose Verify command gates from evidence in the repo (CI configs, Makefiles, package scripts, documented validate commands), so that I confirm real gates instead of inventing `make test`.
5. As a developer, I want to choose concurrency, budgets, whether to enable the `review` / `conformance` skill gates, and which driver roles to use, so that preferences — not the template — shape `giro.toml`.
6. As a developer revisiting setup, I want the skill to read an existing `giro.toml` and offer to revise it, so that reconfiguration doesn't require deleting the file and starting from a blind template.
7. As a developer in CI or a non-chat environment, I still want `giro init` to write a minimal valid starter config, so that headless bootstraps keep working.
8. As a chat agent following the `giro` doorway skill, I want its setup section to point at `giro install` then the `giro-setup` skill, so that I don't tell users to run `giro init` as the primary path.

## Implementation Decisions

- **New host skill, not an extension of `giro`.** The `giro` skill remains the doorway for driving loops (`implement`, status, escalations). Setup is a different job with a different trigger and conversation shape. Name it **`giro-setup`** (not bare `setup` — too generic in a crowded host skill list; not `giro-config`).
- **`giro.toml` is conversation-owned.** The `giro-setup` skill writes it; the engine only reads it. Wholly conversation-owned — no engine zone (unlike Spec/Issue markdown). No new engine command that invents gates from heuristics — probing and preference-taking stay in the skill.
- **Where the skill is invoked is the project root.** Inspect that tree (and PATH for drivers). Do not configure a sibling repo unless the user says so.
- **Inspection sources (non-exhaustive, skill guidance):** package manifests and scripts, Makefiles, CI workflows, existing verify docs (`GUIDELINES.md`, `README`, `AGENTS.md`), presence of `CONTEXT.md` / `docs/adr/` (suggest enabling `conformance`), and `which`-style probes for the four bundled drivers.
- **Interview style:** grill-like frontier rounds for *decisions* (driver choice when several are installed, concurrency, which proposed gates to keep). Facts the skill can look up must not be asked. Recommend defaults; confirm before writing.
- **Write path:** create or overwrite `giro.toml` only after the user confirms the proposed config. Create `docs/specs/` if missing. Prefer writing the full file from the settled preferences over calling `giro init` and patching — `init`'s template must not become the source of truth for skill-authored configs.
- **`giro init`:** keep as non-interactive fallback; update its "Next:" copy to point at `giro install` + the `giro-setup` skill for the human path. Do not remove `init` in this Spec.
- **Quickstart / doorway docs (worker-owned):** reorder to `uv tool install git+https://github.com/pierg/giro` → `giro install` → chat "set up giro" (`giro-setup` skill) → then grill/spec/implement. Update README and the `giro` skill's Setup section only.
- **ADR + glossary (conversation-owned, not worker Issues):** per ADR-0010, `CONTEXT.md` and `docs/adr/` are written in conversation, never by workers. Before this Spec can validate, conversation records ADR-0012 (giro-setup owns config; init is CI fallback) and adds `giro-setup` to the Skill glossary in `CONTEXT.md`. Do not put those edits in Issue acceptance criteria.


## Testing Decisions

Good tests here check *external* bootstrap behavior and packaging, not the LLM interview:

- `giro install` places a `giro-setup` skill folder alongside `giro` / `spec` / `plan` / `grill` (existing install tests extended).
- `giro init` still writes a loadable `giro.toml` and `docs/specs/`, and its stdout points humans at install + giro-setup.
- Bundled skill markdown for `giro-setup` exists under `skills/giro-setup/` and is the unit `install` copies (same packaging seam as other host skills).
- Doc/quickstart strings that still prescribe `init`-then-`install` as the human path are updated (drift covered by whatever doc checks already exist; otherwise review gate / manual acceptance).

Prior art: `tests/test_skills.py` for install placement and skill resolution; treat the interactive inspection/interview as skill procedure text, not unit-tested dialogue.

## Out of Scope

- Auto-detecting or generating Validate rubrics beyond a sensible default prompt gate.
- A new `giro doctor` / probe CLI (the skill shells out for PATH and reads files; a dedicated command can wait until a second consumer needs the same probes).
- Reconfiguring other repositories that already use giro (and their existing `giro.toml`) as part of shipping this Spec.
- Removing `giro init`, or making `init` itself interactive in the terminal.
- Host discovery paths beyond what `giro install` already supports (still `.claude/skills` unless install grows separately).
