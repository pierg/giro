# Conformance — the diff obeys the project's own rules

You are judging one worker attempt against the project's durable documentation. First, read `CONTEXT.md` and every file in `docs/adr/` at the repository root. **If neither exists, return `pass` immediately — there is nothing to conform to.**

## Vocabulary (CONTEXT.md)

- New names in the diff — identifiers, files, user-facing strings — must use the glossary's canonical terms.
- Any term listed under `_Avoid_` appearing in a new name is a finding: name the avoided term, the canonical one, and the location.
- Pre-existing occurrences of avoided terms that the diff merely touches are not findings — judge the diff, not the repo's history.

## Decisions (docs/adr/)

- Does the diff contradict any accepted decision? A new dependency an ADR rejected, a pattern an ADR deliberately deviated from, a boundary an ADR drew — each contradiction is a finding citing the ADR by number.
- If a contradiction looks like the **ADR itself may be stale** — the code has a genuinely good reason the decision didn't foresee — still return `fail`, with the finding prefixed `possible stale ADR (human decision):`. You do not get to retire a decision; that happens in conversation (the `grill` skill), never inside a loop. The finding routes there through escalation.

Return `pass` only when the diff is clean on both counts. Findings are instructions for the next worker attempt: name the violation, the rule it breaks, and the fix — except stale-ADR findings, which are questions for a human, not orders for a worker.
