# CONTEXT.md format

A glossary and nothing else — the project's canonical vocabulary.

```md
# {Project name}

{One or two sentences: what this project is and why it exists.}

## Language

**Order**:
{One or two sentences describing the term.}
_Avoid_: purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: bill, payment request
```

Rules:

- **Be opinionated.** When several words exist for one concept, pick the best and list the rest under `_Avoid_`.
- **Keep definitions tight.** One or two sentences; what it IS, not what it does.
- **Project-specific terms only.** General programming concepts don't belong, however often they appear.
- **No implementation details.** Not a spec, not a scratch pad, not a decision log — those live in Specs and ADRs.
- Group under subheadings only when natural clusters emerge.
