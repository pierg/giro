---
name: grill
description: Relentlessly interview to sharpen a fuzzy plan or design before it becomes a spec — recording vocabulary in CONTEXT.md and load-bearing decisions as ADRs as they crystallise. Use when someone wants to stress-test their thinking, says "grill me", or intent is too fuzzy to write a spec yet.
---

# grill — sharpen intent, leave durable docs behind

Interview the user relentlessly until you reach a shared understanding — and capture what crystallises: vocabulary into `CONTEXT.md`, load-bearing decisions into `docs/adr/`. This happens in conversation, always upstream of any build. These docs are the source of truth: whatever implements the work afterwards obeys them.

## The interview: a design tree, worked in frontier rounds

Map the design as a tree — every decision branches into the decisions that hang off it. The **frontier** is every question you can ask *now* without guessing at unheard answers. Ask the whole frontier in one round, numbered, each with your recommended answer:

```
❓ **Q1** — **<question title>**: <question body, options included>

➡️ <your recommended answer>
```

Each round of answers reshapes the tree and pushes the frontier outward. A question that depends on another question still open this round belongs to a later round. Finding *facts* is your job, never the user's — look things up in the repo yourself; only *decisions* go to the user. The session ends when the frontier is empty: nothing left silently assumed.

## Capture as you go — never batch

### Vocabulary → CONTEXT.md

- When the user's term conflicts with the glossary, call it out immediately: "CONTEXT.md defines *cancellation* as X, but you seem to mean Y — which is it?"
- When language is vague or overloaded, propose one precise canonical term and list the losers under `_Avoid_`.
- Stress-test relationships with concrete edge-case scenarios; check claims against the code and surface contradictions.
- Update `CONTEXT.md` the moment a term settles, using [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md). Glossary only — no implementation details, ever. Create the file lazily, when the first term resolves.

### Decisions → ADRs, sparingly

Offer an ADR only when **all three** hold: hard to reverse · surprising without context · the result of a real trade-off. If any is missing, skip it — spec-scoped decisions belong in the spec's *Implementation Decisions* section instead, and an ADR belongs to no spec: it outlives them all.

Create `docs/adr/NNNN-slug.md`, taking the next free number in the directory (highest `NNNN` plus one), then write the body per [ADR-FORMAT.md](./ADR-FORMAT.md) — one paragraph is a complete ADR.

## When the session ends

- Fuzzy → settled? Point at the `spec` skill: the settled understanding becomes a spec.
- The docs you just wrote are now the source of truth. Anything built from here must respect the vocabulary in `CONTEXT.md` and the decisions in `docs/adr/`, and must not silently edit them: a vocabulary or decision shift comes *back* through this skill, not through the implementation.
- A decision later found to rest on a **stale ADR**? This is where it gets resolved: grill the decision again, update or supersede the ADR, then continue the work.
