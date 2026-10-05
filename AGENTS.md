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
