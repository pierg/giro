---
state: done
blocked_by: [03-issue-lifecycle-projection]
attempts: 1
---
# Narration comments and commit statuses

The Run's story, told where humans read. Every log section the engine appends to an Issue — attempt done, gates failed, errored, budget exhausted, integrated — lands verbatim as one comment on that Issue's sub-issue. Orchestration moments — plan complete, wave start, Validate verdict, gap cycle — comment on the parent. Integrated Verify and Validate verdicts become commit statuses on the pull-request head, so all-green is literally green.

## Acceptance criteria

- [ ] Each appended Issue log section arrives as exactly one comment on the right sub-issue, carrying a hidden marker so re-projection updates instead of duplicating.
- [ ] Parent-issue comments cover plan completion, wave starts, Validate verdicts, and gap-cycle filings.
- [ ] Commit statuses on the pull-request head report integrated Verify and Validate per gate, pass and fail.
- [ ] Nothing posts mid-attempt: comments carry engine-observed facts — envelope outcomes, verdicts, findings — with the worker's own summary confined to its attempt comment.
- [ ] Comment volume is bounded by the budgets; a secondary-rate-limit response retries once, then drops fail-soft with a Ledger event.

## Attempt 1 — done — all gates green

- worker: Narration comments and commit statuses are implemented and tested. Every appended Issue log section becomes exactly one marked comment on its sub-issue (found by hidden marker, edited rather than duplicated, restored when a human edits it, human comments never touched); orchestration moments — plan completion, wave start, Validate verdict, gap-cycle filing — become marked comments on the parent tracking issue; integrated Verify and Validate verdicts become per-gate commit statuses (giro/verify/<gate>, giro/validate/<gate>, pass and fail) on the head as it was pushed. Comment volume is bounded by the attempt budget (asserted: 3 attempts + 1 escalation = 4 comments), a secondary-rate-limit response retries once after a pause and then drops fail-soft with a Ledger event, and a refused comment endpoint leaves the Run's outcome, markdown, and exit path identical. 19 new tests in tests/test_projection_narration.py; full suite 237 passed, ruff clean.
- notes: Design points worth knowing downstream: (1) The Issue markdown now carries a hidden `<!-- giro:log -->` fence written at the first append_log — that is what separates conversation-owned body from engine-appended log, and it is what log_sections() reads. An Issue file that already had log sections before this slice has no fence, so its earlier sections project no comments until the next section is appended (which lays the fence); giro's own dogfood issue files are in that state. (2) Parent narration comment keys include the Ledger's run id (`<run>:<slug>:<key>`), because wave and validate counters restart per Run and would otherwise overwrite an earlier Run's comments. Consequence for slice 06 (Reconcile): parent narration comments cannot be recomputed from the store alone — Reconcile should leave marked comments whose keys it cannot derive alone rather than delete them. Sub-issue log comments are fully store-derived (`<slug>/<issue>#<n>`) and are reconcilable. (3) Statuses are posted only when the checkpoint's push succeeded, and in the parallel wave only when the merge actually stuck — a reset merge judged nothing that is on the published head. (4) `run_gates` is now unused by loops.py (still used by cli.py's `giro verify`); `_integrate_branch` returns an `Integration` record instead of a tuple. Unrelated, not touched: as noted in slice 03, `ruff format` would reformat several pre-existing multi-arg `gh` call blocks in projection.py; the gate is `ruff check`, which passes.
