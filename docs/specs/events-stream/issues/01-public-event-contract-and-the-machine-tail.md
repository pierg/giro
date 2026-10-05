---
state: ready
blocked_by: []
attempts: 0
---
# Public event contract and the machine tail

Turn the Ledger's `events.jsonl` from an internal artefact into a public, versioned contract, and give a machine tail that consumers can point at any Run — live or finished, foreground or detached, from any branch.

## What to build

- **Every event carries `schema_version = 1`** as a top-level field, alongside the existing `seq`, `at`, `type`. The value is a constant in the emitter; a bump is a semver-shaped signal that the contract changed.
- **A new "Events" subsection under Runs in `docs/design.md`** names every event `type` the loops emit, its required and optional fields, and the invariants: append-only, one per state-changing moment, monotonically increasing `seq` across the whole Run, main-thread-only writer.
- **A doc-drift test** walks a scripted end-to-end fixture Run through every event `type` (activation, plan, wave, claim, attempt with verdicts and findings, merge, gap-cycle, escalation, projection, exit), collects each event, and asserts that every `type` and every field name that appeared is named in the design-doc subsection. Adding an undocumented event type or field fails the build.
- **`giro logs --json`** prints the raw JSONL — one JSON object per line, ordered by `seq` — instead of the human rendering. It honours the same `-n`/`--lines` semantics (`0` = every event, default keeps the human default), the same `--follow` behaviour (streams as events land, exits when the Run does), and the same run-id resolution (a named id, or omitted = the newest Run).
- **The `giro` doorway skill** learns the machine surface in one short paragraph: `giro logs --json` is the JSONL tail; primary contract remains `giro status` and the final report.

## Acceptance criteria

- [ ] Every event in a fresh Run's `.giro/runs/<id>/events.jsonl` carries `schema_version = 1`, across every `type` and both foreground and detached Runs.
- [ ] `docs/design.md` gains an "Events" subsection under Runs naming every emitted `type`, every field each carries, and the ordering / writer invariants.
- [ ] A test walks a fixture Run that produces every event `type` and asserts each `type` and every field name that appeared is documented; the test fails when an event or field is added without a doc update.
- [ ] `giro logs <run-id> --json` on a finished Run prints valid JSONL, one event per line, in `seq` order, with no other stdout content.
- [ ] `giro logs <run-id> --json --follow` on a live Run streams events as they land and exits when the Run does.
- [ ] `giro logs --json` (no run-id) resolves to the newest Run, same as human mode.
- [ ] `giro logs` without `--json` prints byte-identical output to today (existing tests remain green).
- [ ] The `giro` doorway skill (`skills/giro/SKILL.md`) has a short note that `--json` is the machine tail, without changing its primary contract.
- [ ] The full test suite and `ruff` pass; `make check` is green.

## Not in this Issue

- Phase lines on stderr and `--quiet` on `implement` — that is Issue 02.
- Any `--events` flag on `giro implement` — deliberately out of scope for the whole Spec.
- Backward-compat handling for events with no `schema_version`: a consumer that meets one treats it as pre-1; the engine emits `1` from now on.
- Changing the Ledger's on-disk format, path, or the `.giro/runs/` layout.
