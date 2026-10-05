# giro — roadmap

What has shipped, milestone by milestone. The fuller record, with dates, is the Milestones section of [design.md](design.md).

- **M0 — engine core** *(done)*: guarded loops, gates, store, CLI; proven end-to-end with a fake driver.
- **M1 — live driver** *(done, [evidence](notes/m1-first-light.md), on a toy repo)*: four drivers (`claude`, `agy`, `codex`, `gemini`), fail-loud errors; first real Spec planned, implemented, verified, and validated by the machine.
- **M2 — parallel waves** *(done, [evidence](notes/m2-parallel-waves.md), on a toy repo)*: isolated worktrees, serialized merges + integrated re-verify, conflict retry from updated HEAD; live wave of two concurrent workers.
- **M3 — the doorway** *(done)*: skills ship inside the wheel; `giro install` places them for host discovery; skill resolution honors project overrides; `giro status` doubles as the escalation console.
- **M4 — the upstream** *(done)*: the authoring seam and the one-file-two-zones contract; the `spec`, `plan`, and `grill` skills (later renamed from `giro-`prefixed and cut loose from the CLI); default judged gates in `prompts/review.md` (active) and `prompts/conformance.md` (docs-obedience, commented until docs exist); `CONTEXT.md` in every worker packet — then proven by dogfood: the [`strip-skill-frontmatter`](specs/strip-skill-frontmatter/SPEC.md) Spec was built by giro.
- **M5 — detached runs** *(done, [spec](specs/detached-runs/SPEC.md))*: dispatch a Spec and keep working — every loop in its own worktree off a recorded base branch, `--detach` Runs that survive the session, a Run Ledger, cross-branch `giro status` with machine-readable output, per-Spec locks and a per-checkout worker cap; the `giro` skill became a dispatch console.
- **M6 — the projection** *(done, [spec](specs/github-projection/SPEC.md))*: the story told on GitHub, one-way and fail-soft — tracking issue + sub-issues with live labels and progress, attempt comments as they happen, commit statuses per gate, a draft PR that flips ready when Validate passes, escalations that ping your phone; `giro project` reconciles it all from markdown.
- **M7 — 0.1.0 cut and M8 — events stream** *(done; M8 by hand — its Spec was not run by the engine)*: prompts and skills split into two directories, `command | judge` gate types; the Ledger's events become a versioned public contract (`giro logs --json`).
- **M9 — the documentation phase stands alone** *(done 2026-09-15)*: `grill`/`spec`/`plan` drop the `giro-` prefix and every CLI dependency — they write plain Specs, ADRs, and Issues installable and usable with no giro at all. The engine ingests them: a missing `state:` defaults on read and is stamped at activation, and `giro doctor` reads every artifact the engine's way so a malformed one never reaches a Run.

## Next

- **Open drivers** ([spec](specs/open-drivers/SPEC.md)): define a driver for any CLI in `giro.toml`. `RecipeDriver` was written by hand; its Issues and the config wiring are still open.
- **Per-Spec roster** ([spec](specs/per-spec-roster/SPEC.md)): choose and freeze the roster per Spec. Draft.
