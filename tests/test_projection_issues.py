"""Issue lifecycle projection: every giro Issue as a sub-issue of its Spec's
parent, wearing the state the store gives it.

The desired surface — labels, body, open or closed — is a pure function of the
store and is asserted directly; the `gh` boundary is scripted the way drivers
are, so every invocation the engine would make is asserted instead of sent.
"""

import json
import threading
from pathlib import Path

import pytest

from giro.drivers import FakeDriver
from giro.projection import FakeGh, GhResult, issue_body, issue_labels, snapshot_of
from giro.store import Issue, Store
from tests.conftest import git, good_worker, make_engine, spec_store
from tests.test_parallel import parallel_project, route_by_title
from tests.test_projection import projection_for
from tests.test_projection_spec import demo_spec, passing_judge, surface_gh, with_origin

REPO = "acme/widgets"
PARENT = 12  # the parent tracking issue `surface_gh` creates
FIRST = 100  # the first number a minting `gh` hands out
SUB_ISSUES = f"repos/{REPO}/issues/{PARENT}/sub_issues"  # where the progress bar is hung


def an_issue(issue_id: str = "01-alpha", state: str = "ready", **fields) -> Issue:
    title = fields.pop("title", f"{issue_id.partition('-')[2].capitalize()} slice")
    return Issue("demo", issue_id, Path(f"{issue_id}.md"), state, title=title, **fields)


class MintingGh(FakeGh):
    """A scripted `gh` that plays the two parts of GitHub the sub-issues are a
    function of: it mints a fresh number and id for every issue it is asked to
    create — so N Issues make N distinguishable sub-issues — and it remembers
    which of them the parent holds, so a link once made is reported back.
    Anything scripted still wins, including a call that fails."""

    def __init__(self, results=None, first: int = FIRST):
        super().__init__(results)
        self.next = first
        self.hung: list[int] = []  # the sub-issue numbers hung under the parent

    def run(self, args: list[str]) -> GhResult:
        joined = " ".join(args)
        if any(joined.startswith(key) for key in self.results):
            return super().run(args)
        if joined == f"api {SUB_ISSUES}?per_page=100":
            self.calls.append(list(args))
            body = json.dumps([{"number": number} for number in self.hung])
            return GhResult(0, body, args=list(args))
        if joined.startswith(f"api {SUB_ISSUES} -X POST"):
            self.hung.append(int(field(args, "sub_issue_id")) - 9000)  # id is 9000 + number
            return super().run(args)
        if not joined.startswith(f"api repos/{REPO}/issues -X POST"):
            return super().run(args)
        self.calls.append(list(args))
        number, self.next = self.next, self.next + 1
        return GhResult(
            0,
            json.dumps(
                {
                    "number": number,
                    "id": 9000 + number,
                    "state": "open",
                    "labels": [
                        {"name": value[len("labels[]=") :]}
                        for value in args
                        if value.startswith("labels[]=")
                    ],
                    "body": field(args, "body"),
                }
            ),
            args=list(args),
        )


def sub_issue_gh(**scripted) -> MintingGh:
    """A `gh` that answers preflight, the Spec surface, and sub-issue creation."""
    gh = MintingGh(surface_gh().results)
    gh.results.update(scripted)
    return gh


def view(labels=("giro:issue", "giro:ready"), state="OPEN", reason="", body="") -> GhResult:
    """What `gh issue view --json labels,state,stateReason,body` answers."""
    return GhResult(
        0,
        json.dumps(
            {
                "labels": [{"name": name} for name in labels],
                "state": state,
                "stateReason": reason,
                "body": body,
            }
        ),
    )


def creates(gh: FakeGh) -> list[list[str]]:
    return [args for args in gh.calls if args[:4] == ["api", f"repos/{REPO}/issues", "-X", "POST"]]


def links(gh: FakeGh) -> list[list[str]]:
    """The calls that hang a sub-issue under the parent — the writes, not the
    read the projection diffs them against."""
    return [args for args in gh.calls if args[:4] == ["api", SUB_ISSUES, "-X", "POST"]]


def edits(gh: FakeGh, number: int) -> list[list[str]]:
    return [args for args in gh.calls if args[:3] == ["issue", "edit", str(number)]]


def flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1] if name in args else ""


def field(args: list[str], name: str) -> str:
    """The value of a ``gh api -f key=value`` field."""
    return next((a[len(name) + 1 :] for a in args if a.startswith(f"{name}=")), "")


# -- the desired surface, computed from the store alone ----------------------


def test_the_labels_a_sub_issue_wears_through_the_loop():
    assert issue_labels(an_issue(state="ready")) == {"giro:issue", "giro:ready"}
    assert issue_labels(an_issue(state="in-progress")) == {"giro:issue", "giro:in-progress"}
    assert issue_labels(an_issue(state="needs-human")) == {"giro:issue", "giro:needs-human"}
    assert issue_labels(an_issue(state="done")) == {"giro:issue", "giro:done"}
    assert issue_labels(an_issue(state="wontfix")) == {"giro:issue", "giro:wontfix"}
    assert issue_labels(an_issue(gap_gate="spec-fit")) == {
        "giro:issue",
        "giro:ready",
        "giro:gap",
    }


def test_a_sub_issues_body_points_at_the_markdown_and_names_the_gate_that_filed_it():
    body = issue_body(demo_spec(), an_issue())
    assert "docs/specs/demo/issues/01-alpha.md" in body
    assert "<!-- giro:issue:demo/01-alpha -->" in body  # found again, never remade
    assert "Blocked by" not in body and "gap" not in body

    gap = issue_body(demo_spec(), an_issue(gap_gate="spec-fit"))
    assert "gap finding" in gap and "`spec-fit`" in gap

    blocked = issue_body(demo_spec(), an_issue(), ["#41", "`03-late`"])
    assert "Blocked by #41, `03-late`." in blocked


def test_a_snapshot_reads_the_same_issue_however_it_was_asked_for():
    viewed = snapshot_of(json.loads(view(state="CLOSED", reason="NOT_PLANNED").stdout))
    assert viewed.state == "closed" and viewed.reason == "not_planned"
    assert viewed.labels == {"giro:issue", "giro:ready"}

    api = snapshot_of({"state": "closed", "state_reason": "completed", "body": "b"})
    assert api.state == "closed" and api.reason == "completed" and api.body == "b"
    assert snapshot_of(None) is None


# -- the sub-issues, projected -----------------------------------------------


def test_planning_issues_creates_one_sub_issue_each_under_the_parent():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    issues = [an_issue("01-alpha"), an_issue("02-beta")]
    gh = sub_issue_gh()

    surface = projection_for(gh).project_spec(spec, issues, "planned")

    assert surface.issues == {"01-alpha": FIRST, "02-beta": FIRST + 1}
    made = creates(gh)
    assert [field(args, "title") for args in made] == ["Alpha slice", "Beta slice"]
    assert all("labels[]=giro:issue" in args for args in made)
    # each one hung under the parent by the identifier GitHub answered with
    at_parent = [args for args in gh.calls if args[0] == "api" and args[1].startswith(SUB_ISSUES)]
    assert at_parent[0] == ["api", f"{SUB_ISSUES}?per_page=100"]  # read first, then diffed
    assert [flag(args, "-F") for args in links(gh)] == ["sub_issue_id=9100", "sub_issue_id=9101"]


def test_a_sub_issue_the_parent_never_took_is_hung_at_the_next_projection():
    """The link is a write like any other, and fail-soft like any other: a
    refused one would otherwise orphan an Issue from the progress bar forever,
    because the number in frontmatter stops it from ever being created again."""
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    issue = an_issue("01-alpha")
    refused = {f"api {SUB_ISSUES} -X POST": GhResult(1, stderr="HTTP 403: Forbidden")}
    gh = sub_issue_gh(**refused)
    projection = projection_for(gh)

    surface = projection.project_spec(spec, [issue], "planned")
    issue.github_issue = surface.issues["01-alpha"]  # created and recorded; never hung

    del gh.results[f"api {SUB_ISSUES} -X POST"]  # the next projection meets a live GitHub
    gh.calls.clear()
    projection.project_spec(spec, [issue], "planned")

    assert creates(gh) == []  # the sub-issue is found, not remade
    assert [flag(args, "-F") for args in links(gh)] == [f"sub_issue_id={9000 + FIRST}"]

    # and once the parent holds it, the link is not written a third time
    gh.calls.clear()
    projection.project_spec(spec, [issue], "planned")
    assert links(gh) == []


@pytest.mark.parametrize(
    "state,label,reason",
    [
        ("ready", "giro:ready", ""),
        ("in-progress", "giro:in-progress", ""),
        ("needs-human", "giro:needs-human", ""),
        ("done", "giro:done", "completed"),
        ("wontfix", "giro:wontfix", "not planned"),
    ],
)
def test_labels_track_the_loop_and_a_terminal_issue_closes_for_the_right_reason(
    state, label, reason
):
    issue = an_issue(state=state, github_issue=41)
    claimed = view(  # what GitHub holds: the Issue as the wave claimed it
        labels=["giro:issue", "giro:in-progress"], body=issue_body(demo_spec(), issue)
    )
    gh = sub_issue_gh(**{"issue view 41": claimed})

    projection_for(gh).project_spec(demo_spec(github_issue=PARENT, github_pr=13), [issue])

    assert creates(gh) == []  # the number in frontmatter is the whole mapping
    if state == "in-progress":
        assert edits(gh, 41) == []  # already right: labels are diffed, not rewritten
    else:
        edit = edits(gh, 41)[0]
        assert flag(edit, "--add-label") == label
        assert flag(edit, "--remove-label") == "giro:in-progress"
        assert "--body" not in edit
    closed = [args for args in gh.calls if args[:3] == ["issue", "close", "41"]]
    assert [flag(args, "--reason") for args in closed] == ([reason] if reason else [])
    assert [args for args in gh.calls if args[:2] == ["issue", "reopen"]] == []


def test_blocking_edges_become_native_issue_dependencies():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    alpha = an_issue("01-alpha", state="done", github_issue=41)
    beta = an_issue("02-beta", github_issue=42, blocked_by=["01-alpha"])
    gh = sub_issue_gh(
        **{
            f"api repos/{REPO}/issues/41 --jq .id": GhResult(0, "9041\n"),
            "issue view 41": view(
                labels=["giro:issue", "giro:done"],
                state="CLOSED",
                reason="COMPLETED",
                body=issue_body(spec, alpha),
            ),
            "issue view 42": view(body=issue_body(spec, beta)),
        }
    )

    projection_for(gh).project_spec(spec, [alpha, beta])

    endpoint = f"repos/{REPO}/issues/42/dependencies/blocked_by"
    at_endpoint = [args for args in gh.calls if len(args) > 1 and args[1] == endpoint]
    assert at_endpoint[0] == ["api", endpoint]  # read first: an edge is added once
    assert [flag(args, "-F") for args in at_endpoint[1:]] == ["issue_id=9041"]
    assert not [args for args in gh.calls if "Blocked by" in " ".join(args)]


def test_an_edge_a_repository_cannot_take_natively_is_stated_in_the_body():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    alpha = an_issue("01-alpha", state="done", github_issue=41)
    beta = an_issue("02-beta", github_issue=42, blocked_by=["01-alpha"])
    gh = sub_issue_gh(
        **{
            f"api repos/{REPO}/issues/42/dependencies": GhResult(1, stderr="HTTP 404: Not Found"),
            "issue view 41": view(
                labels=["giro:issue", "giro:done"],
                state="CLOSED",
                reason="COMPLETED",
                body=issue_body(spec, alpha),
            ),
            "issue view 42": view(body=issue_body(spec, beta)),
        }
    )

    projection_for(gh).project_spec(spec, [alpha, beta])

    assert not [a for a in gh.calls if len(a) > 3 and a[2:4] == ["-X", "POST"] and "dep" in a[1]]
    assert "Blocked by #41." in flag(edits(gh, 42)[0], "--body")


def test_an_edge_with_no_end_to_hang_it_on_is_stated_in_the_body_too():
    """A repository that takes dependencies still cannot take an edge whose
    blocker GitHub does not hold — the edge is prose, not nowhere."""
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    alpha = an_issue("01-alpha", state="done", github_issue=41)  # its id cannot be read
    gamma = an_issue("03-gamma")  # never created: no number to point at
    beta = an_issue("02-beta", github_issue=42, blocked_by=["01-alpha", "03-gamma"])
    gh = sub_issue_gh(
        **{
            f"api repos/{REPO}/issues -X POST": GhResult(1, stderr="HTTP 403: Forbidden"),
            f"api repos/{REPO}/issues/41 --jq .id": GhResult(1, stderr="HTTP 502"),
            "issue view 41": view(
                labels=["giro:issue", "giro:done"],
                state="CLOSED",
                reason="COMPLETED",
                body=issue_body(spec, alpha),
            ),
            "issue view 42": view(body=issue_body(spec, beta)),
        }
    )

    projection_for(gh).project_spec(spec, [alpha, beta, gamma])

    assert not [a for a in gh.calls if len(a) > 1 and "dependencies" in a[1] and "-X" in a]
    assert "Blocked by #41, `03-gamma`." in flag(edits(gh, 42)[0], "--body")


def test_projecting_twice_writes_nothing_the_second_time():
    spec = demo_spec(github_issue=PARENT, github_pr=13)
    issue = an_issue(state="done", github_issue=41)
    settled = view(
        labels=["giro:issue", "giro:done"],
        state="CLOSED",
        reason="COMPLETED",
        body=issue_body(spec, issue),
    )
    gh = sub_issue_gh(**{"issue view 41": settled})
    projection = projection_for(gh)

    projection.project_spec(spec, [issue], "wave 1")
    gh.calls.clear()
    projection.project_spec(spec, [issue], "wave 1")

    assert creates(gh) == [] and edits(gh, 41) == []
    assert [args for args in gh.calls if args[:2] == ["issue", "close"]] == []
    assert [args[:3] for args in gh.calls].count(["issue", "view", "41"]) == 1  # read, compared


def test_a_hand_closed_or_relabelled_sub_issue_is_restored_and_human_labels_kept():
    issue = an_issue(github_issue=41)  # the store says ready; a human said otherwise
    gh = sub_issue_gh(
        **{
            "issue view 41": view(
                labels=["giro:issue", "giro:done", "urgent"], state="CLOSED", reason="COMPLETED"
            )
        }
    )

    projection_for(gh).project_spec(demo_spec(github_issue=PARENT, github_pr=13), [issue])

    edit = edits(gh, 41)[0]
    assert flag(edit, "--add-label") == "giro:ready"
    assert flag(edit, "--remove-label") == "giro:done"  # never the human's own label
    assert [args[:3] for args in gh.calls if args[:2] == ["issue", "reopen"]] == [
        ["issue", "reopen", "41"]
    ]
    assert issue.state == "ready"  # nothing was read back into the store


# -- a Run, projected --------------------------------------------------------


def test_a_run_records_the_sub_issue_number_and_never_makes_a_second_one(project):
    with_origin(project)
    gh = sub_issue_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo/01-write-feature").outcome == "done"

    assert len(creates(gh)) == 1
    issue = spec_store(project).load_issues("demo")[0]
    assert issue.github_issue == FIRST  # the mapping is in frontmatter, and committed
    closed = [args for args in gh.calls if args[:3] == ["issue", "close", str(FIRST)]]
    assert flag(closed[0], "--reason") == "completed"

    # a later Run finds the sub-issue through the number the first one recorded
    gh.calls.clear()
    gh.results[f"issue view {FIRST}"] = view(
        labels=["giro:issue", "giro:done"], state="CLOSED", reason="COMPLETED"
    )
    resumed = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )
    assert resumed.implement("demo").outcome == "all-done"
    assert creates(gh) == []
    assert spec_store(project).load_issues("demo")[0].github_issue == FIRST


def test_a_claimed_issue_says_in_progress_while_the_worker_runs(project):
    with_origin(project)
    gh = sub_issue_gh(**{f"issue view {FIRST}": view()})  # GitHub holds it as ready
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    # claimed before the worker runs, done after it — the loop, as it happens
    adds = [flag(args, "--add-label") for args in edits(gh, FIRST)]
    assert adds[0] == "giro:in-progress"  # a fake that never moves is re-converged
    assert set(adds[1:]) == {"giro:done"}
    closed = [args for args in gh.calls if args[:3] == ["issue", "close", str(FIRST)]]
    assert flag(closed[0], "--reason") == "completed"


def test_planning_a_spec_projects_every_planned_issue_as_a_sub_issue(project):
    Store(project).load_issues("demo")[0].path.unlink()  # an empty Spec, to be planned
    git(project, "commit", "-am", "drop the seeded issue")
    with_origin(project)
    planner = FakeDriver(
        [
            {
                "issues": [
                    {"title": "Write feature file", "body": "Create feature.txt."},
                    {"title": "Polish", "body": "Round it out.", "blocked_by": [1]},
                ]
            }
        ]
    )

    def polish(prompt, cwd):
        (cwd / "polish.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": "polished"}

    gh = sub_issue_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker, polish]),
        judge=passing_judge(),
        planner=planner,
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    assert len(creates(gh)) == 2  # N Issues planned, N sub-issues under the parent
    numbers = [i.github_issue for i in spec_store(project).load_issues("demo")]
    assert numbers == [FIRST, FIRST + 1]
    # the blocking edge offered to GitHub natively, from the blocked end
    endpoint = f"repos/{REPO}/issues/{FIRST + 1}/dependencies/blocked_by"
    assert [args for args in gh.calls if args[:4] == ["api", endpoint, "-X", "POST"]]


def test_a_gap_issue_is_projected_when_filed_naming_the_gate_that_found_it(project):
    with_origin(project)

    def gap_worker(prompt, cwd):
        (cwd / "gap.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": "closed the gap"}

    judge = FakeDriver(
        [
            {"verdict": "fail", "findings": [{"summary": "gap: missing gap.txt"}]},
            {"verdict": "pass"},
        ]
    )
    gh = sub_issue_gh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker, gap_worker]),
        judge=judge,
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    gap = spec_store(project).load_issues("demo")[1]
    assert gap.gap_gate == "spec-fit" and gap.github_issue == FIRST + 1
    created = creates(gh)[1]
    assert "labels[]=giro:gap" in created
    assert "`spec-fit`" in field(created, "body")


# -- a wave, projected -------------------------------------------------------


class WaveGh(MintingGh):
    """A minting `gh` that also says where and when it was called from: every
    invocation's thread, and its place in a timeline the workers write to."""

    def __init__(self, timeline: list[tuple[str, str]], results=None):
        super().__init__(results)
        self.timeline = timeline
        self.threads: set[str] = set()

    def run(self, args: list[str]) -> GhResult:
        self.threads.add(threading.current_thread().name)
        self.timeline.append(("gh", " ".join(args)))
        return super().run(args)


class RefusingGh(MintingGh):
    """A minting `gh` that refuses its first ``refuse`` creations — so the
    numbers are minted by a later projection, not the first one."""

    def __init__(self, refuse: int, results=None):
        super().__init__(results)
        self.refuse = refuse

    def run(self, args: list[str]) -> GhResult:
        if self.refuse and " ".join(args).startswith(f"api repos/{REPO}/issues -X POST"):
            self.refuse -= 1
            self.calls.append(list(args))
            return GhResult(1, stderr="HTTP 403: Forbidden", args=list(args))
        return super().run(args)


def wave_of_two(project: Path):
    """A Spec with two unblocked Issues and concurrency 2 — one parallel wave."""
    return parallel_project(
        project,
        issues=[
            ("Alpha slice", "Create alpha.txt.", ()),
            ("Beta slice", "Create beta.txt.", ()),
        ],
    )


def wave_worker(timeline: list[tuple[str, str]] | None = None):
    def acts(name: str, filename: str):
        def act(cwd: Path) -> None:
            if timeline is not None:
                timeline.append(("work", name))
            (cwd / filename).write_text(f"{name}\n")

        return act

    return route_by_title({"Alpha": acts("Alpha", "alpha.txt"), "Beta": acts("Beta", "beta.txt")})


def at(timeline: list[tuple[str, str]], kind: str, prefix: str = "") -> list[int]:
    return [i for i, (k, text) in enumerate(timeline) if k == kind and text.startswith(prefix)]


def test_a_wave_is_projected_from_the_main_thread_in_claim_work_checkpoint_order(project):
    """Only the main thread touches the store, the integration branch — or
    GitHub. The parent's progress bar is the wave's scoreboard as it happens:
    in-progress before the workers run, closed after each one lands."""
    wave_of_two(project)
    with_origin(project)
    timeline: list[tuple[str, str]] = []
    gh = WaveGh(timeline, surface_gh().results)
    gh.results[f"issue view {FIRST}"] = view()  # GitHub holds both as ready
    gh.results[f"issue view {FIRST + 1}"] = view()
    engine = make_engine(
        project,
        worker=FakeDriver([wave_worker(timeline)] * 2),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    assert gh.threads == {threading.main_thread().name}
    worked = at(timeline, "work")
    assert len(worked) == 2 and len(creates(gh)) == 2
    for number in (FIRST, FIRST + 1):
        claimed = [
            i
            for i in at(timeline, "gh", f"issue edit {number}")
            if "giro:in-progress" in timeline[i][1]
        ]
        assert claimed and min(claimed) < min(worked)  # claimed before the wave ran
        closed = at(timeline, "gh", f"issue close {number}")
        assert closed and min(closed) > max(worked)  # each checkpoint after the work


def test_a_number_minted_at_a_waves_claim_survives_every_later_save(project):
    """The wave holds its Issue objects across the whole wave and saves each one
    again at its checkpoint. A number minted while they are held has to reach
    them too, or that save erases it and the next projection makes a second
    sub-issue."""
    store = wave_of_two(project)
    with_origin(project)
    gh = RefusingGh(2, surface_gh().results)  # activation projects nothing
    engine = make_engine(
        project,
        worker=FakeDriver([wave_worker()] * 2),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    assert engine.implement("demo").outcome == "all-done"

    assert [i.github_issue for i in store.load_issues("demo")] == [FIRST, FIRST + 1]
    assert gh.next == FIRST + 2  # two Issues, two sub-issues, however often projected
    assert len(creates(gh)) == 4  # the two refused at activation, then the two made
    assert len(links(gh)) == 2  # each hung under the parent exactly once


def test_a_broken_projection_leaves_the_issue_loop_exactly_as_it_was(project):
    with_origin(project)
    gh = sub_issue_gh(
        **{f"api repos/{REPO}/issues -X POST": GhResult(1, stderr="HTTP 403: Forbidden")}
    )
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=passing_judge(),
        projection=projection_for(gh),
    )

    report = engine.implement("demo")

    assert report.outcome == "all-done"  # identical to the projected Run
    issue = spec_store(project).load_issues("demo")[0]
    assert issue.state == "done" and issue.github_issue == 0
    events = [e for e in engine.ledger.events() if e["type"] == "projection"]
    assert any(e.get("ok") is False and "403" in e.get("detail", "") for e in events)
