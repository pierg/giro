"""The guarded loops — one primitive, two levels.

An actor makes an attempt; an independent verifier judges it; findings feed
the next attempt; a budget bounds the loop; exhaustion escalates. Every
exit is proof or ``needs-human`` — never a silent stop.

The Issue loop's actor is a worker context; its verifier is the ``[verify]``
gate set. The Spec loop's actor is a wave of Issue loops; its verifier is
the ``[validate]`` gate set, and its findings become gap Issues. Control
flow lives entirely here, in deterministic code.

Parallel waves (``concurrency > 1``) keep one invariant absolute: **only the
engine's main thread touches the store or the integration branch.** Workers
run concurrently in isolated worktrees on their own branches; their results
are merged, re-verified, and recorded strictly one at a time.

Every Run happens in the Spec's own persistent worktree (ADR-0013): the
engine binds itself to that worktree and never reads, writes, or switches the
invoking checkout. It holds that Spec's lock for its whole life, so a second
dispatch is refused rather than allowed to fight over the branch, and every
worker context takes one of the checkout's worker slots, so Runs dispatched
freely still share one ceiling.

The Run narrates itself into its Ledger as it goes — one event per
state-changing moment. The Ledger is observability, never control: nothing
here reads it back, and a Run with no Ledger behaves identically.
"""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import Config, ConfigError
from .drivers import Driver, DriverError, SubprocessDriver
from .envelope import (
    PLAN_SCHEMA,
    WORKER_SCHEMA,
    EnvelopeError,
    Finding,
    parse_plan,
    parse_worker,
)
from .gates import build_context, run_gate_set
from .ledger import Ledger
from .projection import Projection
from .prompts import resolve_prompt
from .runs import spec_lock, worker_slot
from .states import ISSUE_TERMINAL, SPEC_TERMINAL
from .store import Issue, Spec, SpecView, Store, StoreError
from .workspace import Workspace, WorkspaceError

# Paths a worker attempt may not touch (relative to ws.root). Workers are told
# so in the preamble; the engine enforces it after the driver returns because
# a rewritten Spec/CONTEXT/ADR/prompt/gate/config would rewrite the very rules
# a judge next reads.
PROTECTED_WORKER_PATHS = (
    "docs/specs/",
    "docs/adr/",
    "prompts/",
    "gates/",
    "CONTEXT.md",
    "giro.toml",
)


# One probe path per protected prefix: if a worker's ignore rule would ignore
# it, the rule hides a protected path from git and is itself reverted.
_PROTECTED_PROBES = [
    guard + "__giro_probe__" if guard.endswith("/") else guard
    for guard in PROTECTED_WORKER_PATHS
]


@dataclass
class _GuardBaseline:
    """What was already there before a worker ran: ignored files under the
    protected prefixes, and which protected probes were already ignored."""

    ignored: dict[str, tuple[int, int]]
    probes: dict[str, str]


def _guard_baseline(ws: Workspace) -> _GuardBaseline:
    return _GuardBaseline(
        ignored=ws.ignored_snapshot(list(PROTECTED_WORKER_PATHS)),
        probes=ws.ignored_probes(_PROTECTED_PROBES),
    )


def _guardrail_hits(ws: Workspace, before: _GuardBaseline) -> list[str]:
    """Every path the attempt changed that the topology rule forbids: edits
    under a protected prefix, ignored files there that the attempt created
    or changed, and any changed ignore file that newly hides a protected path
    from git. What was there before the worker ran is never blamed on it."""
    changed = ws.changed_paths()
    now = ws.ignored_snapshot(list(PROTECTED_WORKER_PATHS))
    fresh = [p for p, stamp in now.items() if before.ignored.get(p) != stamp]
    hits = _protected_edits([*changed, *fresh])
    newly_hidden = {
        probe: src for probe, src in ws.ignored_probes(_PROTECTED_PROBES).items()
        if probe not in before.probes
    }
    for src in newly_hidden.values():
        if src in changed and src not in hits:
            hits.append(src)
    return hits


def _protected_edits(paths: list[str]) -> list[str]:
    """The subset of ``paths`` that fall under a protected prefix — the pieces
    the topology rule (workers cannot rewrite what judges them) forbids."""
    hit: list[str] = []
    for path in paths:
        for guard in PROTECTED_WORKER_PATHS:
            if guard.endswith("/") and path.startswith(guard):
                hit.append(path)
                break
            if not guard.endswith("/") and (path == guard or path.startswith(guard + "/")):
                hit.append(path)
                break
    return hit


WORKER_PREAMBLE = """\
You are a worker agent implementing exactly one Issue in this repository.

Hard rules (the engine enforces control flow; these are yours to keep):
- Edit files only inside this repository. Never run `git commit` or touch `.git`;
  the engine commits for you.
- Never edit Spec or Issue markdown under docs/specs/ — state belongs to the engine.
- Never edit CONTEXT.md or docs/adr/ — the project's rules are judged, not
  rewritten; a gate cannot be passed by changing what it checks against.
- Never edit prompts/ or giro.toml — the engine's own contract with itself
  is not yours to tune from inside a worker attempt.
- Never weaken, skip, or delete an existing test to reach green.
- Stay inside this Issue's scope. If it cannot be done as specified, say so.

When finished, your ENTIRE final output must be a single JSON object matching:
{schema}

Use outcome "completed" only when your authored tests pass. Use "needs-human"
when a human decision is required (explain in summary). Use "failed" when you
could not complete the work (explain in summary).
"""

PLANNER_PREAMBLE = """\
You are a planning agent. Decompose the Spec below into the smallest set of
independently implementable Issues — each a thin, vertical, demoable slice
with clear acceptance criteria in its body. Order them; use blocked_by
(1-based indices into your own list) only for real dependencies, and never
form a cycle.

If a planning skill follows, apply its slicing judgment — tracer-bullet
vertical slices, blocking edges, expand–contract for wide refactors. Its
interactive steps do not apply here: you cannot quiz anyone and you must not
run commands or edit files; the engine writes the Issue files from your plan.

Your ENTIRE final output must be a single JSON object matching:
{schema}
"""


def _finding_lines(findings: list[Finding]) -> list[str]:
    """Render findings as human-readable log/prompt bullets."""
    return [f"{f.gate + ': ' if f.gate else ''}{f.summary}" for f in findings]


def _count(n: int, noun: str) -> str:
    """"1 Issue", "2 Issues" — a comment a human reads should read like one."""
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _issue_lines(issues: list[Issue]) -> list[str]:
    """Name Issues for a narration comment — through their sub-issues where the
    Projection has already made them, so the parent's thread links to them."""
    return [
        f"{f'#{i.github_issue} ' if i.github_issue else ''}`{i.id}` {i.title or i.id}"
        for i in issues
    ]


@dataclass
class Report:
    outcome: str  # "done" | "all-done" | "needs-human"
    target: str
    detail: str = ""
    issues: dict[str, str] = field(default_factory=dict)  # issue ref -> final state


@dataclass
class CycleOutcome:
    """Result of one store-free attempt cycle (safe to produce in a thread)."""

    status: str  # "green" | "worker-escalated" | "budget"
    attempts_used: int
    carried: list[Finding]  # last findings, for escalation reporting
    log: list[tuple[str, list[str]]]  # (heading, rendered lines) per attempt
    summary: str = ""  # worker escalation summary
    verdicts: dict[str, str] = field(default_factory=dict)  # the last verify, per gate


@dataclass
class Integration:
    """What merging one green worker branch into the integration branch came
    to — and, when it stuck, what the integrated Verify said about it."""

    status: str  # "done" | "conflict" | "verify-failed"
    findings: list[Finding] = field(default_factory=list)
    verdicts: dict[str, str] = field(default_factory=dict)


@dataclass
class GateReport:
    """One gate set's verdicts, for the commit statuses a checkpoint publishes
    alongside the commits they judge."""

    kind: str  # "verify" | "validate"
    verdicts: dict[str, str]
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Engine:
    """The loop engine: config + store + workspace + one driver per role."""

    cfg: Config
    store: Store
    workspace: Workspace
    drivers: dict[str, Driver]  # role -> driver
    ledger: Ledger = field(default_factory=Ledger)  # default: the Run nobody watches
    projection: Projection = field(default_factory=Projection)  # default: off
    runtime: Path | None = None  # the invoking checkout's .giro — shared by every Run

    # -- preflight ------------------------------------------------------------

    def _preflight(self) -> None:
        """Fail closed before spending a token: every named judge criterion
        must resolve. A typo'd ``criterion = "reviw"`` should stop the run at
        zero cost, not after a worker has already run. Judge gates with only
        an inline ``rubric`` have nothing to resolve.

        Resolution is rooted at ``cfg.root``, so this must be asked of the engine
        bound to the tree the gates will actually run in — the Spec worktree for
        a Run, the checkout for ``giro verify``. Asking the wrong tree turns a
        zero-cost refusal back into a mid-Run surprise.
        """
        for gate in (*self.cfg.verify_gates, *self.cfg.validate_gates):
            if gate.type != "judge" or not gate.criterion:
                continue
            if resolve_prompt(self.cfg.root, "prompts", gate.criterion) is None:
                raise ConfigError(
                    f"gate {gate.name!r} names criterion {gate.criterion!r}, which is not "
                    "found in prompts/ (project or bundled) — fix giro.toml or add the file"
                )

    # -- checkpoints (the only moments anything is published) ------------------

    def _checkpoint(
        self,
        spec: Spec,
        phase: str,
        live: list[Issue] | None = None,
        gates: GateReport | None = None,
    ) -> None:
        """Publish a decision: push the branch, then re-render the Spec's surface.

        Called only after a state commit — never mid-attempt and never before a
        reset — so published history only grows and the engine never
        force-pushes (ADR-0014). Fail-soft throughout: a Run with a dead
        network, or with Projection off, takes exactly the same path.

        Identifiers minted here land in Spec and Issue frontmatter and are
        committed, so the next Run finds the surface instead of making a second
        one. ``live`` is the Issue objects the caller still holds and will save
        again — they are given the numbers too, or the next save would erase
        what this checkpoint just recorded.

        ``gates`` is the verdict on the commits this checkpoint publishes: the
        statuses hang on the head as it was pushed, never on the identifier
        commit that follows and is not published until the next checkpoint.
        """
        if not self.projection.enabled:
            return
        branch = f"giro/{spec.slug}"
        head = self.workspace.head_sha()
        published = self.workspace.push(branch, timeout=self.projection.settings.timeout)
        if not published:
            self.projection.note("push", ok=False, detail=f"could not push {branch}")
        issues = self.store.load_issues(spec.slug)
        surface = self.projection.project_spec(spec, issues, phase, branch_published=published)
        recorded = self._record_numbers(issues, surface.issues, live or [])
        if (surface.issue, surface.pr) != (spec.github_issue, spec.github_pr):
            spec.github_issue, spec.github_pr = surface.issue, surface.pr
            self.store.save_spec(spec)
            recorded = True
        if recorded:
            self.workspace.commit_within(
                f"docs/specs/{spec.slug}",
                f"giro({spec.slug}): record github identifiers",
            )
        if gates is not None and published:
            self.projection.report_gates(head, gates.kind, gates.verdicts, gates.findings)

    def _project_claim(self, spec: Spec, live: list[Issue]) -> None:
        """A claim is a decision too: the sub-issues say in-progress while the
        workers run, so the parent's progress bar is the wave's scoreboard as it
        happens. Nothing is pushed — a claim adds no history worth publishing —
        so only the Issues' own surface is re-rendered.
        """
        if not self.projection.enabled or not spec.github_issue:
            return
        issues = self.store.load_issues(spec.slug)
        numbers = self.projection.project_issues(spec, issues, spec.github_issue)
        if self._record_numbers(issues, numbers, live):
            self.workspace.commit_within(
                f"docs/specs/{spec.slug}",
                f"giro({spec.slug}): record github identifiers",
            )

    def _record_numbers(
        self, issues: list[Issue], numbers: dict[str, int], live: list[Issue]
    ) -> bool:
        """Write the sub-issue numbers the projection minted into the store, and
        answer whether anything changed — the mapping is memory, so it is
        committed."""
        changed = False
        held = {i.id: i for i in live}
        for issue in issues:
            number = numbers.get(issue.id, 0)
            if not number or number == issue.github_issue:
                continue
            issue.github_issue = number
            self.store.save_issue(issue)
            changed = True
            if (holder := held.get(issue.id)) is not None:
                holder.github_issue = number
        return changed

    # -- the attempt cycle (shared by both paths) -----------------------------

    def _attempt_cycle(
        self,
        spec: Spec,
        issue: Issue,
        ws: Workspace,
        base: str,
        budget: int,
        carried: list[Finding],
    ) -> CycleOutcome:
        """Worker attempts vs the [verify] gates until green, escalation, or budget.

        Store-free and main-workspace-free by design: everything happens in
        ``ws`` (the project root at concurrency 1, an isolated worktree in a
        parallel wave), so this may run in a thread. ``base`` is the commit the
        Issue's work forks from — the diff base handed to judged verify gates,
        so a gate sees this Issue's changes, not the whole Spec branch.
        """
        log: list[tuple[str, list[str]]] = []
        verdicts: dict[str, str] = {}  # the last verify run, whatever it said
        used = 0
        while used < budget:
            used += 1
            n = issue.attempts + used
            self.ledger.mark(issue=issue.ref, attempt=n)
            self.ledger.phase(f"worker {issue.ref} attempt {n}")

            start = ws.head_sha()
            before = _guard_baseline(ws)
            edits: list[str] = []
            reverted: list[Finding] = []
            result = None
            try:
                with worker_slot(self._runtime_dir(), self.cfg.max_workers):
                    data = self.drivers["worker"].run(
                        self._task_packet(spec, issue, carried),
                        WORKER_SCHEMA,
                        cwd=ws.root,
                        model=self.cfg.roster_for("worker").model,
                    )
                result = parse_worker(data)
            except (DriverError, EnvelopeError) as exc:
                carried = [Finding(summary=f"worker context failed: {exc}")]
                log.append((f"Attempt {n} — errored", _finding_lines(carried)))
                self._attempt_event(issue, n, "errored", findings=carried)
                continue  # the finally below still reverts protected edits
            finally:
                # A worker never commits; if it did anyway, fold its commits
                # back into the working tree so the guardrail sees every path
                # the attempt changed, and only the engine commits.
                ws.uncommit_to(start)
                # Topology guardrail (F2): a worker cannot rewrite what judges
                # it. Revert Spec/CONTEXT/ADR/prompt/gate/config edits on every
                # outcome, before any engine commit can pick them up.
                edits = _guardrail_hits(ws, before)
                if edits:
                    ws.revert_paths(edits)
                    reverted = [
                        Finding(
                            summary=(
                                f"worker edited protected path(s): {', '.join(edits)}; "
                                "changes reverted"
                            )
                        )
                    ]
                    if result is None or result.outcome != "completed":
                        log.append(
                            (f"Attempt {n} — protected-path edit reverted",
                             _finding_lines(reverted))
                        )

            if result.outcome == "needs-human":
                log.append((f"Attempt {n} — escalated by worker", [result.summary]))
                self._attempt_event(issue, n, "worker-escalated", summary=result.summary)
                self._discard_leftovers(ws, n, log)
                return CycleOutcome("worker-escalated", used, [], log, summary=result.summary)

            if result.outcome == "failed":
                carried = [Finding(summary=f"worker gave up: {result.summary}"), *reverted]
                log.append((f"Attempt {n} — failed", _finding_lines(carried)))
                self._attempt_event(issue, n, "worker-failed", findings=carried)
                continue

            # A completed attempt that touched a protected path is refused (the
            # edits were reverted above). The worker's other uncommitted edits
            # stay in the working tree for the next attempt, which is told why.
            if edits:
                carried = reverted
                log.append(
                    (f"Attempt {n} — protected-path edit refused", _finding_lines(carried))
                )
                self._attempt_event(
                    issue, n, "protected-path-edit", findings=carried
                )
                continue

            if not ws.commit_all(f"giro({issue.ref}): attempt {n}"):
                carried = [Finding(summary="worker reported completed but produced no changes")]
                log.append((f"Attempt {n} — empty", _finding_lines(carried)))
                self._attempt_event(issue, n, "no-changes", findings=carried)
                continue

            gates = run_gate_set(
                self.cfg.verify_gates,
                build_context(
                    self.cfg, ws.root, self._material(spec, issue, ws, base), self.drivers["judge"]
                ),
            )
            self._phase_gates(f"verify {issue.ref}", gates.verdicts)
            if gates.passed:
                lines = [f"worker: {result.summary}"] if result.summary else []
                if result.notes:
                    lines.append(f"notes: {result.notes}")
                log.append((f"Attempt {n} — done — all gates green", lines))
                self._attempt_event(
                    issue, n, "green", verdicts=gates.verdicts, summary=result.summary
                )
                return CycleOutcome("green", used, [], log, verdicts=gates.verdicts)

            carried = gates.findings
            verdicts = gates.verdicts
            log.append((f"Attempt {n} — gates failed", _finding_lines(gates.findings)))
            self._attempt_event(
                issue, n, "gates-failed", verdicts=gates.verdicts, findings=gates.findings
            )

        self._discard_leftovers(ws, issue.attempts + used, log)
        return CycleOutcome("budget", used, carried, log, verdicts=verdicts)

    def _discard_leftovers(
        self, ws: Workspace, n: int, log: list[tuple[str, list[str]]]
    ) -> None:
        """An escalating cycle leaves the engine's worktree clean: whatever the
        last worker left uncommitted was never judged, so it is discarded and
        named in the Issue's log — the Run that answers the escalation then
        starts from the committed branch, as the clean-tree check requires."""
        dropped = ws.discard_changes()
        if dropped:
            log.append(
                (f"Attempt {n} — uncommitted leftovers discarded", sorted(dropped))
            )

    def _attempt_event(
        self,
        issue: Issue,
        n: int,
        outcome: str,
        verdicts: dict[str, str] | None = None,
        findings: list[Finding] | None = None,
        summary: str = "",
    ) -> None:
        """One event per attempt end — how it went, what each gate said, and
        what was wrong. Appended from a wave's threads as readily as the main
        one; the Ledger is append-only."""
        self.ledger.event(
            "attempt",
            issue=issue.ref,
            n=n,
            outcome=outcome,
            verdicts=verdicts or {},
            findings=[f.as_dict() for f in findings or []],
            summary=summary,
        )
        self.ledger.phase(f"worker {issue.ref} attempt {n} — {outcome}")

    def _phase_gates(self, prefix: str, verdicts: dict[str, str]) -> None:
        """One line for a gate set's verdicts — one per gate, on one line, so
        a human watching stderr reads them at a glance."""
        if not verdicts:
            return
        summary = " ".join(f"{gate}={verdict}" for gate, verdict in verdicts.items())
        self.ledger.phase(f"{prefix}: {summary}")

    def _apply_cycle_logs(self, issue: Issue, cycle: CycleOutcome) -> None:
        for heading, lines in cycle.log:
            self.store.append_log(issue, heading, lines or ["—"])

    def _escalate_budget(self, issue: Issue, carried: list[Finding]) -> str:
        """The Issue's state and its story; the durable reason is returned, for
        the caller to tell once the decision is committed."""
        issue.state = "needs-human"
        self.store.append_log(
            issue,
            f"Budget exhausted after {issue.attempts} attempts",
            [f.summary for f in carried] or ["no findings recorded"],
        )
        return f"budget exhausted after {issue.attempts} attempts"

    def _escalation_event(
        self,
        spec: Spec,
        reason: str,
        findings: list[Finding] | None = None,
        issue: Issue | None = None,
    ) -> None:
        """One escalation, told everywhere it has to be felt: the Ledger records
        it, and the Projection makes it a comment and an assignment that notify
        (the label follows the state, at the next convergence).

        Called after the checkpoint that commits and publishes the escalated
        state — the surface it lands on is the one that checkpoint rendered.
        Fail-soft throughout: an unreachable GitHub costs the notification and
        nothing else.
        """
        target = issue.ref if issue is not None else spec.slug
        self.ledger.event(
            "escalation",
            target=target,
            reason=reason,
            findings=[f.as_dict() for f in findings or []],
        )
        self.ledger.phase(f"escalation {target}: {reason}")
        self.projection.escalate(spec, issue, reason, _finding_lines(findings or []))

    # -- issue loop (sequential: work lands directly on the integration branch)

    def run_issue_loop(self, spec: Spec, issue: Issue) -> str:
        """Drive one Issue to done or needs-human. Returns the final state."""
        issue.state = "in-progress"
        # F10 — the diff base a judged verify gate sees is anchored at the
        # first claim: a resume must not slide the base forward to whatever
        # HEAD the second Run happens to inherit, or the gate would judge a
        # window narrower than the Issue's actual work.
        if not issue.verify_base:
            issue.verify_base = self.workspace.head_sha()
        self.store.save_issue(issue)
        # Commit the claim separately so attempt commits contain only worker
        # output — that is what "no changes" detection reads.
        self.workspace.commit_within(
            f"docs/specs/{spec.slug}", f"giro({issue.ref}): claim"
        )
        self.ledger.event("claim", issue=issue.ref, attempts=issue.attempts)
        self.ledger.phase(f"claim {issue.ref}")
        self._project_claim(spec, [issue])
        base = issue.verify_base

        remaining = self.cfg.issue_attempts - issue.attempts
        cycle = self._attempt_cycle(spec, issue, self.workspace, base, remaining, [])
        issue.attempts += cycle.attempts_used
        self._apply_cycle_logs(issue, cycle)

        escalation: tuple[str, list[Finding]] | None = None
        if cycle.status == "green":
            issue.state = "done"
        elif cycle.status == "worker-escalated":
            issue.state = "needs-human"
            escalation = (cycle.summary, [])
        else:
            escalation = (self._escalate_budget(issue, cycle.carried), cycle.carried)
        self.store.save_issue(issue)
        self.workspace.commit_within(
            f"docs/specs/{spec.slug}",
            f"giro({issue.ref}): state -> {issue.state}",
        )
        self.ledger.phase(f"state {issue.ref} → {issue.state}")
        # The attempts landed on the integration branch as they happened, so
        # what the last Verify said is what this published head is worth.
        self._checkpoint(
            spec,
            f"{issue.id} {issue.state}",
            live=[issue],
            gates=GateReport("verify", cycle.verdicts, cycle.carried),
        )
        if escalation is not None:
            self._escalation_event(spec, *escalation, issue=issue)
        return issue.state

    # -- parallel wave ---------------------------------------------------------

    def _run_wave_parallel(self, spec: Spec, frontier: list[Issue]) -> None:
        """One wave of concurrent workers, integrated one at a time.

        Claim all → parallel store-free attempt cycles in isolated worktrees →
        serialized per-issue: merge --no-ff, integrated Verify, state + logs,
        commit. A conflict or integrated failure sends the Issue back to ready
        (budget permitting) to retry from the updated integration HEAD — that
        retry IS the serialization of colliding scopes.

        Wave worktrees live under ``.giro/wave-worktrees/`` (F4): the parent is
        inside the runtime directory, self-ignored, so a hard-killed run's
        leftovers can be pruned and rmtree'd deterministically on the next
        wave rather than wedging inside the system tmp dir.
        """
        self.workspace.prune_worktrees()  # clear any worktree a hard-killed run left behind
        # F10 — anchor each Issue's verify base at first claim: a resume must
        # not re-anchor it to whatever HEAD the second Run happens to inherit.
        first_claim_base = self.workspace.head_sha()
        for issue in frontier:
            issue.state = "in-progress"
            if not issue.verify_base:
                issue.verify_base = first_claim_base
            self.store.save_issue(issue)
        refs = ", ".join(i.id for i in frontier)
        self.workspace.commit_within(
            f"docs/specs/{spec.slug}", f"giro({spec.slug}): claim wave [{refs}]"
        )
        for issue in frontier:
            self.ledger.event("claim", issue=issue.ref, attempts=issue.attempts)
            self.ledger.phase(f"claim {issue.ref}")
        self._project_claim(spec, frontier)
        head = self.workspace.head_sha()

        # Under .giro/ (git-ignored, per-slug) rather than the system tmp dir:
        # a killed run's debris is deterministically discoverable and prunable
        # by the next wave.
        wave_root = self._runtime_dir() / "wave-worktrees" / spec.slug
        wave_root.mkdir(parents=True, exist_ok=True)

        jobs: list[tuple[Issue, str, Path | None, Workspace | None, int]] = []
        for issue in frontier:
            remaining = self.cfg.issue_attempts - issue.attempts
            # NB: not giro/<slug>/<issue> — a git ref cannot be nested under an
            # existing branch name (giro/<slug> is the integration branch).
            branch = f"giro-wt/{spec.slug}/{issue.id}"
            if remaining <= 0:
                jobs.append((issue, branch, None, None, 0))
                continue
            path = wave_root / issue.id
            # Clear any leftover from a killed prior wave: the branch, the
            # git worktree registration, and the on-disk directory.
            self.workspace.remove_worktree(path, branch)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            ws = self.workspace.add_worktree(path, branch, head)
            jobs.append((issue, branch, path, ws, remaining))

        try:
            with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as pool:
                futures = {
                    issue.id: pool.submit(
                        self._attempt_cycle,
                        spec,
                        issue,
                        ws,
                        # F10 — use the Issue's first-claim base, not the
                        # wave's current head.
                        issue.verify_base or head,
                        remaining,
                        [],
                    )
                    for issue, _branch, _path, ws, remaining in jobs
                    if ws is not None
                }
            # F7 — isolate per-future errors: a non-DriverError/EnvelopeError
            # thrown by one thread must not sink its siblings (whose green
            # branches would otherwise be deleted by the outer finally).
            # Turn any unexpected exception into a CycleOutcome("errored") so
            # the sequential integration loop still runs for the survivors.
            results: dict[str, CycleOutcome] = {}
            for issue_id, future in futures.items():
                try:
                    results[issue_id] = future.result()
                except (DriverError, EnvelopeError) as exc:
                    finding = Finding(summary=f"wave thread errored: {exc}")
                    results[issue_id] = CycleOutcome(
                        status="errored",
                        attempts_used=1,
                        carried=[finding],
                        log=[
                            (
                                "Attempt errored — driver/envelope failure in wave thread",
                                _finding_lines([finding]),
                            )
                        ],
                    )
                except Exception as exc:  # noqa: BLE001 — wave thread isolation
                    finding = Finding(summary=f"wave thread errored: {exc!r}")
                    results[issue_id] = CycleOutcome(
                        status="errored",
                        attempts_used=1,
                        carried=[finding],
                        log=[
                            (
                                "Attempt errored — unexpected exception in wave thread",
                                _finding_lines([finding]),
                            )
                        ],
                    )

            for issue, branch, _path, _ws, remaining in jobs:
                integration: Integration | None = None
                escalation: tuple[str, list[Finding]] | None = None
                if remaining <= 0:
                    escalation = (self._escalate_budget(issue, []), [])
                else:
                    cycle = results[issue.id]
                    # Integrate first, store writes after: reset_hard must never
                    # meet uncommitted issue-file edits.
                    integration = (
                        self._integrate_branch(spec, issue, branch)
                        if cycle.status == "green"
                        else None
                    )
                    issue.attempts += cycle.attempts_used
                    self._apply_cycle_logs(issue, cycle)
                    if cycle.status == "worker-escalated":
                        issue.state = "needs-human"
                        escalation = (cycle.summary, [])
                    elif cycle.status == "budget":
                        escalation = (self._escalate_budget(issue, cycle.carried), cycle.carried)
                    elif cycle.status == "errored":
                        # F7 — the wave thread crashed. Treat as a failed
                        # attempt: budget-permitting, retry from the next
                        # wave; else escalate.
                        if issue.attempts < self.cfg.issue_attempts:
                            issue.state = "ready"
                        else:
                            escalation = (
                                self._escalate_budget(issue, cycle.carried),
                                cycle.carried,
                            )
                    else:
                        escalation = self._record_integration(issue, integration)
                self.store.save_issue(issue)
                self.workspace.commit_within(
                    f"docs/specs/{spec.slug}",
                    f"giro({issue.ref}): state -> {issue.state}",
                )
                self.ledger.phase(f"state {issue.ref} → {issue.state}")
                # After the merge and after any reset it caused: a checkpoint
                # publishes decided history only. The wave's own Issue objects
                # are saved one at a time, so they take the numbers with them.
                # A merge that was reset judged nothing that is on this head,
                # so only an integrated one leaves statuses behind.
                self._checkpoint(
                    spec,
                    f"{issue.id} {issue.state}",
                    live=frontier,
                    gates=(
                        GateReport("verify", integration.verdicts)
                        if integration is not None and integration.status == "done"
                        else None
                    ),
                )
                if escalation is not None:
                    self._escalation_event(spec, *escalation, issue=issue)
        finally:
            for _issue, branch, path, _ws, _remaining in jobs:
                if path is not None:
                    self.workspace.remove_worktree(path, branch)
                    shutil.rmtree(path, ignore_errors=True)

    def _integrate_branch(self, spec: Spec, issue: Issue, branch: str) -> Integration:
        """Merge a green worker branch and re-verify the merged result.

        On any failure the integration branch is restored to its pre-merge
        state. The integrated verify judges just this merge's contribution
        (pre..HEAD).
        """
        pre = self.workspace.head_sha()
        if not self.workspace.merge_no_ff(branch, f"giro({spec.slug}): merge {branch}"):
            findings = [Finding(summary=f"merge conflict integrating {branch}")]
            self._merge_event(issue, branch, "conflict", {}, findings)
            return Integration("conflict", findings)
        gates = run_gate_set(
            self.cfg.verify_gates,
            build_context(
                self.cfg,
                self.cfg.root,
                self._material(spec, issue, self.workspace, pre),
                self.drivers["judge"],
            ),
        )
        self._phase_gates(f"verify {issue.ref} (integrated)", gates.verdicts)
        if gates.passed:
            self._merge_event(issue, branch, "done", gates.verdicts, [])
            return Integration("done", verdicts=gates.verdicts)
        self.workspace.reset_hard(pre)
        self._merge_event(issue, branch, "verify-failed", gates.verdicts, gates.findings)
        return Integration("verify-failed", gates.findings, gates.verdicts)

    def _merge_event(
        self,
        issue: Issue,
        branch: str,
        result: str,
        verdicts: dict[str, str],
        findings: list[Finding],
    ) -> None:
        self.ledger.event(
            "merge",
            issue=issue.ref,
            branch=branch,
            result=result,
            verdicts=verdicts,
            findings=[f.as_dict() for f in findings],
        )
        self.ledger.phase(f"merge {issue.ref} — {result}")

    def _record_integration(
        self, issue: Issue, integration: Integration | None
    ) -> tuple[str, list[Finding]] | None:
        """What the merge came to, in the store — and the escalation it forced,
        for the caller to tell once the state is committed."""
        assert integration is not None
        status, findings = integration.status, integration.findings
        if status == "done":
            issue.state = "done"
            self.store.append_log(
                issue, "Integrated — merged and verified on the integration branch", []
            )
            return None
        why = "Merge conflict" if status == "conflict" else "Integrated verify failed"
        lines = _finding_lines(findings)
        if issue.attempts < self.cfg.issue_attempts:
            issue.state = "ready"
            self.store.append_log(
                issue, f"{why} — retrying from the updated integration HEAD", lines or ["—"]
            )
            return None
        return self._escalate_budget(issue, findings), findings

    # -- task packets & material ----------------------------------------------

    def _task_packet(self, spec: Spec, issue: Issue, carried: list[Finding]) -> str:
        parts = [
            WORKER_PREAMBLE.format(schema=json.dumps(WORKER_SCHEMA, indent=2)),
            self._prompt_text("prompts", "worker"),
            f"## Spec: {spec.title or spec.slug}\n\n{spec.body.strip()}",
            f"## Issue: {issue.title or issue.id}\n\n{issue.body.strip()}",
        ]
        context_md = self.cfg.root / "CONTEXT.md"
        if context_md.is_file():
            parts.append(
                "## Project language (CONTEXT.md) — use these terms, avoid the avoided\n\n"
                + context_md.read_text(encoding="utf-8").strip()
            )
        if carried:
            found = "\n".join(
                f"- {(f.gate + ': ') if f.gate else ''}{f.summary}"
                + (f"\n  {f.detail}" if f.detail else "")
                for f in carried
            )
            parts.append(f"## Findings from the previous attempt — fix these first\n\n{found}")
        return "\n\n".join(parts)

    def _prompt_text(self, category: str, name: str) -> str:
        return resolve_prompt(self.cfg.root, category, name) or ""

    def _material(self, spec: Spec, issue: Issue | None, ws: Workspace, base: str) -> str:
        """The change under judgment: the diff since ``base`` in workspace ``ws``.

        Verify gates get an Issue-scoped base (the Issue's own changes);
        Validate gets ``spec.base`` (the whole Spec branch).
        """
        diff = ws.diff_since(base)
        header = f"### Spec\n\n{spec.body.strip()}\n"
        if issue is not None:
            header += f"\n### Issue\n\n{issue.body.strip()}\n"
        return f"{header}\n### Diff under judgment\n\n```diff\n{diff}\n```"

    # -- spec loop -----------------------------------------------------------

    def run_spec_loop(self, spec: Spec) -> Report:
        answered = spec.state == "needs-human"
        reset_logs: list[tuple[str, list[str]]] = []
        if spec.state == "draft":
            spec.state = "active"
        elif answered:
            spec.state = "active"  # a human re-invoked us: that is the answer
            spec.gap_cycles = 0  # ...and re-invoking resets the validate budget
            # Every Issue the earlier Run escalated is answered by this
            # re-invoke too: reset them so the wave picks them up with a fresh
            # budget. Lands in the activate commit so the reset is atomic with
            # the Spec's own state change.
            for issue in self.store.load_issues(spec.slug):
                if issue.state == "needs-human":
                    issue.state = "ready"
                    issue.attempts = 0
                    self.store.save_issue(issue)
                    self.store.append_log(
                        issue,
                        "Reset by human re-invoke — budget restored",
                        ["state -> ready, attempts -> 0"],
                    )
                    reset_logs.append(
                        (f"reset issue {issue.ref}", ["state -> ready, attempts -> 0"])
                    )
        self.store.save_spec(spec)
        # Commit activation before any driver call: a crashed context must
        # never leave the tree dirty, or the retry would refuse to start.
        self.workspace.commit_within(
            f"docs/specs/{spec.slug}", f"giro({spec.slug}): activate"
        )
        for heading, lines in reset_logs:
            self.ledger.event("issue-reset", target=heading, lines=lines)
        self.ledger.event(
            "activation",
            spec=spec.slug,
            state=spec.state,
            branch=f"giro/{spec.slug}",
            base_branch=spec.base_branch,
        )
        self.ledger.phase(f"state {spec.slug} → {spec.state}")
        self._checkpoint(spec, "activated")
        if answered:
            # the checkpoint has just cleared the escalation label; this says why
            self.projection.resume(spec)

        if not self.store.load_issues(spec.slug):
            self._plan(spec)
            self._checkpoint(spec, "planned")
            planned = self.store.load_issues(spec.slug)
            self.projection.narrate(
                spec, "plan", f"Plan — {_count(len(planned), 'Issue')}", _issue_lines(planned)
            )

        wave = 0
        while True:
            wave = self._run_waves(spec, wave)
            issues = self.store.load_issues(spec.slug)
            unfinished = [i for i in issues if i.state not in ISSUE_TERMINAL]
            if unfinished:
                spec.state = "needs-human"
                self.store.save_spec(spec)
                self.workspace.commit_within(
                    f"docs/specs/{spec.slug}", f"giro({spec.slug}): needs-human"
                )
                self.ledger.phase(f"state {spec.slug} → {spec.state}")
                refs = ", ".join(f"{i.id} ({i.state})" for i in unfinished)
                self._checkpoint(spec, f"unfinished: {refs}")
                self._escalation_event(spec, f"unfinished issue(s): {refs}")
                return self._report(
                    spec, "needs-human", f"unfinished issue(s): {refs}", issues
                )

            validate = run_gate_set(
                self.cfg.validate_gates,
                build_context(
                    self.cfg,
                    self.cfg.root,
                    self._material(spec, None, self.workspace, spec.base),
                    self.drivers["judge"],
                ),
            )
            green, findings = validate.passed, validate.findings
            report = GateReport("validate", validate.verdicts, findings)
            self._phase_gates(f"validate {spec.slug}", validate.verdicts)
            self.projection.narrate(
                spec,
                f"validate-{spec.gap_cycles + 1}",
                f"Validate — {'passed' if green else 'failed'}",
                [f"{gate}: {verdict}" for gate, verdict in validate.verdicts.items()]
                + _finding_lines(findings),
            )
            if green:
                spec.state = "done"
                self.store.save_spec(spec)
                self.workspace.commit_within(
                    f"docs/specs/{spec.slug}", f"giro({spec.slug}): state -> done"
                )
                self.ledger.phase(f"state {spec.slug} → {spec.state}")
                self._checkpoint(spec, "validate passed", gates=report)
                # after the last checkpoint, so the review surface a human opens
                # already carries the finished story
                self.projection.complete(spec, issues)
                return self._report(
                    spec,
                    "all-done",
                    f"validate passed — merge branch giro/{spec.slug} when ready",
                    issues,
                )

            spec.gap_cycles += 1
            if spec.gap_cycles > self.cfg.validate_cycles:
                spec.state = "needs-human"
                self.store.save_spec(spec)
                self.workspace.commit_within(
                    f"docs/specs/{spec.slug}",
                    f"giro({spec.slug}): needs-human (validate budget)",
                )
                self.ledger.phase(f"state {spec.slug} → {spec.state}")
                detail = (
                    f"validate budget exhausted after {self.cfg.validate_cycles} gap cycles"
                )
                self._checkpoint(spec, detail, gates=report)
                self._escalation_event(spec, detail, findings)
                return self._report(spec, "needs-human", detail, issues)
            filed = [
                self.store.create_issue(
                    spec.slug,
                    title=f.summary[:72],
                    body=(f.detail or f.summary)
                    + f"\n\nFiled by validate gate `{f.gate}` in gap cycle {spec.gap_cycles}.",
                    gap_gate=f.gate,
                )
                for f in findings
            ]
            self.store.save_spec(spec)
            self.workspace.commit_within(
                f"docs/specs/{spec.slug}",
                f"giro({spec.slug}): validate gap cycle {spec.gap_cycles}",
            )
            self._checkpoint(spec, f"gap cycle {spec.gap_cycles}", live=filed, gates=report)
            self.ledger.phase(
                f"gap-cycle {spec.gap_cycles} — {_count(len(filed), 'issue')} filed"
            )
            self.ledger.event(
                "gap-cycle",
                spec=spec.slug,
                cycle=spec.gap_cycles,
                issues=[i.id for i in filed],
                findings=[f.as_dict() for f in findings],
            )
            self.projection.narrate(
                spec,
                f"gap-{spec.gap_cycles}",
                f"Gap cycle {spec.gap_cycles} — {_count(len(filed), 'Issue')} filed",
                _issue_lines(filed),
            )

    def _run_waves(self, spec: Spec, wave: int = 0) -> int:
        """Run frontiers until none is left; return the last wave number, so a
        Run's waves keep counting across gap cycles instead of starting over."""
        while True:
            issues = self.store.load_issues(spec.slug)
            by_id = {i.id: i for i in issues}
            for issue in issues:
                unknown = [d for d in issue.blocked_by if d not in by_id]
                if unknown:
                    raise StoreError(
                        f"{issue.ref}: blocked_by references unknown issue(s) "
                        f"{', '.join(unknown)} — fix the id or recreate with "
                        f"`giro new issue --blocked-by`"
                    )
            _check_no_cycles(issues, by_id)
            # "in-progress" here means a crashed earlier run: the engine is the
            # only writer, so a stale claim is always safe to re-claim.
            frontier = [
                i
                for i in issues
                if i.state in ("ready", "in-progress")
                and all(by_id[d].state in ISSUE_TERMINAL for d in i.blocked_by)
            ]
            if not frontier:
                return wave
            wave += 1
            self.ledger.mark(wave=wave, issue="", attempt=0)
            self.ledger.event(
                "wave",
                spec=spec.slug,
                wave=wave,
                issues=[i.id for i in frontier],
                concurrency=self.cfg.concurrency,
            )
            self.ledger.phase(
                f"wave {wave} — {', '.join(i.id for i in frontier)}"
            )
            self.projection.narrate(
                spec,
                f"wave-{wave}",
                f"Wave {wave} — {_count(len(frontier), 'Issue')}",
                _issue_lines(frontier),
            )
            if self.cfg.concurrency > 1 and len(frontier) > 1:
                self._run_wave_parallel(spec, frontier)
            else:
                for issue in frontier:  # sequential fresh workers
                    self.run_issue_loop(spec, issue)

    def _plan(self, spec: Spec) -> None:
        parts = [PLANNER_PREAMBLE.format(schema=json.dumps(PLAN_SCHEMA, indent=2))]
        # The planner prompt lives at prompts/planner.md — the same slicing
        # judgment the `plan` authoring skill teaches, minus its chat steps.
        if judgment := self._prompt_text("prompts", "planner"):
            parts.append(judgment)
        parts.append(f"## Spec: {spec.title or spec.slug}\n\n{spec.body.strip()}")
        prompt = "\n\n".join(parts)
        data = self.drivers["planner"].run(
            prompt, PLAN_SCHEMA, cwd=self.cfg.root, model=self.cfg.roster_for("planner").model
        )
        planned = parse_plan(data)
        created = [
            self.store.create_issue(spec.slug, p.title, p.body) for p in planned
        ]
        for planned_issue, issue in zip(planned, created, strict=True):
            if planned_issue.blocked_by:
                issue.blocked_by = [created[d - 1].id for d in planned_issue.blocked_by]
                self.store.save_issue(issue)
        self.workspace.commit_within(
            f"docs/specs/{spec.slug}",
            f"giro({spec.slug}): plan {len(created)} issue(s)",
        )
        self.ledger.event("plan", spec=spec.slug, issues=[i.id for i in created])
        self.ledger.phase(f"plan {spec.slug} — {_count(len(created), 'issue')}")

    def _report(
        self, spec: Spec, outcome: str, detail: str, issues: list[Issue]
    ) -> Report:
        return Report(
            outcome=outcome,
            target=spec.slug,
            detail=detail,
            issues={i.ref: i.state for i in issues},
        )

    # -- the run's workshop ---------------------------------------------------

    def _bind(self, ws: Workspace) -> Engine:
        """This engine, moved into ``ws``: store, prompts, gates, and commits all
        resolve inside the worktree. Gates therefore see committed content only —
        they must be hermetic (ADR-0013).

        The Ledger, the Projection, and the runtime directory do not move with
        it: all three belong to the invoking checkout, where the human reads
        them, where every Run's slots and locks meet, and — for the Projection —
        where the token's env file lives."""
        return Engine(
            cfg=replace(self.cfg, root=ws.root),
            store=Store(ws.root),
            workspace=ws,
            drivers=self.drivers,
            ledger=self.ledger,
            projection=self.projection,
            runtime=self._runtime_dir(),
        )

    def _runtime_dir(self) -> Path:
        """The invoking checkout's ``.giro`` — where locks, slots, and Ledgers
        live for every Run in this project."""
        if self.runtime is None:
            self.runtime = self.workspace.runtime_dir()
        return self.runtime

    def _driver_preflight(self) -> None:
        """Every real driver's CLI is on PATH — asked before git is touched.

        A missing ``claude`` or ``codex`` used to hide as three silent worker
        attempts against a dead executable; now it is one clear error before
        the runtime dir is even made. Non-subprocess drivers (``FakeDriver``
        in tests, any custom Driver protocol implementation) are not on PATH
        by construction, so they bypass this check.
        """
        for role in ("worker", "judge", "planner"):
            driver = self.drivers.get(role)
            if not isinstance(driver, SubprocessDriver):
                continue
            executable = driver.name
            if shutil.which(executable) is None:
                raise DriverError(
                    f"driver {executable!r} for role {role!r} not found on PATH — "
                    "run `giro doctor` to see what's expected"
                )

    def _base_branch_for(self, spec: Spec) -> str:
        """Resolve the base branch for a *first* activation: config if set, else the
        branch the human dispatched from. Fails before a context is ever spawned.

        Only a fork asks this question. Once ``giro/<slug>`` exists there is
        nothing left to fork from, and the value recorded on the branch is the
        truth — so a resume never re-infers, and never depends on where the
        human happens to be standing (ADR-0013).
        """
        if not self.workspace.has_commits():
            raise WorkspaceError(
                f"{self.store.root} has no commits yet — a Spec branch forks from a base "
                "branch, so make the first commit before running giro"
            )
        branch = self.cfg.base_branch or self.workspace.current_branch()
        if not branch:
            raise WorkspaceError(
                "cannot resolve a base branch: this checkout is on a detached HEAD — "
                "check out a branch or set base_branch in [runner] of giro.toml"
            )
        if branch == f"giro/{spec.slug}":
            raise WorkspaceError(
                f"a Spec branch cannot fork from itself: giro/{spec.slug} is the branch "
                "the engine works on — check out the branch this Spec should merge into, "
                "or set base_branch in [runner] of giro.toml"
            )
        if not self.workspace.branch_exists(branch):
            raise WorkspaceError(
                f"base branch {branch!r} does not exist — create it, or set base_branch "
                "in [runner] of giro.toml to a branch that does"
            )
        return branch

    def _seed_spec(self, spec: Spec, ws: Workspace) -> None:
        """A Spec is born in the checkout but lives on the branch: at first
        activation its directory is copied onto the fresh branch — the human's
        working copy, uncommitted edits and all. It is never re-seeded."""
        source = spec.path.parent
        target = ws.root / source.relative_to(self.store.root)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)

    def _open_run(self, spec: Spec) -> tuple[Engine, Spec]:
        """Enter the Spec's persistent worktree; return the engine bound to it and
        the Spec as the branch tells it. The invoking checkout is never touched."""
        # A base branch is a fork's question: ask it only when there is no branch
        # yet. A resume reads what the branch already records.
        forking = not self.workspace.branch_exists(f"giro/{spec.slug}")
        base_branch = self._base_branch_for(spec) if forking else ""
        ws, first_activation = self.workspace.ensure_spec_worktree(spec.slug, base_branch)
        # An escalation is answered where the Issue lives — in the Spec's own
        # worktree, from whatever branch the human is standing on. That answer
        # arrives uncommitted, and committing it is the engine's job, never the
        # human's: only then does the clean-tree check speak about debris.
        ws.commit_within(
            f"docs/specs/{spec.slug}", f"giro({spec.slug}): answer from the console"
        )
        ws.ensure_clean()  # the engine's own tree, never the human's
        run = self._bind(ws)
        forked_at = ws.head_sha()  # read before the seed commit: the base branch's tip
        if first_activation:
            self._seed_spec(spec, ws)

        on_branch = run.store.load_spec(spec.slug)
        if on_branch is None:
            raise StoreError(
                f"spec {spec.slug!r} is missing from branch giro/{spec.slug} — "
                f"restore it in {ws.root} or delete that branch to start over"
            )
        if first_activation:
            on_branch.base = forked_at  # the diff base Validate judges against
            on_branch.base_branch = base_branch  # recorded once; later Runs read it
            run.store.save_spec(on_branch)
            ws.commit_all(f"giro({spec.slug}): seed spec from {base_branch}")
        return run, on_branch

    # -- entry ---------------------------------------------------------------

    def implement(self, target: str, run_id: str = "", *, quiet: bool = False) -> Report:
        """The router: one verb, level picked by what the target is.

        The target is resolved through the branch tip wherever one exists, so a
        re-invoke from a stale — or empty — checkout resumes the real state. The
        Spec's lock is then held for the whole Run: two engines never work one
        branch.

        This call *is* the Run, and it owns the Run's record start to finish: it
        opens the Ledger — or claims the one ``run_id`` names, the entry a
        dispatch minted before this process existed — narrates every
        state-changing moment into it, and closes it with the outcome, however
        the Run ends. Opening before the target is resolved is the point: a Run
        that dies on an unknown target still says why.

        The Projection is proved before the loop leans on it and never after
        (ADR-0014): when [github_projection] enabled = true, a preflight
        failure refuses the Run rather than silently running it blind, so the
        operator gets the surface they asked for or a clear reason it is not
        possible. Runtime GitHub failures still fail soft so the loop's
        outcome never depends on the tracker.
        """
        self._driver_preflight()  # PATH before git — one clear error, not three silent attempts
        runtime = self._runtime_dir()
        self.ledger = Ledger.open(runtime, target, run_id=run_id, quiet=quiet)
        self.projection.attach(self.ledger)
        try:
            resolved = SpecView(self.store, self.workspace).resolve(target)
            spec = resolved if isinstance(resolved, Spec) else resolved[0]
            self.ledger.mark(spec=spec.slug, branch=f"giro/{spec.slug}")
            with spec_lock(runtime, spec.slug, target):
                self.projection.start()
                report = self._route(resolved, spec)
        except Exception as exc:
            self.ledger.finish("error", f"{type(exc).__name__}: {exc}")
            raise
        self.ledger.finish(report.outcome, report.detail)
        return report

    def _route(self, resolved: Spec | tuple[Spec, Issue], spec: Spec) -> Report:
        run, spec = self._open_run(spec)
        run._preflight()  # gates resolve where they run: inside the Spec worktree

        if isinstance(resolved, Spec):
            if spec.state in SPEC_TERMINAL:
                return run._report(
                    spec,
                    "done",
                    f"already {spec.state} — merge branch giro/{spec.slug} when ready",
                    run.store.load_issues(spec.slug),
                )
            return run.run_spec_loop(spec)

        issue_id = resolved[1].id
        issue = next((i for i in run.store.load_issues(spec.slug) if i.id == issue_id), None)
        if issue is None:
            raise StoreError(
                f"no issue {issue_id!r} under spec {spec.slug!r} on branch giro/{spec.slug}"
            )
        if issue.state in ISSUE_TERMINAL:
            return Report("done", issue.ref, f"already {issue.state}", {issue.ref: issue.state})
        if issue.state == "needs-human":
            issue.state = "ready"  # a human re-invoked us: that is the answer
            issue.attempts = 0
            run.store.save_issue(issue)
            # the claim's projection clears the escalation label; this says why
            run.projection.resume(spec, issue)
        state = run.run_issue_loop(spec, issue)
        outcome = "done" if state == "done" else "needs-human"
        return Report(outcome, issue.ref, f"issue {state}", {issue.ref: state})


def _check_no_cycles(issues: list[Issue], by_id: dict[str, Issue]) -> None:
    """Reject a blocked_by cycle before scheduling — a cycle can never reach a
    schedulable frontier, so it is a deadlock, not a wait. Assumes edges have
    already been checked for danglers."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {i.id: WHITE for i in issues}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        for dep in by_id[node].blocked_by:
            if color[dep] == GREY:
                cycle = " -> ".join(stack[stack.index(dep):] + [dep])
                raise StoreError(
                    f"blocked_by cycle detected: {cycle} — no Issue in it can ever "
                    "start; break the cycle by editing a blocked_by edge"
                )
            if color[dep] == WHITE:
                visit(dep, stack + [dep])
        color[node] = BLACK

    for issue in issues:
        if color[issue.id] == WHITE:
            visit(issue.id, [issue.id])
