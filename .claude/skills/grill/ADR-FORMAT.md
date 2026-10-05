# ADR format

ADRs live in `docs/adr/` as `NNNN-slug.md` — take the next free number in the directory (the highest existing `NNNN` plus one, zero-padded to four digits).

```md
# {Short title of the decision}

{1–3 sentences: the context, what was decided, and why.}
```

That's a complete ADR. The value is recording *that* a decision was made and *why* — not filling out sections. Add more only when it genuinely earns its place:

- **Considered options** — only when the rejected alternatives are worth remembering (otherwise someone re-proposes GraphQL in six months).
- **Consequences** — only for non-obvious downstream effects.
- **Superseded by ADR-NNNN** — a single line at the top when a decision is revisited; never rewrite history.

What qualifies (all three of: hard to reverse, surprising without context, a real trade-off): architectural shape, integration patterns, technology choices with lock-in, boundary and scope decisions ("the explicit no-s are as valuable as the yes-s"), deliberate deviations from the obvious path, constraints invisible in the code.
