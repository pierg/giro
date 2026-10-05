---
state: ready
blocked_by: []
attempts: 0
---
# Generic recipe-driven Driver

## What to build

A driver that spawns an agent CLI purely from a declarative recipe — its command, how the prompt is delivered, the model-flag template, and optional envelope handling — satisfying the same contract the four built-in drivers already meet: prompt in, one JSON envelope out. It covers all three prompt-delivery modes (stdin, a trailing positional dash, a prompt file), substitutes the chosen model into the model-flag template, returns the last JSON object as the envelope, and optionally unwraps a named key or surfaces a named error key as a failure.

## Acceptance criteria

- A stdin-delivery recipe passes the whole prompt on stdin and never on the command line, at any prompt size.
- A positional-dash recipe passes the prompt via a trailing `-` on stdin; a file recipe writes the prompt to a temporary file, passes its path, and removes it afterward.
- The model-flag template substitutes the selected model; an empty template omits the model flag entirely.
- The last JSON object in the CLI's output is returned; with an unwrap key set, the envelope is read from that key's text.
- With an error key set and truthy in the output, the driver fails with that payload's message rather than treating it as work.
- A CLI missing from PATH, or one that exceeds the context timeout, fails with the CLI's own message, never a bare exit code.

## Not in this Issue

- Reading recipes from config or resolving a driver name — Issue 02.
- The smoke test and verified marker — Issue 03.
- Retiring the four built-in driver subclasses — out of scope for this Spec.
