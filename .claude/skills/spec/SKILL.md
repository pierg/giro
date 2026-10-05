---
name: spec
description: Turn the current conversation into a spec — synthesize what was discussed, sketch the test seams, then write docs/specs/<slug>/SPEC.md. Use when a discussed feature should be captured as a spec ready to build.
---

# spec — from conversation to artifact

Turn what has already been discussed into one spec. Do NOT interview the user — synthesize what you already know. If intent is still fuzzy, that is a job for the `grill` skill, not a spec to write yet.

## Process

1. **Explore** the repo if you haven't. Use the project's domain vocabulary throughout, and respect any ADRs and glossary terms (`CONTEXT.md`) in the area you're touching.
2. **Sketch the seams** at which the feature will be tested. Prefer existing seams to new ones; propose new ones at the highest point you can — the fewer seams the better, and the ideal number is one. Check the seams with the user before writing.
3. **Create the file**: write `docs/specs/<slug>/SPEC.md`, where `<slug>` is a short kebab-case name for the feature. It is plain markdown — the body below is the whole file; add no status or lifecycle frontmatter.
4. **Write the body** using the template below, replacing the placeholder text.
5. **Hand off**: the spec is ready. Decompose it into Issues with the `plan` skill, or implement it however you choose.

## Body template

<spec-template>

## Problem Statement

The problem the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A numbered list of user stories, each in the form: As an <actor>, I want <feature>, so that <benefit>. Extensive — cover every aspect of the feature.

## Shape

A small diagram of the moving parts and the flow between them — boxes named by role from the project's vocabulary, never by file path. Draw it with whatever the project already uses (Mermaid, an SVG, a drawing tool's export, plain text); a diagram that is its own file lives in the spec's folder, beside `SPEC.md`, and is linked from here.

## Implementation Decisions

Decisions already made: modules to build or modify, interfaces, schema changes, API contracts, architectural choices, technical clarifications from the developer.

## Testing Decisions

What makes a good test here (external behavior, never implementation details), which modules get tested, and prior art — similar tests already in the codebase.

## Docs Impact

Which living docs this spec obsoletes or extends — design doc sections, README claims, figures — and what must change in each. "None" is an honest entry; silence is not. Never list CONTEXT.md or ADRs here: the implementation must not edit them — vocabulary and decision shifts go back through the `grill` skill.

## Out of Scope

What this spec deliberately does not cover.

</spec-template>

## Writing rules

- **Lean beats thorough-sounding.** A spec is re-read by every fresh implementer context and every reviewer — each paragraph costs. A decision is a bullet with a one-line why; when a section grows into a prose wall, move its weight into the Shape diagram or an example.
- **A contract gets an example.** If the spec defines anything machine-readable — a schema, an event, an API shape — show one trimmed example instance. Prose alone cannot carry a contract.
