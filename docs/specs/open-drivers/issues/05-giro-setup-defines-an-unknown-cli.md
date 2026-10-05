---
state: ready
blocked_by: [03-smoke-test-verified-marker-and-doctor-driver-che, 04-user-level-driver-registry]
attempts: 0
---
# giro-setup defines an unknown CLI

## What to build

When an operator names an agent CLI giro doesn't recognize, the setup skill guides them to a working recipe: it proposes a best-guess recipe from the CLI's own help output, confirms the contract answers with them (how the prompt is delivered, whether it prints the envelope as JSON, the model flag), writes the recipe to the user registry by default, and verifies it with the driver check before handing back. It offers — but does not default to — committing the recipe to the project for CI.

## Acceptance criteria

- Naming an unrecognized CLI leads to a proposed recipe and an explicit confirmation of the contract answers, never a silent guess.
- The confirmed recipe is written to the user registry and verified by the driver check before the flow completes; a recipe that fails verification is not left usable.
- Committing the recipe to the project is offered as the choice for CI reproducibility, not the default.
- The skill states plainly that a worker driver runs an arbitrary command under the write-bypass flag the operator grants.

## Not in this Issue

- The engine mechanics of recipes, resolution, or the driver check — Issues 01–04.
- The dispatch console's handling of a driver preference at implement time — the `per-spec-roster` Spec.
