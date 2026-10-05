"""Reconcile — GitHub reconverged on the markdown store.

One command recomputes the surface a Spec *should* have and converges GitHub
to it: create what is missing, update what drifted, touch nothing human-owned.
It is the Projection's disaster recovery after an outage, its answer to
hand-meddling, and the operative proof of ADR-0014 — GitHub is display, not
memory. Everything it renders is derivable from the markdown, so nothing is
lost by a Run that projected badly or not at all.

Strictly one direction. Reconcile reads the store — through each Spec's branch
tip where there is one (ADR-0013), the checkout where there is not — and writes
only to GitHub: no commit, no push, no worktree, no identifier written back to
frontmatter. What frontmatter cannot say is asked of GitHub instead: every
generated body carries a hidden marker, so a surface with no identifier
recorded is *found* rather than made a second time, and reconciling twice
changes nothing.

Two things a Run tells that the store cannot, and Reconcile therefore leaves
alone: the orchestration narration keyed to a Run (its plan, its waves, its
Validate verdicts, its escalation comments) and the commit statuses that hang
on the head a checkpoint published. Both are one Run's story; the Issue logs
that carry the same facts are in the store and are rendered in full.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import Config
from .projection import Change, Projection, ProjectionError, TallyGh, build_projection
from .store import Issue, Spec, SpecView, Store
from .workspace import Workspace


@dataclass
class SpecReport:
    """What reconciling one Spec came to."""

    slug: str
    state: str
    issue: int = 0  # the tracking issue, once it exists
    pr: int = 0
    changes: list[Change] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        for change in self.changes:
            tally[change.kind] = tally.get(change.kind, 0) + 1
        return tally

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "state": self.state,
            "issue": self.issue,
            "pr": self.pr,
            "counts": self.counts,
            "changes": [c.as_dict() for c in self.changes],
        }

    def lines(self) -> list[str]:
        where = f" — tracking issue #{self.issue}" if self.issue else ""
        pull = f", pull request #{self.pr}" if self.pr else ""
        counts = self.counts
        summary = ", ".join(f"{counts[kind]} {kind}" for kind in ("created", "updated", "skipped"))
        if counts["failed"]:
            summary += f", {counts['failed']} failed"
        return [
            f"{self.slug} [{self.state}]{where}{pull}",
            *(f"  {change}" for change in self.changes),
            f"  {summary}",
        ]


@dataclass
class ReconcileReport:
    """What one Reconcile did, and whether GitHub now says what the store does."""

    repo: str
    ready: bool = True
    failure: str = ""
    # What the repository itself needed — the `giro:` label set, restored when
    # a hand deleted one of them. Repository-wide, so it belongs to no Spec.
    repo_changes: list[Change] = field(default_factory=list)
    specs: list[SpecReport] = field(default_factory=list)

    @property
    def failures(self) -> list[Change]:
        """Every call the convergence could not make — reads as well as writes.
        A read that failed is a part of the surface this Reconcile never looked
        at and therefore never converged, which is a gap like any other."""
        return [c for c in self.repo_changes if c.kind == "failed"] + [
            c for spec in self.specs for c in spec.changes if c.kind == "failed"
        ]

    @property
    def ok(self) -> bool:
        """Whether every call landed. A failure here is reported, never raised:
        the surface converges as far as it can and says where it stopped."""
        return self.ready and not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ready": self.ready,
            "repo": self.repo,
            "failure": self.failure,
            "repo_changes": [c.as_dict() for c in self.repo_changes],
            "specs": [spec.as_dict() for spec in self.specs],
        }

    def lines(self) -> list[str]:
        if not self.ready:
            return [f"repo {self.repo or '(none)'}", f"not ready — {self.failure}"]
        out = [f"repo {self.repo} — {len(self.specs)} spec(s)"]
        out += [f"  {change}" for change in self.repo_changes]
        for spec in self.specs:
            out += spec.lines()
        if not self.specs:
            out.append("nothing to reconcile — no Spec has been activated yet")
        elif self.ok:
            out.append("GitHub now says what the markdown says")
        else:
            out.append(
                f"partially reconciled — {len(self.failures)} call(s) failed: "
                f"{self._named_failures()}. GitHub does not yet say everything the "
                "markdown says; run `giro project` again when it answers."
            )
        return out

    def _named_failures(self, most: int = 5) -> str:
        """What could not be read or written, said by name — the whole point of
        a report a human reads after an outage."""
        named = [change.what.partition(":")[0].strip() for change in self.failures[:most]]
        if len(self.failures) > most:
            named.append(f"and {len(self.failures) - most} more")
        return ", ".join(named)


@dataclass
class Reconciler:
    """The convergence itself: the store on one side, GitHub on the other.

    The Projection handed in is the same one a Run uses — every write goes
    through its find-or-create, compare-before-writing convergence, so this
    class decides *what* to reconverge and the Projection decides *how*. Its
    ``gh`` is wrapped on the first run and stays wrapped, because the writes
    that pass through it are the report.
    """

    projection: Projection
    view: SpecView
    workspace: Workspace

    def run(self, target: str = "") -> ReconcileReport:
        """Reconverge one Spec, or every Spec a Run has ever worked on."""
        if not self.projection.settings.enabled:
            raise ProjectionError(
                "Projection is off — set enabled = true in [github_projection] of "
                "giro.toml (`giro project --check` proves the repository first)"
            )
        tally = self._tally()
        report = ReconcileReport(repo=self.projection.repo)
        preflight = self.projection.preflight()  # also restores a deleted label set
        report.repo_changes = tally.take() if tally is not None else []
        if not preflight.ready:
            return replace(report, ready=False, failure=preflight.failure)
        if tally is not None:
            # Past preflight, every call is the convergence: a read that fails
            # from here leaves the surface short of the store, and the report
            # must say so rather than claim a convergence it did not make.
            tally.watch_reads = True
        for spec in self._specs(target):
            report.specs.append(self._spec(spec, tally))
        return report

    def _tally(self) -> TallyGh | None:
        """The Projection's boundary, watching what it writes. Wrapped once,
        however many times this Reconciler is asked to run — a tally of a tally
        would count every change twice — and handed back on preflight's terms,
        because every run begins there."""
        if self.projection.gh is None:
            return None
        if not isinstance(self.projection.gh, TallyGh):
            self.projection.gh = TallyGh(self.projection.gh)
        self.projection.gh.watch_reads = False
        return self.projection.gh

    def _specs(self, target: str) -> list[Spec]:
        """Which Specs to reconverge, read where each one's truth lives."""
        if target:
            resolved = self.view.resolve(target)
            spec = resolved if isinstance(resolved, Spec) else resolved[0]
            return [spec]
        return self.view.list_specs()

    def _spec(self, spec: Spec, tally: TallyGh | None) -> SpecReport:
        entry = SpecReport(slug=spec.slug, state=spec.state)
        # A Spec is projected from its first checkpoint, and a checkpoint is a
        # thing a Run does on a branch: no branch, no Run, nothing to converge.
        if not self.view.on_branch(spec.slug):
            entry.changes.append(
                Change("skipped", f"no giro/{spec.slug} branch — nothing has run for this Spec")
            )
            return entry
        issues = self.view.load_issues(spec.slug)
        surface = self.projection.discover(spec, issues)
        known = [replace(i, github_issue=surface.issues.get(i.id, 0)) for i in issues]
        published = self.workspace.remote_branch_exists(f"giro/{spec.slug}")
        # The phase is a live Run's word for where it is; the store knows the
        # state alone, so that is what a reconciled zone says.
        rendered = self.projection.project_spec(
            replace(spec, github_issue=surface.issue, github_pr=surface.pr),
            known,
            branch_published=published,
        )
        entry.issue, entry.pr = rendered.issue, rendered.pr
        projected = replace(spec, github_issue=rendered.issue, github_pr=rendered.pr)
        # the numbers as they stand *after* the convergence: what discovery
        # found, plus whatever it had to make
        rendered_issues = [replace(i, github_issue=rendered.issues.get(i.id, 0)) for i in issues]
        if not rendered.pr and not published:
            entry.changes.append(
                Change("skipped", f"pull request — giro/{spec.slug} is not on the remote yet")
            )
        if spec.state == "done":
            # the ready flip and the last word on the parent: both are what
            # `done` in the store means, so both are Reconcile's to restore
            self.projection.complete(projected, issues)
        self._notify(projected, rendered_issues)
        entry.changes += tally.take() if tally is not None else []
        return entry

    def _notify(self, spec: Spec, issues: list[Issue]) -> None:
        """The assignment an escalation makes, restored: the label follows state
        through ordinary convergence, and this is the half that notifies.

        The escalation *comment* is a Run's own words, keyed to that Run — the
        durable reason is in the Issue's log, which is rendered verbatim as the
        sub-issue's own comments either way.
        """
        if spec.state == "needs-human" and spec.github_issue:
            self.projection.assign(spec.github_issue)
        for issue in issues:
            if issue.state == "needs-human" and issue.github_issue:
                self.projection.assign(issue.github_issue)


def build_reconciler(cfg: Config, root: Path, projection: Projection | None = None) -> Reconciler:
    """A Reconciler for a checkout: the store as its branches tell it, and the
    Projection wired from the same config a Run would use."""
    workspace = Workspace(root)
    return Reconciler(
        projection=projection or build_projection(cfg, workspace),
        view=SpecView(Store(root), workspace),
        workspace=workspace,
    )
