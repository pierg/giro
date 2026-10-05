# Changelog

All notable changes to giro. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## 0.1.0 — 2026-09-27

First real release. The founding milestones (M0–M6) ran end-to-end with live agents, on toy repositories and on this repository's own Specs (`docs/specs/`).

### Added

- **Guarded-loop engine.** Deterministic Issue and Spec loops with budgeted retry, fail-closed gates, and `needs-human` escalation as the only alternative to proof.
- **One verb, one router.** `giro implement <target>` picks the level from the target; no mode flags.
- **Parallel waves.** At `concurrency > 1` workers run in isolated git worktrees, serialized-merge back with an integrated re-verify; the shared-checkout corruption that makes parallel agents dangerous cannot happen.
- **Detached Runs.** `giro implement --detach` returns at once; Runs survive the shell and the chat session; per-Spec locks (reclaimed when the holder dies) and a per-checkout worker cap; `giro status --json`, `giro runs`, `giro logs <run-id>`, `giro logs --console <run-id>`.
- **A Ledger per Run.** Append-only event stream (activation, plan, wave, claim, attempt with gate verdicts and findings, merge, gap cycle, escalation, exit); disposable observability — markdown is still the memory.
- **Host skills.** The engine skills `giro` and `giro-setup`, and the standalone authoring skills `grill`, `spec` and `plan` — bundled in the wheel and placed by `giro install`. Skills are the human path; the CLI verbs stay for CI.
- **Engine prompts.** `worker.md`, `planner.md`, `review.md`, `conformance.md` — bundled in the wheel, project-overridable via `prompts/<name>.md`. Prompts and skills live apart (ADR-0015): nothing engine-internal masquerades as a chat command.
- **The `giro new` seam and one-file-two-zones contract.** `giro new spec`, `giro new issue`, `giro new adr` scaffold the frontmatter; the conversation writes only the body; the engine owns state and appends the log — same file, no collisions.
- **GitHub projection (experimental).** `giro project` reconciles the story to GitHub — tracking issue + sub-issues with live labels and progress, attempt comments, commit statuses per gate, a draft PR that flips ready on Validate, escalations that ping via labels. One-way and fail-soft (ADR-0014); requires `gh` and a token in `.env`.
- **Drivers.** `claude` (Claude Code), `agy` (Antigravity), `codex`, `gemini`; fail-loud on missing binaries. `RecipeDriver` runs any CLI that honours the worker contract from a declarative recipe; selecting one from `giro.toml` is not wired yet.
- **CI: wheel-bundles-skills invariant.** `scripts/dist-check.sh` builds the wheel and asserts every shipped skill and prompt is present under `giro/_skills/` and `giro/_prompts/`; the release workflow runs it before publish.
- **`giro doctor` specs section.** A fail-closed pre-flight that reads every Spec and Issue the way the engine will — parse, resolvable `blocked_by`, no cycle — so a malformed artifact never reaches a Run.
- **`src/giro/scaffold.py`.** The shared, dependency-free authoring seam (numbering + starter bodies) that both `giro new` and the standalone `spec`/`plan`/`grill` skills build on.

### Changed

- Version is now sourced from `src/giro/__init__.py` via hatch dynamic versioning — one owner, no drift.
- README rewritten for launch: short, with an enforced-by-code versus by-instruction table. Image and link URLs are absolute so they render on PyPI (which shows the light image of each `<picture>`). The long material moved to `docs/guide.md` and `docs/roadmap.md`; `DESIGN.md` moved to `docs/design.md`.
- **Authoring skills do not mention giro.** `grill`, `spec`, and `plan` describe documents only; they leave implementation open instead of dispatching a loop engine.
- **Authoring skills drop the adaptation footer.** `spec`, `plan`, and `grill` no longer end with an "Adapted from Matt Pocock…" credit; the MIT notice stays in [LICENSE](LICENSE).
- **The documentation phase stands alone.** The authoring skills are `grill`, `spec` and `plan`, with no `giro-` prefix (earlier development builds called them `giro-grill`, `giro-spec`, `giro-plan`). They carry their own templates and numbering and write plain Specs, ADRs and Issues, usable with no giro installed (see `skills/README.md`). Adapted from Matt Pocock's *Skills for Real Engineers* (MIT). ([ADR-0018](docs/adr/0018-authoring-skills-are-giro-independent.md))
- **Lifecycle state is engine-only.** Authoring artifacts carry no `state:` / `attempts:` frontmatter; the store defaults a missing state on read (`draft`/`ready`) and the engine stamps the real value when the Spec activates. A present-but-invalid state still fails loud. ([ADR-0019](docs/adr/0019-state-frontmatter-is-engine-only.md), extends [ADR-0009](docs/adr/0009-the-template-has-one-writer.md))

### Fixed

- **Protected-path edits can no longer slip past the guardrail.** The guardrail read only uncommitted changes, and only for completed attempts: a worker that ran `git commit` could rewrite a Spec, an ADR, a prompt or `giro.toml` and pass. A worker that rewrote a protected path and then escalated or gave up had the rewrite picked up by the engine's next state commit. The engine now folds any commits a worker made back into the working tree and reverts protected edits after every attempt, whatever the worker reports; reverting copes with new files next to modified ones. Git-ignored files under protected paths, and a changed ignore file whose rules would hide one, count as protected edits too, but only when the attempt created or changed them: a gate's own ignored output, or an ignore rule that was already there, is never blamed on the worker.
- **Non-ASCII paths no longer slip past the guardrail.** git C-quotes such names by default, so `docs/adr/0099-é.md` did not match its protected prefix. The engine now reads git's NUL-separated (`-z`) output.
- **Re-running after `needs-human` works when the last worker left uncommitted edits.** An escalating attempt cycle now discards what the worker left uncommitted (never judged) and names the paths in the Issue's log, so the next Run's clean-tree check passes.

### Known limitations

- **POSIX only.** giro uses `fcntl`, POSIX hardlinks, and `start_new_session`; import fails with a clean message on Windows. WSL works.
- **`gh` CLI required for the projection only.** The projection is experimental in 0.1.0; without it, giro has no runtime system deps beyond Python 3.11+ and git.
- **The machine is not sandboxed.** The engine bounds *iteration*; it does not bound *blast radius*. Run in a container, VM, or throwaway clone. See "Running safely" in `docs/guide.md`.

## 0.0.1 — Preview

Placeholder release published 2026-08-06 to reserve the name on PyPI. It predates the reorg and does not represent shippable code.
