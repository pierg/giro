---
blocked_by: [02-tolerant-reading]
---
# The fail-closed boundary in `giro doctor`

## What to build

`giro doctor` gains a section that reads every Spec and Issue the way the engine will, so a malformed or inconsistent artifact is caught before a Run trusts it — not deep inside a loop. A repo with no Specs is ready-to-author, not a failure.

## Acceptance criteria

- The report gains a `specs` section; it is a "must" check for the exit code.
- It fails on an unparseable artifact, an unresolvable `blocked_by`, or a blocking cycle, naming the offender.
- With no Specs it passes with a "none yet" note.
- Valid artifacts pass and the section appears in `--json` output.

## Not in this Issue

Any new validation the engine does not already enforce at run time — this reads artifacts the existing way, earlier.
