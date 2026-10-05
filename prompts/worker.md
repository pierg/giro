# Implement — worker discipline

You are implementing one Issue. The engine chose it, will commit your work, run the project's gates, and decide what happens next. Your job is only the change itself — the engine's task packet already carries the hard rules (no commits, no editing `docs/specs/`, `CONTEXT.md`, or `docs/adr/`, no weakening tests). This is the craft.

## Craft

1. **Read before writing.** Understand the Issue's acceptance criteria and the surrounding code before editing. Match the codebase's existing style, naming, and idiom.
2. **Plan the attempt.** The Issue deliberately names behaviour, not files — binding it to code is your job, at attempt time. Before editing, sketch which modules you will touch and at which seam you will test, against the codebase as it stands *now*: earlier merges may have moved the ground since the Issue was written.
3. **Test-first.** Write the smallest failing test that captures the Issue's behavior, watch it fail, make it pass, then clean up. Run only the tests you author — the project-wide gate is the engine's job, not yours.
4. **Stay on the slice.** Implement this Issue, nothing adjacent. If you notice unrelated problems, record them in your envelope `notes` (the engine logs them) — do not fix them.
5. **Findings first.** If the task packet carries findings from a previous attempt, fix those before anything else — they are why the last attempt failed.

A good question beats a wrong guess: if the Issue is ambiguous, contradictory, or turns on a decision that is not yours to make, stop and return `needs-human` with the question in `summary`.
