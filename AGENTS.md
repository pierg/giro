# AGENTS.md — giro

This file is the only copy of the operating rules. `CLAUDE.md` is the one line `@AGENTS.md`. Codex and Cursor read this file and do not expand `@` imports.

## Read

- `README.md` — what giro is and how a project installs it.
- `docs/design.md` — the nouns and the loop.
- `docs/adr/0011-host-skills-and-engine-prompts-live-apart.md` — host skills are copied into `.claude/skills`; engine prompts are not.
- `skills/` — the host-skill bodies. `prompts/` — text the engine injects, including the judge criteria.

## The gate

```bash
uv run pytest -q
uv run ruff check .
scripts/dist-check.sh
```

That is what `.github/workflows/ci.yml` runs.

## Skills

`giro install` copies each host skill from `skills/<name>/` into `.claude/skills/<name>/` and records a content hash in `.claude/skills/.giro-install.json`. A copy edited after that is an override: `giro status` warns, and `giro install` does not replace it unless `--force`. `.agents/skills` is a relative symlink to `../.claude/skills`, so Codex and Cursor read those same copies. There is no second body.

<!-- ops:lanes:start (generated from the owner's central rules; change them there, not here) -->
## How changes reach main

`main` is the only source of truth and is always clean: every commit on it passes the gate below.
Every change goes through a pull request; `main` accepts nothing else.

1. Branch from the latest `origin/main` in a separate worktree, named after the task (its tracker
   issue, for example `pie-142-fix-login`).
2. Push early and open a draft pull request.
3. When the work is done and the gate passes, squash-merge it with a clear title and description,
   then delete the branch and the worktree.

**Gate:** `uv run pytest -q` and `uv run ruff check .`.

**Authorship.** Every commit is authored with the repository owner's git identity. No co-author
line, tool or agent name, "generated with" footer or session link appears in a commit message, a
branch name or a pull request. A required check on every pull request enforces it.
<!-- ops:lanes:end -->
