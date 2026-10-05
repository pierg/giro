"""The two moments a human must feel: needs-human, and the ready flip.

An escalation becomes a comment carrying the durable reason, the `giro:`
escalation label, and an assignment that notifies; the answer clears the label
and says the Run resumed. Validate green takes the pull request out of draft
and posts the last word on the parent — and nothing merges. The rendered
comments are pure functions of the store and are asserted directly; the `gh`
boundary is scripted the way drivers are, so a broken GitHub is exercised
rather than assumed.
"""

import json
from pathlib import Path

from giro import cli
from giro.drivers import FakeDriver
from giro.projection import (
    FakeGh,
    GhResult,
    completion_comment,
    escalation_comment,
    resumed_comment,
)
from giro.store import log_sections
from tests.conftest import good_worker, make_engine, spec_store
from tests.test_projection import LABEL_NAMES, PROJECTION_TOML, healthy_gh, projection_for
from tests.test_projection_issues import (
    FIRST,
    PARENT,
    an_issue,
    creates,
    edits,
    field,
    flag,
    view,
)
from tests.test_projection_narration import (
    CommentingGh,
    commenting_gh,
    headings,
    posted,
    scribbler,
)
from tests.test_projection_spec import (
    demo_issues,
    demo_spec,
    escalating_worker,
    passing_judge,
    surface_gh,
    with_origin,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PULL = 13  # the draft pull request `surface_gh` opens


class DraftingGh(CommentingGh):
    """A commenting `gh` that remembers whether the pull request is a draft, so
    the flip is a state GitHub reports back rather than a call counted."""

    def __init__(self, results=None):
        super().__init__(results)
        self.draft = True

    def run(self, args: list[str]) -> GhResult:
        if args[:2] == ["pr", "view"] and "isDraft" in args:
            self.calls.append(list(args))
            return GhResult(0, json.dumps({"isDraft": self.draft}), args=list(args))
        if args[:2] == ["pr", "ready"] and "pr ready" not in self.results:
            self.calls.append(list(args))
            self.draft = False
            return GhResult(0, args=list(args))
        return super().run(args)


def drafting_gh(**scripted) -> DraftingGh:
    gh = DraftingGh(surface_gh().results)
    gh.results.update(scripted)
    return gh


def flips(gh: FakeGh) -> list[list[str]]:
    return [args for args in gh.calls if args[:2] == ["pr", "ready"]]


def assignments(gh: FakeGh, number: int) -> list[str]:
    return [flag(a, "--add-assignee") for a in edits(gh, number) if "--add-assignee" in a]


def escalations(gh: FakeGh, number: int) -> list[str]:
    return [field(a, "body") for a in posted(gh, number) if "Needs human" in field(a, "body")]


def labelled(gh: FakeGh, number: int, label: str) -> bool:
    """Whether a sub-issue wears a label — put there when it was created in this
    state, or added when the state changed under it."""
    return any(f"labels[]={label}" in args for args in creates(gh)) or any(
        label in flag(args, "--add-label") for args in edits(gh, number)
    )


# -- the desired comments, computed from the store alone ---------------------


def test_an_escalation_comment_carries_the_reason_the_findings_and_the_way_back():
    body = escalation_comment(
        "demo/01-alpha",
        "budget exhausted after 3 attempts",
        ["feature: `test -f feature.txt` exited 1"],
        assignee="octocat",
    )

    assert body.startswith("## Needs human — `demo/01-alpha`")
    assert "@octocat" in body  # the mention is the notification
    assert "budget exhausted after 3 attempts" in body
    assert "- feature: `test -f feature.txt` exited 1" in body
    assert "giro implement demo/01-alpha" in body  # re-invoking is the answer

    unassigned = escalation_comment("demo", "unfinished issue(s): 01-alpha (needs-human)")
    assert "@" not in unassigned and "unfinished issue(s)" in unassigned


def test_a_resumed_comment_says_the_answer_arrived_and_the_loop_moved():
    body = resumed_comment("demo/01-alpha")
    assert body.startswith("## Run resumed — `demo/01-alpha`")
    assert "re-invoked" in body and "escalation is cleared" in body


def test_a_completion_comment_lists_the_issues_and_leaves_the_merge_to_the_human():
    body = completion_comment(demo_spec(state="done"), demo_issues(), pr=13, ready=True)

    assert body.startswith("## Validate passed — `demo` is done")
    assert "#13" in body and "ready for review" in body
    assert "- `01-alpha` Alpha slice — done" in body
    assert "Nothing merges automatically" in body and "the merge is yours" in body


# -- needs-human, projected --------------------------------------------------


def test_an_issue_escalation_labels_comments_and_assigns_the_configured_human(project):
    with_origin(project)
    gh = commenting_gh(**{f"issue view {FIRST}": view()})
    engine = make_engine(
        project,
        worker=FakeDriver([escalating_worker]),
        projection=projection_for(gh, assignee="octocat"),
    )

    assert engine.implement("demo/01-write-feature").outcome == "needs-human"

    told = escalations(gh, FIRST)
    assert len(told) == 1
    assert "spec ambiguous: which format?" in told[0]  # the reason the log keeps
    assert "@octocat" in told[0] and "giro implement demo/01-write-feature" in told[0]
    assert assignments(gh, FIRST) == ["octocat"]
    assert labelled(gh, FIRST, "giro:needs-human")


def test_a_spec_escalation_reaches_the_parent_the_same_way(project):
    with_origin(project)
    gh = commenting_gh(**{f"issue view {FIRST}": view()})
    engine = make_engine(
        project,
        worker=FakeDriver([escalating_worker]),
        projection=projection_for(gh, assignee="octocat"),
    )

    assert engine.implement("demo").outcome == "needs-human"

    told = escalations(gh, PARENT)
    assert len(told) == 1
    assert "unfinished issue(s): 01-write-feature (needs-human)" in told[0]
    assert "@octocat" in told[0] and "giro implement demo" in told[0]
    assert assignments(gh, PARENT) == ["octocat"]
    assert escalations(gh, FIRST)  # and the Issue said it where it stopped


def test_the_human_is_assigned_once_however_often_giro_escalates():
    gh = commenting_gh(
        **{
            "issue view 41 --repo acme/widgets --json assignees": GhResult(
                0, json.dumps({"assignees": [{"login": "octocat"}]})
            )
        }
    )
    projection = projection_for(gh, assignee="octocat")
    spec = demo_spec(github_issue=PARENT)
    issue = an_issue(state="needs-human", github_issue=41)

    projection.escalate(spec, issue, "budget exhausted after 3 attempts")
    projection.escalate(spec, issue, "budget exhausted after 3 attempts")

    assert assignments(gh, 41) == []  # already theirs: compared before written
    assert len(escalations(gh, 41)) == 1  # and the moment is told once


def test_no_configured_assignee_still_labels_and_comments_and_assigns_nobody():
    gh = commenting_gh()
    projection = projection_for(gh)  # no assignee configured

    projection.escalate(demo_spec(github_issue=PARENT), None, "unfinished issue(s): 01-alpha")

    assert len(escalations(gh, PARENT)) == 1
    assert assignments(gh, PARENT) == []
    assert [a for a in gh.calls if a[:2] == ["issue", "view"]] == []  # nothing to compare


def test_a_re_dispatch_clears_the_escalation_and_says_the_run_resumed(project):
    with_origin(project)
    gh = commenting_gh(
        **{f"issue view {FIRST}": view(labels=["giro:issue", "giro:needs-human"])}
    )
    escalated = make_engine(
        project,
        worker=FakeDriver([escalating_worker]),
        projection=projection_for(gh, assignee="octocat"),
    )
    assert escalated.implement("demo/01-write-feature").outcome == "needs-human"
    gh.calls.clear()

    answered = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh, assignee="octocat"),
    )
    assert answered.implement("demo/01-write-feature").outcome == "done"

    resumed = [a for a in posted(gh, FIRST) if "Run resumed" in field(a, "body")]
    assert len(resumed) == 1
    assert [a for a in edits(gh, FIRST) if "giro:needs-human" in flag(a, "--remove-label")]
    assert escalations(gh, FIRST) == []  # the answered Run raises nothing


def test_a_re_dispatched_spec_says_so_on_the_parent(project):
    """A Spec whose validate budget ran out: every Issue is done, the Spec is
    needs-human, and re-invoking it is the answer."""
    with_origin(project)
    gh = commenting_gh(**{f"issue view {FIRST}": view()})
    refusing = FakeDriver([{"verdict": "fail", "findings": [{"summary": "gap: no gap.txt"}]}] * 3)

    escalated = make_engine(
        project,
        worker=FakeDriver([good_worker, scribbler(1), scribbler(2)]),
        judge=refusing,
        projection=projection_for(gh),
    )
    assert escalated.implement("demo").outcome == "needs-human"
    assert escalations(gh, PARENT)  # the Spec's own escalation, on the parent
    gh.calls.clear()

    answered = make_engine(
        project, worker=FakeDriver([]), judge=passing_judge(), projection=projection_for(gh)
    )
    assert answered.implement("demo").outcome == "all-done"

    assert [a for a in posted(gh, PARENT) if "Run resumed — `demo`" in field(a, "body")]
    assert escalations(gh, PARENT) == []  # the answered Run raises nothing


# -- the ready flip ----------------------------------------------------------


def test_validate_green_flips_the_draft_to_ready_and_posts_the_last_word(project):
    with_origin(project)
    gh = drafting_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    assert [a[2] for a in flips(gh)] == [str(PULL)]
    assert gh.draft is False
    assert [a for a in gh.calls if a[:2] == ["pr", "merge"]] == []  # the merge is a human's
    last = field(posted(gh, PARENT)[-1], "body")
    assert last.startswith("## Validate passed — `demo` is done")
    assert f"#{PULL}" in last and "ready for review" in last
    assert "Nothing merges automatically" in last


def test_the_flip_reads_the_draft_state_first_so_projecting_twice_changes_nothing():
    gh = drafting_gh()
    projection = projection_for(gh)
    spec = demo_spec(state="done", github_issue=PARENT, github_pr=PULL)

    projection.complete(spec, demo_issues())
    projection.complete(spec, demo_issues())

    assert len(flips(gh)) == 1  # already ready: read, compared, left alone
    assert headings(gh, PARENT).count("## Validate passed — `demo` is done") == 1


def test_a_spec_that_escalates_never_flips_its_pull_request(project):
    with_origin(project)
    gh = drafting_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([escalating_worker]),
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "needs-human"

    assert flips(gh) == [] and gh.draft is True


# -- the token's identity ----------------------------------------------------


def test_preflight_names_the_token_identity_and_warns_when_it_is_the_human_itself():
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["api user"] = GhResult(0, "octocat\n")

    itself = projection_for(gh, assignee="octocat").preflight()

    assert itself.ready  # a notification nobody receives is not a refusal
    identity = next(check for check in itself.checks if check.name == "identity")
    assert identity.state == "ok" and identity.detail == "@octocat"
    text = "\n".join(itself.lines())
    assert "never notifies anyone about their own actions" in text
    assert "machine account" in text

    machine = projection_for(gh, assignee="giro-bot").preflight()
    assert machine.ready and machine.warnings == []

    nobody = projection_for(gh).preflight()  # nobody to notify is a choice, not a hazard
    assert nobody.ready and nobody.warnings == []
    unassigned = next(check for check in nobody.checks if check.name == "identity")
    assert "notifies nobody" in unassigned.detail


def test_project_check_surfaces_the_self_notification_limitation(project, monkeypatch, capsys):
    (project / "giro.toml").write_text(PROJECTION_TOML)
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["api user"] = GhResult(0, "octocat\n")
    monkeypatch.setattr(
        cli, "build_projection", lambda cfg, workspace: projection_for(gh, assignee="octocat")
    )

    assert cli.main(["-C", str(project), "project", "--check"]) == 0
    human = capsys.readouterr().out
    assert "@octocat" in human and "machine account" in human

    assert cli.main(["-C", str(project), "project", "--check", "--json"]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert any("machine account" in warning for warning in machine["warnings"])


def test_a_run_says_the_limitation_out_loud_before_it_leans_on_the_projection(project, capsys):
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["api user"] = GhResult(0, "octocat\n")
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        projection=projection_for(gh, assignee="octocat"),
    )

    assert engine.implement("demo/01-write-feature").outcome == "done"
    assert "machine account" in capsys.readouterr().err


def test_the_docs_carry_the_limitation_and_the_machine_account_recommendation():
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    assert "machine account" in design and "own actions" in design

    from giro.config import INIT_TEMPLATE

    assert "assignee" in INIT_TEMPLATE and "machine account" in INIT_TEMPLATE


# -- fail-soft ---------------------------------------------------------------


def test_an_escalation_lands_in_full_when_runtime_gh_calls_fail(project):
    """The inverted rule at the moment it matters most: preflight is fine
    (the operator has already turned Projection on and the repository is
    reachable), but the specific calls the escalation would post — comments,
    label edits, the assignment — fail mid-Run. A Run nobody can be notified
    about must still escalate as loudly in markdown and the Ledger."""
    with_origin(project)
    gh = healthy_gh(existing=LABEL_NAMES)
    # Every content-creating call the escalation makes fails at runtime.
    gh.results["issue create"] = GhResult(1, stderr="upstream request timeout")
    gh.results["issue comment"] = GhResult(1, stderr="upstream request timeout")
    gh.results["issue edit"] = GhResult(1, stderr="upstream request timeout")
    engine = make_engine(
        project,
        worker=FakeDriver([escalating_worker]),
        projection=projection_for(gh, assignee="octocat"),
    )

    report = engine.implement("demo/01-write-feature")

    assert report.outcome == "needs-human"  # identical to the projected Run
    issue = spec_store(project).load_issues("demo")[0]
    assert issue.state == "needs-human"
    assert "spec ambiguous: which format?" in "\n".join(log_sections(issue.body))
    escalated = [e for e in engine.ledger.events() if e["type"] == "escalation"]
    assert escalated and escalated[0]["reason"] == "spec ambiguous: which format?"


def test_a_refused_flip_costs_the_flip_and_nothing_else(project):
    with_origin(project)
    gh = drafting_gh(**{"pr ready": GhResult(1, stderr="HTTP 403: Forbidden")})
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    report = engine.implement("demo")

    assert report.outcome == "all-done"  # identical to the flipped Run
    assert spec_store(project).load_spec("demo").state == "done"
    events = [e for e in engine.ledger.events() if e["type"] == "projection"]
    assert any(e.get("action") == "pr-ready" and e.get("ok") is False for e in events)
    # the last word is still posted — but it must not claim a flip that failed soft
    completions = [a for a in posted(gh, PARENT) if "Validate passed" in field(a, "body")]
    assert completions
    last = field(completions[-1], "body")
    assert "ready for review" not in last
    assert "could not be flipped out of draft" in last
