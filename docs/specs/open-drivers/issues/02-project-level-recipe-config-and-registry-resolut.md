---
state: ready
blocked_by: [01-generic-recipe-driven-driver]
attempts: 0
---
# Project-level recipe config and registry resolution

## What to build

An operator declares an agent CLI as a `[drivers.<name>]` recipe in the project config and names it in the roster; giro resolves that name — across the built-in presets and the project's own recipes, last-wins — and builds the generic driver for it. A name that resolves to nothing is refused before any work starts. The generated config starter documents the recipe form.

## Acceptance criteria

- A `[drivers.<name>]` recipe with the recognized fields parses; a stray field warns and is ignored, matching how the rest of the config treats unknown keys.
- A roster role naming a project-defined driver resolves to a working driver built from that recipe.
- A project recipe whose name matches a built-in preset overrides that preset.
- A roster role naming neither a preset nor a project recipe is refused loudly at preflight, naming the unknown driver, before git is touched.
- The generated config starter shows a commented recipe example.

## Not in this Issue

- The user/machine-level registry layer — Issue 04.
- Smoke-testing a resolved driver — Issue 03.
- Per-Spec or profile selection — the `per-spec-roster` Spec.
