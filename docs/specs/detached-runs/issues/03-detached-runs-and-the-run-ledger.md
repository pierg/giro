---
state: done
blocked_by: [02-dispatch-safety-and-cross-branch-status]
attempts: 1
---
# Detached runs and the run ledger

`giro implement --detach` returns at once with a Run identifier; the loop continues in a background process that survives the invoking shell and session. Every Run — foreground or detached — writes the Ledger: liveness, an append-only event stream of state-changing moments, and the final outcome. New porcelain lists Runs and follows a live one. A global cap bounds concurrent worker contexts across all Runs.

**Escalation answer (conversation) — 2026-08-11.** The implementation is on the branch and green: `make check` passes 152 tests (ruff clean), the global-cap TOCTOU race from attempt 2 is fixed, and the run-id/Ledger-collision and dead-code findings from attempt 1 are resolved. Keep the existing `runs.py` / `ledger.py` modules and their tests — do **not** rewrite working code. One blocker remains, and the `review` gate is correct about it: nothing in the diff would fail if `--detach` stopped detaching.

**Mandatory change this attempt.** Add a genuine, process-level detachment test — the Spec's Testing Decisions ask for exactly this ("dispatch, outlive the parent shell, read the outcome from the Ledger"). Dispatch a `--detach` Run backed by the FakeDriver whose worker blocks long enough that the dispatching call must return first; then assert (a) the dispatch call returns promptly with the Run id *while the Ledger still shows the Run live/in-progress*, and (b) the Run later reaches its terminal outcome in the Ledger after the parent process has already returned. The test must fail against a blocking/synchronous implementation — guarantee that by construction (a synchronous `implement` cannot return before its outcome is written). If a seam is needed to make detachment observable without a flaky real-fork race, add the minimal one; never weaken an existing test to reach green.

## Acceptance criteria

- [ ] Dispatch prints the Run identifier and returns promptly; the Run keeps going after the invoking shell exits, and its outcome lands durably in the Ledger.
- [ ] Every state-changing moment appends one event — activation, plan, wave start, claim, attempt end (outcome, verdicts, findings), merge, gap cycle, escalation, exit — for foreground and detached Runs alike.
- [ ] `giro runs` lists live and recent Runs with target, liveness, and phase; `giro logs` follows a live Run's story and prints a finished Run's tail.
- [ ] `giro status` overlays Ledger liveness: a Spec with a live Run says so, with the current wave and attempt.
- [ ] The global worker cap holds across two simultaneous Runs — a Run waits for a slot instead of exceeding the cap — and is configurable.
- [ ] Foreground exit codes are unchanged; a detached Run's outcome is readable after the fact with no live process.
- [ ] Deleting a finished Run's Ledger loses nothing durable: resume, status, and re-invoke still work from markdown alone.

## Attempt 1 — gates failed

- review: Axis 2 (code quality): the global worker-slot reclaim can unlink a slot a live worker is holding, so the cap the Issue introduces can be exceeded.
- review: Axis 2 (code quality): `giro logs --follow` can exit without printing the Run's final events, including `exit` — the story can end without its ending, and the test asserting it is racy.
- review: Axis 2 (code quality): `Engine.implement`'s `run_id` parameter is dead — the Ledger is opened in two places and only the CLI's path ever runs.
- review: Axis 2 (code quality): `new_run_id` does not deliver the uniqueness its docstring claims, so two Runs can silently share one Ledger directory.

## Attempt 2 — gates failed

- review: Axis 2 (code quality / correctness): the stale-slot reclaim path in `_take_slot` is a TOCTOU race that can put two live worker contexts in one slot, breaking the very global cap the module exists to enforce.

## Attempt 3 — gates failed

- review: Axis 3 (honest tests): no test in the diff would fail if `--detach` stopped detaching — the dispatch tests pass equally well against a blocking implementation.

## Budget exhausted after 3 attempts

- Axis 3 (honest tests): no test in the diff would fail if `--detach` stopped detaching — the dispatch tests pass equally well against a blocking implementation.

## Attempt 1 — done — all gates green

- worker: Added the mandated process-level detachment test to tests/test_detach.py: a blocking stub agent (releases only via a file the test touches after dispatch returns) proves `giro implement --detach` hands back the Run id while the Ledger still shows the Run live and unfinished, that the Run advances on its own after its caller returned (the `claim` event lands with the parent gone), and that it reaches its terminal outcome in the Ledger once released. Verified honest by mutation: with `cmd_dispatch` temporarily made synchronous, the new test fails (TimeoutExpired) while the four pre-existing detach tests still pass — precisely the gap the review reported; cli.py was then restored byte-identically. No existing code or tests were rewritten or weakened. make check: ruff clean, 153 tests passed; the new test ran 5x with no flakiness (~1s each).
- notes: Refactored the two stub-agent fixtures onto a shared `agent_on_path` helper and factored the Ledger-reading helpers in test_detach.py (`run_dir`, `read_record`, `console`, plus a new `wait_for_event` that fails loudly with the Run's console if the Run ends before the awaited event). No unrelated problems observed elsewhere in the diff.
