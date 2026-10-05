---
state: done
blocked_by: []
attempts: 1
---
# Projection plumbing

The substrate every later slice stands on: Projection configuration, token loading from the project's git-ignored env file, a fail-soft `gh` runner with timeouts, preflight that downgrades loudly instead of blocking, an ensured label set, and a check command that proves a repository is ready before any Run relies on it.

## Acceptance criteria

- [ ] Projection is off by default; enabling it names the repository explicitly or autodetects it from the origin remote.
- [ ] GITHUB_TOKEN is read from the project's git-ignored env file and reaches `gh` only through the subprocess environment; with no token, `gh`'s own login is the fallback; with neither, preflight downgrades the Run to projection-off with a loud warning.
- [ ] Every `gh` invocation carries a timeout; a failing or hanging call records a Ledger event and never raises into the loop.
- [ ] Preflight verifies the `gh` binary, auth, and repository reachability, and ensures the `giro:` label set exists — idempotently, with stable colors.
- [ ] `giro project --check` reports readiness in human- and machine-readable form: exit 0 ready, nonzero not.
- [ ] Strict mode makes preflight failures fatal, for operators who want fail-closed.
- [ ] With Projection off, engine behaviour and outputs are identical to today's.

## Attempt 1 — done — all gates green

- worker: Added the Projection substrate: a new `src/giro/projection.py` (fail-soft timed `gh` runner, token loading, preflight, the `giro:` label set, `FakeGh` scripted boundary), a `[projection]` config section (off by default, repo explicit or autodetected from the origin remote, strict, timeout), `Workspace.remote_url`, a `projection` Ledger event type, Engine wiring that preflights inside the Run and downgrades loudly, and `giro project --check` in human and `--json` form. 19 new tests in tests/test_projection.py cover every acceptance criterion; the full suite (183 tests) and `ruff` pass, and a live smoke against real `gh` with an unreachable repository produced the expected not-ready report and exit 1.
- notes: Design choices worth knowing: (1) the token is read from `<invoking checkout>/.env` (the `.env` convention README/design.md already name as the project's git-ignored file) and reaches `gh` only via the subprocess environment — asserted by test that argv never carries it; process-env GITHUB_TOKEN is the second source, `gh auth login` the third. (2) `giro project --check` runs the checks even when Projection is off — so an operator can prove the repository before enabling — but exits nonzero in that case, saying which of the two is missing. (3) Bare `giro project` errors with 'needs --check'; the bare verb is left free for Reconcile (Issue 06). (4) Preflight stops at the first failed check and marks the rest 'skipped', so a missing `gh` doesn't produce four failures. Out-of-scope observations, not fixed: nothing verifies that `.env` is actually git-ignored — a preflight warning when the token file is tracked would be cheap insurance against a committed token; and README/docs/design.md still describe M6 as planned, which is conversation-owned per ADR-0010.
