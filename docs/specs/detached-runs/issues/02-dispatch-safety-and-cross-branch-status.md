---
state: done
blocked_by: [01-worktree-resident-spec-loops]
attempts: 1
---
# Dispatch safety and cross-branch status

giro stays truthful and safe when the human's checkout is somewhere else entirely. One lock per Spec makes double-dispatch impossible; `giro status` reads every Spec's current truth from its branch tip wherever the human stands, and grows machine-readable output so a skill or CI can answer "how is it going?" without parsing prose.

## Acceptance criteria

- [ ] A second `giro implement` against a locked Spec fails immediately and loudly, naming the live Run; it never queues and never corrupts state.
- [ ] A lock whose holding process is dead is reclaimed with a message saying so; the new Run proceeds.
- [ ] `giro status` reports Spec and Issue states from the tip of `giro/<slug>` whenever that branch exists — even from a checkout that lacks those Spec files entirely; draft Specs with no branch are still read from the checkout.
- [ ] Target resolution for `giro implement` prefers the branch copy when the branch exists, so a re-invoke from a stale checkout resumes the real state.
- [ ] Machine-readable status output carries per-Spec and per-Issue state, attempts, blocking edges, and each Spec branch's ahead/behind counts against its recorded base.
- [ ] Status exit codes are unchanged: 2 while anything waits on a human, else 0.

## Attempt 1 — done — all gates green

- worker: Dispatch safety and cross-branch status. (1) New src/giro/runs.py holds the per-Spec lock: Engine.implement now wraps the whole Run in spec_lock(runtime/locks/<slug>.json), so a second dispatch against a live Spec raises RunError immediately — naming the holder's pid, target, and start time — and never queues; a lock whose process is dead (or whose contents are unreadable) is reclaimed with a stderr notice and the Run proceeds; the lock is released at the Run's end and never removed if a later Run took it over. RunError joins the CLI's caught errors, so double-dispatch exits 1. (2) store.py gains BranchStore (reads Spec and Issue markdown straight from the tip of giro/<slug> via git show/ls-tree — no worktree, no checkout switch) and SpecView, which picks the reader per Spec: the branch tip when the branch exists and carries the Spec, the invoking checkout otherwise (drafts, and a branch forked by a Run that died before seeding). File-parsing was factored into parse_spec/parse_issue and target resolution into one _resolve used by Store, BranchStore, and SpecView. (3) Engine.implement resolves through SpecView, so a re-invoke from a stale or Spec-less checkout resumes the real state instead of dead-ending. (4) giro status reads through SpecView and grows `--json`: per-Spec slug/state/title/source/branch/worktree/base_branch/ahead/behind/gap_cycles and per-Issue id/ref/state/attempts/blocked_by/unknown_blocked_by, plus a top-level needs_human list; text and JSON render from one structure, so exit codes are unchanged (2 while anything waits on a human, else 0). Workspace gained spec_branch_slugs, read_blob, list_tree, and ahead_behind. Twelve tests in tests/test_dispatch_safety.py; each was verified to fail against the pre-change code (checked in a scratch copy built from git archive HEAD, and by temporarily disabling the not-yet-seeded fallback). Full suite 124 passed, ruff clean.
- notes: Two things deliberately left to later Issues in this Spec: the Ledger overlay on status and `giro runs`/`giro logs` belong to Issue 03, and README/design/skill prose belongs to Issue 04 — nothing in the current docs became false here (status is only truer, plus one new flag). One judgement call worth flagging: the text escalation lines now point at the Spec's worktree path (.giro/worktrees/<slug>/docs/specs/...) when the Spec is on a branch, because the checkout copy it used to name may be stale or absent; that path is right whenever the worktree exists, and a human who deleted it gets it back on the next Run. Unrelated observation carried over from the previous Issue and still true: Config is loaded from the invoking checkout's giro.toml while gate criteria and commands resolve inside the worktree, so the gate list and the gate content come from different trees — worth an explicit decision when the dispatch Issue lands, since a detached Run's config is captured at dispatch time.
