---
state: ready
blocked_by: [02-project-level-recipe-config-and-registry-resolut, 03-smoke-test-verified-marker-and-doctor-driver-che, 04-user-level-driver-registry]
attempts: 0
---
# Docs DESIGN and README

## What to build

Update the living design and readme docs to describe open drivers as they now work: the design doc's Driver vocabulary and roster configuration describe the recipe form and the built-in → user → project resolution order, its decisions list references the open-driver ADR, and the readme's supported-agents framing becomes "built-in presets plus define your own," with a note in its safety section that a user-defined worker driver runs an arbitrary command under the granted bypass flag.

## Acceptance criteria

- The design doc describes a driver as a recipe with its fields and the built-in → user → project resolution order, and no longer implies a fixed set of four drivers.
- The open-driver ADR appears in the design doc's decisions list.
- The readme presents defining a custom driver as a first-class path and carries the safety note about arbitrary-command execution.
- No claim anywhere states the driver set is limited to the four built-ins.

## Not in this Issue

- CONTEXT.md or the ADR files themselves — those are set through conversation, not by workers.
- The config starter's recipe example — Issue 02.
