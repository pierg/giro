# Docs change in conversation; loops obey them

`CONTEXT.md` and `docs/adr/` are written upstream (the `grill` skill), forbidden to workers, and enforced per attempt by the `conformance` gate. A gate is never passed by rewriting what it checks against: a suspected stale ADR fails with a human-decision finding and resolves through escalation, then re-run. Default gates are visible starter config, never hidden engine behavior.
