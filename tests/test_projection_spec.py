"""Spec lifecycle projection: the parent tracking issue, the draft pull
request, the engine zone in its body, and the checkpoints that publish them.

The desired surface is a pure function of the store, so it is asserted
directly; the `gh` boundary is scripted the way drivers are, and the pushes
are real ones against a bare repository — so "never force-pushes" is asserted,
not assumed.
"""

import json
import subprocess
from pathlib import Path

from giro.drivers import FakeDriver
from giro.projection import (
    ZONE_END,
    ZONE_START,
    FakeGh,
    GhResult,
    parent_body,
    render_zone,
    spec_labels,
    splice_zone,
)
from giro.store import Issue, Spec
from giro.workspace import Workspace, WorkspaceError
from tests.conftest import git, good_worker, make_engine, spec_store
from tests.test_parallel import parallel_project, route_by_title
from tests.test_projection import LABEL_NAMES, healthy_gh, projection_for

PARENT = "https://github.com/acme/widgets/issues/12"
PULL = "https://github.com/acme/widgets/pull/13"


def surface_gh(labels=("giro:spec",), body="") -> FakeGh:
    """A `gh` that answers preflight and the Spec surface: creating the parent
    and the pull request, and reporting ``labels`` and ``body`` as GitHub's."""
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["issue create"] = GhResult(0, PARENT + "\n")
    gh.results["pr create"] = GhResult(0, PULL + "\n")
    gh.results["issue view"] = GhResult(0, json.dumps({"labels": [{"name": n} for n in labels]}))
    gh.results["pr view"] = GhResult(0, json.dumps({"body": body}))
    return gh


def with_origin(project: Path) -> Path:
    """A bare repository as origin, so a checkpoint's push is a real push."""
    remote = project.parent / "origin.git"
    git(project, "init", "--bare", str(remote))
    git(project, "remote", "add", "origin", str(remote))
    return remote


def spy_git(monkeypatch) -> list[tuple[list[str], int]]:
    """Every git invocation the engine makes, with how it ended."""
    calls: list[tuple[list[str], int]] = []
    original = Workspace._git

    def record(self, *args, **kwargs):
        try:
            proc = original(self, *args, **kwargs)
        except WorkspaceError:
            calls.append((list(args), -1))
            raise
        calls.append((list(args), proc.returncode))
        return proc

    monkeypatch.setattr(Workspace, "_git", record)
    return calls


def watch_pushes(monkeypatch, hang: bool = False) -> list[dict]:
    """Every push as git actually receives it, with how it is bounded.

    ``hang`` plays the remote that silently drops packets — a VPN down, a
    firewall — so the call never comes back and only the bound ends it.

    F6 — the engine spawns git via subprocess.Popen (start_new_session=True)
    with a bounded ``communicate(timeout=...)``. We match on Popen and on
    ``communicate`` for the hang path.
    """
    calls: list[dict] = []
    original_popen = subprocess.Popen

    class _Watched:
        def __init__(self, argv, **kwargs):
            self._proc = original_popen(argv, **kwargs)
            self._hang = False
            self._argv = argv
            self.pid = self._proc.pid
            argv_list = list(argv)
            head = argv_list[:2]
            # engine argv includes `-c commit.gpgsign=false` so `git push`
            # sits at [3:5]; also handle the plain form for other callers.
            is_push = (head == ["git", "push"]) or (
                argv_list[:1] == ["git"] and "push" in argv_list[1:5]
            )
            if is_push:
                calls.append({"argv": argv_list, **kwargs})
                self._hang = hang

        def communicate(self, input=None, timeout=None):
            # push calls carry their bound as ``communicate(timeout=...)`` now;
            # attach it to the recorded call so tests can assert on it.
            for call in calls:
                if call.get("argv") is self._argv or call.get("argv") == list(self._argv):
                    call["timeout"] = timeout
            if self._hang:
                raise subprocess.TimeoutExpired(self._argv, timeout or 0)
            return self._proc.communicate(input=input, timeout=timeout)

        @property
        def returncode(self):
            return self._proc.returncode

        def kill(self):
            return self._proc.kill()

    monkeypatch.setattr(subprocess, "Popen", _Watched)
    return calls


def pushes(calls: list[tuple[list[str], int]]) -> list[tuple[list[str], int]]:
    return [(args, code) for args, code in calls if args and args[0] == "push"]


def demo_spec(state: str = "active", **fields) -> Spec:
    return Spec(
        slug="demo",
        path=Path("docs/specs/demo/SPEC.md"),
        state=state,
        base_branch="main",
        body="# Demo feature\n",
        title="Demo feature",
        **fields,
    )


def demo_issues() -> list[Issue]:
    return [
        Issue("demo", "01-alpha", Path("01-alpha.md"), "done", title="Alpha slice"),
        Issue("demo", "02-beta", Path("02-beta.md"), "ready", title="Beta slice"),
    ]


def escalating_worker(prompt, cwd) -> dict:
    return {"outcome": "needs-human", "summary": "spec ambiguous: which format?"}


def passing_judge() -> FakeDriver:
    return FakeDriver([{"verdict": "pass"}])


# -- the desired surface, computed from the store alone ----------------------


def test_the_engine_zone_carries_the_checklist_the_phase_and_the_closing_keyword():
    zone = render_zone(demo_spec(), demo_issues(), parent=12, phase="wave 1")

    assert zone.startswith(ZONE_START) and zone.endswith(ZONE_END)
    assert "Closes #12" in zone  # the human's merge closes the parent
    assert "- state: **active** — wave 1" in zone
    assert "- [x] `01-alpha` Alpha slice — done" in zone
    assert "- [ ] `02-beta` Beta slice — ready" in zone
    assert "base branch: `main`" in zone

    planned = render_zone(demo_spec(), [], parent=12, phase="activated")
    assert "no Issues yet" in planned and "Closes #12" in planned


def test_the_states_a_spec_wears_on_its_parent_issue():
    assert spec_labels("active") == {"giro:spec"}
    assert spec_labels("draft") == {"giro:spec"}
    assert spec_labels("needs-human") == {"giro:spec", "giro:needs-human"}
    assert spec_labels("done") == {"giro:spec", "giro:done"}
    assert "docs/specs/demo/SPEC.md" in parent_body(demo_spec())


def test_the_engine_zone_updates_in_place_and_never_touches_human_prose():
    before = "Reviewer: start with the migration.\n\n"
    after = "\n\n### Notes\n\nI rebased this by hand.\n"
    body = before + render_zone(demo_spec(), [], 12, "activated") + after

    updated = splice_zone(body, render_zone(demo_spec(), demo_issues(), 12, "wave 1"))

    assert updated.startswith(before) and updated.endswith(after)
    assert "wave 1" in updated and "activated" not in updated
    assert updated.count(ZONE_START) == 1 and updated.count(ZONE_END) == 1
    # splicing the same zone back is a no-op — projecting twice changes nothing
    assert splice_zone(updated, render_zone(demo_spec(), demo_issues(), 12, "wave 1")) == updated

    fenceless = splice_zone("A pull request a human opened.", render_zone(demo_spec(), [], 12))
    assert fenceless.startswith("A pull request a human opened.") and ZONE_START in fenceless


def test_projecting_twice_writes_nothing_the_second_time():
    spec = demo_spec(github_issue=12, github_pr=13)
    issues = demo_issues()
    settled = render_zone(spec, issues, 12, "wave 1")
    gh = surface_gh(labels=["giro:spec"], body=splice_zone("", settled))
    projection = projection_for(gh)

    projection.project_spec(spec, issues, "wave 1")
    projection.project_spec(spec, issues, "wave 1")

    assert [args for args in gh.calls if args[1:2] in (["create"], ["edit"])] == []
    assert [args[:2] for args in gh.calls].count(["pr", "view"]) == 2  # read, compared, left


def test_a_drifted_pull_request_body_is_re_rendered_around_the_human_prose():
    spec = demo_spec(github_issue=12, github_pr=13)
    prose = "Reviewer: start with the migration.\n\n"
    stale = prose + render_zone(spec, [], 12, "activated") + "\n\nSigned, a human.\n"
    gh = surface_gh(body=stale)

    projection_for(gh).project_spec(spec, demo_issues(), "wave 1")

    edit = next(args for args in gh.calls if args[:2] == ["pr", "edit"])
    body = edit[edit.index("--body") + 1]
    assert body.startswith(prose) and body.endswith("Signed, a human.\n")
    assert "- [x] `01-alpha` Alpha slice — done" in body and "activated" not in body


# -- a Run, projected --------------------------------------------------------


def test_first_activation_creates_the_parent_and_the_draft_pull_request_exactly_once(project):
    with_origin(project)
    gh = surface_gh()
    engine = make_engine(
        project, worker=FakeDriver([escalating_worker]), projection=projection_for(gh)
    )

    assert engine.implement("demo").outcome == "needs-human"

    assert len([args for args in gh.calls if args[:2] == ["issue", "create"]]) == 1
    created = next(args for args in gh.calls if args[:2] == ["pr", "create"])
    assert len([args for args in gh.calls if args[:2] == ["pr", "create"]]) == 1
    assert "--draft" in created  # a review surface from the first commit, not a merge
    assert created[created.index("--base") + 1] == "main"
    assert created[created.index("--head") + 1] == "giro/demo"
    assert "Closes #12" in created[created.index("--body") + 1]

    spec = spec_store(project).load_spec("demo")
    assert spec.github_issue == 12 and spec.github_pr == 13

    # a later Run finds the surface through the identifiers in frontmatter
    gh.calls.clear()
    resumed = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )
    assert resumed.implement("demo/01-write-feature").outcome == "done"
    assert [args for args in gh.calls if args[1:2] == ["create"]] == []
    assert any(args[:2] == ["pr", "edit"] for args in gh.calls)  # the zone re-rendered


def test_spec_needs_human_and_done_are_labels_on_a_parent_that_stays_open(project):
    with_origin(project)
    gh = surface_gh()
    escalated = make_engine(
        project, worker=FakeDriver([escalating_worker]), projection=projection_for(gh)
    )
    assert escalated.implement("demo").outcome == "needs-human"

    edits = [args for args in gh.calls if args[:2] == ["issue", "edit"]]
    assert any("giro:needs-human" in args[args.index("--add-label") + 1] for args in edits)

    # GitHub now carries what that Run applied; the answer is a re-invoke
    gh.results["issue view"] = GhResult(
        0, json.dumps({"labels": [{"name": "giro:spec"}, {"name": "giro:needs-human"}]})
    )
    gh.calls.clear()
    answered = make_engine(
        project,
        worker=FakeDriver([good_worker, good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )
    assert answered.implement("demo/01-write-feature").outcome == "done"
    assert answered.implement("demo").outcome == "all-done"

    edits = [args for args in gh.calls if args[:2] == ["issue", "edit"]]
    last = edits[-1]
    assert "giro:done" in last[last.index("--add-label") + 1]
    assert "giro:needs-human" in last[last.index("--remove-label") + 1]
    assert [args for args in gh.calls if args[:2] == ["issue", "close"]] == []
    assert spec_store(project).load_spec("demo").state == "done"  # open for the merge to close


def test_pushes_happen_only_at_post_decision_checkpoints(project, monkeypatch):
    with_origin(project)
    calls = spy_git(monkeypatch)
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(surface_gh()),
    )

    assert engine.implement("demo").outcome == "all-done"

    # activation, the Issue's final state, Validate — and nothing mid-attempt
    assert len(pushes(calls)) == 3
    for args, code in pushes(calls):
        assert code == 0  # every push a fast-forward: published history only grew
        assert not [a for a in args if a in ("--force", "-f", "--force-with-lease")]
        assert not [a for a in args if a.startswith("+")]  # nor a forcing refspec
    local = git(project, "rev-parse", "giro/demo")
    assert git(project, "rev-parse", "refs/remotes/origin/giro/demo") == local


def test_a_run_that_resets_after_a_failed_integrated_verify_never_force_pushes(
    project, monkeypatch
):
    # Alpha and Beta pass alone and fail merged: the second merge is reset.
    parallel_project(
        project,
        gate="test $(ls marker-* 2>/dev/null | wc -l) -lt 2",
        attempts=1,
        issues=[
            ("Alpha slice", "Write marker-alpha.", ()),
            ("Beta slice", "Write marker-beta.", ()),
        ],
    )
    with_origin(project)
    calls = spy_git(monkeypatch)
    worker = route_by_title(
        {
            "Alpha": lambda cwd: (cwd / "marker-alpha").write_text("a\n"),
            "Beta": lambda cwd: (cwd / "marker-beta").write_text("b\n"),
        }
    )
    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker]),
        judge=FakeDriver([]),
        projection=projection_for(surface_gh()),
    )

    assert engine.implement("demo").outcome == "needs-human"

    assert pushes(calls)
    for args, code in pushes(calls):
        assert code == 0  # never rejected, so never a candidate for forcing
        assert not [a for a in args if a in ("--force", "-f", "--force-with-lease")]
    # the reset merge was never published: the remote is the decided history
    published = git(project, "log", "--format=%H", "refs/remotes/origin/giro/demo")
    assert published == git(project, "log", "--format=%H", "giro/demo")
    assert "marker-beta" not in git(project, "show", "--stat", "refs/remotes/origin/giro/demo")


def test_a_checkpoint_push_is_bounded_and_never_asks_a_question(project, monkeypatch):
    with_origin(project)
    pushed = watch_pushes(monkeypatch)
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(surface_gh(), timeout=7),
    )

    assert engine.implement("demo").outcome == "all-done"

    assert pushed
    for call in pushed:
        assert call["timeout"] == 7  # the Projection's bound, like every gh call
        assert call["stdin"] is subprocess.DEVNULL  # no stdin to block a read on
        assert call["env"]["GIT_TERMINAL_PROMPT"] == "0"  # a credential is not a question
        assert "BatchMode=yes" in call["env"]["GIT_SSH_COMMAND"]  # nor is a host key


def test_a_remote_that_never_answers_costs_visibility_not_work(project, monkeypatch):
    """The inverted rule at the push: a Run behind a dead network renders less
    and does exactly as much (ADR-0014)."""
    with_origin(project)
    pushed = watch_pushes(monkeypatch, hang=True)
    gh = surface_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh, timeout=1),
    )

    report = engine.implement("demo")

    assert report.outcome == "all-done"  # identical to the projected Run
    assert report.issues == {"demo/01-write-feature": "done"}
    assert spec_store(project).load_spec("demo").state == "done"
    assert len(pushed) == 3  # every checkpoint still tried; none of them waited
    # what could be rendered still was; the pull request waits for a published head
    assert [args for args in gh.calls if args[:2] == ["issue", "create"]]
    assert [args for args in gh.calls if args[:2] == ["pr", "create"]] == []
    events = [e for e in engine.ledger.events() if e["type"] == "projection"]
    assert any(e.get("action") == "push" and e.get("ok") is False for e in events)


def test_projection_off_leaves_the_run_exactly_as_it_was(project, monkeypatch):
    """With [github_projection] enabled = false the Run makes no GitHub calls
    at all and no branch is pushed — the loop's outcome is identical to a
    projected Run. (When enabled = true and preflight fails, the Run refuses
    to start rather than running silently — that path is covered in
    test_projection.py::test_preflight_failure_refuses_the_run.)
    """
    with_origin(project)
    calls = spy_git(monkeypatch)
    gh = surface_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh, enabled=False),
    )

    report = engine.implement("demo")

    assert report.outcome == "all-done"  # identical to the projected Run
    assert engine.projection.enabled is False
    assert pushes(calls) == []  # nothing published without a Projection
    assert [args for args in gh.calls if args[1:2] == ["create"]] == []
    spec = spec_store(project).load_spec("demo")
    assert spec.github_issue == 0 and spec.github_pr == 0
