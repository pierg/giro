---
state: ready
blocked_by: [02-project-level-recipe-config-and-registry-resolut]
attempts: 0
---
# User-level driver registry

## What to build

An operator defines driver recipes once in a user/machine-level location and uses them from any project without adding them to that project's config. Resolution considers built-in presets, then the user registry, then the project's recipes, last-wins — so a project can override a personal recipe, and a personal recipe can add a CLI the project never mentions.

## Acceptance criteria

- A recipe defined only in the user registry resolves when named by a roster in a project that has no matching project recipe.
- When a name exists in more than one layer, the project recipe wins over the user registry, which wins over a built-in preset.
- A malformed user registry file fails loudly, naming its location, never silently ignored.
- With no user registry present, resolution behaves exactly as project-only resolution does today.

## Not in this Issue

- The chat flow that writes to the registry — Issue 05.
- Per-Spec selection — the `per-spec-roster` Spec.
