---
name: plan
description: Decompose a spec into tracer-bullet Issues with blocking edges — propose the breakdown, quiz the user, then write each approved slice as docs/specs/<slug>/issues/NN-slug.md. Use when a spec should be sliced into Issues by hand.
---

# plan — from spec to Issues

Break one spec into **tracer-bullet Issues** — thin vertical slices, each declaring the Issues that block it. Your job is the judgment of slices and blocking edges, not scheduling or ordering labels.

## Slicing judgment

<vertical-slice-rules>

- Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI, tests) — vertical, never a horizontal slice of one layer.
- A completed slice is demoable or verifiable on its own.
- Each slice is sized to fit one fresh implementer context.
- Any prefactoring is its own slice, first: make the change easy, then make the easy change.

</vertical-slice-rules>

Give each slice its **blocking edges** — the slices that must complete before it can start. A slice with no blockers can start immediately, and unblocked slices can run in parallel. Never form a cycle.

**Wide refactors are the exception to vertical slicing.** One mechanical change whose blast radius fans across the codebase — a rename, a retype — cannot land green as a tracer bullet. Sequence it as **expand–contract**: an *expand* Issue adds the new form beside the old; *migrate* Issues move call sites over in batches (each blocked by the expand); a *contract* Issue deletes the old form, blocked by every migrate. The migrate slices can proceed in parallel; the contract waits until they all land.

## Issue bodies

An Issue body is a work order, not an essay — exactly these three sections, kept lean:

<issue-body-template>

## What to build

The end-to-end behaviour of this slice, from the user's perspective. Behaviour only — the implementer binds it to the actual codebase at build time.

## Acceptance criteria

A checklist of observable outcomes a reviewer can check — external behaviour, never implementation details.

## Not in this Issue

Adjacent work this slice must refuse to absorb, and the Issue or spec where it lives instead.

</issue-body-template>

No file paths or code snippets — the `spec` skill's writing rules apply here too.

## Process

1. **Read the spec** at `docs/specs/<slug>/SPEC.md`, and whatever is already in the conversation.
2. **Explore the codebase** so Issue titles and bodies use the project's vocabulary and respect its ADRs.
3. **Draft the slices** per the judgment above.
4. **Quiz the user.** Present the breakdown as a numbered list — title, blocked by, what it delivers — and ask: does the granularity feel right? Are the edges real dependencies? Merge or split anything? Iterate until approved.
5. **Create the files**, in dependency order. Each approved slice becomes `docs/specs/<slug>/issues/NN-slug.md`, numbered `01`, `02`, … in the order you list them, with a slug from the title. Plain markdown: the three-section body above, preceded only by a `blocked_by` frontmatter list naming the Issue ids it waits on:

   ```md
   ---
   blocked_by: [01-prefactor-parser]
   ---
   # Wire the new parser into the CLI

   ## What to build
   ...
   ```

   A slice with no blockers needs no frontmatter at all (or `blocked_by: []`). These files are documents — add only `blocked_by` when needed, never lifecycle status or execution metadata.
