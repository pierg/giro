---
state: done
blocked_by: [02-spec-lifecycle-projection]
attempts: 2
---
# Issue lifecycle projection

Every giro Issue exists on GitHub as a sub-issue of its Spec's parent from the moment it is planned or filed — gap Issues included — and its labels and open/closed state follow the loop with no hands. The parent's native sub-issue progress bar becomes the wave's scoreboard.

## Acceptance criteria

- [ ] Planning N Issues creates N sub-issues under the parent; the parent shows native sub-issue progress.
- [ ] Labels track ready, in-progress, and needs-human; done closes the sub-issue as completed; wontfix closes it as not planned.
- [ ] Blocking edges appear as native issue dependencies where the API offers them, else as a stated line in the sub-issue body.
- [ ] Gap Issues filed by Validate appear as sub-issues when filed, identifiable as gap findings and naming their gate.
- [ ] Sub-issue identifiers live in Issue frontmatter; re-projection and resume never duplicate a sub-issue.
- [ ] A human hand-closing or relabeling a sub-issue never alters engine behaviour — the store is the truth, and the next projection restores it.

## Attempt 1 — gates failed

- review: Axis 1 (Issue fit): the parent↔sub-issue link is written once at creation and never reconverged, so a single failed link permanently orphans an Issue from the parent's progress bar.
- review: Axis 3 (honest tests): nothing tests the parallel wave — the Issue's stated scenario — and the `live=` guard against duplicate sub-issues is exercised by no test that would fail if it were deleted.
- review: Axis 2 (code quality): `_link_blockers` reports "linked natively" for an edge it skipped, so a blocking edge to an unprojected Issue is shown nowhere.

## Attempt 2 — done — all gates green

- worker: Fixed the three review findings on Issue lifecycle projection. (1) The parent↔sub-issue link is now part of the converged surface: a new `_hang_under_parent` reads `GET repos/{repo}/issues/{parent}/sub_issues?per_page=100` once per `project_issues` call and hangs every recorded number the parent is missing, so a link refused fail-soft at creation is repaired at the next projection instead of orphaning the Issue from the progress bar forever; `_create_sub_issue` no longer links, it only records the REST id. (2) `_link_blockers` now tracks which edges were actually hung natively and returns the refs of the ones that were not — an edge whose blocker has no sub-issue number, or whose id lookup fails, is stated in the body instead of vanishing. (3) Added two parallel-wave engine tests: one asserts every `gh` invocation is made from the main thread and that each sub-issue is labelled in-progress before any worker runs and closed only after the work lands; the other refuses the activation projection's creations so the numbers are minted at the wave's claim, then asserts exactly N sub-issues are ever created, each hung once, and every frontier Issue keeps its number through its final state save — that test fails (duplicate sub-issues 102/103) if the `live=` aliasing in `_record_numbers` is removed, and the link-repair test fails if the link goes back to creation-time only, both verified by mutation.
- notes: The sub-issue list read uses a `?per_page=100` query string rather than `-F per_page=100` because `gh api` switches the method to POST as soon as any field parameter is added — worth remembering for later projection slices. The test fake `MintingGh` is now stateful for the parent's sub-issue set, so "projecting twice changes nothing" is exercised at that endpoint rather than assumed. Unrelated observation, not touched: `ruff format` would reformat several pre-existing blocks in src/giro/projection.py from earlier slices (the gate is `ruff check`, which passes).
