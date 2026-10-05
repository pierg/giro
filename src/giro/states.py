"""State sets for Issues and Specs.

Deliberately small — exactly what the loops branch on. ``blocked`` is not a
stored state: it is computed from ``blocked_by`` at scheduling time.
``wontfix`` is a human-set terminal state; the engine respects it but never
sets it. ``done`` is terminal for both machines — the engine never schedules
or re-validates a target that is already ``done``.

The engine is the single writer of these fields and its transitions are
exercised end-to-end in ``tests/`` against the ``FakeDriver``; there is no
separate transition guard to keep in sync.
"""

from __future__ import annotations

ISSUE_STATES = frozenset({"ready", "in-progress", "done", "needs-human", "wontfix"})
SPEC_STATES = frozenset({"draft", "active", "done", "needs-human"})

ISSUE_TERMINAL = frozenset({"done", "wontfix"})
SPEC_TERMINAL = frozenset({"done"})

# The state an artifact holds before the engine has ever written it. A Spec or
# Issue authored without giro (by the `spec`/`plan` skills, or by hand) carries
# no lifecycle frontmatter; the reader defaults a missing ``state`` to these,
# and the engine stamps the real value when the Spec activates. A *present but
# invalid* state is still a loud error — this defaults absence, never garbage.
SPEC_INITIAL = "draft"
ISSUE_INITIAL = "ready"
