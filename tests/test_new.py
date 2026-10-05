"""The seam: `giro new` scaffolds conformant artifacts; dangling edges fail loud."""

import pytest

from giro.cli import main
from giro.drivers import FakeDriver
from giro.store import Store, StoreError
from tests.conftest import git, good_worker, make_engine


def test_new_spec_scaffolds_conformant_draft(project, capsys):
    assert main(["-C", str(project), "new", "spec", "Payment Retry"]) == 0
    store = Store(project)
    spec = store.load_spec("payment-retry")
    assert spec is not None and spec.state == "draft"
    assert spec.title == "Payment Retry"
    assert "the body is yours" in spec.body
    out = capsys.readouterr().out
    assert "giro implement payment-retry" in out


def test_new_spec_rejects_duplicate(project):
    assert main(["-C", str(project), "new", "spec", "demo"]) == 1  # seeded spec exists


def test_new_issue_scaffolds_ready_with_edges(project):
    assert (
        main(
            [
                "-C", str(project), "new", "issue", "demo", "Second slice",
                "--blocked-by", "01-write-feature",
            ]
        )
        == 0
    )
    issue = Store(project).load_issues("demo")[1]
    assert issue.id == "02-second-slice"
    assert issue.state == "ready" and issue.attempts == 0
    assert issue.blocked_by == ["01-write-feature"]


def test_new_issue_rejects_unknown_edge_and_missing_spec(project, capsys):
    assert (
        main(["-C", str(project), "new", "issue", "demo", "X", "--blocked-by", "99-nope"])
        == 1
    )
    assert "99-nope" in capsys.readouterr().err
    assert len(Store(project).load_issues("demo")) == 1  # nothing was filed
    assert main(["-C", str(project), "new", "issue", "ghost", "X"]) == 1


def test_engine_fails_loud_on_dangling_edge(project):
    store = Store(project)
    issue = store.load_issues("demo")[0]
    issue.blocked_by = ["99-typo"]
    store.save_issue(issue)
    git(project, "add", "-A")
    git(project, "commit", "-m", "introduce dangling edge")

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    with pytest.raises(StoreError, match="99-typo"):
        engine.implement("demo")


def test_status_flags_dangling_edge(project, capsys):
    store = Store(project)
    issue = store.load_issues("demo")[0]
    issue.blocked_by = ["99-typo"]
    store.save_issue(issue)
    assert main(["-C", str(project), "status"]) == 0  # a dangling edge warns, but exit stays 0
    assert "unknown issue(s): 99-typo" in capsys.readouterr().out


def test_status_exits_needs_human_on_escalation(project, capsys):
    store = Store(project)
    issue = store.load_issues("demo")[0]
    issue.state = "needs-human"
    store.save_issue(issue)
    assert main(["-C", str(project), "status"]) == 2  # CI/chat can poll the exit code
    assert "needs-human — the loop is waiting on you" in capsys.readouterr().out


def test_plain_issue_with_no_frontmatter_parses_as_ready(project):
    """An Issue authored without giro — no frontmatter at all — is a valid plain
    artifact, not an error: it reads back at its initial state (`ready`), and the
    engine stamps the real frontmatter as it runs."""
    plain = project / "docs" / "specs" / "demo" / "issues" / "02-plain.md"
    plain.write_text("# A hand-written slice\n\nDo the thing.\n")
    issue = next(i for i in Store(project).load_issues("demo") if i.id == "02-plain")
    assert issue.state == "ready" and issue.attempts == 0 and issue.blocked_by == []


def test_invalid_state_still_fails_loud(project):
    """Tolerance covers *absent* frontmatter, never garbage: a present-but-invalid
    state is a loud error, so the engine never trusts a malformed artifact."""
    bad = project / "docs" / "specs" / "demo" / "issues" / "02-bad.md"
    bad.write_text("---\nstate: whoops\n---\n# Bad\n")
    with pytest.raises(StoreError, match="invalid issue state"):
        Store(project).load_issues("demo")


def test_planner_context_receives_plan_skill_judgment(project):
    store = Store(project)
    store.load_issues("demo")[0].path.unlink()
    git(project, "add", "-A")
    git(project, "commit", "-m", "empty spec")

    planner = FakeDriver(
        [{"issues": [{"title": "Write feature file", "body": "Create feature.txt."}]}]
    )
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
        planner=planner,
    )
    assert engine.implement("demo").outcome == "all-done"
    prompt = planner.calls[0]["prompt"]
    assert "tracer-bullet" in prompt  # the plan skill's judgment rode along
    assert "expand–contract" in prompt


def test_new_issue_lands_on_branch_after_spec_activation(project, capsys):
    """Once a Spec is on ``giro/<slug>``, ``giro new issue`` writes the file
    into the Spec's worktree and commits it onto the branch — so `giro status`
    (which reads the branch tip) sees the new Issue. The checkout is never
    touched."""
    from giro.workspace import Workspace

    # Activate the Spec by running the first issue green.
    make_engine(project, worker=FakeDriver([good_worker])).implement(
        "demo/01-write-feature"
    )
    assert Workspace(project).branch_exists("giro/demo")

    # `giro new issue demo "Second slice"` after activation — the Issue must
    # land on the branch, not the checkout (where the Spec's copy is stale).
    assert main(["-C", str(project), "new", "issue", "demo", "Second slice"]) == 0
    out = capsys.readouterr().out
    assert "on branch giro/demo" in out

    on_branch = Workspace(project).read_blob(
        "giro/demo", "docs/specs/demo/issues/02-second-slice.md"
    )
    assert on_branch is not None
    assert "Second slice" in on_branch

    # `giro status` reads the branch tip and now lists two issues.
    assert main(["-C", str(project), "status"]) == 0
    listing = capsys.readouterr().out
    assert "02-second-slice" in listing


def test_new_issue_bare_numeric_blocked_by_resolves(project, capsys):
    """`--blocked-by 01` resolves to the "01-…" issue when unambiguous, so
    a human need not retype the slug the engine already picked."""
    assert (
        main(["-C", str(project), "new", "issue", "demo", "Next", "--blocked-by", "01"])
        == 0
    )
    issues = Store(project).load_issues("demo")
    assert issues[1].blocked_by == ["01-write-feature"]


def test_new_issue_before_activation_writes_to_the_checkout(project):
    """Before the Spec activates, the checkout IS the source of truth — the
    new Issue lands there, same as before."""
    from giro.workspace import Workspace

    assert not Workspace(project).branch_exists("giro/demo")
    assert main(["-C", str(project), "new", "issue", "demo", "Second slice"]) == 0
    # The file exists in the checkout, not on any branch (there is no branch).
    checkout = project / "docs" / "specs" / "demo" / "issues" / "02-second-slice.md"
    assert checkout.is_file()


def test_status_points_at_issue_when_spec_needs_human_has_unfinished_child(
    project, capsys
):
    """U9: a Spec escalating because a child Issue is unfinished must point at
    the Issue ref, not the Spec slug — the answer target is the Issue, the
    Spec's body has nothing new to say."""
    store = Store(project)
    spec = store.load_spec("demo")
    spec.state = "needs-human"
    store.save_spec(spec)
    issue = store.load_issues("demo")[0]
    issue.state = "needs-human"  # child escalation
    store.save_issue(issue)

    assert main(["-C", str(project), "status"]) == 2
    out = capsys.readouterr().out
    # The needs-human hint points at the Issue ref, not the Spec slug.
    assert "demo/01-write-feature" in out
    # It does NOT tell the human to re-invoke the Spec itself.
    assert "giro implement demo\n" not in out
    assert "spec escalated" not in out
