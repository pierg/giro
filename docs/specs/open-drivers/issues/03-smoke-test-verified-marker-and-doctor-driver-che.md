---
state: ready
blocked_by: [02-project-level-recipe-config-and-registry-resolut]
attempts: 0
---
# Smoke test, verified marker, and doctor driver check

## What to build

Before a Run trusts a driver, giro proves it honours the envelope contract by round-tripping a canned prompt and checking a parseable envelope returns. A pass is recorded so it isn't repeated needlessly; changing the recipe invalidates the record. `giro doctor` reports which drivers are runnable here and re-checks one on demand, and preflight refuses a driver that has never passed or whose recipe changed since it last passed. Built-in presets are trusted and need only PATH presence.

## Acceptance criteria

- Checking a single driver spawns it with a fixed prompt and reports pass when a parseable envelope returns, fail with the reason when it does not.
- A passing check records a marker tied to the recipe's content; changing any recipe field invalidates that marker.
- `giro doctor` lists each roster driver's runnability — on PATH, and verified where verification applies — in both human and JSON output.
- Preflight refuses a Run whose recipe-defined driver has no current verification, pointing at the doctor check, before git is touched.
- A built-in preset runs on PATH presence alone, so upgrading changes nothing for existing users.

## Not in this Issue

- Defining recipes or resolving a name — Issue 02.
- The chat flow that writes and verifies a new recipe — Issue 05.
