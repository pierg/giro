# Planning — slicing judgment

Break one Spec into **tracer-bullet Issues** — thin vertical slices, each declaring the Issues that block it. The engine schedules the frontier itself; your job is the judgment, not the ordering labels.

## Slicing judgment

<vertical-slice-rules>

- Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI, tests) — vertical, never a horizontal slice of one layer.
- A completed slice is demoable or verifiable on its own.
- Each slice is sized to fit one fresh worker context — that is exactly what the engine will give it.
- Any prefactoring is its own slice, first: make the change easy, then make the easy change.

</vertical-slice-rules>

Give each slice its **blocking edges** — the slices that must complete before it can start. A slice with no blockers can start immediately, and the engine runs unblocked slices in parallel when concurrency allows. Never form a cycle.

**Wide refactors are the exception to vertical slicing.** One mechanical change whose blast radius fans across the codebase — a rename, a retype — cannot land green as a tracer bullet. Sequence it as **expand–contract**: an *expand* Issue adds the new form beside the old; *migrate* Issues move call sites over in batches (each `--blocked-by` the expand); a *contract* Issue deletes the old form, blocked by every migrate. The engine's waves run the migrates in parallel and hold the contract until they all land.

## Issue bodies

An Issue body is a work order, not an essay — exactly these three sections, kept lean:

<issue-body-template>

## What to build

The end-to-end behaviour of this slice, from the user's perspective. Behaviour only — the worker binds it to the actual codebase at attempt time.

## Acceptance criteria

A checklist of observable outcomes a gate can check — external behaviour, never implementation details.

## Not in this Issue

Adjacent work this slice must refuse to absorb, and the Issue or Spec where it lives instead.

</issue-body-template>

No file paths or code snippets.
