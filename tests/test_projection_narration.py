"""Narration: the Run's story, told where humans read.

Every log section the engine appends to an Issue arrives verbatim as one
comment on its sub-issue; every orchestration moment — plan, wave, Validate
verdict, gap cycle — is a comment on the parent; and the integrated gate
verdicts are commit statuses on the published head. The desired comments are a
pure function of the store and are asserted directly; the `gh` boundary is
scripted the way drivers are, and remembers what it was given, so "projecting
twice changes nothing" is exercised rather than assumed.
"""

import json
import re

import pytest

from giro import projection as proj
from giro.drivers import FakeDriver
from giro.envelope import Finding
from giro.ledger import Ledger
from giro.projection import (
    RETRY_PAUSE,
    FakeGh,
    GhResult,
    issue_body,
    log_comments,
    projected_comments,
)
from giro.store import LOG_FENCE, Store, log_sections
from giro.workspace import Workspace
from tests.conftest import git, good_worker, lazy_worker, make_engine, spec_store
from tests.test_projection import projection_for
from tests.test_projection_issues import (
    FIRST,
    PARENT,
    REPO,
    MintingGh,
    an_issue,
    field,
    view,
)
from tests.test_projection_spec import demo_spec, passing_judge, surface_gh, with_origin

COMMENTS = f"repos/{REPO}/issues"  # .../<number>/comments
STATUSES = f"repos/{REPO}/statuses"


class CommentingGh(MintingGh):
    """A minting `gh` that also remembers the comments it is given: a posted
    comment is readable at the next projection, and an edited one keeps what it
    was edited to — so idempotency and drift are both exercised for real."""

    def __init__(self, results=None):
        super().__init__(results)
        self.comments: dict[int, list[dict]] = {}  # issue number -> rows, in order
        self.next_comment = 500

    def run(self, args: list[str]) -> GhResult:
        joined = " ".join(args)
        if any(joined.startswith(key) for key in self.results):
            return super().run(args)
        if listed := re.fullmatch(rf"api {COMMENTS}/(\d+)/comments\?per_page=100", joined):
            self.calls.append(list(args))
            rows = self.comments.get(int(listed.group(1)), [])
            return GhResult(0, json.dumps(rows), args=list(args))
        if made := re.match(rf"api {COMMENTS}/(\d+)/comments -X POST", joined):
            self.calls.append(list(args))
            row = {"id": self.next_comment, "body": field(args, "body")}
            self.next_comment += 1
            self.comments.setdefault(int(made.group(1)), []).append(row)
            return GhResult(0, json.dumps(row), args=list(args))
        if edited := re.match(rf"api {COMMENTS}/comments/(\d+) -X PATCH", joined):
            self.calls.append(list(args))
            for rows in self.comments.values():
                for row in rows:
                    if row["id"] == int(edited.group(1)):
                        row["body"] = field(args, "body")
            return GhResult(0, "{}", args=list(args))
        return super().run(args)


class TimelineGh(CommentingGh):
    """A commenting `gh` that says when it was called, in a timeline the
    workers write to as well — so "nothing posts mid-attempt" is a fact about
    ordering, not a hope."""

    def __init__(self, timeline: list[tuple[str, str]], results=None):
        super().__init__(results)
        self.timeline = timeline

    def run(self, args: list[str]) -> GhResult:
        self.timeline.append(("gh", " ".join(args)))
        return super().run(args)


def commenting_gh(**scripted) -> CommentingGh:
    """A `gh` that answers preflight, the Spec surface, sub-issues, comments."""
    gh = CommentingGh(surface_gh().results)
    gh.results.update(scripted)
    return gh


def posted(gh: FakeGh, number: int) -> list[list[str]]:
    return [a for a in gh.calls if a[:4] == ["api", f"{COMMENTS}/{number}/comments", "-X", "POST"]]


def patched(gh: FakeGh) -> list[list[str]]:
    return [a for a in gh.calls if a[:1] == ["api"] and "-X" in a and "PATCH" in a]


def status_calls(gh: FakeGh) -> list[list[str]]:
    return [a for a in gh.calls if len(a) > 1 and a[1].startswith(STATUSES)]


def headings(gh: FakeGh, number: int) -> list[str]:
    return [field(a, "body").splitlines()[0] for a in posted(gh, number)]


def logged(issue_id: str = "01-alpha", state: str = "done", **fields):
    """An Issue whose story is already two attempts long."""
    issue = an_issue(issue_id, state, **fields)
    issue.body = (
        f"# {issue.title}\n\nDo the thing.\n\n## Acceptance criteria\n\n- it works\n\n"
        f"{LOG_FENCE}\n\n"
        "## Attempt 1 — gates failed\n\n- feature: `test -f feature.txt` exited 1\n\n"
        "## Attempt 2 — done — all gates green\n\n- worker: wrote feature.txt\n"
    )
    return issue


# -- the desired comments, computed from the store alone ---------------------


def test_the_log_is_fenced_off_from_the_body_and_read_back_section_by_section(project):
    store = Store(project)
    issue = store.load_issues("demo")[0]
    issue.body += "\n## Acceptance criteria\n\n- feature.txt says ok\n"
    store.save_issue(issue)
    assert log_sections(issue.body) == []  # a body nobody has run yet has no story

    store.append_log(issue, "Attempt 1 — gates failed", ["feature: exited 1"])
    store.append_log(issue, "Attempt 2 — done — all gates green", ["worker: wrote it"])
    reloaded = store.load_issues("demo")[0]

    sections = log_sections(reloaded.body)
    assert [s.splitlines()[0] for s in sections] == [
        "## Attempt 1 — gates failed",  # the conversation's own heading is not a section
        "## Attempt 2 — done — all gates green",
    ]
    assert "- feature: exited 1" in sections[0]
    assert reloaded.body.count(LOG_FENCE) == 1  # one fence, however long the story gets
    assert "Create feature.txt" in reloaded.body  # the body itself, untouched


def test_each_log_section_is_one_comment_verbatim_under_a_hidden_marker():
    issue = logged()
    comments = log_comments(demo_spec(), issue)

    assert [key for key, _body in comments] == ["demo/01-alpha#1", "demo/01-alpha#2"]
    for (key, body), section in zip(comments, log_sections(issue.body), strict=True):
        assert body.startswith(section.rstrip())  # one writer, one story, two displays
        assert body.endswith(f"<!-- giro:comment:{key} -->")
    key, body = comments[0]
    assert projected_comments([{"id": 7, "body": body}]) == {key: (7, body)}
    assert projected_comments([{"id": 8, "body": "a human's own comment"}]) == {}


# -- the story, projected ----------------------------------------------------


def settled_view(spec, issue):
    return view(
        labels=["giro:issue", f"giro:{issue.state}"],
        state="CLOSED" if issue.state == "done" else "OPEN",
        reason="COMPLETED" if issue.state == "done" else "",
        body=issue_body(spec, issue),
    )


def test_the_log_arrives_as_one_comment_per_section_and_never_a_second_time():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    issue = logged(github_issue=41)
    gh = commenting_gh(**{"issue view 41": settled_view(spec, issue)})
    projection = projection_for(gh)

    projection.project_spec(spec, [issue])

    assert headings(gh, 41) == [
        "## Attempt 1 — gates failed",
        "## Attempt 2 — done — all gates green",
    ]
    gh.calls.clear()
    projection.project_spec(spec, [issue])
    assert posted(gh, 41) == [] and patched(gh) == []  # found by marker, not remade


def test_a_hand_edited_comment_is_restored_and_a_humans_own_is_left_alone():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    issue = logged(github_issue=41)
    gh = commenting_gh(**{"issue view 41": settled_view(spec, issue)})
    projection = projection_for(gh)

    projection.project_spec(spec, [issue])
    original = gh.comments[41][0]["body"]
    gh.comments[41][0]["body"] = "I disagree.\n\n<!-- giro:comment:demo/01-alpha#1 -->"
    gh.comments[41].append({"id": 999, "body": "Looks good to me."})  # a human's comment
    gh.calls.clear()

    projection.project_spec(spec, [issue])

    edits = patched(gh)
    assert len(edits) == 1 and field(edits[0], "body") == original
    assert posted(gh, 41) == []
    assert gh.comments[41][-1]["body"] == "Looks good to me."  # never touched


def test_a_freshly_created_sub_issue_takes_its_story_without_reading_comments_back():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    gh = commenting_gh()

    surface = projection_for(gh).project_spec(spec, [logged()], "wave 1")

    number = surface.issues["01-alpha"]
    assert len(posted(gh, number)) == 2
    assert [a for a in gh.calls if "?per_page=100" in " ".join(a) and "comments" in a[1]] == []


# -- the orchestration moments, on the parent --------------------------------


def test_an_orchestration_moment_is_one_marked_comment_on_the_parent():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    gh = commenting_gh()
    projection = projection_for(gh)

    projection.narrate(spec, "wave-1", "Wave 1 — 2 Issues", ["`01-alpha`", "`02-beta`"])
    projection.narrate(spec, "wave-1", "Wave 1 — 2 Issues", ["`01-alpha`", "`02-beta`"])

    assert headings(gh, PARENT) == ["## Wave 1 — 2 Issues"]  # the moment happened once
    body = field(posted(gh, PARENT)[0], "body")
    assert "- `01-alpha`" in body and "giro:comment:" in body


def test_a_spec_with_no_parent_yet_narrates_nothing():
    gh = commenting_gh()
    projection_for(gh).narrate(demo_spec(), "plan", "Plan — 2 Issues", [])
    assert gh.calls == []


# -- commit statuses ---------------------------------------------------------


def test_gate_verdicts_become_one_commit_status_per_gate_pass_and_fail():
    gh = commenting_gh()
    projection = projection_for(gh)

    projection.report_gates(
        "abc123",
        "verify",
        {"feature": "pass", "review": "fail"},
        [Finding(summary="no test covers the empty case", gate="review")],
    )

    made = status_calls(gh)
    assert [a[1] for a in made] == [f"{STATUSES}/abc123"] * 2
    assert [field(a, "context") for a in made] == ["giro/verify/feature", "giro/verify/review"]
    assert [field(a, "state") for a in made] == ["success", "failure"]
    assert "no test covers the empty case" in field(made[1], "description")


def test_no_head_and_no_verdicts_means_no_status():
    gh = commenting_gh()
    projection_for(gh).report_gates("", "validate", {"spec-fit": "pass"})
    projection_for(gh).report_gates("abc123", "validate", {})
    assert status_calls(gh) == []


# -- a Run, narrated ---------------------------------------------------------


def marking_worker(timeline, act=None):
    def worker(prompt, cwd):
        timeline.append(("work", "start"))
        summary = "claims done, did nothing"
        if act is not None:
            act(cwd)
            summary = "wrote feature.txt"
        timeline.append(("work", "end"))
        return {"outcome": "completed", "summary": summary}

    return worker


def test_a_run_tells_the_issues_story_and_posts_nothing_while_a_worker_runs(project):
    with_origin(project)
    timeline: list[tuple[str, str]] = []
    gh = TimelineGh(timeline, surface_gh().results)
    gh.results[f"issue view {FIRST}"] = view()
    engine = make_engine(
        project,
        worker=FakeDriver(
            [
                marking_worker(timeline),
                marking_worker(timeline, lambda cwd: (cwd / "feature.txt").write_text("ok\n")),
            ]
        ),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo/01-write-feature").outcome == "done"

    bodies = [field(a, "body") for a in posted(gh, FIRST)]
    assert [b.splitlines()[0] for b in bodies] == [
        "## Attempt 1 — empty",
        "## Attempt 2 — done — all gates green",
    ]
    assert "worker: wrote feature.txt" in bodies[1]  # the worker's own words, in its comment
    issue = spec_store(project).load_issues("demo")[0]
    sections = log_sections(issue.body)
    assert [b.startswith(s.rstrip()) for b, s in zip(bodies, sections, strict=True)] == [True] * 2

    # every comment call sits outside every attempt: nothing narrates mid-flight
    marks = [i for i, (kind, _what) in enumerate(timeline) if kind == "work"]
    comments = [
        i
        for i, (kind, what) in enumerate(timeline)
        if kind == "gh" and "comments" in what and "-X POST" in what
    ]
    for start, end in zip(marks[::2], marks[1::2], strict=True):
        assert not [i for i in comments if start < i < end]


def test_comment_volume_is_bounded_by_the_attempt_budget(project):
    with_origin(project)
    gh = commenting_gh(**{f"issue view {FIRST}": view()})
    engine = make_engine(
        project,
        worker=FakeDriver([lazy_worker] * 3),  # budget is 3 attempts
        projection=projection_for(gh),
    )

    assert engine.implement("demo/01-write-feature").outcome == "needs-human"

    issue = spec_store(project).load_issues("demo")[0]
    made = posted(gh, FIRST)
    story = [a for a in made if "Needs human" not in field(a, "body")]
    assert len(story) == len(log_sections(issue.body)) == 4  # three attempts, one escalation
    assert len(made) == 5  # the story, plus the one comment that calls a human to it
    assert headings(gh, FIRST)[-2].startswith("## Budget exhausted")
    keys = [re.search(r"giro:comment:(\S+) -->", field(a, "body")).group(1) for a in made]
    assert len(set(keys)) == 5  # one marker each: nothing overwrites the story


def plan_two(project):
    """The seeded Spec, emptied so a plan happens, with a planner for two Issues."""
    Store(project).load_issues("demo")[0].path.unlink()
    git(project, "commit", "-am", "drop the seeded issue")
    with_origin(project)
    return FakeDriver(
        [
            {
                "issues": [
                    {"title": "Write feature file", "body": "Create feature.txt."},
                    {"title": "Polish", "body": "Round it out."},
                ]
            }
        ]
    )


def polish(prompt, cwd):
    (cwd / "polish.txt").write_text("ok\n")
    return {"outcome": "completed", "summary": "polished"}


def test_the_parent_carries_the_plan_the_waves_and_the_validate_verdict(project):
    planner = plan_two(project)
    gh = commenting_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker, polish]),
        judge=passing_judge(),
        planner=planner,
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    told = headings(gh, PARENT)
    assert told[0].startswith("## Plan — 2 Issue")
    assert any(h.startswith("## Wave 1") for h in told)
    assert told[-2] == "## Validate — passed"
    assert told[-1] == "## Validate passed — `demo` is done"  # and the last word after it
    plan = field(posted(gh, PARENT)[0], "body")
    assert "`01-write-feature-file`" in plan and "`02-polish`" in plan
    wave = next(field(a, "body") for a in posted(gh, PARENT) if "Wave 1" in field(a, "body"))
    assert f"#{FIRST}" in wave  # the sub-issues the wave is running, linked


def test_a_gap_cycle_is_told_on_the_parent_with_the_verdict_that_filed_it(project):
    with_origin(project)
    judge = FakeDriver(
        [
            {"verdict": "fail", "findings": [{"summary": "gap: missing gap.txt"}]},
            {"verdict": "pass"},
        ]
    )

    def gap_worker(prompt, cwd):
        (cwd / "gap.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": "closed the gap"}

    gh = commenting_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker, gap_worker]),
        judge=judge,
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    told = headings(gh, PARENT)
    assert told.count("## Validate — failed") == 1 and told.count("## Validate — passed") == 1
    failed = next(a for a in posted(gh, PARENT) if "Validate — failed" in field(a, "body"))
    assert "spec-fit" in field(failed, "body") and "missing gap.txt" in field(failed, "body")
    gap = next(a for a in posted(gh, PARENT) if "Gap cycle 1" in field(a, "body"))
    assert f"#{FIRST + 1}" in field(gap, "body")  # the Issue it filed, linked


def test_the_integrated_verdicts_land_as_statuses_on_a_published_commit(project):
    with_origin(project)
    gh = commenting_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    made = status_calls(gh)
    assert [field(a, "context") for a in made] == ["giro/verify/feature", "giro/validate/spec-fit"]
    assert {field(a, "state") for a in made} == {"success"}
    published = git(project, "log", "--format=%H", "refs/remotes/origin/giro/demo").split()
    # never a sha the remote does not have: a status hangs on published history
    assert all(a[1].rpartition("/")[2] in published for a in made)


def scribbler(n: int):
    def worker(prompt, cwd):
        (cwd / f"note-{n}.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": f"wrote note {n}"}

    return worker


def test_a_failed_validate_is_a_red_status_on_the_head(project):
    """The validate budget is 2 gap cycles: three verdicts, all fail, then the
    Run escalates — and the head says so in red."""
    with_origin(project)
    judge = FakeDriver(
        [
            {"verdict": "fail", "findings": [{"summary": "gap: missing gap.txt"}]},
            {"verdict": "fail", "findings": [{"summary": "gap: still missing"}]},
            {"verdict": "fail", "findings": [{"summary": "gap: still missing"}]},
        ]
    )
    gh = commenting_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker, scribbler(2), scribbler(3)]),
        judge=judge,
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "needs-human"

    validate = [a for a in status_calls(gh) if field(a, "context") == "giro/validate/spec-fit"]
    assert validate and {field(a, "state") for a in validate} == {"failure"}
    assert "missing gap.txt" in field(validate[0], "description")


def test_a_refused_comment_endpoint_costs_the_story_and_nothing_else(project):
    """Fail-soft at the narration too: what GitHub will not say is said in
    markdown and the Ledger, and a story that cannot be read back is not
    guessed at — a comment posted blind is a comment posted twice."""
    with_origin(project)
    gh = commenting_gh(
        **{
            f"api {COMMENTS}/{FIRST}/comments": GhResult(1, stderr="HTTP 403: Forbidden"),
            f"issue view {FIRST}": view(),  # the sub-issue exists: this is no first sight
        }
    )
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    report = engine.implement("demo")

    assert report.outcome == "all-done"  # identical to the narrated Run
    issue = spec_store(project).load_issues("demo")[0]
    assert issue.state == "done" and log_sections(issue.body)
    assert posted(gh, FIRST) == []
    events = [e for e in engine.ledger.events() if e["type"] == "projection"]
    assert any(e.get("action") == "comment" and e.get("ok") is False for e in events)


# -- the rate limit ----------------------------------------------------------


class FlakyGh(FakeGh):
    """A `gh` whose first content-creating call meets a secondary rate limit."""

    def __init__(self, limits: int = 1):
        super().__init__()
        self.limits = limits

    def run(self, args: list[str]) -> GhResult:
        self.calls.append(list(args))
        if self.limits and "-X" in args:
            self.limits -= 1
            return GhResult(
                1, stderr="HTTP 403: You have exceeded a secondary rate limit", args=list(args)
            )
        return GhResult(0, args=list(args))


@pytest.fixture
def no_pause(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(proj.time, "sleep", slept.append)
    return slept


def test_a_secondary_rate_limit_retries_once_and_then_succeeds(project, no_pause):
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    gh = FlakyGh()
    projection = projection_for(gh)
    projection.attach(ledger)

    result = projection.call("api", f"{COMMENTS}/41/comments", "-X", "POST", "-f", "body=x")

    assert result.ok and len(gh.calls) == 2  # one retry, and the comment lands
    assert no_pause == [RETRY_PAUSE]  # not the instant hammering that caused the limit
    assert [e for e in ledger.events() if e["type"] == "projection"] == []


def test_a_secondary_rate_limit_that_persists_drops_with_a_ledger_event(project, no_pause):
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    gh = FlakyGh(limits=5)
    projection = projection_for(gh)
    projection.attach(ledger)

    result = projection.call("api", f"{COMMENTS}/41/comments", "-X", "POST", "-f", "body=x")

    assert not result.ok and len(gh.calls) == 2  # retried once, then dropped
    recorded = [e for e in ledger.events() if e["type"] == "projection"]
    assert len(recorded) == 1 and recorded[0]["ok"] is False
    assert "secondary rate limit" in recorded[0]["detail"] and "retry" in recorded[0]["detail"]


def test_a_read_is_not_retried_and_an_ordinary_failure_is_not_either(project, no_pause):
    gh = FakeGh(
        {
            "api": GhResult(1, stderr="HTTP 403: You have exceeded a secondary rate limit"),
            "issue create": GhResult(1, stderr="HTTP 422: Validation Failed"),
        }
    )
    projection = projection_for(gh)

    projection.call("api", f"{COMMENTS}/41/comments?per_page=100")
    projection.call("issue", "create", "--title", "x")

    assert len(gh.calls) == 2 and no_pause == []
