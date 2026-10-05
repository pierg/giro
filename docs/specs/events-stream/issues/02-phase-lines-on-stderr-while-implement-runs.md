---
state: ready
blocked_by: []
attempts: 0
---
# Phase lines on stderr while implement runs

Give a human watching `giro implement` in a terminal a sense of what the loop is doing while it runs, without touching the machine surface — the final report, the exit code, or the Ledger.

## What to build

- **Short human-readable phase lines on stderr** while `giro implement` runs, emitted from the loop at the same triggers the event emitter fires: claim, worker start / end, each gate (with its verdict), state transitions, wave merge. One line per moment; readable at a glance; never JSON.
- **The trigger set matches the event emitter's**, so a phase line is a sibling to the event, not a consumer of it: silence in one path never means silence in the other. Adding a new event type in the future should be a natural place to add a phase line, though it is not required to.
- **A new `--quiet` flag on `giro implement`** suppresses phase lines. It does not touch stdout (the final report), does not touch the Ledger's on-disk events, and does not change exit codes.
- **Both foreground and detached Runs** produce phase lines the same way — a dispatched Run's stderr is captured to `.giro/runs/<id>/console.log` as it already is, so the same phase lines land there for later reading.

## Acceptance criteria

- [ ] Running `giro implement <target>` in the foreground prints at least one stderr phase line for each of: the claim, the worker start, each verify gate (naming the gate and its verdict), each state transition observed, and each wave merge (when `concurrency > 1`).
- [ ] Phase lines are short, human-readable, one per moment, and never JSON.
- [ ] `giro implement <target> --quiet` produces no stderr phase lines; the final stdout report and the exit code are byte-identical to the noisy run.
- [ ] `giro implement` without `--quiet` produces stdout byte-identical to today (existing tests remain green).
- [ ] A dispatched Run (`--detach`) writes the same phase lines into its Ledger's `console.log` as a foreground Run would print to stderr.
- [ ] `giro implement --detach --quiet` is accepted; the dispatched Run's `console.log` carries no phase lines.
- [ ] The full test suite and `ruff` pass; `make check` is green.

## Not in this Issue

- The machine surface (`schema_version`, doc, drift test, `giro logs --json`) — that is Issue 01.
- Any `--events` flag on `giro implement` — deliberately out of scope for the whole Spec.
- Structured / JSON phase lines — phase lines are for humans, and the machine surface is the JSONL stream.
- `## Delivered` digest sections on Issues and Specs when work lands (a second slice that was split off and dropped). If wanted later, that is its own Spec — this Issue stays about presence during a Run.
