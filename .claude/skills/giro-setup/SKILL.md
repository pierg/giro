---
name: giro-setup
description: Configure giro for the project in the current working tree — probe installed agent CLIs and repo evidence for real Verify gates, interview for preferences, then write a fitting giro.toml. Use when the user wants to set up giro in this project, configure giro, or revise an existing giro.toml.
---

# giro-setup — a giro.toml that fits this repo

Configure giro for the project rooted at the current working directory: inspect it, interview only for preferences, then write `giro.toml`. This is the human path into giro — `giro init` is the non-interactive fallback for headless/CI use, not something to tell a chat user to run.

## Process

1. **Root is cwd.** Treat the current working directory as the project root. Don't configure a sibling repo unless the user says so.
   It must be a git repository with at least one commit before giro can run (`git rev-parse HEAD`). If it is not, say so and offer to `git init` and make the first commit; do not do either without a yes.
2. **Read any existing `giro.toml`.** If one exists, this is a revision, not a blank start — show what's there and ask what should change instead of starting over.
3. **Probe the four bundled drivers on PATH.** Check `claude`, `agy`, `codex`, `gemini` (e.g. `which <name>`). Only installed ones are credible defaults for the roster.
4. **Gather Verify-gate evidence from the repo** — don't invent `make test`. Look at:
   - package manifests and their scripts (`package.json` scripts, `pyproject.toml`, `Cargo.toml`, …)
   - `Makefile` targets
   - CI workflow files (`.github/workflows/*`, etc.) for the commands they actually run
   - documented validate commands in `README`, `GUIDELINES.md`, `AGENTS.md`
   - `CONTEXT.md` / `docs/adr/` presence — if either exists, propose enabling the bundled `conformance` judge criterion

   Propose command gates built from what you found, never a guess.
5. **Interview in frontier rounds**, grill-style — only for genuine decisions, never for facts you can look up yourself:
   - which driver(s) for `worker` / `judge` / `planner` (default to what's on PATH; if several are installed, ask)
   - `concurrency`
   - budgets (`issue_attempts`, `validate_cycles`, `gate_timeout`, `context_timeout`) — recommend the bundled defaults unless the user wants otherwise
   - which gates to keep: the evidence-based command gates from step 4, the bundled `review` judge gate, and `conformance` if offered

   Present each round numbered, with your recommended answer, same shape as the `grill` skill.
6. **Propose the full `giro.toml`** from the settled answers and show it to the user.
7. **Write only after the user confirms.** Write the complete file yourself from the settled preferences — never call `giro init` and patch its template; that template is not the source of truth for a skill-authored config.
8. **Create `docs/specs/`** if it doesn't exist yet.
9. **Point at what's next**, in plain words the user can say: "grill me on <idea>" (the `grill` skill), "write that up as a spec" (`spec`), "implement <slug> with giro" (the `giro` skill). Mention `giro doctor` only if a check failed.

## What a good `giro.toml` looks like

- `[verify]` — one or more `command` gates from real repo evidence, plus a `judge` gate with `criterion = "review"`; add another with `criterion = "conformance"` only once `CONTEXT.md` or `docs/adr/` exist.
- `[validate]` — a `judge` gate carrying an inline `rubric` for Spec acceptance criteria, unless the user wants a named criterion.
- `[runner]` — `concurrency`, and `[runner.roster]` entries per role using drivers actually on PATH. The `worker` needs its CLI's permission-bypass flag in `args`; keep that flag out of `judge` and `planner`.
- `[budget]` — `issue_attempts`, `validate_cycles`, `gate_timeout`, `context_timeout`.

## Hard rules

- Never write `giro.toml` before the user confirms the proposed config.
- Never shell out to `giro init` as a shortcut — write the settled file directly.
- Facts you can find by reading the repo or probing PATH are never a question to the user; only real preferences are.
