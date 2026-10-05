---
state: done
blocked_by: [04-narration-comments-and-commit-statuses, 05-escalation-surface-and-the-ready-flip]
attempts: 1
---
# The reconciler

One command reconverges GitHub to the markdown store: create what is missing, update what drifted, touch nothing human-owned. It is the Projection's disaster recovery after outages, its answer to hand-meddling, and the operative proof that GitHub is display, not memory.

**Escalation answer (conversation) — 2026-08-11.** The reconciler is on the branch and green (`make check` passes); the earlier findings — the dropped draft→ready flip and dead report branch, the duplicate-surface creation on busy repos, the double-tally on rate-limit retries, and the misspelled "reopend" — are all resolved. Keep the working code; do **not** rewrite it. One blocker remains, and the `review` gate is right: a failed `gh` **read** during convergence is swallowed — Reconcile aborts that part of the surface silently, records it nowhere, and still reports success and exits 0.

**Mandatory change this attempt.** Reconcile must never claim a convergence it did not achieve. Thread every failed `gh` call during reconvergence — **reads included**, not only writes — into the same tally the report is built from, so that (a) any read or write failure makes the final report state the surface was only *partially* reconciled and name what could not be read or written, and (b) the command exits non-zero whenever anything failed, printing the "GitHub now says what the markdown says" success line only when every operation succeeded. Add a test that injects a failing `gh` read mid-convergence and asserts the report flags the gap and the exit is non-zero; never weaken an existing test to reach green. This is fail-soft done right for a user-invoked command: it may not crash, but it must tell the truth about what it did.

## Acceptance criteria

- [ ] Against a repository with no projected surface, Reconcile builds the complete one — parent, sub-issues, labels, states, comments, pull request where the branch exists — equal to what incremental projection would have produced.
- [ ] After injected drift — a hand-closed sub-issue, a deleted label, an edited engine zone — Reconcile restores the store's truth while leaving human-zone prose and human comments untouched.
- [ ] Reconcile run twice is a no-op, and it never writes the store, the branches, or any local state — one direction only.
- [ ] A Run that suffered Projection failures reconciles to the same surface as a Run that had none.
- [ ] Reconcile is safe at every lifecycle point — draft, active, needs-human, done — and reports what it created, updated, and skipped.

## Attempt 1 — gates failed

- review: Axis 2/1 — TallyGh silently drops the draft→ready flip, and the `pr ready` branch of `_describe_write` is dead code, so Reconcile's report can claim zero changes while GitHub visibly changed.
- review: Axis 1 — marker discovery scans an unfiltered, 500-issue window, so on a busy repository Reconcile can fail to find its own surface and create a duplicate parent and sub-issues on every run, breaking AC3's "run twice is a no-op".

## Attempt 2 — gates failed

- review: Axis 1/2 (correctness of the report AC): a write that is retried after a GitHub secondary rate limit is tallied twice — once as `failed`, once as its real kind — so a Reconcile that fully succeeded reports a failure and the CLI exits nonzero.
- review: Axis 2 (code quality): `_describe_change` builds the verb by appending 'd' to the gh subcommand, producing the misspelled 'reopend' in user-facing report output.

## Attempt 3 — gates failed

- review: Axis 2 (swallowed errors) / axis 1 (AC5): a failed `gh` *read* during convergence silently aborts that part of the surface, is recorded nowhere, and Reconcile still reports ok / exits 0 with "GitHub now says what the markdown says".

## Budget exhausted after 3 attempts

- Axis 2 (swallowed errors) / axis 1 (AC5): a failed `gh` *read* during convergence silently aborts that part of the surface, is recorded nowhere, and Reconcile still reports ok / exits 0 with "GitHub now says what the markdown says".

## Attempt 1 — done — all gates green

- worker: Reconcile no longer swallows failed `gh` reads. `TallyGh` now records a failed read as a `Change("failed", "read of <what>: <error>")` — through a new `_describe_read` that names the call the way a human reads it ("comments on issue #7", "the giro:spec issue index", "pull request #3") — gated by a `watch_reads` flag the Reconciler turns on once preflight is past, since a preflight read already answers for itself in its own check. `ReconcileReport` gained a `failures` property spanning repo and spec changes; `ok` is now "every call landed", not "every write landed", so the CLI's existing `EXIT_PROOF if report.ok` exits non-zero on any read or write failure, and `lines()` prints a "partially reconciled — N call(s) failed: <named>" line instead of "GitHub now says what the markdown says". Two tests added: one injects a failing comments read mid-convergence after a hand deletes the Issue's story, and asserts the gap is real, named in the report, absent the success line, and exits 1 through `cli.main`; the other pins the one deliberate carve-out. `make check` passes (ruff clean, 278 tests).
- notes: Design decision worth a reviewer's eye: the `blocked_by` dependency GET is excluded from the read tally by `_is_probe`. It is a feature probe, not a fetch — a repository whose API has no issue dependencies refuses it on every single run, and `_link_blockers` then states the edge in the sub-issue body, so nothing is missing from the surface. Tallying it would make every Reconcile on such a repository claim a gap it does not have and exit non-zero forever, breaking AC3/AC5. `test_the_one_read_that_may_refuse_is_a_feature_probe_and_not_a_gap` pins that, and I verified it fails when the carve-out is removed. No unrelated problems noticed; nothing outside the Issue's slice was touched.
